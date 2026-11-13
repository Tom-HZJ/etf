"""Deploy the paper review release from a verified v2.19.0 bundle baseline."""

from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

app = Path('/opt/etf-monitor')
web = Path('/srv/etf-web/web')
data = app / 'data'
backend = app / 'etfmon'
ledger = data / 'research' / 'paper' / 'trades.sqlite3'
service = 'etf-monitor'
version = '2.20.0'

def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def tree_hashes(root, pattern='*'):
    return {p.relative_to(root).as_posix(): sha(p) for p in root.rglob(pattern)
            if p.is_file() and '__pycache__' not in p.parts}

def set_frontend_permissions(root, files):
    # Build and tar creation may inherit a restrictive umask from the operator.
    directories = {root}
    for name in files:
        path = root / name
        path.chmod(0o644)
        directories.update(parent for parent in path.parents if parent.is_relative_to(root))
    for directory in directories:
        directory.chmod(0o755)

def check_frontend_worker_access(root, files):
    probe = 'import sys\nfor path in sys.argv[1:]:\n    with open(path, "rb") as handle:\n        handle.read(1)'
    result = subprocess.run(
        ['runuser', '-u', 'www-data', '--', sys.executable, '-c', probe,
         *(str(root/name) for name in files)], capture_output=True, text=True,
    )
    if result.returncode:
        raise RuntimeError(f'Nginx worker cannot read frontend files: {result.stderr.strip()}')

def get(path, authorized=True):
    headers = {'Remote-User': 'tom'} if authorized else {}
    with urlopen(Request('http://127.0.0.1:8765' + path, headers=headers), timeout=12) as response:
        return json.load(response)

def read_database(path):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        return {row['buy_event_id']: dict(row) for row in db.execute('SELECT * FROM trades')}

def require_backfill(before, after):
    assert before.keys() == after.keys(), 'Trade identities changed during migration'
    for event_id, old in before.items():
        new = after[event_id]
        assert all(new[k] == v for k, v in old.items() if k != 'quantity'), 'Original trade was modified'
        price = Decimal(str(new['entry_price']))
        quantity = new['quantity']
        assert isinstance(quantity, int) and quantity > 0 and quantity % 100 == 0
        assert price * quantity > 10_000 >= price * (quantity - 100)

def check_unauthenticated():
    try:
        get('/api/research/paper-trades', authorized=False)
    except HTTPError as error:
        assert error.code == 401, error.code
    else:
        raise AssertionError('The review endpoint must require authentication')

