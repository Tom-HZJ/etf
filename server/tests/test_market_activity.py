from __future__ import annotations

import copy
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from etfmon import config, store, turnover, market_activity, views, collectors

DAY, PREVIOUS = "2026-11-11", "2026-11-10"


def moment(clock="14:11:20"):
    return datetime.fromisoformat(f"{DAY}T{clock}+08:00")


def payload(code, today=200, before=100, price=11):
    return {"code": code, "pre_close": 10, "days": [
        {"date": day, "points": [{"time": t, "volume": 1, "price": price,
                                   "cumulative_amount": (i + 1) * scale}
                                  for i, t in enumerate(turnover.CLOCKS)]}
        for day, scale in ((PREVIOUS, before), (DAY, today))]}


class MarketActivityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"ETF_DATA": self.tmp.name})
        self.env.start()
        self.universe = mock.patch.object(config, "HOTSPOT_UNIVERSE", (
            ("sh515880", "通信", "科技"), ("sh512800", "银行", "金融"),
            ("sh513180", "恒生科技", "港股"), ("sh518880", "黄金", "周期")))
        self.universe.start()
        self.now = moment()
        self.write("sh000001", payload("sh000001", 200, 100))
        self.write("sz399001", payload("sz399001", 300, 200))
        self.write("sh515880", payload("sh515880", 300, 100, 11))
        self.write("sh512800", payload("sh512800", 400, 100, 9))

    def tearDown(self):
        self.universe.stop()
        self.env.stop()
        self.tmp.cleanup()

    def write(self, code, value):
        store.write_json(store.path_for("intraday", code + ".json"), value)

    def view(self, quotes=None):
        return market_activity.build_view(quotes or {}, DAY, self.now, [PREVIOUS, DAY])

    def test_exact_market_sum_same_clock_direction_signs_and_no_future_minutes(self):
        result = self.view()
        index = turnover.CLOCKS.index("14:10") + 1
        self.assertEqual(result["as_of"], DAY + "T14:10:00+08:00")
        self.assertEqual(result["amount"], index * 500)
        self.assertEqual(result["previous_amount"], index * 300)
        self.assertEqual(result["delta"], sum(r["delta"] for r in result["markets"]))
        self.assertEqual(result["volume_state"], "expanded")
        self.assertAlmostEqual(result["delta_pct"], 2/3)
        self.assertEqual([r["direction"] for r in result["directions"]["up"]], ["通信"])
        self.assertEqual([r["direction"] for r in result["directions"]["down"]], ["银行"])
        self.assertEqual(result["directions"]["compared"], 2)
        self.assertEqual(result["directions"]["total"], 2)

    def test_cannot_sum_one_market_or_replace_missing_previous_day(self):
        p = payload("sz399001")
        p["days"] = p["days"][:1]
        self.write("sz399001", p)
        self.assertIsNone(self.view()["amount"])
        self.assertEqual(self.view()["state"], "unavailable")
        p = payload("sz399001", 300, 200)
        p["days"][0]["date"] = "2026-11-09"
        self.write("sz399001", p)
        result = self.view()
        self.assertIsNotNone(result["amount"])
        self.assertIsNone(result["delta"])
        self.assertEqual(result["previous_date"], PREVIOUS)

    def test_actual_zero_difference_is_not_missing(self):
        for code, _ in market_activity.MARKETS:
            self.write(code, payload(code, 100, 100))
        result = self.view()
        self.assertEqual(result["delta"], 0)
        self.assertEqual(result["delta_pct"], 0)
        self.assertEqual(result["volume_state"], "unchanged")

    def test_read_only_cache_and_atomic_replacement_invalidation(self):
        with mock.patch.object(store, "write_json", side_effect=AssertionError("GET wrote data")), mock.patch("etfmon.sources._http_get", side_effect=AssertionError("GET fetched upstream")):
            first = self.view()
        self.write("sh515880", payload("sh515880", 50, 100, 11))
        second = self.view()
        self.assertEqual(first["directions"]["up"][0]["direction"], "通信")
        self.assertEqual(second["directions"]["up"], [])

    def test_conflicting_and_missing_proxy_points_never_borrow_an_earlier_minute(self):
        p = payload("sh515880", 300, 100, 11)
        p["days"][1]["points"] = [x for x in p["days"][1]["points"] if x["time"] != "14:10"]
        self.write("sh515880", p)
        self.assertEqual(self.view()["directions"]["compared"], 1)
        p = payload("sh000001")
        p["days"][1]["points"].append({**p["days"][1]["points"][0], "cumulative_amount": 999})
        self.write("sh000001", p)
        self.assertIsNone(self.view()["amount"])

    def test_lunch_future_points_and_stale_brief_do_not_claim_current_expansion(self):
        self.now = moment("12:00:00")
        result = self.view()
        self.assertEqual(result["state"], "lunch")
        self.assertEqual(result["as_of"], DAY + "T11:30:00+08:00")
        self.assertIsNone(result["afternoon_amount"])
        self.now = moment("14:30:00")
        for c, _ in market_activity.MARKETS:
            p = payload(c)
            p["days"][1]["points"] = [x for x in p["days"][1]["points"] if x["time"] <= "14:00"]
            self.write(c, p)
        result = self.view()
        self.assertEqual(result["state"], "stale")
        brief = views.build_brief({}, {}, {}, [], result)
        self.assertEqual(brief["headline"], "成交数据待更新")
        self.assertEqual(brief["tone"], "neutral")

    def test_live_quotes_and_sampled_comparison_keep_independent_times(self):
        q = {c: {"amount": 500_000, "quote_time": self.now.isoformat(), "pre_close": 10} for c, _ in market_activity.MARKETS}
        result = self.view(q)
        self.assertEqual(result["quote_amount"], 1_000_000)
        self.assertEqual(result["quote_time"], self.now.isoformat())
        self.assertNotEqual(result["quote_time"], result["as_of"])
        morning = 121 * 500
        self.assertEqual(result["quote_afternoon_amount"], 1_000_000 - morning)
        q["sh000001"]["quote_time"] = DAY + "T14:00:00+08:00"
        self.assertIsNone(self.view(q)["quote_amount"])
        self.assertEqual(self.view(q)["quote_time"], DAY + "T14:00:00+08:00")

    def test_expansion_is_never_a_bullish_brief_or_investor_identity(self):
        brief = views.build_brief({}, {}, {}, [], self.view())
        self.assertIn("沪深放量", brief["headline"])
        self.assertEqual(brief["tone"], "neutral")
        self.assertNotIn("净流入", str(brief))
        self.assertNotIn("国家队", str(brief))

    def test_existing_cache_loop_fetches_once_for_each_session_close(self):
        times = [moment("11:31:00"), moment("11:36:00"), moment("15:01:00"), moment("15:06:00")]
        class Stop:
            index = 0
            def is_set(self): return self.index >= len(times)
            def wait(self, seconds):
                assert seconds == 300
                self.index += 1
        worker = collectors.Collector(collectors.QuoteCache())
        worker._stop = Stop()
        with mock.patch.object(config, "TEAM_CODES", []), mock.patch.object(store, "now", side_effect=lambda: times[worker._stop.index]), mock.patch.object(worker, "_intraday_cache_fresh", return_value=True), mock.patch.object(collectors, "quote_codes", return_value=[]), mock.patch.object(collectors, "refresh_intraday_cache", return_value={"errors": []}) as fetch:
            worker._intraday_cache_loop()
        self.assertEqual(fetch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
