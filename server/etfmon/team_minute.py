"""国家队篮子分钟序列：26 只 ETF 用最新总份额加权分钟收盘价（亿元）。

- 权重：``national/daily.json`` 最近可用的总份额（逐只取最新非空值）；
- 每分钟：``value = Σ(shares_i × minute_close_i) / 1e8``，与日线合成口径一致；
- 分钟数据复用 ``sources.fetch_minute``（东财主/新浪次），26 只并发 ≤8、单只超时 3s；
- 时间对齐：某只缺该分钟时不计入该分钟；整只无数据/失败写入 ``method.missing``；
- 缓存 ``data/team-minute/<interval>.json``：盘中 60s、收盘后 30min；
- 失败/无数据返回空 ``series`` + ``warning``（不抛 500）。
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Mapping, Sequence

from . import config, sources, store

LOG = logging.getLogger("etfmon.team_minute")

MAX_WORKERS = 8
FETCH_TIMEOUT_S = 3
CACHE_TTL_INTRADAY_S = 60
CACHE_TTL_CLOSED_S = 30 * 60
NEGATIVE_TTL_S = 60

UPSTREAM_WARNING = "上游暂时不可用，稍后重试"
NO_DATA_WARNING = "暂无该篮子分钟数据"
NO_SHARES_WARNING = "暂无国家队份额数据，无法合成篮子分钟"

_guard = threading.Lock()
_fetch_lock = threading.Lock()
_negative_until = 0.0


# ---------------------------------------------------------------- 缓存


def cache_path(interval: int) -> Any:
    return store.path_for("team-minute", f"{int(interval)}.json")


def load_cache(interval: int) -> dict[str, Any] | None:
    payload = store.read_json(cache_path(interval), None)
    if not isinstance(payload, dict):
        return None
    return payload


def is_fresh(payload: Mapping[str, Any], now: Any = None) -> bool:
    """缓存是否新鲜：盘中 60s（且同日），其余时间 30min。"""
    moment = now or store.now()
    fetched = store.parse_iso(payload.get("fetched_at"))
    if fetched is None:
        return False
    age = (moment - fetched).total_seconds()
    if age < 0:
        return False
    if store.is_trading_time(moment):
        return fetched.date() == moment.date() and age <= CACHE_TTL_INTRADAY_S
    return age <= CACHE_TTL_CLOSED_S


def _save(interval: int, payload: Mapping[str, Any]) -> None:
    store.write_json(cache_path(interval), dict(payload), compact=True)


# ---------------------------------------------------------------- 份额权重


def latest_shares(days: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None) -> tuple[dict[str, float], str | None]:
    """返回 ``({code: shares}, 最新份额日期)``，逐只取最近一个非空份额。"""
    if days is None:
        days = (store.load_daily().get("days") or {})  # type: ignore[assignment]
    dates = sorted((str(date) for date in days), reverse=True)
    if not dates:
        return {}, None
    weights: dict[str, float] = {}
    for code in config.TEAM_CODES:
        for date in dates:
            row = (days.get(date) or {}).get(code) or {}
            shares = row.get("shares")
            try:
                value = float(shares) if shares is not None else 0.0
            except (TypeError, ValueError):
                value = 0.0
            if value > 0:
                weights[code] = value
                break
    return weights, dates[0]


# ---------------------------------------------------------------- 并发取数


def _fetch_one(code: str, interval: int, limit: int) -> tuple[str, list[dict[str, Any]], str, str]:
    """返回 ``(code, bars, source, error)``；error 为空表示成功。"""
    try:
        bars, source = sources.fetch_minute(
            code,
            interval,
            limit,
            timeout=FETCH_TIMEOUT_S,
            fallback_timeout=1,
        )
        return code, bars, source, ""
    except sources.NoDataError:
        return code, [], "", "无数据"
    except (sources.SourceError, OSError, ValueError) as exc:
        return code, [], "", f"失败：{exc}"


# ---------------------------------------------------------------- 合成


def compose(
    results: Mapping[str, Sequence[Mapping[str, Any]]],
    weights: Mapping[str, float],
    limit: int = config.MINUTE_LIMIT_DEFAULT,
    *,
    errors: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """把 26 只分钟 K 合成为篮子序列（亿元）。"""
    errors = errors or {}
    missing: list[str] = []
    buckets: dict[str, list[float]] = {}
    total_codes = len(config.TEAM_CODES)
    for code in config.TEAM_CODES:
        bars = results.get(code) or []
        shares = weights.get(code)
        if not bars or not shares:
            missing.append(code)
            continue
        for bar in bars:
            time_text = str(bar.get("time") or "")
            close = bar.get("close")
            if not time_text or close is None:
                continue
            try:
                close_value = float(close)
                volume = float(bar.get("volume") or 0.0)
            except (TypeError, ValueError):
                continue
            bucket = buckets.setdefault(time_text, [0.0, 0.0])
            bucket[0] += float(shares) * close_value
            bucket[1] += volume
    series: list[dict[str, Any]] = []
    first_value: float | None = None
    for time_text in sorted(buckets):
        total_value, volume = buckets[time_text]
        value = round(total_value / 1e8, 4)
        if first_value is None:
            first_value = value
        change = round(value / first_value - 1, 6) if first_value else None
        series.append(
            {
                "time": time_text,
                "date": time_text[:10],
                "value": value,
                "change_pct": change,
                "volume": round(volume, 2),
            }
        )
    if limit and len(series) > limit:
        series = series[-limit:]
    if not series:
        warning = UPSTREAM_WARNING if errors else NO_DATA_WARNING
    elif missing:
        warning = f"{len(missing)} 只基金缺少分钟数据：{','.join(missing[:8])}"
    else:
        warning = None
    return {
        "interval": None,  # 由调用方填充
        "count": len(series),
        "as_of": series[-1]["time"] if series else None,
        "source": "unknown",
        "warning": warning,
        "method": {
            "unit": "亿元",
            "weighting": "latest shares × minute close",
            "codes": total_codes,
            "missing": missing,
        },
        "series": series,
    }


# ---------------------------------------------------------------- 对外入口


def _fetch_with_sources(
    interval: int,
    limit: int,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str], dict[str, str]]:
    """并发取 26 只，返回 ``(bars_by_code, sources_by_code, errors_by_code)``。"""
    codes = list(config.TEAM_CODES)
    bars_by_code: dict[str, list[dict[str, Any]]] = {code: [] for code in codes}
    sources_by_code: dict[str, str] = {}
    errors_by_code: dict[str, str] = {}
    if not codes:
        return bars_by_code, sources_by_code, errors_by_code
    executor = ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(codes)), thread_name_prefix="team-minute")
    futures = {executor.submit(_fetch_one, code, interval, limit): code for code in codes}
    try:
        for future in as_completed(futures, timeout=20.0):
            code, bars, source, error = future.result()
            bars_by_code[code] = bars
            if bars:
                sources_by_code[code] = source
            elif error:
                errors_by_code[code] = error
                LOG.info("团队分钟 %s 取数失败：%s", code, error)
    except TimeoutError:
        LOG.warning("团队分钟并发取数超时，使用已完成部分")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return bars_by_code, sources_by_code, errors_by_code


def _build_payload(interval: int, limit: int, *, use_cache: bool = True) -> dict[str, Any]:
    global _negative_until
    moment = store.now()
    cached = load_cache(interval) if use_cache else None
    if cached is not None and is_fresh(cached, moment):
        payload = dict(cached)
        series = list(payload.get("series") or [])
        if limit and len(series) > limit:
            payload["series"] = series[-limit:]
            payload["count"] = len(payload["series"])
            payload["as_of"] = payload["series"][-1]["time"] if payload["series"] else None
        return payload
    with _guard:
        if time.monotonic() < _negative_until:
            return _empty_response(interval, limit, UPSTREAM_WARNING, cached)
    if not _fetch_lock.acquire(timeout=15):
        return _empty_response(interval, limit, UPSTREAM_WARNING, cached)
    try:
        cached = load_cache(interval) if use_cache else None
        if cached is not None and is_fresh(cached, store.now()):
            return cached
        weights, share_date = latest_shares()
        if not weights:
            return _empty_response(interval, limit, NO_SHARES_WARNING, cached)
        started = time.perf_counter()
        bars_by_code, sources_by_code, errors_by_code = _fetch_with_sources(interval, limit)
        payload = compose(bars_by_code, weights, limit, errors=errors_by_code)
        payload["interval"] = interval
        payload["source"] = "+".join(dict.fromkeys(sources_by_code.values())) or "unknown"
        payload["fetched_at"] = store.iso_now()
        payload["share_date"] = share_date
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if payload["series"]:
            _save(interval, payload)
            LOG.info(
                "团队分钟合成完成：interval=%sm 点数=%d 来源=%s 缺失=%d 耗时=%dms",
                interval,
                payload["count"],
                payload["source"],
                len(payload["method"]["missing"]),
                elapsed_ms,
            )
        else:
            with _guard:
                _negative_until = time.monotonic() + NEGATIVE_TTL_S
            LOG.warning("团队分钟合成失败（interval=%sm）：%s", interval, payload["warning"])
            # 失败但有旧缓存：返回旧数据 + warning，而不是空序列
            fallback = _empty_response(interval, limit, str(payload["warning"] or UPSTREAM_WARNING), cached)
            if fallback["series"]:
                return fallback
        return payload
    finally:
        _fetch_lock.release()


def _empty_response(
    interval: int,
    limit: int,
    warning: str,
    cached: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """失败时优先返回旧缓存，否则空序列。"""
    if cached is not None:
        series = list(cached.get("series") or [])
        if limit and len(series) > limit:
            series = series[-limit:]
        if series:
            return {
                "interval": interval,
                "count": len(series),
                "as_of": series[-1]["time"],
                "source": str(cached.get("source") or "unknown"),
                "warning": f"{warning}（当前展示 {cached.get('fetched_at') or '最近一次'} 的缓存）",
                "method": cached.get("method")
                or {
                    "unit": "亿元",
                    "weighting": "latest shares × minute close",
                    "codes": len(config.TEAM_CODES),
                    "missing": [],
                },
                "series": series,
            }
    return {
        "interval": interval,
        "count": 0,
        "as_of": None,
        "source": "unknown",
        "warning": warning,
        "method": {
            "unit": "亿元",
            "weighting": "latest shares × minute close",
            "codes": len(config.TEAM_CODES),
            "missing": list(config.TEAM_CODES),
        },
        "series": [],
    }


def get_team_minute(interval: int, limit: int = config.MINUTE_LIMIT_DEFAULT) -> dict[str, Any]:
    """读缓存/并发合成国家队篮子分钟序列（失败不抛异常）。"""
    try:
        payload = _build_payload(interval, limit)
    except (OSError, ValueError) as exc:
        LOG.warning("团队分钟读取/合成失败：%s", exc)
        return _empty_response(interval, limit, UPSTREAM_WARNING, load_cache(interval))
    # 只返回契约字段（内部 fetched_at/share_date 不对外）
    return {
        "interval": payload.get("interval") or interval,
        "count": int(payload.get("count") or 0),
        "as_of": payload.get("as_of"),
        "source": str(payload.get("source") or "unknown"),
        "warning": payload.get("warning"),
        "method": payload.get("method")
        or {
            "unit": "亿元",
            "weighting": "latest shares × minute close",
            "codes": len(config.TEAM_CODES),
            "missing": [],
        },
        "series": list(payload.get("series") or []),
    }


def warm_default() -> dict[str, Any]:
    """启动后台预热 1m/5m 篮子分钟缓存（失败只记日志）。"""
    summary: dict[str, Any] = {}
    for interval in (1, 5):
        started = time.perf_counter()
        try:
            payload = get_team_minute(interval, config.MINUTE_LIMIT_DEFAULT)
            summary[interval] = {
                "count": payload.get("count"),
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "warning": payload.get("warning"),
            }
        except (OSError, ValueError) as exc:
            summary[interval] = {"error": str(exc)}
    return summary
