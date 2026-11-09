"""数据目录、原子 JSON 读写、交易时段判断与日线合并。

所有写入都是「临时文件 + ``os.replace``」，断电或并发读写不会留下半个 JSON。
数据目录默认 ``server/data``，可用 ``ETF_DATA`` 覆盖。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, TypeVar

from . import config

LOG = logging.getLogger("etfmon.store")

T = TypeVar("T")

_write_lock = threading.RLock()
_state_lock = threading.RLock()


# ---------------------------------------------------------------- 目录与基础 IO


def data_root() -> Path:
    """返回并创建数据根目录。"""
    root = config.data_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def path_for(*parts: str) -> Path:
    """数据目录下的路径（不创建）。"""
    return data_root().joinpath(*parts)


def ensure_layout() -> None:
    """建好约定的子目录，并写入/校验 schema 版本。"""
    root = data_root()
    for parts in (
        ("bars",),
        ("flows",),
        ("national",),
        ("hotspots", "history"),
    ):
        root.joinpath(*parts).mkdir(parents=True, exist_ok=True)
    _ensure_schema()


def _ensure_schema() -> None:
    path = path_for("schema_version.json")
    payload = read_json(path, None)
    if isinstance(payload, dict) and payload.get("schema_version") == config.SCHEMA_VERSION:
        return
    if isinstance(payload, dict):
        LOG.warning(
            "数据目录 schema 版本 %s 与程序 %s 不一致，按兼容读取继续（v1 无迁移脚本）",
            payload.get("schema_version"),
            config.SCHEMA_VERSION,
        )
    write_json(
        path,
        {
            "schema_version": config.SCHEMA_VERSION,
            "app_version": config.APP_VERSION,
            "created_at": iso_now(),
        },
    )


def read_json(path: Path, fallback: T) -> T:
    """读 JSON；文件缺失/损坏时返回 fallback，并记录告警。"""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return fallback
    except (OSError, ValueError) as exc:
        LOG.warning("读取 %s 失败：%s（使用兜底值）", path, exc)
        return fallback


def write_json(path: Path, value: Any, *, compact: bool = False) -> None:
    """原子写 JSON：先写同目录临时文件并 fsync，再 ``os.replace``。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        indent=None if compact else 2,
        separators=(",", ":") if compact else None,
        allow_nan=False,
    )
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with _write_lock:
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:  # 清理失败不影响已完成的替换
                    LOG.debug("清理临时文件 %s 失败", temporary)


def update_json(path: Path, fallback: Any, mutator: Callable[[Any], Any]) -> Any:
    """读-改-写，整个过程持锁，避免采集线程与 CLI 互相覆盖。"""
    with _write_lock:
        current = read_json(path, fallback)
        updated = mutator(current)
        write_json(path, updated)
        return updated


# ---------------------------------------------------------------- 时间


def now() -> datetime:
    """当前 Asia/Shanghai 时间。"""
    return config.now()


def iso_now(timespec: str = "seconds") -> str:
    """当前时间 ISO 字符串（带 +08:00）。"""
    return now().isoformat(timespec=timespec)


def iso(value: datetime | None, timespec: str = "seconds") -> str | None:
    """datetime → ISO 字符串；None 透传。"""
    if value is None:
        return None
    return value.isoformat(timespec=timespec)


def parse_iso(text: str | None) -> datetime | None:
    """ISO 字符串 → 带时区 datetime；无法解析时返回 None。"""
    if not text:
        return None
    try:
        value = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=config.SHANGHAI_TZ)
    return value


def session_of(moment: datetime | None = None) -> str:
    """交易时段枚举：pre/morning/lunch/afternoon/post/closed/weekend。

    只按「星期 + 时钟」判断；节假日需要调用方结合最近交易日数据降级为 closed
    （见 :func:`api.resolve_session`）。
    """
    moment = moment or now()
    if moment.weekday() >= 5:
        return "weekend"
    minutes = moment.hour * 60 + moment.minute
    if minutes < config.MORNING_START:
        return "pre"
    if minutes <= config.MORNING_END:
        return "morning"
    if minutes < config.AFTERNOON_START:
        return "lunch"
    if minutes <= config.AFTERNOON_END:
        return "afternoon"
    return "post"


def is_trading_time(moment: datetime | None = None) -> bool:
    """是否处于连续竞价时段（含 11:30 与 15:00 收盘分钟）。"""
    return session_of(moment) in {"morning", "afternoon"}


def is_trading_day(moment: datetime | None = None) -> bool:
    """周一~周五（不含节假日判断）。"""
    return (moment or now()).weekday() < 5


def is_quote_window(moment: datetime | None = None) -> bool:
    """报价值得频繁刷新的窗口：盘前 09:15 到收盘后 15:10。"""
    moment = moment or now()
    if not is_trading_day(moment):
        return False
    minutes = moment.hour * 60 + moment.minute
    return config.PRE_MARKET <= minutes <= config.CLOSE_RECORD


def market_data_is_final(moment: datetime | None = None) -> bool:
    """当日行情是否已收盘落定：周末或 15:10 之后。"""
    moment = moment or now()
    if moment.weekday() >= 5:
        return True
    return (moment.hour, moment.minute) >= (config.DAILY_REFRESH_HOUR, config.DAILY_REFRESH_MINUTE)


