"""响应组装：把磁盘数据 + 内存报价缓存拼成 docs/API.md 里的结构。

这一层不直接访问上游网络（报价来自 :class:`etfmon.collectors.QuoteCache`，
分时/日线/申赎来自磁盘），保证 HTTP 请求路径稳定、可预期。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from . import analytics, config, minute, observability, sources, store, team_minute, research

LOG = logging.getLogger("etfmon.views")

#: 单只标的的 Quote 缺数据时使用的占位（契约字段全为 null，前端显示 —）
_NULL_QUOTE_FIELDS = {
    "price": None,
    "pre_close": None,
    "open": None,
    "high": None,
    "low": None,
    "change": None,
    "change_pct": None,
    "volume": None,
    "amount": None,
    "turnover": None,
    "shares": None,
    "market_cap": None,
    "quote_time": None,
}


@dataclass
class App:
    """HTTP 服务共享的运行状态。"""

    cache: Any  # duck-typed: snapshot() -> (quotes, fetched_at, age_s)
    started_at: datetime
    version: str = config.APP_VERSION
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 报价缓存


def _cached_quotes(app: App) -> tuple[dict[str, dict[str, Any]], str | None, bool]:
    """返回 (quotes, fetched_at, stale)。缓存为空时回落到 state.json 的落盘快照。"""
    quotes: dict[str, dict[str, Any]] = {}
    fetched_at: str | None = None
    age: float | None = None
    snapshot = getattr(app.cache, "snapshot", None)
    if callable(snapshot):
        try:
            quotes, fetched_at, age = snapshot()
        except Exception as exc:  # 缓存对象异常不能拖垮响应
            LOG.warning("读取报价缓存失败：%s", exc)
            quotes, fetched_at, age = {}, None, None
    persisted = False
    if not quotes:
        saved = store.load_quotes_snapshot()
        raw = saved.get("quotes") if isinstance(saved, dict) else None
        if isinstance(raw, dict) and raw:
            quotes = {str(code): quote for code, quote in raw.items() if isinstance(quote, dict)}
            fetched_at = str(saved.get("fetched_at") or "") or None
            persisted = True
    now = store.now()
    stale = False
    if store.is_trading_time(now):
        if fetched_at is None:
            stale = bool(quotes)
        else:
            fetched = store.parse_iso(fetched_at)
            if fetched is not None and (now - fetched).total_seconds() > config.STALE_AFTER_S:
                stale = True
    result: dict[str, dict[str, Any]] = {}
    for code, quote in quotes.items():
        item = dict(quote)
        item["stale"] = bool(item.get("stale") or stale or persisted)
        item.setdefault("source", "tencent")
        result[code] = item
    return result, fetched_at, stale


def _empty_quote(code: str, name: str | None = None) -> dict[str, Any]:
    """没有报价时的占位 Quote。"""
    return {
        "code": code,
        "name": name or config.entity_name(code),
        "kind": config.kind_of(code),
        **_NULL_QUOTE_FIELDS,
        "source": "none",
        "stale": True,
    }


def _quote_of(app: App, quotes: Mapping[str, dict[str, Any]], code: str) -> dict[str, Any]:
    quote = quotes.get(code)
    if quote is not None:
        return quote
    return _empty_quote(code)


def _names(app: App | None = None) -> dict[str, str]:
    """腾讯返回过的名称缓存（离线/搜索兜底）。``app`` 仅为调用一致性保留。"""
    return store.load_names()


def _display_name(names: Mapping[str, str], code: str) -> str:
    return names.get(code) or config.entity_name(code)


# ---------------------------------------------------------------- 交易日期 / 会话


def resolve_trade_date(quotes: Mapping[str, dict[str, Any]], daily: Mapping[str, Any]) -> str:
    """最近一个交易日：优先取报价时间戳日期，其次国家队日数据，最后本地日线。"""
    quote_dates = [
        str(quote.get("quote_time") or "")[:10]
        for quote in quotes.values()
        if str(quote.get("quote_time") or "")[:10]
    ]
    if quote_dates:
        return max(quote_dates)
    days = sorted((daily.get("days") or {}).keys())
    if days:
        return str(days[-1])
    return store.trade_date_of()


def resolve_session(trade_date: str, moment: datetime | None = None) -> str:
    """交易时段：盘中但数据停在上一交易日时按 ``closed``（节假日启发式）。"""
    moment = moment or store.now()
    session = store.session_of(moment)
    if session in {"morning", "lunch", "afternoon"} and trade_date != store.trade_date_of(moment):
        return "closed"
    return session


def _now() -> datetime:
    return store.now()


# ---------------------------------------------------------------- 健康度


def _health_sources(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """/api/health.sources：优先用多源注册表（含 fallback_count/circuit），否则回退旧 state。"""
    if observability.registry.any_data_seen():
        return observability.registry.health_entries()
    return [dict(row) for row in (state.get("sources") or []) if isinstance(row, dict)]


def _primary_sources_ok(entries: Sequence[Mapping[str, Any]]) -> bool:
    """每个数据类别至少有一个源可用（主源被 WAF 拦截时允许兜底源）。

    健康徽章表示「数据是否可用」而不是「是否零降级」：腾讯 fqkline 偶发 501 时，
    东财/新浪兜底仍能提供日线，不应让整站长期显示 degraded；具体源状态仍在
    /api/sources 中展示。
    """
    registry_rows = {row["id"]: row for row in observability.registry.snapshot()}
    for kind in observability.GROUPS:
        specs = observability.specs(kind)
        if not specs:
            continue
        if not any((registry_rows.get(spec.id) or {}).get("status") != "down" for spec in specs):
            return False
    return True


def build_health(app: App) -> dict[str, Any]:
    """GET /api/health。"""
    state = store.load_state()
    quotes, _fetched_at, stale = _cached_quotes(app)
    daily = store.load_daily()
    trade_date = resolve_trade_date(quotes, daily)
    now = _now()
    entries = _health_sources(state)
    warnings: list[str] = []
    if not quotes:
        warnings.append("报价缓存为空，等待采集线程首次刷新")
    if stale:
        warnings.append(f"交易时段报价已超过 {config.STALE_AFTER_S} 秒未刷新")
    if not daily.get("days"):
        warnings.append("国家队日数据为空（national/daily.json）")
    if not store.load_hotspots_latest():
        warnings.append("热点数据尚未生成（hotspots/latest.json）")
    degraded = (
        bool(warnings)
        or not _primary_sources_ok(entries)
    )
    return {
        "generated_at": store.iso_now(),
        "status": "degraded" if degraded else "ok",
        "version": app.version,
        "uptime_s": max(0, int((now - app.started_at).total_seconds())),
        "session": resolve_session(trade_date, now),
        "trade_date": trade_date,
        "last_intraday_sample": state.get("last_intraday_sample"),
        "last_daily_refresh": state.get("last_daily_refresh"),
        "sources": entries,
    }


def _data_health(app: App, quotes: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    """总览页的数据健康块（含最近告警摘要）。"""
    state = store.load_state()
    entries = _health_sources(state)
    warnings: list[str] = []
    if not quotes:
        warnings.append("报价缓存为空，暂用最近一次落盘快照")
    if any(quote.get("stale") for quote in quotes.values()):
        warnings.append("部分报价已过期（stale）")
    if not store.load_daily().get("days"):
        warnings.append("国家队日数据为空")
    if not store.load_hotspots_latest():
        warnings.append("热点数据尚未生成")
    if not entries:
        warnings.append("尚未探测数据源")
    alerts = observability.registry.alerts(limit=5)
    for alert in alerts[:2]:
        message = str(alert.get("message") or "")
        if message and message not in warnings:
            warnings.append(message)
    stale_quotes = any(quote.get("stale") for quote in quotes.values())
    degraded = (
        stale_quotes
        or not store.load_daily().get("days")
        or not store.load_hotspots_latest()
        or not entries
        or not _primary_sources_ok(entries)
    )
    return {
        "status": "degraded" if degraded else "ok",
        "warnings": warnings,
        "sources": entries,
        "alerts": alerts,
    }


# ---------------------------------------------------------------- 国家队


def _basket_flow_points(bars_by_code: Mapping[str, Sequence[Mapping[str, Any]]]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """26 只的报告期申赎（亿元）+ 未结束报告期。"""
    periods_by_code: dict[str, list[dict[str, Any]]] = {}
    for code in config.TEAM_CODES:
        periods = store.load_flows(code).get("periods") or []
        if periods:
            periods_by_code[code] = list(periods)
    flows = analytics.combine_flow_periods(periods_by_code, bars_by_code)
    daily = store.load_daily()
    days = daily.get("days") or {}
    latest_day = max(days) if days else None
    latest_shares = {code: analytics.to_float(((days.get(latest_day) or {}).get(code) or {}).get("shares")) for code in config.TEAM_CODES} if latest_day else {}
    prices: dict[str, float | None] = {}
    for code in config.TEAM_CODES:
        bars = bars_by_code.get(code) or []
        if bars:
            prices[code] = analytics.to_float(bars[-1].get("close"))
        else:
            # 缺日线时退回日数据里的收盘价，避免未结束报告期金额为空
            prices[code] = analytics.to_float(((days.get(latest_day) or {}).get(code) or {}).get("price"))
    open_flow = analytics.open_period_basket(periods_by_code, latest_shares, prices, latest_day)
    return flows, open_flow


def compute_chart_cache(moment: datetime | None = None) -> dict[str, Any]:
    """计算份额加权合成 K 线缓存（日终刷新写盘：national/chart.json）。"""
    moment = moment or _now()
    daily = store.load_daily()
    days = daily.get("days") or {}
    latest_day = max(days) if days else None
    weights: dict[str, float] = {}
    if latest_day:
        for code in config.TEAM_CODES:
            shares = analytics.to_float(((days.get(latest_day) or {}).get(code) or {}).get("shares"))
            if shares is not None and shares > 0:
                weights[code] = shares
    bars_by_code: dict[str, list[dict[str, Any]]] = {}
    missing: list[str] = []
    for code in config.TEAM_CODES:
        bars = store.load_bars(code)
        if bars:
            bars_by_code[code] = bars
        else:
            missing.append(code)
    year = int(str(latest_day or store.trade_date_of(moment))[:4])
    year_start = f"{year:04d}-01-01"
    history = analytics.combine_ohlc(bars_by_code, weights, f"{year - 1:04d}-12-01")
    base = next((bar for bar in reversed(history) if str(bar.get("date") or "") < year_start), None)
    day_bars = [bar for bar in history if str(bar.get("date") or "") >= year_start]
    flows, open_flow = _basket_flow_points(bars_by_code)
    return {
        "unit": "亿元",
        "year": year,
        "weight_date": latest_day,
        "weights": weights,
        "covered": [code for code in config.TEAM_CODES if code in bars_by_code],
        "missing": missing,
        "base_close": analytics.to_float(base.get("close")) if base else None,
        "day_bars": day_bars,
        "flows": flows,
        "open_flow": open_flow,
        "updated_at": store.iso_now(),
    }


def _load_chart_cache() -> dict[str, Any]:
    payload = store.read_json(store.path_for("national", "chart.json"), {})
    if not isinstance(payload, dict) or not payload.get("day_bars") and not payload.get("weights"):
        payload = compute_chart_cache()
    return payload


def _live_chart(chart: Mapping[str, Any], quotes: Mapping[str, dict[str, Any]], trade_date: str) -> dict[str, Any]:
    """在缓存 K 线上补当日实时 K，并重算周期/趋势/涨跌幅。"""
    day_bars = [dict(bar) for bar in chart.get("day_bars") or []]
    weights = chart.get("weights") or {}
    day_bars = analytics.apply_live_bar(day_bars, quotes, weights, trade_date)
    flows = [dict(flow) for flow in chart.get("flows") or []]
    open_flow = chart.get("open_flow")
    open_flow = dict(open_flow) if isinstance(open_flow, dict) else None
    week = analytics.resample_ohlc(day_bars, "week")
    month = analytics.resample_ohlc(day_bars, "month")
    base_close = analytics.to_float(chart.get("base_close"))
    year_change = analytics.pct_change(day_bars[-1]["close"], base_close) if day_bars and base_close else None
    day_flow_points = analytics.place_flows(flows, [str(bar.get("date")) for bar in day_bars])
    if open_flow:
        day_flow_points = [*day_flow_points, dict(open_flow)]
    month_flow_points = analytics.place_flows(flows, [str(bar.get("date")) for bar in month])
    return {
        "unit": str(chart.get("unit") or "亿元"),
        "weight_date": chart.get("weight_date"),
        "covered": list(chart.get("covered") or []),
        "missing": list(chart.get("missing") or []),
        "spans": {
            "day": {
                "label": "日K",
                "change": year_change,
                "change_label": "今年",
                "bars": day_bars,
                "flows": day_flow_points,
            },
            "week": {
                "label": "周K",
                "change": analytics.span_change(week),
                "change_label": "较上周",
                "bars": week,
                "flows": [],
            },
            "month": {
                "label": "月K",
                "change": analytics.span_change(month),
                "change_label": "较上月",
                "bars": month,
                "flows": month_flow_points,
            },
        },
        "trend": {**analytics.trend_structure([bar for bar in day_bars
                  if str(bar.get("date") or "") <= research.completed_cutoff(_now())]),
                  "evaluation": "completed_daily_only", "synthetic_basket": True},
    }


def _trading_calendar() -> list[str]:
    """本地交易日历：优先用沪深300指数日线，其次沪深300ETF，最后空列表。"""
    for code in ("sh000300", "sh510300"):
        bars = store.load_bars(code)
        if len(bars) > 20:
            return [str(bar.get("date")) for bar in bars if bar.get("date")]
    return []


def _team_history(daily: Mapping[str, Any], quotes: Mapping[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return analytics.build_history(daily.get("days") or {}, config.TEAM_CODES, _trading_calendar())


def _intraday_points_for(daily: Mapping[str, Any], trade_date: str) -> list[dict[str, Any]]:
    """当日盘中采样（只取当天，不使用其它日期的样本冒充）。"""
    return analytics.intraday_series(daily.get("intraday") or {}, trade_date)


def build_team(app: App) -> dict[str, Any]:
    """GET /api/team。"""
    now = _now()
    quotes, fetched_at, stale = _cached_quotes(app)
    daily = store.load_daily()
    days = daily.get("days") or {}
    history = _team_history(daily, quotes)
    trade_date = resolve_trade_date(quotes, daily)
    latest_day = max(days) if days else None
    previous_day = sorted(days)[-2] if len(days) > 1 else None
    latest = (days.get(latest_day) or {}) if latest_day else {}
    previous = (days.get(previous_day) or {}) if previous_day else {}
    latest_close = history[-1] if history else None
    cumulative = analytics.cumulative_stats(history)
    total_shares, total_value = analytics.basket_totals(latest, config.TEAM_CODES)
    rows = analytics.basket_rows(config.TEAM_CODES, quotes, latest, previous)
    up = sum(1 for row in rows if analytics.to_float(row.get("change_pct")) is not None and (row["change_pct"] or 0) > 0)
    down = sum(1 for row in rows if analytics.to_float(row.get("change_pct")) is not None and (row["change_pct"] or 0) < 0)
    is_today = trade_date == store.trade_date_of(now)
    live = None
    if is_today and store.is_trading_time(now) and (latest_day is None or latest_day < trade_date):
        live = analytics.live_flow(config.TEAM_CODES, quotes, latest_day, latest, trade_date)
    chart_cache = _load_chart_cache()
    chart = _live_chart(chart_cache, quotes, trade_date)
    warnings: list[str] = []
    if not quotes:
        warnings.append("报价缓存为空，使用最近一次落盘数据")
    if stale:
        warnings.append("报价已过期")
    if not history:
        warnings.append("还没有相邻两个交易日的份额，无法计算净申赎")
    data_warnings = store.load_state().get("data_warnings")
    if isinstance(data_warnings, dict) and latest_day and str(latest_day) in data_warnings:
        warnings.append(str(data_warnings[str(latest_day)]))
    if cumulative.get("excluded_days"):
        warnings.append(
            f"{cumulative['excluded_days']} 条记录跨多个交易日，未计入日净申赎累计（展示层标注 gap_days）"
        )
    fallback_funds = sum(
        int(flow.get("price_fallback_funds") or 0) for flow in (chart_cache.get("flows") or [])
    )
    if fallback_funds:
        warnings.append(f"{fallback_funds} 笔报告期申赎缺少报告日收盘价，已退化为最近收盘价估算")
    missing_bars = chart.get("missing") or []
    if missing_bars:
        warnings.append(f"{len(missing_bars)} 只缺少本地日线，合成 K 线覆盖 {len(chart.get('covered') or [])} 只")
    if not chart_cache.get("flows"):
        warnings.append("尚未取得报告期申赎数据（flows/*.json）")
    quote_times = [str(quote.get("quote_time") or "") for quote in quotes.values() if quote.get("quote_time")]
    return {
        "generated_at": store.iso_now(),
        "as_of": max(quote_times) if quote_times else (f"{latest_day}T15:00:00+08:00" if latest_day else store.iso_now()),
        "trade_date": trade_date,
        "session": resolve_session(trade_date, now),
        "basket": {
            "name": "汇金核心宽基篮子",
            "count": len(config.TEAM_CODES),
            "quoted": sum(1 for row in rows if not row["missing_quote"]),
            "total_shares": total_shares,
            "total_value": total_value,
            "value_date": latest_day,
        },
        "live_flow": (
            {
                "date": live["date"],
                "previous_day": live["previous_day"],
                "amount": live["amount"],
                "share_delta": live["share_delta"],
                "partial": True,
                "time": live["time"] or None,
            }
            if live
            else None
        ),
        "latest_close": latest_close,
        "history": history,
        "cumulative": cumulative,
        "market_pnl": analytics.market_pnl(quotes, config.TEAM_CODES),
        "up": up,
        "down": down,
        "trend": chart["trend"],
        "chart": {"unit": chart["unit"], "weight_date": chart["weight_date"], "covered": len(chart["covered"]), "missing": chart["missing"], "spans": chart["spans"]},
        "rows": rows,
        "flow_periods": chart_cache.get("flows") or [],
        "intraday": _intraday_points_for(daily, trade_date),
        "quote_time": max(quote_times) if quote_times else None,
        "fetched_at": fetched_at,
        "data_quality": {
            "quotes_ok": sum(1 for row in rows if not row["missing_quote"]),
            "quotes_failed": sum(1 for row in rows if row["missing_quote"]),
            "shares_as_of": latest_day,
            "warnings": warnings,
        },
        "notes": {
            "method": (
                "净申赎 = 相邻交易日总份额差 × 当日价格（元）；份额来自腾讯行情总份额字段。"
                "跨日缺口记录保留 gap_days 展示，但不计入日净申赎累计。"
                "合成 K 线 = 各日价格 × 最新一日份额，单位亿元。"
                "报告期净申赎 = 净份额（亿份）× 该报告期最后一个交易日收盘价（逐只乘后加总），单位亿元；"
                "缺报告日收盘价时退化为最近收盘价并在 warnings 标注。"
            ),
            "caveat": "只能推断净申赎，不能识别申赎主体，不能断言国家队持仓。",
        },
    }


# ---------------------------------------------------------------- 热点


def compute_hotspot_payload(quotes: Mapping[str, dict[str, Any]], moment: datetime | None = None) -> dict[str, Any]:
    """计算热点完整 payload（日终刷新写盘：hotspots/latest.json）。"""
    moment = moment or _now()
    latest_previous = store.load_hotspots_latest()
    fallback_code = str(((latest_previous.get("flow") or {}).get("largest_csi300") or {}).get("code") or "")
    benchmark_code = analytics.select_largest_csi300(quotes, fallback_code or None)
    names = _names()
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for code in config.HOTSPOT_CODES:
        entity = config.ENTITIES[code]
        bars = store.load_bars(code)
        quote = dict(quotes.get(code) or {})
        if not quote:
            quote = {"name": names.get(code) or entity.name}
        row = analytics.analyze_hotspot_row(
            {"code": code, "name": quote.get("name") or entity.name, "direction": entity.direction, "group": entity.group},
            bars,
            quote,
        )
        rows.append(row)
    ranked = analytics.rank_rows(rows)
    as_of = max((str(row.get("data_date")) for row in ranked if row.get("data_date")), default=None)
    history = [h for h in store.load_hotspots_history() if h.get("rule_version")==config.HOTSPOT_RULE_VERSION]
    changes = analytics.rank_changes(ranked, history, as_of)
    for row in ranked:
        row["rank_change_5d"] = changes.get(str(row.get("code")))
    counts = analytics.count_states(ranked)
    state, headline = analytics.market_state(ranked, counts)
    breadth = analytics.breadth_from_rows(ranked, quotes)
    flow = _hotspot_flow(quotes, benchmark_code, names)
    usable = [row for row in ranked if row.get("state") != "unavailable"]
    unavailable = len(ranked) - len(usable)
    if unavailable:
        warnings.append(f"{unavailable} 个方向本地日线不足 {config.HOTSPOT_MIN_BARS} 根，未参与排序")
    if not flow.get("latest"):
        warnings.append("最大沪深300 ETF 尚无日份额快照")
    if not flow.get("basket") or not (flow.get("basket") or {}).get("periods"):
        warnings.append("尚未取得国家队篮子报告期申赎")
    version = f"hotspot-{(as_of or store.trade_date_of(moment)).replace('-', '')}"
    return {
        "generated_at": store.iso_now(),
        "as_of": as_of,
        "market_data_to": as_of,
        "version": version,
        "rule_version": config.HOTSPOT_RULE_VERSION,
        "decision_scope": "self_only",
        "rule": config.HEAT_RULE,
        "state": state,
        "headline": headline,
        "counts": counts,
        "summary": {
            "direction_count": len(usable),
            "strong_or_warming": counts.get("hot", 0) + counts.get("warming", 0),
            "crowded": counts.get("crowded", 0),
            "fading": counts.get("fading", 0),
            "above_ma20": breadth["above_ma20"],
            "above_ma60": breadth["above_ma60"],
            "median_ret_20d": breadth["median_ret_20d"],
        },
        "rows": ranked,
        "agents": analytics.agent_reports(ranked, counts, flow),
        "flow": flow,
        "warnings": warnings,
        "method": {
            "universe": f"{len(config.HOTSPOT_CODES)} 个行业/主题代表 ETF（逐只按自身走势排序）",
            "decision_scope": "ETF自身价格、动量、均线和量能，不设宽基条件",
            "heat": config.HEAT_RULE,
            "share_flow": (
                "相邻已保存收盘快照的份额差 × 最新收盘价（当日净申赎）；"
                "5/20 日窗口用区间均价；报告期申赎来自东方财富基金档案"
            ),
        },
        "limitations": [
            "热度是可解释排序，不是收益预测或买卖建议。",
            "代表 ETF 等权计数，不等于全市场涨跌家数或行业指数。",
            "份额变化只能推断净申赎，不能识别国家队、机构或个人。",
            "未接入新闻情绪、政策事件、折溢价与成分股基本面。",
        ],
    }


def _hotspot_flow(
    quotes: Mapping[str, dict[str, Any]],
    benchmark_code: str,
    names: Mapping[str, str],
) -> dict[str, Any]:
    """热点页的份额申赎卡（最大沪深300 ETF）。"""
    daily = store.load_daily()
    snapshots = analytics.share_snapshots(daily.get("days") or {}, benchmark_code)
    flows_payload = store.load_flows(benchmark_code)
    periods = list(flows_payload.get("periods") or [])
    latest_snapshot = snapshots[-1] if snapshots else None
    open_period = analytics.open_period_flow(
        periods,
        latest_snapshot["date"] if latest_snapshot else None,
        latest_snapshot["shares"] if latest_snapshot else None,
        latest_snapshot.get("price") if latest_snapshot else None,
    )
    bars = store.load_bars(benchmark_code)
    period_redemptions = [
        {
            "date": str(row.get("date")),
            # 报告期净申购额 = 净份额（亿份）× 报告期末收盘价（元）→ 亿元
            "net": (analytics.to_float(row.get("net")) or 0.0)
            * (analytics.price_at(bars, str(row.get("date"))) or 0.0),
            "net_pct": analytics.to_float(row.get("net_pct")),
            "report_date": str(row.get("date")),
            "label": analytics.quarter_label(str(row.get("date"))),
            "large_redemption": True,
        }
        for row in periods
        if row.get("large_redemption")
    ]
    summary = analytics.flow_summary(snapshots, open_period, periods, period_redemptions)
    entity = config.ENTITIES.get(benchmark_code)
    name = names.get(benchmark_code) or (quotes.get(benchmark_code) or {}).get("name") or (entity.name if entity else benchmark_code)
    chart = _load_chart_cache()
    basket_periods = list(chart.get("flows") or [])
    return {
        "largest_csi300": {"code": benchmark_code, "name": name},
        "state_label": summary["state_label"],
        "latest": summary["latest"],
        "daily_share_change": summary["daily_share_change"],
        "daily_flow_amount": summary["daily_flow_amount"],
        "five_flow_amount": summary["five_flow_amount"],
        "twenty_flow_amount": summary["twenty_flow_amount"],
        "snapshots": summary["snapshots"],
        "open_period": summary["open_period"],
        "large_redemptions": summary["large_redemptions"],
        "basket": {"periods": basket_periods},
    }


def build_hotspots(app: App) -> dict[str, Any]:
    """GET /api/hotspots：读取日终快照，用最新报价补价格/涨跌幅。"""
    payload = store.load_hotspots_latest()
    quotes, _fetched_at, _stale = _cached_quotes(app)
    if payload and payload.get("rule_version")!=config.HOTSPOT_RULE_VERSION:
        payload=compute_hotspot_payload(quotes)  # Local-only read; never relabel a legacy relative-strength snapshot.
    if not payload:
        return {
            "generated_at": store.iso_now(),
            "as_of": None,
            "market_data_to": None,
            "version": config.HOTSPOT_RULE_VERSION,
            "rule": config.HEAT_RULE,
            "state": "未初始化",
            "headline": "热点雷达还没有数据，等待首轮日终采集。",
            "counts": analytics.count_states([]),
            "summary": {
                "direction_count": 0,
                "strong_or_warming": 0,
                "crowded": 0,
                "fading": 0,
                "above_ma20": 0,
                "above_ma60": 0,
                "median_ret_20d": None,
            },
            "rows": [],
            "agents": [],
            "flow": {
                "largest_csi300": None,
                "state_label": None,
                "latest": None,
                "daily_share_change": None,
                "daily_flow_amount": None,
                "five_flow_amount": None,
                "twenty_flow_amount": None,
                "snapshots": [],
                "open_period": None,
                "large_redemptions": [],
                "basket": None,
            },
            "warnings": ["热点数据尚未生成"],
            "method": {"universe": "等待采集", "decision_scope": "self_only"},
            "limitations": ["尚无数据时所有结论为空。"],
        }
    result = dict(payload)
    result["generated_at"] = store.iso_now()
    rows: list[dict[str, Any]] = []
    for row in payload.get("rows") or []:
        item = dict(row)
        quote = quotes.get(str(item.get("code")))
        if quote:
            if quote.get("price") is not None:
                item["price"] = quote["price"]
            if quote.get("change_pct") is not None:
                item["change_pct"] = quote["change_pct"]
            if quote.get("name"):
                item["name"] = quote["name"]
        rows.append(item)
    # 排名变化依赖历史快照，读取时用最新历史重算，避免日终计算顺序影响结果
    changes = analytics.rank_changes(rows, [h for h in store.load_hotspots_history() if h.get("rule_version")==config.HOTSPOT_RULE_VERSION], payload.get("as_of"))
    for row in rows:
        row["rank_change_5d"] = changes.get(str(row.get("code")))
    result["rows"] = rows
    return result


def build_hotspot_history(rows: Sequence[Mapping[str, Any]], as_of: str, moment: datetime | None = None) -> dict[str, Any]:
    """生成要做历史对比的热点快照（供 rank_change_5d 使用）。"""
    return {
        "date": as_of,
        "rule_version": config.HOTSPOT_RULE_VERSION,
        "generated_at": store.iso_now(),
        "rows": analytics.stock_rows_for_history(rows),
    }


def backfill_hotspot_history(quotes: Mapping[str, dict[str, Any]] | None = None, count: int = config.HOTSPOT_HISTORY_BACKFILL) -> int:
    """用本地日线回填热点历史排名（只补缺失日期，不覆盖已有快照）。"""
    quotes = quotes or {}
    bars_by_code: dict[str, list[dict[str, Any]]] = {code: store.load_bars(code) for code in config.HOTSPOT_CODES}
    dates = sorted({str(bar["date"]) for bars in bars_by_code.values() for bar in bars if bar.get("date")})
    targets = dates[-count:]
    existing = {str(item.get("date")) for item in store.load_hotspots_history(limit=config.HOTSPOT_HISTORY_KEEP)}
    written = 0
    for date in targets:
        if date in existing:
            continue
        rows: list[dict[str, Any]] = []
        for code in config.HOTSPOT_CODES:
            entity = config.ENTITIES[code]
            bars = [bar for bar in bars_by_code.get(code, []) if str(bar.get("date")) <= date]
            row = analytics.analyze_hotspot_row(
                {"code": code, "name": entity.name, "direction": entity.direction, "group": entity.group},
                bars,
                {},
            )
            rows.append(row)
        ranked = analytics.rank_rows(rows)
        store.save_hotspots_history(build_hotspot_history(ranked, date))
        written += 1
    if written:
        LOG.info("回填热点历史 %d 天", written)
    return written


# ---------------------------------------------------------------- 市场行情


# ---------------------------------------------------------------- 行情（按需拉取）

#: 请求路径按需拉取失败时给前端的提示
UPSTREAM_WARNING = "上游暂时不可用，稍后重试"

_ondemand_guard = threading.Lock()
_ondemand_locks: dict[str, threading.Lock] = {}
_ondemand_failed: dict[str, float] = {}


def _ondemand_lock(key: str) -> threading.Lock:
    """按 code 维度取互斥锁，避免同一标的并发请求打爆上游。"""
    with _ondemand_guard:
        lock = _ondemand_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _ondemand_locks[key] = lock
        return lock


def _ondemand_cooldown(key: str) -> bool:
    """失败负缓存：60 秒内不再重试同一标的。"""
    with _ondemand_guard:
        until = _ondemand_failed.get(key)
        if until is None:
            return False
        if time.monotonic() >= until:
            _ondemand_failed.pop(key, None)
            return False
        return True


def _ondemand_fail(key: str) -> None:
    with _ondemand_guard:
        _ondemand_failed[key] = time.monotonic() + config.ONDEMAND_NEGATIVE_TTL_S
        if len(_ondemand_failed) > 512:  # 防止长期运行内存增长
            now = time.monotonic()
            for name in [name for name, until in _ondemand_failed.items() if until <= now]:
                _ondemand_failed.pop(name, None)


def _is_known_code(code: str) -> bool:
    """本地认识这个代码吗：实体表 / 指数 / 名称缓存 / 自选。"""
    if code in config.ENTITIES or code in config.INDEX_CODES:
        return True
    if code in _names():
        return True
    return any(str(item.get("code")) == code for item in store.load_watchlist().get("items") or [])


def _fetch_bars_ondemand(code: str) -> tuple[list[dict[str, Any]], str]:
    """请求路径专用的短超时取日线：腾讯 2s，失败再用东财 1s（总预算 ≤3s）。"""
    errors: list[str] = []
    try:
        bars = sources.fetch_tencent_bars(code, 500, timeout=config.ONDEMAND_TIMEOUT_S, retries=0)
        if bars:
            return bars, "tencent"
        errors.append("腾讯返回空")
    except (sources.SourceError, OSError, ValueError) as exc:
        errors.append(str(exc))
    try:
        bars = sources.fetch_eastmoney_bars(code, 500, timeout=config.ONDEMAND_FALLBACK_TIMEOUT_S)
        if bars:
            return bars, "eastmoney"
        errors.append("东方财富返回空")
    except (sources.SourceError, OSError, ValueError) as exc:
        errors.append(str(exc))
    raise sources.SourceError("; ".join(errors))


def _ondemand_bars(code: str) -> tuple[list[dict[str, Any]], str | None]:
    """本地没有日线时按需向上游拉一次并落盘；失败返回 (空, warning)。"""
    bars = store.load_bars(code)
    if bars:
        return bars, None
    key = f"bars:{code}"
    if _ondemand_cooldown(key):
        return [], UPSTREAM_WARNING
    lock = _ondemand_lock(key)
    if not lock.acquire(timeout=config.ONDEMAND_TIMEOUT_S + config.ONDEMAND_FALLBACK_TIMEOUT_S + 1):
        return [], UPSTREAM_WARNING
    try:
        bars = store.load_bars(code)
        if bars:  # 等锁期间别的请求已经拉完
            return bars, None
        fresh, source = _fetch_bars_ondemand(code)
        if not fresh:
            _ondemand_fail(key)
            return [], UPSTREAM_WARNING
        store.save_bars(
            code,
            fresh,
            name=_display_name(_names(), code),
            source=source,
            adjustment="qfq" if config.is_etf_code(code) else "none",
        )
        LOG.info("按需拉取 %s 日线 %d 根（%s）", code, len(fresh), source)
        return fresh, None
    except (sources.SourceError, OSError, ValueError) as exc:
        LOG.warning("按需拉取 %s 日线失败：%s", code, exc)
        _ondemand_fail(key)
        return [], UPSTREAM_WARNING
    finally:
        lock.release()


def _ondemand_intraday(code: str) -> tuple[dict[str, Any] | None, str | None]:
    """本地没有分时缓存时按需向上游拉一次并落盘；失败返回 (None, warning)。"""
    path = store.path_for("intraday", f"{code}.json")
    payload = store.read_json(path, {})
    if isinstance(payload, dict) and payload.get("days"):
        return payload, None
    key = f"intraday:{code}"
    if _ondemand_cooldown(key):
        return None, UPSTREAM_WARNING
    lock = _ondemand_lock(key)
    if not lock.acquire(timeout=config.ONDEMAND_TIMEOUT_S + 1):
        return None, UPSTREAM_WARNING
    try:
        payload = store.read_json(path, {})
        if isinstance(payload, dict) and payload.get("days"):
            return payload, None
        fresh = sources.fetch_intraday(code, timeout=config.ONDEMAND_TIMEOUT_S)
        fresh["updated_at"] = store.iso_now()
        store.write_json(path, fresh, compact=True)
        LOG.info("按需拉取 %s 分时 %d 天", code, len(fresh.get("days") or []))
        return fresh, None
    except (sources.SourceError, OSError, ValueError) as exc:
        LOG.warning("按需拉取 %s 分时失败：%s", code, exc)
        _ondemand_fail(key)
        return None, UPSTREAM_WARNING
    finally:
        lock.release()


def build_bars(app: App, code: str, span: str, limit: int) -> dict[str, Any]:
    """GET /api/market/bars：本地优先，缺失时按需拉取一次。"""
    bars = store.load_bars(code)
    warning: str | None = None
    if not bars:
        bars, warning = _ondemand_bars(code)
    if not bars and not _is_known_code(code):
        raise LookupError(f"没有 {code} 的本地日线")
    quotes, _fetched_at, _stale = _cached_quotes(app)
    quote = quotes.get(code)
    name = (quote or {}).get("name") or _display_name(_names(), code)
    if span == "day":
        series = [dict(bar) for bar in bars]
    else:
        series = analytics.resample_ohlc(bars, span) if bars else []
    series = series[-limit:] if limit and len(series) > limit else series
    current_year = str(series[-1]["date"])[:4] if series else ""
    year_start = f"{current_year}-01-01" if current_year else ""
    ytd_window = (
        [
            bar
            for bar in bars
            if year_start <= str(bar.get("date") or "") <= str(series[-1]["date"])
        ]
        if series and year_start
        else []
    )
    stats_window = ytd_window if span == "day" and len(ytd_window) > 1 else series
    stats = analytics.market_stats(stats_window, "day" if span == "day" else span, store.trade_date_of())
    ytd_base = None
    ytd_base_date = None
    for bar in reversed(bars):
        if str(bar.get("date") or "") < year_start:
            ytd_base = analytics.to_float(bar.get("close")); ytd_base_date = bar.get("date"); break
    if span == "day" and series:
        ytd_base = None
        for bar in reversed(bars):
            if str(bar.get("date") or "") < year_start:
                ytd_base = analytics.to_float(bar.get("close"))
                break
        stats["change"] = (
            analytics.pct_change(analytics.to_float(series[-1].get("close")), ytd_base)
            if ytd_base
            else stats["change"]
        )
        stats["change_label"] = "今年以来" if ytd_base else stats["change_label"]
    ma20 = analytics.moving_average(series, 20)
    ma60 = analytics.moving_average(series, 60)
    return {
        "generated_at": store.iso_now(),
        "code": code,
        "name": name,
        "kind": config.kind_of(code),
        "span": span,
        "adjustment": "qfq" if config.is_etf_code(code) else "none",
        "quote": quote if quote else None,
        "bars": series,
        "ma": {"ma5":analytics.moving_average(series,5),"ma10":analytics.moving_average(series,10),
               "ma30":analytics.moving_average(series,30),"ma20": ma20, "ma60": ma60},
        "stats": stats,
        "warning": warning,
        "ytd": {"year": int(current_year) if current_year else None, "start_date": year_start,
                "baseline": ytd_base, "baseline_date": ytd_base_date},
        "coverage": __import__(__package__ + '.coverage', fromlist=['inspect']).inspect(code),
    }


def build_intraday(app: App, code: str, days: int) -> dict[str, Any]:
    """GET /api/market/intraday：本地优先，缺失时按需拉取一次。"""
    payload = store.read_json(store.path_for("intraday", f"{code}.json"), {})
    quotes, _fetched_at, _stale = _cached_quotes(app)
    quote = quotes.get(code) or {}
    warning: str | None = None
    if not (isinstance(payload, dict) and payload.get("days")):
        payload, warning = _ondemand_intraday(code)
    name = quote.get("name") or _display_name(_names(), code)
    if not (isinstance(payload, dict) and payload.get("days")):
        if not _is_known_code(code) and not store.load_bars(code):
            raise LookupError(f"没有 {code} 的分时缓存")
        bars = store.load_bars(code)
        return {
            "generated_at": store.iso_now(),
            "code": code,
            "name": name,
            "pre_close": analytics.to_float(quote.get("pre_close"))
            or (analytics.to_float(bars[-1].get("close")) if bars else None),
            "days": [],
            "warning": warning or UPSTREAM_WARNING,
        }
    assert isinstance(payload, dict)
    all_days = list(payload.get("days") or [])[-days:]
    return {
        "generated_at": store.iso_now(),
        "code": code,
        "name": quote.get("name") or payload.get("name") or name,
        "pre_close": analytics.to_float(quote.get("pre_close")) or analytics.to_float(payload.get("pre_close")),
        "days": all_days,
        "warning": warning,
    }


def build_quotes(app: App, codes: Sequence[str]) -> dict[str, Any]:
    """GET /api/market/quotes。"""
    quotes, _fetched_at, _stale = _cached_quotes(app)
    result = []
    for code in codes:
        quote = quotes.get(code)
        if quote is None:
            quote = _empty_quote(code, _display_name(_names(), code))
        result.append(quote)
    return {"generated_at": store.iso_now(), "quotes": result}


def build_minute(app: App, code: str, interval: int, limit: int) -> dict[str, Any]:
    """GET /api/market/minute：读缓存/按需拉取分钟 K（失败 200 + warning）。"""
    payload = minute.get_minute(code, interval, limit)
    quotes, _fetched_at, _stale = _cached_quotes(app)
    quote = quotes.get(code) or {}
    return {
        "generated_at": store.iso_now(),
        "code": code,
        "name": quote.get("name") or payload.get("name") or _display_name(_names(), code),
        "interval": payload["interval"],
        "count": payload["count"],
        "as_of": payload["as_of"],
        "source": payload["source"],
        "warning": payload["warning"],
        "bars": payload["bars"],
    }


def build_team_minute(app: App, interval: int, limit: int = config.MINUTE_LIMIT_DEFAULT) -> dict[str, Any]:
    """GET /api/team/minute：国家队篮子（26 只份额加权）分钟序列，单位亿元。"""
    payload = team_minute.get_team_minute(interval, limit)
    return {"generated_at": store.iso_now(), **payload}


# ---------------------------------------------------------------- 数据源可观测性


def build_sources(app: App) -> dict[str, Any]:
    """GET /api/sources：多源状态、分组与最近告警。"""
    return {
        "generated_at": store.iso_now(),
        "sources": observability.registry.snapshot(),
        "groups": observability.registry.groups(),
        "alerts": observability.registry.alerts(limit=20),
    }


# ---------------------------------------------------------------- 自选


def build_watchlist(app: App) -> dict[str, Any]:
    """GET /api/watchlist。"""
    payload = store.load_watchlist()
    quotes, _fetched_at, _stale = _cached_quotes(app)
    items = []
    for item in payload.get("items") or []:
        code = str(item.get("code") or "")
        if not code:
            continue
        quote = quotes.get(code)
        if quote is None:
            quote = _empty_quote(code, item.get("label") or _display_name(_names(), code))
        items.append(
            {
                "code": code,
                "kind": str(item.get("kind") or config.kind_of(code)),
                "label": item.get("label"),
                "quote": quote,
            }
        )
    return {"generated_at": store.iso_now(), "updated_at": payload.get("updated_at"), "items": items}


# ---------------------------------------------------------------- 搜索 / 元数据


def _catalog(app: App) -> list[dict[str, str]]:
    """搜索目录：实体表 + 自选 + 名称缓存。"""
    names = _names()
    entries: dict[str, dict[str, str]] = {}
    for code, entity in config.ENTITIES.items():
        entries[code] = {
            "code": code,
            "name": names.get(code) or entity.name,
            "kind": config.kind_of(code),
            "market": code[:2],
            "direction": entity.direction,
            "group": entity.group,
        }
    for code, name in names.items():
        if code in entries:
            entries[code]["name"] = name
    for item in store.load_watchlist().get("items") or []:
        code = str(item.get("code") or "")
        if code and code not in entries:
            entries[code] = {
                "code": code,
                "name": str(item.get("label") or names.get(code) or config.entity_name(code)),
                "kind": str(item.get("kind") or config.kind_of(code)),
                "market": code[:2],
                "direction": "",
                "group": "",
            }
    return list(entries.values())


def _local_search(app: App, text: str) -> list[dict[str, str]]:
    """本地目录检索（实体表 + 自选 + 名称缓存），按匹配质量排序。"""
    lowered = text.lower()
    scored: list[tuple[int, dict[str, str]]] = []
    for entry in _catalog(app):
        haystacks = [
            entry["code"].lower(),
            entry["name"].lower(),
            entry.get("direction", "").lower(),
            entry.get("group", "").lower(),
        ]
        if not any(lowered in hay for hay in haystacks):
            continue
        if entry["code"].lower() == lowered:
            score = 100
        elif entry["name"].lower() == lowered:
            score = 90
        elif entry["name"].lower().startswith(lowered):
            score = 70
        elif entry.get("direction", "").lower() == lowered:
            score = 60
        else:
            score = 30
        scored.append((score, entry))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["code"]))
    return [
        {"code": entry["code"], "name": entry["name"], "kind": entry["kind"], "market": entry["market"]}
        for _score, entry in scored[: config.SEARCH_LIMIT]
    ]


def build_search(app: App, query: str) -> dict[str, Any]:
    """GET /api/search：本地目录优先，不足 20 条时用腾讯搜索建议兜底。

    上游查询超时/失败时回退本地结果，并在 ``warning`` 里说明（请求路径总预算 <3s）。
    """
    text = query.strip()
    if not text:
        raise ValueError("搜索词不能为空")
    results = _local_search(app, text)
    warning: str | None = None
    if len(results) < config.SEARCH_LIMIT:
        try:
            upstream = sources.fetch_tencent_search(
                text, timeout=config.SEARCH_UPSTREAM_TIMEOUT_S, limit=config.SEARCH_LIMIT
            )
        except (sources.SourceError, OSError) as exc:
            upstream = []
            warning = f"搜索上游暂不可用，已回退本地目录：{exc}"
        seen = {item["code"] for item in results}
        for item in upstream:
            if len(results) >= config.SEARCH_LIMIT:
                break
            if item["code"] in seen:
                continue
            seen.add(item["code"])
            results.append(item)
    return {"generated_at": store.iso_now(), "results": results, "warning": warning}


def build_meta(app: App) -> dict[str, Any]:
    """GET /api/meta。"""
    names = _names()
    entities = [
        {"code": code, "name": names.get(code) or entity.name, "group": entity.group}
        for code, entity in sorted(config.ENTITIES.items())
    ]
    return {
        "product": config.PRODUCT,
        "version": app.version,
        "built_at": config.BUILT_AT,
        "entities": entities,
        "definitions": {
            "net_flow": "相邻交易日总份额差 × 当日价格，单位亿元；正数为净申购代理。",
            "heat": config.HEAT_RULE,
            "trend": "收盘离开极值 10% 才确认转折；未确认段标「未完成/左边」。",
            "breadth": "统计对象是行业/主题代表 ETF，不是全市场个股。",
            "unit": "金额元、份额份、图表合成市值亿元；涨跌幅与占比为小数。",
        },
        "data_sources": [
            {"id": "tencent_quote", "name": "腾讯行情", "usage": "报价/日线/分时/总份额（主源）"},
            {"id": "sina_quote", "name": "新浪行情", "usage": "报价/日线/分钟K 备用"},
            {"id": "eastmoney_quote", "name": "东方财富行情", "usage": "报价/日线兜底、分钟K 主源"},
            {"id": "sina_bars", "name": "新浪日线", "usage": "日线第三兜底"},
            {"id": "eastmoney_fund", "name": "东方财富基金档案", "usage": "报告期申赎/持有人结构（单源）"},
        ],
        "limitations": [
            "总份额变化只能推断净申赎，不能识别申赎主体，不能断言国家队持仓。",
            "报告期申赎按基金披露季度更新，不是逐日数据。",
            "尚未接入节假日日历，节假日按「最近交易日 + closed」处理。",
            "热度分、趋势结构、宽度都是代表 ETF 口径的确定性规则，不构成投资建议。",
        ],
    }


# ---------------------------------------------------------------- 总览


def build_brief(
    national: Mapping[str, Any],
    hotspots: Mapping[str, Any],
    breadth: Mapping[str, Any],
    warnings: Sequence[str],
) -> dict[str, Any]:
    """总览「今日观察」：完全由同一响应里的真实字段生成。"""
    five = analytics.to_float(national.get("five_day_flow"))
    five_days = int(national.get("five_day_days") or 0)
    today = analytics.to_float(national.get("today_flow"))
    partial = bool(national.get("today_flow_partial"))
    flow_date = str(national.get("flow_date") or "")
    today_text = "今日" if flow_date == store.trade_date_of() else "最近交易日"
    above20 = int(breadth.get("above_ma20") or 0)
    total = int(breadth.get("total") or 0)
    ratio = (above20 / total) if total else None
    counts = hotspots.get("counts") or {}
    hot = int(counts.get("hot") or 0) + int(counts.get("warming") or 0)
    crowded = int(counts.get("crowded") or 0)
    top_rows = hotspots.get("top") or []
    top_names = "、".join(str(row.get("direction") or row.get("name") or "") for row in top_rows[:2]) or "—"
    median = analytics.to_float(breadth.get("median_ret_20d"))
    flow_yi = analytics.to_yi(today) if today is not None else None
    five_yi = analytics.to_yi(five) if five is not None else None
    window_label = "5 日" if five_days >= 5 else f"近 {five_days} 日"

    bullets: list[str] = []
    if flow_yi is not None:
        direction = "净申购" if flow_yi >= 0 else "净赎回"
        text = f"篮子{today_text}估算{direction} {abs(flow_yi):.1f} 亿元"
        if partial:
            text += "（未收盘）"
        if five_yi is not None and five_days > 1 and abs(flow_yi) != abs(five_yi):
            five_direction = "净申购" if five_yi >= 0 else "净赎回"
            text += f"；{window_label}{five_direction} {abs(five_yi):.1f} 亿元"
        bullets.append(_clip(text, 40))
    elif five_yi is not None:
        direction = "净申购" if five_yi >= 0 else "净赎回"
        bullets.append(_clip(f"篮子{window_label}估算{direction} {abs(five_yi):.1f} 亿元", 40))
    else:
        bullets.append("缺：相邻两个交易日的份额，无法计算净申赎")
    if total:
        bullets.append(_clip(f"{total} 个方向中 {above20} 个站上 MA20", 40))
    else:
        bullets.append("缺：热点代表 ETF 的本地日线")
    if counts.get("hot"):
        bullets.append(_clip(f"{hot} 个方向强势、{crowded} 个高位拥挤；领先：{top_names}", 40))
    elif median is not None:
        bullets.append(_clip(f"中位 20 日收益 {median:+.1%}，热点集中在 {top_names}", 40))
    else:
        bullets.append("缺：热点报告的排名与热度")

    if five is None and today is None and not total:
        return {
            "tone": "neutral",
            "headline": "数据尚不足以形成观察，等待首轮采集。",
            "bullets": bullets[:3],
        }

    positive = five is not None and five > 0 and ratio is not None and ratio >= 0.5
    risk = (five is not None and five < 0 and ratio is not None and ratio < 0.5) or (
        five is None and ratio is not None and ratio < 0.35
    )
    if positive:
        tone = "positive"
        headline = _clip(
            f"篮子{window_label}净申购 {abs(five_yi):.1f} 亿元，宽度过半。" if five_yi is not None else "篮子是净申购，宽度过半。",
            30,
        )
    elif risk:
        tone = "risk"
        if five_yi is not None and five_yi < 0:
            headline = _clip(f"篮子{window_label}净赎回 {abs(five_yi):.1f} 亿元，宽度偏弱。", 30)
        else:
            headline = _clip(f"仅 {above20}/{total} 个方向站上 MA20，宽度偏弱。", 30)
    else:
        tone = "neutral"
        if five_yi is not None:
            headline = _clip(f"篮子{window_label}净申赎 {five_yi:+.1f} 亿元，结构分化。", 30)
        elif flow_yi is not None:
            headline = _clip(f"{today_text}篮子估算净申赎 {flow_yi:+.1f} 亿元。", 30)
        else:
            headline = "数据尚不足以形成观察，等待更多交易日。"
    return {"tone": tone, "headline": headline, "bullets": bullets[:3]}


def _clip(text: str, limit: int) -> str:
    """按字符数截断（契约要求 headline<=30、bullets<=40 字）。"""
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def build_overview(app: App) -> dict[str, Any]:
    """GET /api/overview。"""
    now = _now()
    quotes, _fetched_at, stale = _cached_quotes(app)
    daily = store.load_daily()
    hotspot_payload = build_hotspots(app)
    history = _team_history(daily, quotes)
    trade_date = resolve_trade_date(quotes, daily)
    session = resolve_session(trade_date, now)
    latest_day = history[-1]["date"] if history else None
    latest_close = history[-1] if history else None
    cumulative = analytics.cumulative_stats(history)
    today_flow: float | None = None
    today_partial = False
    today_share_delta: float | None = None
    flow_date: str | None = None
    is_today = trade_date == store.trade_date_of(now)
    if store.is_trading_time(now) and is_today and (latest_day is None or latest_day < trade_date):
        live = analytics.live_flow(config.TEAM_CODES, quotes, latest_day, (daily.get("days") or {}).get(latest_day) or {}, trade_date)
        if live:
            today_flow = live["amount"]
            today_share_delta = live["share_delta"]
            today_partial = True
            flow_date = live["date"]
    if today_flow is None and latest_close is not None:
        # 收盘后/周末回退到最近一个已入库交易日的净申赎，只要有历史就不该为 null
        today_flow = latest_close.get("amount")
        today_share_delta = latest_close.get("share_delta")
        flow_date = latest_close.get("date")
        today_partial = False
    five_day_flow, five_day_days = analytics.recent_flow(history, 5)
    adjacent_days = sum(
        1
        for row in history
        if row.get("amount") is not None and int(row.get("gap_days", 1)) == 1
    )
    month_flow = (
        analytics.calendar_month_flow(history, trade_date[:7]) if adjacent_days >= 5 else None
    )
    total_shares, total_value = analytics.basket_totals((daily.get("days") or {}).get(latest_day) or {}, config.TEAM_CODES)
    chart_cache = _load_chart_cache()
    chart = _live_chart(chart_cache, quotes, trade_date)
    indices = [quotes.get(code) or _empty_quote(code) for code in config.INDEX_CODES]
    hotspot_rows = list(hotspot_payload.get("rows") or [])
    top_rows: list[dict[str, Any]] = []
    for row in hotspot_rows[: config.OVERVIEW_TOP_N]:
        item = dict(row)
        quote = quotes.get(str(item.get("code")))
        if quote:
            if quote.get("price") is not None:
                item["price"] = quote["price"]
            if quote.get("change_pct") is not None:
                item["change_pct"] = quote["change_pct"]
        top_rows.append(item)
    enriched_rows = []
    for row in hotspot_rows:
        item = dict(row)
        quote = quotes.get(str(item.get("code")))
        if quote:
            if quote.get("price") is not None:
                item["price"] = quote["price"]
            if quote.get("change_pct") is not None:
                item["change_pct"] = quote["change_pct"]
        enriched_rows.append(item)
    breadth = analytics.breadth_from_rows(enriched_rows, quotes) if enriched_rows else {
        "above_ma20": 0,
        "above_ma60": 0,
        "total": 0,
        "advancers": 0,
        "decliners": 0,
        "median_ret_20d": None,
    }
    national = {
        "today_flow": today_flow,
        "today_flow_partial": today_partial,
        "flow_date": flow_date,
        "today_share_delta": today_share_delta,
        "five_day_flow": five_day_flow,
        "five_day_days": five_day_days,
        "month_flow": month_flow,
        "total_value": total_value,
        "total_shares": total_shares,
        "trend_state": chart["trend"]["state"],
        "trend_text": chart["trend"]["text"],
        "up": sum(1 for code, row in quotes.items() if code in set(config.TEAM_CODES) and (analytics.to_float(row.get("change_pct")) or 0) > 0) or None,
        "down": sum(1 for code, row in quotes.items() if code in set(config.TEAM_CODES) and (analytics.to_float(row.get("change_pct")) or 0) < 0) or None,
        "history_days": len(history),
        "daily_series": [{"date": row["date"], "amount": row.get("amount")} for row in history[-30:]],
    }
    counts = hotspot_payload.get("counts") or analytics.count_states([])
    hotspots = {
        "state": str(hotspot_payload.get("state") or "未初始化"),
        "headline": str(hotspot_payload.get("headline") or "热点雷达还没有数据，等待首轮日终采集。"),
        "counts": counts,
        "top": top_rows,
        "as_of": hotspot_payload.get("as_of"),
    }
    health = _data_health(app, quotes)
    brief = build_brief(national, hotspots, breadth, health["warnings"])
    watchlist = []
    names = _names()
    for item in store.load_watchlist().get("items") or []:
        code = str(item.get("code") or "")
        if not code:
            continue
        watchlist.append(
            {
                "code": code,
                "label": item.get("label"),
                "quote": quotes.get(code) or _empty_quote(code, item.get("label") or _display_name(names, code)),
            }
        )
    quote_times = [str(quote.get("quote_time") or "") for quote in quotes.values() if quote.get("quote_time")]
    if quote_times:
        as_of = max(quote_times)
    elif latest_day:
        as_of = f"{latest_day}T15:00:00+08:00"
    else:
        as_of = store.iso_now()
    warnings = list(health["warnings"])
    if stale:
        warnings.append("报价已过期")
    return {
        "generated_at": store.iso_now(),
        "as_of": as_of,
        "trade_date": trade_date,
        "session": session,
        "is_trading": session in {"morning", "afternoon"} and is_today,
        "indices": indices,
        "national": national,
        "hotspots": hotspots,
        "breadth": breadth,
        "brief": brief,
        "watchlist": watchlist,
        "data_health": {**health, "warnings": warnings},
    }
