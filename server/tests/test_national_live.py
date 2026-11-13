"""national_live 的离线单元测试。"""
import json
import tempfile
import unittest
from pathlib import Path

from etfmon import national_live


def write_day(directory: Path, day: str, points: list[dict]) -> None:
    (directory / f'{day}.json').write_text(
        json.dumps({'version': 1, 'points': points}), encoding='utf-8')


def point(clock: str, **amounts) -> dict:
    return {'time': f'2026-11-13T{clock}+08:00', 'amounts': {k: float(v) for k, v in amounts.items()}}


class LiveMonitorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / 'national'
        self.samples = self.root / 'turnover_samples'
        self.samples.mkdir(parents=True)
        self._orig = national_live._root
        national_live._root = lambda: self.root
        self._orig_codes = national_live.config.TEAM_CODES
        national_live.config.TEAM_CODES = ['sh510300']

    def tearDown(self):
        national_live._root = self._orig
        national_live.config.TEAM_CODES = self._orig_codes
        self._tmp.cleanup()

    def test_baseline_uses_same_clock_not_full_day(self):
        # 过去两天在 10:00 时累计成交 100，收盘时 500
        write_day(self.samples, '2026-11-11', [
            point('10:00:00', sh510300=100), point('15:00:00', sh510300=500)])
        write_day(self.samples, '2026-11-12', [
            point('10:00:00', sh510300=100), point('15:00:00', sh510300=500)])
        # 今天只到 10:00，成交 200 -> 比率应为 2.0（而不是与 500 比）
        write_day(self.samples, '2026-11-13', [point('10:00:00', sh510300=200)])

        payload = national_live.build()
        self.assertEqual(payload['state'], 'ok')
        self.assertAlmostEqual(payload['ratio'], 2.0, places=2)
        self.assertIsNotNone(payload['alert'])

    def test_no_alert_when_in_line(self):
        write_day(self.samples, '2026-11-11', [point('10:00:00', sh510300=100)])
        write_day(self.samples, '2026-11-12', [point('10:00:00', sh510300=100)])
        write_day(self.samples, '2026-11-13', [point('10:00:00', sh510300=105)])

        payload = national_live.build()
        self.assertIsNone(payload['alert'])
        self.assertAlmostEqual(payload['ratio'], 1.05, places=2)

    def test_missing_baseline_does_not_crash(self):
        write_day(self.samples, '2026-11-13', [point('10:00:00', sh510300=200)])
        payload = national_live.build()
        self.assertEqual(payload['state'], 'ok')
        self.assertIsNone(payload['ratio'])
        self.assertIsNone(payload['alert'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
