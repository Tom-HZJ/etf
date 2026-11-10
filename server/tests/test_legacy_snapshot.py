import copy,unittest
from datetime import datetime,timedelta
from unittest.mock import patch
from etfmon import config,views
NOW=datetime.fromisoformat('2026-11-10T13:40:00+08:00');CODES=('sh510300','sz159915')
class LegacySnapshotTest(unittest.TestCase):
    def setUp(self):
        self.codes=patch.object(config,'TEAM_CODES',CODES);self.codes.start()
        self.daily={'days':{'2026-11-09':{CODES[0]:{'shares':100},CODES[1]:{'shares':200}}}}
        self.quotes={CODES[0]:dict(shares=110,price=4,quote_time=NOW.isoformat()),CODES[1]:dict(shares=190,price=3,quote_time=NOW.isoformat())}
    def tearDown(self):self.codes.stop()
    def view(self,quotes=None,daily=None,now=NOW,stale=False):return views._legacy_snapshot_estimate(quotes or self.quotes,daily or self.daily,now.date().isoformat(),now,stale)
    def test_original_formula_preserves_signs_and_is_not_confirmed_daily_flow(self):
        v=self.view();self.assertEqual(v['amount'],10);self.assertEqual(v['share_delta'],0)
        self.assertEqual(v['basis'],'legacy_snapshot_difference');self.assertFalse(v['shares_date_verified']);self.assertFalse(v['is_confirmed_current_day_flow'])
        self.assertEqual(v['previous_day_basis'],'snapshot_label')
    def test_price_changes_can_revalue_without_changing_share_delta(self):
        q=copy.deepcopy(self.quotes);q[CODES[0]]['price']=5
        self.assertEqual(self.view(q)['amount'],20);self.assertEqual(self.view(q)['share_delta'],0)
    def test_complete_basket_is_required_and_zero_remains_zero(self):
        q=copy.deepcopy(self.quotes);q.pop(CODES[1]);self.assertIsNone(self.view(q))
        q=copy.deepcopy(self.quotes);q[CODES[1]]['price']=4;self.assertEqual(self.view(q)['amount'],0)
    def test_stale_future_and_previous_day_quotes_cannot_be_current(self):
        for at in (NOW-timedelta(minutes=6),NOW-timedelta(days=1),NOW+timedelta(seconds=10)):
            q=copy.deepcopy(self.quotes);q[CODES[0]]['quote_time']=at.isoformat();self.assertIsNone(self.view(q))
        self.assertIsNone(self.view(stale=True))
    def test_no_baseline_or_invalid_shares_cannot_mean_zero(self):
        self.assertIsNone(self.view(daily={'days':{}}))
        for value in (None,0,-1,float('nan')):
            q=copy.deepcopy(self.quotes);q[CODES[0]]['shares']=value;self.assertIsNone(self.view(q))
    def test_today_snapshot_does_not_consume_the_prior_baseline(self):
        d=copy.deepcopy(self.daily);d['days']['2026-11-10']={c:{'shares':999} for c in CODES}
        self.assertEqual(self.view(daily=d)['amount'],10)
    def test_lunch_preserves_dated_morning_observation(self):
        q=copy.deepcopy(self.quotes)
        for r in q.values():r['quote_time']='2026-11-10T11:30:00+08:00'
        now=datetime.fromisoformat('2026-11-10T12:30:00+08:00')
        self.assertEqual(self.view(q,now=now)['amount'],10)
    def test_official_value_match_never_becomes_a_verified_source_date(self):
        bundle={'complete':{'2026-11-06':self.daily['days']['2026-11-09'],'2026-11-09':self.quotes}}
        v=views._legacy_snapshot_estimate(self.quotes,self.daily,'2026-11-10',NOW,False,bundle)
        e=v['date_evidence'];self.assertIsNone(e['quote_shares']['actual_date']);self.assertIsNone(e['baseline_shares']['actual_date'])
        self.assertEqual(e['quote_shares']['matching_cleared_dates'],['2026-11-09']);self.assertEqual(e['baseline_shares']['matching_cleared_dates'],['2026-11-06'])
        self.assertTrue(e['snapshot_label_mismatch']);self.assertFalse(e['reference_range']['verified']);self.assertFalse(e['is_current_day_subscription_flow'])
        self.assertEqual(v['amount'],10)
    def test_ambiguous_or_future_value_matches_do_not_manufacture_a_range(self):
        bundle={'complete':{'2026-11-05':self.quotes,'2026-11-09':self.quotes,'2026-11-10':self.quotes,'2026-11-06':self.daily['days']['2026-11-09']}}
        e=views._legacy_snapshot_estimate(self.quotes,self.daily,'2026-11-10',NOW,False,bundle)['date_evidence']
        self.assertEqual(e['quote_shares']['matching_cleared_dates'],['2026-11-05','2026-11-09']);self.assertIsNone(e['reference_range'])
if __name__=='__main__':unittest.main()
