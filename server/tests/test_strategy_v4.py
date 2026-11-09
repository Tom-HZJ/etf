"""Causal entry, bounded state, actual-open R and close-only exit references."""
from datetime import datetime,timedelta
import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from etfmon import config,research,replay,strategy,store

def bars(prices):
 return [dict(date=(datetime(2026,1,1)+timedelta(days=i)).date().isoformat(),open=p,high=p+.3,low=p-.3,close=p) for i,p in enumerate(prices)]
def fixture(n=12,stop=9.):
 b=bars([10.]*n);cs=[dict(ma10=9.8,atr14=1.) for _ in b]
 return b,cs,[dict(date=b[0]['date'],path='breakout',invalidation=stop)]

class V4Entries(unittest.TestCase):
 def test_new_cross_rearms_below_a_previous_signal_without_recovering_old_high(self):
  b=bars([10.]*70+[9.8]*3+[9.9,10.,10.1,10.2]+[9.5]*10+[9.3]*3+[9.4,9.5,9.6,9.7])
  s=strategy.scan(b);self.assertGreaterEqual(len(s['events']),2)
  first,last=s['events'][0],s['events'][-1];by={r['date']:i for i,r in enumerate(b)}
  self.assertLess(b[by[last['date']]]['close'],b[by[first['date']]]['close'])
  self.assertLess(s['contexts'][by[last['date']]]['ma20'],s['contexts'][by[last['date']]-5]['ma20'])
  self.assertTrue(all(by[e['date']]>=64 for e in s['events']))
 def test_every_prefix_has_the_same_past_events_and_future_mutations_cannot_repaint(self):
  import math
  b=bars([10+.005*i+.35*math.sin(i*.4) for i in range(150)]);events=strategy.scan(b)['events']
  self.assertTrue(events)
  for n in range(65,len(b)+1):self.assertEqual(strategy.scan(b[:n])['events'],[e for e in events if e['date']<=b[n-1]['date']])
  cutoff=b[99]['date'];before=research.screen({'code':'sh512480'},b,cutoff,collect_events=True)
  b[-1].update(close=999.,high=1000.)
  self.assertEqual(before,research.screen({'code':'sh512480'},b,cutoff,collect_events=True))
 def test_old_unrelated_history_does_not_change_recent_average_or_cross_decisions(self):
  import math
  recent=bars([10+.005*i+.35*math.sin(i*.4) for i in range(170)])
  old=[{**r,'date':r['date'].replace('2026','2024')} for r in bars([13.]*100)]
  a=strategy.scan(recent);b=strategy.scan(old+recent);cut=recent[80]['date']
  self.assertEqual([e for e in a['events'] if e['date']>=cut],[e for e in b['events'] if e['date']>=cut])
  self.assertEqual(a['contexts'][80:],b['contexts'][180:])
 def test_window_expires_and_own_structure_loss_clears_episode_state(self):
  b=bars([10.]*70+[9.8]*3+[9.9,10.,10.1,10.2]+[9.]*8)
  state=strategy.scan(b)['state'];self.assertIsNone(state['armed_date']);self.assertFalse(state['episode_consumed'])
 def test_continuous_nearness_does_not_renew_an_expired_prealert(self):
  b=bars([10.+.001*i for i in range(100)])
  state=strategy.scan(b)['state'];self.assertIsNone(state['watch_date'])
  r=research.screen({'code':'sh512480'},b,b[-1]['date'])
  self.assertFalse(r['prealert']);self.assertIsNone(r['preparation']['armed_date'])
 def test_anchor_is_own_ma10_and_no_external_reference_is_required(self):
  b=bars([10.]*70+[9.8]*3+[9.9,10.,10.1,10.2]);r=research.screen({'code':'sh512480'},b,b[-1]['date'])
  self.assertTrue(r['eligible']);self.assertEqual(r['entry_anchor'],'MA10');self.assertFalse(r['external_reference_required'])
  self.assertAlmostEqual(r['entry_distance_atr'],round((r['reference_close']-r['ma10'])/r['atr14'],3))

