"""views 层单测：brief 规则、报价缓存 TTL/stale、搜索与元数据。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

from etfmon import collectors, config, sources, store, views


def shanghai(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=config.SHANGHAI_TZ)


class BriefTest(unittest.TestCase):
    def test_positive(self) -> None:
        national = {"five_day_flow": 5e8, "five_day_days": 5, "today_flow": 1e8, "today_flow_partial": False, "flow_date": store.trade_date_of()}
        hotspots = {"counts": {"hot": 3, "warming": 2, "crowded": 1}, "top": [{"direction": "半导体"}, {"direction": "芯片"}]}
        breadth = {"above_ma20": 6, "total": 10, "median_ret_20d": 0.02}
        brief = views.build_brief(national, hotspots, breadth, [])
        self.assertEqual(brief["tone"], "positive")
        self.assertLessEqual(len(brief["headline"]), 30)
        self.assertLessEqual(len(brief["bullets"]), 3)
        for bullet in brief["bullets"]:
            self.assertLessEqual(len(bullet), 40)
        self.assertIn("半导体", brief["bullets"][-1])

    def test_risk(self) -> None:
        national = {"five_day_flow": -5e8, "five_day_days": 5, "today_flow": -1e8, "today_flow_partial": True, "flow_date": store.trade_date_of()}
        hotspots = {"counts": {"hot": 0, "warming": 1, "crowded": 0}, "top": []}
        breadth = {"above_ma20": 3, "total": 10, "median_ret_20d": -0.02}
        brief = views.build_brief(national, hotspots, breadth, [])
        self.assertEqual(brief["tone"], "risk")
        self.assertIn("净赎回", brief["headline"])

    def test_neutral(self) -> None:
        national = {"five_day_flow": 0.0, "five_day_days": 5, "today_flow": 0.0, "today_flow_partial": False, "flow_date": store.trade_date_of()}
        hotspots = {"counts": {"hot": 2, "warming": 2, "crowded": 0}, "top": [{"direction": "酒"}]}
        breadth = {"above_ma20": 5, "total": 10, "median_ret_20d": 0.0}
        brief = views.build_brief(national, hotspots, breadth, [])
        self.assertEqual(brief["tone"], "neutral")

    def test_insufficient_data(self) -> None:
        brief = views.build_brief({}, {}, {"above_ma20": 0, "total": 0}, ["报价缓存为空"])
        self.assertIn("数据尚不足以形成观察", brief["headline"])
        self.assertLessEqual(len(brief["bullets"]), 3)
        for bullet in brief["bullets"]:
            self.assertLessEqual(len(bullet), 40)

    def test_non_trading_flow_label(self) -> None:
        national = {
            "five_day_flow": 5e8,
            "five_day_days": 2,
            "today_flow": 4.69e9,
            "today_flow_partial": False,
            "flow_date": "2026-11-06",
        }
        hotspots = {"counts": {"hot": 3}, "top": [{"direction": "酒"}]}
        breadth = {"above_ma20": 6, "total": 10, "median_ret_20d": 0.02}
        brief = views.build_brief(national, hotspots, breadth, [])
        self.assertIn("最近交易日", brief["bullets"][0])
        self.assertIn("近 2 日", brief["bullets"][0])
        # 5 天不足时 headline 不能写「5 日」
        self.assertNotIn("5 日", brief["headline"])

    def test_clip(self) -> None:
        self.assertEqual(views._clip("abc", 10), "abc")
        clipped = views._clip("一" * 50, 40)
        self.assertEqual(len(clipped), 40)
        self.assertTrue(clipped.endswith("…"))


class QuoteCacheTest(unittest.TestCase):
    def _quote(self, price: float = 4.1) -> dict:
        return {"code": "sh510300", "name": "沪深300ETF", "kind": "etf", "price": price, "source": "tencent", "stale": False}

    def test_snapshot_is_copy(self) -> None:
        cache = collectors.QuoteCache()
        batch = sources.QuoteBatch(quotes={"sh510300": self._quote()}, fetched_at=store.iso_now(), source="tencent")
        cache.update(batch)
        quotes, fetched_at, age = cache.snapshot()
        quotes["sh510300"]["price"] = 999
        self.assertEqual(cache.get("sh510300")["price"], 4.1)
        self.assertIsNotNone(fetched_at)
        self.assertGreaterEqual(age or 0, 0)

    def test_empty_cache(self) -> None:
        cache = collectors.QuoteCache()
        quotes, fetched_at, age = cache.snapshot()
        self.assertEqual(quotes, {})
        self.assertIsNone(fetched_at)
        self.assertIsNone(age)

    def test_stale_during_trading(self) -> None:
        fixed = shanghai(2026, 11, 6, 10, 0)
        cache = collectors.QuoteCache()
        old = (fixed - timedelta(seconds=400)).isoformat(timespec="seconds")
        cache.update(sources.QuoteBatch(quotes={"sh510300": self._quote()}, fetched_at=old, source="tencent"))
        with mock.patch("etfmon.store.now", return_value=fixed):
            app = views.App(cache=cache, started_at=fixed)
            quotes, _fetched_at, stale = views._cached_quotes(app)
        self.assertTrue(stale)
        self.assertTrue(quotes["sh510300"]["stale"])

    def test_fresh_not_stale(self) -> None:
        fixed = shanghai(2026, 11, 6, 10, 0)
        cache = collectors.QuoteCache()
        fresh = (fixed - timedelta(seconds=10)).isoformat(timespec="seconds")
        cache.update(sources.QuoteBatch(quotes={"sh510300": self._quote()}, fetched_at=fresh, source="tencent"))
        with mock.patch("etfmon.store.now", return_value=fixed):
            app = views.App(cache=cache, started_at=fixed)
            _quotes, _fetched_at, stale = views._cached_quotes(app)
        self.assertFalse(stale)

    def test_off_hours_never_stale(self) -> None:
        fixed = shanghai(2026, 11, 7, 10, 0)  # 周六
        cache = collectors.QuoteCache()
        old = (fixed - timedelta(hours=48)).isoformat(timespec="seconds")
        cache.update(sources.QuoteBatch(quotes={"sh510300": self._quote()}, fetched_at=old, source="tencent"))
        with mock.patch("etfmon.store.now", return_value=fixed):
            app = views.App(cache=cache, started_at=fixed)
            _quotes, _fetched_at, stale = views._cached_quotes(app)
        self.assertFalse(stale)

    def test_persisted_snapshot_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"ETF_DATA": tmp}):
            store.ensure_layout()
            store.update_quotes_snapshot({"sh510300": self._quote(4.5)}, "2026-11-06T15:00:00+08:00")
            cache = collectors.QuoteCache()
            app = views.App(cache=cache, started_at=store.now())
            quotes, fetched_at, _stale = views._cached_quotes(app)
            self.assertEqual(quotes["sh510300"]["price"], 4.5)
            self.assertEqual(fetched_at, "2026-11-06T15:00:00+08:00")
            self.assertTrue(quotes["sh510300"]["stale"])

    def test_empty_quote_fields(self) -> None:
        quote = views._empty_quote("sh510300")
        self.assertEqual(quote["source"], "none")
        self.assertTrue(quote["stale"])
        for field in ("price", "shares", "amount", "quote_time"):
            self.assertIsNone(quote[field])


class TeamDataQualityTest(unittest.TestCase):
    """盘中样本只取当日；自愈告警透传到 data_quality.warnings。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"ETF_DATA": self._tmp.name})
        self._env.start()
        store.ensure_layout()
        store.save_daily(
            {
                "days": {
                    "2026-11-05": {"sh510300": {"shares": 1000.0, "price": 4.0}},
                    "2026-11-06": {"sh510300": {"shares": 1100.0, "price": 4.1}},
                },
                "intraday": {
                    "2026-11-05": [{"time": "15:00:00", "amount": 100.0, "share_delta": 10.0}],
                },
            }
        )
        self.app = views.App(cache=collectors.QuoteCache(), started_at=store.now())

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def test_intraday_only_for_trade_date(self) -> None:
        team = views.build_team(self.app)
        self.assertEqual(team["trade_date"], "2026-11-06")
        # 只有 11-05 的样本，不能拿来冒充 11-06
        self.assertEqual(team["intraday"], [])

    def test_data_warning_surfaced(self) -> None:
        message = "2026-11-06 已作废 2 条与最终份额不一致的盘中样本"
        store.update_state(lambda state: {**state, "data_warnings": {"2026-11-06": message}})
        team = views.build_team(self.app)
        self.assertIn(message, team["data_quality"]["warnings"])


