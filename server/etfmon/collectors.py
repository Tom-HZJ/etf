"""后台采集：报价缓存、盘中国家队份额采样、日终刷新、启动回填。

所有写盘都通过 :mod:`etfmon.store` 的原子写完成；同一进程内的写操作由模块级
``_DATA_LOCK`` 串行化，避免日终任务与盘中采样互相覆盖。
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from . import analytics, config, minute, observability, sources, store, team_minute, views, turnover

LOG = logging.getLogger("etfmon.collectors")

#: 数据文件（daily/chart/hotspots/flows）的进程内写锁
_DATA_LOCK = threading.RLock()

#: 当日份额变化小于「篮子总份额 × 该比例」时，不做盘中样本一致性校验（避免把噪音当矛盾）
INTRADAY_MIN_FLOW_RATIO = 1e-5
#: 最后一条盘中样本与当日最终份额差的允许相对误差
INTRADAY_TOLERANCE = 0.01


# ---------------------------------------------------------------- 报价缓存


class QuoteCache:
    """线程安全的报价缓存：HTTP 请求只读这里，永不直接访问上游。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._quotes: dict[str, dict[str, Any]] = {}
        self._fetched_at: str | None = None
        self._warnings: list[str] = []
        self._source = ""

    def update(self, batch: sources.QuoteBatch) -> None:
        """用一次批量报价整体替换缓存。"""
        with self._lock:
            self._quotes = dict(batch.quotes)
            self._fetched_at = batch.fetched_at
            self._warnings = list(batch.warnings)
            self._source = batch.source

    def merge(self, quotes: Mapping[str, Mapping[str, Any]]) -> None:
        """把单只/少量报价并入缓存（自选新增时现场校验用，不影响整体刷新时间戳）。"""
        if not quotes:
            return
        with self._lock:
            for code, quote in quotes.items():
                self._quotes[str(code)] = dict(quote)
            if self._fetched_at is None:
                self._fetched_at = store.iso_now()

    def snapshot(self) -> tuple[dict[str, dict[str, Any]], str | None, float | None]:
        """返回 ``(quotes, fetched_at, age_seconds)`` 的快照副本。"""
        with self._lock:
            quotes = {code: dict(quote) for code, quote in self._quotes.items()}
            fetched_at = self._fetched_at
            warnings = list(self._warnings)
        age: float | None = None
        fetched = store.parse_iso(fetched_at)
        if fetched is not None:
            age = max(0.0, (store.now() - fetched).total_seconds())
        self._last_warnings = warnings
        return quotes, fetched_at, age

    def get(self, code: str) -> dict[str, Any] | None:
        with self._lock:
            quote = self._quotes.get(code)
            return dict(quote) if quote else None

    @property
    def warnings(self) -> list[str]:
        with self._lock:
            return list(self._warnings)

    @property
    def fetched_at(self) -> str | None:
        with self._lock:
            return self._fetched_at

    @property
    def source(self) -> str:
        with self._lock:
            return self._source

    def __len__(self) -> int:
        with self._lock:
            return len(self._quotes)


# ---------------------------------------------------------------- 采集范围


def quote_codes() -> list[str]:
    """报价采集范围：实体 + 指数 + 自选。"""
    extra = [str(item.get("code")) for item in store.load_watchlist().get("items") or [] if item.get("code")]
    return list(dict.fromkeys([*config.QUOTE_CODES, *extra]))


# ---------------------------------------------------------------- 单项任务


def refresh_quotes(cache: QuoteCache) -> sources.QuoteBatch:
    """刷新报价缓存，并把名称/快照/来源健康写入 state.json。"""
    codes = quote_codes()
    started = time.perf_counter()
    batch = sources.fetch_quotes(codes)
    latency_ms = int((time.perf_counter() - started) * 1000)
    cache.update(batch)
    store.append_task(
        "refresh_quotes",
        ok=True,
        detail=f"{len(batch.quotes)} 只，来源 {batch.source}",
        duration_ms=latency_ms,
    )
    store.record_source(
        "tencent_quote",
        ok=batch.source.startswith("tencent"),
        latency_ms=latency_ms,
        detail="" if batch.source.startswith("tencent") else f"降级为 {batch.source}",
    )
    if "eastmoney" in batch.source:
        store.record_source("eastmoney_quote", ok=True, latency_ms=latency_ms, detail="兜底生效")
    names = {code: str(quote.get("name") or "") for code, quote in batch.quotes.items() if quote.get("name")}
    store.set_names(names)

    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        state["last_quotes_at"] = batch.fetched_at
        state["quotes_snapshot"] = {"fetched_at": batch.fetched_at, "quotes": batch.quotes}
        return state

    store.update_state(mutate)
    if batch.warnings:
        LOG.warning("报价刷新有告警：%s", "; ".join(batch.warnings))
    return batch


