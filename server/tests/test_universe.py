import os,tempfile,threading,unittest
from datetime import datetime
from unittest.mock import patch
from etfmon import universe,replay,views,collectors,store

MOMENT=datetime.fromisoformat('2026-11-09T22:00:00+08:00')
class UniverseTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  env=patch.dict(os.environ,{'ETF_DATA':self.temp.name});env.start();self.addCleanup(env.stop)
 def test_summary_keeps_zero_one_multiple_and_exclusions_distinct(self):
  def row(state,count,buy=1):return dict(state=state,closed_trades=count,buy_confirmations=buy,buy_references=buy,entry_cancellations=0)
  result=universe.summarize([row('research_only',0,0),row('research_only',1),row('research_only',3),row('unsupported',0,0),row('unavailable',0,0)])
  self.assertEqual(result['total'],5);self.assertEqual(result['applicable'],3)
  self.assertEqual([result[k] for k in ['zero_trades','one_trade','multiple_trades','separate_rules','pending_data']],[1,1,1,1,1])
  self.assertEqual(result['closed_trades'],4)
 def test_audit_keeps_an_unavailable_fund_and_caches_without_upstream(self):
  def payload(code,**kwargs):
   if code=='sh512480':raise OSError('offline')
   return dict(code=code,name='ETF',state='research_only',as_of='2026-11-09',signals=[dict(side='buy'),dict(side='sell')],executions=[dict(side='buy'),dict(side='sell')],trades=[{}],cancelled=[],message='paper',diagnostics={})
  with patch.object(universe,'_KEY',None),patch.object(universe,'_DATA',None),patch.object(universe.coverage,'etf_codes',return_value=['sh510300','sh512480']),patch.object(replay,'build_payload',side_effect=payload) as call:
   result=universe.build_payload(MOMENT);self.assertEqual(len(result['rows']),2);self.assertEqual(result['summary']['pending_data'],1)
   self.assertEqual(universe.build_payload(MOMENT),result);self.assertEqual(call.call_count,2)
 def test_ma5_ma10_ma30_have_correct_warmup_and_leave_strategy_mas_intact(self):
  bars=[dict(date=f'2026-01-{i:02}',open=float(i),high=float(i)+1,low=float(i)-.5,close=float(i),volume=100) for i in range(1,31)]
  store.save_bars('sh510300',bars,source='test')
  result=views.build_bars(views.App(cache=collectors.QuoteCache(),started_at=store.now()),'sh510300','day',5000)
  self.assertEqual(set(result['ma']),{'ma5','ma10','ma30','ma20','ma60'})
  for period,expected in [(5,28),(10,25.5),(30,15.5)]:
   points=result['ma'][f'ma{period}'];self.assertEqual(points[0]['date'],bars[period-1]['date']);self.assertEqual(points[-1]['value'],expected)
 def test_changed_source_clears_every_financial_result(self):
  path=store.bars_path('sh510300');store.write_json(path,{'bars':[]})
  def changing(*args):
   store.write_json(path,{'bars':[],'revision':'changed after read'})
   return dict(state='research_only',signals=[{}],executions=[{}],trades=[{}],position={'value':1},pending={'value':1},equity_curve=[{}],summary={'model_net_return':9},diagnostics={'count':9})
  with patch.object(replay,'_build',side_effect=changing):result=replay.build_payload('sh510300',moment=MOMENT)
  self.assertEqual(result['state'],'updating');self.assertIsNone(result['position']);self.assertIsNone(result['pending']);self.assertFalse(result['summary']);self.assertFalse(result['equity_curve']);self.assertFalse(result['diagnostics'])
