"""Data integrity and causal-event tests, all offline with isolated stores."""
import copy
from datetime import datetime,timedelta
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from etfmon import coverage,holdings,share_history,research,evaluation,store,sources,maintenance

MOMENT=datetime.fromisoformat('2026-11-09T20:00:00+08:00')
def bar(day,p=10):return dict(date=day,open=p,close=p,high=p+.2,low=p-.2)

class DataUpgradeTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.env=patch.dict(os.environ,{'ETF_DATA':self.temp.name});self.env.start();self.addCleanup(self.env.stop)
 def test_real_holdings_fixture_has_quarter_units_and_no_invented_publication(self):
  path=Path(__file__).resolve().parents[2]/'tests/fixtures/holdings-510300-2026.txt'
  reports=holdings.parse_reports(path.read_text(),MOMENT.isoformat())
  self.assertEqual([r['report_period_end'] for r in reports],['2026-03-31','2026-06-30','2026-09-30'])
  latest=reports[-1];self.assertEqual(len(latest['rows']),10);self.assertIsNone(latest['published_at'])
  self.assertAlmostEqual(latest['rows'][0]['weight'],.0356);self.assertAlmostEqual(latest['rows'][0]['shares_wan'],470.63)
  self.assertEqual(latest['rows'][0]['symbol'],'300308')
 def test_holdings_revisions_immutable_idempotent_and_older_year_does_not_roll_back(self):
  old={'report_period_end':'2025-12-31','rows':[dict(rank=1,symbol='000001',name='A',weight=.1)],'fetched_at':'2026-01-20'}
  new={**old,'report_period_end':'2026-09-30'}
  self.assertEqual(holdings.save_reports('sh510300',[new,old]),2)
  self.assertEqual(holdings.save_reports('sh510300',[new]),0)
  revised=copy.deepcopy(new);revised['rows'][0]['weight']=.11
  holdings.save_reports('sh510300',[revised]);holdings.save_reports('sh510300',[old])
  self.assertEqual(len(list(store.path_for('holdings','sh510300','2026-09-30').glob('*.json'))),2)
  payload=holdings.build_payload('sh510300');self.assertEqual(payload['report_period_end'],'2026-09-30')
  self.assertAlmostEqual(payload['rows'][0]['weight_change'],.01)
  self.assertEqual(holdings.build_payload('sh510300','2025-12-31')['report_period_end'],'2025-12-31')
 def test_changed_or_malicious_holdings_payload_is_not_evaluated(self):
  with self.assertRaises(sources.SourceError):holdings.parse_reports('content: __import__("os").system("false")')
  fixture=(Path(__file__).resolve().parents[2]/'tests/fixtures/holdings-510300-2026.txt').read_text()
  with self.assertRaises(sources.SourceError):holdings.parse_reports(fixture.replace('3.56%','999%'))
 def test_partial_source_failure_preserves_reports_and_backs_off(self):
  holdings.save_reports('sh510300',[{'report_period_end':'2026-09-30','rows':[dict(rank=1,symbol='A',name='A',weight=.1)]}])
  with patch.object(holdings,'fetch_reports',side_effect=sources.SourceError('offline')),patch.object(holdings.time,'sleep'):
   result=holdings.refresh_code('sh510300',MOMENT)
   self.assertEqual(result['state'],'degraded');self.assertEqual(len(holdings.build_payload('sh510300')['rows']),1)
   self.assertTrue(holdings.refresh_code('sh510300',MOMENT+timedelta(minutes=1))['skipped'])
 def test_quarterly_new_disclosure_is_detected_by_daily_checks(self):
  report={'report_period_end':'2026-09-30','rows':[dict(rank=1,symbol='A',name='A',weight=.1)]}
  with patch.object(holdings,'fetch_reports',return_value=[report]),patch.object(holdings.time,'sleep'):
   self.assertEqual(holdings.refresh_code('sh510300',MOMENT)['updated_reports'],1)
  q4={**report,'report_period_end':'2026-12-31'}
  with patch.object(holdings,'fetch_reports',return_value=[q4]),patch.object(holdings.time,'sleep'):
   self.assertEqual(holdings.refresh_code('sh510300',MOMENT.replace(year=2027,month=1,day=25))['updated_reports'],1)
  self.assertEqual(holdings.build_payload('sh510300')['report_period_end'],'2026-12-31')
 def test_calendar_uses_trading_dates_not_weekends_and_listing_excludes_pre_listing(self):
  days=['2026-01-05','2026-01-06','2026-01-08']
  store.save_bars('sh000300',[bar(d) for d in days],source='test')
  store.save_bars('sh510300',[bar(d) for d in days[1:]],source='test')
  self.assertEqual(coverage.inspect('sh510300',MOMENT)['status'],'short_history')
  store.write_json(store.path_for('fund_meta','sh510300.json'),{'listing_date':'2026-01-06'})
  row=coverage.inspect('sh510300',MOMENT)
  self.assertEqual(row['status'],'complete');self.assertEqual(row['expected_sessions'],2)
  self.assertFalse(row['research_history_sufficient'])
 def test_gap_repair_archives_and_preserves_old_dates(self):
  days=['2025-12-31','2026-01-05','2026-01-06','2026-01-07']
  store.save_bars('sh000300',[bar(d) for d in days],source='test')
  store.save_bars('sh510300',[bar(days[0]),bar(days[1]),bar(days[3])],source='test')
  with patch.object(sources,'fetch_bars',return_value=([bar(d) for d in days[1:]],'test')):
   result=coverage.repair('sh510300',threading.RLock(),MOMENT)
  self.assertEqual(result['state'],'complete');self.assertEqual([r['date'] for r in store.load_bars('sh510300')],days)
  self.assertEqual(len(list(store.path_for('archives','bars','sh510300').glob('*.json'))),1)
 def test_prior_year_warmup_is_repaired_when_ytd_already_complete(self):
  day='2026-01-05';store.save_bars('sh000300',[bar(day)],source='test')
  store.save_bars('sh510300',[bar(day)],source='test')
  old=[bar((datetime(2025,8,1)+timedelta(days=i)).date().isoformat()) for i in range(80)]
  with patch.object(sources,'fetch_bars',return_value=(old+[bar(day)],'test')):
   coverage.repair('sh510300',threading.RLock(),MOMENT)
  self.assertTrue(coverage.inspect('sh510300',MOMENT)['research_history_sufficient'])
 def test_adjustment_conflict_does_not_destroy_prices(self):
  days=['2026-01-05','2026-01-06'];store.save_bars('sh000300',[bar(d) for d in days],source='test')
  old=[bar(days[0])];store.save_bars('sh510300',old,source='test')
  with patch.object(sources,'fetch_bars',return_value=([bar(d,50) for d in days],'test')):
   result=coverage.repair('sh510300',threading.RLock(),MOMENT)
  self.assertEqual(result['state'],'adjustment_conflict');self.assertEqual(store.load_bars('sh510300'),old)
 def test_official_share_units_and_date_validation(self):
  import json
  raw={'result':[{'SEC_CODE':'510300','STAT_DATE':'2026-01-05','TOT_VOL':'12.34','SEC_NAME':'x'}, {'SEC_CODE':'510300','STAT_DATE':'2026-01-06','TOT_VOL':'999'}]}
  with patch.object(sources,'_http_get',return_value=json.dumps(raw)):
   result=share_history.fetch_day('2026-01-05')
  self.assertAlmostEqual(result['rows']['sh510300']['shares'],123400)
 def test_stale_calendar_does_not_hide_latest_session(self):
  rows=[bar('2026-11-06')]
  with patch.object(coverage,'tracked_codes',return_value=[]),patch.object(coverage,'etf_codes',return_value=[]),patch.object(coverage,'repair',return_value={}) as repair,patch.object(store,'load_bars',return_value=rows),patch.object(store,'now',return_value=MOMENT),patch.object(share_history,'backfill',return_value={}),patch.object(evaluation,'run_due'),patch.object(coverage,'build_summary',return_value={}):
   maintenance.run_due(threading.RLock())
   self.assertEqual(repair.call_count,3)
   maintenance.run_due(threading.RLock())
   self.assertEqual(repair.call_count,3)
  self.assertEqual(coverage.calendar_target(MOMENT),'2026-11-09')
  self.assertEqual(coverage.calendar_target(MOMENT.replace(hour=10)),'2026-11-06')
 def test_maintenance_failure_clears_running_and_lock(self):
  with patch.object(coverage,'tracked_codes',side_effect=RuntimeError('disk')):
   with self.assertRaises(RuntimeError):maintenance.run_due(threading.RLock())
  self.assertFalse(store.read_json(store.path_for('maintenance','status.json'),{})['running'])
  self.assertTrue(maintenance._RUN_LOCK.acquire(blocking=False));maintenance._RUN_LOCK.release()
 def test_event_outcomes_use_next_open_and_reject_incomplete_future(self):
  rows=[bar(f'2026-01-{d:02}',10+d) for d in range(1,11)]
  value=evaluation.outcomes(rows,rows[1]['date'],5)
  self.assertEqual(value['entry_date'],rows[2]['date']);self.assertEqual(value['exit_date'],rows[7]['date'])
  self.assertAlmostEqual(value['gross_return'],rows[7]['close']/rows[2]['open']-1)
  self.assertIsNone(evaluation.outcomes(rows,rows[-2]['date'],5))
  rows[2]['high']=rows[2]['low'];self.assertIsNone(evaluation.outcomes(rows,rows[1]['date'],5))
 def test_two_path_historical_events_are_prefix_causal(self):
  import math
  rows=[bar((datetime(2025,1,1)+timedelta(days=i)).date().isoformat(),100+i*.03+math.sin(i*.4)) for i in range(160)]
  states={r['date']:'supportive' for r in rows};entity={'code':'sh512480','name':'半导体'}
  def screen(data):return research.screen(entity,data,data[-1]['date'],collect_events=True)
  events=screen(rows)['events'];self.assertTrue(events)
  for length in (80,120,150):
   expected=[e for e in events if e['date']<=rows[length-1]['date']]
   self.assertEqual(screen(rows[:length])['events'],expected)
   self.assertTrue(all(e['date']>=rows[64]['date'] for e in expected))
