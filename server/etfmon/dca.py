"""红利低波定投台账。

与 ``paper.py`` 的模拟交易台账分开：那套靠盘中买入观察开仓，是单次进出；
这里是无信号、按周定额买入并长期持有。两者资金模型不同，不能混在一起算。

口径：
  - 每次定投金额固定（CNY），按当日收盘价换算份额。
  - 费用默认 0：ETF 场内佣金各券商差异大，不做假假设；页面上说明。
  - 使用本地日线参考价；不将复权价等同于已实现的分红再投资。
  - 不做卖出，没有“收益率承诺”。
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from . import config, store

POLICY = 'weekly-dca-v1'
LOT_SIZE = 100
CENT = Decimal('0.01')


def ledger_path():
    return store.path_for('research', 'dca', 'ledger.json')


def config_path():
    return store.path_for('research', 'dca', 'config.json')


DEFAULT_CONFIG = {
    'policy': POLICY,
    'code': 'sh512890',
    'name': '红利低波ETF',
    'amount': 10_000.0,      # 每次投入金额
    'weekday': 0,            # 0=周一
    'start': None,           # 首次定投日期，None 表示从下一个定投日开始
    'note': '每周定额买入并持有；不设卖点，不构成收益承诺。',
}


def load_config() -> dict[str, Any]:
    payload = store.read_json(config_path(), {})
    merged = dict(DEFAULT_CONFIG)
    if isinstance(payload, dict):
        merged.update({k: v for k, v in payload.items() if v is not None or k == 'start'})
    return merged


def save_config(**changes) -> dict[str, Any]:
    current = load_config()
    for key in ('code', 'name', 'amount', 'weekday', 'start', 'note'):
        if key in changes and changes[key] is not None:
            current[key] = changes[key]
    store.write_json(config_path(), current)
    return current


def is_active() -> bool:
    """Only an explicitly saved DCA configuration switches ledger mode."""
    return config_path().is_file() and load_config().get('policy') == POLICY


def _load_entries() -> list[dict[str, Any]]:
    payload = store.read_json(ledger_path(), {})
    rows = payload.get('entries') if isinstance(payload, dict) else None
    return [r for r in rows or [] if isinstance(r, dict)]


def _save_entries(entries: list[dict[str, Any]]) -> None:
    store.write_json(ledger_path(), {'policy': POLICY, 'entries': entries,
                                     'updated_at': store.iso_now()})


def _next_weekday(from_day: date, weekday: int) -> date:
    delta = (weekday - from_day.weekday()) % 7
    return from_day + timedelta(days=delta or 7)


def _completed_bars(bars: list[dict], moment) -> list[dict]:
    local = moment.astimezone(config.SHANGHAI_TZ)
    cutoff = local.date() if local.hour * 60 + local.minute >= config.CLOSE_RECORD else local.date() - timedelta(days=1)
    result = []
    for bar in bars:
        try:
            day = date.fromisoformat(bar['date'])
            price = float(bar['close'])
        except (KeyError, TypeError, ValueError):
            continue
        if day <= cutoff and day.weekday() < 5 and math.isfinite(price) and price > 0:
            result.append(bar)
    return sorted(result, key=lambda b: b['date'])


def _price_on(bars: list[dict], day: str) -> tuple[str, float] | None:
    """First available completed session on/after the schedule, never prior close."""
    later = [b for b in bars if b['date'] >= day]
    if not later:
        return None
    first = min(later, key=lambda b: b['date'])
    return first['date'], float(first['close'])


def _quantity(amount: Decimal, price: Decimal, lot: int = LOT_SIZE) -> int:
    """按整手取份额；不足一手就返回 0，不记小数份额。"""
    if price <= 0:
        return 0
    lots = int(amount / (price * lot))
    while lots > 0 and Decimal(lots * lot) * price > amount:
        lots -= 1
    return lots * lot


def run_due(moment=None) -> int:
    """补齐到今日为止应执行但尚未执行的定投。返回新增笔数。"""
    moment = moment or store.now()
    today = moment.astimezone(config.SHANGHAI_TZ).date()
    cfg = load_config()
    code = cfg['code']
    bars = _completed_bars(store.load_bars(code), moment)
    if not bars:
        return 0
    amount = Decimal(str(cfg['amount']))
    weekday = int(cfg['weekday'])
    if not amount.is_finite() or amount <= 0 or not 0 <= weekday <= 4:
        raise ValueError('Invalid weekly DCA amount or weekday')
    entries = _load_entries()
    done = {e['date'] for e in entries}

    first = cfg.get('start')
    try:
        cursor = date.fromisoformat(first) if first else _next_weekday(today, weekday)
    except (TypeError, ValueError):
        raise ValueError('Invalid DCA start date')

    if not first:
        save_config(start=cursor.isoformat())
    cursor += timedelta(days=(weekday - cursor.weekday()) % 7)

    added = 0
    while cursor <= today:
        key = cursor.isoformat()
        if key not in done:
            hit = _price_on(bars, key)
            if hit:
                price_date, price = hit
                qty = _quantity(amount, Decimal(str(price)))
                if qty > 0:
                    entries.append({
                        'date': key,
                        'kind': 'historical_simulation',
                        'recorded_at': moment.isoformat(),
                        'price_date': price_date,
                        'price': price,
                        'amount': float(amount),
                        'quantity': qty,
                        'cost': float((Decimal(qty) * Decimal(str(price))).quantize(CENT, rounding=ROUND_HALF_UP)),
                    })
                    done.add(key)
                    added += 1
        cursor += timedelta(days=7)

    if added:
        entries.sort(key=lambda e: e['date'])
        _save_entries(entries)
    return added


def build_payload() -> dict[str, Any]:
    cfg = load_config()
    code = cfg['code']
    entries = _load_entries()
    bars = _completed_bars(store.load_bars(code), store.now())
    last_close = None
    last_date = None
    if bars:
        last = max(bars, key=lambda b: b['date'])
        last_date, last_close = last['date'], float(last['close'])

    total_qty = sum(int(e['quantity']) for e in entries)
    total_cost = sum(Decimal(str(e['cost'])) for e in entries) if entries else Decimal('0')
    market_value = None
    unrealised = None
    unrealised_pct = None
    average_cost = None
    if total_qty > 0:
        average_cost = float((total_cost / total_qty).quantize(Decimal('0.0001')))
    if last_close is not None and total_qty > 0:
        market_value = float((Decimal(total_qty) * Decimal(str(last_close))).quantize(CENT, rounding=ROUND_HALF_UP))
        unrealised = float(Decimal(str(market_value)) - total_cost)
        if total_cost > 0:
            unrealised_pct = float((Decimal(str(market_value)) - total_cost) / total_cost)

    # Each point uses only quantities effective by that date and that day's close.
    # Preserve legacy entries verbatim, including their original price_date.
    history = []
    ordered = sorted(entries, key=lambda e: max(e['date'], e['price_date']))
    index = 0
    quantity = 0
    cost = Decimal('0')
    for bar in bars:
        while index < len(ordered) and max(ordered[index]['date'], ordered[index]['price_date']) <= bar['date']:
            entry = ordered[index]
            quantity += int(entry['quantity'])
            cost += Decimal(str(entry['cost']))
            index += 1
        if quantity:
            history.append({'date': bar['date'], 'cost': float(cost),
                            'value': float((Decimal(quantity) * Decimal(str(bar['close']))).quantize(CENT)),
                            'quantity': quantity, 'price': float(bar['close'])})

    return {
        'history': history,
        'policy': POLICY,
        'config': cfg,
        'instrument': {'code': code, 'name': cfg.get('name') or code,
                       'last_date': last_date, 'last_close': last_close},
        'entries': entries,
        'summary': {
            'count': len(entries),
            'total_quantity': total_qty,
            'total_cost': float(total_cost) if entries else 0.0,
            'average_cost': average_cost,
            'market_value': market_value,
            'unrealised': unrealised,
            'unrealised_pct': unrealised_pct,
            'first_date': entries[0]['date'] if entries else None,
            'last_date': entries[-1]['date'] if entries else None,
        },
        'notes': [
            '历史模拟，非真实持仓或成交；收益为未年化的价格收益。',
            '按周定额买入；休市或日线未到时等待首个可用收盘参考价。',
            '费用按 0 计：不同券商佣金差异大，不做假设；实际成本会略高。',
            '使用本地日线参考序列，复权与分红口径未独立核验，不代表已实现分红再投资。',
            '份额按整手取整，不足一手的余额不记入台账。',
        ],
    }
