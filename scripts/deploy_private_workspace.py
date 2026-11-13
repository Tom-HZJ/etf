"""Publish the private workspace against the verified live release."""

from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import fcntl
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

from deploy_paper_review import (
    app, backend, check_frontend_worker_access, run, service,
    read_database, set_frontend_permissions, sha, tree_hashes, web,
)

VERSION = '2.24.0'
BASE_VERSION = '2.23.0'
CHANGED_SOURCE = {'config.py', 'paper.py'}


def api(path, user=None, *, method='GET'):
    headers = {'Remote-User': user} if user else {}
    if method == 'POST':
        headers.update({'X-ETF-Client': 'web-v1', 'Content-Type': 'application/json'})
    request = Request('http://127.0.0.1:8765' + path, headers=headers, method=method,
                      data=b'{}' if method == 'POST' else None)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        with error:
            return error.code, json.load(error)


def verify_access():
    for user, status in ((None, 401), ('test', 403), ('tom', 200)):
        funds_status, funds = api('/api/private/funds', user)
        assert funds_status == status, ('funds', user, funds_status)
        stock_status, stock = api('/api/private/stock?code=sz002258', user)
        assert stock_status == status, ('stock', user, stock_status)
        if user == 'tom':
            assert len(funds['items']) == 8, 'Unexpected private snapshot count'
            assert stock['as_of'] and stock['bars'], 'Tracked stock bars unavailable'
            assert stock['comparison']['rows'] and stock['forecast'], 'Tracked stock archive unavailable'
    assert api('/api/research/actions', 'test')[0] == 200
    assert api('/api/watchlist', 'test', method='POST')[0] == 403
    status, desk = api('/api/research/actions?model=expanded&protection=none', 'test')
    excluded = {'sh515210', 'sh512660', 'sh512810'}
    assert status == 200 and excluded.issubset(desk['trade_scope']['user_excluded_codes'])
    assert all(row['code'] not in excluded for row in desk['rows'])
    status, review = api('/api/research/paper-trades?status=open', 'test')
    assert status == 200
    assert all('floating_pnl' in row and 'mark_time' in row for row in review['trades'])


def remove_requested_trades(stage, original_trades, backup, ledger):
    requests = json.loads((stage / 'removals.json').read_text())
    for request in requests:
        original = original_trades[request['buy_event_id']]
        assert all(original[key] == value for key, value in request.items()), 'Removal target changed'
    with closing(sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True)) as original:
        with closing(sqlite3.connect(backup / 'trades-before-removal.sqlite3')) as saved:
            original.backup(saved)
    (backup / 'trades-before-removal.sqlite3').chmod(0o600)
    service_user = run('systemctl', 'show', '-p', 'User', '--value', service) or 'root'
    command = '''
import json,sys
from etfmon import paper,store,actions
requests=json.loads(sys.argv[1])
with paper.connect() as db:
    db.execute('BEGIN IMMEDIATE')
    for request in requests:
        assert paper.remove_trade(db,request['buy_event_id'],'user requested removal',store.now())
    db.commit()
    snapshot=store.read_json(actions.path('latest.json'),{})
    snapshot['paper_ledger']=paper.snapshot(db)
    store.write_json(actions.path('latest.json'),snapshot,compact=True)
'''
    run('runuser', '-u', service_user, '--', 'env', f'ETF_DATA={app / "data"}',
        f'PYTHONPATH={app}', 'PYTHONDONTWRITEBYTECODE=1', sys.executable, '-c', command,
        json.dumps(requests))
    return {request['buy_event_id'] for request in requests}


def verify_base(baseline):
    status, health = api('/api/health')
    assert status == 200 and health['version'] == BASE_VERSION, 'Unexpected live version'
    # This source-only update stops the collector during both directory swaps.
    assert run('systemctl', 'is-active', service) == 'active'
    assert tree_hashes(backend, '*.py') == baseline['backend'], 'Live backend changed during preparation'
    assert sha(web / 'index.html') == baseline['entry_sha256'], 'Live frontend changed during preparation'


