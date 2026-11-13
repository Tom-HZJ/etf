"""Turnover evidence: fixed members, causal baselines and observed windows."""
from __future__ import annotations

import copy
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

from etfmon import config, store, turnover


def stamp(clock: str, day: str = "2026-11-11") -> datetime:
    return datetime.fromisoformat(f"{day}T{clock}+08:00")


def quotes(moment: datetime, amount: float = 100_000) -> dict:
    return {c: {"quote_time": moment.isoformat(), "amount": amount, "stale": False} for c in config.TEAM_CODES}


def series(scale: float = 1) -> dict:
    return {c: {t: (i + 1) * 1000 * scale for i, t in enumerate(turnover.CLOCKS)} for c in config.TEAM_CODES}


class TurnoverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"ETF_DATA": self.temp.name})
        self.env.start()
        self.now = stamp("13:10:20")

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def save(self, day: str, data: dict) -> None:
        store.write_json(store.path_for("national", "turnover", f"{day}.json"),
                         {"version": turnover.VERSION, "date": day, "series": data})

    def history(self, count: int) -> list[str]:
        day = self.now.date() - timedelta(days=1)
        result = []
        while len(result) < count:
            if day.weekday() < 5:
                text = day.isoformat()
                self.save(text, series())
                result.append(text)
            day -= timedelta(days=1)
        return result

    def view(self, data: dict | None = None) -> dict:
        return turnover.build_view(data if data is not None else quotes(self.now), "2026-11-11", self.now)

    def test_no_history_is_null_not_zero_and_get_never_writes(self) -> None:
        with mock.patch.object(store, "write_json", side_effect=AssertionError("GET wrote data")):
            result = self.view()
        self.assertEqual(result["total_amount"], 26 * 100_000)
        self.assertEqual(result["baseline_days"], 0)
        self.assertIsNone(result["ratio"])
        self.assertIsNone(result["one_minute"])

    def test_19_days_cannot_produce_ratio_or_projection(self) -> None:
        self.history(19)
        self.save("2026-11-11", series(2))
        result = self.view()
        self.assertEqual(result["baseline_days"], 19)
        self.assertIsNone(result["ratio"])
        self.assertIsNone(result["projected_close_amount"])

    def test_same_minute_baseline_and_projection_ignore_today_future_and_incomplete_day(self) -> None:
        dates = self.history(20)
        self.save("2026-11-11", series(2))
        self.save("2026-11-12", series(1000))
        result = self.view()
        self.assertAlmostEqual(result["ratio"], 2)
        self.assertEqual(result["level"], "volume")
        self.assertEqual(result["minute_time"], "2026-11-11T13:09:00+08:00")
        self.assertAlmostEqual(result["projected_close_amount"], 26 * 241_000 * 2)
        self.assertIsNone(next(p for p in result["curve"] if p["time"] == "13:10")["amount"])
        broken = series()
        broken.pop(config.TEAM_CODES[0])
        self.save(dates[0], broken)
        self.assertEqual(self.view()["baseline_days"], 19)
        self.assertIsNone(self.view()["ratio"])

    def test_fixed_basket_withholds_incomplete_minutes_and_rejects_nonfinite_history(self) -> None:
        dates = self.history(20)
        today = series(2)
        today[config.TEAM_CODES[0]].pop("13:09")
        self.save("2026-11-11", today)
        result = self.view()
        self.assertIsNone(next(p for p in result["curve"] if p["time"] == "13:09")["amount"])
        self.assertEqual(result["minute_time"], "2026-11-11T13:08:00+08:00")
        broken = series()
        broken[config.TEAM_CODES[0]]["10:00"] = None
        self.save(dates[0], broken)
        self.assertEqual(self.view()["baseline_days"], 19)

    def test_quote_coverage_staleness_future_and_nonfinite_are_not_zero(self) -> None:
        for field, value in (("amount", float("nan")), ("amount", -1), ("quote_time", "2026-11-11T13:20:00+08:00"),
                             ("quote_time", "2026-11-10T13:10:00+08:00")):
            data = quotes(self.now)
            data[config.TEAM_CODES[0]][field] = value
            result = self.view(data)
            self.assertEqual(result["coverage"], 25)
            self.assertIsNone(result["total_amount"])
            self.assertEqual(result["state"], "stale")
        result = self.view(quotes(self.now - timedelta(seconds=61)))
        self.assertEqual(result["coverage"], 0)

    def test_samples_survive_restart_and_windows_use_real_source_times(self) -> None:
        start = stamp("13:04:00")
        for i in range(20):
            moment = start + timedelta(seconds=i * 20)
            turnover.sample_quotes(quotes(moment, 100_000 + i * 100), moment)
            turnover.sample_quotes(quotes(moment, 100_000 + i * 100), moment)  # idempotent
        self.now = moment
        result = self.view()
        self.assertEqual(result["one_minute"]["amount"], 26 * 300)
        self.assertEqual(result["five_minute"]["amount"], 26 * 1500)
        self.assertEqual(result["five_minute"]["elapsed_seconds"], 300)
        self.assertEqual(len(store.read_json(store.path_for("national", "turnover_samples", "2026-11-11.json"), {})["points"]), 20)

    def test_windows_reset_on_gap_lunch_and_amount_correction(self) -> None:
        for clock, amount in (("11:29:40", 100), ("13:00:00", 200), ("13:00:20", 220), ("13:01:00", 230)):
            moment = stamp(clock)
            turnover.sample_quotes(quotes(moment, amount), moment)
        self.now = moment
        self.assertIsNotNone(self.view()["one_minute"])
        self.assertIsNone(self.view()["five_minute"])
        moment = stamp("13:01:20")
        turnover.sample_quotes(quotes(moment, 210), moment)
        self.now = moment
        self.assertIsNone(self.view()["one_minute"])
        moment = stamp("13:05:20")
        turnover.sample_quotes(quotes(moment, 300), moment)
        self.now = stamp("13:06:20")
        turnover.sample_quotes(quotes(self.now, 400), self.now)
        self.assertIsNone(self.view()["one_minute"])

    def test_no_samples_outside_continuous_session_or_with_partial_basket(self) -> None:
        for clock in ("09:20:00", "12:00:00", "15:10:00"):
            moment = stamp(clock)
            turnover.sample_quotes(quotes(moment), moment)
        partial = quotes(self.now)
        partial.pop(config.TEAM_CODES[0])
        turnover.sample_quotes(partial, self.now)
        self.assertFalse(store.path_for("national", "turnover_samples", "2026-11-11.json").exists())

    def test_stale_minute_disables_projection(self) -> None:
        self.history(20)
        today = series()
        today = {c: {t: v for t, v in s.items() if t <= "13:01"} for c, s in today.items()}
        self.save("2026-11-11", today)
        result = self.view()
        self.assertTrue(result["minute_stale"])
        self.assertIsNone(result["ratio"])
        self.assertIsNone(result["projected_close_amount"])

    def test_close_prediction_becomes_actual_only(self) -> None:
        self.save("2026-11-11", series(2))
        self.now = stamp("15:10:00")
        result = self.view(quotes(stamp("15:00:00")))
        self.assertEqual(result["state"], "closed")
        self.assertEqual(result["projected_close_amount"], 26 * 241_000 * 2)

    def test_legacy_reconstruction_and_invalid_duplicates_gaps_resets(self) -> None:
        points = [{"time": "09:30", "volume": 10, "avg": 2}, {"time": "09:31", "volume": 5, "avg": 2.2}]
        self.assertEqual(turnover.normalize_day(points), {"09:30": 2000, "09:31": 3300})
        self.assertEqual(turnover.normalize_day([points[0], points[0], points[1]])["09:31"], 3300)
        conflict = copy.deepcopy(points)
        conflict.append({**points[1], "volume": 6})
        self.assertIsNone(turnover.normalize_day(conflict))
        self.assertIsNone(turnover.normalize_day(points[1:]))
        self.assertIsNone(turnover.normalize_day([points[0], {**points[1], "avg": 1}]))
        self.assertIsNone(turnover.normalize_day([{**points[0], "cumulative_amount": None}]))

    def test_archiving_preserves_full_day_when_provider_truncates(self) -> None:
        code = config.TEAM_CODES[0]
        points = [{"time": t, "volume": 1, "cumulative_amount": (i + 1) * 100} for i, t in enumerate(turnover.CLOCKS)]
        payload = {"code": code, "days": [{"date": "2026-11-10", "points": points}]}
        turnover.archive_intraday(payload, self.now)
        payload["days"][0]["points"] = points[:10]
        turnover.archive_intraday(payload, self.now)
        self.assertEqual(len(turnover._load_day("2026-11-10")[code]), 241)
        payload["days"][0]["date"] = "2026-11-12"
        turnover.archive_intraday(payload, self.now)
        self.assertEqual(turnover._load_day("2026-11-12"), {})

    def test_comparison_requires_causal_history_and_exact_verified_share_date(self) -> None:
        dates = self.history(22)
        ordered = sorted(dates)
        archive = [(d, turnover._load_day(d)) for d in ordered]
        archive[-1] = (ordered[-1], series(2))
        official = [{"date": ordered[-1], "verified_members": 26, "gap_days": 1, "share_delta": 100},
                    {"date": ordered[-2], "verified_members": 25, "gap_days": 1, "share_delta": 999}]
        result = turnover.compare_closed_days(archive, official)
        self.assertEqual(len(result["rows"]), 2)
        self.assertIsNone(result["rows"][0]["share_delta"])
        self.assertEqual(result["rows"][1]["peak_ratio"], 2)
        self.assertEqual(result["volume_days_verified"], 1)
        self.assertEqual(result["volume_days_increased"], 1)
        official[0]["gap_days"] = 2
        self.assertEqual(turnover.compare_closed_days(archive, official)["volume_days_verified"], 0)

    def test_backfilled_5m_baseline_has_exact_same_clock_and_never_fills_minutes(self) -> None:
        dates = self.history(20)
        for day in dates:
            data = {c: {t: v for t, v in s.items() if t in turnover.REFERENCE_CLOCKS} for c, s in series().items()}
            store.write_json(store.path_for("national", "turnover_5m", f"{day}.json"),
                             {"version": 1, "interval": 5, "unit": "CNY", "series": data})
        self.save("2026-11-11", series(2))
        result = self.view()
        self.assertEqual(result["baseline_interval"], 5)
        self.assertEqual(result["comparison_time"], "2026-11-11T13:05:00+08:00")
        self.assertAlmostEqual(result["ratio"], 2)
        self.assertIsNone(next(p for p in result["curve"] if p["time"] == "13:06")["expected"])

    def test_raw_backfill_rejects_missing_conflicting_and_invalid_amounts(self) -> None:
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location("backfill_turnover", Path(__file__).resolve().parents[2] / "scripts/backfill_turnover.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        bars = [{"date": "2026-11-10", "time": f"2026-11-10 {t}", "amount": 100} for t in turnover.REFERENCE_CLOCKS]
        self.assertEqual(module.cumulative_days(bars)["2026-11-10"]["15:00"], 4800)
        self.assertEqual(module.cumulative_days(bars[1:]), {})
        self.assertEqual(module.cumulative_days([*bars, {**bars[0], "amount": 101}]), {})
        self.assertEqual(module.cumulative_days([{**b, "amount": float("nan")} for b in bars]), {})

    def test_missing_recent_session_cannot_be_replaced_with_an_older_good_day(self) -> None:
        dates = self.history(22)
        self.save("2026-11-11", series(2))
        broken = series()
        broken[config.TEAM_CODES[0]].pop("10:00")
        self.save(dates[0], broken)
        result = self.view()
        self.assertEqual(result["baseline_days"], 19)
        self.assertIsNone(result["ratio"])
        store.path_for("national", "turnover", f"{dates[0]}.json").unlink()
        result = turnover.build_view(quotes(self.now), "2026-11-11", self.now, calendar=dates)
        self.assertEqual(result["baseline_days"], 19)
        self.assertIsNone(result["ratio"])


if __name__ == "__main__":
    unittest.main()
