"""采集层单测：盘中样本自愈、迁移数据不导入旧盘中样本。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from etfmon import config, store
from etfmon.collectors import reconcile_intraday_samples


def daily_payload() -> dict:
    """两个相邻交易日 + 一天跨日缺口，份额为「份」。"""
    return {
        "days": {
            "2026-11-04": {"sh510300": {"shares": 99_800_000_000.0, "price": 4.0}},
            "2026-11-05": {"sh510300": {"shares": 100_000_000_000.0, "price": 4.0}},
            "2026-11-06": {"sh510300": {"shares": 100_300_000_000.0, "price": 4.0}},
        },
        "intraday": {
            "2026-11-05": [
                {"time": "09:32:00", "amount": 400_000_000.0, "share_delta": 100_000_000.0},
                {"time": "15:00:00", "amount": 800_000_000.0, "share_delta": 200_000_000.0},
            ],
            "2026-11-06": [
                {"time": "09:32:00", "amount": 900_000_000.0, "share_delta": 200_000_000.0},
                # 与最终份额差 300_000_000 不一致（旧份额基线）
                {"time": "15:00:00", "amount": 1_000_000_000.0, "share_delta": 250_000_000.0},
            ],
        },
        "updated_at": "2026-11-06T15:10:00+08:00",
    }


class ReconcileIntradayTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {"ETF_DATA": self._tmp.name})
        self._env.start()
        store.ensure_layout()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def test_removes_inconsistent_day_only(self) -> None:
        daily = daily_payload()
        result = reconcile_intraday_samples(daily, persist=False)
        self.assertEqual(result["removed_days"], ["2026-11-06"])
        self.assertEqual(result["removed_samples"], 2)
        self.assertIn("2026-11-06", result["warnings"][0])
        # 11-05 的最后样本与当日最终份额差一致（2 亿份），保留
        self.assertIn("2026-11-05", daily["intraday"])
        self.assertNotIn("2026-11-06", daily["intraday"])

    def test_normal_close_path_not_removed(self) -> None:
        daily = daily_payload()
        daily["intraday"]["2026-11-06"] = [
            {"time": "15:00:00", "amount": 1_200_000_000.0, "share_delta": 300_000_000.0}
        ]
        result = reconcile_intraday_samples(daily, persist=False)
        self.assertEqual(result["removed_days"], [])
        self.assertEqual(len(daily["intraday"]), 2)
        self.assertIn("2026-11-06", daily["intraday"])

    def test_small_flow_skipped(self) -> None:
        daily = daily_payload()
        daily["days"]["2026-11-06"]["sh510300"]["shares"] = 100_000_500_000.0  # +50 万份，很小
        daily["intraday"]["2026-11-06"] = [
            {"time": "15:00:00", "amount": 1_000_000.0, "share_delta": 0.0}
        ]
        result = reconcile_intraday_samples(daily, persist=False)
        self.assertEqual(result["removed_days"], [])
        self.assertIn("2026-11-06", daily["intraday"])

    def test_persist_and_state_warning(self) -> None:
        store.save_daily(daily_payload())
        result = reconcile_intraday_samples()
        self.assertEqual(result["removed_days"], ["2026-11-06"])
        self.assertNotIn("2026-11-06", store.load_daily()["intraday"])
        warnings = store.load_state().get("data_warnings") or {}
        self.assertIn("2026-11-06", warnings)
        self.assertIn("已作废", warnings["2026-11-06"])
        # 幂等：再跑一次不会重复删/重复告警
        again = reconcile_intraday_samples()
        self.assertEqual(again["removed_days"], [])

    def test_missing_previous_day_not_judged(self) -> None:
        daily = {
            "days": {"2026-11-06": {"sh510300": {"shares": 100_000_000_000.0, "price": 4.0}}},
            "intraday": {"2026-11-06": [{"time": "15:00:00", "amount": 1.0, "share_delta": 123.0}]},
        }
        result = reconcile_intraday_samples(daily, persist=False)
        self.assertEqual(result["removed_days"], [])


class LegacyImportTest(unittest.TestCase):
    def _write_legacy(self, legacy_dir: Path) -> None:
        national = legacy_dir / "national"
        national.mkdir(parents=True)
        payload = {
            "days": {
                "2026-10-27": {"sh510300": {"shares": 100.0, "price": 4.0}},
                "2026-11-06": {"sh510300": {"shares": 110.0, "price": 4.1}},
            },
            "intraday": {
                "2026-11-06": [{"time": "15:00:00", "amount": 1.0, "share_delta": 1.0}],
            },
            "updated_at": "2026-11-06T15:10:00+08:00",
        }
        (national / "daily.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_daily_imports_but_intraday_skipped(self) -> None:
        from etfmon import __main__ as cli

        with tempfile.TemporaryDirectory() as legacy_tmp, tempfile.TemporaryDirectory() as data_tmp:
            legacy_dir = Path(legacy_tmp)
            self._write_legacy(legacy_dir)
            with mock.patch.dict(os.environ, {"ETF_DATA": data_tmp}):
                store.ensure_layout()
                result = cli._merge_legacy_daily(legacy_dir)
                daily = store.load_daily()
        self.assertEqual(result["days"], 2)
        self.assertEqual(result["intraday_days_skipped"], 1)
        self.assertIn("2026-11-06", daily["days"])
        self.assertEqual(daily["intraday"], {})  # 旧盘中样本被丢弃


class IntradaySampleUnitTest(unittest.TestCase):
    def test_team_codes_basket_totals_used(self) -> None:
        """确认自愈用的是篮子合计份额差，而不是单只基金。"""
        daily = {
            "days": {
                "2026-11-05": {
                    "sh510300": {"shares": 100_000_000_000.0, "price": 4.0},
                    "sh510050": {"shares": 50_000_000_000.0, "price": 2.0},
                },
                "2026-11-06": {
                    "sh510300": {"shares": 100_300_000_000.0, "price": 4.0},
                    "sh510050": {"shares": 49_700_000_000.0, "price": 2.0},
                },
            },
            "intraday": {"2026-11-06": [{"time": "15:00:00", "amount": 1.0, "share_delta": 0.0}]},
        }
        result = reconcile_intraday_samples(daily, persist=False)
        # 合计份额差 0（3 亿 - 3 亿），与样本 0 一致 → 不应删除
        self.assertEqual(result["removed_days"], [])
        self.assertEqual(len(config.TEAM_CODES), 26)


if __name__ == "__main__":
    unittest.main()
