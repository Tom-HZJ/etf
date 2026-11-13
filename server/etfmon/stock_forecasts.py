"""Record issued stock forecasts and compare them with completed source bars."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import date, datetime, time, timedelta
from statistics import mean

from . import config, store

CODE = 'sz002258'
NAME = '利尔化学'
# SZSE notice 深证会〔2025〕481号. Extend coverage only from a published calendar.
HOLIDAYS = {
    2026: (('01-01', '01-03'), ('02-15', '02-23'), ('04-04', '04-06'),
           ('05-01', '05-05'), ('06-19', '06-21'), ('09-25', '09-27'), ('10-01', '10-07')),
}


def owner_path(*parts: str):
    return config.data_dir().joinpath('private', 'tom', *parts)


def trading_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    holidays = HOLIDAYS.get(day.year)
    if holidays is None:
        raise ValueError(f'{day.year} 年深交所交易日历待更新')
    month_day = day.isoformat()[5:]
    return not any(start <= month_day <= end for start, end in holidays)


def next_sessions(day: date) -> list[str]:
    sessions = []
    while len(sessions) < 3:
        day += timedelta(days=1)
        if trading_day(day):
            sessions.append(day.isoformat())
    return sessions


def minute_clock(day: date) -> list[datetime]:
    return [datetime.combine(day, time(hour, minute))
            for start, end in ((575, 690), (785, 900))
            for hour, minute in (divmod(i, 60) for i in range(start, end + 1, 5))]


def minute_window(now: datetime) -> tuple[str, list[str]] | None:
    local = now.astimezone(config.SHANGHAI_TZ).replace(tzinfo=None)
    clock = minute_clock(local.date())
    completed = [stamp for stamp in clock if stamp <= local - timedelta(seconds=30)]
    if not completed or local.time() >= time(15):
        return None
    cutoff = completed[-1]
    morning = cutoff.hour < 12
    if morning != (local.hour < 12) or local.time() >= time(11, 30) and morning:
        return None
    targets = [stamp for stamp in clock if stamp > local and (stamp.hour < 12) == morning][:3]
    if not targets:
        return None
    return cutoff.isoformat(sep=' ', timespec='minutes'), [
        stamp.isoformat(sep=' ', timespec='minutes') for stamp in targets
    ]


def archive_path():
    return owner_path('forecast-history.sqlite3')


@contextmanager
def writable_archive():
    path = archive_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS issues (as_of TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        db.execute('''CREATE TABLE IF NOT EXISTS actuals (
            as_of TEXT NOT NULL, target TEXT NOT NULL, close REAL NOT NULL,
            comparable REAL NOT NULL, PRIMARY KEY (as_of, target))''')
        yield db


def archive_issue(payload):
    if payload['code'] != CODE or payload['adjustment'] != 'qfq':
        raise ValueError('留档需要利尔化学前复权预测')
    issued = store.parse_iso(payload['generated_at'])
    first = datetime.fromisoformat(payload['sessions'][0]['date']).replace(tzinfo=config.SHANGHAI_TZ)
    if issued is None or issued.date().isoformat() != payload['as_of'] or issued >= first:
        raise ValueError('预测须在目标交易日开始前留档')
    with writable_archive() as db:
        db.execute('INSERT OR IGNORE INTO issues VALUES (?, ?)',
                   (payload['as_of'], json.dumps(payload, allow_nan=False)))


def settle(bars):
    closes = {row['date']: row['close'] for row in bars}
    with writable_archive() as db:
        for as_of, payload in db.execute('SELECT as_of, payload FROM issues'):
            if as_of not in closes:
                continue
            issue = json.loads(payload)
            # Later qfq bars can rebase past prices after a corporate action.
            scale = issue['baseline_close'] / closes[as_of]
            db.executemany('INSERT OR IGNORE INTO actuals VALUES (?, ?, ?, ?)', [
                (as_of, target, closes[target], closes[target] * scale)
                for target in (point['date'] for point in issue['sessions']) if target in closes
            ])


def report(latest_day: str | None):
    path = archive_path()
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        issues = [json.loads(row[0]) for row in db.execute('SELECT payload FROM issues ORDER BY as_of DESC')]
        actuals = {(as_of, target): (close, comparable)
                   for as_of, target, close, comparable in db.execute('SELECT * FROM actuals')}
    rows = []
    for issue in issues:
        for horizon, point in enumerate(issue['sessions'], 1):
            target = point['date']
            close, comparable = actuals.get((issue['as_of'], target), (None, None))
            base = issue['baseline_close']
            rows.append({
                'as_of': issue['as_of'], 'issued_at': issue['generated_at'], 'target': target,
                'horizon': horizon, 'prediction': point['close'], 'baseline': base,
                'actual': close, 'comparable': comparable,
                'status': 'scored' if comparable is not None else
                          'missing' if latest_day and target <= latest_day else 'pending',
                'error': abs(point['close'] - comparable) if comparable is not None else None,
                'baseline_error': abs(base - comparable) if comparable is not None else None,
            })
    metrics = []
    for horizon in range(1, 4):
        matches = [row for row in rows if row['horizon'] == horizon and row['status'] == 'scored']
        metrics.append({
            'horizon': horizon, 'count': len(matches),
            'mae': mean(row['error'] for row in matches) if matches else None,
            'baseline_mae': mean(row['baseline_error'] for row in matches) if matches else None,
        })
    return {'latest': issues[0] if issues else None, 'metrics': metrics, 'rows': rows[:90], 'total': len(rows)}
