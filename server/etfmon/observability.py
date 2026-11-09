"""数据源注册表、健康指标、熔断、告警与 webhook。

- 每类数据（quote/bars/intraday/fund）按优先级注册多个公开源；
- 每次取数记录 success/failure/fallback/latency，连续 5 次失败熔断 60 秒
  （之后 half_open 放一个探针），成功即恢复；
- 熔断、连续失败、交易时段数据过期会生成告警（同 key 10 分钟冷却），
  写 ``state.json`` 的 ``source_health``；配置 ``ETF_ALERT_WEBHOOK`` 时异步 POST。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from . import config, store

LOG = logging.getLogger("etfmon.observability")


# ---------------------------------------------------------------- 源注册表


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """一个数据源的身份。"""

    id: str
    name: str
    kind: str  # quote | bars | intraday | fund
    priority: int


SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec("tencent_quote", "腾讯行情", "quote", 1),
    SourceSpec("sina_quote", "新浪行情", "quote", 2),
    SourceSpec("eastmoney_quote", "东方财富行情", "quote", 3),
    SourceSpec("tencent_bars", "腾讯日线", "bars", 1),
    SourceSpec("eastmoney_bars", "东方财富日线", "bars", 2),
    SourceSpec("sina_bars", "新浪日线", "bars", 3),
    SourceSpec("tencent_intraday", "腾讯分时", "intraday", 1),
    SourceSpec("eastmoney_minute", "东方财富分钟K", "intraday", 2),
    SourceSpec("sina_minute", "新浪分钟K", "intraday", 3),
    SourceSpec("eastmoney_fund", "东方财富基金档案", "fund", 1),
)

_BY_ID: dict[str, SourceSpec] = {spec.id: spec for spec in SOURCES}
GROUPS: dict[str, list[str]] = {}
for _spec in SOURCES:
    GROUPS.setdefault(_spec.kind, []).append(_spec.id)


def specs(kind: str | None = None) -> list[SourceSpec]:
    """按 kind 返回有序源列表（含 disabled，由调用方判断 allow）。"""
    selected = [spec for spec in SOURCES if kind is None or spec.kind == kind]
    return sorted(selected, key=lambda spec: (spec.kind, spec.priority))


_URL_RE = re.compile(r"https?://\S+")


def public_detail(text: Any, limit: int = 120) -> str:
    """公开端点脱敏：去掉完整上游 URL，并限制长度。"""
    cleaned = _URL_RE.sub("上游接口", str(text or "")).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1] + "…"
    return cleaned


# ---------------------------------------------------------------- 指标 / 熔断


@dataclass
class Metrics:
    """单源运行指标（持久化到 state.json）。"""

    success_count: int = 0
    failure_count: int = 0
    fallback_count: int = 0
    consecutive_failures: int = 0
    latency_ms: int | None = None
    last_ok_at: str | None = None
    last_error_at: str | None = None
    last_error: str | None = None
    circuit: str = "closed"
    open_until: float = 0.0
    probe_in_flight: bool = False

    def persisted(self) -> dict[str, Any]:
        return {
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "fallback_count": self.fallback_count,
            "consecutive_failures": self.consecutive_failures,
            "latency_ms": self.latency_ms,
            "last_ok_at": self.last_ok_at,
            "last_error_at": self.last_error_at,
            "last_error": self.last_error,
            "circuit": self.circuit,
            "open_until": round(self.open_until, 3),
        }


class SourceRegistry:
    """线程安全的源指标 / 熔断 / 告警注册表。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._metrics: dict[str, Metrics] = {spec.id: Metrics() for spec in SOURCES}
        self._disabled: dict[str, str] = {}
        self._alerts: list[dict[str, Any]] = []
        self._cooldown: dict[str, float] = {}
        self._loaded = False
        self._last_persist = 0.0

    # -- 持久化 ----------------------------------------------------
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            payload = store.load_state().get("source_health")
            if isinstance(payload, dict):
                for source_id, saved in (payload.get("metrics") or {}).items():
                    metric = self._metrics.get(source_id)
                    if metric is None or not isinstance(saved, dict):
                        continue
                    metric.success_count = int(saved.get("success_count") or 0)
                    metric.failure_count = int(saved.get("failure_count") or 0)
                    metric.fallback_count = int(saved.get("fallback_count") or 0)
                    metric.consecutive_failures = int(saved.get("consecutive_failures") or 0)
                    metric.latency_ms = saved.get("latency_ms")
                    metric.last_ok_at = saved.get("last_ok_at")
                    metric.last_error_at = saved.get("last_error_at")
                    metric.last_error = saved.get("last_error")
                    circuit = str(saved.get("circuit") or "closed")
                    metric.circuit = circuit if circuit in {"closed", "open", "half_open"} else "closed"
                    metric.open_until = float(saved.get("open_until") or 0.0)
                disabled = payload.get("disabled")
                if isinstance(disabled, dict):
                    # 已下线/未知的源（例如 v2.1 删除的 netease_bars）直接丢弃
                    self._disabled = {
                        str(key): str(value)
                        for key, value in disabled.items()
                        if str(key) in _BY_ID
                    }
                alerts = payload.get("alerts")
                if isinstance(alerts, list):
                    self._alerts = [
                        dict(item)
                        for item in alerts
                        if isinstance(item, dict)
                        and (item.get("source_id") is None or str(item.get("source_id")) in _BY_ID)
                    ][: config.ALERT_KEEP]
            self._loaded = True

    def _persist(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_persist < 10:
            return
        self._last_persist = now
        with self._lock:
            payload = {
                "metrics": {source_id: metric.persisted() for source_id, metric in self._metrics.items()},
                "disabled": dict(self._disabled),
                "alerts": list(self._alerts),
                "updated_at": store.iso_now(),
            }
        store.update_state(lambda state: {**state, "source_health": payload})

    def reset(self) -> None:
        """清空内存指标（测试用；不覆盖已落盘数据）。"""
        with self._lock:
            self._metrics = {spec.id: Metrics() for spec in SOURCES}
            self._disabled = {}
            self._alerts = []
            self._cooldown = {}
            self._loaded = True
            self._last_persist = 0.0

    # -- 查询 ------------------------------------------------------
    def disabled_reason(self, source_id: str) -> str | None:
        self._ensure_loaded()
        with self._lock:
            return self._disabled.get(source_id)

    def is_disabled(self, source_id: str) -> bool:
        return self.disabled_reason(source_id) is not None

    def allow(self, source_id: str) -> bool:
        """当前是否允许调用该源（熔断/禁用/半开探针）。"""
        self._ensure_loaded()
        with self._lock:
            if source_id in self._disabled:
                return False
            metric = self._metrics.get(source_id)
            if metric is None:
                return False
            if metric.circuit == "closed":
                return True
            if metric.circuit == "open":
                if time.time() < metric.open_until:
                    return False
                metric.circuit = "half_open"
                metric.probe_in_flight = True
                return True
            # half_open：只放一个探针
            if metric.probe_in_flight:
                return False
            metric.probe_in_flight = True
            return True

    def metric(self, source_id: str) -> Metrics | None:
        self._ensure_loaded()
        with self._lock:
            return self._metrics.get(source_id)

    # -- 记录 ------------------------------------------------------
    def record_success(self, source_id: str, latency_ms: int | None, *, fallback: bool = False) -> None:
        self._ensure_loaded()
        with self._lock:
            metric = self._metrics.get(source_id)
            if metric is None:
                return
            metric.success_count += 1
            if fallback:
                metric.fallback_count += 1
            metric.consecutive_failures = 0
            metric.latency_ms = latency_ms
            metric.last_ok_at = store.iso_now()
            metric.circuit = "closed"
            metric.open_until = 0.0
            metric.probe_in_flight = False
        self._persist()

    def record_failure(self, source_id: str, error: str) -> None:
        self._ensure_loaded()
        opened = False
        with self._lock:
            metric = self._metrics.get(source_id)
            spec = _BY_ID.get(source_id)
            if metric is None:
                return
            metric.failure_count += 1
            metric.consecutive_failures += 1
            metric.last_error_at = store.iso_now()
            metric.last_error = str(error)[:300]
            metric.probe_in_flight = False
            if metric.consecutive_failures >= config.CIRCUIT_FAILURE_THRESHOLD and metric.circuit != "open":
                metric.circuit = "open"
                metric.open_until = time.time() + config.CIRCUIT_OPEN_S
                opened = True
            elif metric.circuit == "half_open":
                # 半开探针失败 → 重新打开
                metric.circuit = "open"
                metric.open_until = time.time() + config.CIRCUIT_OPEN_S
                opened = True
            failures = metric.consecutive_failures
        if opened:
            self.add_alert(
                "critical",
                f"{spec.name if spec else source_id} 连续 {failures} 次失败，熔断 {config.CIRCUIT_OPEN_S} 秒",
                source_id,
            )
        self._persist()

    def set_disabled(self, source_id: str, reason: str) -> None:
        """标记源不可用（探测失败），并在 /api/sources 里显示原因。"""
        self._ensure_loaded()
        spec = _BY_ID.get(source_id)
        with self._lock:
            self._disabled[source_id] = reason
        self.add_alert(
            "warn",
            f"{spec.name if spec else source_id} 已标记为不可用：{reason}",
            source_id,
            key=f"disabled:{source_id}",
            cooldown_s=config.ALERT_DISABLED_COOLDOWN_S,
        )
        self._persist(force=True)

    def enable(self, source_id: str) -> None:
        """探测器成功时恢复源（同时清掉 disabled 告警冷却，便于下次故障及时提醒）。"""
        self._ensure_loaded()
        with self._lock:
            self._disabled.pop(source_id, None)
            self._cooldown.pop(f"disabled:{source_id}", None)
        self._persist(force=True)

    # -- 告警 ------------------------------------------------------
    def add_alert(
        self,
        level: str,
        message: str,
        source_id: str | None = None,
        *,
        key: str | None = None,
        cooldown_s: float | None = None,
    ) -> dict[str, Any] | None:
        """生成告警（同 key 冷却，默认 10 分钟）；返回 None 表示被冷却抑制。"""
        self._ensure_loaded()
        now = time.time()
        dedupe_key = key or f"{source_id or 'system'}:{message}"
        window = float(cooldown_s if cooldown_s is not None else config.ALERT_COOLDOWN_S)
        with self._lock:
            until = self._cooldown.get(dedupe_key)
            if until is not None and now < until:
                return None
            self._cooldown[dedupe_key] = now + window
        at = store.iso_now()
        alert = {
            "id": hashlib.sha1(f"{dedupe_key}:{at}".encode("utf-8")).hexdigest()[:12],
            "level": level if level in {"info", "warn", "critical"} else "warn",
            "message": str(message),
            "at": at,
            "source_id": source_id,
        }
        with self._lock:
            self._alerts.insert(0, alert)
            del self._alerts[config.ALERT_KEEP :]
        self._persist(force=True)
        _dispatch_webhook(alert)
        LOG.warning("数据源告警：%s", message)
        return alert

    def alerts(self, limit: int = 20) -> list[dict[str, Any]]:
        self._ensure_loaded()
        with self._lock:
            return [dict(item) for item in self._alerts[:limit]]

    # -- 快照 ------------------------------------------------------
    def _status(self, metric: Metrics, disabled_reason: str | None) -> str:
        if disabled_reason is not None:
            return "down"
        if metric.circuit == "open":
            return "down"
        if metric.circuit == "half_open":
            return "degraded"
        if metric.success_count == 0 and metric.failure_count == 0:
            return "unknown"
        if metric.consecutive_failures > 0:
            return "degraded"
        return "ok"

    def snapshot(self) -> list[dict[str, Any]]:
        """/api/sources 用的完整源状态列表。"""
        self._ensure_loaded()
        with self._lock:
            result: list[dict[str, Any]] = []
            for spec in SOURCES:
                metric = self._metrics[spec.id]
                disabled_reason = self._disabled.get(spec.id)
                status = self._status(metric, disabled_reason)
                detail = disabled_reason or metric.last_error or ""
                result.append(
                    {
                        "id": spec.id,
                        "name": spec.name,
                        "kind": spec.kind,
                        "priority": spec.priority,
                        "status": status,
                        "ok": status == "ok",
                        "latency_ms": metric.latency_ms,
                        "success_count": metric.success_count,
                        "failure_count": metric.failure_count,
                        "fallback_count": metric.fallback_count,
                        "consecutive_failures": metric.consecutive_failures,
                        "circuit": metric.circuit,
                        "last_ok_at": metric.last_ok_at,
                        "last_error_at": metric.last_error_at,
                        "last_error": metric.last_error,
                        "checked_at": metric.last_ok_at or metric.last_error_at,
                        "detail": detail,
                    }
                )
            return result

    def groups(self) -> dict[str, list[str]]:
        return {kind: list(ids) for kind, ids in GROUPS.items()}

    def health_entries(self) -> list[dict[str, Any]]:
        """/api/health.sources 的简化投影（公开端点：脱敏上游 URL）。"""
        entries = []
        for row in self.snapshot():
            entries.append(
                {
                    "id": row["id"],
                    "ok": row["ok"],
                    "latency_ms": row["latency_ms"],
                    "checked_at": row["checked_at"],
                    "detail": public_detail(row["detail"]),
                    "fallback_count": row["fallback_count"],
                    "circuit": row["circuit"],
                }
            )
        return entries

    def any_data_seen(self) -> bool:
        self._ensure_loaded()
        with self._lock:
            return any(metric.success_count or metric.failure_count for metric in self._metrics.values())


registry = SourceRegistry()


# ---------------------------------------------------------------- webhook


def _dispatch_webhook(alert: Mapping[str, Any]) -> None:
    """异步发送告警 webhook（失败只记日志，不影响主流程）。"""
    url = config.alert_webhook()
    if not url:
        return

    def send() -> None:
        _post_webhook(url, alert)

    threading.Thread(target=send, name="etfmon-alert-webhook", daemon=True).start()


def _post_webhook(url: str, alert: Mapping[str, Any], *, timeout: float = 5.0) -> bool:
    """POST JSON 到 webhook；返回是否成功（测试可 mock 本函数）。"""
    payload = {
        "text": str(alert.get("message") or ""),
        "source": alert.get("source_id"),
        "level": alert.get("level"),
        "at": alert.get("at"),
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(256)
        return True
    except (OSError, ValueError) as exc:
        LOG.warning("告警 webhook 发送失败：%s", exc)
        return False


# ---------------------------------------------------------------- 数据过期检查


def check_data_staleness(now: Any = None) -> list[dict[str, Any]]:
    """交易时段超过 ``DATA_STALE_ALERT_S`` 没有新报价/盘中采样 → 告警。"""
    moment = now or store.now()
    if not store.is_trading_time(moment):
        return []
    state = store.load_state()
    alerts: list[dict[str, Any]] = []
    for key, source_id, label in (
        ("last_quotes_at", "tencent_quote", "报价"),
        ("last_intraday_sample", "tencent_quote", "盘中份额采样"),
    ):
        last = store.parse_iso(state.get(key))
        if last is None:
            continue
        age = (moment - last).total_seconds()
        if age > config.DATA_STALE_ALERT_S:
            alert = registry.add_alert(
                "warn",
                f"交易时段{label}已 {int(age // 60)} 分钟未更新（阈值 {config.DATA_STALE_ALERT_S // 60} 分钟）",
                source_id,
                key=f"stale:{key}",
            )
            if alert:
                alerts.append(alert)
    return alerts