def refresh_bars(codes: Iterable[str], *, count: int = 500, workers: int = 4) -> dict[str, Any]:
    """补齐/更新本地日线，返回每个代码的合并结果。"""
    unique = list(dict.fromkeys(codes))
    results: list[dict[str, Any]] = []
    errors: list[str] = []

    def fetch_one(code: str) -> dict[str, Any]:
        fresh, source = sources.fetch_bars(code, count)
        with _DATA_LOCK:
            existing = store.load_bars(code)
            merged, added, warnings = store.merge_bars(existing, fresh)
            if existing and added == 0 and not warnings:
                return {"code": code, "added": 0, "source": source, "last": merged[-1]["date"] if merged else None}
            if not merged:
                return {"code": code, "added": 0, "source": source, "error": "合并后为空"}
            store.save_bars(
                code,
                merged,
                source=source,
                name=config.entity_name(code),
                adjustment="qfq" if config.is_etf_code(code) else "none",
            )
            return {
                "code": code,
                "added": added,
                "source": source,
                "last": merged[-1]["date"] if merged else None,
                "warnings": warnings,
            }

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch_one, code): code for code in unique}
        for future in as_completed(futures):
            code = futures[future]
            try:
                results.append(future.result())
            except (sources.SourceError, OSError, ValueError) as exc:
                errors.append(f"{code}: {exc}")
                results.append({"code": code, "added": 0, "error": str(exc)})
    order = {code: index for index, code in enumerate(unique)}
    results.sort(key=lambda row: order.get(row["code"], 999))
    added = sum(int(row.get("added") or 0) for row in results)
    detail = f"{len(unique)} 只，新增 {added} 根"
    if errors:
        detail += f"，失败 {len(errors)} 只"
    store.append_task("refresh_bars", ok=not errors, detail=detail)
    store.record_source(
        "tencent_bars",
        ok=not errors,
        latency_ms=None,
        detail="" if not errors else errors[0],
    )
    if errors:
        LOG.warning("日线刷新失败 %d 只：%s", len(errors), "; ".join(errors[:3]))
    return {"results": results, "errors": errors, "added": added, "count": len(unique)}


def refresh_flows(codes: Iterable[str]) -> dict[str, Any]:
    """更新报告期申赎（东方财富基金档案），并写入 flows/<code>.json。"""
    unique = list(dict.fromkeys(codes))
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for code in unique:
        try:
            history = sources.fetch_fund_history(code)
        except sources.SourceError as exc:
            errors.append(f"{code}: {exc}")
            results.append({"code": code, "error": str(exc)})
            continue
        with _DATA_LOCK:
            previous = store.load_flows(code)
            payload = dict(previous)
            payload.update(
                {
                    "code": code,
                    "name": history.get("name") or previous.get("name") or config.entity_name(code),
                    "periods": history["periods"],
                    "holder_history": history["holder_history"],
                    "history_source": history["source"],
                    "history_frequency": history["frequency"],
                    "large_redemption_rule": history["large_redemption_rule"],
                    "updated_at": store.iso_now(),
                }
            )
            payload["snapshots"] = analytics.share_snapshots(store.load_daily().get("days") or {}, code)
            store.save_flows(code, payload)
        results.append({"code": code, "periods": len(history["periods"])})
    store.append_task(
        "refresh_flows",
        ok=not errors,
        detail=f"{len(unique) - len(errors)}/{len(unique)} 只成功",
    )
    store.record_source("eastmoney_fund", ok=len(errors) < len(unique), latency_ms=None, detail="" if not errors else errors[0])
    if errors:
        LOG.warning("报告期申赎更新失败 %d 只：%s", len(errors), "; ".join(errors[:3]))
    return {"results": results, "errors": errors, "count": len(unique)}


def prefetch_code(code: str, *, count: int = 500) -> dict[str, Any]:
    """自选新增后的后台预取：日线 + 5 日分时（失败只记日志，不抛给调用方）。"""
    result: dict[str, Any] = {"code": code, "bars": 0, "intraday": 0, "error": ""}
    try:
        summary = refresh_bars([code], count=count)
        result["bars"] = summary["added"]
        if summary["errors"]:
            result["error"] = summary["errors"][0]
    except (sources.SourceError, OSError, ValueError) as exc:
        result["error"] = str(exc)
        LOG.warning("预取 %s 日线失败：%s", code, exc)
    try:
        summary = refresh_intraday_cache([code])
        result["intraday"] = summary["saved"]
        if summary["errors"] and not result["error"]:
            result["error"] = summary["errors"][0]
    except (sources.SourceError, OSError, ValueError) as exc:
        result["error"] = result["error"] or str(exc)
        LOG.warning("预取 %s 分时失败：%s", code, exc)
    if not result["error"]:
        LOG.info("预取 %s 完成：日线 %d 根，分时 %d 天", code, result["bars"], result["intraday"])
    return result