def publish(stage):
    manifest = json.loads((stage / 'manifest.json').read_text())
    for name, digest in manifest.items():
        assert sha(stage / name) == digest, name
    baseline = json.loads((stage / 'baseline.json').read_text())
    verify_base(baseline)
    source = stage / 'backend'
    assert set(tree_hashes(source)) == CHANGED_SOURCE
    assert (stage / 'frontend/index.html').is_file()

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-v224-requested-removals'
    backup = Path('/opt/etf-monitor-backups') / stamp
    backup.mkdir(mode=0o700)
    prepared_backend = app / ('.etfmon-' + stamp)
    previous_backend = app / ('.etfmon-before-' + stamp)
    prepared_web = web.parent / ('.web-' + stamp)
    previous_web = web.parent / ('.web-before-' + stamp)
    pairs = ((backend, prepared_backend, previous_backend), (web, prepared_web, previous_web))

    try:
        shutil.copytree(backend, prepared_backend, ignore=shutil.ignore_patterns('__pycache__'))
        for name in CHANGED_SOURCE:
            shutil.copyfile(source / name, prepared_backend / name)
        owner = backend.stat()
        for path in (prepared_backend, *prepared_backend.rglob('*')):
            os.chown(path, owner.st_uid, owner.st_gid)

        # Preserve hashed assets still used by tabs opened before this release.
        shutil.copytree(web, prepared_web)
        shutil.copytree(stage / 'frontend', prepared_web, dirs_exist_ok=True)
        published_files = tree_hashes(prepared_web)
        set_frontend_permissions(prepared_web, published_files)
        check_frontend_worker_access(prepared_web, published_files)

        verify_base(baseline)
        stopped = False
        try:
            run('systemctl', 'stop', service)
            stopped = True
            ledger = app / 'data/research/paper/trades.sqlite3'
            original_trades = read_database(ledger)
            for live, candidate, previous in pairs:
                os.replace(live, previous)
                os.replace(candidate, live)
            check_frontend_worker_access(web, published_files)
            removed = remove_requested_trades(stage, original_trades, backup, ledger)
            run('systemctl', 'start', service)
            for _ in range(25):
                try:
                    status, health = api('/api/health')
                    if status == 200 and health['version'] == VERSION:
                        break
                except OSError:
                    pass
                time.sleep(.4)
            else:
                raise RuntimeError(f'{VERSION} did not start')
            verify_access()
            current_trades = read_database(ledger)
            assert not removed.intersection(current_trades), 'Removed simulation reappeared'
            for event_id, trade in original_trades.items():
                if event_id in removed:
                    continue
                assert event_id in current_trades, 'An existing trade disappeared'
                current = current_trades[event_id]
                immutable = ('id', 'code', 'name', 'buy_event_id', 'entry_time', 'entry_price', 'quantity')
                assert all(current[field] == trade[field] for field in immutable)
                if trade['exit_event_id'] is not None:
                    assert all(current[field] == trade[field] for field in
                               ('exit_event_id', 'exit_time', 'exit_price', 'exit_reason'))
            receipt = {
                'version': VERSION, 'health': health['status'], 'backend_api_roles_verified': True,
                'frontend_worker_readable': True, 'frontend_files': len(published_files),
                'entry_sha256': sha(web / 'index.html'), 'authenticated_browser_acceptance': 'pending',
                'rollback_backend': str(previous_backend), 'rollback_frontend': str(previous_web),
                'verified_existing_trades': len(original_trades),
                'removed_trade_count': len(removed),
            }
            (backup / 'deployment-receipt.json').write_text(json.dumps(receipt, indent=2))
            print(json.dumps(receipt), flush=True)
        except BaseException:
            if stopped:
                subprocess.run(['systemctl', 'stop', service], check=False)
                for live, candidate, previous in pairs:
                    if previous.exists():
                        if live.exists():
                            os.replace(live, candidate)
                        os.replace(previous, live)
                run('systemctl', 'start', service)
            raise
    finally:
        for path in (prepared_backend, prepared_web):
            if path.exists():
                shutil.rmtree(path)


if __name__ == '__main__':
    os.umask(0o077)
    with open('/opt/etf-monitor-backups/.v220-deploy.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        publish(Path(sys.argv[1]))
