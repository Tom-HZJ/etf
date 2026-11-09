import unittest
from datetime import datetime,timedelta
from etfmon import legacy_replay as replay

def series(n=12):
 days=[(datetime(2026,1,5)+timedelta(days=i)).date().isoformat() for i in range(n)]
 bars=[dict(date=d,open=10.,close=10.,high=10.5,low=9.5) for d in days]
 contexts=[dict(ma20=9.8,atr14=1.) for _ in bars]
 event=dict(date=days[0],path='pullback',invalidation=9.)
 return bars,contexts,[event]

class ReplayTests(unittest.TestCase):
 def test_entry_next_open_and_stop_exit_next_open_not_stop_price(self):
  b,c,e=series(4);b[1].update(close=8.,low=7.5);b[2].update(open=7.8,close=8.,low=7.5)
  r=replay.simulate(b,c,e,2026)
  self.assertEqual([(f['date'],f['side']) for f in r['executions']],[(b[1]['date'],'buy'),(b[2]['date'],'sell')])
  t=r['trades'][0];self.assertEqual(t['exit_price'],7.8);self.assertLess(t['net_return'],-.22)
  self.assertEqual(t['held_sessions'],1)
 def test_last_close_signal_never_gets_a_future_reference(self):
  b,c,e=series(1);r=replay.simulate(b,c,e,2026)
  self.assertEqual(r['executions'],[]);self.assertIsNone(r['pending']['reference_price'])
 def test_gap_above_risk_boundary_cancels_without_fake_fill(self):
  b,c,e=series(4);b[1].update(open=15.,close=15.,high=15.2,low=14.)
  r=replay.simulate(b,c,e,2026);self.assertEqual(r['executions'],[]);self.assertEqual(len(r['cancelled']),1)
 def test_five_day_stall_and_twenty_day_review(self):
  b,c,e=series(8);r=replay.simulate(b,c,e,2026)
  self.assertEqual(r['signals'][1]['date'],b[5]['date']);self.assertIn('5个交易日',r['trades'][0]['exit_reason'])
  b,c,e=series(24)
  for i,row in enumerate(b):row.update(close=10+i*.05,high=11+i*.05,low=9+i*.05)
  r=replay.simulate(b,c,e,2026)
  self.assertEqual(r['signals'][1]['date'],b[20]['date']);self.assertIn('20个交易日',r['trades'][0]['exit_reason'])
 def test_trailing_exit_never_uses_future_high(self):
  b,c,e=series(6);b[1].update(close=13.,high=14.);b[2].update(close=10.5,high=11.)
  r=replay.simulate(b,c,e,2026);self.assertIn('2ATR',r['trades'][0]['exit_reason'])
  b[5]['high']=10000
  self.assertEqual(r['signals'],replay.simulate(b,c,e,2026)['signals'])
 def test_two_closes_below_ma_exit(self):
  b,c,e=series(6);b[1]['close']=9.7;b[2]['close']=9.6
  r=replay.simulate(b,c,e,2026);self.assertIn('连续两日',r['trades'][0]['exit_reason'])
 def test_prefix_signals_and_fills_are_causal(self):
  b,c,e=series(25);e.append(dict(date=b[12]['date'],path='breakout',invalidation=9.))
  full=replay.simulate(b,c,e,2026)
  fields=lambda xs:[{k:r[k] for k in ('date','side','price','reason','path','invalidation')} for r in xs]
  for n in range(1,len(b)+1):
   result=replay.simulate(b[:n],c[:n],e,2026);cut=b[n-1]['date']
   self.assertEqual(fields(result['signals']),fields([s for s in full['signals'] if s['date']<=cut]))
   self.assertEqual(result['executions'],[f for f in full['executions'] if f['date']<=cut])
   self.assertTrue(all(t['exit_date']<=cut for t in result['trades']))
 def test_never_sells_without_a_position_or_overlaps_entries(self):
  b,c,e=series(9);e +=[dict(date=row['date'],path='breakout',invalidation=9.) for row in b[1:]]
  r=replay.simulate(b,c,e,2026);holding=False
  for f in r['executions']:
   self.assertEqual(f['side']=='sell',holding);holding=f['side']=='buy'
 def test_year_start_flat_and_cost_assumptions(self):
  b,c,e=series(9);e[0]['date']='2025-12-31'
  self.assertEqual(replay.simulate(b,c,e,2026)['executions'],[])
  e[0]['date']=b[0]['date'];zero=replay.simulate(b,c,e,2026,0);cost=replay.simulate(b,c,e,2026,40)
  self.assertLess(cost['summary']['model_net_return'],zero['summary']['model_net_return'])
 def test_parameter_validation(self):
  with self.assertRaises(ValueError):replay.build_payload('sh512980',cost_bps=-1)
  with self.assertRaises(ValueError):replay.build_payload('sh512980',path='guaranteed')
  with self.assertRaises(ValueError):replay.build_payload('sh512980',cutoff='2099-12-31')