def refresh_intraday_cache(codes: Iterable[str], *, workers: int = 4) -> dict[str, Any]:
    """刷新行情页用的 5 日分时缓存（intraday/<code>.json）。"""
    unique = list(dict.fromkeys(codes))
    errors: list[str] = []
    saved = 0

    def fetch_one(code: str) -> dict[str, Any]:
        payload = sources.fetch_intraday(code)
        payload["updated_at"] = store.iso_now()
        store.write_json(store.path_for("intraday", f"{code}.json"), payload, compact=True)
        with _DATA_LOCK:
            turnover.archive_intraday(payload, store.now())
        return {"code": code, "days": len(payload.get("days") or [])}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch_one, code): code for code in unique}
        for future in as_completed(futures):
            code = futures[future]
            try:
                future.result()
                saved += 1
            except (sources.SourceError, OSError, ValueError) as exc:
                errors.append(f"{code}: {exc}")
    store.append_task(
        "refresh_intraday_cache",
        ok=not errors,
        detail=f"{saved}/{len(unique)} 只成功",
    )
    store.record_source(
        "tencent_intraday",
        ok=saved > 0,
        latency_ms=None,
        detail="" if not errors else errors[0],
    )
    return {"saved": saved, "errors": errors}


# ---------------------------------------------------------------- 盘中国家队采样


def _last_sample_time(daily: dict[str, Any], day: str) -> str | None:
    samples = (daily.get("intraday") or {}).get(day) or []
    return str(samples[-1].get("time")) if samples else None


def sample_intraday(cache: QuoteCache, moment: datetime | None = None, *, force: bool = False) -> dict[str, Any] | None:
    """采集一次盘中国家队篮子份额，写入 ``daily.json`` 的 intraday 段。

    返回本次采样（含 amount/share_delta/time），跳过时返回 None。
    """
    moment = moment or store.now()
    day = store.trade_date_of(moment)
    with _DATA_LOCK:
        daily = store.load_daily()
        days = daily.get("days") or {}
        previous_day = max((date for date in days if date < day), default=None)
        if previous_day is None:
            LOG.info("还没有上一交易日份额，跳过盘中采样")
            return None
        quotes, fetched_at, age = cache.snapshot()
        if not quotes:
            return None
        if age is not None and age > 120 and not force:
            return None
        shares: dict[str, dict[str, Any]] = {}
        prices: dict[str, float] = {}
        for code in config.TEAM_CODES:
            quote = quotes.get(code) or {}
            share_count = analytics.to_float(quote.get("shares"))
            price = analytics.to_float(quote.get("price"))
            if share_count is not None:
                shares[code] = {"shares": share_count}
            if price is not None:
                prices[code] = price
        if not shares:
            return None
        base = days.get(previous_day) or {}
        amount = 0.0
        share_delta = 0.0
        valued = 0
        for code, row in shares.items():
            previous_shares = analytics.to_float((base.get(code) or {}).get("shares"))
            price = prices.get(code)
            if previous_shares is None or price is None:
                continue
            delta = row["shares"] - previous_shares
            amount += delta * price
            share_delta += delta
            valued += 1
        if not valued:
            return None
        clock = moment.strftime("%H:%M:%S")
        sample = {
            "time": clock,
            "amount": amount,
            "share_delta": share_delta,
            "previous": previous_day,
        }
        bucket = daily.setdefault("intraday", {}).setdefault(day, [])
        last_time = _last_sample_time(daily, day)
        if last_time and not force and last_time >= clock:
            return None
        bucket.append(sample)
        del bucket[:-240]
        for old in sorted(daily["intraday"])[: -config.INTRADAY_KEEP_DAYS]:
            daily["intraday"].pop(old, None)
        daily["updated_at"] = store.iso_now()
        store.save_daily(daily)

    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        state["last_intraday_sample"] = moment.isoformat(timespec="seconds")
        state["last_quote_sample_at"] = fetched_at
        return state

    store.update_state(mutate)
    LOG.info("盘中采样 %s：净申赎 %.2f 亿元", clock, amount / 1e8)
    return sample


def _maybe_close_sample(cache: QuoteCache, moment: datetime | None = None) -> dict[str, Any] | None:
    """11:30 / 15:00 收盘分钟补一笔：用当前报价（已是收盘价）落最后一个采样点。"""
    moment = moment or store.now()
    clock = moment.strftime("%H:%M")
    if clock not in {"11:30", "15:00"}:
        return None
    day = store.trade_date_of(moment)
    with _DATA_LOCK:
        daily = store.load_daily()
    samples = (daily.get("intraday") or {}).get(day) or []
    target = f"{clock}:00"
    if any(str(sample.get("time")) == target for sample in samples):
        return None
    stamped = moment.replace(second=0, microsecond=0)
    return sample_intraday(cache, stamped, force=True)


