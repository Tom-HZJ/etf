"""数据目录、原子写、日线合并与 schema 的单测。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from etfmon import config, store


class StoreTestCase(unittest.TestCase):
    """每个测试用独立的临时数据目录（通过 ETF_DATA 环境变量）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"ETF_DATA": self._tmp.name})
        self._env.start()
        store.ensure_layout()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    @property
    def root(self) -> Path:
        return Path(self._tmp.name)


class LayoutTest(StoreTestCase):
    def test_layout_and_schema(self) -> None:
        for parts in (("bars",), ("flows",), ("national",), ("hotspots", "history")):
            self.assertTrue(self.root.joinpath(*parts).is_dir(), parts)
        schema = store.read_json(self.root / "schema_version.json", {})
        self.assertEqual(schema["schema_version"], config.SCHEMA_VERSION)
        self.assertEqual(schema["app_version"], config.APP_VERSION)

    def test_data_root_follows_env(self) -> None:
        self.assertEqual(store.data_root(), self.root.resolve())


class JsonIoTest(StoreTestCase):
    def test_atomic_write_and_read(self) -> None:
        path = self.root / "bars" / "sh510300.json"
        store.write_json(path, {"code": "sh510300", "bars": [1, 2]})
        self.assertEqual(store.read_json(path, None), {"code": "sh510300", "bars": [1, 2]})
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_read_missing_and_corrupt(self) -> None:
        self.assertEqual(store.read_json(self.root / "missing.json", "fallback"), "fallback")
        bad = self.root / "broken.json"
        bad.write_text("{not json", encoding="utf-8")
        self.assertEqual(store.read_json(bad, {}), {})

    def test_update_json(self) -> None:
        path = self.root / "counter.json"
        store.update_json(path, {"n": 1}, lambda data: {**data, "n": data["n"] + 1})
        store.update_json(path, {"n": 1}, lambda data: {**data, "n": data["n"] + 1})
        self.assertEqual(store.read_json(path, None), {"n": 3})

    def test_write_rejects_nan(self) -> None:
        with self.assertRaises(ValueError):
            store.write_json(self.root / "nan.json", {"value": float("nan")})


class MergeBarsTest(unittest.TestCase):
    @staticmethod
    def bar(date: str, close: float) -> dict[str, float | str]:
        return {"date": date, "open": close, "high": close, "low": close, "close": close, "volume": 100.0}

    def test_append_only(self) -> None:
        existing = [self.bar("2026-11-04", 4.0), self.bar("2026-11-05", 4.1)]
        fresh = [self.bar("2026-11-05", 4.1), self.bar("2026-11-06", 4.2)]
        bars, added, warnings = store.merge_bars(existing, fresh)
        self.assertEqual(added, 1)
        self.assertEqual(warnings, [])
        self.assertEqual([row["date"] for row in bars], ["2026-11-04", "2026-11-05", "2026-11-06"])
        self.assertEqual(bars[-1]["close"], 4.2)

    def test_never_overwrites_history(self) -> None:
        existing = [self.bar("2026-11-05", 4.1)]
        fresh = [self.bar("2026-11-05", 9.9)]
        bars, added, warnings = store.merge_bars(existing, fresh)
        self.assertEqual(added, 0)
        self.assertEqual(bars[0]["close"], 4.1)
        # 0.5% 容差内视为同一根 K 线，不算复权变化
        bars, added, warnings = store.merge_bars(existing, [self.bar("2026-11-05", 4.12)])
        self.assertEqual(added, 0)
        self.assertEqual(warnings, [])

    def test_adjustment_mismatch_warns(self) -> None:
        existing = [self.bar("2026-11-05", 4.1)]
        fresh = [self.bar("2026-11-05", 3.2), self.bar("2026-11-06", 3.3)]
        bars, added, warnings = store.merge_bars(existing, fresh)
        self.assertEqual(added, 0)
        self.assertTrue(warnings)
        self.assertIn("复权", warnings[0])

    def test_invalid_rows_skipped(self) -> None:
        fresh = [
            {"date": "2026-11-06", "open": 0, "high": 1, "low": 1, "close": 1},
            {"date": "", "open": 1, "high": 1, "low": 1, "close": 1},
            self.bar("2026-11-07", 4.4),
        ]
        bars, added, _ = store.merge_bars([], fresh)
        self.assertEqual(added, 1)
        self.assertEqual(bars[0]["date"], "2026-11-07")

    def test_sorted_output(self) -> None:
        fresh = [self.bar("2026-11-06", 4.2), self.bar("2026-11-04", 4.0)]
        bars, added, _ = store.merge_bars([], fresh)
        self.assertEqual(added, 2)
        self.assertEqual([row["date"] for row in bars], ["2026-11-04", "2026-11-06"])


