import unittest
from unittest.mock import patch
import tempfile
import os
from datetime import datetime, timedelta
from pathlib import Path
from etfmon import legacy_research as research, analytics, config, store

def bars(prices):
    start=datetime(2026, 1, 1)
    return [{'date':(start+timedelta(days=i)).date().isoformat(), 'open':p, 'high':p+.5, 'low':p-.5, 'close':p} for i,p in enumerate(prices)]

E={'code':'sh512480','name':'半导体ETF','group':'科技'}

class ResearchTests(unittest.TestCase):
    def test_rank_comparison_never_uses_future_snapshots(self):
        rows=[{'code':'x','rank':1}]
        history=[{'date':'2026-11-10','rows':[{'code':'x','rank':99}]},
                 {'date':'2026-11-06','rows':[{'code':'x','rank':3}]}]
        self.assertEqual(analytics.rank_changes(rows,history,'2026-11-09',1),{'x':2})
    def test_incomplete_daily_is_excluded_and_future_data_does_not_change_result(self):
        data=bars([100+i*.1 for i in range(80)])
        cutoff=data[-2]['date']; benchmark={'date':cutoff,'state':'supportive'}
        before=research.screen(E,data[:-1],cutoff)
        data[-1].update(close=99999,high=100000)
        self.assertEqual(before,research.screen(E,data,cutoff))

    def test_cutoff_is_shanghai_1510_policy_not_utc(self):
        self.assertEqual(research.completed_cutoff(datetime.fromisoformat('2026-11-09T07:09:59+00:00')),'2026-11-08')
        self.assertEqual(research.completed_cutoff(datetime.fromisoformat('2026-11-09T07:10:00+00:00')),'2026-11-09')

    def test_invalid_ohlc_stops_screen(self):
        data=bars([100+i*.1 for i in range(80)]); data[-2]['low']=999
        self.assertEqual(research.screen(E,data,'2099-01-01')['phase'],'invalid_data')

    def test_crossborder_still_requires_separate_rules(self):
        data=bars([100+i*.1 for i in range(80)])
        self.assertEqual(research.screen({'code':'sh513100','name':'纳指ETF'},data,'2099-01-01')['phase'],'separate_rules')

    def test_heat_does_not_change_entry_geometry(self):
        data=bars([100+i*.1 for i in range(80)]); b={'date':data[-1]['date'],'state':'supportive'}
        low=research.screen(E,data,'2099-01-01',10); high=research.screen(E,data,'2099-01-01',99)
        low.pop('heat'); high.pop('heat'); self.assertEqual(low,high)

    def test_first_trigger_is_next_session_only_and_does_not_repeat(self):
        prices=[100+i*.04 for i in range(70)]+[102.4,102.45,102.7]
        data=bars(prices)
        for row in data: row.update(high=row['close']+.1,low=row['close']-.1)
        def check(d):
            return research.screen(E,d,d[-1]['date'])
        self.assertEqual(check(data[:-1])['phase'],'preparing')
        first=check(data); self.assertTrue(first['eligible']); self.assertEqual(first['phase'],'triggered')
        self.assertFalse(first['external_reference_required'])
        next_row={**data[-1],'date':'2026-03-15','close':102.71,'open':102.71,'high':102.81,'low':102.61}
        following=check(data+[next_row])
        self.assertFalse(following['eligible']); self.assertEqual(following['last_signal_date'],first['trigger_date'])
        self.assertNotEqual(following['phase'],'triggered')

    def test_pivot_contains_actual_knowledge_date_and_prefix_is_causal(self):
        data=bars([100]*19+[110,120,118,107])
        full=analytics.trend_structure(data)
        self.assertFalse(full['add']); self.assertEqual(full['decision_role'],'context_only')
        confirmed=[p for p in full['points'] if p['confirmed']]
        for p in confirmed:
            self.assertGreaterEqual(p['confirmed_at'],p['date'])
            prefix=[b for b in data if b['date']<=p['confirmed_at']]
            self.assertIn(p,analytics.trend_structure(prefix)['points'])
        self.assertEqual(confirmed[-1]['date'],data[20]['date'])
        self.assertEqual(confirmed[-1]['confirmed_at'],data[22]['date'])

    def test_t1_is_locked_before_exit_review(self):
        data=bars([100]*30)
        pos={'entry_date':data[-1]['date'],'entry_price':100,'invalidation_close':101}
        self.assertEqual(research.assess_position(pos,data,'2099-01-01')['state'],'t1_locked')
        pos['entry_date']=data[-2]['date']
        self.assertEqual(research.assess_position(pos,data,'2099-01-01')['state'],'exit_review')

    def test_time_and_stall_reviews_are_session_counts(self):
        data=bars([100]*30)
        pos={'entry_date':data[-6]['date'],'entry_price':100,'invalidation_close':90}
        self.assertEqual(research.assess_position(pos,data,'2099-01-01')['state'],'stalled')
        pos['entry_date']=data[-21]['date']
        self.assertEqual(research.assess_position(pos,data,'2099-01-01')['state'],'time_review')
        pos['entry_date']=''
        self.assertEqual(research.assess_position(pos,data,'2099-01-01')['state'],'unknown')

    def test_actual_close_record_is_append_only_and_never_backfilled(self):
        moment=datetime.fromisoformat('2026-11-09T16:00:00+08:00')
        payload={'as_of':'2026-11-09','rule_version':research.RULE_VERSION,'rows':[],'mode':'experimental_reconstruction'}
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'ETF_DATA':d}),patch.object(research,'build_payload',side_effect=lambda _:dict(payload)):
            name=research.record_close(moment); self.assertTrue(name)
            path=Path(d)/'research/decisions/2026-11-09'/name
            old=path.stat().st_mtime_ns
            self.assertEqual(research.record_close(moment),name); self.assertEqual(path.stat().st_mtime_ns,old)
            self.assertEqual(store.read_json(path,{})['mode'],'live_recorded')
            payload['as_of']='2026-11-06'; self.assertIsNone(research.record_close(moment))
            self.assertIsNone(research.record_close(moment.replace(hour=14)))

    def test_missing_previous_session_blocks_old_eligibility_during_market(self):
        data=bars([100+i*.1 for i in range(80)])
        with patch.object(store,'load_bars',return_value=data),patch.object(store,'load_bars_payload',return_value={}),patch.object(store,'load_hotspots_latest',return_value={}),patch.object(config,'HOTSPOT_CODES',[]):
            x=research.build_payload(datetime.fromisoformat('2026-11-10T10:00:00+08:00'))
            self.assertTrue(x['data_stale'])

if __name__=='__main__': unittest.main()