# ---------------------------------------------------------------- 日终刷新


def _record_daily_day(quotes: dict[str, dict[str, Any]], trade_date: str) -> dict[str, Any]:
    """把当日份额/价格写成 daily.json 的一行。"""
    with _DATA_LOCK:
        daily = store.load_daily()
        days = daily.setdefault("days", {})
        entry = dict(days.get(trade_date) or {})
        recorded = 0
        for code in config.TEAM_CODES:
            quote = quotes.get(code) or {}
            shares = analytics.to_float(quote.get("shares"))
            price = analytics.to_float(quote.get("price"))
            if shares is None or shares <= 0:
                continue
            bars = store.load_bars(code)
            close = analytics.price_at(bars, trade_date)
            entry[code] = {"shares": shares, "price": close or price}
            recorded += 1
        days[trade_date] = entry
        for old in sorted(days)[: -config.DAILY_KEEP_DAYS]:
            days.pop(old, None)
        daily["updated_at"] = store.iso_now()
        store.save_daily(daily)
    return {"date": trade_date, "recorded": recorded, "basket": len(config.TEAM_CODES)}


def _day_totals(daily: Mapping[str, Any], date: str) -> dict[str, Any] | None:
    """按 daily.json 计算某日的篮子份额差/金额（缺前一日时返回仍带 None）。"""
    days = daily.get("days") or {}
    dates = sorted(days)
    if date not in dates:
        return None
    index = dates.index(date)
    previous = days.get(dates[index - 1]) if index else None
    totals = analytics.day_totals(config.TEAM_CODES, days[date], previous)
    totals["previous"] = dates[index - 1] if index else None
    return totals


def _intraday_mismatch(daily: Mapping[str, Any], date: str) -> dict[str, Any] | None:
    """检查某日最后一条盘中样本是否与最终份额差一致。

    判定依据：最后样本 ``share_delta`` 与当日最终 ``share_delta`` 的相对误差超过
    ``INTRADAY_TOLERANCE``；当日份额变化过小（< 总份额 × 1e-5）时跳过，避免噪音误删。
    """
    samples = (daily.get("intraday") or {}).get(date) or []
    if not samples:
        return None
    totals = _day_totals(daily, date)
    if not totals or totals.get("share_delta") is None:
        return None
    sample_delta = analytics.to_float(samples[-1].get("share_delta"))
    final_delta = analytics.to_float(totals.get("share_delta"))
    if sample_delta is None or final_delta is None:
        return None
    total_shares = analytics.to_float(totals.get("total_shares")) or 0.0
    if max(abs(sample_delta), abs(final_delta)) < total_shares * INTRADAY_MIN_FLOW_RATIO:
        return None  # 当日几乎没有份额变化，不做判断
    denominator = max(abs(sample_delta), abs(final_delta), 1.0)
    ratio = abs(sample_delta - final_delta) / denominator
    if ratio <= INTRADAY_TOLERANCE:
        return None
    return {
        "date": date,
        "samples": len(samples),
        "sample_share_delta": sample_delta,
        "final_share_delta": final_delta,
        "sample_amount": analytics.to_float(samples[-1].get("amount")),
        "final_amount": analytics.to_float(totals.get("amount")),
        "ratio": ratio,
    }


def _remember_data_warnings(messages: Mapping[str, str]) -> None:
    """把数据质量告警写入 state.json（供 /api/team 的 data_quality.warnings 使用）。"""
    if not messages:
        return

    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        existing = state.get("data_warnings")
        if not isinstance(existing, dict):
            existing = {}
        existing.update(dict(messages))
        # 只保留最近 10 条，避免 state.json 无限增长
        for key in sorted(existing)[:-10]:
            existing.pop(key, None)
        state["data_warnings"] = existing
        return state

    store.update_state(mutate)


