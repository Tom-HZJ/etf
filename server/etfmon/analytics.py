"""确定性计算：国家队篮子、热点雷达、市场宽度、趋势结构、合成 K 线。

这里的函数都是纯函数（不读写磁盘、不联网），方便单元测试。
金额单位：元；份额单位：份；涨跌幅/占比：小数；图表合计市值单位：亿元。
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from . import config

# ---------------------------------------------------------------- 基础工具


def to_float(value: Any) -> float | None:
    """宽松转 float：非法值返回 None（不抛异常）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if not text or text in {"-", "--"}:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    return number if math.isfinite(number) else None


def clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """把数值裁到区间内。"""
    return max(low, min(high, value))


def mean(values: Iterable[Any]) -> float | None:
    """有效数字的算术平均；空列表返回 None。"""
    clean = [number for number in (to_float(value) for value in values) if number is not None]
    return sum(clean) / len(clean) if clean else None


def median(values: Iterable[Any]) -> float | None:
    """有效数字的中位数；空列表返回 None。"""
    clean = [number for number in (to_float(value) for value in values) if number is not None]
    return statistics.median(clean) if clean else None


def pct_change(now_value: float | None, before_value: float | None) -> float | None:
    """``now/before - 1``；任一为空或 before 为 0 时返回 None。"""
    now_number = to_float(now_value)
    before_number = to_float(before_value)
    if now_number is None or not before_number:
        return None
    return now_number / before_number - 1


def rounded(value: float | None, digits: int = 6) -> float | None:
    """四舍五入；None 透传。"""
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def sum_or_none(values: Iterable[float | None]) -> float | None:
    """求和；全部为空时返回 None。"""
    usable = [value for value in values if value is not None]
    return sum(usable) if usable else None


def to_yi(amount: float | None) -> float | None:
    """元 → 亿元。"""
    return None if amount is None else amount / 1e8


# ---------------------------------------------------------------- 国家队篮子


