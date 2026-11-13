"""Account-owned research data, separate from the shared ETF desk."""

from __future__ import annotations

import re
from datetime import date

from . import research, stock_forecasts, store, views
from .stock_forecasts import owner_path


def funds():
    return store.read_json(owner_path('funds.json'), {'snapshot_date': None, 'items': []})


def check_stock_code(code: str) -> None:
    if not re.fullmatch(r'(?:sh(?:60|68)\d{4}|sz(?:00|30)\d{4}|bj\d{6})', code):
        raise ValueError('请输入沪深或北交所 A 股代码，例如 sh600000')


def stock(app: views.App, code: str):
    check_stock_code(code)
    snapshot = store.read_json(owner_path('stock-bars', f'{code}.json'), {})
    if snapshot.get('code') == code and snapshot.get('bars'):
        raw = snapshot
        source, adjustment = snapshot.get('source'), snapshot.get('adjustment')
    else:
        raw = views.build_bars(app, code, 'day', 120)
        source = store.load_bars_payload(code).get('source')
        adjustment = None
    cutoff = research.completed_cutoff(store.now())
    bars, _ = research.clean_bars(raw['bars'], cutoff)
    as_of = bars[-1]['date'] if bars else None
    tracked = code == stock_forecasts.CODE
    comparison = stock_forecasts.report(as_of) if tracked else None
    intraday = store.read_json(owner_path('forecasts', f'{code}-intraday.json'), None) if tracked else None
    forecast = store.read_json(owner_path('forecasts', f'{code}.json'), {})
    ready = (
        as_of and forecast.get('code') == code and forecast.get('as_of') == as_of
        and forecast.get('adjustment') == adjustment == 'qfq' and forecast.get('sessions')
        and forecast['sessions'][0]['date'] > cutoff
    )
    return {
        'code': code,
        'name': raw['name'],
        'as_of': as_of,
        'source': source,
        'adjustment': adjustment,
        'bars': bars[-20:],
        'forecast': comparison['latest'] if comparison else forecast if ready else None,
        'comparison': comparison,
        'intraday': intraday,
        'warning': raw.get('warning'),
    }


def validate_sessions(values: list[str], as_of: str) -> list[str]:
    """Use operator-verified exchange sessions supplied to the offline worker."""
    days = [date.fromisoformat(value).isoformat() for value in values]
    if not 1 <= len(days) <= 5 or days != sorted(set(days)) or days[0] <= as_of:
        raise ValueError('请提供 1–5 个晚于最新日线、升序且不重复的交易日')
    if any(date.fromisoformat(day).weekday() >= 5 for day in days):
        raise ValueError('预测交易日不能落在周末')
    return days