def reconcile_intraday_samples(
    daily: dict[str, Any] | None = None,
    *,
    persist: bool = True,
) -> dict[str, Any]:
    """作废与最终份额不一致的盘中样本（迁移数据/异常刷新后的自愈）。

    正常 15:10 收盘路径下，最后一条 15:00 样本与最终份额差通常一致（误差 <1%），
    不会被误删；迁移来的旧样本基于旧份额，差异明显，会被删除并记录告警。
    """
    container = daily if daily is not None else store.load_daily()
    intraday = container.get("intraday")
    if not isinstance(intraday, dict) or not intraday:
        return {"removed_days": [], "removed_samples": 0, "warnings": []}
    removed_days: list[str] = []
    messages: dict[str, str] = {}
    removed_samples = 0
    for date in sorted(intraday):
        info = _intraday_mismatch(container, date)
        if info is None:
            continue
        count = info["samples"]
        removed_samples += count
        removed_days.append(date)
        messages[date] = (
            f"{date} 已作废 {count} 条与最终份额不一致的盘中样本"
            f"（盘中 {info['sample_share_delta'] / 1e8:.2f} 亿份 vs 收盘 {info['final_share_delta'] / 1e8:.2f} 亿份）"
        )
        intraday.pop(date, None)
    if removed_days and persist:
        container["updated_at"] = store.iso_now()
        with _DATA_LOCK:
            store.save_daily(container)
        _remember_data_warnings(messages)
    if removed_days:
        LOG.warning("作废 %d 天不一致盘中样本：%s", len(removed_days), "；".join(messages.values()))
    return {"removed_days": removed_days, "removed_samples": removed_samples, "warnings": list(messages.values())}


def _write_snapshots(trade_date: str) -> None:
    """把日份额同步到 flows/<code>.json 的 snapshots 段（供份额卡片使用）。"""
    daily = store.load_daily()
    with _DATA_LOCK:
        for code in config.TEAM_CODES:
            payload = store.load_flows(code)
            if not payload:
                continue
            payload["snapshots"] = analytics.share_snapshots(daily.get("days") or {}, code)
            store.save_flows(code, payload)


def daily_refresh(cache: QuoteCache, moment: datetime | None = None) -> dict[str, Any]:
    """日终刷新：日线 → 份额 → 报告期申赎 → 合成 K 线 → 热点 → 历史。

    任何一步失败都只记 warning，不中断后续步骤（服务端永不因上游失败而 500）。
    """
    moment = moment or store.now()
    started = time.perf_counter()
    warnings: list[str] = []
    summary: dict[str, Any] = {"at": moment.isoformat(timespec="seconds")}
    try:
        refresh_quotes(cache)
    except sources.SourceError as exc:
        warnings.append(f"报价刷新失败：{exc}")
    quotes, _fetched_at, _age = cache.snapshot()
    if not quotes:
        quotes = {str(code): quote for code, quote in (store.load_quotes_snapshot().get("quotes") or {}).items()}

    bars_summary = refresh_bars(quote_codes(), workers=4)
    summary["bars"] = {"added": bars_summary["added"], "errors": len(bars_summary["errors"])}
    if bars_summary["errors"]:
        warnings.append(f"日线失败 {len(bars_summary['errors'])} 只")

    bar_dates: list[str] = []
    for code in [*config.TEAM_CODES, *config.INDEX_CODES]:
        bars = store.load_bars(code)
        if bars:
            bar_dates.append(str(bars[-1].get("date")))
    quote_dates = [str(quote.get("quote_time") or "")[:10] for quote in quotes.values() if quote.get("quote_time")]
    candidates = [date for date in [*bar_dates, *quote_dates] if date]
    trade_date = max(candidates) if candidates else store.trade_date_of(moment)

    day_summary = _record_daily_day(quotes, trade_date)
    summary["daily"] = day_summary
    if day_summary["recorded"] < day_summary["basket"]:
        warnings.append(f"当日份额只记录 {day_summary['recorded']}/{day_summary['basket']} 只")

    # 写入/覆盖当日份额后自愈：作废与最终份额不一致的盘中样本（迁移数据常见）
    reconcile = reconcile_intraday_samples()
    summary["intraday_reconcile"] = {
        "removed_days": len(reconcile["removed_days"]),
        "removed_samples": reconcile["removed_samples"],
    }
    warnings.extend(reconcile["warnings"])

    flows_summary = refresh_flows(config.TEAM_CODES)
    summary["flows"] = {"ok": len(config.TEAM_CODES) - len(flows_summary["errors"]), "errors": len(flows_summary["errors"])}
    if flows_summary["errors"]:
        warnings.append(f"报告期申赎失败 {len(flows_summary['errors'])} 只")

    _write_snapshots(trade_date)
    with _DATA_LOCK:
        chart = views.compute_chart_cache(moment)
        store.write_json(store.path_for("national", "chart.json"), chart, compact=True)
    summary["chart"] = {
        "bars": len(chart["day_bars"]),
        "covered": len(chart["covered"]),
        "missing": len(chart["missing"]),
    }
    if chart["missing"]:
        warnings.append(f"合成 K 线缺少 {len(chart['missing'])} 只日线")

    # 先回填历史快照，再计算当日排名，这样 rank_change_5d 当天就能算出来
    try:
        views.backfill_hotspot_history(quotes)
    except (ValueError, OSError) as exc:  # 回填失败不影响主流程
        warnings.append(f"热点历史回填失败：{exc}")

    with _DATA_LOCK:
        payload = views.compute_hotspot_payload(quotes, moment)
        if payload.get("as_of"):
            store.save_hotspots_latest(payload)
            store.save_hotspots_history(views.build_hotspot_history(payload["rows"], payload["as_of"]))
    summary["hotspots"] = {
        "as_of": payload.get("as_of"),
        "state": payload.get("state"),
        "rows": len(payload.get("rows") or []),
    }
    if payload.get("as_of") and payload["as_of"] < trade_date:
        warnings.append(f"热点数据停在 {payload['as_of']}，当前交易日 {trade_date}")

    try:
        from . import research
        summary["research_record"] = research.record_close(store.now())
        summary["expanded_research_record"] = research.record_close(store.now(),model='expanded')
        summary["protected_research_record"] = research.record_close(store.now(),model='expanded',protection='trailing7')
    except (ValueError, OSError) as exc:
        warnings.append(f"研究观察记录失败：{exc}")

    duration_ms = int((time.perf_counter() - started) * 1000)
    summary["warnings"] = warnings
    summary["duration_ms"] = duration_ms

    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        state["last_daily_refresh"] = moment.isoformat(timespec="seconds")
        state["trade_date"] = trade_date
        return state

    store.update_state(mutate)
    store.append_task(
        "daily_refresh",
        ok=not warnings,
        detail="; ".join(warnings[:3]) or f"{trade_date} 完成",
        duration_ms=duration_ms,
        extra={"trade_date": trade_date},
    )
    LOG.info("日终刷新完成 %s（%d ms）%s", trade_date, duration_ms, "；告警：" + "; ".join(warnings) if warnings else "")
    return summary