class V4Exits(unittest.TestCase):
 def test_target_uses_actual_next_open_r_and_never_signal_close(self):
  b,c,e=fixture(5);b[1].update(open=10.4,close=11.,high=11.2);b[2].update(close=12.2,high=12.3);b[3].update(close=12.6,high=12.7)
  r=strategy.simulate(b,c,e,2026);t=r['trades'][0]
  self.assertAlmostEqual(t['risk_unit'],1.4);self.assertAlmostEqual(t['target_price'],12.5)
  self.assertEqual(t['exit_signal_date'],b[3]['date']);self.assertEqual(t['exit_date'],b[4]['date'])
 def test_t1_target_signal_at_entry_close_exits_only_next_open(self):
  b,c,e=fixture(3);b[1].update(close=11.6,high=11.7);b[2].update(open=11.4)
  r=strategy.simulate(b,c,e,2026);self.assertEqual(r['trades'][0]['held_sessions'],1)
  self.assertEqual(r['trades'][0]['exit_price'],11.4)
 def test_1r_protection_can_still_lose_through_an_overnight_gap(self):
  b,c,e=fixture(4);b[1].update(close=11.2,high=11.3);b[2].update(close=9.9);b[3].update(open=9.4)
  r=strategy.simulate(b,c,e,2026);t=r['trades'][0]
  self.assertTrue(t['profit_armed']);self.assertEqual(t['trailing_stop'],10.)
  self.assertIn('利润保护',t['exit_reason']);self.assertEqual(t['exit_price'],9.4);self.assertLess(t['net_return'],0.)
 def test_intraday_or_future_high_never_arms_profit_protection(self):
  b,c,e=fixture(3);b[1].update(close=10.5,high=30.)
  r=strategy.simulate(b,c,e,2026);self.assertFalse(r['position']['profit_armed'])
  b[-1]['high']=9999.;self.assertEqual(r['signals'],strategy.simulate(b,c,e,2026)['signals'])
 def test_initial_stop_exit_is_next_open_not_the_stop_price(self):
  b,c,e=fixture(4);b[1].update(close=8.8,low=8.5);b[2].update(open=8.)
  t=strategy.simulate(b,c,e,2026)['trades'][0]
  self.assertEqual(t['exit_price'],8.);self.assertIn('结构失效',t['exit_reason'])
 def test_protection_never_loosens_when_atr_expands(self):
  b,c,e=fixture(4);b[1].update(close=11.2,high=11.3);b[2].update(close=11.3,high=11.4);c[2]['atr14']=5.
  r=strategy.simulate(b,c,e,2026);self.assertEqual(r['position']['trailing_stop'],10.)
 def test_gap_above_entry_boundary_cancels_without_fills(self):
  b,c,e=fixture(4);b[1].update(open=15.,high=16.)
  r=strategy.simulate(b,c,e,2026);self.assertEqual(r['executions'],[]);self.assertEqual(len(r['cancelled']),1)
 def test_two_close_and_stall_and_ten_session_review(self):
  b,c,e=fixture(5);c[1]['ma10']=10.2;c[2]['ma10']=10.2;b[1]['close']=9.9;b[2]['close']=9.8
  self.assertIn('两日',strategy.simulate(b,c,e,2026)['trades'][0]['exit_reason'])
  b,c,e=fixture(8);self.assertIn('5个',strategy.simulate(b,c,e,2026)['trades'][0]['exit_reason'])
  b,c,e=fixture(13,8.5)
  for i,row in enumerate(b):row.update(close=10.+i*.03,high=10.5+i*.03)
  self.assertIn('10个',strategy.simulate(b,c,e,2026)['trades'][0]['exit_reason'])
 def test_prefix_signals_fills_and_cost_sensitivity(self):
  b,c,e=fixture(14);b[1].update(close=11.2,high=11.3);b[2].update(close=11.6,high=11.7)
  full=strategy.simulate(b,c,e,2026)
  fields=lambda rows:[{k:r[k] for k in ('date','side','price','reason','path','invalidation')} for r in rows]
  for n in range(1,len(b)+1):
   r=strategy.simulate(b[:n],c[:n],e,2026);cut=b[n-1]['date']
   self.assertEqual(fields(r['signals']),fields([s for s in full['signals'] if s['date']<=cut]))
   self.assertEqual(r['executions'],[f for f in full['executions'] if f['date']<=cut])
  expensive=strategy.simulate(b,c,e,2026,40)
  self.assertEqual(full['executions'],expensive['executions']);self.assertLess(expensive['summary']['model_net_return'],full['summary']['model_net_return'])
 def test_no_future_reference_and_no_sell_without_position(self):
  b,c,e=fixture(1);r=strategy.simulate(b,c,e,2026)
  self.assertEqual(r['executions'],[]);self.assertIsNone(r['pending']['reference_price'])
  self.assertEqual(strategy.simulate(*fixture(5)[:2],[],2026)['signals'],[])

class V4Records(unittest.TestCase):
 def test_new_record_keeps_old_version_immutable(self):
  moment=datetime.fromisoformat('2026-11-09T16:00:00+08:00')
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ETF_DATA':tmp}):
   old=store.path_for('research','decisions','2026-11-09','self-trend-two-paths-v3-original.json');store.write_json(old,{'rule_version':'self-trend-two-paths-v3'})
   before=old.read_bytes();payload={'as_of':'2026-11-09','rule_version':research.RULE_VERSION,'rows':[]}
   with patch.object(research,'build_payload',return_value=payload):name=research.record_close(moment)
   self.assertTrue(name.startswith('self-early-turn-v4-'));self.assertEqual(old.read_bytes(),before)
 def test_model_validation_and_version_are_distinct(self):
  with self.assertRaises(ValueError):replay.build_payload('sh512480',model='unknown')
  self.assertEqual(replay.VERSION,'self-early-turn-v4/protected-bite-v2')

if __name__=='__main__':unittest.main()
