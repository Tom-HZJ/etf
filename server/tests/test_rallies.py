import json,os,tempfile,threading,unittest
from datetime import datetime,timedelta
from unittest.mock import patch
from etfmon import history,rallies,store,sources,coverage

MOMENT=datetime.fromisoformat('2026-11-09T22:00:00+08:00')
def bars(values,start='2026-01-01'):
 first=datetime.fromisoformat(start)
 return [dict(date=(first+timedelta(days=i)).date().isoformat(),open=p,high=p+.01,low=p-.01,close=p,volume=100) for i,p in enumerate(values)]

class RallyTests(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
  env=patch.dict(os.environ,{'ETF_DATA':temp.name});env.start();self.addCleanup(env.stop)
 def test_provider_units_and_missing_turnover(self):
  rows=history.parse_activity({'data':{'klines':['2026-11-09,1,1.1,1.2,.9,1200,130000,0,0,0,8.34','2026-11-06,1,1,1,1,0,0,0,0,0,-']}})
  self.assertEqual(rows[-1]['volume'],1200);self.assertEqual(rows[-1]['amount'],130000)
  self.assertAlmostEqual(rows[-1]['turnover'],.0834);self.assertIsNone(rows[0]['turnover'])
 def test_invalid_rows_and_duplicate_dates_are_not_double_counted(self):
  rows=history.parse_activity({'data':{'klines':['2026-11-09,1,1,1,1,100,100,0,0,0,0','2026-11-09,1,1,1,1,200,200,0,0,0,0','2026-11-08,1,2,1,.9,-1,20,0,0,0,3']}})
  self.assertEqual(len(rows),1);self.assertEqual(rows[0]['volume'],200);self.assertEqual(rows[0]['turnover'],0)
 def test_rolling_three_year_window_handles_leap_day(self):
  self.assertEqual(history.window(datetime.fromisoformat('2024-02-29T22:00:00+08:00')),('2021-02-28','2024-02-29'))
 def test_features_have_no_future_data_and_volume_denominator_excludes_today(self):
  data=bars([10+i/10 for i in range(80)]);data[40]['volume']=1000
  a={r['date']:{'volume':r['volume'],'turnover':.02,'amount':100} for r in data}
  prefix=rallies.features(data[:41],a);full=rallies.features(data,a)
  self.assertEqual(prefix,full[:41]);self.assertEqual(prefix[-1]['volume_ratio'],10)
  self.assertIsNone(prefix[28]['ma30']);self.assertIsNone(prefix[33]['slope30']);self.assertIsNotNone(prefix[34]['slope30'])
 def test_missing_turnover_is_not_zero_or_reconstructed_from_current_shares(self):
  rows=bars([10]*40);f=rallies.features(rows,{})[-1]
  self.assertIsNone(f['turnover']);self.assertIsNone(f['turnover_ratio']);self.assertEqual(f['volume_ratio'],1)
 def test_phase_confirmation_peak_and_retracement_are_separate(self):
  rows=bars([10,9,9.5,10,11,12,11,10.9]);phases=rallies.identify_phases(rows,rows[0]['date'])
  self.assertEqual(len(phases),1);p=phases[0]
  self.assertEqual((p['start_index'],p['confirmed_index'],p['peak_index'],p['ended_index']),(1,3,5,6))
  detail=rallies.describe_phase(p,rows,rallies.features(rows,{}))
  self.assertEqual(detail['sessions'],4);self.assertFalse(detail['stages'][1]['available'])
  self.assertEqual(detail['confirmed_at'],rows[3]['date'])
 def test_unfinished_rally_is_not_a_closed_peak(self):
  rows=bars([10,9,9.5,10,11,12]);p=rallies.identify_phases(rows,rows[0]['date'])[0]
  self.assertEqual(p['state'],'ongoing');self.assertIsNone(p['ended_index'])
 def test_cohort_anchors_are_past_only_and_have_equal_followup(self):
  values=[10]*30+[9,9,9,11]+[11]*12+[9]*10+[12]*45
  rows=bars(values);fs=rallies.features(rows,{})
  full=rallies.candidate_cohort(rows,fs,rows[0]['date']);prefix=rallies.candidate_cohort(rows[:50],fs[:50],rows[0]['date'])
  self.assertEqual([r['date'] for r in full if r['date']<=rows[49]['date']],[r['date'] for r in prefix])
  self.assertFalse(prefix[0]['matured']);self.assertEqual(prefix[0]['group'],'pending')
  self.assertTrue(full[0]['matured']);self.assertEqual(set(full[0]['forward']),{'5','10','20','30'})
 def test_stage_denominators_keep_short_phases_and_missing_ma_separate(self):
  rows=bars([10,9,10,12,11]);p=rallies.identify_phases(rows,rows[0]['date'])[0]
  result=rallies.stage_summary([rallies.describe_phase(p,rows,rallies.features(rows,{}))])
  self.assertEqual(result[0]['n'],1);self.assertEqual(result[0]['above_ma_n']['30'],0);self.assertIsNone(result[0]['above_ma_fraction']['30'])
  self.assertEqual(result[1]['n'],0);self.assertIsNone(result[1]['change']['median'])
 def test_known_suspension_is_explained_and_never_filled(self):
  rows=bars([10,10,10],start='2023-11-27');store.save_bars('sz159952',[rows[0],rows[2]],source='test')
  sessions=['2023-11-08','2023-11-27','2023-11-28','2023-11-29','2026-11-09']
  result=history.inspect('sz159952',MOMENT,sessions)
  self.assertNotIn('2023-11-28',result['missing_dates']);self.assertIn('2023-11-28',result['verified_suspensions'])
  self.assertEqual(len(store.load_bars('sz159952')),2)
 def test_listing_date_excludes_pre_listing_without_inventing_prices(self):
  store.write_json(store.path_for('fund_meta','sz159206.json'),{'listing_date':'2025-03-14'})
  row=bars([1],start='2025-03-14')[0];store.save_bars('sz159206',[row],source='test')
  result=history.inspect('sz159206',MOMENT,['2023-11-08','2023-11-09','2025-03-14','2026-11-09'])
  self.assertEqual(result['missing_dates'],['2026-11-09']);self.assertEqual(result['expected_sessions'],2)
 def test_activity_refresh_preserves_strategy_prices_and_archives_revisions(self):
  store.save_bars('sh510300',bars([9],start='2026-11-09'),source='test');original=store.bars_path('sh510300').read_bytes()
  def payload(vol):return json.dumps({'data':{'klines':[f'2026-11-09,1,1,1,1,{vol},100,0,0,0,2']}})
  with patch.object(sources,'_http_get',return_value=payload(100)):history.refresh_activity('sh510300',MOMENT,force=True)
  with patch.object(sources,'_http_get',return_value=payload(200)):history.refresh_activity('sh510300',MOMENT,force=True)
  self.assertEqual(store.bars_path('sh510300').read_bytes(),original)
  self.assertEqual(len(list(store.path_for('archives','activity','sh510300').glob('*.json'))),1)
 def test_missing_latest_source_session_is_retried_hourly_not_each_minute(self):
  store.write_json(history.activity_path('sh510300'),{'rows':[{'date':'2026-11-06'}],'checked_at':MOMENT.isoformat(),'as_of':'2026-11-06'})
  with patch.object(sources,'_http_get',side_effect=AssertionError('Should not fetch')):self.assertTrue(history.refresh_activity('sh510300',MOMENT)['skipped'])
 def test_quartiles_and_empty_groups_are_explicit(self):
  self.assertEqual(rallies.stat([]),dict(n=0,median=None,q25=None,q75=None))
  self.assertEqual(rallies.stat([1,2,3,4]),dict(n=4,median=2.5,q25=1.75,q75=3.25))
 def test_invalid_or_gap_prices_do_not_generate_phase_results(self):
  with patch.object(history,'inspect',return_value={'state':'gaps'}):
   result=rallies.analyze_code('sh510300',MOMENT,[])
  self.assertEqual(result['state'],'unavailable');self.assertEqual(result['phases'],[])
 def test_read_path_never_starts_network_fetch_or_maintenance(self):
  with patch.object(rallies,'_KEY',None),patch.object(rallies,'_DATA',None),patch.object(coverage,'etf_codes',return_value=['sh510300']),patch.object(sources,'_http_get',side_effect=AssertionError('Read must be offline')),patch.object(history,'run_due',side_effect=AssertionError('Read must not maintain')):
   result=rallies.build_payload(moment=MOMENT)
  self.assertEqual(result['summary']['pending'],1)
 def test_concurrent_source_revision_drops_entire_mixed_result(self):
  row={'code':'sh510300','name':'ETF','state':'ready','coverage':{'state':'complete','activity_state':'complete'},'phases':[],'candidates':[]}
  with patch.object(rallies,'_KEY',None),patch.object(rallies,'_DATA',None),patch.object(coverage,'etf_codes',return_value=['sh510300']),patch.object(rallies,'_fingerprint',side_effect=['before','after']),patch.object(rallies,'analyze_code',return_value=row):
   result=rallies.build_payload(moment=MOMENT)
  self.assertEqual(result['state'],'updating');self.assertEqual(result['rows'],[]);self.assertEqual(result['summary'],{})
 def test_untracked_code_is_not_a_free_form_source_fetch(self):
  with patch.object(coverage,'etf_codes',return_value=['sh510300']):
   with self.assertRaises(ValueError):rallies.build_payload('sh519999',MOMENT)