# ---------------------------------------------------------------- 启动回填


def startup_backfill(cache: QuoteCache) -> dict[str, Any]:
    """服务启动后的后台回填：缺日线/缺分时/缺报告期/缺热点时各补一次。"""
    result: dict[str, Any] = {"bars": 0, "flows": 0, "intraday": 0, "hotspots": False, "daily": False}
    missing_bars = [code for code in quote_codes() if not store.load_bars(code)]
    stale_bars = [code for code in config.TEAM_CODES if len(store.load_bars(code)) < 60]
    targets = list(dict.fromkeys([*missing_bars, *stale_bars]))
    if targets:
        summary = refresh_bars(targets)
        result["bars"] = summary["added"]
    flows_dir = store.path_for("flows")
    missing_flows = [code for code in config.TEAM_CODES if not (flows_dir / f"{code}.json").is_file()]
    if missing_flows:
        refresh_flows(missing_flows)
        result["flows"] = len(missing_flows)
    if not store.load_hotspots_latest() or store.load_hotspots_latest().get("as_of") is None:
        with _DATA_LOCK:
            payload = views.compute_hotspot_payload(cache.snapshot()[0])
            if payload.get("as_of"):
                store.save_hotspots_latest(payload)
                store.save_hotspots_history(views.build_hotspot_history(payload["rows"], payload["as_of"]))
                result["hotspots"] = True
    views.backfill_hotspot_history(cache.snapshot()[0])
    daily = store.load_daily()
    days = daily.get("days") or {}
    bar_dates = [str(store.load_bars(code)[-1].get("date")) for code in config.TEAM_CODES if store.load_bars(code)]
    latest_bar = max(bar_dates) if bar_dates else None
    latest_day = max(days) if days else None
    quotes, _fetched_at, _age = cache.snapshot()
    if latest_bar and (latest_day is None or latest_day < latest_bar) and quotes:
        _record_daily_day(quotes, latest_bar)
        _write_snapshots(latest_bar)
        with _DATA_LOCK:
            store.write_json(store.path_for("national", "chart.json"), views.compute_chart_cache(), compact=True)
        result["daily"] = True
    # 启动时自愈：不信任迁移/旧刷新留下的不一致盘中样本
    reconcile = reconcile_intraday_samples()
    result["intraday_removed"] = reconcile["removed_samples"]
    return result


def warmup_intraday(cache: QuoteCache) -> int:
    """启动时给自选/指数/热点前几名准备好分时缓存，避免行情页空表。"""
    codes = list(dict.fromkeys([*config.INDEX_CODES, *[code for code, _kind, _label in config.DEFAULT_WATCHLIST]]))
    summary = refresh_intraday_cache(codes)
    return summary["saved"]


# ---------------------------------------------------------------- 后台线程