class SessionResolveTest(unittest.TestCase):
    def test_holiday_downgrade(self) -> None:
        with mock.patch("etfmon.store.now", return_value=shanghai(2026, 11, 9, 10, 0)):
            # 周一是工作日，但数据停在 11-06，说明是节假日
            self.assertEqual(views.resolve_session("2026-11-06"), "closed")
            self.assertEqual(views.resolve_session("2026-11-09"), "morning")

    def test_weekend(self) -> None:
        with mock.patch("etfmon.store.now", return_value=shanghai(2026, 11, 7, 10, 0)):
            self.assertEqual(views.resolve_session("2026-11-06"), "weekend")


class CatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"ETF_DATA": self._tmp.name})
        self._env.start()
        store.ensure_layout()
        self.cache = collectors.QuoteCache()
        self.app = views.App(cache=self.cache, started_at=store.now())

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def test_search_by_direction(self) -> None:
        with mock.patch("etfmon.views.sources.fetch_tencent_search", side_effect=sources.SourceError("offline")):
            result = views.build_search(self.app, "半导体")
        codes = [item["code"] for item in result["results"]]
        self.assertIn("sh512480", codes)
        self.assertLessEqual(len(result["results"]), config.SEARCH_LIMIT)
        self.assertIsNotNone(result["warning"])  # 上游失败时回退本地并 warning

    def test_search_by_code(self) -> None:
        with mock.patch("etfmon.views.sources.fetch_tencent_search", return_value=[]):
            result = views.build_search(self.app, "510300")
        self.assertEqual(result["results"][0]["code"], "sh510300")
        self.assertEqual(result["results"][0]["market"], "sh")
        self.assertIsNone(result["warning"])

    def test_search_upstream_merge(self) -> None:
        upstream = [{"code": "sh518880", "name": "黄金ETF", "kind": "etf", "market": "sh"}]
        with mock.patch("etfmon.views.sources.fetch_tencent_search", return_value=upstream):
            result = views.build_search(self.app, "黄金")
        self.assertEqual([item["code"] for item in result["results"]], ["sh518880"])
        self.assertIsNone(result["warning"])

    def test_search_unknown(self) -> None:
        with mock.patch("etfmon.views.sources.fetch_tencent_search", return_value=[]):
            result = views.build_search(self.app, "不存在的方向")
        self.assertEqual(result["results"], [])
        self.assertIsNone(result["warning"])

    def test_meta_entities(self) -> None:
        meta = views.build_meta(self.app)
        self.assertEqual(meta["product"], config.PRODUCT)
        self.assertEqual(meta["version"], config.APP_VERSION)
        self.assertGreater(len(meta["entities"]), 50)
        self.assertIn("net_flow", meta["definitions"])
        self.assertTrue(meta["limitations"])


if __name__ == "__main__":
    unittest.main()