def day_totals(
    codes: Sequence[str],
    current: Mapping[str, Mapping[str, Any]],
    previous: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """单个交易日的篮子合计。

    ``current``/``previous`` 形如 ``{code: {"shares": 份, "price": 元}}``。
    净申赎 = Σ(当日份额 - 上一日份额) × 当日价格，只在两天都有效时计入。
    """
    total_shares = 0.0
    total_value = 0.0
    amount = 0.0
    share_delta = 0.0
    valued = 0
    inflow = 0
    outflow = 0
    for code in codes:
        row = current.get(code) or {}
        shares = to_float(row.get("shares"))
        price = to_float(row.get("price"))
        if shares is not None and shares > 0:
            total_shares += shares
            if price is not None and price > 0:
                total_value += shares * price
        if previous is None:
            continue
        base = (previous.get(code) or {})
        base_shares = to_float(base.get("shares"))
        if shares is None or base_shares is None:
            continue
        delta = shares - base_shares
        share_delta += delta
        if delta > 0:
            inflow += 1
        elif delta < 0:
            outflow += 1
        if price is None or price <= 0:
            continue
        amount += delta * price
        valued += 1
    return {
        "amount": amount if valued else None,
        "share_delta": share_delta if (inflow or outflow) else None,
        "inflow": inflow,
        "outflow": outflow,
        "total_shares": total_shares or None,
        "total_value": total_value or None,
    }


def trading_day_gap(earlier: str | None, later: str, calendar: Sequence[str] | None = None) -> int:
    """两个记录日期之间相隔多少个交易日（1 = 相邻交易日）。

    优先用本地日线日期作为交易日历；日历不可用时退回「工作日估算」。
    """
    if not earlier or earlier >= later:
        return 1
    if calendar:
        count = sum(1 for date in calendar if earlier < date <= later)
        if count:
            return count
    # 兜底：数工作日（含 later，不含 earlier）
    try:
        start = datetime.strptime(earlier, "%Y-%m-%d").date()
        end = datetime.strptime(later, "%Y-%m-%d").date()
    except ValueError:
        return 1
    from datetime import timedelta

    count = 0
    cursor = start + timedelta(days=1)
    while cursor <= end:
        if cursor.weekday() < 5:
            count += 1
        cursor += timedelta(days=1)
    return max(1, count)


def build_history(
    days: Mapping[str, Mapping[str, Mapping[str, Any]]],
    codes: Sequence[str],
    calendar: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """把 ``daily.json`` 的 ``days`` 转成按日期升序的 TeamDay 列表。

    ``gap_days`` 是该记录与上一条记录之间相隔的交易日数（1 = 相邻）。
    跨日缺口仍保留展示，但 :func:`cumulative_stats` 不会把它们当成单日净申赎。
    """
    dates = sorted(str(date) for date in days.keys() if str(date))
    history: list[dict[str, Any]] = []
    for index, date in enumerate(dates):
        previous_date = dates[index - 1] if index else None
        current = days.get(date) or {}
        previous = (days.get(previous_date) or {}) if previous_date else None
        totals = day_totals(codes, current, previous)
        history.append(
            {
                "date": date,
                "previous": previous_date,
                "gap_days": trading_day_gap(previous_date, date, calendar),
                "amount": totals["amount"],
                "share_delta": totals["share_delta"],
                "inflow": totals["inflow"],
                "outflow": totals["outflow"],
                "total_shares": totals["total_shares"],
                "total_value": totals["total_value"],
            }
        )
    return history


def _adjacent_rows(history: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """有净申赎金额且与上一条记录相邻的交易日（gap_days==1）。"""
    return [
        row
        for row in history
        if row.get("amount") is not None and int(row.get("gap_days", 1)) == 1
    ]


def cumulative_stats(history: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """累计净申赎统计：只统计相邻交易日，跨日缺口单独计数。"""
    valued = _adjacent_rows(history)
    gapped = [
        row
        for row in history
        if row.get("amount") is not None and int(row.get("gap_days", 1)) != 1
    ]
    amount = sum(float(row["amount"]) for row in valued) if valued else None
    inflow_days = sum(1 for row in valued if float(row["amount"]) > 0)
    outflow_days = sum(1 for row in valued if float(row["amount"]) < 0)
    best = max(valued, key=lambda row: float(row["amount"]), default=None)
    worst = min(valued, key=lambda row: float(row["amount"]), default=None)
    return {
        "amount": amount if amount is not None else 0.0,
        "days": len(valued),
        "since": str(valued[0]["date"]) if valued else None,
        "excluded_days": len(gapped),
        "inflow_days": inflow_days,
        "outflow_days": outflow_days,
        "best": {"date": str(best["date"]), "amount": float(best["amount"])} if best else None,
        "worst": {"date": str(worst["date"]), "amount": float(worst["amount"])} if worst else None,
    }


def recent_flow(
    history: Sequence[Mapping[str, Any]], count: int = 5
) -> tuple[float | None, int]:
    """最近 ``count`` 个**相邻**交易日的净申赎合计（元）与实际天数。

    历史不足 ``count`` 天时按实际天数求和（契约允许不足 5 天也展示）；
    跨日缺口（gap_days>1）不计入。
    """
    valued = _adjacent_rows(history)
    used = valued[-count:] if count > 0 else []
    if not used:
        return None, 0
    return sum(float(row["amount"]) for row in used), len(used)


def calendar_month_flow(history: Sequence[Mapping[str, Any]], month: str) -> float | None:
    """某个自然月（``YYYY-MM``）的净申赎合计（元）。"""
    rows = [row for row in history if str(row.get("date") or "").startswith(month) and row.get("amount") is not None]
    return sum(float(row["amount"]) for row in rows) if rows else None


def basket_totals(current: Mapping[str, Mapping[str, Any]], codes: Sequence[str]) -> tuple[float | None, float | None]:
    """篮子总份额（份）与总市值（元），只累加有效报价。"""
    totals = day_totals(codes, current, None)
    return totals["total_shares"], totals["total_value"]


def basket_rows(
    codes: Sequence[str],
    quotes: Mapping[str, Mapping[str, Any]],
    latest: Mapping[str, Mapping[str, Any]],
    previous: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """26 只明细行：价格/涨跌幅/份额/日增减/净申赎/权重。"""
    total_value = 0.0
    for code in codes:
        quote = quotes.get(code) or {}
        row = latest.get(code) or {}
        price = to_float(quote.get("price")) or to_float(row.get("price"))
        shares = to_float(quote.get("shares")) or to_float(row.get("shares"))
        if price and shares:
            total_value += price * shares
    rows: list[dict[str, Any]] = []
    for code in codes:
        entity = config.ENTITIES.get(code)
        quote = quotes.get(code) or {}
        today = latest.get(code) or {}
        before = previous.get(code) or {}
        price = to_float(quote.get("price"))
        if price is None:
            price = to_float(today.get("price"))
        shares = to_float(quote.get("shares"))
        if shares is None:
            shares = to_float(today.get("shares"))
        share_delta = None
        amount = None
        today_shares = to_float(today.get("shares"))
        before_shares = to_float(before.get("shares"))
        if today_shares is not None and before_shares is not None:
            share_delta = today_shares - before_shares
            used_price = to_float(today.get("price")) or price
            if used_price:
                amount = share_delta * used_price
        market_cap = price * shares if price and shares else None
        weight = market_cap / total_value if market_cap and total_value else None
        rows.append(
            {
                "code": code,
                "name": str(quote.get("name") or (entity.name if entity else code)),
                "group": entity.group if entity else "",
                "price": price,
                "change_pct": to_float(quote.get("change_pct")),
                "shares": shares,
                "share_delta": share_delta,
                "amount": amount,
                "market_cap": market_cap,
                "weight": weight,
                "missing_quote": quote.get("price") is None,
            }
        )
    return rows


def market_pnl(quotes: Mapping[str, Mapping[str, Any]], codes: Sequence[str]) -> float | None:
    """按最新价与昨收估算的篮子市值变动（元）。"""
    total = 0.0
    counted = 0
    for code in codes:
        quote = quotes.get(code) or {}
        price = to_float(quote.get("price"))
        pre_close = to_float(quote.get("pre_close"))
        shares = to_float(quote.get("shares"))
        if price is None or pre_close is None or shares is None:
            continue
        total += (price - pre_close) * shares
        counted += 1
    return total if counted else None


def live_flow(
    codes: Sequence[str],
    quotes: Mapping[str, Mapping[str, Any]],
    latest_day: str | None,
    latest: Mapping[str, Mapping[str, Any]],
    trade_date: str,
) -> dict[str, Any] | None:
    """盘中累计净申赎代理：最新总份额 - 上一交易日收盘份额。

    只在当日份额尚未收盘入库时使用；``partial`` 恒为 True。
    """
    if not latest_day or latest_day >= trade_date:
        return None
    amount = 0.0
    share_delta = 0.0
    valued = 0
    inflow = 0
    outflow = 0
    for code in codes:
        quote = quotes.get(code) or {}
        shares = to_float(quote.get("shares"))
        price = to_float(quote.get("price"))
        base = to_float((latest.get(code) or {}).get("shares"))
        if shares is None or base is None or price is None or price <= 0:
            continue
        delta = shares - base
        amount += delta * price
        share_delta += delta
        valued += 1
        if delta > 0:
            inflow += 1
        elif delta < 0:
            outflow += 1
    if not valued:
        return None
    sample_time = ""
    for code in codes:
        quote_time = str((quotes.get(code) or {}).get("quote_time") or "")
        if len(quote_time) >= 19 and quote_time[11:19] > sample_time:
            sample_time = quote_time[11:19]
    return {
        "date": trade_date,
        "previous_day": latest_day,
        "net": amount,
        "amount": amount,
        "share_delta": share_delta,
        "partial": True,
        "inflow": inflow,
        "outflow": outflow,
        "time": sample_time or "",
    }


def intraday_series(
    intraday: Mapping[str, Any], trade_date: str, sample_time: str | None = None
) -> list[dict[str, Any]]:
    """当日盘中采样序列（契约字段：time/amount/share_delta）。"""
    samples = (intraday or {}).get(trade_date) or []
    result: list[dict[str, Any]] = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        amount = to_float(sample.get("amount"))
        share_delta = to_float(sample.get("share_delta"))
        if amount is None:
            continue
        result.append(
            {
                "time": str(sample.get("time") or ""),
                "amount": amount,
                "share_delta": share_delta or 0.0,
            }
        )
    return result


# ---------------------------------------------------------------- 报告期申赎


def quarter_label(date: str) -> str:
    """报告期日期 → 季度标签（``2026-09-30`` → ``Q3``）。"""
    text = str(date)
    try:
        month = int(text[5:7])
    except (ValueError, IndexError):
        return text
    return f"Q{(month - 1) // 3 + 1}"


def price_at(bars: Sequence[Mapping[str, Any]], date: str) -> float | None:
    """取 ``date`` 当日或之前最近一根日线的收盘价。"""
    best: float | None = None
    for bar in bars:
        if str(bar.get("date") or "") > date:
            break
        close = to_float(bar.get("close"))
        if close is not None and close > 0:
            best = close
    return best


def combine_flow_periods(
    periods_by_code: Mapping[str, Sequence[Mapping[str, Any]]],
    bars_by_code: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """把多只 ETF 的报告期申赎按报告期加总。

    上游 ``net`` 单位亿份；这里乘以**该报告期最后一个交易日收盘价**换算成亿元
    （逐只乘再加总），与契约 ``FlowPoint.net``（亿元）一致。若某只基金在报告日
    之前没有本地日线，退化为最近收盘价，并在 ``price_fallback_funds`` 里计数。
    ``net_pct`` 用期初份额加总计算。
    """
    grouped: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
    for code, periods in periods_by_code.items():
        for row in periods:
            date = str(row.get("date") or "")
            if date:
                grouped.setdefault(date, []).append((code, row))
    result: list[dict[str, Any]] = []
    for date in sorted(grouped):
        rows = grouped[date]
        net_shares = 0.0
        net_money = 0.0
        start_shares = 0.0
        covered = 0
        fallback_funds = 0
        for code, row in rows:
            net = to_float(row.get("net"))
            start = to_float(row.get("start_shares"))
            bars = bars_by_code.get(code) or []
            price = price_at(bars, date)
            if price is None and bars:
                price = to_float(bars[-1].get("close"))
                if price is not None:
                    fallback_funds += 1
            if net is None:
                continue
            net_shares += net
            if start:
                start_shares += start
            if price is not None:
                net_money += net * price
            covered += 1
        if not covered:
            continue
        result.append(
            {
                "date": date,
                "net": net_money,
                "net_pct": net_shares / start_shares if start_shares else None,
                "share_net": net_shares,
                "report_date": date,
                "label": quarter_label(date),
                "partial": False,
                "large_redemption": net_money <= -config.LARGE_REDEMPTION_YI,
                "funds": covered,
                "price_fallback_funds": fallback_funds,
            }
        )
    return result


def open_period_flow(
    periods: Sequence[Mapping[str, Any]],
    latest_date: str | None,
    latest_shares: float | None,
    price: float | None,
) -> dict[str, Any] | None:
    """未结束报告期的申赎：最新总份额 - 上一报告期末总份额（亿元）。"""
    if not periods or not latest_date or latest_shares is None:
        return None
    latest_period = max(periods, key=lambda row: str(row.get("date") or ""))
    period_date = str(latest_period.get("date") or "")
    base_yi = to_float(latest_period.get("total_shares"))
    latest_yi = latest_shares / 1e8
    if not period_date or latest_date <= period_date or not base_yi:
        return None
    net_shares = latest_yi - base_yi
    used_price = price if price is not None else None
    net_money = net_shares * used_price if used_price is not None else None
    try:
        month = int(latest_date[5:7])
    except ValueError:
        month = None
    return {
        "date": latest_date,
        "label": f"{month}月至今" if month else "至今",
        "net": net_money if net_money is not None else net_shares,
        "net_pct": net_shares / base_yi if base_yi else None,
        "partial": True,
        "base_date": period_date,
        "large_redemption": bool(net_money is not None and net_money <= -config.LARGE_REDEMPTION_YI),
    }


def open_period_basket(
    periods_by_code: Mapping[str, Sequence[Mapping[str, Any]]],
    latest_shares: Mapping[str, float | None],
    prices: Mapping[str, float | None],
    latest_date: str | None,
) -> dict[str, Any] | None:
    """篮子口径的「未结束报告期」申赎（亿元）。"""
    if not latest_date:
        return None
    latest_report = ""
    for periods in periods_by_code.values():
        for row in periods:
            date = str(row.get("date") or "")
            if date > latest_report:
                latest_report = date
    if not latest_report:
        return None
    net_shares = 0.0
    net_money = 0.0
    base_shares = 0.0
    covered = 0
    for code, periods in periods_by_code.items():
        matching = next((row for row in periods if str(row.get("date") or "") == latest_report), None)
        if matching is None:
            continue
        base = to_float(matching.get("total_shares"))
        shares = to_float(latest_shares.get(code))
        if base is None or shares is None:
            continue
        delta = shares / 1e8 - base
        net_shares += delta
        base_shares += base
        price = prices.get(code)
        if price is not None:
            net_money += delta * price
        covered += 1
    if not covered:
        return None
    try:
        month = int(latest_date[5:7])
    except ValueError:
        month = None
    return {
        "date": latest_date,
        "label": f"{month}月至今" if month else "至今",
        "net": net_money,
        "net_pct": net_shares / base_shares if base_shares else None,
        "partial": True,
        "base_date": latest_report,
        "funds": covered,
        "large_redemption": net_money <= -config.LARGE_REDEMPTION_YI,
    }


# ---------------------------------------------------------------- 趋势结构


def trend_structure(bars: Sequence[Mapping[str, Any]], threshold: float = config.TREND_THRESHOLD) -> dict[str, Any]:
    """10% 转折阈值下的趋势结构。

    规则（与旧台一致，重写后明确状态机）：
    - 收盘离开最近极值至少 ``threshold`` 才确认一个转折点（低点/高点）；
    - 最后一个极值点始终是「未确认」，据此判断当前处在左边还是右边；
    - 状态含义见 ``docs/API.md`` / README；极值日期是图形定位，confirmed_at 才是可知日期；add 不再代替入场判断。
    """
    closes: list[tuple[str, float]] = []
    for bar in bars:
        close = to_float(bar.get("close"))
        date = str(bar.get("date") or "")
        if close is None or close <= 0 or not date:
            continue
        closes.append((date, close))
    if len(closes) < 20:
        return {
            "state": "range",
            "threshold": threshold,
            "text": "日线还不够，暂时看不出趋势结构。",
            "add": False,
            "decision_role": "context_only",
            "last_confirmed_at": None,
            "points": [],
            "segments": [],
        }

    points: list[dict[str, Any]] = []

    def confirmed_point(extreme_index: int, known_index: int, kind: str) -> dict[str, Any]:
        return {"date": closes[extreme_index][0], "close": closes[extreme_index][1],
                "kind": kind, "confirmed": True,
                "confirmed_at": closes[known_index][0],
                "confirmation_close": closes[known_index][1],
                "confirmation_lag_bars": known_index - extreme_index}

    mode = "seek"
    anchor = 0  # 备选低点
    extreme = 0  # 备选高点 / 当前段极值
    for index in range(1, len(closes)):
        price = closes[index][1]
        if mode == "seek":
            if price <= closes[anchor][1]:
                anchor = index
            if price >= closes[extreme][1]:
                extreme = index
            if closes[extreme][1] >= closes[anchor][1] * (1 + threshold) and extreme > anchor:
                points.append(confirmed_point(anchor, index, "low"))
                mode = "rise"
                extreme = index
            elif closes[anchor][1] <= closes[extreme][1] * (1 - threshold) and anchor > extreme:
                points.append(confirmed_point(extreme, index, "high"))
                mode = "fall"
                extreme = anchor
            continue
        if mode == "rise":
            if price >= closes[extreme][1]:
                extreme = index
            elif price <= closes[extreme][1] * (1 - threshold):
                points.append(confirmed_point(extreme, index, "high"))
                mode = "fall"
                extreme = index
        else:  # fall
            if price <= closes[extreme][1]:
                extreme = index
            elif price >= closes[extreme][1] * (1 + threshold):
                points.append(confirmed_point(extreme, index, "low"))
                mode = "rise"
                extreme = index

    if mode == "seek":
        # 整体波动没超过阈值：区间震荡，取区间两端作为未确认点
        if closes[extreme][1] >= closes[anchor][1]:
            pending_kind = "high"
            pending_index = extreme
        else:
            pending_kind = "low"
            pending_index = anchor
    else:
        pending_kind = "high" if mode == "rise" else "low"
        pending_index = extreme
    points.append(
        {
            "date": closes[pending_index][0],
            "close": closes[pending_index][1],
            "kind": pending_kind,
            "confirmed": False,
            "confirmed_at": None,
        }
    )

    for point in points:
        if point["confirmed"]:
            point["label"] = "转折低点" if point["kind"] == "low" else "转折高点"
        else:
            point["label"] = "低点未确认" if point["kind"] == "low" else "高点未确认"

    confirmed = [point for point in points if point["confirmed"]]
    pending = points[-1]
    percent = f"{threshold:.0%}"
    if not confirmed:
        net_change = closes[-1][1] / closes[0][1] - 1 if closes[0][1] else 0.0
        if abs(net_change) < threshold / 2:
            state = "range"
            text = f"区间震荡：收盘还没离开极值 {percent}，没有确认的转折点。"
        elif net_change > 0:
            state = "up_left"
            text = f"上涨左边：{pending['date']} 高点还没确认，先按观察处理。"
        else:
            state = "down_left"
            text = f"下跌左边：{pending['date']} 低点还没转折，收盘比这段低点高出 {percent} 才换成右边。"
    elif confirmed[-1]["kind"] == "low":
        state = "up_right"
        text = f"底部右边已出现：{confirmed[-1]['date']} 低点后，于 {confirmed[-1]['confirmed_at']} 收盘确认反弹 {percent}。仅作结构背景。"
    else:
        state = "down_right"
        text = f"顶部右边已确认：{confirmed[-1]['date']} 高点后，于 {confirmed[-1]['confirmed_at']} 收盘确认回落 {percent}，当前仍在寻找新的低点。"

    # 段语义：从极值点出发的行情在「极值右侧」（right），走向极值的是「左侧」（left）。
    # 第一个段从数据起点出发，只能按方向标「左侧上涨/左侧下跌」。
    segments: list[dict[str, Any]] = []
    previous_date = closes[0][0]
    previous_kind: str | None = None  # 上一段起点的极值类型
    for point in points:
        if previous_kind == "low":
            side, label = "right", "右侧上涨"
        elif previous_kind == "high":
            side, label = "right", "右侧下跌"
        else:
            side = "left"
            label = "左侧下跌" if point["kind"] == "low" else "左侧上涨"
        if previous_date != point["date"]:
            # 起点与第一个转折点同一天时跳过零长度段，前端不需要
            segments.append(
                {
                    "from": previous_date,
                    "to": point["date"],
                    "side": side,
                    "label": label,
                }
            )
        previous_date = point["date"]
        previous_kind = point["kind"]

    add = False  # 图形结构不代表当前入场风险合适
    return {
        "state": state,
        "threshold": threshold,
        "text": text,
        "add": add,
        "decision_role": "context_only",
        "last_confirmed_at": confirmed[-1]["confirmed_at"] if confirmed else None,
        "points": points,
        "segments": segments,
    }


# ---------------------------------------------------------------- K 线合成


def combine_ohlc(
    bars_by_code: Mapping[str, Sequence[Mapping[str, Any]]],
    weights: Mapping[str, float],
    start: str,
) -> list[dict[str, Any]]:
    """份额加权合成 K 线（单位亿元）。

    每个交易日只有在所有参与标的都有有效 K 线时才计入，避免缺数据的日期跳变。
    """
    usable = {code: bars for code, bars in bars_by_code.items() if weights.get(code) and bars}
    if not usable:
        return []
    needed = len(usable)
    buckets: dict[str, dict[str, Any]] = {}
    for code, bars in usable.items():
        weight = float(weights[code])
        for bar in bars:
            date = str(bar.get("date") or "")
            if date < start:
                continue
            values = [to_float(bar.get(key)) for key in ("open", "high", "low", "close")]
            if any(value is None or value <= 0 for value in values):
                continue
            slot = buckets.setdefault(date, {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "count": 0})
            slot["open"] += float(values[0]) * weight
            slot["high"] += float(values[1]) * weight
            slot["low"] += float(values[2]) * weight
            slot["close"] += float(values[3]) * weight
            slot["count"] += 1
    result: list[dict[str, Any]] = []
    for date in sorted(buckets):
        slot = buckets[date]
        if slot["count"] != needed:
            continue
        result.append(
            {
                "date": date,
                "open": round(slot["open"] / 1e8, 4),
                "high": round(slot["high"] / 1e8, 4),
                "low": round(slot["low"] / 1e8, 4),
                "close": round(slot["close"] / 1e8, 4),
            }
        )
    return result


def apply_live_bar(
    history: Sequence[Mapping[str, Any]],
    quotes: Mapping[str, Mapping[str, Any]],
    weights: Mapping[str, float],
    trade_date: str,
) -> list[dict[str, Any]]:
    """行情已开盘但当日 K 线还没入库时，用实时报价补一根当日 K。

    只在所有参与标的都有有效的开/高/低/现价时补，避免半根 K 线。
    """
    if not history or not quotes or not weights:
        return list(history)
    if str(history[-1].get("date") or "") >= trade_date:
        return list(history)
    open_sum = high_sum = low_sum = close_sum = 0.0
    for code, weight in weights.items():
        quote = quotes.get(code) or {}
        price = to_float(quote.get("price"))
        open_price = to_float(quote.get("open"))
        high = to_float(quote.get("high"))
        low = to_float(quote.get("low"))
        if price is None or open_price is None or high is None or low is None:
            return list(history)
        if price <= 0 or open_price <= 0 or high <= 0 or low <= 0:
            return list(history)
        close_sum += price * weight
        open_sum += open_price * weight
        high_sum += high * weight
        low_sum += low * weight
    live = {
        "date": trade_date,
        "open": round(open_sum / 1e8, 4),
        "high": round(high_sum / 1e8, 4),
        "low": round(low_sum / 1e8, 4),
        "close": round(close_sum / 1e8, 4),
    }
    return [*history, live]


def resample_ohlc(bars: Sequence[Mapping[str, Any]], span: str) -> list[dict[str, Any]]:
    """日 K 重采样为周 K / 月 K：首开、最高、最低、末收。

    周 K 的日期取该周最后一个交易日，月 K 同样取当月最后一个交易日。
    """
    if span == "day":
        return [dict(bar) for bar in bars]
    grouped: list[dict[str, Any]] = []
    key: tuple[int, int] | str | None = None
    for bar in bars:
        date = str(bar.get("date") or "")
        values = [to_float(bar.get(key2)) for key2 in ("open", "high", "low", "close")]
        if not date or any(value is None for value in values):
            continue
        moment = datetime.strptime(date, "%Y-%m-%d")
        if span == "week":
            year, week, _weekday = moment.isocalendar()
            bucket: tuple[int, int] | str = (year, week)
        else:
            bucket = date[:7]
        volume = to_float(bar.get("volume")) or 0.0
        item = {
            "date": date,
            "open": float(values[0]),
            "high": float(values[1]),
            "low": float(values[2]),
            "close": float(values[3]),
            "volume": volume,
        }
        if bucket != key:
            grouped.append(item)
            key = bucket
            continue
        current = grouped[-1]
        current["date"] = date
        current["high"] = max(current["high"], item["high"])
        current["low"] = min(current["low"], item["low"])
        current["close"] = item["close"]
        current["volume"] += volume
    return grouped


def span_change(bars: Sequence[Mapping[str, Any]]) -> float | None:
    """最后一根相对前一根的变化（用收盘价）。"""
    if len(bars) < 2:
        return None
    return pct_change(to_float(bars[-1].get("close")), to_float(bars[-2].get("close")))


def align_flow_date(date: str, bar_dates: Sequence[str]) -> str | None:
    """把一个报告期日期对齐到不晚于它的最近 K 线日期。"""
    aligned: str | None = None
    for bar_date in bar_dates:
        if bar_date <= date:
            aligned = bar_date
        else:
            break
    return aligned


def place_flows(flows: Sequence[Mapping[str, Any]], bar_dates: Sequence[str]) -> list[dict[str, Any]]:
    """把报告期申赎标记放到 K 线日期上（同日只放一个）。"""
    placed: list[dict[str, Any]] = []
    used: set[str] = set()
    for flow in flows:
        source = str(flow.get("report_date") or flow.get("date") or "")
        aligned = align_flow_date(source, bar_dates)
        if not aligned or aligned in used:
            continue
        used.add(aligned)
        item = dict(flow)
        item["date"] = aligned
        placed.append(item)
    return placed


# ---------------------------------------------------------------- 日线指标


def bar_changes(bars: Sequence[Mapping[str, Any]], days: int) -> float | None:
    """最近 ``days`` 个交易日涨跌幅。"""
    if len(bars) <= days:
        return None
    return pct_change(to_float(bars[-1].get("close")), to_float(bars[-days - 1].get("close")))


def moving_average(bars: Sequence[Mapping[str, Any]], window: int) -> list[dict[str, Any]]:
    """收盘价简单移动平均序列（跳过无效收盘，窗口按有效样本计）。"""
    if window <= 0:
        raise ValueError("window 必须为正整数")
    valid: list[tuple[str, float]] = []
    for bar in bars:
        close = to_float(bar.get("close"))
        date = str(bar.get("date") or "")
        if close is None or close <= 0 or not date:
            continue
        valid.append((date, close))
    result: list[dict[str, Any]] = []
    running = 0.0
    for index, (date, close) in enumerate(valid):
        running += close
        if index >= window:
            running -= valid[index - window][1]
        if index >= window - 1:
            result.append({"date": date, "value": round(running / window, 4)})
    return result


def year_location(bars: Sequence[Mapping[str, Any]]) -> float | None:
    """收盘价在最近 250 根 K 线高低区间中的位置（0~1）。"""
    window = bars[-250:]
    if not window:
        return None
    current = to_float(window[-1].get("close"))
    highs = [to_float(bar.get("high")) for bar in window]
    lows = [to_float(bar.get("low")) for bar in window]
    clean_highs = [value for value in highs if value is not None]
    clean_lows = [value for value in lows if value is not None]
    if current is None or not clean_highs or not clean_lows:
        return None
    high = max(clean_highs)
    low = min(clean_lows)
    if high <= low:
        return 0.5
    return clip((current - low) / (high - low))


def market_stats(bars: Sequence[Mapping[str, Any]], span: str, trade_date: str) -> dict[str, Any]:
    """行情页区间统计：涨跌幅/高低/区间位置/回撤。"""
    if not bars:
        return {
            "change": None,
            "change_label": "近0根",
            "high": None,
            "low": None,
            "position": None,
            "drawdown": None,
        }
    closes = [to_float(bar.get("close")) for bar in bars if to_float(bar.get("close")) is not None]
    highs = [to_float(bar.get("high")) for bar in bars if to_float(bar.get("high")) is not None]
    lows = [to_float(bar.get("low")) for bar in bars if to_float(bar.get("low")) is not None]
    last_close = closes[-1] if closes else None
    high = max(highs) if highs else None
    low = min(lows) if lows else None
    change_label = {"day": "近%d日", "week": "近%d周", "month": "近%d月"}[span] % len(bars)
    change = pct_change(last_close, closes[0]) if len(closes) > 1 else None
    position = None
    if last_close is not None and high is not None and low is not None and high > low:
        position = clip((last_close - low) / (high - low))
    drawdown = pct_change(last_close, high) if last_close is not None and high else None
    return {
        "change": change,
        "change_label": change_label,
        "high": high,
        "low": low,
        "position": position,
        "drawdown": drawdown,
    }


# ---------------------------------------------------------------- 热点雷达

STATE_LABEL = {
    "hot": "强势",
    "warming": "升温",
    "crowded": "高位拥挤",
    "cooling": "高位降温",
    "fading": "退潮",
    "neutral": "盘整",
    "unavailable": "数据不足",
}

STATE_ORDER: tuple[str, ...] = ("hot", "warming", "crowded", "cooling", "fading", "neutral", "unavailable")


def heat_score(row: Mapping[str, Any]) -> int:
    """热度分（0~100）：相对强度 40 + 5 日动量 20 + MA 趋势 20 + 量能 15 + 新高 5。"""
    relative = clip(((to_float(row.get("relative_20d")) or 0) + 0.15) / 0.30)
    momentum = clip(((to_float(row.get("ret_5d")) or 0) + 0.10) / 0.20)
    trend = (0.5 if row.get("close_above_ma20") else 0.0) + (0.5 if row.get("ma20_above_ma60") else 0.0)
    volume_ratio = to_float(row.get("volume_ratio_5d"))
    volume = clip((((volume_ratio if volume_ratio is not None else 0.8) - 0.8) / 1.2))
    breakout = 1.0 if row.get("breakout_20d") else 0.0
    score = 40 * relative + 20 * momentum + 20 * trend + 15 * volume + 5 * breakout
    if row.get("relative_20d") is None:
        score = min(score, 75.0)
    return int(round(clip(score, 0, 100)))


def hot_state(row: Mapping[str, Any]) -> str:
    """状态标签：由年内位置/动量/相对强度/量能确定，规则写进 method。"""
    location = to_float(row.get("year_loc"))
    ret5 = to_float(row.get("ret_5d")) or 0.0
    ret20 = to_float(row.get("ret_20d")) or 0.0
    relative = to_float(row.get("relative_20d")) or 0.0
    vs_ma20 = to_float(row.get("vs_ma20")) or 0.0
    volume_ratio = to_float(row.get("volume_ratio_5d")) or 0.0
    if location is not None and location >= 0.85 and (vs_ma20 >= 0.04 or ret20 >= 0.12):
        return "crowded"
    if ret20 >= 0.05 and ret5 < 0:
        return "cooling"
    if relative > 0.03 and ret5 > 0 and row.get("close_above_ma20"):
        return "hot"
    if ret5 > 0 and (relative > 0 or volume_ratio >= 1.2):
        return "warming"
    if relative < -0.02 and ret5 < 0:
        return "fading"
    return "neutral"


def spark_series(bars: Sequence[Mapping[str, Any]], length: int = 20) -> list[float]:
    """20 日 sparkline：收盘价归一化到 1.0（样本不足返回空）。"""
    closes = [to_float(bar.get("close")) for bar in bars[-length:]]
    clean = [value for value in closes if value is not None and value > 0]
    if len(clean) < min(length, 5) or not clean:
        return []
    base = clean[0]
    return [round(value / base, 4) for value in clean]


def analyze_hotspot_row(
    entity: Mapping[str, Any],
    bars: Sequence[Mapping[str, Any]],
    benchmark_ret20: float | None,
    quote: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """单只代表 ETF 的热点行（含热度/状态/证据句/spark）。"""
    code = str(entity.get("code") or "")
    quote = quote or {}
    base = {
        "rank": 0,
        "code": code,
        "name": str(quote.get("name") or entity.get("name") or code),
        "direction": str(entity.get("direction") or ""),
        "group": str(entity.get("group") or ""),
        "price": to_float(quote.get("price")),
        "change_pct": to_float(quote.get("change_pct")),
        "data_date": str(bars[-1].get("date")) if bars else None,
        "ret_1d": None,
        "ret_5d": None,
        "ret_20d": None,
        "relative_20d": None,
        "ma20": None,
        "ma60": None,
        "vs_ma20": None,
        "close_above_ma20": False,
        "ma20_above_ma60": False,
        "volume_ratio_5d": None,
        "breakout_20d": False,
        "year_loc": None,
        "heat": 0,
        "state": "unavailable",
        "state_label": STATE_LABEL["unavailable"],
        "rank_change_5d": None,
        "evidence": ["本地日线不足 25 根"],
        "spark": [],
    }
    if len(bars) < config.HOTSPOT_MIN_BARS:
        return base

    closes = [float(value) for value in (to_float(bar.get("close")) for bar in bars) if value is not None and value > 0]
    volumes = [to_float(bar.get("volume")) or 0.0 for bar in bars]
    if len(closes) < config.HOTSPOT_MIN_BARS:
        return base
    ret1 = bar_changes(bars, 1)
    ret5 = bar_changes(bars, 5)
    ret20 = bar_changes(bars, 20)
    ma20 = mean(closes[-20:])
    ma60 = mean(closes[-60:]) if len(closes) >= 60 else None
    volume5 = mean(volumes[-5:])
    volume_base = mean(volumes[-25:-5]) if len(volumes) >= 25 else None
    volume_ratio = volume5 / volume_base if volume5 and volume_base else None
    breakout = False
    if len(bars) >= 21:
        previous_high = max(
            value for value in (to_float(bar.get("high")) for bar in bars[-21:-1]) if value is not None
        )
        breakout = closes[-1] >= previous_high
    relative = ret20 - benchmark_ret20 if ret20 is not None and benchmark_ret20 is not None else None
    row = {
        **base,
        "price": base["price"] if base["price"] is not None else closes[-1],
        "change_pct": base["change_pct"] if base["change_pct"] is not None else ret1,
        "ret_1d": ret1,
        "ret_5d": ret5,
        "ret_20d": ret20,
        "relative_20d": relative,
        "ma20": ma20,
        "ma60": ma60,
        "vs_ma20": pct_change(closes[-1], ma20),
        "close_above_ma20": bool(ma20 is not None and closes[-1] > ma20),
        "ma20_above_ma60": bool(ma20 is not None and ma60 is not None and ma20 > ma60),
        "volume_ratio_5d": volume_ratio,
        "breakout_20d": breakout,
        "year_loc": year_location(bars),
        "spark": spark_series(bars),
    }
    state = hot_state(row)
    row["state"] = state
    row["state_label"] = STATE_LABEL[state]
    row["heat"] = heat_score(row)
    evidence: list[str] = []
    if ret20 is not None:
        evidence.append(f"20日 {ret20:+.1%}")
    if relative is not None:
        evidence.append(f"相对沪深300 {relative:+.1%}")
    if volume_ratio is not None:
        evidence.append(f"5日量能 {volume_ratio:.2f}x")
    if breakout:
        evidence.append("站上20日高点")
    if row["year_loc"] is not None:
        evidence.append(f"年内位置 {row['year_loc']:.0%}")
    row["evidence"] = evidence
    return row


def rank_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按热度（热度相同看 20 日动量）降序，写入 rank。"""
    ordered = sorted(
        rows,
        key=lambda row: (row.get("heat") or 0, row.get("ret_20d") if row.get("ret_20d") is not None else -9),
        reverse=True,
    )
    for index, row in enumerate(ordered, 1):
        row["rank"] = index
    return ordered


def count_states(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """各状态数量（缺省状态补 0，保证前端直接读取）。"""
    counts = {state: 0 for state in STATE_ORDER}
    for row in rows:
        state = str(row.get("state") or "unavailable")
        if state not in counts:
            counts[state] = 0
        counts[state] += 1
    return counts


def market_state(rows: Sequence[Mapping[str, Any]], counts: Mapping[str, int]) -> tuple[str, str]:
    """热点的整体状态与一句话 headline（确定性规则）。"""
    usable = [row for row in rows if row.get("state") != "unavailable"]
    strong = counts.get("hot", 0) + counts.get("warming", 0)
    crowded = counts.get("crowded", 0)
    fading = counts.get("fading", 0)
    total = len(usable)
    if crowded >= max(3, total * 0.25):
        return "高位分化", "热点仍多，但高位拥挤占比上升；优先辨认承接，而不是追逐涨幅。"
    if strong >= max(4, total * 0.35):
        return "热点扩散", "多个方向同时升温；观察相对强度和量能能否连续，而不是只看单日涨幅。"
    if fading >= max(3, total * 0.25):
        return "退潮占优", "弱势方向增多；反弹需要先看到相对沪深300 重新转正。"
    return "结构性轮动", "热点集中在少数方向；没有足够证据说明全面扩散。"


def breadth_from_rows(
    rows: Sequence[Mapping[str, Any]],
    quotes: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """宽度：MA20/MA60 上方数量、涨跌家数、中位 20 日收益。"""
    usable = [row for row in rows if row.get("state") != "unavailable"]
    above_ma20 = sum(1 for row in usable if row.get("close_above_ma20"))
    above_ma60 = 0
    for row in usable:
        ma60 = to_float(row.get("ma60"))
        price = to_float(row.get("price"))
        if ma60 is not None and price is not None and price > ma60:
            above_ma60 += 1
    advancers = 0
    decliners = 0
    for row in usable:
        code = str(row.get("code") or "")
        quote = (quotes or {}).get(code) or {}
        change = to_float(quote.get("change_pct"))
        if change is None:
            change = to_float(row.get("ret_1d"))
        if change is None:
            continue
        if change > 0:
            advancers += 1
        elif change < 0:
            decliners += 1
    return {
        "above_ma20": above_ma20,
        "above_ma60": above_ma60,
        "total": len(rows),
        "advancers": advancers,
        "decliners": decliners,
        "median_ret_20d": median([row.get("ret_20d") for row in usable]),
    }


def rank_changes(
    rows: Sequence[Mapping[str, Any]],
    history: Sequence[Mapping[str, Any]],
    as_of: str | None,
    lookback: int = config.HOTSPOT_RANK_LOOKBACK,
) -> dict[str, int | None]:
    """相对 ``lookback`` 个交易日的排名变化：旧排名 - 新排名（正数=排名上升）。"""
    snapshots = sorted((snapshot for snapshot in history
                        if as_of and snapshot.get("date") and snapshot["date"] < as_of),
                       key=lambda snapshot: snapshot["date"])
    if len(snapshots) < lookback:
        return {str(row.get("code")): None for row in rows}
    reference = snapshots[-lookback]
    previous_ranks: dict[str, int] = {}
    for item in reference.get("rows") or []:
        try:
            previous_ranks[str(item.get("code"))] = int(item.get("rank"))
        except (TypeError, ValueError):
            continue
    result: dict[str, int | None] = {}
    for row in rows:
        code = str(row.get("code"))
        previous = previous_ranks.get(code)
        result[code] = (previous - int(row.get("rank"))) if previous is not None and row.get("rank") else None
    return result


def stock_rows_for_history(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """历史快照只需要排名相关字段，避免文件膨胀。"""
    return [
        {
            "code": row.get("code"),
            "rank": row.get("rank"),
            "heat": row.get("heat"),
            "state": row.get("state"),
            "ret_20d": row.get("ret_20d"),
        }
        for row in rows
    ]


def select_largest_csi300(
    quotes: Mapping[str, Mapping[str, Any]],
    fallback: str | None = None,
) -> str:
    """按总市值选出最大的沪深300 ETF 作为基准。"""
    candidates = [
        code
        for code in config.CSI300_CANDIDATES
        if (to_float((quotes.get(code) or {}).get("market_cap")) or 0) > 0
    ]
    if candidates:
        return max(candidates, key=lambda code: (to_float(quotes[code].get("market_cap")) or 0.0, code))
    if fallback:
        return fallback
    return config.CSI300_CANDIDATES[0]


# ---------------------------------------------------------------- 基金份额摘要


def share_snapshots(days: Mapping[str, Mapping[str, Mapping[str, Any]]], code: str) -> list[dict[str, Any]]:
    """从国家队日数据里提取某只 ETF 的份额快照（按日期升序）。"""
    snapshots: list[dict[str, Any]] = []
    for date in sorted(days):
        row = (days.get(date) or {}).get(code) or {}
        shares = to_float(row.get("shares"))
        if shares is None or shares <= 0:
            continue
        snapshots.append({"date": str(date), "shares": shares, "price": to_float(row.get("price"))})
    return snapshots


def flow_summary(
    snapshots: Sequence[Mapping[str, Any]],
    open_period: Mapping[str, Any] | None = None,
    periods: Sequence[Mapping[str, Any]] = (),
    period_redemptions: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """最大沪深300 ETF 的份额申赎摘要（金额单位元）。"""
    ordered = sorted(snapshots, key=lambda row: str(row.get("date") or ""))
    latest = ordered[-1] if ordered else None
    previous = ordered[-2] if len(ordered) > 1 else None
    if latest is None:
        return {
            "state_label": "尚无份额快照",
            "latest": None,
            "daily_share_change": None,
            "daily_flow_amount": None,
            "five_flow_amount": None,
            "twenty_flow_amount": None,
            "snapshots": [],
            "open_period": None,
            "large_redemptions": [],
        }

    def window(count: int) -> tuple[float | None, float | None]:
        subset = ordered[-count:]
        if len(subset) < count:
            return None, None
        delta = float(subset[-1]["shares"]) - float(subset[0]["shares"])
        prices = [to_float(row.get("price")) for row in subset]
        clean = [price for price in prices if price is not None and price > 0]
        amount = delta * mean(clean) if clean else None
        return delta, amount

    daily_delta, _daily_amount_ignored = window(2)
    five_delta, five_amount = window(5)
    twenty_delta, twenty_amount = window(20)
    # 当日净申赎金额按「最新收盘价」折算（契约/README 口径），不用两日均价
    latest_price = to_float(latest.get("price"))
    daily_amount = daily_delta * latest_price if daily_delta is not None and latest_price else None
    daily_pct = pct_change(latest.get("shares"), previous.get("shares")) if previous else None
    five_pct = pct_change(latest.get("shares"), ordered[-5].get("shares")) if len(ordered) >= 5 else None
    if previous is None:
        state_label = "建立份额基线"
    elif (daily_pct or 0) >= 0.002 or (five_pct or 0) >= 0.01:
        state_label = "净申购 / 宽基承接"
    elif (daily_pct or 0) <= -0.002 or (five_pct or 0) <= -0.01:
        state_label = "净赎回"
    else:
        state_label = "份额基本平衡"
    redemptions = (
        [dict(row) for row in period_redemptions]
        if period_redemptions is not None
        else [
            {
                "date": str(row.get("date")),
                "net": (to_float(row.get("net")) or 0.0) * (to_float(latest.get("price")) or 0.0),
                "net_pct": to_float(row.get("net_pct")),
                "report_date": str(row.get("date")),
                "label": quarter_label(str(row.get("date"))),
                "large_redemption": True,
            }
            for row in periods
            if row.get("large_redemption")
        ]
    )
    return {
        "state_label": state_label,
        "latest": {"date": str(latest.get("date")), "shares": float(latest["shares"]), "price": to_float(latest.get("price"))},
        "daily_share_change": daily_delta,
        "daily_flow_amount": daily_amount,
        "five_flow_amount": five_amount,
        "twenty_flow_amount": twenty_amount,
        "snapshots": [
            {"date": str(row.get("date")), "shares": float(row["shares"]), "price": to_float(row.get("price"))}
            for row in ordered[-60:]
        ],
        "open_period": dict(open_period) if open_period else None,
        "large_redemptions": redemptions,
    }


def agent_reports(
    rows: Sequence[Mapping[str, Any]],
    counts: Mapping[str, int],
    flow: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """四路「证据研究员」：趋势 / 扩散 / 份额 / 风险，全部由规则生成。"""
    usable = [row for row in rows if row.get("state") != "unavailable"]
    total = len(usable)
    strong = counts.get("hot", 0) + counts.get("warming", 0)
    crowded = counts.get("crowded", 0)
    fading = counts.get("fading", 0)
    above20 = sum(1 for row in usable if row.get("close_above_ma20"))
    ma20_above_ma60 = sum(1 for row in usable if row.get("ma20_above_ma60"))
    top = usable[:3]
    top_text = "、".join(f"{row.get('direction')} {to_float(row.get('ret_20d')) or 0:+.1%}" for row in top) or "没有可用样本"
    flow_label = str((flow or {}).get("state_label") or "尚无份额快照")
    latest = (flow or {}).get("latest") or {}
    if latest.get("date"):
        flow_text = f"{flow_label}；最新 {latest['date']}，份额 {float(latest['shares']) / 1e8:.2f} 亿份"
    else:
        flow_text = flow_label
    return [
        {
            "id": "trend",
            "name": "趋势研究员",
            "stance": "偏强" if strong > max(3, total * 0.25) else "中性",
            "summary": top_text,
            "facts": [
                f"{strong}/{total} 个方向处于强势或升温",
                f"{above20}/{total} 个方向站上 MA20",
                "以代表 ETF 日线计算，不覆盖全部 5000 只个股",
            ],
        },
        {
            "id": "breadth",
            "name": "扩散研究员",
            "stance": "扩散" if ma20_above_ma60 > total * 0.6 else "分化",
            "summary": f"{ma20_above_ma60}/{total} 个方向的 MA20 高于 MA60。",
            "facts": [f"上涨 {strong} 个，退潮 {fading} 个", "代表 ETF 等权计数，不等于全市场涨跌家数"],
        },
        {
            "id": "flow",
            "name": "份额研究员",
            "stance": flow_label,
            "summary": flow_text,
            "facts": ["份额增加 = 净申购代理，减少 = 净赎回代理", "主体不可识别，不把变化直接归因给国家队"],
        },
        {
            "id": "risk",
            "name": "风险研究员",
            "stance": "克制" if crowded else "监控",
            "summary": f"{crowded} 个方向处于高位拥挤。",
            "facts": [
                f"{above20}/{total} 站上 MA20，趋势与拥挤度要分开展示",
                "新闻、政策和成分股基本面尚未进入本轮规则",
            ],
        },
    ]
