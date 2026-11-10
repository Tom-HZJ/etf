"""Causal prior-known-high protection, gap execution and T+1 limitations."""
import os,tempfile,unittest
from datetime import datetime,timedelta
from unittest.mock import patch
from etfmon import opportunities,research,replay,store

def fixture(n=5):
    data=[dict(date=(datetime(2026,1,1)+timedelta(days=i)).date().isoformat(),open=100.,high=102.,low=99.,close=100.) for i in range(n)]
    cs=[dict(ma10=98.,atr14=10.) for _ in data]
    events=[dict(date=data[0]['date'],path='breakout',invalidation=80.)]
    return data,cs,events
def run(data,cs,events,**kwargs):return opportunities.simulate(data,cs,events,2026,trailing_drawdown=.07,**kwargs)

class DrawdownGuard(unittest.TestCase):
    def test_entry_day_cannot_sell_even_when_low_is_below_protection(self):
        b,c,e=fixture(2);b[1]['low']=90.
        r=run(b,c,e)
        self.assertEqual([f['side'] for f in r['executions']],['buy'])
        self.assertIsNotNone(r['position']);self.assertAlmostEqual(r['position']['drawdown_stop'],94.86)
    def test_later_low_touch_has_an_explicit_hypothetical_basis(self):
        b,c,e=fixture(3);b[2].update(open=99.,high=100.,low=94.,close=98.)
        r=run(b,c,e);trade=r['trades'][0]
        self.assertEqual(trade['held_sessions'],1);self.assertAlmostEqual(trade['exit_price'],94.86)
        self.assertEqual(trade['execution_basis'],'stop_assumption');self.assertEqual(r['executions'][-1]['timing'],'intraday_estimate')
        self.assertEqual(trade['entry_date'],b[1]['date']);self.assertEqual(trade['exit_date'],b[2]['date'])
    def test_gap_is_actual_open_and_can_lose_more_than_seven_percent(self):
        b,c,e=fixture(3);b[2].update(open=90.,high=92.,low=88.,close=91.)
        trade=run(b,c,e)['trades'][0]
        self.assertEqual(trade['exit_price'],90.);self.assertEqual(trade['execution_basis'],'gap_open');self.assertLess(trade['net_return'],-.07)
    def test_same_day_new_high_cannot_tighten_before_same_day_low(self):
        b,c,e=fixture(4);b[2].update(open=100.,high=120.,low=96.,close=110.)
        before=run(b[:3],c[:3],e)
        self.assertEqual(before['trades'],[]);self.assertEqual(before['drawdown_guard']['ambiguous_new_high_dates'],[b[2]['date']])
        self.assertAlmostEqual(before['position']['drawdown_stop'],111.6)
        b[3].update(open=112.,high=114.,low=110.,close=112.)
        after=run(b,c,e);self.assertAlmostEqual(after['trades'][0]['exit_price'],111.6)
    def test_prefixes_preserve_signals_and_references_without_future_prices(self):
        b,c,e=fixture(5);b[2].update(open=99.,high=100.,low=94.,close=98.)
        full=run(b,c,e)
        for n in range(1,len(b)+1):
            cut=b[n-1]['date'];r=run(b[:n],c[:n],e)
            keys=('date','side','price','reason','path','invalidation')
            self.assertEqual([{k:s[k] for k in keys} for s in r['signals']],[{k:s[k] for k in keys} for s in full['signals'] if s['date']<=cut])
            self.assertEqual(r['executions'],[f for f in full['executions'] if f['date']<=cut])
        future=run(b[:3],c[:3],e);b[-1].update(open=999.,high=2000.)
        self.assertEqual(future['executions'],run(b,c,e)['executions'])
    def test_single_position_guard_allows_a_later_independent_entry(self):
        b,c,e=fixture(6);b[2].update(open=99.,high=100.,low=94.,close=98.);e.append({**e[0],'date':b[3]['date']})
        self.assertEqual(sum(f['side']=='buy' for f in run(b,c,e)['executions']),2)
    def test_costs_do_not_change_protective_event_dates(self):
        b,c,e=fixture(3);b[2].update(open=99.,high=100.,low=94.,close=98.)
        a=run(b,c,e);d=run(b,c,e,cost_bps=40)
        self.assertEqual(a['executions'],d['executions']);self.assertLess(d['trades'][0]['net_return'],a['trades'][0]['net_return'])
    def test_cache_version_and_record_keep_unprotected_history_separate(self):
        import math
        prices=[100.+i*.05+3.5*math.sin(i*.4) for i in range(150)]
        data=[dict(date=(datetime(2026,1,1)+timedelta(days=i)).date().isoformat(),open=p,high=p+1.,low=p-1.,close=p) for i,p in enumerate(prices)]
        moment=datetime.fromisoformat('2026-11-09T16:00:00+08:00')
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ETF_DATA':tmp}),patch.object(store,'load_bars',return_value=data):
            a=replay.build_payload('sh512480',model='expanded',moment=moment)
            b=replay.build_payload('sh512480',model='expanded',protection='trailing7',moment=moment)
            c=replay.build_payload('sh512480',model='expanded',moment=moment)
            self.assertEqual(a['trades'],c['trades']);self.assertEqual(a['protection'],'none');self.assertEqual(b['protection'],'trailing7')
            self.assertNotEqual(a['rule_version'],b['rule_version']);self.assertIn('drawdown_guard',b)
            original=store.path_for('research','decisions','2026-11-09','v5-old.json');store.write_json(original,{'keep':True});before=original.read_bytes()
            payload={'as_of':'2026-11-09','rule_version':'self-cross-window-v5-drawdown7','rows':[]}
            with patch.object(research,'build_payload',return_value=payload):
                name=research.record_close(moment,model='expanded',protection='trailing7');self.assertIn('drawdown7',name)
                self.assertIsNone(research.record_close(moment.replace(hour=14),model='expanded',protection='trailing7'))
            self.assertEqual(original.read_bytes(),before)
    def test_unknown_or_legacy_protection_is_rejected(self):
        for model,protection in [('current','trailing7'),('legacy','trailing7'),('expanded','unknown')]:
            with self.assertRaises(ValueError):replay.build_payload('sh512480',model=model,protection=protection)
        with self.assertRaises(ValueError):research.build_payload(protection='trailing7')

if __name__=='__main__':unittest.main()
