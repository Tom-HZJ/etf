import copy
import hashlib
import os
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch
from etfmon import actions,paper,store
from tests.test_actions import plan,quote,samples,moment


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'ETF_DATA':self.tmp.name});self.env.start()
        self.plans=patch.object(actions,'build_plans',side_effect=lambda *a:{'expanded-none':{'sh512980':copy.deepcopy(plan())}})
        self.plans.start()

    def tearDown(self):
        self.plans.stop();self.env.stop();self.tmp.cleanup()

    def seed_samples(self):
        store.write_json(actions.path('state.json'),dict(date='2026-11-10',samples={'sh512980':samples()},active={},recent=[]))

    def test_actual_buy_is_durable_once_and_no_same_day_sell(self):
        self.seed_samples()
        first=actions.process({'sh512980':quote()},moment(),paper_trading=True)
        trade=first['paper_ledger']['trades'][0]
        self.assertEqual(trade['entry_time'],moment().isoformat())
        self.assertEqual(trade['entry_price'],10.15)
        self.assertEqual(trade['buy_event_id'],first['buy_points'][0]['event_id'])
        later=moment()+timedelta(seconds=20)
        again=actions.process({'sh512980':quote(9.7,later)},later,paper_trading=True)
        self.assertEqual(again['paper_ledger']['open_count'],1)
        self.assertEqual(again['paper_ledger']['trades'],first['paper_ledger']['trades'])
        self.assertEqual(again['exit_points'],[])
        self.assertEqual(again['boards']['expanded-none']['rows'][0]['label'],'当日模拟买入')

    def test_next_day_stop_sells_at_observed_price_and_survives_condition_clear(self):
        self.seed_samples()
        actions.process({'sh512980':quote()},moment(),paper_trading=True)
        tomorrow=moment('2026-11-11T09:36:10+08:00')
        stopped=actions.process({'sh512980':quote(9.7,tomorrow)},tomorrow,paper_trading=True)
        point=stopped['exit_points'][0];trade=stopped['paper_ledger']['trades'][0]
        self.assertEqual(trade['exit_price'],9.7)  # no guaranteed fill at9.8 stop
        self.assertEqual(trade['exit_event_id'],point['event_id'])
        self.assertEqual(point['reference_price'],9.7)
        self.assertEqual(point['simulated_entry_date'],'2026-11-10')
        self.assertEqual(point['confirmation'],'close_required')
        self.assertEqual(stopped['paper_ledger']['open_count'],0)
        later=tomorrow+timedelta(seconds=20)
        clear=actions.process({'sh512980':quote(10.2,later)},later,paper_trading=True)
        self.assertEqual(clear['paper_ledger']['trades'],stopped['paper_ledger']['trades'])
        api=actions.build_payload('expanded','none',later)
        self.assertEqual(api['exit_points'][0]['reference_price'],9.7)
        self.assertFalse(api['exit_points'][0]['active'])
        self.assertEqual(api['position_basis'],'simulated_ledger')
        before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in actions.path().rglob('*.json')}
        actions.build_payload('expanded','none',later+timedelta(minutes=3))
        self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in actions.path().rglob('*.json')})

    def test_initial_adoption_uses_recorded_reference_not_current_quote(self):
        event={**actions.evaluate(plan(),quote(),samples(),moment()),'event_id':'earliest',
               'observed_at':moment().isoformat(),'live_recorded':True,'version':actions.VERSION}
        store.write_json(actions.path('events','2026-11-10','earliest.json'),event)
        later=moment()+timedelta(minutes=10)
        result=actions.process({'sh512980':quote(10.3,later)},later,paper_trading=True)
        self.assertEqual(result['paper_ledger']['trades'][0]['entry_price'],10.15)
        self.assertEqual(result['paper_ledger']['trades'][0]['entry_time'],event['observed_at'])
        self.assertEqual(result['paper_ledger']['open_count'],1)

    def test_replay_positions_do_not_create_ledger_holdings_or_false_sells(self):
        fake=plan(position=dict(entry_date='2026-11-09',entry_price=10.))
        with patch.object(actions,'build_plans',return_value={'expanded-none':{'sh512980':fake}}):
            result=actions.process({'sh512980':quote(9.7)},moment(),paper_trading=True)
        self.assertEqual(result['paper_ledger']['trades'],[])
        self.assertEqual(result['exit_points'],[])
        self.assertFalse(result['boards']['expanded-none']['rows'][0]['simulated_position'])

    def test_stale_quotes_cannot_close_positions(self):
        self.seed_samples();actions.process({'sh512980':quote()},moment(),paper_trading=True)
        tomorrow=moment('2026-11-11T09:36:10+08:00')
        result=actions.process({'sh512980':quote(9.7,moment())},tomorrow,paper_trading=True)
        self.assertEqual(result['paper_ledger']['open_count'],1)
        self.assertEqual(result['exit_points'],[])

    def test_sqlite_recovery_and_t1_matching_and_one_buy_per_day(self):
        e={**actions.evaluate(plan(),quote(),samples(),moment()),'event_id':'db-first',
           'observed_at':moment().isoformat(),'live_recorded':True,'version':actions.VERSION}
        with paper.connect() as db:
            self.assertTrue(paper.buy(db,e,moment()))
            self.assertFalse(paper.buy(db,e,moment()))
            self.assertFalse(paper.buy(db,{**e,'event_id':'rearm'},moment()))
            pos=paper.open_positions(db)['sh512980']
            x={**e,'event_id':'stop','kind':'risk','simulated_position':True,
               'paper_buy_event_id':pos['buy_event_id'],'reference_price':9.7}
            self.assertFalse(paper.sell(db,x,moment()))
        # SQLite is authoritative even if a crash prevented all JSON snapshots.
        later=moment()+timedelta(seconds=20)
        result=actions.process({'sh512980':quote(10.2,later)},later,paper_trading=True)
        self.assertEqual(result['buy_points'][0]['event_id'],'db-first')
        self.assertEqual(result['paper_ledger']['open_count'],1)
        with paper.connect() as db:
            future=moment('2026-11-11T09:36:10+08:00')
            x.update(observed_at=future.isoformat(),trigger_time=future.isoformat(),paper_buy_event_id='wrong')
            self.assertFalse(paper.sell(db,x,future))
            x['paper_buy_event_id']=pos['buy_event_id']
            self.assertTrue(paper.sell(db,x,future))
            self.assertFalse(paper.sell(db,x,future))
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_overlay_uses_entry_reference_and_causal_daily_exit(self):
        p=plan();p['ledger_daily']=[dict(date='2026-11-10',close=10.3,ma10=10.,atr14=.2),
                                      dict(date='2026-11-11',close=10.8,ma10=10.2,atr14=.2)]
        trade=dict(id=1,buy_event_id='actual',entry_time=moment().isoformat(),entry_price=10.15,initial_stop=9.8,target_price=10.675)
        before=paper.overlay({**p,'ledger_daily':p['ledger_daily'][:1]},trade)
        self.assertIsNone(before['pending'])
        after=paper.overlay(p,trade)
        self.assertEqual(after['pending']['date'],'2026-11-11')
        self.assertEqual(after['target'],10.675)
        self.assertEqual(after['position']['entry_price'],10.15)

    def test_prestart_fabricated_future_and_excluded_buys_are_not_adopted(self):
        event={**actions.evaluate(plan(),quote(),samples(),moment()),'event_id':'actual',
               'observed_at':moment().isoformat(),'live_recorded':True,'version':actions.VERSION}
        with paper.connect() as db:
            for bad in ({'live_recorded':False},{'observed_at':'2026-11-09T09:36:10+08:00','trigger_time':'2026-11-09T09:36:10+08:00'},
                        {'observed_at':'2026-11-11T09:36:10+08:00'}, {'model':'current'}):
                self.assertFalse(paper.buy(db,{**event,**bad},moment()))
        store.write_json(actions.path('events','2026-11-10','excluded.json'),{**event,'code':'sh516950'})
        result=actions.process({'sh512980':quote()},moment(),paper_trading=True)
        self.assertEqual(result['paper_ledger']['trades'],[])

    def test_open_ledger_holdings_survive_representative_change_for_exits_only(self):
        # A previously selected STAR50 holding must not vanish when a peer wins
        # a subsequent day's size comparison. No new duplicate entry is added.
        event={**actions.evaluate(plan(),quote(),samples(),moment()),'event_id':'prior-representative',
               'observed_at':moment().isoformat(),'live_recorded':True,'version':actions.VERSION,'code':'sh588080'}
        with paper.connect() as db:
            paper.buy(db,event,moment())
        tomorrow=moment('2026-11-11T09:36:10+08:00')
        p=plan();p['code']='sh588080'
        selection={'date':'2026-11-11','groups':{'star_chips':{'selected_code':'sh588000','status':'ready'}}}
        with patch.object(actions,'build_plans',return_value={'expanded-none':{'sh588080':p}}),patch.object(actions.representatives,'update',return_value=selection),patch.object(actions.representatives,'allowed',return_value=False):
            result=actions.process({'sh588080':quote(9.7,tomorrow)},tomorrow,paper_trading=True)
            payload=actions.build_payload('expanded','none',tomorrow)
        self.assertEqual(result['paper_ledger']['open_count'],0)
        self.assertEqual(len(payload['exit_points']),1)
        self.assertEqual(payload['exit_points'][0]['code'],'sh588080')


if __name__=='__main__':unittest.main()
