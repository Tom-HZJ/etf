import os
import unittest
from datetime import datetime
from tempfile import TemporaryDirectory
from unittest.mock import patch

from etfmon import stock_forecasts


class ForecastTrackingTest(unittest.TestCase):
    def test_archive_is_immutable_and_scores_only_matching_actual_dates(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {'ETF_DATA': directory}):
            issue = {
                'code': 'sz002258', 'adjustment': 'qfq', 'as_of': '2026-11-10',
                'generated_at': '2026-11-10T15:20:00+08:00', 'baseline_close': 15,
                'sessions': [{'date': '2026-11-11', 'close': 16}, {'date': '2026-11-12', 'close': 17}],
            }
            stock_forecasts.archive_issue(issue)
            stock_forecasts.archive_issue({**issue, 'baseline_close': 99})
            stock_forecasts.settle([
                {'date': '2026-11-10', 'close': 12}, {'date': '2026-11-11', 'close': 13.6},
            ])
            stock_forecasts.settle([
                {'date': '2026-11-10', 'close': 12}, {'date': '2026-11-11', 'close': 20},
            ])
            report = stock_forecasts.report('2026-11-11')
            self.assertEqual(report['latest'], issue)
            first, second = report['rows']
            self.assertEqual(first['actual'], 13.6)
            self.assertEqual(first['comparable'], 17)
            self.assertEqual(second['status'], 'pending')
            self.assertEqual(report['metrics'][0], {'horizon': 1, 'count': 1, 'mae': 1, 'baseline_mae': 2})
            self.assertEqual(report['metrics'][1]['count'], 0)
            with self.assertRaises(ValueError):
                stock_forecasts.archive_issue({**issue, 'generated_at': '2026-11-11T15:20:00+08:00'})

    def test_intraday_waits_for_a_closed_bar_and_never_connects_across_lunch(self):
        def window(clock):
            return stock_forecasts.minute_window(datetime.fromisoformat(f'2026-11-11T{clock}+08:00'))

        self.assertIsNone(window('09:35:29'))
        self.assertEqual(window('09:35:40'), ('2026-11-11 09:35', [
            '2026-11-11 09:40', '2026-11-11 09:45', '2026-11-11 09:50',
        ]))
        self.assertEqual(window('11:25:40')[1], ['2026-11-11 11:30'])
        self.assertIsNone(window('11:30:40'))
        self.assertIsNone(window('13:00:40'))
        self.assertEqual(window('13:05:40')[0], '2026-11-11 13:05')
        self.assertIsNone(window('15:00:40'))
