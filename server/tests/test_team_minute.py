"""国家队篮子分钟序列单测：合成公式、时间对齐、缓存、缺失、并发、API 视图。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import timedelta
from unittest import mock

from etfmon import config, sources, store, team_minute, views
from etfmon.collectors import QuoteCache


def bar(time_text: str, close: float, volume: float = 100.0) -> dict:
    return {
        "time": time_text,
        "date": time_text[:10],
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": volume,
        "amount": None,
    }


class _TeamMinuteBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._env = mock.patch.dict(os.environ, {"ETF_DATA": self._tmp.name})
        self._env.start()
        store.ensure_layout()
        team_minute._negative_until = 0.0

    def tearDown(self) -> None:
        team_minute._negative_until = 0.0
        self._env.stop()
        self._tmp.cleanup()

    def write_daily(self, shares: dict[str, float], date: str = "2026-11-06") -> None:
        payload = {"days": {date: {code: {"shares": value, "price": 4.0} for code, value in shares.items()}}}
        store.write_json(store.path_for("national", "daily.json"), payload)


class SharesTest(_TeamMinuteBase):
    def test_latest_shares_per_code(self) -> None:
        store.write_json(
            store.path_for("national", "daily.json"),
            {
                "days": {
                    "2026-11-05": {"a": {"shares": 111}, "b": {"shares": 222}},
                    "2026-11-06": {"a": {"shares": 333}},
                }
            },
        )
        with mock.patch.object(team_minute.config, "TEAM_CODES", ("a", "b", "c")):
            weights, date = team_minute.latest_shares()
        self.assertEqual(date, "2026-11-06")
        self.assertEqual(weights, {"a": 333.0, "b": 222.0})  # b 回退到最近可用日期


class ComposeTest(_TeamMinuteBase):
    def test_formula_and_alignment(self) -> None:
        windows = ("s1", "s2", "s3")
        results = {
            "s1": [bar("2026-11-06 09:31", 4.0, 10), bar("2026-11-06 09:32", 4.4, 20)],
            "s2": [bar("2026-11-06 09:31", 2.0, 30)],
            "s3": [],
        }
        weights = {"s1": 1e8, "s2": 1e8, "s3": 1e8}
        with mock.patch.object(team_minute.config, "TEAM_CODES", windows):
            payload = team_minute.compose(results, weights, 240, errors={"s3": "失败：timeout"})
        self.assertEqual(payload["method"]["codes"], 3)
        self.assertEqual(payload["method"]["missing"], ["s3"])
        self.assertIn("1 只基金缺少分钟数据", payload["warning"])
        self.assertEqual([point["time"] for point in payload["series"]], ["2026-11-06 09:31", "2026-11-06 09:32"])
        first = payload["series"][0]
        # (1e8×4.0 + 1e8×2.0)/1e8 = 6.0 亿元
        self.assertAlmostEqual(first["value"], 6.0)
        self.assertAlmostEqual(first["change_pct"], 0.0)
        self.assertAlmostEqual(first["volume"], 40.0)
        second = payload["series"][1]
        # s2 缺 09:32 → 只计 s1：(1e8×4.4)/1e8 = 4.4 亿元
        self.assertAlmostEqual(second["value"], 4.4)
        self.assertAlmostEqual(second["change_pct"], round(4.4 / 6.0 - 1, 6))
        self.assertEqual(payload["as_of"], "2026-11-06 09:32")
        self.assertEqual(payload["count"], 2)

    def test_empty_series_warning(self) -> None:
        with mock.patch.object(team_minute.config, "TEAM_CODES", ("s1",)):
            no_data = team_minute.compose({"s1": []}, {"s1": 1.0})
            upstream = team_minute.compose({"s1": []}, {"s1": 1.0}, errors={"s1": "失败：x"})
        self.assertEqual(no_data["series"], [])
        self.assertEqual(no_data["warning"], team_minute.NO_DATA_WARNING)
        self.assertEqual(upstream["warning"], team_minute.UPSTREAM_WARNING)


class GetTeamMinuteTest(_TeamMinuteBase):
    def test_compose_end_to_end_and_cache(self) -> None:
        self.write_daily({"sh510300": 1e8, "sh510500": 1e8})
        bars = {
            "sh510300": [bar("2026-11-06 09:31", 4.0), bar("2026-11-06 09:32", 4.1)],
            "sh510500": [bar("2026-11-06 09:31", 2.0), bar("2026-11-06 09:32", 2.1)],
        }
        with mock.patch.object(team_minute.config, "TEAM_CODES", ("sh510300", "sh510500")):
            with mock.patch(
                "etfmon.sources.fetch_minute",
                side_effect=lambda code, interval, limit, **kwargs: (bars[code], "eastmoney"),
            ) as fetch:
                first = team_minute.get_team_minute(1, 240)
                second = team_minute.get_team_minute(1, 240)
        self.assertEqual(fetch.call_count, 2)  # 第二次命中缓存（每只 1 次/轮）
        self.assertEqual(first["count"], 2)
        self.assertEqual(first["source"], "eastmoney")
        self.assertIsNone(first["warning"])
        self.assertAlmostEqual(first["series"][0]["value"], round((1e8 * 4.0 + 1e8 * 2.0) / 1e8, 4))
        self.assertTrue(team_minute.cache_path(1).is_file())
        self.assertEqual(second["count"], 2)

    def test_partial_failure_returns_available_series(self) -> None:
        self.write_daily({"sh510300": 1e8, "sh510500": 1e8})

        def fetch(code, interval, limit, **kwargs):  # noqa: ANN001
            if code == "sh510500":
                raise sources.SourceError("upstream boom")
            return [bar("2026-11-06 09:31", 4.0)], "eastmoney"

        with mock.patch.object(team_minute.config, "TEAM_CODES", ("sh510300", "sh510500")):
            with mock.patch("etfmon.sources.fetch_minute", side_effect=fetch):
                payload = team_minute.get_team_minute(1, 240)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["method"]["missing"], ["sh510500"])
        self.assertIn("缺少分钟数据", payload["warning"])

    def test_all_fail_returns_empty_with_warning(self) -> None:
        self.write_daily({"sh510300": 1e8})
        with mock.patch.object(team_minute.config, "TEAM_CODES", ("sh510300",)):
            with mock.patch("etfmon.sources.fetch_minute", side_effect=sources.SourceError("down")):
                payload = team_minute.get_team_minute(5, 240)
        self.assertEqual(payload["series"], [])
        self.assertEqual(payload["warning"], team_minute.UPSTREAM_WARNING)

    def test_missing_shares_warning(self) -> None:
        with mock.patch.object(team_minute.config, "TEAM_CODES", ("sh510300",)):
            with mock.patch("etfmon.sources.fetch_minute", return_value=([bar("2026-11-06 09:31", 4.0)], "eastmoney")):
                payload = team_minute.get_team_minute(1, 240)
        self.assertEqual(payload["series"], [])
        self.assertEqual(payload["warning"], team_minute.NO_SHARES_WARNING)

    def test_stale_cache_fallback_on_failure(self) -> None:
        self.write_daily({"sh510300": 1e8})
        old = {
            "interval": 1,
            "fetched_at": "2020-01-01T10:00:00+08:00",
            "source": "eastmoney",
            "series": [{"time": "2020-01-01 09:31", "date": "2020-01-01", "value": 1.0, "change_pct": 0.0, "volume": 1.0}],
            "method": {"unit": "亿元", "weighting": "latest shares × minute close", "codes": 1, "missing": []},
        }
        store.write_json(team_minute.cache_path(1), old)
        with mock.patch.object(team_minute.config, "TEAM_CODES", ("sh510300",)):
            with mock.patch("etfmon.sources.fetch_minute", side_effect=sources.SourceError("down")):
                payload = team_minute.get_team_minute(1, 240)
        self.assertEqual(payload["count"], 1)
        self.assertIn("缓存", payload["warning"])

    def test_26_codes_concurrent_mock(self) -> None:
        shares = {code: 1e8 for code in config.TEAM_CODES}
        self.write_daily(shares)
        calls: list[str] = []

        def fetch(code, interval, limit, **kwargs):  # noqa: ANN001
            calls.append(code)
            return [bar("2026-11-06 09:31", 4.0), bar("2026-11-06 09:32", 4.1)], "eastmoney"

        with mock.patch("etfmon.sources.fetch_minute", side_effect=fetch):
            payload = team_minute.get_team_minute(1, 240)
        self.assertEqual(len(calls), len(config.TEAM_CODES))
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["method"]["codes"], 26)
        self.assertEqual(payload["method"]["missing"], [])
        self.assertIsNone(payload["warning"])

    def test_limit_trims_recent_points(self) -> None:
        self.write_daily({"sh510300": 1e8})
        many = [bar(f"2026-11-06 09:{minute:02d}", 4.0) for minute in range(1, 11)]
        with mock.patch.object(team_minute.config, "TEAM_CODES", ("sh510300",)):
            with mock.patch("etfmon.sources.fetch_minute", return_value=(many, "eastmoney")):
                payload = team_minute.get_team_minute(1, 5)
        self.assertEqual(payload["count"], 5)
        self.assertEqual(payload["series"][0]["time"], "2026-11-06 09:06")
        self.assertEqual(payload["as_of"], "2026-11-06 09:10")


class TeamMinuteViewTest(_TeamMinuteBase):
    def test_build_team_minute_contract(self) -> None:
        app = views.App(cache=QuoteCache(), started_at=store.now())
        payload = {
            "interval": 1,
            "count": 1,
            "as_of": "2026-11-06 15:00",
            "source": "eastmoney",
            "warning": None,
            "method": {"unit": "亿元", "weighting": "latest shares × minute close", "codes": 26, "missing": []},
            "series": [
                {
                    "time": "2026-11-06 15:00",
                    "date": "2026-11-06",
                    "value": 10123.45,
                    "change_pct": -0.0012,
                    "volume": 123456,
                }
            ],
        }
        with mock.patch("etfmon.team_minute.get_team_minute", return_value=payload):
            result = views.build_team_minute(app, 1, 240)
        self.assertIn("generated_at", result)
        for key in ("interval", "count", "as_of", "source", "warning", "method", "series"):
            self.assertIn(key, result)
        self.assertEqual(result["method"]["codes"], 26)
        self.assertEqual(result["series"][0]["value"], 10123.45)


if __name__ == "__main__":
    unittest.main()
