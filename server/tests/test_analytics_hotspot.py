"""热点雷达计算单测：热度分、状态、spark、宽度、排名变化、份额摘要。"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from etfmon import analytics

ENTITY = {"code": "sh512480", "name": "半导体", "direction": "半导体", "group": "科技"}


def weekdays(start: date, count: int) -> list[str]:
    result: list[str] = []
    cursor = start
    while len(result) < count:
        if cursor.weekday() < 5:
            result.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return result


def bars_from_closes(closes: list[float], volumes: list[float] | None = None) -> list[dict]:
    dates = weekdays(date(2026, 1, 5), len(closes))
    volumes = volumes or [100.0] * len(closes)
    return [
        {"date": dates[index], "open": close, "high": close, "low": close, "close": close, "volume": volumes[index]}
        for index, close in enumerate(closes)
    ]


def uptrend_bars(count: int = 70, start: float = 1.0, step: float = 0.01) -> list[dict]:
    return bars_from_closes([start + step * index for index in range(count)])


class HeatScoreTest(unittest.TestCase):
    def test_bounds(self) -> None:
        strong = {
            "ret_20d": 0.20,
            "ret_5d": 0.10,
            "close_above_ma20": True,
            "ma20_above_ma60": True,
            "volume_ratio_5d": 2.5,
            "breakout_20d": True,
        }
        weak = {
            "ret_20d": -0.20,
            "ret_5d": -0.10,
            "close_above_ma20": False,
            "ma20_above_ma60": False,
            "volume_ratio_5d": 0.4,
            "breakout_20d": False,
        }
        self.assertGreater(analytics.heat_score(strong), 85)
        self.assertLess(analytics.heat_score(weak), 5)
        for row in (strong, weak, {}):
            score = analytics.heat_score(row)
            self.assertIsInstance(score, int)
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)

    def test_missing_own_momentum_caps_score(self) -> None:
        row = {
            "ret_20d": None,
            "ret_5d": 0.2,
            "close_above_ma20": True,
            "ma20_above_ma60": True,
            "volume_ratio_5d": 3.0,
            "breakout_20d": True,
        }
        self.assertLessEqual(analytics.heat_score(row), 75)


class HotStateTest(unittest.TestCase):
    def test_rules(self) -> None:
        self.assertEqual(analytics.hot_state({"year_loc": 0.9, "vs_ma20": 0.05}), "crowded")
        self.assertEqual(analytics.hot_state({"ret_20d": 0.06, "ret_5d": -0.01}), "cooling")
        self.assertEqual(
            analytics.hot_state(
                {"ret_20d": 0.05, "ret_5d": 0.01, "close_above_ma20": True}
            ),
            "hot",
        )
        self.assertEqual(analytics.hot_state({"ret_20d": 0.0, "ret_5d": 0.01, "volume_ratio_5d": 1.3}), "warming")
        self.assertEqual(analytics.hot_state({"ret_20d": -0.03, "ret_5d": -0.01}), "fading")
        self.assertEqual(analytics.hot_state({}), "neutral")

    def test_state_labels_complete(self) -> None:
        self.assertEqual(set(analytics.STATE_LABEL), set(analytics.STATE_ORDER))


class SparkTest(unittest.TestCase):
    def test_normalized(self) -> None:
        bars = bars_from_closes([2.0, 2.2, 1.8, 2.4])
        spark = analytics.spark_series(bars, length=4)
        self.assertEqual(spark[0], 1.0)
        self.assertAlmostEqual(spark[1], 1.1)
        self.assertAlmostEqual(spark[-1], 1.2)

    def test_insufficient(self) -> None:
        self.assertEqual(analytics.spark_series(bars_from_closes([1.0, 1.1]), length=20), [])


class AnalyzeRowTest(unittest.TestCase):
    def test_unavailable_when_short_history(self) -> None:
        row = analytics.analyze_hotspot_row(ENTITY, bars_from_closes([1.0] * 10), {})
        self.assertEqual(row["state"], "unavailable")
        self.assertEqual(row["heat"], 0)
        self.assertIsNone(row["ret_20d"])
        self.assertEqual(row["evidence"], ["本地日线不足 25 根"])

    def test_uptrend_metrics(self) -> None:
        bars = uptrend_bars()
        row = analytics.analyze_hotspot_row(ENTITY, bars, {"price": 1.8, "change_pct": 0.02})
        self.assertEqual(row["code"], "sh512480")
        self.assertEqual(row["direction"], "半导体")
        self.assertGreater(row["ret_20d"], 0)
        self.assertGreater(row["ret_20d"], 0)
        self.assertTrue(row["close_above_ma20"])
        self.assertTrue(row["ma20_above_ma60"])
        self.assertTrue(row["breakout_20d"])
        # 单边上涨到年内高位，按规则是 crowded（高位拥挤）或 hot（强势）
        self.assertIn(row["state"], {"hot", "crowded"})
        self.assertEqual(row["heat"], max(row["heat"], 1))
        self.assertEqual(len(row["spark"]), 20)
        self.assertFalse(any("沪深300" in item for item in row["evidence"]))

    def test_quote_fills_price(self) -> None:
        bars = uptrend_bars()
        row = analytics.analyze_hotspot_row(ENTITY, bars, {"price": 9.9, "change_pct": 0.5, "name": "半导体ETF"})
        self.assertEqual(row["price"], 9.9)
        self.assertEqual(row["change_pct"], 0.5)
        self.assertEqual(row["name"], "半导体ETF")


class RankTest(unittest.TestCase):
    def test_rank_rows(self) -> None:
        rows = [
            {"code": "a", "heat": 10, "ret_20d": 0.1},
            {"code": "b", "heat": 80, "ret_20d": 0.0},
            {"code": "c", "heat": 80, "ret_20d": 0.2},
        ]
        ranked = analytics.rank_rows(rows)
        self.assertEqual([row["code"] for row in ranked], ["c", "b", "a"])
        self.assertEqual([row["rank"] for row in ranked], [1, 2, 3])

    def test_count_states(self) -> None:
        rows = [{"state": "hot"}, {"state": "hot"}, {"state": "unknown_state"}]
        counts = analytics.count_states(rows)
        self.assertEqual(counts["hot"], 2)
        self.assertEqual(counts["unavailable"], 0)
        self.assertEqual(sum(counts.values()), 3)

    def test_market_state(self) -> None:
        rows = [{"state": "hot"}, {"state": "hot"}, {"state": "warming"}]
        counts = analytics.count_states(rows)
        state, headline = analytics.market_state(rows, counts)
        self.assertIn(state, {"热点扩散", "结构性轮动", "高位分化", "退潮占优"})
        self.assertTrue(headline)

    def test_rank_changes(self) -> None:
        rows = [{"code": "a", "rank": 1}, {"code": "b", "rank": 2}, {"code": "c", "rank": 3}]
        history = [
            {"date": "2026-10-30", "rows": [{"code": "a", "rank": 3}, {"code": "b", "rank": 1}, {"code": "d", "rank": 2}]},
            {"date": "2026-11-02", "rows": [{"code": "a", "rank": 3}, {"code": "b", "rank": 1}]},
            {"date": "2026-11-03", "rows": [{"code": "a", "rank": 3}, {"code": "b", "rank": 1}]},
            {"date": "2026-11-04", "rows": [{"code": "a", "rank": 3}, {"code": "b", "rank": 1}]},
            {"date": "2026-11-05", "rows": [{"code": "a", "rank": 3}, {"code": "b", "rank": 2}]},
            {"date": "2026-11-06", "rows": [{"code": "a", "rank": 1}, {"code": "b", "rank": 2}]},
        ]
        changes = analytics.rank_changes(rows, history, as_of="2026-11-06", lookback=5)
        # 基准是 5 个快照之前（2026-11-05 之前）：a 从第 3 名升到第 1 名
        self.assertEqual(changes["a"], 2)
        self.assertIsNone(changes["c"])
        # 历史不足时全部 null，不拿 0 冒充「没变化」
        self.assertEqual(analytics.rank_changes(rows, history[:2], as_of="2026-11-06"), {"a": None, "b": None, "c": None})


class BreadthTest(unittest.TestCase):
    def test_breadth(self) -> None:
        rows = [
            {"code": "a", "state": "hot", "close_above_ma20": True, "ma60": 1.0, "price": 2.0, "ret_20d": 0.1, "ret_1d": 0.01},
            {"code": "b", "state": "neutral", "close_above_ma20": False, "ma60": 2.0, "price": 1.0, "ret_20d": 0.0, "ret_1d": -0.01},
            {"code": "c", "state": "unavailable", "close_above_ma20": False, "ma60": None, "price": None, "ret_20d": None, "ret_1d": None},
        ]
        breadth = analytics.breadth_from_rows(rows)
        self.assertEqual(breadth["total"], 3)
        self.assertEqual(breadth["above_ma20"], 1)
        self.assertEqual(breadth["above_ma60"], 1)
        self.assertEqual(breadth["advancers"], 1)
        self.assertEqual(breadth["decliners"], 1)
        self.assertAlmostEqual(breadth["median_ret_20d"], 0.05)

    def test_quotes_override(self) -> None:
        rows = [{"code": "a", "state": "hot", "close_above_ma20": True, "ret_1d": 0.01, "ret_20d": 0.0}]
        breadth = analytics.breadth_from_rows(rows, {"a": {"change_pct": -0.02}})
        self.assertEqual(breadth["decliners"], 1)
        self.assertEqual(breadth["advancers"], 0)


class SelectBenchmarkTest(unittest.TestCase):
    def test_largest_by_market_cap(self) -> None:
        quotes = {
            "sh510300": {"market_cap": 100.0},
            "sh510310": {"market_cap": 300.0},
            "sh510330": {"market_cap": 200.0},
        }
        self.assertEqual(analytics.select_largest_csi300(quotes), "sh510310")
        self.assertEqual(analytics.select_largest_csi300({}, "sh510300"), "sh510300")
        self.assertEqual(analytics.select_largest_csi300({}), "sh510300")


class FlowSummaryTest(unittest.TestCase):
    def test_basic(self) -> None:
        snapshots = [
            {"date": "2026-11-02", "shares": 100.0, "price": 4.0},
            {"date": "2026-11-03", "shares": 110.0, "price": 4.0},
            {"date": "2026-11-04", "shares": 120.0, "price": 4.0},
            {"date": "2026-11-05", "shares": 130.0, "price": 4.0},
            {"date": "2026-11-06", "shares": 140.0, "price": 4.0},
        ]
        summary = analytics.flow_summary(snapshots)
        self.assertEqual(summary["state_label"], "净申购 / 宽基承接")
        self.assertEqual(summary["latest"]["date"], "2026-11-06")
        self.assertAlmostEqual(summary["daily_share_change"], 10.0)
        self.assertAlmostEqual(summary["daily_flow_amount"], 40.0)
        self.assertAlmostEqual(summary["five_flow_amount"], 40.0 * 4.0)  # 40 份 × 均价 4 元
        self.assertIsNone(summary["twenty_flow_amount"])
        self.assertEqual(len(summary["snapshots"]), 5)
        self.assertIsNone(summary["open_period"])

    def test_daily_flow_amount_uses_latest_close(self) -> None:
        """当日净申赎金额 = 份额差 × 当日最新收盘价，不是两日均价。"""
        snapshots = [
            {"date": "2026-11-05", "shares": 100.0, "price": 3.0},
            {"date": "2026-11-06", "shares": 110.0, "price": 5.0},
        ]
        summary = analytics.flow_summary(snapshots)
        self.assertAlmostEqual(summary["daily_share_change"], 10.0)
        self.assertAlmostEqual(summary["daily_flow_amount"], 10.0 * 5.0)  # 不是 10 × 4

    def test_baseline_only(self) -> None:
        summary = analytics.flow_summary([{"date": "2026-11-06", "shares": 100.0, "price": 4.0}])
        self.assertEqual(summary["state_label"], "建立份额基线")
        self.assertIsNone(summary["daily_share_change"])

    def test_empty(self) -> None:
        summary = analytics.flow_summary([])
        self.assertEqual(summary["state_label"], "尚无份额快照")
        self.assertIsNone(summary["latest"])

    def test_large_redemptions(self) -> None:
        snapshots = [{"date": "2026-11-06", "shares": 100.0, "price": 4.0}]
        periods = [{"date": "2026-09-30", "net": -80.0, "net_pct": -0.3, "large_redemption": True}]
        redemptions = [
            {"date": "2026-09-30", "net": -80.0 * 4.0, "net_pct": -0.3, "report_date": "2026-09-30", "label": "Q3", "large_redemption": True}
        ]
        summary = analytics.flow_summary(snapshots, None, periods, redemptions)
        self.assertEqual(len(summary["large_redemptions"]), 1)
        self.assertAlmostEqual(summary["large_redemptions"][0]["net"], -320.0)


class AgentReportsTest(unittest.TestCase):
    def test_four_agents(self) -> None:
        rows = [
            {"direction": "半导体", "state": "hot", "close_above_ma20": True, "ma20_above_ma60": True, "ret_20d": 0.1},
            {"direction": "银行", "state": "fading", "close_above_ma20": False, "ma20_above_ma60": False, "ret_20d": -0.05},
        ]
        counts = analytics.count_states(rows)
        reports = analytics.agent_reports(rows, counts, {"state_label": "净申购", "latest": {"date": "2026-11-06", "shares": 1e10}})
        self.assertEqual([report["id"] for report in reports], ["trend", "breadth", "flow", "risk"])
        for report in reports:
            self.assertTrue(report["name"])
            self.assertTrue(report["stance"])
            self.assertTrue(report["summary"])
            self.assertTrue(report["facts"])


if __name__ == "__main__":
    unittest.main()