def session_minute(hour: int, minute: int) -> int | None:
    """交易分钟在「当日连续交易轴」上的位置（午休压掉），非交易分钟返回 None。"""
    clock = hour * 60 + minute
    if config.MORNING_START <= clock <= config.MORNING_END:
        return clock - config.MORNING_START
    if config.AFTERNOON_START <= clock <= config.AFTERNOON_END:
        return config.MORNING_SESSION_MINUTES + (clock - config.AFTERNOON_START)
    return None


def trade_date_of(moment: datetime | None = None) -> str:
    """按上海时区取日期字符串 YYYY-MM-DD。"""
    return (moment or now()).date().isoformat()


# ---------------------------------------------------------------- 日线


def bars_path(code: str) -> Path:
    return path_for("bars", f"{code}.json")


def load_bars(code: str) -> list[dict[str, Any]]:
    """读取本地日线（按日期升序）；不存在返回空列表。"""
    payload = read_json(bars_path(code), {})
    if not isinstance(payload, dict):
        return []
    bars = [bar for bar in payload.get("bars") or [] if isinstance(bar, dict)]
    bars.sort(key=lambda bar: str(bar.get("date") or ""))
    return bars


def load_bars_payload(code: str) -> dict[str, Any]:
    """读取完整日线文件（含名称/复权/来源元数据）。"""
    payload = read_json(bars_path(code), {})
    return payload if isinstance(payload, dict) else {}


def save_bars(
    code: str,
    bars: list[dict[str, Any]],
    *,
    name: str | None = None,
    source: str | None = None,
    adjustment: str = "qfq",
) -> None:
    """原子写入日线文件。"""
    payload: dict[str, Any] = {
        "code": code,
        "name": name or config.entity_name(code),
        "adjustment": adjustment,
        "bars": bars,
        "updated_at": iso_now(),
    }
    previous = read_json(bars_path(code), {})
    if source:
        payload["source"] = source
    elif isinstance(previous, dict) and previous.get("source"):
        payload["source"] = previous["source"]
    write_json(bars_path(code), payload, compact=True)


