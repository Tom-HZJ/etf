import copy
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime,timedelta
from unittest.mock import patch
from etfmon import actions,config,store,sources

def moment(text='2026-11-10T09:36:10+08:00'):return datetime.fromisoformat(text)
def plan(position=None,pending=None):
    return dict(code='sh512980',name='半导体',direction='科技',model='expanded',protection='none',
                data_date='2026-11-09',daily_stale=False,daily_source='tencent',daily_collected_at='2026-11-09T15:10:00+08:00',
                daily_rule='test-v5',close=10.,ma5=9.99,ma10=10.,atr14=.2,last4_closes=[10.]*4,last9_closes=[10.]*9,
                entry_low=10.,entry_high=10.4,entry_stop=9.8,entry_valid=True,pending=pending,position=position,
                risk_line=9.8 if position else None,target=10.5 if position else None,armed_date=None,live_window=False)
def quote(price=10.15,when=None):return dict(code='sh512980',price=price,pre_close=10.,open=10.1,quote_time=(when or moment()).isoformat(),source='tencent',stale=False)
def samples():
    return [dict(minute=f'2026-11-10T09:{30+i:02d}:00+08:00',close=10.01+i*.02,count=3,
                 last_source_time=f'2026-11-10T09:{30+i:02d}:40+08:00',last_second=40) for i in range(6)]

