"""国家队篮子计算单测：净申赎、跨日缺口、趋势结构、重采样、报告期申赎。"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from etfmon import analytics, config


def weekdays(start: date, count: int) -> list[str]:
    """从 start 开始生成 count 个工作日字符串。"""
    result: list[str] = []
    cursor = start
    while len(result) < count:
        if cursor.weekday() < 5:
            result.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return result


def bars_from_closes(closes: list[float], start: date = date(2026, 1, 5)) -> list[dict]:
    dates = weekdays(start, len(closes))
    return [
        {"date": dates[index], "open": close, "high": close, "low": close, "close": close, "volume": 100.0}
        for index, close in enumerate(closes)
    ]


class DayTotalsTest(unittest.TestCase):
    def test_net_flow(self) -> None:
        current = {"a": {"shares": 110.0, "price": 4.0}, "b": {"shares": 90.0, "price": 2.0}}
        previous = {"a": {"shares": 100.0, "price": 3.9}, "b": {"shares": 100.0, "price": 2.1}}
        totals = analytics.day_totals(["a", "b"], current, previous)
        self.assertAlmostEqual(totals["amount"], (10 * 4.0) + (-10 * 2.0))
        self.assertAlmostEqual(totals["share_delta"], 0.0)
        self.assertEqual(totals["inflow"], 1)
        self.assertEqual(totals["outflow"], 1)
        self.assertAlmostEqual(totals["total_shares"], 200.0)
        self.assertAlmostEqual(totals["total_value"], 110 * 4.0 + 90 * 2.0)

    def test_first_day_has_no_flow(self) -> None:
        totals = analytics.day_totals(["a"], {"a": {"shares": 100.0, "price": 4.0}}, None)
        self.assertIsNone(totals["amount"])
        self.assertIsNone(totals["share_delta"])
        self.assertAlmostEqual(totals["total_shares"], 100.0)

    def test_missing_price_keeps_share_delta(self) -> None:
        totals = analytics.day_totals(
            ["a", "b"],
            {"a": {"shares": 110.0, "price": None}, "b": {"shares": 100.0, "price": 2.0}},
            {"a": {"shares": 100.0}, "b": {"shares": 90.0}},
        )
        # a 没有价格，不计入金额，但份额差在 b 上仍有效
        self.assertAlmostEqual(totals["amount"], 10 * 2.0)
        self.assertAlmostEqual(totals["share_delta"], 10 + 10)


class HistoryAndCumulativeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.days = weekdays(date(2026, 10, 27), 9)  # 10-27 ... 11-06
        self.history_days = {
            self.days[0]: {"a": {"shares": 100.0, "price": 4.0}},
            self.days[6]: {"a": {"shares": 130.0, "price": 4.0}},  # 11-04，跨 6 个交易日
            self.days[7]: {"a": {"shares": 110.0, "price": 4.0}},
            self.days[8]: {"a": {"shares": 120.0, "price": 4.0}},
        }
        self.calendar = self.days

    def test_gap_days(self) -> None:
        history = analytics.build_history(self.history_days, ["a"], self.calendar)
        self.assertEqual(len(history), 4)
        self.assertEqual([row["gap_days"] for row in history], [1, 6, 1, 1])
        self.assertEqual(history[1]["previous"], self.days[0])
        self.assertEqual(history[0]["amount"], None)

    def test_gap_days_weekday_fallback(self) -> None:
        # 没有交易日历时，按工作日估算：周五 11-06 → 周一 11-09 = 1 个交易日
        gap = analytics.trading_day_gap("2026-11-06", "2026-11-09", None)
        self.assertEqual(gap, 1)
        self.assertEqual(analytics.trading_day_gap("2026-10-27", "2026-11-04", None), 6)

    def test_cumulative_excludes_gaps(self) -> None:
        history = analytics.build_history(self.history_days, ["a"], self.calendar)
        cumulative = analytics.cumulative_stats(history)
        self.assertEqual(cumulative["days"], 2)  # 11-05、11-06 两个相邻日
        self.assertEqual(cumulative["excluded_days"], 1)  # 11-04 跨日
        self.assertAlmostEqual(cumulative["amount"], (-20 + 10) * 4.0)
        self.assertEqual(cumulative["since"], self.days[7])
        self.assertEqual(cumulative["inflow_days"], 1)
        self.assertEqual(cumulative["outflow_days"], 1)
        self.assertEqual(cumulative["best"]["date"], self.days[8])
        self.assertEqual(cumulative["worst"]["date"], self.days[7])

    def test_recent_flow_only_adjacent(self) -> None:
        history = analytics.build_history(self.history_days, ["a"], self.calendar)
        amount, days = analytics.recent_flow(history, 5)
        self.assertEqual(days, 2)
        self.assertAlmostEqual(amount, (-20 + 10) * 4.0)

    def test_empty_history(self) -> None:
        cumulative = analytics.cumulative_stats([])
        self.assertEqual(cumulative["amount"], 0.0)
        self.assertEqual(cumulative["days"], 0)
        self.assertIsNone(cumulative["since"])
        self.assertIsNone(cumulative["best"])
        self.assertEqual(analytics.recent_flow([], 5), (None, 0))


class BasketRowsTest(unittest.TestCase):
    def test_weights_and_missing(self) -> None:
        codes = ["sh510300", "sh510050"]
        quotes = {
            "sh510300": {"price": 4.0, "shares": 100.0, "change_pct": 0.01, "name": "A"},
            "sh510050": {"price": None, "shares": None, "change_pct": None, "name": "B"},
        }
        latest = {
            "sh510300": {"shares": 100.0, "price": 4.0},
            "sh510050": {"shares": 50.0, "price": 2.0},
        }
        previous = {"sh510300": {"shares": 90.0}, "sh510050": {"shares": 50.0}}
        rows = analytics.basket_rows(codes, quotes, latest, previous)
        self.assertAlmostEqual(rows[0]["weight"], 400 / 500)
        self.assertAlmostEqual(rows[0]["share_delta"], 10.0)
        self.assertAlmostEqual(rows[0]["amount"], 40.0)
        self.assertTrue(rows[1]["missing_quote"])
        # 行情缺失时退回日数据的价格与份额
        self.assertAlmostEqual(rows[1]["price"], 2.0)
        self.assertAlmostEqual(rows[1]["shares"], 50.0)
        self.assertAlmostEqual(rows[1]["weight"], 100 / 500)

    def test_market_pnl(self) -> None:
        quotes = {
            "sh510300": {"price": 4.1, "pre_close": 4.0, "shares": 100.0},
            "sh510050": {"price": 2.0, "pre_close": 2.1, "shares": 50.0},
        }
        pnl = analytics.market_pnl(quotes, ["sh510300", "sh510050"])
        self.assertAlmostEqual(pnl, 0.1 * 100 + (-0.1) * 50)


class LiveFlowTest(unittest.TestCase):
    def test_partial_live_flow(self) -> None:
        quotes = {
            "sh510300": {"shares": 110.0, "price": 4.0, "quote_time": "2026-11-09T10:00:00+08:00"},
            "sh510050": {"shares": 100.0, "price": 2.0, "quote_time": "2026-11-09T10:00:05+08:00"},
        }
        latest = {"sh510300": {"shares": 100.0}, "sh510050": {"shares": 100.0}}
        live = analytics.live_flow(["sh510300", "sh510050"], quotes, "2026-11-06", latest, "2026-11-09")
        assert live is not None
        self.assertTrue(live["partial"])
        self.assertAlmostEqual(live["amount"], 10 * 4.0)
        self.assertAlmostEqual(live["share_delta"], 10.0)
        self.assertEqual(live["time"], "10:00:05")
        self.assertEqual(live["previous_day"], "2026-11-06")

    def test_no_live_flow_after_close(self) -> None:
        self.assertIsNone(analytics.live_flow([], {}, "2026-11-06", {}, "2026-11-06"))

    def test_intraday_series(self) -> None:
        intraday = {"2026-11-09": [{"time": "09:32:00", "amount": 100.0, "share_delta": 10.0}]}
        series = analytics.intraday_series(intraday, "2026-11-09")
        self.assertEqual(series, [{"time": "09:32:00", "amount": 100.0, "share_delta": 10.0}])


class TrendTest(unittest.TestCase):
    def test_insufficient_bars(self) -> None:
        trend = analytics.trend_structure(bars_from_closes([1.0] * 10))
        self.assertEqual(trend["state"], "range")
        self.assertEqual(trend["points"], [])
        self.assertFalse(trend["add"])

    def test_up_right_confirmed_bottom(self) -> None:
        closes = [1.0 + 0.01 * index for index in range(60)]
        trend = analytics.trend_structure(bars_from_closes(closes))
        self.assertEqual(trend["state"], "up_right")
        self.assertFalse(trend["add"])  # v2.5: context does not authorize entry
        self.assertTrue(trend["points"][0]["confirmed"])
        self.assertEqual(trend["points"][0]["kind"], "low")
        self.assertIs(trend["points"][-1]["confirmed"], False)

    def test_down_right_confirmed_top(self) -> None:
        closes = [1.0 + 0.02 * index for index in range(30)] + [1.58 - 0.02 * index for index in range(1, 20)]
        trend = analytics.trend_structure(bars_from_closes(closes))
        self.assertEqual(trend["state"], "down_right")
        self.assertFalse(trend["add"])
        self.assertEqual(trend["points"][-1]["kind"], "low")

    def test_down_left_unconfirmed_decline(self) -> None:
        closes = [1.0, 1.005, 1.01] + [1.0] * 4 + [0.95, 0.945, 0.94] + [0.941 + 0.001 * (i % 3) for i in range(15)]
        trend = analytics.trend_structure(bars_from_closes(closes))
        self.assertEqual(trend["state"], "down_left")
        self.assertEqual(trend["segments"][-1]["side"], "left")
        self.assertIn("左侧", trend["segments"][-1]["label"])
        self.assertFalse(trend["add"])

    def test_range(self) -> None:
        closes = [1.0 + (0.01 if index % 2 else -0.01) for index in range(40)]
        trend = analytics.trend_structure(bars_from_closes(closes))
        self.assertEqual(trend["state"], "range")

    def test_segments_semantics(self) -> None:
        closes = [1.0 + 0.02 * index for index in range(30)] + [1.58 - 0.02 * index for index in range(1, 20)]
        trend = analytics.trend_structure(bars_from_closes(closes))
        # 首段与第一个转折点同日会被跳过，因此第一段是「从低点出发」的右侧上涨
        first = trend["segments"][0]
        self.assertEqual(first["side"], "right")
        self.assertEqual(first["label"], "右侧上涨")
        labels = [segment["label"] for segment in trend["segments"]]
        self.assertIn("右侧上涨", labels)
        self.assertIn("右侧下跌", labels)
        self.assertTrue(all(segment["from"] < segment["to"] for segment in trend["segments"]))


class ResampleTest(unittest.TestCase):
    def test_week_bucket(self) -> None:
        bars = bars_from_closes([1.0, 1.1, 0.9, 1.2, 1.3, 1.1, 1.0, 1.4, 1.2, 1.1])
        weekly = analytics.resample_ohlc(bars, "week")
        self.assertEqual(len(weekly), 2)
        first = weekly[0]
        self.assertAlmostEqual(first["open"], 1.0)
        self.assertAlmostEqual(first["high"], 1.3)
        self.assertAlmostEqual(first["low"], 0.9)
        self.assertAlmostEqual(first["close"], 1.3)
        self.assertEqual(first["date"], bars[4]["date"])

    def test_month_bucket(self) -> None:
        bars = [
            {"date": "2026-01-05", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 1},
            {"date": "2026-01-30", "open": 2, "high": 3, "low": 1.5, "close": 3, "volume": 1},
            {"date": "2026-02-02", "open": 3, "high": 4, "low": 2.5, "close": 4, "volume": 1},
        ]
        monthly = analytics.resample_ohlc(bars, "month")
        self.assertEqual(len(monthly), 2)
        self.assertAlmostEqual(monthly[0]["open"], 1)
        self.assertAlmostEqual(monthly[0]["close"], 3)
        self.assertEqual(monthly[0]["date"], "2026-01-30")
        self.assertEqual(monthly[1]["date"], "2026-02-02")

    def test_day_passthrough(self) -> None:
        bars = bars_from_closes([1.0, 2.0])
        self.assertEqual(analytics.resample_ohlc(bars, "day"), bars)


class CombineOhlcTest(unittest.TestCase):
    def test_share_weighted_units(self) -> None:
        bars = {
            "a": [{"date": "2026-01-05", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}],
            "b": [{"date": "2026-01-05", "open": 2, "high": 2, "low": 2, "close": 2, "volume": 1}],
        }
        weights = {"a": 1e8, "b": 2e8}
        combined = analytics.combine_ohlc(bars, weights, "2026-01-01")
        self.assertEqual(len(combined), 1)
        self.assertAlmostEqual(combined[0]["close"], 1 + 2 * 2)  # 亿元

    def test_skips_incomplete_dates(self) -> None:
        bars = {
            "a": [
                {"date": "2026-01-05", "open": 1, "high": 1, "low": 1, "close": 1},
                {"date": "2026-01-06", "open": 1, "high": 1, "low": 1, "close": 1},
            ],
            "b": [{"date": "2026-01-05", "open": 1, "high": 1, "low": 1, "close": 1}],
        }
        combined = analytics.combine_ohlc(bars, {"a": 1e8, "b": 1e8}, "2026-01-01")
        self.assertEqual([row["date"] for row in combined], ["2026-01-05"])

    def test_apply_live_bar(self) -> None:
        history = [{"date": "2026-11-06", "open": 1, "high": 1, "low": 1, "close": 1}]
        quotes = {"a": {"price": 2, "open": 1.5, "high": 2, "low": 1.5}}
        bars = analytics.apply_live_bar(history, quotes, {"a": 1e8}, "2026-11-09")
        self.assertEqual(bars[-1]["date"], "2026-11-09")
        self.assertAlmostEqual(bars[-1]["close"], 2.0)
        # 已有当日 K 线时不重复追加
        same = analytics.apply_live_bar(bars, quotes, {"a": 1e8}, "2026-11-09")
        self.assertEqual(len(same), len(bars))

    def test_moving_average(self) -> None:
        bars = bars_from_closes([1.0, 2.0, 3.0, 4.0])
        ma2 = analytics.moving_average(bars, 2)
        self.assertEqual(len(ma2), 3)
        self.assertAlmostEqual(ma2[0]["value"], 1.5)
        self.assertAlmostEqual(ma2[-1]["value"], 3.5)


class FlowPeriodsTest(unittest.TestCase):
    def test_money_conversion_uses_report_close(self) -> None:
        bars = {
            "a": bars_from_closes([2.0] * 5 + [2.5] * 3),
            "b": bars_from_closes([1.0] * 8),
        }
        periods = {
            "a": [{"date": "2026-01-09", "net": 10.0, "start_shares": 100.0}],
            "b": [{"date": "2026-01-09", "net": -4.0, "start_shares": 50.0}],
        }
        combined = analytics.combine_flow_periods(periods, bars)
        self.assertEqual(len(combined), 1)
        row = combined[0]
        self.assertAlmostEqual(row["share_net"], 6.0)
        # a: 10 × 2.0 = 20 亿元；b: -4 × 1.0 = -4 亿元
        self.assertAlmostEqual(row["net"], 16.0)
        self.assertEqual(row["funds"], 2)
        self.assertEqual(row["price_fallback_funds"], 0)
        self.assertEqual(row["label"], "Q1")

    def test_price_fallback_counted(self) -> None:
        bars = {"a": [{"date": "2026-03-31", "open": 3, "high": 3, "low": 3, "close": 3}]}
        periods = {"a": [{"date": "2025-12-31", "net": 1.0, "start_shares": 10.0}]}
        combined = analytics.combine_flow_periods(periods, bars)
        self.assertEqual(combined[0]["price_fallback_funds"], 1)
        self.assertAlmostEqual(combined[0]["net"], 3.0)

    def test_open_period_flow(self) -> None:
        periods = [{"date": "2026-09-30", "total_shares": 200.0}]
        flow = analytics.open_period_flow(periods, "2026-11-06", 210e8, 4.0)
        assert flow is not None
        self.assertTrue(flow["partial"])
        self.assertEqual(flow["label"], "11月至今")
        self.assertEqual(flow["base_date"], "2026-09-30")
        self.assertAlmostEqual(flow["net"], 10 * 4.0)
        self.assertAlmostEqual(flow["net_pct"], 10 / 200)

    def test_open_period_none_when_same_day(self) -> None:
        periods = [{"date": "2026-09-30", "total_shares": 200.0}]
        self.assertIsNone(analytics.open_period_flow(periods, "2026-09-30", 200e8, 4.0))


class MarketStatsTest(unittest.TestCase):
    def test_stats(self) -> None:
        bars = bars_from_closes([1.0, 2.0, 3.0])
        stats = analytics.market_stats(bars, "day", "2026-01-07")
        self.assertAlmostEqual(stats["change"], 2.0)
        self.assertAlmostEqual(stats["high"], 3.0)
        self.assertAlmostEqual(stats["low"], 1.0)
        self.assertAlmostEqual(stats["position"], 1.0)

    def test_empty(self) -> None:
        stats = analytics.market_stats([], "day", "2026-01-07")
        self.assertIsNone(stats["change"])


class QuarterLabelTest(unittest.TestCase):
    def test_labels(self) -> None:
        self.assertEqual(analytics.quarter_label("2026-03-31"), "Q1")
        self.assertEqual(analytics.quarter_label("2026-06-30"), "Q2")
        self.assertEqual(analytics.quarter_label("2026-09-30"), "Q3")
        self.assertEqual(analytics.quarter_label("2026-12-31"), "Q4")


class ConfigUniverseTest(unittest.TestCase):
    def test_universe_sizes(self) -> None:
        self.assertEqual(len(config.TEAM_CODES), 26)
        # 热点宇宙可扩容：不写死数量，但覆盖全部行业/主题代表（当前 54 只）
        self.assertGreaterEqual(len(config.HOTSPOT_CODES), 54)
        self.assertEqual(len(config.HOTSPOT_CODES), len(config.HOTSPOT_UNIVERSE))
        self.assertEqual(len(config.INDEX_CODES), 6)
        self.assertTrue(all(code.startswith(("sh", "sz")) for code in config.HOTSPOT_CODES))
        self.assertEqual(len(set(config.TEAM_CODES)), len(config.TEAM_CODES))

    def test_broad_not_in_hotspot_ranking(self) -> None:
        for code in config.BROAD_CODES:
            self.assertNotIn(code, config.HOTSPOT_CODES)

    def test_entity_lookup(self) -> None:
        self.assertEqual(config.ENTITIES["sh512480"].direction, "半导体")
        self.assertEqual(config.ENTITIES["sh510230"].direction, "金融")
        self.assertEqual(config.kind_of("sh510300"), "etf")
        self.assertEqual(config.kind_of("sh000300"), "stock")

    def test_kind_of_arbitrary_codes(self) -> None:
        self.assertEqual(config.kind_of("sh518880"), "etf")  # 黄金 ETF，不在 universe
        self.assertEqual(config.kind_of("sz159915"), "etf")
        self.assertEqual(config.kind_of("sz161725"), "etf")  # LOF
        self.assertEqual(config.kind_of("sh600519"), "stock")
        self.assertEqual(config.kind_of("sz000001"), "stock")
        self.assertEqual(config.kind_of("sz300750"), "stock")
        self.assertEqual(config.kind_of("sh688981"), "stock")


if __name__ == "__main__":
    unittest.main()
