"""Expanded entries remain bounded, causal and independent of other ETFs."""
import math,unittest,tempfile,os
from datetime import datetime,timedelta
from unittest.mock import patch
from etfmon import opportunities,research,replay,strategy,store

def bars(prices):
    return [{'date':(datetime(2026,1,1)+timedelta(days=i)).date().isoformat(),'open':p,'close':p,'low':p-.3,'high':p+.3} for i,p in enumerate(prices)]

class WindowEntries(unittest.TestCase):
    def controlled(self,first_good):
        data=bars([10.]*75);cs=[]
        for i,row in enumerate(data):
            if 64<=i<first_good:row.update(open=12.,close=12.,high=12.3)
            cs.append(dict(ma5=9.2 if i>=64 else 8.9,ma10=9.,ma30=9.5,ma20=9.4,ma60=9.6,atr14=1.,fast=i>=64,strong=False))
        return data,cs
    def test_failed_cross_can_confirm_later_inside_the_same_window_once(self):
        data,cs=self.controlled(66)
        with patch.object(strategy,'contexts',return_value=cs):result=opportunities.scan(data)
        self.assertEqual([e['date'] for e in result['events']],[data[66]['date']])
        self.assertEqual(result['events'][0]['setup_date'],data[64]['date'])
    def test_cross_day_plus_four_later_sessions_is_the_whole_window(self):
        data,cs=self.controlled(68)
        with patch.object(strategy,'contexts',return_value=cs):self.assertEqual(len(opportunities.scan(data)['events']),1)
        data,cs=self.controlled(69)
        with patch.object(strategy,'contexts',return_value=cs):
            result=opportunities.scan(data);self.assertEqual(result['events'],[]);self.assertIsNone(result['state']['armed_date'])
    def test_loss_of_own_alignment_invalidates_the_old_window(self):
        data,cs=self.controlled(68);cs[66]['ma5']=8.9;cs[66]['fast']=False
        with patch.object(strategy,'contexts',return_value=cs):result=opportunities.scan(data)
        self.assertEqual(result['events'][0]['setup_date'],data[67]['date'])
    def test_future_prefix_and_old_history_cannot_change_known_events(self):
        data=bars([10+.005*i+.35*math.sin(i*.4) for i in range(170)]);events=opportunities.scan(data)['events'];self.assertTrue(events)
        for n in (65,80,100,130,169):self.assertEqual(opportunities.scan(data[:n])['events'],[e for e in events if e['date']<=data[n-1]['date']])
        old=[{**r,'date':r['date'].replace('2026','2024')} for r in bars([13.]*100)]
        self.assertEqual([e for e in events if e['date']>=data[80]['date']],[e for e in opportunities.scan(old+data)['events'] if e['date']>=data[80]['date']])
        before=research.screen({'code':'sh512480'},data,data[99]['date'],collect_events=True,model='expanded')
        data[-1].update(close=999.,high=1000.)
        self.assertEqual(before,research.screen({'code':'sh512480'},data,data[99]['date'],collect_events=True,model='expanded'))
    def test_wider_next_open_boundary_does_not_change_v4(self):
        data=bars([10.]*4);cs=[dict(ma10=9.8,atr14=1.) for _ in data];events=[dict(date=data[0]['date'],path='breakout',invalidation=9.)]
        data[1].update(open=11.6,high=11.9)
        self.assertEqual(strategy.simulate(data,cs,events,2026)['executions'],[])
        expanded=opportunities.simulate(data,cs,events,2026)
        self.assertEqual(expanded['executions'][0]['price'],11.6);self.assertEqual(expanded['executions'][0]['date'],data[1]['date'])
        data[1].update(open=12.9,high=13.)
        self.assertEqual(opportunities.simulate(data,cs,events,2026)['executions'],[])
    def test_expanded_cache_and_metadata_cannot_mix_with_v4(self):
        data=bars([10+.005*i+.35*math.sin(i*.4) for i in range(170)])
        moment=datetime.fromisoformat('2026-11-09T16:00:00+08:00')
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ETF_DATA':tmp}),patch.object(store,'load_bars',return_value=data):
            a=replay.build_payload('sh512480',moment=moment);b=replay.build_payload('sh512480',moment=moment,model='expanded');c=replay.build_payload('sh512480',moment=moment)
            self.assertEqual(a['signals'],c['signals']);self.assertEqual(a['model'],'current');self.assertEqual(b['model'],'expanded')
            self.assertEqual(b['entry_limits']['maximum_risk_atr'],3.)
            self.assertIn('self-cross-window-v5',b['rule_version']);self.assertFalse(b['external_reference_required']);self.assertIn('model_warning',b)
    def test_research_model_validation_and_metadata(self):
        with self.assertRaises(ValueError):research.build_payload(model='unknown')
        with patch.object(research.config,'HOTSPOT_CODES',[]):
            a=research.build_payload(model='expanded');b=research.build_payload()
            self.assertEqual(a['model'],'expanded');self.assertEqual(b['model'],'current')
            self.assertEqual(a['parameters']['extension_atr'],2.);self.assertEqual(b['parameters']['extension_atr'],1.5)
    def test_expanded_actual_close_records_have_a_separate_version_and_are_not_backfilled(self):
        moment=datetime.fromisoformat('2026-11-09T16:00:00+08:00')
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ETF_DATA':tmp}):
            original=store.path_for('research','decisions','2026-11-09','self-early-turn-v4-original.json');store.write_json(original,{'rule_version':'self-early-turn-v4'})
            before=original.read_bytes();payload={'as_of':'2026-11-09','rule_version':opportunities.ENTRY_VERSION,'rows':[]}
            with patch.object(research,'build_payload',return_value=payload):
                name=research.record_close(moment,model='expanded');self.assertTrue(name.startswith('self-cross-window-v5-'))
                self.assertEqual(research.record_close(moment,model='expanded'),name)
                self.assertIsNone(research.record_close(moment.replace(hour=14),model='expanded'))
            self.assertEqual(original.read_bytes(),before)

if __name__=='__main__':unittest.main()
