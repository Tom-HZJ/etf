"""Read-only turnover views; collectors alone archive existing public feeds.

Same-time baseline: mean of 20 complete prior sessions, with fixed membership.
No investor attribution, order-flow proxy, trading rule or upstream requests.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any, Mapping

from . import config, store

VERSION = 1
BASELINE_DAYS = 20
CLOCKS = tuple(
    f"{m // 60:02d}:{m % 60:02d}"
    for m in (*range(570, 691), *range(781, 901))
)
REFERENCE_CLOCKS = tuple(t for t in CLOCKS if int(t[-2:]) % 5 == 0 and t != "09:30")
_VALID_CLOCKS = frozenset((*CLOCKS, "13:00"))


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def normalize_day(points: list[dict[str, Any]]) -> dict[str, float] | None:
    """Recover cumulative CNY from legacy averages, never from price × volume.

    Legacy reconstruction requires the opening point and every source delta.
    Conflicting duplicates, resets and gaps invalidate that member/day.
    """
    unique: dict[str, dict[str, Any]] = {}
    for point in points:
        clock = str(point.get("time") or "")
        if clock not in _VALID_CLOCKS:
            return None
        if clock in unique and unique[clock] != point:
            return None
        unique[clock] = point
    result: dict[str, float] = {}
    volume = 0.0
    previous = 0.0
    reconstructable = True
    for clock, point in sorted(unique.items()):
        delta = number(point.get("volume"))
        if delta is None:
            return None
        volume += delta
        if clock != "13:00" and len(result) < len(CLOCKS) and clock != CLOCKS[len(result)]:
            reconstructable = False
        amount = number(point.get("cumulative_amount"))
        if "cumulative_amount" in point and amount is None:
            return None
        if amount is None:
            avg = number(point.get("avg"))
            if not reconstructable or (volume > 0 and not avg):
                return None
            amount = volume * (avg or 0) * 100
        if amount + 0.01 < previous:
            return None
        previous = amount
        if clock != "13:00":
            result[clock] = amount
    return result or None


def archive_intraday(payload: Mapping[str, Any], moment: datetime) -> None:
    """Called under collectors._DATA_LOCK after the existing 5-day fetch."""
    if not isinstance(payload, Mapping):
        return
    code = payload.get("code")
    if code not in config.TEAM_CODES:
        return
    for day in payload.get("days") or []:
        text = str(day.get("date") or "")
        try:
            parsed = date.fromisoformat(text)
        except ValueError:
            continue
        if parsed > moment.date() or parsed.weekday() >= 5:
            continue
        curve = normalize_day(day.get("points") or [])
        if not curve:
            continue
        path = store.path_for("national", "turnover", f"{text}.json")
        saved = store.read_json(path, {})
        if saved.get("version") != VERSION:
            saved = {"version": VERSION, "date": text, "series": {}}
        old = saved["series"].get(code) or {}
        if set(old) - set(curve):
            continue  # A truncated refresh cannot erase a previously complete day.
        saved["series"][code] = curve
        saved["updated_at"] = moment.isoformat(timespec="seconds")
        store.write_json(path, saved, compact=True)
    paths = sorted(store.path_for("national", "turnover").glob("????-??-??.json"))
    for path in paths[:-45]:
        path.unlink(missing_ok=True)


def _quote_values(quotes: Mapping[str, dict], day: str, moment: datetime) -> tuple[dict[str, float], list[datetime]]:
    values: dict[str, float] = {}
    times: list[datetime] = []
    for code in config.TEAM_CODES:
        quote = quotes.get(code) or {}
        stamp = store.parse_iso(quote.get("quote_time"))
        amount = number(quote.get("amount"))
        if stamp is None or stamp.date().isoformat() != day or amount is None or quote.get("stale"):
            continue
        if (stamp - moment).total_seconds() > 5:
            continue
        if store.is_trading_time(moment) and (moment - stamp).total_seconds() > 60:
            continue
        values[code] = amount
        times.append(stamp)
    if times and (max(times) - min(times)).total_seconds() > 30:
        return {}, []
    return values, times


def sample_quotes(quotes: Mapping[str, dict], moment: datetime) -> None:
    """Existing 20-second poll, actual cumulative quote observations only."""
    if not store.is_trading_time(moment):
        return
    day = moment.date().isoformat()
    values, times = _quote_values(quotes, day, moment)
    if len(values) != len(config.TEAM_CODES):
        return
    stamp = max(times)
    if not store.is_trading_time(stamp):
        return
    path = store.path_for("national", "turnover_samples", f"{day}.json")
    payload = store.read_json(path, {"version": VERSION, "points": []})
    points = (payload.get("points") or []) if payload.get("version") == VERSION else []
    text = stamp.isoformat(timespec="seconds")
    if points and text <= points[-1]["time"]:
        return
    points.append({"time": text, "earliest_time": min(times).isoformat(timespec="seconds"), "amounts": values})
    store.write_json(path, {"version": VERSION, "points": points[-1600:]}, compact=True)
    for old in sorted(path.parent.glob("????-??-??.json"))[:-3]:
        old.unlink(missing_ok=True)


def _window(points: list[dict], seconds: int) -> dict | None:
    if len(points) < 2:
        return None
    end = store.parse_iso(points[-1].get("time"))
    if end is None:
        return None
    choices = []
    for i, point in enumerate(points[:-1]):
        start = store.parse_iso(point.get("time"))
        if start and store.session_of(start) == store.session_of(end):
            elapsed = (end - start).total_seconds()
            if abs(elapsed - seconds) <= 15:
                choices.append((abs(elapsed - seconds), i, elapsed))
    if not choices:
        return None
    _, index, elapsed = min(choices)
    section = points[index:]
    for left, right in zip(section, section[1:]):
        a, b = store.parse_iso(left["time"]), store.parse_iso(right["time"])
        if a is None or b is None or (b - a).total_seconds() > 45:
            return None
        for code in config.TEAM_CODES:
            x, y = number(left["amounts"].get(code)), number(right["amounts"].get(code))
            if x is None or y is None or y < x:
                return None
    amounts = {c: section[-1]["amounts"][c] - section[0]["amounts"][c] for c in config.TEAM_CODES}
    return {"amount": sum(amounts.values()), "amounts": amounts, "start": section[0]["time"],
            "end": section[-1]["time"], "elapsed_seconds": elapsed}


def _load_day(day: str) -> dict[str, dict[str, float]]:
    payload = store.read_json(store.path_for("national", "turnover", f"{day}.json"), {})
    return payload.get("series", {}) if payload.get("version") == VERSION else {}


def _complete(series: Mapping[str, dict]) -> bool:
    for code in config.TEAM_CODES:
        curve = series.get(code) or {}
        values = [number(curve.get(t)) for t in REFERENCE_CLOCKS]
        if any(v is None for v in values) or any(b < a for a, b in zip(values, values[1:])):
            return False
        if not values[-1]:
            return False
    return True


def build_view(quotes: Mapping[str, dict], day: str, moment: datetime, settled_history: list[dict] | None = None,
               calendar: list[str] | None = None) -> dict[str, Any]:
    """GET reads archived files and quote cache only. No sampling or fetching."""
    values, times = _quote_values(quotes, day, moment)
    complete = len(values) == len(config.TEAM_CODES)
    session = store.session_of(moment)
    live = session in {"morning", "afternoon"} and day == moment.date().isoformat()
    state = ("live" if complete else "stale") if live else ("lunch" if session == "lunch" else "closed")
    current = _load_day(day)
    archive: list[tuple[str, dict]] = []
    baseline_root = store.path_for("national", "turnover_5m")
    dates = {p.stem for root in (baseline_root, store.path_for("national", "turnover")) for p in root.glob("????-??-??.json")}
    for date_text in sorted(dates, reverse=True):
        if date_text >= day:
            continue
        saved = store.read_json(baseline_root / f"{date_text}.json", {})
        series = saved.get("series", {}) if saved.get("version") == VERSION and saved.get("interval") == 5 and saved.get("unit") == "CNY" else {}
        if not _complete(series):
            series = _load_day(date_text)
        if _complete(series):
            archive.append((date_text, series))
    known_dates = sorted({d for d in (calendar or dates) if d < day})
    required_dates = set(known_dates[-BASELINE_DAYS:])
    prior = [row for row in archive if row[0] in required_dates]
    ready = len(prior) == BASELINE_DAYS
    mean = {c: {t: sum(s[c][t] for _, s in prior) / BASELINE_DAYS for t in REFERENCE_CLOCKS}
            for c in config.TEAM_CODES} if ready else {}
    curve = []
    latest_clock = None
    for clock in CLOCKS:
        stamp = store.parse_iso(f"{day}T{clock}:00+08:00")
        finished = stamp is not None and stamp + timedelta(minutes=1) <= moment
        members = [number((current.get(c) or {}).get(clock)) for c in config.TEAM_CODES]
        amount = sum(members) if finished and all(v is not None for v in members) else None
        if amount is not None:
            latest_clock = clock
        expected = sum(mean[c][clock] for c in config.TEAM_CODES) if ready and clock in REFERENCE_CLOCKS else None
        curve.append({"time": clock, "amount": amount, "expected": expected})
    minute_time = f"{day}T{latest_clock}:00+08:00" if latest_clock else None
    minute_stamp = store.parse_iso(minute_time)
    minute_stale = bool(live and (minute_stamp is None or (moment - minute_stamp).total_seconds() > 420))
    samples = store.read_json(store.path_for("national", "turnover_samples", f"{day}.json"), {})
    points = [p for p in samples.get("points", []) if p.get("time", "") <= moment.isoformat()]
    last = store.parse_iso(points[-1].get("time")) if points else None
    sampled = complete and last is not None and (not live or (moment - last).total_seconds() <= 60)
    one, five = (_window(points, 60), _window(points, 300)) if sampled else (None, None)
    comparable = next((p for p in reversed(curve) if p["amount"] is not None and p["expected"] is not None), None)
    comparison_clock = comparable["time"] if comparable else None
    rows = []
    for code, name, group in config.TEAM_BASKET:
        observed = (current.get(code) or {}).get(comparison_clock) if comparison_clock else None
        expected = mean[code][comparison_clock] if ready and comparison_clock else None
        ratio = observed / expected if observed is not None and expected and not minute_stale else None
        rows.append({"code": code, "name": name, "group": group, "amount": values.get(code),
                     "one_minute": one["amounts"][code] if one else None,
                     "five_minute": five["amounts"][code] if five else None, "ratio": ratio})
    latest = next((p for p in reversed(curve) if p["amount"] is not None), None)
    expected = comparable["expected"] if comparable else None
    ratio = comparable["amount"] / expected if comparable and expected and not minute_stale else None
    mean_close = sum(mean[c]["15:00"] for c in config.TEAM_CODES) if ready else None
    projection = comparable["amount"] / (expected / mean_close) if ratio is not None and expected and mean_close else None
    if latest_clock == "15:00" and latest:
        projection = latest["amount"]  # Once closed, only an actual full-day amount.
    elif not live:
        projection = None
    return {
        "version": VERSION, "date": day, "state": state, "coverage": len(values), "total": len(config.TEAM_CODES),
        "quote_time": min(times).isoformat(timespec="seconds") if times else None,
        "valid_until": (min(times) + timedelta(seconds=60)).isoformat(timespec="seconds") if times and live else None,
        "total_amount": sum(values.values()) if complete else None,
        "one_minute": one, "five_minute": five,
        "minute_time": minute_time, "minute_stale": minute_stale,
        "minute_valid_until": (minute_stamp + timedelta(seconds=420)).isoformat(timespec="seconds") if minute_stamp and live else None,
        "baseline_days": len(prior), "baseline_required": BASELINE_DAYS, "baseline_dates": [d for d, _ in reversed(prior)],
        "baseline_interval": 5, "comparison_time": f"{day}T{comparison_clock}:00+08:00" if comparison_clock else None,
        "ratio": ratio, "expected_amount": expected, "projected_close_amount": projection,
        "level": "volume" if ratio is not None and ratio >= 1.8 else "attention" if ratio is not None and ratio >= 1.3 else "normal" if ratio is not None else "unavailable",
        "curve": curve, "rows": rows,
        "comparison": compare_closed_days(archive, settled_history or [], calendar),
        "method": {"source": "tencent_quotes_and_intraday", "unit": "CNY", "quote_poll_seconds": 20,
                   "minute_cache_seconds": 300, "thresholds": [1.3, 1.8], "investor_identity": None,
                   "definition": "按真实5分钟端点比较当前累计成交额与前20个完整交易日同时点均额；近1/5分钟为实际报价采样端点之差。"},
    }


def compare_closed_days(archive: list[tuple[str, dict]], settled: list[dict], calendar: list[str] | None = None) -> dict:
    """Descriptive historical comparison, never reconstructed live alerts."""
    days = sorted(archive)
    official = {r.get("date"): r for r in settled
                if r.get("verified_members") == len(config.TEAM_CODES) and r.get("gap_days") == 1}
    rows = []
    available = dict(days)
    for i in range(BASELINE_DAYS, len(days)):
        day, series = days[i]
        preceding = sorted({d for d in (calendar or available) if d < day})[-BASELINE_DAYS:]
        if len(preceding) < BASELINE_DAYS or any(d not in available for d in preceding):
            continue
        history = [(d, available[d]) for d in preceding]
        peak = 0.0
        peak_clock = None
        for clock in REFERENCE_CLOCKS:
            actual = sum(series[c][clock] for c in config.TEAM_CODES)
            expected = sum(s[c][clock] for _, s in history for c in config.TEAM_CODES) / BASELINE_DAYS
            ratio = actual / expected if expected > 0 else 0
            if ratio > peak:
                peak, peak_clock = ratio, clock
        record = official.get(day) or {}
        delta = record.get("share_delta")
        delta = float(delta) if isinstance(delta, (int, float)) and math.isfinite(delta) else None
        rows.append({"date": day, "peak_ratio": peak, "peak_time": peak_clock, "share_delta": delta,
                     "shares_date": day if delta is not None else None,
                     "status": "increased" if delta is not None and delta > 0 else "unchanged" if delta == 0 else "decreased" if delta is not None else "pending"})
    volume_days = [r for r in rows if r["peak_ratio"] >= 1.8 and r["share_delta"] is not None]
    return {"basis": "historical_minute_comparison", "rows": rows[-10:],
            "volume_days_verified": len(volume_days), "volume_days_increased": sum(r["share_delta"] > 0 for r in volume_days)}
