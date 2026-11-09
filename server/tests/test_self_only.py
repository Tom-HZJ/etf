"""External market files must not influence a fund's causal trading decisions."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from etfmon import analytics, config, evaluation, replay, research, store, views

CODE='sh512480'
def own_bars():
 prices=[100+i*.04 for i in range(70)]+[102.4,102.45,102.7]+[102.71]*8
 dates=[];day=datetime(2026,1,1)
 while len(dates)<len(prices):
  if day.weekday()<5:dates.append(day.date().isoformat())
  day+=timedelta(days=1)
 return [dict(date=dates[i],
              open=p,high=p+.1,low=p-.1,close=p,volume=100.) for i,p in enumerate(prices)]

class SelfOnlyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'ETF_DATA':self.tmp.name});self.env.start()
  self.data=own_bars();store.write_json(store.bars_path(CODE),{'bars':self.data,'source':'fixture'})
  self.moment=datetime.fromisoformat(self.data[-1]['date']+'T16:00:00+08:00')
 def tearDown(self):self.env.stop();self.tmp.cleanup()
 def test_replay_never_reads_any_external_symbol_and_keeps_own_cache(self):
  load=store.load_bars;seen=[]
  def guarded(code):
   seen.append(code);self.assertEqual(code,CODE);return load(code)
  with patch.object(store,'load_bars',side_effect=guarded):a=replay.build_payload(CODE,moment=self.moment)
  self.assertEqual(a['state'],'research_only');self.assertTrue(a['signals']);self.assertEqual(set(seen),{CODE})
  for code in ['sh000300','sh510300','sh000905','sz399006']:
   store.write_json(store.bars_path(code),{'bars':[{'date':self.data[-1]['date'],'close':-99999}]})
  with patch.object(store,'load_bars',side_effect=AssertionError('External changes must not invalidate own cache')):
   b=replay.build_payload(CODE,moment=self.moment)
  self.assertEqual(a,b);self.assertFalse(a['external_reference_required']);self.assertNotIn('benchmark_sessions',a['diagnostics'])
 def test_own_source_changes_invalidate_and_bad_ohlc_blocks(self):
  replay.build_payload(CODE,moment=self.moment)
  self.data[-1]['low']=9999;store.write_json(store.bars_path(CODE),{'bars':self.data})
  self.assertEqual(replay.build_payload(CODE,moment=self.moment)['state'],'invalid_data')
 def test_screen_and_event_study_work_with_no_benchmark_files(self):
  def own(code):self.assertEqual(code,CODE);return self.data
  with patch.object(config,'HOTSPOT_CODES',[CODE]),patch.object(store,'load_bars',side_effect=own):
   p=research.build_payload(self.moment);study=evaluation.build(self.moment)
  self.assertEqual(p['decision_scope'],'self_only');self.assertNotIn('benchmark',p)
  self.assertEqual(study['state'],'research_only');self.assertTrue(study['events']);self.assertFalse(study['external_reference_required'])
 def test_one_stale_fund_never_blocks_another_fresh_fund(self):
  other='sh512980'
  def own(code):return self.data if code==CODE else self.data[:-1]
  with patch.object(config,'HOTSPOT_CODES',[CODE,other]),patch.object(store,'load_bars',side_effect=own):p=research.build_payload(self.moment)
  rows={r['code']:r for r in p['rows']}
  self.assertEqual(rows[other]['phase'],'stale');self.assertNotEqual(rows[CODE]['phase'],'stale');self.assertFalse(p['data_stale'])
 def test_heat_ignores_deprecated_relative_field(self):
  row={'ret_20d':.05,'ret_5d':.01,'close_above_ma20':True}
  self.assertEqual(analytics.heat_score({**row,'relative_20d':100}),analytics.heat_score({**row,'relative_20d':-100}))
  self.assertEqual(analytics.hot_state({**row,'relative_20d':100}),analytics.hot_state({**row,'relative_20d':-100}))
 def test_hotspot_own_ranks_ignore_legacy_snapshots_and_external_prices(self):
  def own(code):self.assertEqual(code,CODE);return self.data
  legacy=[{'date':self.data[i]['date'],'rows':[{'code':CODE,'rank':99}]} for i in range(50,70)]
  with patch.object(config,'HOTSPOT_CODES',[CODE]),patch.object(store,'load_bars',side_effect=own),patch.object(store,'load_hotspots_history',return_value=legacy),patch.object(views,'_hotspot_flow',return_value={}):
   a=views.compute_hotspot_payload({});b=views.compute_hotspot_payload({'sh510300':{'market_cap':1e9,'price':1e9}})
  self.assertEqual(a['rows'],b['rows']);self.assertIsNone(a['rows'][0]['rank_change_5d']);self.assertIsNone(a['rows'][0]['relative_20d'])
 def test_backfill_uses_own_dates_without_index_and_preserves_old_history(self):
  with patch.object(config,'HOTSPOT_CODES',[CODE]):
   store.save_hotspots_history({'date':self.data[-1]['date'],'rows':[],'rule_version':'legacy'})
   self.assertEqual(views.backfill_hotspot_history(count=2),1)
   items=store.load_hotspots_history();versions={x['date']:x.get('rule_version') for x in items}
   self.assertEqual(versions[self.data[-1]['date']],'legacy');self.assertEqual(versions[self.data[-2]['date']],config.HOTSPOT_RULE_VERSION)

if __name__=='__main__':unittest.main()
