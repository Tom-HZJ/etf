"""Publish the turnover trial, preserving ledger data and readable static files.

Run on the target with an integrity-checked stage directory. Runtime configuration
and accounts are inherited from the existing service, never packaged in a release.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from deploy_paper_review import (
    app, backend, check_frontend_worker_access, read_database, run, service,
    set_frontend_permissions, sha, tree_hashes, web,
)
from deploy_private_workspace import api, verify_access

VERSION = "2.26.2"
BASE_VERSION = "2.26.1"
CHANGED_SOURCE = {"config.py", "views.py", "sources.py", "minute.py", "observability.py"}


def verify_base(baseline):
    assert api("/api/health")[1]["version"] == BASE_VERSION, "Unexpected live version"
    assert run("systemctl", "is-active", service) == "active"
    assert tree_hashes(backend, "*.py") == baseline["backend"], "Backend changed during preparation"
    assert sha(web / "index.html") == baseline["entry_sha256"], "Frontend changed during preparation"


def publish(stage):
    manifest = json.loads((stage / "manifest.json").read_text())
    for name, digest in manifest.items():
        assert sha(stage / name) == digest, name
    baseline = json.loads((stage / "baseline.json").read_text())
    verify_base(baseline)
    source = stage / "backend"
    assert set(tree_hashes(source)) == CHANGED_SOURCE
    assert (stage / "frontend/index.html").is_file()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-v2262-minute-loading"
    backup = Path(os.environ.get("ETF_RELEASE_BACKUPS", str(app.parent / "etf-monitor-backups"))) / stamp
    backup.mkdir(mode=0o700)
    prepared_backend, previous_backend = app / (".etfmon-" + stamp), app / (".etfmon-before-" + stamp)
    prepared_web, previous_web = web.parent / (".web-" + stamp), web.parent / (".web-before-" + stamp)
    pairs = ((backend, prepared_backend, previous_backend), (web, prepared_web, previous_web))
    switched = []
    stopped = False
    try:
        shutil.copytree(backend, prepared_backend, ignore=shutil.ignore_patterns("__pycache__"))
        for name in CHANGED_SOURCE:
            shutil.copyfile(source / name, prepared_backend / name)
        owner = backend.stat()
        for path in (prepared_backend, *prepared_backend.rglob("*")):
            os.chown(path, owner.st_uid, owner.st_gid)
        shutil.copytree(web, prepared_web)
        shutil.copytree(stage / "frontend", prepared_web, dirs_exist_ok=True)
        published = tree_hashes(prepared_web)
        set_frontend_permissions(prepared_web, published)
        check_frontend_worker_access(prepared_web, published)
        verify_base(baseline)
        run("systemctl", "stop", service)
        stopped = True
        ledger = app / "data/research/paper/trades.sqlite3"
        original = read_database(ledger)
        # Dedicated new historical namespace only, with the collector stopped.
        # Existing quote/share/ledger archives are never overwritten by backfill.
        target = app / "data/national/turnover_5m"
        target.mkdir(exist_ok=True)
        data_owner = (app / "data/national").stat()
        os.chown(target, data_owner.st_uid, data_owner.st_gid)
        for path in (stage / "turnover_history").glob("*.json"):
            payload = json.loads(path.read_text())
            assert payload["interval"] == 5 and payload["unit"] == "CNY"
            destination = target / path.name
            if destination.exists():
                assert sha(destination) == sha(path), "Existing baseline file differs"
            else:
                shutil.copyfile(path, destination)
                os.chown(destination, data_owner.st_uid, data_owner.st_gid)
                destination.chmod(0o600)
        for live, candidate, previous in pairs:
            os.replace(live, previous)
            switched.append((live, candidate, previous))
            os.replace(candidate, live)
        check_frontend_worker_access(web, published)
        # Refresh the existing derived chart while its writer is stopped. An
        # invalid old weight cache otherwise makes every cold Team GET rebuild
        # the same chart alongside all startup workers.
        chart = app / "data/national/chart.json"
        if chart.exists():
            shutil.copyfile(chart, backup / "previous-chart.json")
        service_user = run("systemctl", "show", "-p", "User", "--value", service) or "root"
        run("runuser", "-u", service_user, "--", "env", f"ETF_DATA={app / 'data'}", f"PYTHONPATH={app}",
            sys.executable, "-c", "from etfmon import store,views; store.write_json(store.path_for('national','chart.json'),views.compute_chart_cache(),compact=True)")
        run("systemctl", "start", service)
        for _ in range(30):
            try:
                health = api("/api/health")[1]
                if health["version"] == VERSION:
                    break
            except OSError:
                pass
            time.sleep(.4)
        else:
            raise RuntimeError("New version did not start")
        verify_access()
        team_times = {}
        for user in ("tom", "test"):
            started = time.monotonic()
            local_api = os.environ.get("ETF_API_LOCAL", "http://127.0.0.1:8765")
            with urlopen(Request(local_api + "/api/team", headers={"Remote-User": user}), timeout=15) as response:
                status, team = response.status, json.load(response)
            team_times[user] = round((time.monotonic() - started) * 1000)
            assert status == 200 and team["basket"]["count"] == 26
            assert len(team["turnover"]["rows"]) == 26
            assert team["turnover"]["baseline_days"] == 20
            assert team["turnover"]["baseline_interval"] == 5
            assert team["live_flow"] is None or team["live_flow"]["basis"] == "legacy_snapshot_difference"
            with urlopen(Request(local_api + "/api/overview", headers={"Remote-User": user}), timeout=15) as response:
                overview = json.load(response)
                assert response.status == 200 and overview["market_activity"]["scope"] == "shanghai_shenzhen"
                assert overview["market_activity"]["unit"] == "CNY"
                assert len(overview["market_activity"]["markets"]) == 2
        after = read_database(ledger)
        for event_id, trade in original.items():
            current = after[event_id]
            immutable = ("id", "code", "name", "buy_event_id", "entry_time", "entry_price", "quantity")
            assert all(current[key] == trade[key] for key in immutable), "Original simulated entry changed"
            if trade["exit_event_id"] is not None:
                assert all(current[key] == trade[key] for key in ("exit_event_id", "exit_time", "exit_price", "exit_reason"))
        receipt = {"version": VERSION, "deployed": True, "backend_api_roles_verified": True,
                   "frontend_worker_readable": True, "frontend_files": len(published),
                   "entry_sha256": sha(web / "index.html"), "existing_trades_verified": len(original),
                   "team_api_ms": team_times,
                   "authenticated_browser_acceptance": "pending", "backup": str(backup),
                   "rollback_backend": str(previous_backend), "rollback_frontend": str(previous_web)}
        (backup / "deployment-receipt.json").write_text(json.dumps(receipt, indent=2))
        print(json.dumps(receipt), flush=True)
    except BaseException:
        if stopped:
            subprocess.run(["systemctl", "stop", service], check=False)
            for live, candidate, previous in reversed(switched):
                if live.exists():
                    os.replace(live, candidate)
                os.replace(previous, live)
            run("systemctl", "start", service)
        raise
    finally:
        for path in (prepared_backend, prepared_web):
            if path.exists():
                shutil.rmtree(path)


if __name__ == "__main__":
    os.umask(0o077)
    lock_path = Path(os.environ.get("ETF_RELEASE_BACKUPS", str(app.parent / "etf-monitor-backups"))) / ".v220-deploy.lock"
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        publish(Path(sys.argv[1]))
