"""Homepage market turnover and sector ETF observations from existing caches.

Shanghai/Shenzhen totals are additive. Sector ETFs are overlapping proxies and
never a decomposition of market turnover or identified investor cash flows.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from . import config, store, turnover

MARKETS = (("sh000001", "沪市"), ("sz399001", "深市"))
TTL_SECONDS = 420


@lru_cache(maxsize=160)
def _decode(path: str, modified: int, size: int) -> dict:
    payload = store.read_json(Path(path), {})
    if not isinstance(payload, dict):
        return {}
    days = {}
    for day in payload.get("days") or []:
        points = day.get("points") or []
        curve = turnover.normalize_day(points)
        if curve:
            days[day.get("date")] = {
                "amounts": curve,
                "prices": {p.get("time"): turnover.number(p.get("price")) for p in points},
            }
    return {"days": days, "pre_close": turnover.number(payload.get("pre_close"))}


def _cached(code: str) -> dict:
    path = store.path_for("intraday", code + ".json")
    try:
        stat = path.stat()
    except OSError:
        return {}
    return _decode(str(path), stat.st_mtime_ns, stat.st_size)


def _stamp(day: str, clock: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{clock}:00+08:00")


def build_view(quotes: Mapping[str, dict], day: str, moment: datetime, calendar: list[str]) -> dict[str, Any]:
    """Read-only; never starts collection or fetches upstream data."""
    previous = max((d for d in calendar if d < day), default=None)
    market = {code: _cached(code) for code, _ in MARKETS}
    today = {c: (p.get("days", {}).get(day) or {}).get("amounts", {}) for c, p in market.items()}
    prior = {c: (p.get("days", {}).get(previous) or {}).get("amounts", {}) for c, p in market.items()}
    common = set.intersection(*(set(v) for v in today.values()))
    clocks = [t for t in common if _stamp(day, t) + timedelta(minutes=1) <= moment]
    clock = max(clocks, default=None)
    stamp = _stamp(day, clock) if clock else None
    session = store.session_of(moment)
    live = day == moment.date().isoformat() and session in {"morning", "afternoon"}
    stale = bool(stamp and live and (moment - stamp).total_seconds() > TTL_SECONDS)
    if day < moment.date().isoformat() and session in {"morning", "afternoon"}:
        stale = True
    state = "unavailable" if stamp is None else "stale" if stale else "live" if live else "lunch" if session == "lunch" else "closed"
    rows = []
    for code, name in MARKETS:
        amount = today[code].get(clock)
        base = prior[code].get(clock) if previous else None
        rows.append({"code": code, "name": name, "amount": amount, "previous_amount": base,
                     "delta": amount - base if amount is not None and base is not None else None})
    total = sum(r["amount"] for r in rows) if all(r["amount"] is not None for r in rows) else None
    before = sum(r["previous_amount"] for r in rows) if all(r["previous_amount"] is not None for r in rows) else None
    delta = total - before if total is not None and before is not None else None
    delta_pct = delta / before if delta is not None and before else None
    morning = sum(today[c]["11:30"] for c, _ in MARKETS) if all("11:30" in today[c] for c, _ in MARKETS) else None
    afternoon = total - morning if total is not None and morning is not None and clock and clock > "11:30" else None
    quote_values, quote_times, seen_times = [], [], []
    for code, _ in MARKETS:
        q = quotes.get(code) or {}
        qtime = store.parse_iso(q.get("quote_time"))
        value = turnover.number(q.get("amount"))
        if qtime is not None and qtime.date().isoformat() == day and (qtime - moment).total_seconds() <= 5:
            seen_times.append(qtime)
        if qtime is None or qtime.date().isoformat() != day or value is None or q.get("stale") or (qtime - moment).total_seconds() > 5:
            continue
        if live and (moment - qtime).total_seconds() > 60:
            continue
        quote_values.append(value)
        quote_times.append(qtime)
    quoted = len(quote_values) == 2 and (max(quote_times) - min(quote_times)).total_seconds() <= 30
    quote_amount = sum(quote_values) if quoted else None
    quote_time = min(seen_times) if len(seen_times) == 2 else None
    quote_afternoon = quote_amount - morning if quote_amount is not None and morning is not None and quote_time and quote_time.hour >= 13 and quote_amount >= morning else None
    candidates = [(c, name, group) for c, name, group in config.HOTSPOT_UNIVERSE
                  if group not in {"港股", "美股"} and not c.startswith("sh513") and c != "sh518880"]
    compared = []
    for code, name, group in candidates:
        data = _cached(code)
        current = data.get("days", {}).get(day) or {}
        old = data.get("days", {}).get(previous) or {}
        amount = current.get("amounts", {}).get(clock)
        base = old.get("amounts", {}).get(clock)
        price = current.get("prices", {}).get(clock)
        quote = quotes.get(code) or {}
        quote_date = str(quote.get("quote_time") or "")[:10]
        reference = turnover.number(quote.get("pre_close")) if quote_date == day else None
        if not reference and max(data.get("days", {}), default=None) == day:
            reference = data.get("pre_close")
        if amount is None or base is None or not price or not reference:
            continue
        compared.append({"code": code, "direction": name, "group": group, "amount": amount,
                         "previous_amount": base, "delta": amount - base,
                         "ratio": amount / base if base else None, "change_pct": price / reference - 1})
    rising = sorted((r for r in compared if r["delta"] > 0 and r["change_pct"] > 0), key=lambda r: r["delta"], reverse=True)
    falling = sorted((r for r in compared if r["delta"] > 0 and r["change_pct"] < 0), key=lambda r: r["delta"], reverse=True)
    return {
        "date": day, "previous_date": previous, "as_of": stamp.isoformat(timespec="seconds") if stamp else None,
        "state": state, "valid_until": (stamp + timedelta(seconds=TTL_SECONDS)).isoformat(timespec="seconds") if stamp and live else None,
        "scope": "shanghai_shenzhen", "unit": "CNY", "amount": total, "previous_amount": before,
        "quote_amount": quote_amount, "quote_time": quote_time.isoformat(timespec="seconds") if quote_time else None,
        "quote_valid_until": (quote_time + timedelta(seconds=60)).isoformat(timespec="seconds") if quote_time and live else None,
        "quote_afternoon_amount": quote_afternoon,
        "delta": delta, "delta_pct": delta_pct, "afternoon_amount": afternoon,
        "volume_state": "expanded" if delta is not None and delta > 0 else "contracted" if delta is not None and delta < 0 else "unchanged" if delta == 0 else "unavailable",
        "markets": rows, "directions": {"up": rising[:4], "down": falling[:3], "compared": len(compared), "total": len(candidates)},
        "method": {"source": "tencent_cached_intraday", "refresh_seconds": 300,
                   "market_definition": "沪深两市累计成交额，与上一交易日同分钟比较，不含北交所。",
                   "direction_definition": "A股方向代表ETF按同刻成交额增量排序，区分上涨与下跌；不构成行业全量或资金来源分解。"},
    }