class DailyAndWatchlistTest(StoreTestCase):
    def test_daily_defaults(self) -> None:
        daily = store.load_daily()
        self.assertEqual(daily["days"], {})
        self.assertEqual(daily["intraday"], {})

    def test_daily_roundtrip(self) -> None:
        store.save_daily({"days": {"2026-11-06": {"sh510300": {"shares": 1.0, "price": 4.1}}}, "intraday": {}})
        self.assertIn("2026-11-06", store.load_daily()["days"])

    def test_watchlist_default_created(self) -> None:
        payload = store.load_watchlist()
        codes = [item["code"] for item in payload["items"]]
        self.assertIn("sh510300", codes)
        self.assertEqual(len(codes), len(config.DEFAULT_WATCHLIST))

    def test_bars_roundtrip(self) -> None:
        store.save_bars("sh510300", [{"date": "2026-11-06", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}])
        bars = store.load_bars("sh510300")
        self.assertEqual(len(bars), 1)
        payload = store.load_bars_payload("sh510300")
        self.assertEqual(payload["adjustment"], "qfq")

    def test_hotspot_history_prune(self) -> None:
        for index in range(3):
            store.save_hotspots_history({"date": f"2026-11-0{index + 1}", "rows": []})
        self.assertEqual(len(store.load_hotspots_history()), 3)


class StateTest(StoreTestCase):
    def test_state_update_and_tasks(self) -> None:
        store.update_state(lambda state: {**state, "flag": True})
        self.assertTrue(store.load_state()["flag"])
        for index in range(3):
            store.append_task("t", ok=True, detail=str(index))
        tasks = store.load_state()["tasks"]
        self.assertEqual(len(tasks), 3)
        self.assertEqual(tasks[-1]["detail"], "2")

    def test_names_and_source(self) -> None:
        store.set_names({"sh510300": "沪深300ETF华泰柏瑞"})
        self.assertEqual(store.load_names()["sh510300"], "沪深300ETF华泰柏瑞")
        entry = store.record_source("tencent_quote", ok=True, latency_ms=12)
        self.assertEqual(entry["id"], "tencent_quote")
        sources = store.load_state()["sources"]
        self.assertEqual(len(sources), 1)
        store.record_source("tencent_quote", ok=False)
        sources = store.load_state()["sources"]
        self.assertEqual(len(sources), 1)
        self.assertFalse(sources[0]["ok"])

    def test_quotes_snapshot(self) -> None:
        store.update_quotes_snapshot({"sh510300": {"price": 4.1}}, "2026-11-06T15:00:00+08:00")
        snapshot = store.load_quotes_snapshot()
        self.assertEqual(snapshot["quotes"]["sh510300"]["price"], 4.1)
        self.assertEqual(snapshot["fetched_at"], "2026-11-06T15:00:00+08:00")


class ShippedDataDirsTest(unittest.TestCase):
    def test_gitignore_covers_data(self) -> None:
        """server/data 是本地数据，不应入库（server/.gitignore 负责）。"""
        ignore = Path(__file__).resolve().parents[1] / ".gitignore"
        self.assertTrue(ignore.is_file(), "缺少 server/.gitignore")
        self.assertIn("data/", ignore.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
