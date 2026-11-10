"""交易时段、交易分钟与时间工具的单测。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from etfmon import config, store


def shanghai(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime:
    """构造 Asia/Shanghai 时间。2026-11-06 是周五，2026-11-07 是周六。"""
    return datetime(year, month, day, hour, minute, second, tzinfo=config.SHANGHAI_TZ)


class TimeZoneTest(unittest.TestCase):
    def test_fixed_offset_matches_shanghai(self) -> None:
        self.assertEqual(config.SHANGHAI_TZ.utcoffset(None), timedelta(hours=8))

    def test_iso_roundtrip(self) -> None:
        text = store.iso(shanghai(2026, 11, 6, 15, 10, 11))
        self.assertEqual(text, "2026-11-06T15:10:11+08:00")
        parsed = store.parse_iso(text)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.hour, 15)
        self.assertEqual(parsed.utcoffset(), timedelta(hours=8))

    def test_parse_iso_invalid(self) -> None:
        self.assertIsNone(store.parse_iso(None))
        self.assertIsNone(store.parse_iso("not-a-time"))


class SessionTest(unittest.TestCase):
    def test_weekend(self) -> None:
        self.assertEqual(store.session_of(shanghai(2026, 11, 7, 10, 0)), "weekend")
        self.assertFalse(store.is_trading_day(shanghai(2026, 11, 8, 10, 0)))

    def test_pre_market(self) -> None:
        self.assertEqual(store.session_of(shanghai(2026, 11, 6, 8, 0)), "pre")
        self.assertEqual(store.session_of(shanghai(2026, 11, 6, 9, 29)), "pre")

    def test_morning_boundaries(self) -> None:
        self.assertEqual(store.session_of(shanghai(2026, 11, 6, 9, 30)), "morning")
        self.assertEqual(store.session_of(shanghai(2026, 11, 6, 11, 29)), "morning")
        self.assertEqual(store.session_of(shanghai(2026, 11, 6, 11, 30)), "morning")

    def test_lunch(self) -> None:
        self.assertEqual(store.session_of(shanghai(2026, 11, 6, 11, 31)), "lunch")
        self.assertEqual(store.session_of(shanghai(2026, 11, 6, 12, 59)), "lunch")
        self.assertFalse(store.is_trading_time(shanghai(2026, 11, 6, 12, 0)))

    def test_afternoon_and_post(self) -> None:
        self.assertEqual(store.session_of(shanghai(2026, 11, 6, 13, 0)), "afternoon")
        self.assertEqual(store.session_of(shanghai(2026, 11, 6, 15, 0)), "afternoon")
        self.assertTrue(store.is_trading_time(shanghai(2026, 11, 6, 15, 0)))
        self.assertEqual(store.session_of(shanghai(2026, 11, 6, 15, 1)), "post")
        self.assertEqual(store.session_of(shanghai(2026, 11, 6, 23, 59)), "post")

    def test_session_minute(self) -> None:
        self.assertEqual(store.session_minute(9, 30), 0)
        self.assertEqual(store.session_minute(11, 30), 120)
        self.assertEqual(store.session_minute(13, 0), 121)
        self.assertEqual(store.session_minute(15, 0), 241)
        self.assertEqual(store.session_minute(12, 0), None)
        self.assertEqual(store.session_minute(15, 1), None)
        self.assertEqual(config.DAY_SESSION_MINUTES, 242)

    def test_market_data_final(self) -> None:
        self.assertFalse(store.market_data_is_final(shanghai(2026, 11, 6, 15, 9)))
        self.assertTrue(store.market_data_is_final(shanghai(2026, 11, 6, 15, 10)))
        self.assertTrue(store.market_data_is_final(shanghai(2026, 11, 7, 9, 0)))

    def test_quote_window(self) -> None:
        self.assertFalse(store.is_quote_window(shanghai(2026, 11, 6, 9, 0)))
        self.assertTrue(store.is_quote_window(shanghai(2026, 11, 6, 9, 15)))
        self.assertTrue(store.is_quote_window(shanghai(2026, 11, 6, 15, 10)))
        self.assertFalse(store.is_quote_window(shanghai(2026, 11, 6, 15, 11)))
        self.assertFalse(store.is_quote_window(shanghai(2026, 11, 7, 10, 0)))

    def test_trade_date(self) -> None:
        self.assertEqual(store.trade_date_of(shanghai(2026, 11, 6, 23, 0)), "2026-11-06")

    def test_utc_input_projects_to_shanghai(self) -> None:
        moment = datetime(2026, 11, 6, 1, 0, tzinfo=timezone.utc)  # 09:00 上海
        self.assertEqual(store.session_of(moment), "pre")


if __name__ == "__main__":
    unittest.main()