class Collector:
    """采集线程集合：报价 / 盘中份额 / 分时缓存 / 日终刷新。"""

    def __init__(self, cache: QuoteCache) -> None:
        self.cache = cache
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._daily_lock = threading.Lock()
        self._last_daily_attempt: datetime | None = None
        self._last_minute_refresh: datetime | None = None
        self._last_recover_at: datetime | None = None

    # -- 生命周期 ------------------------------------------------------
    def start(self) -> None:
        """启动所有后台线程（幂等）。"""
        if self._threads:
            return
        for name, target in (
            ("quote", self._quote_loop),
            ("intraday", self._intraday_loop),
            ("intraday-cache", self._intraday_cache_loop),
            ("minute", self._minute_loop),
            ("recover", self._recover_loop),
            ("daily", self._daily_loop),
            ("backfill", self._backfill_thread),
            ("maintenance", self._maintenance_loop),
        ):
            thread = threading.Thread(target=target, name=f"etfmon-{name}", daemon=True)
            thread.start()
            self._threads.append(thread)
        LOG.info("后台采集线程已启动（%d 个）", len(self._threads))

    def stop(self) -> None:
        """通知线程退出并等待收尾。"""
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=5)
        self._threads.clear()
        LOG.info("后台采集线程已停止")

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    # -- 循环 ----------------------------------------------------------
    def _maintenance_loop(self) -> None:
        from . import maintenance
        while not self._stop.is_set():
            try:
                maintenance.run_due(_DATA_LOCK, self._stop)
            except Exception as exc:
                LOG.exception("自动数据维护失败，下轮自动重试：%s", exc)
            self._stop.wait(60)

    def _quote_loop(self) -> None:
        while not self._stop.is_set():
            try:
                refresh_quotes(self.cache)
                with _DATA_LOCK:
                    turnover.sample_quotes(self.cache.snapshot()[0], store.now())
            except (sources.SourceError, OSError, ValueError) as exc:
                LOG.warning("报价刷新失败：%s", exc)
                store.append_task("refresh_quotes", ok=False, detail=str(exc))
            try:
                from . import actions
                with _DATA_LOCK:
                    actions.process(self.cache.snapshot()[0],paper_trading=True)
            except Exception as exc:
                LOG.exception("盘中观察提醒更新失败，下轮重试：%s", exc)
            try:
                observability.check_data_staleness()
            except (OSError, ValueError) as exc:
                LOG.debug("数据过期检查失败：%s", exc)
            interval = config.QUOTE_REFRESH_S if store.is_quote_window() else config.QUOTE_REFRESH_IDLE_S
            self._stop.wait(interval)

    def _minute_loop(self) -> None:
        """盘中/盘后按 TTL 刷新自选+指数+热点前 N 的 1m/5m 分钟 K。"""
        while not self._stop.is_set():
            try:
                moment = store.now()
                last = self._last_minute_refresh
                due = last is None or (
                    (moment - last).total_seconds()
                    >= (config.MINUTE_REFRESH_S if store.is_trading_time(moment) else 1800)
                )
                if due:
                    self._last_minute_refresh = moment
                    summary = minute.refresh_default_codes()
                    LOG.info("分钟K刷新：%d 成功，%d 失败", summary["saved"], len(summary["errors"]))
            except (sources.SourceError, OSError, ValueError) as exc:
                LOG.warning("分钟K刷新失败：%s", exc)
            self._stop.wait(30)

    def _recover_loop(self) -> None:
        """定期重试被探测禁用的源，恢复后重新启用。"""
        while not self._stop.is_set():
            try:
                moment = store.now()
                last = self._last_recover_at
                if last is None or (moment - last).total_seconds() >= 300:
                    self._last_recover_at = moment
                    for spec in observability.SOURCES:
                        if observability.registry.is_disabled(spec.id):
                            result = sources.probe_source(spec.id)
                            if result.get("ok"):
                                LOG.info("数据源恢复：%s（%d ms）", spec.id, result.get("latency_ms") or 0)
            except (sources.SourceError, OSError, ValueError) as exc:
                LOG.warning("禁用源恢复探测失败：%s", exc)
            self._stop.wait(60)

    def _intraday_loop(self) -> None:
        while not self._stop.is_set():
            try:
                moment = store.now()
                if store.is_trading_time(moment):
                    last = store.load_state().get("last_intraday_sample")
                    last_moment = store.parse_iso(last)
                    due = last_moment is None or (moment - last_moment).total_seconds() >= config.INTRADAY_SAMPLE_S
                    if due:
                        self._ensure_quotes()
                        sample_intraday(self.cache, moment)
                    _maybe_close_sample(self.cache, moment)
            except (sources.SourceError, OSError, ValueError) as exc:
                LOG.warning("盘中采样失败：%s", exc)
                store.append_task("sample_intraday", ok=False, detail=str(exc))
            self._stop.wait(15)

    def _intraday_cache_loop(self) -> None:
        # Preserve existing complete cached sessions before a rolling 5-day feed
        # drops them; this local recovery does not contact the provider.
        for code in config.TEAM_CODES:
            try:
                with _DATA_LOCK:
                    turnover.archive_intraday(store.read_json(store.path_for("intraday", f"{code}.json"), {}), store.now())
            except (OSError, ValueError) as exc:
                LOG.warning("成交分时缓存恢复失败 %s：%s", code, exc)
        closing_done: set[str] = set()
        while not self._stop.is_set():
            try:
                moment = store.now()
                clock = moment.hour * 60 + moment.minute
                closing = "am" if 691 <= clock <= 700 else "pm" if 901 <= clock <= 910 else None
                close_key = f"{moment.date().isoformat()}-{closing}" if closing and moment.weekday() < 5 else None
                if store.is_trading_time(moment) or (close_key and close_key not in closing_done) or not self._intraday_cache_fresh():
                    summary = refresh_intraday_cache(quote_codes())
                    if close_key and not summary["errors"]:
                        closing_done.add(close_key)
            except (sources.SourceError, OSError, ValueError) as exc:
                LOG.warning("分时缓存刷新失败：%s", exc)
            self._stop.wait(300)

    def _daily_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._maybe_daily_refresh()
            except (sources.SourceError, OSError, ValueError) as exc:
                LOG.warning("日终刷新失败：%s", exc)
                store.append_task("daily_refresh", ok=False, detail=str(exc))
            self._stop.wait(60)

    def _backfill_thread(self) -> None:
        # 启动时探测一次全部数据源：不可用的标记 disabled（/api/sources 可见）
        try:
            results = sources.probe_all()
            available = [item["id"] for item in results if item.get("ok")]
            disabled = [item["id"] for item in results if item.get("disabled")]
            LOG.info("启动源探测：可用 %d/%d %s", len(available), len(results), available)
            if disabled:
                LOG.warning("启动源探测：禁用 %s", disabled)
        except (sources.SourceError, OSError, ValueError) as exc:
            LOG.warning("启动源探测失败：%s", exc)
        try:
            summary = startup_backfill(self.cache)
            LOG.info("启动回填完成：%s", summary)
        except (sources.SourceError, OSError, ValueError) as exc:
            LOG.warning("启动回填失败：%s", exc)
        try:
            saved = warmup_intraday(self.cache)
            LOG.info("启动分时缓存：%d 只", saved)
        except (sources.SourceError, OSError, ValueError) as exc:
            LOG.warning("启动分时缓存失败：%s", exc)
        try:
            summary = minute.refresh_default_codes()
            LOG.info("启动分钟K缓存：%d 成功，%d 失败", summary["saved"], len(summary["errors"]))
        except (sources.SourceError, OSError, ValueError) as exc:
            LOG.warning("启动分钟K缓存失败：%s", exc)
        try:
            summary = team_minute.warm_default()
            LOG.info("启动国家队分钟缓存：%s", summary)
        except (sources.SourceError, OSError, ValueError) as exc:
            LOG.warning("启动国家队分钟缓存失败：%s", exc)

    # -- 辅助 ----------------------------------------------------------
    def _ensure_quotes(self) -> None:
        _quotes, _fetched_at, age = self.cache.snapshot()
        if age is None or age > 90:
            refresh_quotes(self.cache)

    def _intraday_cache_fresh(self) -> bool:
        """所有报价范围的代码都有 12 小时内的分时缓存时才算新鲜。"""
        now = time.time()
        for code in quote_codes():
            path = store.path_for("intraday", f"{code}.json")
            try:
                if now - path.stat().st_mtime > 12 * 3600:
                    return False
            except OSError:
                return False
        return True

    def _maybe_daily_refresh(self) -> None:
        """交易日 15:10 后且当日还没刷新时执行一次日终刷新。"""
        moment = store.now()
        if not store.market_data_is_final(moment):
            return
        state = store.load_state()
        last_refresh = str(state.get("last_daily_refresh") or "")
        if last_refresh[:10] == store.trade_date_of(moment):
            return
        if not self._daily_lock.acquire(blocking=False):
            return
        try:
            if self._last_daily_attempt is not None and (moment - self._last_daily_attempt).total_seconds() < 300:
                return
            self._last_daily_attempt = moment
            daily = store.load_daily()
            recorded_dates = set((daily.get("days") or {}).keys())
            bar_dates = [str(store.load_bars(code)[-1].get("date")) for code in [*config.TEAM_CODES, *config.INDEX_CODES] if store.load_bars(code)]
            latest_bar = max(bar_dates) if bar_dates else None
            if latest_bar and latest_bar in recorded_dates and last_refresh[:10] == latest_bar:
                return
            LOG.info("触发日终刷新（%s）", moment.isoformat(timespec="seconds"))
            daily_refresh(self.cache, moment)
        finally:
            self._daily_lock.release()
