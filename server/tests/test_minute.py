"""分钟 K 单测：解析、缓存 TTL、回退 warning、采集刷新、API 视图。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import timedelta
from unittest import mock

from etfmon import config, minute, observability, sources, store, views
from etfmon.collectors import QuoteCache


def bar(time_text: str, close: float = 4.12) -> dict:
    return {
        "time": time_text,
        "date": time_text[:10],
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 100.0,
        "amount": None,
    }


class _MinuteBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._env = mock.patch.dict(os.environ, {"ETF_DATA": self._tmp.name})
        self._env.start()
        store.ensure_layout()
        observability.registry.reset()
        sources._quote_no_data.clear()
        with minute._guard:
            minute._negative.clear()

    def tearDown(self) -> None:
        observability.registry.reset()
        with minute._guard:
            minute._negative.clear()
        self._env.stop()
        self._tmp.cleanup()


class ParserTest(unittest.TestCase):
    def test_eastmoney_minute(self) -> None:
        payload = {
            "data": {
                "name": "沪深300ETF",
                "preKPrice": 4.19,
                "klines": [
                    "2026-11-06 09:31,4.113,4.111,4.119,4.095,1608080,660734927.000",
                    "2026-11-06 15:00,4.124,4.124,4.124,4.124,178763,73721964.000",
                ],
            }
        }
        result = sources.parse_eastmoney_minute(payload, "sh510300")
        self.assertEqual(result["name"], "沪深300ETF")
        self.assertAlmostEqual(result["pre_close"], 4.19)
        self.assertEqual(len(result["bars"]), 2)
        first = result["bars"][0]
        self.assertEqual(first["time"], "2026-11-06 09:31")
        self.assertEqual(first["date"], "2026-11-06")
        self.assertAlmostEqual(first["open"], 4.113)
        self.assertAlmostEqual(first["close"], 4.111)
        self.assertAlmostEqual(first["high"], 4.119)
        self.assertAlmostEqual(first["low"], 4.095)
        self.assertAlmostEqual(first["volume"], 1608080)
        self.assertAlmostEqual(first["amount"], 660734927.0)

    def test_sina_minute_volume_to_hands(self) -> None:
        payload = [
            {"day": "2026-11-06 14:55:00", "open": "4.12", "high": "4.13", "low": "4.11", "close": "4.12", "volume": "44769225"},
            {"day": "2026-11-06 15:00:00", "open": "4.12", "high": "4.12", "low": "4.12", "close": "4.12", "volume": "1000000"},
        ]
        result = sources.parse_sina_minute(payload, "sh510300")
        self.assertEqual(len(result["bars"]), 2)
        self.assertEqual(result["bars"][0]["time"], "2026-11-06 14:55")
        self.assertAlmostEqual(result["bars"][0]["volume"], 447692.25)  # 股 → 手

    def test_empty_payloads(self) -> None:
        self.assertEqual(sources.parse_eastmoney_minute({}, "sh510300")["bars"], [])
        self.assertEqual(sources.parse_sina_minute(None, "sh510300")["bars"], [])

    def test_fetch_minute_fallback_to_sina(self) -> None:
        em_error = sources.SourceError("em down")
        bars = [bar("2026-11-06 14:55")]
        with mock.patch("etfmon.sources.fetch_eastmoney_minute", side_effect=em_error):
            with mock.patch("etfmon.sources.fetch_sina_minute", return_value={"bars": bars, "pre_close": None}):
                result, source = sources.fetch_minute("sh510300", 5, 10)
        self.assertEqual(source, "sina")
        self.assertEqual(result, bars)
        em_metric = observability.registry.metric("eastmoney_minute")
        sina_metric = observability.registry.metric("sina_minute")
        assert em_metric is not None and sina_metric is not None
        self.assertEqual(em_metric.failure_count, 1)
        self.assertEqual(sina_metric.fallback_count, 1)

    def test_sina_minute_rejects_interval_1(self) -> None:
        with self.assertRaises(sources.SourceError):
            sources.fetch_sina_minute("sh510300", 1, 10)

    def test_one_minute_falls_back_to_real_tencent_ohlc(self) -> None:
        observability.registry.reset()
        payload = {"data": {"sh512480": {"m1": [
            ["202611121311", "0.823", "0.825", "0.826", "0.823", "59339", {}, "2.784"],
        ]}}}
        with mock.patch.object(sources, "fetch_eastmoney_minute", side_effect=sources.SourceError("down")), \
                mock.patch.object(sources, "_http_get", return_value=__import__('json').dumps(payload)):
            bars, source = sources.fetch_minute("sh512480", 1, 240)
        self.assertEqual(source, "tencent")
        self.assertEqual(bars, [dict(time="2026-11-12 13:11", date="2026-11-12", open=.823,
                                    close=.825, high=.826, low=.823, volume=59339., amount=None)])
        self.assertEqual(observability.registry.metric("tencent_minute").fallback_count, 1)
        observability.registry.reset()

    def test_tencent_rejects_bad_geometry_nonfinite_conflicts_and_lunch(self) -> None:
        rows = [["202611121311", "1", "2", "2", "1", "20"]]
        rows += [rows[0], [*rows[0][:2], "1.5", *rows[0][3:]]]
        rows += [["202611121312", "1", "2", "1", "1", "20"],
                 ["202611121313", "1", "2", "2", "1", "NaN"],
                 ["202611121200", "1", "2", "2", "1", "20"],
                 ["202611121314", "1", "2", "2", "1", "0"]]
        p = sources.parse_tencent_minute({"data": {"sh512480": {"m1": rows}}}, "sh512480")
        self.assertEqual([b["time"] for b in p["bars"]], ["2026-11-12 13:14"])


class CacheTest(_MinuteBase):
    def test_future_end_is_withheld_even_from_cache(self) -> None:
        now = store.now().replace(year=2026, month=11, day=12, hour=13, minute=11, second=30)
        payload = dict(bars=[bar("2026-11-12 13:11"), bar("2026-11-12 13:12")], source="tencent")
        with mock.patch.object(store, "now", return_value=now):
            response = minute._response(payload, "sh512480", 1, 240, None)
        self.assertEqual(response["count"], 1)
        self.assertEqual(response["as_of"], "2026-11-12 13:11")

    def test_is_fresh_ttl(self) -> None:
        now = store.now()
        payload = {"fetched_at": now.isoformat(timespec="seconds")}
        self.assertTrue(minute.is_fresh(payload, now=now))
        # 盘中 60s TTL（用交易日 10:00 模拟）
        trading_now = now.replace(hour=10, minute=0, second=0, microsecond=0)
        old = (trading_now - timedelta(seconds=90)).isoformat(timespec="seconds")
        if trading_now.weekday() < 5:
            self.assertFalse(minute.is_fresh({"fetched_at": old}, now=trading_now))
        # 收盘后 30min TTL
        closed_now = now.replace(hour=16, minute=0, second=0, microsecond=0)
        payload = {"fetched_at": (closed_now - timedelta(seconds=600)).isoformat(timespec="seconds")}
        self.assertTrue(minute.is_fresh(payload, now=closed_now))
        # 隔日一定过期
        self.assertFalse(
            minute.is_fresh({"fetched_at": "2020-01-01T10:00:00+08:00"}, now=now)
        )

    def test_get_minute_caches(self) -> None:
        bars = [bar("2026-11-06 14:55"), bar("2026-11-06 15:00")]
        with mock.patch("etfmon.sources.fetch_minute", return_value=(bars, "eastmoney")) as fetch:
            first = minute.get_minute("sh510300", 5, 10)
            second = minute.get_minute("sh510300", 5, 10)
        self.assertEqual(fetch.call_count, 1)  # 第二次命中缓存
        self.assertEqual(first["count"], 2)
        self.assertIsNone(first["warning"])
        self.assertEqual(first["source"], "eastmoney")
        self.assertEqual(second["as_of"], "2026-11-06 15:00")
        self.assertTrue(minute.cache_path("sh510300", 5).is_file())

    def test_get_minute_failure_returns_warning_and_stale_cache(self) -> None:
        old_payload = {
            "code": "sh510300",
            "interval": 5,
            "name": "沪深300ETF",
            "source": "eastmoney",
            "fetched_at": "2026-11-05T10:00:00+08:00",
            "bars": [bar("2026-11-05 14:55")],
        }
        store.write_json(minute.cache_path("sh510300", 5), old_payload)
        with mock.patch("etfmon.sources.fetch_minute", side_effect=sources.SourceError("upstream down")) as fetch:
            result = minute.get_minute("sh510300", 5, 10)
            again = minute.get_minute("sh510300", 5, 10)
        self.assertEqual(fetch.call_count, 1)  # 失败负缓存
        self.assertEqual(result["count"], 1)
        self.assertIn("上游暂时不可用", result["warning"])
        self.assertIn("缓存", result["warning"])
        self.assertEqual(again["count"], 1)
        self.assertIn("上游暂时不可用", again["warning"])

    def test_unknown_codes_do_not_poison_sources(self) -> None:
        minute_ids = ("eastmoney_minute", "sina_minute")
        before = {}
        for source_id in minute_ids:
            metric = observability.registry.metric(source_id)
            assert metric is not None
            before[source_id] = (metric.failure_count, metric.consecutive_failures, metric.circuit)
        with mock.patch("etfmon.sources.fetch_eastmoney_minute", side_effect=sources.NoDataError("无数据")):
            with mock.patch("etfmon.sources.fetch_sina_minute", side_effect=sources.NoDataError("无数据")):
                for index in range(5):
                    result = minute.get_minute(f"sh99999{index}", 5, 10)
                    self.assertEqual(result["bars"], [])
                    self.assertIn("暂无该标的数据", result["warning"])
        for source_id in minute_ids:
            metric = observability.registry.metric(source_id)
            assert metric is not None
            self.assertEqual(
                (metric.failure_count, metric.consecutive_failures, metric.circuit),
                before[source_id],
            )
        self.assertFalse(any(alert["level"] == "critical" for alert in observability.registry.alerts()))
        # 合法代码随后仍可用
        with mock.patch("etfmon.sources.fetch_minute", return_value=([bar("2026-11-06 15:00")], "eastmoney")):
            result = minute.get_minute("sh510300", 5, 10)
        self.assertEqual(result["count"], 1)
        self.assertIsNone(result["warning"])

    def test_get_minute_failure_without_cache(self) -> None:
        with mock.patch("etfmon.sources.fetch_minute", side_effect=sources.SourceError("down")):
            result = minute.get_minute("sh513180", 1, 10)
        self.assertEqual(result["bars"], [])
        self.assertEqual(result["warning"], minute.UPSTREAM_WARNING)

    def test_refresh_codes_saves(self) -> None:
        bars = [bar("2026-11-06 14:55")]
        with mock.patch("etfmon.sources.fetch_minute", return_value=(bars, "eastmoney")):
            summary = minute.refresh_codes(["sh510300", "sh000300"], intervals=(1, 5))
        self.assertEqual(summary["saved"], 4)
        self.assertEqual(summary["errors"], [])

    def test_refresh_codes_collects_errors(self) -> None:
        with mock.patch("etfmon.sources.fetch_minute", side_effect=sources.SourceError("down")):
            summary = minute.refresh_codes(["sh510300"], intervals=(1,))
        self.assertEqual(summary["saved"], 0)
        self.assertEqual(len(summary["errors"]), 1)


class MinuteViewTest(_MinuteBase):
    def test_build_minute_contract(self) -> None:
        app = views.App(cache=QuoteCache(), started_at=store.now())
        payload = {
            "code": "sh510300",
            "interval": 1,
            "name": "沪深300ETF华泰柏瑞",
            "count": 1,
            "as_of": "2026-11-06 15:00",
            "source": "eastmoney",
            "warning": None,
            "bars": [
                {
                    "time": "2026-11-06 15:00",
                    "date": "2026-11-06",
                    "open": 4.1,
                    "high": 4.2,
                    "low": 4.0,
                    "close": 4.15,
                    "volume": 100,
                }
            ],
        }
        with mock.patch("etfmon.minute.get_minute", return_value=payload):
            result = views.build_minute(app, "sh510300", 1, 240)
        for key in ("generated_at", "code", "name", "interval", "count", "as_of", "source", "warning", "bars"):
            self.assertIn(key, result)
        self.assertEqual(result["interval"], 1)
        self.assertEqual(result["bars"][0]["close"], 4.15)


if __name__ == "__main__":
    unittest.main()