def merge_bars(
    existing: Iterable[Mapping[str, Any]],
    fresh: Iterable[Mapping[str, Any]],
    *,
    tolerance: float = 0.02,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """合并日线：只追加新日期，绝不覆盖历史 K 线。

    返回 ``(bars, added_count, warnings)``。若重叠日期的收盘价相差超过
    ``tolerance``（默认 2%），视为复权口径变化，停止追加并给出告警。
    """
    known: dict[str, dict[str, Any]] = {}
    for bar in existing:
        date = str(bar.get("date") or "")
        if not date:
            continue
        known[date] = {
            "date": date,
            "open": _number(bar.get("open")),
            "high": _number(bar.get("high")),
            "low": _number(bar.get("low")),
            "close": _number(bar.get("close")),
            "volume": _number(bar.get("volume")) or 0.0,
        }
    warnings: list[str] = []
    added: list[dict[str, Any]] = []
    for bar in fresh:
        date = str(bar.get("date") or "")
        values = {key: _number(bar.get(key)) for key in ("open", "high", "low", "close")}
        if not date or any(value is None or value <= 0 for value in values.values()):
            continue
        old = known.get(date)
        if old is not None:
            base = old.get("close") or 0
            if base and abs(values["close"] - base) / base > tolerance:
                warnings.append(f"{date} 收盘价与本地差超过 {tolerance:.0%}，疑似复权变化，本次不追加")
                return sorted(known.values(), key=lambda item: item["date"]), 0, warnings
            continue
        item = {
            "date": date,
            "open": values["open"],
            "high": values["high"],
            "low": values["low"],
            "close": values["close"],
            "volume": _number(bar.get("volume")) or 0.0,
        }
        known[date] = item
        added.append(item)
    bars = sorted(known.values(), key=lambda item: item["date"])
    return bars, len(added), warnings


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if number == number and number not in (float("inf"), float("-inf")) else None
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------- 国家队日数据


def daily_path() -> Path:
    return path_for("national", "daily.json")


def load_daily() -> dict[str, Any]:
    """国家队日数据：``{days: {date: {code: {shares, price}}}, intraday: {...}}``。"""
    payload = read_json(daily_path(), {})
    if not isinstance(payload, dict):
        payload = {}
    days = payload.get("days")
    payload["days"] = days if isinstance(days, dict) else {}
    intraday = payload.get("intraday")
    payload["intraday"] = intraday if isinstance(intraday, dict) else {}
    return payload


def save_daily(payload: dict[str, Any]) -> None:
    write_json(daily_path(), payload)


# ---------------------------------------------------------------- 报告期申赎


def flows_path(code: str) -> Path:
    return path_for("flows", f"{code}.json")


def load_flows(code: str) -> dict[str, Any]:
    payload = read_json(flows_path(code), {})
    return payload if isinstance(payload, dict) else {}


def save_flows(code: str, payload: dict[str, Any]) -> None:
    write_json(flows_path(code), payload)


# ---------------------------------------------------------------- 热点


def hotspots_latest_path() -> Path:
    return path_for("hotspots", "latest.json")


def load_hotspots_latest() -> dict[str, Any]:
    payload = read_json(hotspots_latest_path(), {})
    return payload if isinstance(payload, dict) else {}


def save_hotspots_latest(payload: dict[str, Any]) -> None:
    write_json(hotspots_latest_path(), payload, compact=True)


def hotspots_history_path(date: str) -> Path:
    return path_for("hotspots", "history", f"{date}.json")


def load_hotspots_history(limit: int = config.HOTSPOT_HISTORY_KEEP) -> list[dict[str, Any]]:
    """读取最近的热点历史快照（按日期升序）。"""
    directory = path_for("hotspots", "history")
    if not directory.is_dir():
        return []
    files = sorted(directory.glob("*.json"))
    result: list[dict[str, Any]] = []
    for path in files[-limit:]:
        payload = read_json(path, None)
        if isinstance(payload, dict) and payload.get("date"):
            result.append(payload)
    return result


def save_hotspots_history(payload: dict[str, Any]) -> None:
    date = str(payload.get("date") or "")
    if not date:
        raise ValueError("热点历史快照缺少 date")
    write_json(hotspots_history_path(date), payload, compact=True)
    _prune_hotspots_history()


def _prune_hotspots_history() -> None:
    directory = path_for("hotspots", "history")
    files = sorted(directory.glob("*.json"))
    for path in files[: max(0, len(files) - config.HOTSPOT_HISTORY_KEEP)]:
        try:
            path.unlink()
        except OSError:
            LOG.debug("清理热点历史 %s 失败", path)


# ---------------------------------------------------------------- 自选


def watchlist_path() -> Path:
    return path_for("watchlist.json")


def load_watchlist() -> dict[str, Any]:
    payload = read_json(watchlist_path(), None)
    if not isinstance(payload, dict):
        payload = {"updated_at": None, "items": list(config.watchlist_default())}
        write_json(watchlist_path(), payload)
        return payload
    items = payload.get("items")
    if not isinstance(items, list):
        payload["items"] = list(config.watchlist_default())
    return payload


def save_watchlist(payload: dict[str, Any]) -> None:
    write_json(watchlist_path(), payload)


# ---------------------------------------------------------------- 采集状态


def state_path() -> Path:
    return path_for("state.json")


def load_state() -> dict[str, Any]:
    payload = read_json(state_path(), {})
    return payload if isinstance(payload, dict) else {}


def update_state(mutator: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    """读-改-写 state.json（进程内加锁，原子落盘）。"""
    with _state_lock:
        current = load_state()
        updated = mutator(current)
        write_json(state_path(), updated, compact=True)
        return updated


def append_task(
    name: str,
    *,
    ok: bool,
    detail: str = "",
    started_at: str | None = None,
    duration_ms: int | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """把一次采集任务追加到 state.json 的 tasks 列表（只保留最近 N 条）。"""
    task: dict[str, Any] = {
        "name": name,
        "ok": ok,
        "detail": detail,
        "started_at": started_at or iso_now(),
        "finished_at": iso_now(),
        "duration_ms": duration_ms,
    }
    if extra:
        task.update(extra)

    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        tasks = state.get("tasks")
        if not isinstance(tasks, list):
            tasks = []
        tasks.append(task)
        state["tasks"] = tasks[-config.STATE_TASK_HISTORY :]
        return state

    return update_state(mutate)


def record_source(
    source_id: str,
    *,
    ok: bool,
    latency_ms: int | None = None,
    detail: str = "",
) -> dict[str, Any]:
    """记录单个数据源健康度（保留最近一次结果）。"""
    entry = {
        "id": source_id,
        "ok": ok,
        "latency_ms": latency_ms,
        "checked_at": iso_now(),
        "detail": detail,
    }

    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        sources = state.get("sources")
        if not isinstance(sources, list):
            sources = []
        sources = [row for row in sources if not (isinstance(row, dict) and row.get("id") == source_id)]
        sources.append(entry)
        state["sources"] = sources
        return state

    update_state(mutate)
    return entry


def set_names(names: Mapping[str, str]) -> None:
    """缓存腾讯返回的名称，供离线/搜索兜底使用。"""
    if not names:
        return

    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        cached = state.get("names")
        if not isinstance(cached, dict):
            cached = {}
        cached.update(names)
        state["names"] = cached
        return state

    update_state(mutate)


def load_names() -> dict[str, str]:
    names = load_state().get("names")
    return {str(key): str(value) for key, value in names.items()} if isinstance(names, dict) else {}


def update_quotes_snapshot(quotes: Mapping[str, Any], fetched_at: str) -> None:
    """把最近一次报价缓存落盘，重启后首屏不至于空白。"""

    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        state["quotes_snapshot"] = {"fetched_at": fetched_at, "quotes": dict(quotes)}
        return state

    update_state(mutate)


def load_quotes_snapshot() -> dict[str, Any]:
    payload = load_state().get("quotes_snapshot")
    return payload if isinstance(payload, dict) else {}
