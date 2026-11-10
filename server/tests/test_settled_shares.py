import copy,io,os,tempfile,unittest,zipfile
from datetime import datetime
from unittest.mock import patch
from xml.sax.saxutils import escape
from etfmon import config,settled_shares as shares,store,views,team_minute
NOW=datetime.fromisoformat('2026-11-10T11:00:00+08:00')
CODES=('sh510300','sz159915')
def workbook(rows):
    body=''.join('<row>'+''.join(f'<c r="{chr(65+i)}{n}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>' for i,v in enumerate(row))+'</row>' for n,row in enumerate(rows,1))
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="'+shares.NS['m']+'"><sheetData>'+body+'</sheetData></worksheet>')
    return out.getvalue()
class SettledSharesTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'ETF_DATA':self.tmp.name});self.env.start()
        self.codes=patch.object(config,'TEAM_CODES',CODES);self.codes.start()
    def tearDown(self):self.codes.stop();self.env.stop();self.tmp.cleanup()
    def seed(self):
        for market,code,values in [('sse',CODES[0],(100.,110.)),('szse',CODES[1],(200.,210.))]:
            for date,value in zip(('2026-11-06','2026-11-09'),values):
                store.write_json(store.path_for('share_history',market,date+'.json'),dict(date=date,source=market+'_settled_etf_shares',unit='份',rows={code:dict(shares=value)},fetched_at=NOW.isoformat()))
            store.save_bars(code,[dict(date=d,open=p,high=p,low=p,close=p,volume=1) for d,p in [('2026-11-06',2),('2026-11-09',3)]],source='test')
            store.write_json(store.path_for('share_history','prices',code+'.json'),dict(source='tencent_unadjusted_daily',adjustment='none',closes={'2026-11-06':2,'2026-11-09':3}))
    def test_xlsx_inline_dates_units_and_code_coverage(self):
        raw=workbook([['日期','基金代码','基金简称','基金规模(份)'],['2026-11-09','159915','创业板ETF','45,862,454,936'],['2026-11-06','159915','创业板ETF','45372454936'],['2026-11-09','159999','其他','123']])
        parsed=shares.parse_szse(raw,'2026-11-09','2026-11-09')
        self.assertEqual(list(parsed),['2026-11-09']);self.assertEqual(parsed['2026-11-09']['sz159915']['shares'],45862454936)
    def test_adjusted_daily_data_is_rejected_for_money_flows(self):
        raw={'data':{CODES[0]:{'qfqday':[['2026-11-09','1','1','1','1','1']]}}}
        import json
        with patch.object(shares.sources,'_http_get',return_value=json.dumps(raw)):
            with self.assertRaises(shares.sources.NoDataError):shares.fetch_unadjusted_closes(CODES[0],'2026-11-09')
        raw['data'][CODES[0]]['day']=[['2026-11-09','3','3','3','3','1']]
        with patch.object(shares.sources,'_http_get',return_value=json.dumps(raw)):
            self.assertEqual(shares.fetch_unadjusted_closes(CODES[0],'2026-11-09')['closes']['2026-11-09'],3)

    def test_activity_fields_keep_units_date_null_and_real_zero_separate(self):
        self.seed();q={CODES[0]:dict(price=4,amount=1875170000,volume=4517957,turnover=.0097,quote_time=NOW.isoformat()),CODES[1]:dict(price=4,amount=0,volume=0,turnover=0)}
        v=shares.build_view(q,NOW);row=v['rows'][0]
        self.assertEqual(row['quote_amount'],1875170000);self.assertEqual(row['quote_volume'],4517957);self.assertEqual(row['turnover'],.0097)
        self.assertNotEqual(row['quote_amount'],row['amount']);self.assertEqual(row['shares_date'],'2026-11-09')
        self.assertEqual(v['rows'][1]['quote_amount'],0);self.assertEqual(v['rows'][1]['turnover'],0)
        q[CODES[1]].update(amount=None,volume=-1,turnover=float('nan'));row=shares.build_view(q,NOW)['rows'][1]
        self.assertIsNone(row['quote_amount']);self.assertIsNone(row['quote_volume']);self.assertIsNone(row['turnover'])

    def test_xlsx_wrong_units_or_conflicting_duplicate_rejected(self):
        header=['日期','基金代码','基金简称','基金规模(份)'];row=['2026-11-09','159915','ETF',100]
        with self.assertRaises(Exception):shares.parse_szse(workbook([header,row,[*row[:3],200]]),'2026-11-09','2026-11-09')
        header[-1]='基金规模(万份)'
        with self.assertRaises(Exception):shares.parse_szse(workbook([header,row]),'2026-11-09','2026-11-09')
    def test_correct_dates_exact_close_and_read_only(self):
        self.seed();q={c:dict(price=4,shares=999,quote_time=NOW.isoformat()) for c in CODES};before=copy.deepcopy(q)
        with patch.object(store,'write_json',side_effect=AssertionError('read cannot write')),patch.object(shares,'fetch_szse',side_effect=AssertionError('read cannot fetch')):
            view=shares.build_view(q,NOW,['2026-11-06','2026-11-09'])
        self.assertEqual(q,before);self.assertEqual(view['latest_date'],'2026-11-09');self.assertFalse(view['quality']['current_day_published'])
        self.assertEqual(view['history'][-1]['amount'],60)
        self.assertEqual([r['share_delta'] for r in view['rows']],[10,10]);self.assertTrue(all(r['amount']==30 for r in view['rows']))
        self.assertTrue(all(r['shares_date']=='2026-11-09' and r['quote_time']==NOW.isoformat() for r in view['rows']))
    def test_missing_official_record_is_not_zero_or_partial_whole_basket(self):
        self.seed();store.path_for('share_history','szse','2026-11-09.json').unlink()
        q={c:dict(price=4,shares=999,quote_time=NOW.isoformat()) for c in CODES}
        view=shares.build_view(q,NOW,['2026-11-06','2026-11-09'])
        self.assertEqual(view['latest_date'],'2026-11-06');self.assertIsNone(view['rows'][1]['share_delta']);self.assertIsNone(view['rows'][1]['amount'])
        store.path_for('share_history','szse','2026-11-06.json').unlink()
        view=shares.build_view(q,NOW);self.assertIsNone(view['rows'][1]['shares']);self.assertIsNone(view['total_shares']);self.assertIsNone(view['total_value']);self.assertEqual(view['history'],[])
    def test_missing_settlement_close_cannot_use_current_price(self):
        self.seed();store.path_for('share_history','prices',CODES[0]+'.json').unlink()
        view=shares.build_view({c:dict(price=4,shares=999) for c in CODES},NOW)
        self.assertEqual(view['rows'][0]['share_delta'],10);self.assertIsNone(view['rows'][0]['amount']);self.assertIsNone(view['history'][-1]['amount'])
    def test_gap_is_not_a_one_day_change_and_zero_is_real(self):
        self.seed();p=store.path_for('share_history','sse','2026-11-09.json');data=store.read_json(p,{});data['rows'][CODES[0]]['shares']=100.;store.write_json(p,data)
        v=shares.build_view({},NOW,['2026-11-06','2026-11-09']);self.assertEqual(v['rows'][0]['share_delta'],0);self.assertEqual(v['rows'][0]['amount'],0)
        v=shares.build_view({},NOW,['2026-11-06','2026-11-07','2026-11-09']);self.assertIsNone(v['rows'][0]['share_delta']);self.assertGreater(v['history'][-1]['gap_days'],1)
    def test_update_failure_keeps_original_evidence_and_throttles(self):
        self.seed();p=store.path_for('share_history','szse','2026-11-09.json');before=p.read_bytes()
        with patch.object(shares.coverage,'calendar',return_value=['2026-11-09']),patch.object(shares,'fetch_szse',side_effect=OSError('network')) as fetch,patch.object(shares,'refresh_prices',return_value=[]):
            first=shares.refresh_due(NOW,force=True);again=shares.refresh_due(NOW)
        self.assertIn('network',first['error']);self.assertTrue(again['skipped']);self.assertEqual(fetch.call_count,1);self.assertEqual(p.read_bytes(),before)
    def test_team_api_never_relabels_prior_clearing_as_current_intraday(self):
        self.seed();store.save_daily(dict(days={'2026-11-10':{c:dict(shares=999,price=4) for c in CODES}},intraday={'2026-11-10':[dict(time='11:00',amount=123,share_delta=1)]}))
        q={c:dict(price=4,pre_close=3,shares=999,quote_time=NOW.isoformat()) for c in CODES}
        with patch.object(store,'now',return_value=NOW),patch.object(views,'_cached_quotes',return_value=(q,NOW.isoformat(),False)):
            view=views.build_team(None)
        self.assertIsNone(view['live_flow']);self.assertEqual(len(view['intraday']),1);self.assertEqual(view['latest_close']['date'],'2026-11-09')
        self.assertEqual(view['latest_close']['amount'],60);self.assertEqual(view['basket']['total_shares'],320)
        self.assertTrue(all(r['shares_date']=='2026-11-09' for r in view['rows']))
        with patch.object(store,'now',return_value=NOW),patch.object(views,'_cached_quotes',return_value=(q,NOW.isoformat(),False)),patch.object(views,'build_hotspots',return_value={}):
            overview=views.build_overview(None)
        self.assertFalse(overview['national']['today_flow_partial']);self.assertEqual(overview['national']['flow_date'],'2026-11-09')

    def test_verified_minute_weights_never_use_legacy_snapshot(self):
        self.seed();store.save_daily(dict(days={'2026-11-09':{c:dict(shares=999) for c in CODES}}))
        with patch.object(store,'now',return_value=NOW):weights,date=team_minute.latest_shares()
        self.assertEqual(weights,{CODES[0]:110,CODES[1]:210});self.assertEqual(date,'2026-11-09')

    def test_overview_and_team_share_the_same_snapshot_estimate_and_keep_clearing_separate(self):
        self.seed();store.save_daily(dict(days={'2026-11-09':{CODES[0]:dict(shares=100),CODES[1]:dict(shares=200)}}))
        q={CODES[0]:dict(shares=110,price=4,pre_close=3,quote_time=NOW.isoformat()),CODES[1]:dict(shares=210,price=4,pre_close=3,quote_time=NOW.isoformat())}
        with patch.object(store,'now',return_value=NOW),patch.object(views,'_cached_quotes',return_value=(q,NOW.isoformat(),False)),patch.object(views,'build_hotspots',return_value={}):
            team=views.build_team(None);overview=views.build_overview(None)
        self.assertEqual(overview['national']['snapshot_estimate'],team['live_flow']);self.assertEqual(team['live_flow']['amount'],80)
        self.assertEqual(overview['national']['today_flow'],60);self.assertEqual(overview['national']['flow_date'],'2026-11-09')
        self.assertEqual(team['latest_close']['amount'],60)
        self.assertIn('快照差额重估',overview['brief']['bullets'][0]);self.assertNotIn('净申购',overview['brief']['bullets'][0])
if __name__=='__main__':unittest.main()
