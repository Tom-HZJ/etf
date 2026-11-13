"""A new clearing weight date must not make every page request rebuild history."""
import concurrent.futures
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from etfmon import store, views


class ChartReadCacheTest(unittest.TestCase):
    def test_concurrent_readers_reuse_rebuild_and_invalidate_changed_inputs(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, ETF_DATA=root), \
                patch.object(views.config, "TEAM_CODES", ("sh510300",)):
            bundle = dict(latest_complete="2026-11-11", complete={
                "2026-11-11": {"sh510300": dict(shares=100)}})
            def rebuild():
                time.sleep(.01)
                return dict(weights={"sh510300": 100}, day_bars=[])
            with patch.object(views, "compute_chart_cache", side_effect=rebuild) as compute, \
                    patch.object(store, "write_json") as write:
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                    result = list(pool.map(lambda _: views._load_chart_cache(bundle), range(5)))
                self.assertEqual(compute.call_count, 1)
                self.assertTrue(all(r == result[0] for r in result))
                write.assert_not_called()
            # An atomically replaced price file must invalidate the derived cache.
            store.write_json(store.bars_path("sh510300"), dict(bars=[dict(date="2026-11-11", close=4)]))
            with patch.object(views, "compute_chart_cache", return_value=dict(changed=True)) as compute:
                self.assertEqual(views._load_chart_cache(bundle), dict(changed=True))
                views._load_chart_cache(bundle)
                self.assertEqual(compute.call_count, 1)
                bundle['complete']['2026-11-11']['sh510300']['shares'] = 200
                views._load_chart_cache(bundle)
                self.assertEqual(compute.call_count, 2)