def verify_payload(expected):
    base = '/api/research/paper-trades?start=2026-11-10&end=2026-11-10'
    page = get(base + '&page=1')
    assert (page['entry_threshold'], page['lot_size']) == (10_000, 100)
    trades = {row['buy_event_id']: row for row in page['trades']}
    for number in range(2, (page['total'] + page['page_size'] - 1) // page['page_size'] + 1):
        trades.update((row['buy_event_id'], row) for row in get(base + f'&page={number}')['trades'])
    for event_id, old in expected.items():
        actual = trades[event_id]
        assert all(actual[k] == old[k] for k in ('code', 'entry_time', 'entry_price', 'quantity'))
    return page['total']

def main(stage):
    candidate_source = stage / 'source' / 'server' / 'etfmon'
    baseline = json.loads((stage/'baseline.json').read_text())
    manifest = json.loads((stage/'manifest.json').read_text())
    for name, expected in manifest.items():
        assert sha(stage/name) == expected, name
    frontend = tree_hashes(stage/'frontend')
    assert 'index.html' in frontend, 'Missing frontend entry point'
    old_health = get('/api/health')
    assert old_health['version'] == '2.19.0', old_health['version']
    assert old_health['session'] not in ('morning', 'afternoon'), 'Defer until the trading session ends'
    assert run('systemctl', 'is-active', service) == 'active'
    assert tree_hashes(backend, '*.py') == baseline['backend'], 'Production source changed since v2.19.0'
    assert sha(web/'index.html') == baseline['entry_sha256'], 'Production frontend changed since v2.19.0'
    candidate_hash = tree_hashes(candidate_source, '*.py')
    changed = {name for name in baseline['backend'].keys() | candidate_hash.keys()
               if baseline['backend'].get(name) != candidate_hash.get(name)}
    assert changed == {'api.py', 'config.py', 'paper.py'}, changed
    expected_trades = {row['buy_event_id']: row for row in baseline['original_trades']}
    assert len(expected_trades) == 14
    rows = read_database(ledger)
    for event_id, record in expected_trades.items():
        assert all(rows[event_id][field] == record[field] for field in ('code', 'entry_time', 'entry_price'))
    watchlist_sha = sha(data/'watchlist.json')

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-v2200-paper-review'
    backup = Path('/opt/etf-monitor-backups') / stamp
    backup.mkdir(mode=0o700)
    shutil.copytree(backend, backup/'etfmon')
    shutil.copytree(web, backup/'web')
    prepared_backend = app / ('.etfmon-' + stamp)
    previous_backend = app / ('.etfmon-before-' + stamp)
    prepared_web = web.parent / ('.web-' + stamp)
    previous_web = web.parent / ('.web-before-' + stamp)
    shutil.copytree(candidate_source, prepared_backend)
    shutil.copytree(web, prepared_web)
    shutil.copytree(stage/'frontend', prepared_web, dirs_exist_ok=True)
    for original, candidate in ((backend, prepared_backend), (web, prepared_web)):
        owner = original.stat()
        for path in [candidate, *candidate.rglob('*')]:
            os.chown(path, owner.st_uid, owner.st_gid)
    set_frontend_permissions(prepared_web, frontend)
    check_frontend_worker_access(prepared_web, frontend)
    assert tree_hashes(backend, '*.py') == baseline['backend']
    service_user = run('systemctl', 'show', '-p', 'User', '--value', service) or 'root'
    latest_session = get('/api/health')['session']
    assert latest_session not in ('morning', 'afternoon'), 'Trading began while preparing'
    original_db = backup/'paper-trades-before.sqlite3'
    try:
        run('systemctl', 'stop', service)
        before = read_database(ledger)
        with closing(sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True)) as original:
            with closing(sqlite3.connect(original_db)) as saved:
                original.backup(saved)
        os.chmod(original_db, 0o600)
        assert read_database(original_db) == before

        os.replace(backend, previous_backend)
        os.replace(prepared_backend, backend)
        os.replace(web, previous_web)
        os.replace(prepared_web, web)
        check_frontend_worker_access(web, frontend)
        run('runuser', '-u', service_user, '--', 'env', f'ETF_DATA={data}', f'PYTHONPATH={app}',
            'PYTHONDONTWRITEBYTECODE=1', sys.executable, '-c',
            'from etfmon import paper\nwith paper.connect():\n    pass')
        migrated = read_database(ledger)
        require_backfill(before, migrated)

        run('systemctl', 'start', service)
        for _ in range(90):
            try:
                health = get('/api/health')
                if health['version'] == version:
                    break
            except OSError:
                pass
            time.sleep(.5)
        else:
            raise RuntimeError('v2.20.0 did not become ready')
        check_unauthenticated()
        reviews = verify_payload({event_id: migrated[event_id] for event_id in expected_trades})
        assert sha(data/'watchlist.json') == watchlist_sha
        assert tree_hashes(backend, '*.py') == candidate_hash
        for rel, digest in frontend.items():
            assert sha(web/rel) == digest, rel
        archive = app/'upgrades'/stamp
        archive.parent.mkdir(exist_ok=True)
        shutil.copytree(stage/'source', archive)
        result = {
            'version': version, 'deployed': True, 'backup': str(backup), 'source': str(archive),
            'migrated_trades': len(migrated), 'verified_original_trades': len(expected_trades),
            'open_positions': sum(row['exit_event_id'] is None for row in migrated.values()),
            'existing_exits': sum(row['exit_event_id'] is not None for row in migrated.values()),
            'review_day_count': reviews, 'integrity_check': 'ok',
            'frontend_sha256': sha(web/'index.html'), 'watchlist_preserved': True,
            'frontend_worker_readable': True, 'authenticated_browser_acceptance': 'pending',
            'health': health['status'], 'service_active': run('systemctl', 'is-active', service) == 'active',
            'verified_at': datetime.now(timezone.utc).isoformat(),
        }
        (archive/'deployment-receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False), flush=True)
    except BaseException:
        subprocess.run(['systemctl', 'stop', service], check=False)
        for live, prior in ((backend, previous_backend), (web, previous_web)):
            if prior.exists():
                if live.exists():
                    os.replace(live, live.parent/('.failed-' + live.name + '-' + stamp))
                os.replace(prior, live)
        # The additive SQLite column is compatible with v2.19.0. Preserve observations.
        run('systemctl', 'start', service)
        raise

if __name__ == '__main__':
    with open('/opt/etf-monitor-backups/.v220-deploy.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        main(Path(sys.argv[1]))
