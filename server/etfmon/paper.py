"""Durable reference-price simulation. Collector writes; never broker orders.

Only actual recorded discoveries since the user's chosen starting day enter
this ledger. Cash figures reflect simulated reference prices and whole lots.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from . import store, research, config

POLICY = 'observed-points-paper-ledger-v1'
START_DATE = '2026-11-10'
MODEL, PROTECTION = 'expanded', 'none'
LOT_SIZE = 100
ENTRY_THRESHOLD = 10_000
PAGE_SIZE = 25
CENT = Decimal('0.01')


def db_path():
    return config.data_dir() / 'research' / 'paper' / 'trades.sqlite3'


def entry_quantity(price):
    """Smallest whole-lot quantity strictly above the entry threshold."""
    numerator, denominator = Decimal(str(price)).as_integer_ratio()
    quantity = (ENTRY_THRESHOLD * denominator // (LOT_SIZE * numerator) + 1) * LOT_SIZE
    return quantity if quantity <= 2**63 - 1 else None


def _cash(price, quantity):
    if price is None or quantity is None:
        return None
    return (Decimal(str(price)) * quantity).quantize(CENT, rounding=ROUND_HALF_UP)


def _quantity(row):
    stored = row['quantity'] if 'quantity' in row.keys() else None
    return stored if stored is not None else entry_quantity(row['entry_price'])


def _migrate_quantity(db):
    # Older writers can still append rows while rolling back the application.
    # Backfill those rows from their original entry reference on the next start.
    with db:
        db.execute('BEGIN IMMEDIATE')
        if not any(row['name'] == 'quantity' for row in db.execute('PRAGMA table_info(trades)')):
            db.execute('ALTER TABLE trades ADD COLUMN quantity INTEGER CHECK(quantity >= 100 AND quantity % 100 = 0)')
        missing = db.execute('SELECT id,entry_price FROM trades WHERE quantity IS NULL').fetchall()
        db.executemany('UPDATE trades SET quantity=? WHERE id=?',
                       ((entry_quantity(row['entry_price']), row['id']) for row in missing))


@contextmanager
def connect():
    target = db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(target, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        db.executescript('''
          CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL,
            model TEXT NOT NULL, protection TEXT NOT NULL,
            buy_event_id TEXT NOT NULL UNIQUE, entry_time TEXT NOT NULL,
            entry_price REAL NOT NULL CHECK(entry_price>0), initial_stop REAL NOT NULL,
            target_price REAL NOT NULL, buy_event TEXT NOT NULL,
            exit_event_id TEXT UNIQUE, exit_time TEXT, exit_price REAL,
            exit_reason TEXT, exit_event TEXT,
            quantity INTEGER CHECK(quantity >= 100 AND quantity % 100 = 0)
          );
          CREATE UNIQUE INDEX IF NOT EXISTS one_open_position
            ON trades(code,model,protection) WHERE exit_event_id IS NULL;
          CREATE UNIQUE INDEX IF NOT EXISTS one_buy_per_day
            ON trades(code,model,protection,substr(entry_time,1,10));
          CREATE TABLE IF NOT EXISTS removed_trades (
            buy_event_id TEXT PRIMARY KEY, code TEXT NOT NULL, entry_date TEXT NOT NULL,
            removed_at TEXT NOT NULL, reason TEXT NOT NULL, original_trade TEXT NOT NULL,
            UNIQUE(code,entry_date)
          );
        ''')
        _migrate_quantity(db)
        with db:
            yield db
    finally:
        db.close()


def valid_event(event, side, moment):
    at = store.parse_iso(event.get('observed_at'))
    trigger = store.parse_iso(event.get('trigger_time'))
    price = research.number(event.get('reference_price'))
    if not (event.get('live_recorded') is True and event.get('event_id')
            and config.is_etf_code(str(event.get('code','')))
            and event.get('code') not in config.USER_EXCLUDED_TRADE_CODES
            and event.get('model') == MODEL and event.get('protection') == PROTECTION
            and event.get('kind') in side and at and trigger and price and price > 0
            and START_DATE <= at.astimezone(config.SHANGHAI_TZ).date().isoformat()
            and at <= moment and 0 <= (at-trigger).total_seconds() <= 90
            and at.astimezone(config.SHANGHAI_TZ).date() == trigger.astimezone(config.SHANGHAI_TZ).date()):
        return False
    local = at.astimezone(config.SHANGHAI_TZ)
    minute = local.hour*60+local.minute
    return local.weekday()<5 and (570<=minute<690 or 780<=minute<900)


def buy(db, event, moment):
    if not valid_event(event, ('buy',), moment):
        return False
    if db.execute('SELECT 1 FROM removed_trades WHERE buy_event_id=? OR (code=? AND entry_date=?)',
                  (event['event_id'], event['code'], event['observed_at'][:10])).fetchone():
        return False
    price = float(event['reference_price'])
    stop = research.number(event.get('stop_price'))
    quantity = entry_quantity(price)
    if quantity is None or not stop or not 0 < stop < price:
        return False
    result = db.execute('''INSERT OR IGNORE INTO trades
      (code,name,model,protection,buy_event_id,entry_time,entry_price,initial_stop,target_price,buy_event,quantity)
      VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
      (event['code'],event['name'],MODEL,PROTECTION,event['event_id'],event['observed_at'],
       price,stop,price+1.5*(price-stop),json.dumps(event,ensure_ascii=False,allow_nan=False),quantity))
    return result.rowcount == 1


def remove_trade(db, buy_event_id, reason, moment):
    """Remove one explicitly selected simulation and remember its identity."""
    row = db.execute('SELECT * FROM trades WHERE buy_event_id=?', (buy_event_id,)).fetchone()
    if row is None:
        return False
    db.execute('INSERT INTO removed_trades VALUES (?,?,?,?,?,?)',
               (buy_event_id, row['code'], row['entry_time'][:10], moment.isoformat(timespec='seconds'),
                reason, json.dumps(dict(row), ensure_ascii=False, allow_nan=False)))
    db.execute('DELETE FROM trades WHERE buy_event_id=?', (buy_event_id,))
    return True


def sell(db, event, moment):
    if not valid_event(event, ('sell','risk'), moment) or event.get('simulated_position') is not True:
        return False
    pos = db.execute('SELECT * FROM trades WHERE code=? AND model=? AND protection=? AND exit_event_id IS NULL',
                     (event['code'],MODEL,PROTECTION)).fetchone()
    if pos is None:
        return False
    entry = store.parse_iso(pos['entry_time'])
    at = store.parse_iso(event['observed_at'])
    if (at.astimezone(config.SHANGHAI_TZ).date() <= entry.astimezone(config.SHANGHAI_TZ).date()
            or event.get('paper_buy_event_id') != pos['buy_event_id']):
        return False
    result = db.execute('''UPDATE trades SET exit_event_id=?,exit_time=?,exit_price=?,exit_reason=?,exit_event=?
                           WHERE id=? AND exit_event_id IS NULL''',
                        (event['event_id'],event['observed_at'],event['reference_price'],event['reason'],
                         json.dumps(event,ensure_ascii=False,allow_nan=False),pos['id']))
    return result.rowcount == 1


def open_positions(db):
    return {r['code']:dict(r) for r in db.execute('SELECT * FROM trades WHERE exit_event_id IS NULL')}


def recorded_events(db, date):
    """Recover original observations after a crash between SQLite and JSON writes."""
    events=[]
    for row in db.execute('SELECT buy_event,exit_event FROM trades WHERE substr(entry_time,1,10)=? OR substr(exit_time,1,10)=?',(date,date)):
        for raw in row:
            if raw:
                event=json.loads(raw)
                if event['observed_at'][:10]==date:events.append(event)
    return events


def snapshot(db):
    rows = [_trade_row(row) for row in db.execute(
        'SELECT * FROM trades ORDER BY exit_event_id IS NULL DESC,entry_time DESC,id DESC LIMIT 200')]
    count=db.execute('SELECT count(*) FROM trades WHERE exit_event_id IS NULL').fetchone()[0]
    return dict(policy=POLICY,start_date=START_DATE,model=MODEL,protection=PROTECTION,
                execution_basis='observed_quote_reference',simulated=True,open_count=count,trades=rows)


def _trade_row(row):
    quantity = _quantity(row)
    entry_amount = _cash(row['entry_price'], quantity)
    exit_amount = _cash(row['exit_price'], quantity)
    gross_pnl = exit_amount - entry_amount if exit_amount is not None else None
    gross_return = gross_pnl / entry_amount if gross_pnl is not None else None
    return {**{key: row[key] for key in (
        'id', 'code', 'name', 'buy_event_id', 'entry_time', 'entry_price', 'initial_stop',
        'target_price', 'exit_event_id', 'exit_time', 'exit_price', 'exit_reason')},
        'quantity': quantity,
        'entry_amount': float(entry_amount) if entry_amount is not None else None,
        'exit_amount': float(exit_amount) if exit_amount is not None else None,
        'gross_pnl': float(gross_pnl) if gross_pnl is not None else None,
        'gross_return': float(gross_return) if gross_return is not None else None,
    }


def _mark_position(trade, quote, moment):
    """Value an open holding from the existing quote cache without ledger writes.

    估值口径分两种：

    - **交易时段**：报价必须是当天的、且不超过 STALE_AFTER_S，否则不算——
      盘中的旧价会把浮盈算错。
    - **非交易时段**（收盘后/周末/节假日）：用最近一个交易日的收盘价估値，
      但把 ``mark_basis`` 标成 ``last_close``。否则整周末台账一片空白，
      看上去像没有数据。
    """
    stamp = store.parse_iso(quote.get('quote_time'))
    price = research.number(quote.get('price'))
    if price is None or price <= 0:
        return {'mark_price': None, 'mark_time': quote.get('quote_time'),
                'mark_stale': True, 'mark_basis': None,
                'floating_pnl': None, 'floating_return': None}

    trading = store.is_trading_time(moment)
    intraday_stale = bool(
        quote.get('stale')
        or stamp is None
        or stamp > moment
        or (trading and stamp.astimezone(config.SHANGHAI_TZ).date() != moment.date())
        or (trading and (moment - stamp).total_seconds() > config.STALE_AFTER_S)
    )

    # 非交易时段：只接受「不晚于当下」的报价，当作最近收盘价。
    if not trading and stamp is not None and stamp <= moment:
        basis = 'last_close' if stamp.astimezone(config.SHANGHAI_TZ).date() != moment.date() else 'quote'
        marked = _cash(price, trade['quantity'])
        profit = marked - Decimal(str(trade['entry_amount']))
        return {
            'mark_price': price,
            'mark_time': quote.get('quote_time'),
            'mark_stale': False,
            'mark_basis': basis,
            'floating_pnl': float(profit),
            'floating_return': float(profit / Decimal(str(trade['entry_amount']))),
        }

    marked = None if intraday_stale else _cash(price, trade['quantity'])
    profit = marked - Decimal(str(trade['entry_amount'])) if marked is not None else None
    return {
        'mark_price': price if marked is not None else None,
        'mark_time': quote.get('quote_time'),
        'mark_stale': intraday_stale,
        'mark_basis': 'quote' if marked is not None else None,
        'floating_pnl': float(profit) if profit is not None else None,
        'floating_return': float(profit / Decimal(str(trade['entry_amount']))) if profit is not None else None,
    }


def review(status='all', start=None, end=None, page=1, *, quotes=None):
    """Read the durable ledger, even before the first new collector snapshot."""
    if status not in {'all', 'open', 'closed'} or not 1 <= page <= 1_000_000:
        raise ValueError('无效的模拟交易筛选或页码')
    for value in (start, end):
        if value is not None and date.fromisoformat(value).isoformat() != value:
            raise ValueError('日期格式应为 YYYY-MM-DD')
    if start and end and start > end:
        raise ValueError('开始日期不能晚于结束日期')

    result = dict(simulated=True, lot_size=LOT_SIZE, entry_threshold=ENTRY_THRESHOLD,
                  page=page, page_size=PAGE_SIZE, total=0, open_count=0,
                  closed_count=0, priced_closed_count=0, gross_pnl=None, gross_return=None, trades=[])
    target = db_path()
    if not target.is_file():
        return result

    clauses, params = [], []
    if status == 'open':
        clauses.append('exit_event_id IS NULL')
    elif status == 'closed':
        clauses.append('exit_event_id IS NOT NULL')
    if start:
        clauses.append('entry_time >= ?')
        params.append(start)
    if end:
        clauses.append('substr(entry_time,1,10) <= ?')
        params.append(end)
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    offset = (page - 1) * PAGE_SIZE
    moment = store.now().astimezone(config.SHANGHAI_TZ)
    today = moment.date()
    cost, profit = Decimal(0), Decimal(0)
    with _read_connection(target) as db:
        for index, row in enumerate(db.execute(
                f'SELECT * FROM trades{where} ORDER BY entry_time DESC,id DESC', params)):
            result['total'] += 1
            if row['exit_event_id'] is None:
                result['open_count'] += 1
            else:
                result['closed_count'] += 1
                quantity = _quantity(row)
                bought, sold = _cash(row['entry_price'], quantity), _cash(row['exit_price'], quantity)
                if bought is not None and sold is not None:
                    result['priced_closed_count'] += 1
                    cost += bought
                    profit += sold - bought
            if offset <= index < offset + PAGE_SIZE:
                trade = _trade_row(row)
                entry = date.fromisoformat(row['entry_time'][:10])
                last = date.fromisoformat(row['exit_time'][:10]) if row['exit_time'] else today
                trade['holding_days'] = max(0, (last - entry).days)
                trade['entry_reason'] = json.loads(row['buy_event']).get('reason')
                if row['exit_event_id'] is None:
                    trade.update(_mark_position(trade, (quotes or {}).get(trade['code'], {}), moment))
                result['trades'].append(trade)
    if cost:
        result['gross_pnl'] = float(profit)
        result['gross_return'] = float(profit / cost)
    return result


@contextmanager
def _read_connection(target):
    db = sqlite3.connect(target.absolute().as_uri() + '?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()


def overlay(plan, trade):
    """Apply ledger holdings to the live plan; daily replay stays untouched."""
    if trade is None:
        pending=plan.get('pending')
        return {**plan,'position':None,'pending':pending if pending and pending['side']=='buy' else None,
                'risk_line':None,'target':None,'paper_trade_id':None,'paper_buy_event_id':None}
    entry_date=store.parse_iso(trade['entry_time']).astimezone(config.SHANGHAI_TZ).date().isoformat()
    price,stop=trade['entry_price'],trade['initial_stop']
    position=dict(entry_date=entry_date,entry_price=price,initial_stop=stop,trailing_stop=stop,
                  peak_close=price,risk_unit=price-stop,target_price=trade['target_price'],profit_armed=False)
    pending=None;previous=None;held=0
    for bar in plan.get('ledger_daily', []):
        if bar['date']<entry_date:
            previous=bar
            continue
        held+=1
        position['peak_close']=max(position['peak_close'],bar['close'])
        if bar['close']>=price+position['risk_unit']:
            position['profit_armed']=True
        if position['profit_armed'] and bar.get('atr14'):
            position['trailing_stop']=max(position['trailing_stop'],price,position['peak_close']-2*bar['atr14'])
        reason=None
        if bar['close']<stop:reason='收盘跌破结构失效线'
        elif position['profit_armed'] and bar['close']<position['trailing_stop']:reason='已达到1R后触及利润保护线'
        elif bar['close']>=trade['target_price']:reason='收盘达到1.5R阶段目标'
        elif held>=2 and previous and bar.get('ma10') and previous.get('ma10') and bar['close']<bar['ma10'] and previous['close']<previous['ma10']:reason='连续两日收盘低于MA10'
        elif held>=5 and bar['close']<=price:reason='持有5个交易日无延续'
        elif held>=10:reason='持有10个交易日到期复核'
        if reason:
            pending=dict(side='sell',date=bar['date'],reason=reason)
            break
        previous=bar
    risk=max(stop,position['trailing_stop'] if position['profit_armed'] else stop)
    return {**plan,'position':position,'pending':pending,'risk_line':risk,
            'target':trade['target_price'],'paper_trade_id':trade['id'],'paper_buy_event_id':trade['buy_event_id']}
