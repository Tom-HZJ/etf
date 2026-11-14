"""红利低波定投台账的离线测试。"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

from etfmon import dca, store


def make_bars(start='2026-01-05', days=60, price=1.0):
    """生成逐日小幅上升的日线，跳过周末。"""
    out = []
    day = datetime.fromisoformat(start)
    while len(out) < days:
        if day.weekday() < 5:
            out.append({'date': day.date().isoformat(),
                        'open': price, 'high': price, 'low': price,
                        'close': round(price, 4), 'volume': 1e6})
            price *= 1.001
        day += timedelta(days=1)
    return out


class DcaTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ, {'ETF_DATA': self._tmp.name})
        self._env.start()
        # 固定标的为测试代码，避开真实名称解析
        mock.patch.object(dca.config, 'SHANGHAI_TZ', dca.config.SHANGHAI_TZ).start()

    def tearDown(self):
        mock.patch.stopall()
        self._env.stop()
        self._tmp.cleanup()

    def _seed(self, bars):
        store.save_bars('sh512890', bars, name='红利低波ETF')

    def test_weekly_entries_follow_the_configured_weekday(self):
        self._seed(make_bars())
        dca.save_config(amount=10_000.0, weekday=0, start='2026-01-05')
        added = dca.run_due(datetime.fromisoformat('2026-01-26T18:00:00+08:00'))
        self.assertGreaterEqual(added, 3)
        for entry in dca.build_payload()['entries']:
            weekday = datetime.fromisoformat(entry['date']).weekday()
            self.assertEqual(weekday, 0, f"{entry['date']} 不是周一")

    def test_rerun_is_idempotent(self):
        self._seed(make_bars())
        dca.save_config(amount=10_000.0, weekday=0, start='2026-01-05')
        now = datetime.fromisoformat('2026-02-02T18:00:00+08:00')
        first = dca.run_due(now)
        second = dca.run_due(now)
        self.assertGreater(first, 0)
        self.assertEqual(second, 0, '重复运行不应新增记录')

    def test_quantity_is_whole_lots_and_never_exceeds_amount(self):
        self._seed(make_bars(price=1.2345))
        dca.save_config(amount=10_000.0, weekday=0, start='2026-01-05')
        dca.run_due(datetime.fromisoformat('2026-01-26T18:00:00+08:00'))
        for entry in dca.build_payload()['entries']:
            self.assertEqual(entry['quantity'] % 100, 0)
            self.assertLessEqual(entry['cost'], entry['amount'] + 1e-6)
            self.assertLessEqual(entry['cost'], 10_000.0)

    def test_summary_matches_entries(self):
        self._seed(make_bars())
        dca.save_config(amount=10_000.0, weekday=0, start='2026-01-05')
        dca.run_due(datetime.fromisoformat('2026-02-02T18:00:00+08:00'))
        payload = dca.build_payload()
        summary = payload['summary']
        entries = payload['entries']
        self.assertEqual(summary['count'], len(entries))
        self.assertEqual(summary['total_quantity'], sum(e['quantity'] for e in entries))
        self.assertAlmostEqual(summary['total_cost'], sum(e['cost'] for e in entries), places=2)
        # 均价应在各期价格区间内
        prices = [e['price'] for e in entries]
        if prices:
            self.assertGreaterEqual(summary['average_cost'], min(prices) - 1e-4)
            self.assertLessEqual(summary['average_cost'], max(prices) + 1e-4)

    def test_missing_bars_produces_no_entries(self):
        dca.save_config(amount=10_000.0, weekday=0, start='2026-01-05')
        added = dca.run_due(datetime.fromisoformat('2026-02-02T18:00:00+08:00'))
        self.assertEqual(added, 0)
        payload = dca.build_payload()
        self.assertEqual(payload['summary']['count'], 0)
        self.assertIsNone(payload['summary']['market_value'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