class ActionsTests(unittest.TestCase):
    def test_complete_minute_buy_is_a_present_observation_not_backdated_fill(self):
        r=actions.evaluate(plan(),quote(),samples(),moment())
        self.assertEqual(r['kind'],'buy');self.assertTrue(r['actionable'])
        self.assertEqual(r['trigger_time'],quote()['quote_time']);self.assertEqual(r['reference_price'],10.15)
        self.assertEqual(r['basis_time'],samples()[-1]['last_source_time']);self.assertEqual(r['confirmation'],'intraday_observation')
    def test_incomplete_minute_and_gaps_cannot_trigger(self):
        for mutate in ('count','second','gap','future'):
            ss=samples()
            if mutate=='count':ss[-1]['count']=1
            elif mutate=='second':ss[-1]['last_second']=20
            elif mutate=='gap':ss[1]['minute']='2026-11-10T09:20:00+08:00'
            else:ss[-1]['minute']='2026-11-10T10:00:00+08:00'
            self.assertFalse(actions.evaluate(plan(),quote(),ss,moment())['actionable'])
    def test_duplicate_source_quote_does_not_create_fake_samples(self):
        ss=[];q=quote(when=moment('2026-11-10T09:30:40+08:00'))
        for i in range(5):ss=actions.observe(ss,q,moment('2026-11-10T09:30:50+08:00'))
        self.assertEqual(ss[0]['count'],1)
    def test_stale_unknown_future_and_auction_quotes_do_not_trigger(self):
        for stamp in ('2026-11-09T15:00:00+08:00','2026-11-10T09:30:00+08:00','2026-11-10T09:37:00+08:00',None):
            q=quote();q['quote_time']=stamp
            self.assertFalse(actions.evaluate(plan(),q,samples(),moment())['actionable'])
        self.assertFalse(actions.quote_valid(quote(when=moment('2026-11-10T09:29:50+08:00')),moment('2026-11-10T09:30:10+08:00')))
    def test_fresh_quote_can_withdraw_a_delayed_minute_breakout(self):
        self.assertFalse(actions.evaluate(plan(),quote(10.05),samples(),moment())['actionable'])
        self.assertFalse(actions.evaluate(plan(),quote(10.6),samples(),moment())['actionable'])
    def test_expired_alignment_does_not_open_a_new_window(self):
        p=plan();p['ma5']=10.1
        self.assertFalse(actions.evaluate(p,quote(),samples(),moment())['actionable'])
        p['live_window']=True
        self.assertTrue(actions.evaluate(p,quote(),samples(),moment())['actionable'])
    def test_corporate_action_basis_conflicts_are_blocked(self):
        q=quote();q['pre_close']=5.
        r=actions.evaluate(plan(),q,samples(),moment());self.assertEqual(r['kind'],'stale')
    def test_no_new_buy_when_model_has_a_position(self):
        p=plan(position=dict(entry_date='2026-11-09'))
        r=actions.evaluate(p,quote(),samples(),moment());self.assertEqual(r['kind'],'holding')
    def test_stop_reference_and_t1_are_separate_from_confirmed_exit(self):
        p=plan(position=dict(entry_date='2026-11-09'))
        r=actions.evaluate(p,quote(9.7),[],moment());self.assertEqual(r['kind'],'risk');self.assertEqual(r['confirmation'],'close_required')
        p['protection']='trailing7';r=actions.evaluate(p,quote(9.7),[],moment());self.assertEqual(r['confirmation'],'intraday_reference')
        p['position']['entry_date']='2026-11-10'
        self.assertEqual(actions.evaluate(p,quote(9.7),[],moment())['kind'],'holding')
    def test_profit_watch_does_not_become_a_confirmed_daily_sale(self):
        p=plan(position=dict(entry_date='2026-11-09'))
        self.assertEqual(actions.evaluate(p,quote(10.6),[],moment())['confirmation'],'close_required')
        p['pending']=dict(side='sell',date='2026-11-09',reason='已确认退出')
        r=actions.evaluate(p,quote(10.3),[],moment());self.assertEqual(r['confirmation'],'daily_confirmed');self.assertEqual(r['sell_price'],10.1)
    def test_lunch_close_weekend_and_premarket_do_not_highlight(self):
        for text in ('2026-11-10T08:50:00+08:00','2026-11-10T11:30:00+08:00','2026-11-10T12:15:00+08:00','2026-11-10T15:00:00+08:00','2026-11-14T10:00:00+08:00'):
            t=moment(text);self.assertFalse(actions.evaluate(plan(),quote(when=t),samples(),t)['actionable'])
    def test_future_samples_cannot_change_current_observation(self):
        a=actions.evaluate(plan(),quote(),samples(),moment())
        future=samples()+[dict(minute='2026-11-10T10:00:00+08:00',close=1000.,count=3,last_second=50,last_source_time='2026-11-10T10:00:50+08:00')]
        self.assertEqual(a,actions.evaluate(plan(),quote(),future,moment()))
    def test_process_deduplicates_preserves_price_and_never_fetches(self):
        p=plan();plans={'expanded-none':{'sh512980':p}}
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ETF_DATA':tmp}),patch.object(actions,'build_plans',return_value=plans),patch.object(sources,'fetch_quotes',side_effect=AssertionError('No requests')):
            state=dict(date='2026-11-10',samples={'sh512980':samples()},active={},recent=[]);store.write_json(actions.path('state.json'),state)
            a=actions.process({'sh512980':quote()},moment());b=actions.process({'sh512980':quote(10.16, moment()+timedelta(seconds=20))},moment()+timedelta(seconds=20))
            self.assertEqual(len(b['recent']),1);row=b['boards']['expanded-none']['rows'][0]
            self.assertEqual(row['reference_price'],10.15);self.assertEqual(row['price'],10.16)
            self.assertEqual(len(list(actions.path('events','2026-11-10').glob('*.json'))),1)
            files={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in actions.path().rglob('*.json')}
            payload=actions.build_payload('expanded','none',moment()+timedelta(seconds=25));self.assertTrue(payload['rows'][0]['actionable'])
            expired=actions.build_payload('expanded','none',moment()+timedelta(minutes=3));self.assertFalse(expired['rows'][0]['actionable'])
            self.assertEqual(files,{str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in actions.path().rglob('*.json')})
            c=actions.process({'sh512980':quote(10.05,moment()+timedelta(seconds=40))},moment()+timedelta(seconds=40))
            self.assertFalse(c['boards']['expanded-none']['rows'][0]['actionable'])
    def test_invalid_modes_are_rejected(self):
        for model,protection in [('current','trailing7'),('legacy','none'),('expanded','unknown')]:
            with self.assertRaises(ValueError):actions.build_payload(model,protection,moment())

    def test_first_buy_point_survives_rearming_and_api_reads_are_pure(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ETF_DATA':tmp}),patch.object(actions,'build_plans',return_value={'expanded-none':{'sh512980':plan()}}):
            store.write_json(actions.path('state.json'),dict(date='2026-11-10',samples={'sh512980':samples()},active={},recent=[]))
            first=actions.process({'sh512980':quote()},moment())
            down=moment()+timedelta(seconds=20)
            actions.process({'sh512980':quote(10.05,down)},down)
            up=moment()+timedelta(seconds=40)
            again=actions.process({'sh512980':quote(10.16,up)},up)
            self.assertEqual(len(again['recent']),2)
            self.assertEqual(len(again['buy_points']),1)
            point=again['buy_points'][0]
            self.assertEqual(point,first['buy_points'][0])
            self.assertEqual(point['reference_price'],10.15)
            self.assertEqual(point['trigger_time'],quote()['quote_time'])
            before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in actions.path().rglob('*.json')}
            payload=actions.build_payload('expanded','none',up)
            self.assertEqual(len(payload['buy_points']),1)
            self.assertTrue(payload['buy_points'][0]['active'])
            expired=actions.build_payload('expanded','none',up+timedelta(minutes=3))
            self.assertFalse(expired['buy_points'][0]['active'])
            self.assertEqual(expired['buy_points'][0]['reference_price'],10.15)
            self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in actions.path().rglob('*.json')})

    def test_upgrade_restores_only_actual_earliest_event_without_relabelling_it(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ETF_DATA':tmp}):
            e={**actions.evaluate(plan(),quote(),samples(),moment()),'event_id':'old','observed_at':moment().isoformat(),'live_recorded':True,'version':'own-minute-observation-v1'}
            store.write_json(actions.path('events','2026-11-10','old.json'),e)
            later={**e,'event_id':'later','observed_at':(moment()+timedelta(seconds=20)).isoformat(),'trigger_time':(moment()+timedelta(seconds=20)).isoformat(),'reference_price':10.2}
            store.write_json(actions.path('events','2026-11-10','later.json'),later)
            for i,bad in enumerate(({**e,'live_recorded':False},{**e,'observed_at':'2026-11-11T09:36:10+08:00'},
                                  {**e,'reference_price':'NaN'},{**e,'trigger_time':'2026-11-10T09:38:10+08:00'})):
                store.write_json(actions.path('events','2026-11-10',f'bad{i}.json'),bad)
            hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in actions.path('events','2026-11-10').glob('*.json')}
            restored=actions.restore_buy_points('2026-11-10',moment()+timedelta(minutes=1))
            self.assertEqual(list(restored),['expanded-none|sh512980'])
            self.assertEqual(restored['expanded-none|sh512980'],e)
            self.assertTrue(all(hashlib.sha256(p.read_bytes()).hexdigest()==digest for p,digest in hashes.items()))

    def test_point_identity_is_separate_for_etfs_models_and_days(self):
        points={}
        e={**actions.evaluate(plan(),quote(),samples(),moment()),'event_id':'one','observed_at':moment().isoformat(),'live_recorded':True}
        for event in (e,{**e,'code':'sh512660','event_id':'two'}, {**e,'model':'current','event_id':'three'}):
            actions.remember_buy_point(points,event,'2026-11-10',moment())
        self.assertEqual(len(points),3)
        actions.remember_buy_point(points,e,'2026-11-11',moment()+timedelta(days=1))
        self.assertEqual(len(points),3)

    def test_team_context_scope_keeps_collection_and_only_seven_trade_exceptions(self):
        original=config.TEAM_CODES
        excluded=set(config.CONTEXT_ONLY_TEAM_CODES)
        eligible={c for c in config.TEAM_CODES if actions.trade_eligible(c)}
        self.assertEqual(len(original),26)
        self.assertEqual(len(excluded),19)
        self.assertEqual(eligible,{'sh588000','sh588050','sh588080','sz159915','sz159949','sz159952','sz159977'})
        self.assertTrue(excluded.issubset(config.QUOTE_CODES))
        self.assertTrue(actions.trade_eligible('sh512980'))
        with patch.object(actions,'fingerprint',side_effect=RuntimeError('filtered-before-plan-read')) as fingerprint:
            with self.assertRaises(RuntimeError):actions.build_plans(moment(),original)
            self.assertEqual(fingerprint.call_args.args[1],sorted(eligible))
        self.assertEqual(config.TEAM_CODES,original)

    def test_api_hides_context_funds_even_from_a_preupgrade_snapshot(self):
        def row(code):
            return dict(code=code,kind='buy',actionable=True,quote_time=quote()['quote_time'],price=10.1,event_id=code)
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ETF_DATA':tmp}):
            store.write_json(actions.path('latest.json'),dict(date='2026-11-10',generated_at=moment().isoformat(),
                boards={'expanded-none':dict(rows=[row('sh510300'),row('sh588000'),row('sz159915'),row('sh512980')])}))
            payload=actions.build_payload('expanded','none',moment())
            self.assertEqual([r['code'] for r in payload['rows']],['sh512980'])  # no size comparison yet: do not guess peers
            self.assertEqual(len(payload['trade_scope']['excluded_codes']),19)
            self.assertFalse(payload['buy_point_policy'])

    def test_user_sector_exclusions_block_plans_before_reading_history(self):
        excluded={'sz159745','sh512200','sh516950','sh563300','sh515210','sh512660','sh512810'}
        self.assertEqual(set(config.USER_EXCLUDED_TRADE_CODES),excluded)
        self.assertTrue(excluded.issubset(config.QUOTE_CODES))
        self.assertTrue(all(not actions.trade_eligible(c) for c in excluded))
        self.assertTrue(actions.trade_eligible('sh512980'))
        self.assertTrue(actions.trade_eligible('sh588000'))
        for code,entity in config.ENTITIES.items():
            if entity.direction in {'建材','房地产','基建','建筑','钢铁','军工','国防军工'}:self.assertFalse(actions.trade_eligible(code))
        with patch.object(actions,'fingerprint',side_effect=RuntimeError('stop-before-history')) as read:
            with self.assertRaises(RuntimeError):actions.build_plans(moment(),[*excluded,'sh512980'])
            self.assertEqual(read.call_args.args[1],['sh512980'])

    def test_user_sector_filter_removes_stale_snapshot_alerts_and_points_without_writes(self):
        def row(code,kind):return dict(code=code,kind=kind,actionable=True,quote_time=quote()['quote_time'],
            price=10.1,event_id=code,model='expanded',protection='none',observed_at=moment().isoformat(),live_recorded=True)
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ETF_DATA':tmp}):
            rows=[row('sz159745','buy'),row('sh512200','buy'),row('sh516950','risk'),row('sh515210','buy'),
                  row('sh512660','buy'),row('sh512810','risk'),row('sh512980','buy')]
            payload=dict(date='2026-11-10',generated_at=moment().isoformat(),boards={'expanded-none':dict(rows=rows)},
                         buy_points=rows,recent=rows,buy_point_policy=actions.BUY_POINT_POLICY)
            store.write_json(actions.path('latest.json'),payload)
            original=actions.path('latest.json').read_bytes()
            result=actions.build_payload('expanded','none',moment())
            for key in ('rows','buy_points','history'):
                self.assertEqual([r['code'] for r in result[key]],['sh512980'])
            self.assertEqual(set(result['trade_scope']['user_excluded_codes']),set(config.USER_EXCLUDED_TRADE_CODES))
            self.assertEqual(actions.path('latest.json').read_bytes(),original)

    def test_live7_ratchets_known_high_but_never_reuses_an_earlier_low(self):
        p=plan(position=dict(entry_date='2026-11-09',peak_high=10.5));p['protection']='trailing7';p['risk_line']=10.5*.93
        def plans(*args):return {'expanded-trailing7':{'sh512980':copy.deepcopy(p)}}
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ETF_DATA':tmp}),patch.object(actions,'build_plans',side_effect=plans):
            q=quote(11.5);q.update(high=12.,low=8.)
            a=actions.process({'sh512980':q},moment());row=a['boards']['expanded-trailing7']['rows'][0]
            self.assertNotEqual(row['kind'],'risk');self.assertAlmostEqual(row['stop_price'],12*.93)
            q=quote(11.1,moment()+timedelta(seconds=20));q['high']=11.2
            b=actions.process({'sh512980':q},moment()+timedelta(seconds=20));row=b['boards']['expanded-trailing7']['rows'][0]
            self.assertEqual(row['kind'],'risk');self.assertAlmostEqual(row['stop_price'],12*.93)

    def test_restart_gap_does_not_reuse_a_stale_episode_timestamp(self):
        p=plan(position=dict(entry_date='2026-11-09'))
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ETF_DATA':tmp}),patch.object(actions,'build_plans',return_value={'expanded-none':{'sh512980':p}}):
            a=actions.process({'sh512980':quote(9.7)},moment());first=a['recent'][0]
            later=moment()+timedelta(minutes=4)
            b=actions.process({'sh512980':quote(9.7,later)},later)
            self.assertNotEqual(first['event_id'],b['recent'][-1]['event_id'])
            self.assertEqual(b['recent'][-1]['observed_at'],later.isoformat())

if __name__=='__main__':unittest.main()
