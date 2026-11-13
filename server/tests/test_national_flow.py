"""national_flow 的离线单元测试：不联网、不读真实数据。"""
import json
import tempfile
import unittest
from pathlib import Path

from etfmon import national_flow


class StreakTests(unittest.TestCase):
    def test_counts_trailing_same_sign(self):
        self.assertEqual(national_flow._streak([1, -1, 2, 3, 4]), (3, 'inflow'))
        self.assertEqual(national_flow._streak([1, 2, -3, -4]), (2, 'outflow'))

    def test_zero_breaks_nothing_but_reports_none(self):
        self.assertEqual(national_flow._streak([1, 2, 0]), (0, 'none'))
        self.assertEqual(national_flow._streak([]), (0, 'none'))


class TotalsTests(unittest.TestCase):
    def test_ignores_days_with_poor_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sse = root / 'share_history' / 'sse'
            sse.mkdir(parents=True)
            codes = ['sh510300', 'sh510500', 'sh510880', 'sh510050']

            def write(day, members):
                rows = {c: {'shares': 1000.0 * (i + 1)} for i, c in enumerate(members)}
                (sse / f'{day}.json').write_text(
                    json.dumps({'date': day, 'rows': rows}), encoding='utf-8')

            write('2026-01-05', codes)          # 完整
            write('2026-01-06', codes[:1])      # 覆盖太少，应被丢弃
            write('2026-01-07', codes)          # 完整

            original = national_flow._share_dir
            national_flow._share_dir = lambda: sse
            try:
                totals = national_flow.load_daily_totals(codes, min_coverage=0.6)
            finally:
                national_flow._share_dir = original

            self.assertEqual([d for d, _, _ in totals], ['2026-01-05', '2026-01-07'])


class SplitDetectionTests(unittest.TestCase):
    def test_no_splits_when_shares_are_flat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sse = root / 'share_history' / 'sse'
            sse.mkdir(parents=True)
            codes = ['sh510300']
            for day, shares in (('2026-01-05', 1000.0), ('2026-01-06', 1010.0)):
                (sse / f'{day}.json').write_text(
                    json.dumps({'date': day, 'rows': {codes[0]: {'shares': shares}}}),
                    encoding='utf-8')

            original = national_flow._share_dir
            national_flow._share_dir = lambda: sse
            try:
                found = national_flow.detect_splits(['2026-01-05', '2026-01-06'], codes)
            finally:
                national_flow._share_dir = original

            # 价格数据缺失时不臆断拆分；份额也没暴增
            self.assertEqual(found, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
