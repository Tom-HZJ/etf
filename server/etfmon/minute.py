"""分钟 K 缓存层：按需取数 + 落盘 + TTL。

- 缓存文件：``data/minute/<code>-<interval>.json``；
- TTL：交易时段 60 秒，其余时间 30 分钟；
- 请求路径按需取数总预算 ≤4s（东财 3s + 新浪 1s），失败负缓存 60s；
- 失败时若有旧缓存则返回旧数据 + warning，否则空数组 + warning（不 404）。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Iterable, Sequence

from . import config, sources, store

LOG = logging.getLogger("etfmon.minute")

_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}
_negative: dict[str, tuple[float, bool]] = {}

UPSTREAM_WARNING = "上游暂时不可用，稍后重试"
NO_DATA_WARNING = "暂无该标的数据（代码可能不存在）"


# ---------------------------------------------------------------- 基础


def cache_path(code: str, interval: int) -> Any:
    return store.path_for("minute", f"{code}-{int(interval)}.json")


def load_cache(code: str, interval: int) -> dict[str, Any] | None:
    payload = store.read_json(cache_path(code, interval), None)
    if not isinstance(payload, dict) or not payload.get("bars"):
        return None
    return payload


def _lock_for(key: str) -> threading.Lock:
    with _guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def _in_cooldown(key: str) -> bool | None:
    """返回 None 表示不在冷却；True=上次是「无数据」，False=上次是上游故障。"""
    with _guard:
        entry = _negative.get(key)
        if entry is None:
            return None
        until, no_data = entry
        if time.monotonic() >= until:
            _negative.pop(key, None)
            return None
        return no_data


def _mark_failed(key: str, *, no_data: bool = False) -> None:
    with _guard:
        _negative[key] = (time.monotonic() + config.MINUTE_NEGATIVE_TTL_S, no_data)
        if len(_negative) > 512:
            now = time.monotonic()
            for name in [name for name, (until, _) in _negative.items() if until <= now]:
                _negative.pop(name, None)


def is_fresh(payload: dict[str, Any], now: Any = None) -> bool:
    """缓存是否新鲜：交易时段 60s，否则 30min（且必须是同一天取的）。"""
    moment = now or store.now()
    fetched = store.parse_iso(payload.get("fetched_at"))
    if fetched is None:
        return False
    if fetched.date() != moment.date():
        return False
    ttl = (
        config.MINUTE_CACHE_TTL_INTRADAY_S
        if store.is_trading_time(moment)
        else config.MINUTE_CACHE_TTL_CLOSED_S
    )
    return (moment - fetched).total_seconds() <= ttl


def _name_for(code: str) -> str:
    return config.entity_name(code)


def _save(code: str, interval: int, bars: Sequence[dict[str, Any]], source: str, name: str) -> dict[str, Any]:
    payload = {
        "code": code,
        "interval": int(interval),
        "name": name,
        "source": source,
        "fetched_at": store.iso_now(),
        "bars": [dict(bar) for bar in bars],
    }
    store.write_json(cache_path(code, interval), payload, compact=True)
    return payload


def _response(payload: dict[str, Any] | None, code: str, interval: int, limit: int, warning: str | None) -> dict[str, Any]:
    bars = list((payload or {}).get("bars") or [])
    cutoff = store.now().strftime("%Y-%m-%d %H:%M")
    bars = [bar for bar in bars if str(bar.get("time") or "") <= cutoff]
    if limit and len(bars) > limit:
        bars = bars[-limit:]
    public_bars = [
        {
            "time": str(bar.get("time") or ""),
            "date": str(bar.get("date") or ""),
            "open": bar.get("open"),
            "high": bar.get("high"),
            "low": bar.get("low"),
            "close": bar.get("close"),
            "volume": bar.get("volume"),
        }
        for bar in bars
    ]
    as_of = str(bars[-1]["time"]) if bars else None
    return {
        "code": code,
        "interval": int(interval),
        "name": str((payload or {}).get("name") or _name_for(code)),
        "count": len(public_bars),
        "as_of": as_of,
        "source": str((payload or {}).get("source") or "none"),
        "warning": warning,
        "bars": public_bars,
    }


def get_minute(code: str, interval: int, limit: int = config.MINUTE_LIMIT_DEFAULT) -> dict[str, Any]:
    """读缓存/按需拉取分钟 K，返回给 views 的半成品（不含 generated_at）。"""
    cached = load_cache(code, interval)
    if cached is not None and is_fresh(cached):
        return _response(cached, code, interval, limit, None)
    key = f"{code}-{int(interval)}"
    cooldown = _in_cooldown(key)
    if cooldown is not None:
        warning = NO_DATA_WARNING if cooldown else UPSTREAM_WARNING
        return _response(cached, code, interval, limit, f"{warning}（{config.MINUTE_NEGATIVE_TTL_S}s 内不再重试）")
    lock = _lock_for(key)
    if not lock.acquire(timeout=config.MINUTE_FETCH_TIMEOUT_S + config.MINUTE_FALLBACK_TIMEOUT_S + 2):
        return _response(cached, code, interval, limit, UPSTREAM_WARNING)
    try:
        cached = load_cache(code, interval)
        if cached is not None and is_fresh(cached):
            return _response(cached, code, interval, limit, None)
        try:
            bars, source = sources.fetch_minute(
                code,
                interval,
                max(limit, config.MINUTE_LIMIT_DEFAULT),
                timeout=config.MINUTE_FETCH_TIMEOUT_S,
                fallback_timeout=config.MINUTE_FALLBACK_TIMEOUT_S,
            )
        except (sources.SourceError, OSError, ValueError) as exc:
            no_data = isinstance(exc, sources.NoDataError)
            LOG.warning("按需拉取 %s %sm K 线失败：%s", code, interval, exc)
            _mark_failed(key, no_data=no_data)
            warning = NO_DATA_WARNING if no_data else UPSTREAM_WARNING
            if cached is not None:
                return _response(
                    cached,
                    code,
                    interval,
                    limit,
                    f"{warning}（当前展示 {cached.get('fetched_at', '最近一次')} 的缓存）",
                )
            return _response(None, code, interval, limit, warning)
        payload = _save(code, interval, bars, source, _name_for(code))
        return _response(payload, code, interval, limit, None)
    finally:
        lock.release()


# ---------------------------------------------------------------- 采集线程刷新


def refresh_codes(
    codes: Iterable[str],
    intervals: Sequence[int] = (1, 5),
    *,
    limit: int = config.MINUTE_LIMIT_DEFAULT,
) -> dict[str, Any]:
    """批量刷新分钟 K 缓存（采集线程用；失败只记日志）。"""
    saved = 0
    errors: list[str] = []
    for code in dict.fromkeys(codes):
        for interval in intervals:
            if interval not in config.MINUTE_INTERVALS:
                continue
            try:
                bars, source = sources.fetch_minute(
                    code,
                    interval,
                    limit,
                    timeout=config.MINUTE_FETCH_TIMEOUT_S,
                    fallback_timeout=config.MINUTE_FALLBACK_TIMEOUT_S,
                )
                _save(code, interval, bars, source, _name_for(code))
                saved += 1
            except sources.NoDataError:
                continue  # 该标的没有分钟数据：不算错误
            except (sources.SourceError, OSError, ValueError) as exc:
                errors.append(f"{code}-{interval}: {exc}")
    if errors:
        LOG.info("分钟 K 刷新：%d 成功，%d 失败", saved, len(errors))
    return {"saved": saved, "errors": errors}


def refresh_default_codes() -> dict[str, Any]:
    """自选 + 指数 + 热点前 N 的 1m/5m 刷新。"""
    codes: list[str] = list(config.INDEX_CODES)
    try:
        watchlist = [str(item.get("code")) for item in store.load_watchlist().get("items") or [] if item.get("code")]
        codes.extend(watchlist)
    except (OSError, ValueError):
        pass
    hotspots = store.load_hotspots_latest()
    for row in (hotspots.get("rows") or [])[: config.MINUTE_REFRESH_HOTSPOT_TOP]:
        code = str(row.get("code") or "")
        if code:
            codes.append(code)
    return refresh_codes(dict.fromkeys(codes), intervals=(1, 5))
