import copy,os,tempfile,unittest
from datetime import datetime,timedelta
from unittest.mock import patch
from etfmon import config,store,representatives,actions

NOW=datetime.fromisoformat('2026-11-10T10:49:40+08:00')
PEERS=('sh588000','sh588080','sh588050')
def quotes(moment=NOW):
    return {c:dict(price=p,shares=s,quote_time=moment.isoformat(),source='tencent',stale=False)
            for c,p,s in zip(PEERS,(1.35,2.,1.3),(1000.,100.,50.))}

class RepresentativesTests(unittest.TestCase):
    def test_largest_size_not_highest_price_or_strategy_signal(self):
        with patch.dict(config.TRADE_REFERENCE_ALTERNATIVES,{'科创50':PEERS},clear=True):
            q=quotes();q[PEERS[1]]['amount']=999999999999
            result=representatives.update(q,NOW)
            group=result['groups']['科创50']
            self.assertEqual(group['selected_code'],'sh588000')
            self.assertEqual(group['comparisons'][0]['size'],1350)
            self.assertTrue(representatives.allowed('sh588000',result,'2026-11-10'))
            self.assertFalse(representatives.allowed('sh588080',result,'2026-11-10'))

    def test_missing_peer_does_not_choose_a_smaller_fund(self):
        with patch.dict(config.TRADE_REFERENCE_ALTERNATIVES,{'科创50':PEERS},clear=True):
            q=quotes();q.pop('sh588000')
            result=representatives.update(q,NOW)
            self.assertIsNone(result['groups']['科创50']['selected_code'])
            self.assertTrue(all(not representatives.allowed(c,result,'2026-11-10') for c in PEERS))

    def test_invalid_stale_and_future_sizes_cannot_rank(self):
        cases=[('shares',0),('shares',float('nan')),('shares',float('inf')),('price',-1),('price',None),
               ('stale',True),('quote_time',(NOW-timedelta(days=1)).isoformat()),
               ('quote_time',(NOW+timedelta(seconds=1)).isoformat()),('quote_time',(NOW-timedelta(seconds=91)).isoformat())]
        with patch.dict(config.TRADE_REFERENCE_ALTERNATIVES,{'科创50':PEERS},clear=True):
            for field,bad in cases:
                with self.subTest(field=field,value=bad):
                    q=quotes();q['sh588000'][field]=bad
                    self.assertIsNone(representatives.update(q,NOW)['groups']['科创50']['selected_code'])

    def test_selection_is_stable_for_day_and_rechecked_next_day(self):
        with patch.dict(config.TRADE_REFERENCE_ALTERNATIVES,{'科创50':PEERS},clear=True):
            first=representatives.update(quotes(),NOW)
            changed=quotes();changed['sh588080']['shares']=100000
            self.assertEqual(representatives.update(changed,NOW,first),first)
            tomorrow=NOW+timedelta(days=1);changed=quotes(tomorrow);changed['sh588080']['shares']=100000
            next_day=representatives.update(changed,tomorrow,first)
            self.assertEqual(next_day['groups']['科创50']['selected_code'],'sh588080')
            self.assertFalse(representatives.allowed('sh588000',first,'2026-11-11'))

    def test_other_indices_are_not_merged(self):
        self.assertNotIn('sz159949',representatives.member_codes())
        self.assertTrue(representatives.allowed('sz159949',{},'2026-11-10'))
        self.assertFalse(representatives.allowed('sh512480',{},'2026-11-10'))

    def test_explicit_alternatives_keep_one_and_do_not_merge_chinext50(self):
        all_peers=representatives.member_codes()
        self.assertTrue({'sh512480','sh588000','sh588200','sz159995','sh512660','sh512810','sz159227','sz159378'}.issubset(all_peers))
        self.assertNotIn('sz159949',all_peers)
        q={c:dict(price=1,shares=i+1,quote_time=NOW.isoformat(),stale=False) for i,c in enumerate(sorted(all_peers))}
        selected=representatives.update(q,NOW)
        for peers in config.TRADE_REFERENCE_ALTERNATIVES.values():
            self.assertEqual(sum(representatives.allowed(c,selected,'2026-11-10') for c in peers),1)
        self.assertFalse(actions.trade_eligible('sh563300'))

    def test_lunch_size_selection_uses_morning_close_without_creating_entry(self):
        lunch=NOW.replace(hour=12,minute=15)
        q=quotes(NOW.replace(hour=11,minute=29,second=50))
        with patch.dict(config.TRADE_REFERENCE_ALTERNATIVES,{'科创50':PEERS},clear=True):
            self.assertEqual(representatives.update(q,lunch)['groups']['科创50']['selected_code'],'sh588000')
            old=quotes(NOW.replace(hour=10,minute=0))
            self.assertIsNone(representatives.update(old,lunch)['groups']['科创50']['selected_code'])

    def test_selecting_a_representative_is_not_an_entry_signal_or_daily_quota(self):
        calls=[]
        def no_plans(moment,codes):calls.append(codes);return {'expanded-none':{}}
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ETF_DATA':tmp}),patch.dict(config.TRADE_REFERENCE_ALTERNATIVES,{'科创50':PEERS},clear=True),patch.object(actions,'build_plans',side_effect=no_plans):
            snapshot=actions.process(quotes(),NOW)
            self.assertEqual(calls[0],['sh588000'])
            self.assertEqual(snapshot['buy_points'],[])
            self.assertEqual(snapshot['recent'],[])
            tomorrow=NOW+timedelta(days=1)
            again=actions.process(quotes(tomorrow),tomorrow)
            self.assertEqual(again['buy_points'],[])
            self.assertEqual(list(actions.path('events').rglob('*.json')),[])

if __name__=='__main__':unittest.main()
