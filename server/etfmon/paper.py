"""Durable reference-price simulation. Collector writes; never broker orders.

Only actual recorded discoveries since the user's chosen starting day enter
this ledger. No historical replay fills, quantity, capital or net-profit claims.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from . import store, research, config

POLICY = 'observed-points-paper-ledger-v1'
START_DATE = '2026-11-10'
MODEL, PROTECTION = 'expanded', 'none'


def db_path():
    return store.path_for('research', 'paper', 'trades.sqlite3')


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
            exit_reason TEXT, exit_event TEXT
          );
          CREATE UNIQUE INDEX IF NOT EXISTS one_open_position
            ON trades(code,model,protection) WHERE exit_event_id IS NULL;
          CREATE UNIQUE INDEX IF NOT EXISTS one_buy_per_day
            ON trades(code,model,protection,substr(entry_time,1,10));
        ''')
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
    price = float(event['reference_price'])
    stop = research.number(event.get('stop_price'))
    if not stop or not 0 < stop < price:
        return False
    result = db.execute('''INSERT OR IGNORE INTO trades
      (code,name,model,protection,buy_event_id,entry_time,entry_price,initial_stop,target_price,buy_event)
      VALUES (?,?,?,?,?,?,?,?,?,?)''',
      (event['code'],event['name'],MODEL,PROTECTION,event['event_id'],event['observed_at'],
       price,stop,price+1.5*(price-stop),json.dumps(event,ensure_ascii=False,allow_nan=False)))
    return result.rowcount == 1


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
    db.execute('''UPDATE trades SET exit_event_id=?,exit_time=?,exit_price=?,exit_reason=?,exit_event=?
                  WHERE id=? AND exit_event_id IS NULL''',
               (event['event_id'],event['observed_at'],event['reference_price'],event['reason'],
                json.dumps(event,ensure_ascii=False,allow_nan=False),pos['id']))
    return True


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
    rows=[]
    for r in db.execute('SELECT * FROM trades ORDER BY exit_event_id IS NULL DESC,entry_time DESC,id DESC LIMIT 200'):
        rows.append({k:r[k] for k in ('id','code','name','buy_event_id','entry_time','entry_price','initial_stop',
                                    'target_price','exit_event_id','exit_time','exit_price','exit_reason')})
    count=db.execute('SELECT count(*) FROM trades WHERE exit_event_id IS NULL').fetchone()[0]
    return dict(policy=POLICY,start_date=START_DATE,model=MODEL,protection=PROTECTION,
                execution_basis='observed_quote_reference',simulated=True,open_count=count,trades=rows)


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
