import copy
import hashlib
import os
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch
from etfmon import actions,api,config,paper,store,views
from types import SimpleNamespace
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
        self.assertEqual(trade['quantity'],1_000)
        self.assertEqual(trade['entry_amount'],10_150)
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
        self.assertEqual(trade['quantity'],1_000)
        self.assertEqual(trade['exit_amount'],9_700)
        self.assertEqual(trade['gross_pnl'],-450)
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
        review=paper.review('closed', '2026-11-10', '2026-11-10')
        self.assertEqual(review['gross_pnl'],-450)
        self.assertAlmostEqual(review['gross_return'],-450/10_150)
        self.assertEqual(review['trades'][0]['holding_days'],1)
        self.assertEqual(paper.review('open')['total'],0)
        self.assertEqual(paper.review(start='2026-11-11')['total'],0)

    def test_entry_sizing_is_strict_at_the_exact_threshold(self):
        for price, quantity in ((10.0,1_100), (0.8,12_600), (1.23,8_200), (10.001,1_000)):
            with self.subTest(price=price):
                self.assertEqual(paper.entry_quantity(price),quantity)

    def test_read_only_legacy_database_and_idempotent_quantity_migration(self):
        self.assertEqual(paper.review()['total'],0)
        self.assertFalse(paper.db_path().parent.exists())
        event={**actions.evaluate(plan(),quote(),samples(),moment()),'event_id':'legacy-entry',
               'observed_at':moment().isoformat(),'live_recorded':True,'version':actions.VERSION}
        with paper.connect() as db:
            paper.buy(db,event,moment())
        with paper.connect() as db:
            db.execute('ALTER TABLE trades DROP COLUMN quantity')
        before=hashlib.sha256(paper.db_path().read_bytes()).hexdigest()
        old=paper.review()['trades'][0]
        self.assertEqual(old['quantity'],1_000)
        self.assertEqual(old['entry_amount'],10_150)
        self.assertEqual(hashlib.sha256(paper.db_path().read_bytes()).hexdigest(),before)
        for _ in range(2):
            with paper.connect() as db:
                row=db.execute('SELECT * FROM trades').fetchone()
                self.assertEqual(row['quantity'],1_000)
                self.assertEqual(row['buy_event_id'],'legacy-entry')
                self.assertEqual(row['entry_time'],event['observed_at'])
                self.assertEqual(row['entry_price'],10.15)
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertEqual(paper.review(page=2)['trades'],[])

    def test_review_paginates_beyond_the_action_snapshot_limit(self):
        event={**actions.evaluate(plan(),quote(),samples(),moment()),
               'observed_at':moment().isoformat(),'live_recorded':True,'version':actions.VERSION}
        with paper.connect() as db:
            for index in range(205):
                self.assertTrue(paper.buy(db,{**event,'code':f'sh51{index:04d}','event_id':f'entry-{index}'},moment()))
            self.assertEqual(len(paper.snapshot(db)['trades']),200)
        last_page=paper.review('open', page=9)
        self.assertEqual(last_page['total'],205)
        self.assertEqual([trade['buy_event_id'] for trade in last_page['trades']],
                         ['entry-4','entry-3','entry-2','entry-1','entry-0'])

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

    def test_review_values_original_quantity_from_cached_quote_without_writing_fills(self):
        self.seed_samples()
        actions.process({'sh512980':quote()},moment(),paper_trading=True)
        now = moment() + timedelta(seconds=20)
        cache = SimpleNamespace(snapshot=lambda: (
            {'sh512980':quote(10.5, now)}, now.isoformat(), 0,
        ))
        app = views.App(cache=cache, started_at=now)
        before = hashlib.sha256(paper.db_path().read_bytes()).hexdigest()
        with patch.object(store, 'now', return_value=now):
            result = api.dispatch_get(app, '/api/research/paper-trades', {})
        trade = result['trades'][0]
        self.assertEqual(trade['quantity'], 1000)
        self.assertEqual(trade['floating_pnl'], 350)
        self.assertAlmostEqual(trade['floating_return'], 350 / 10_150)
        self.assertEqual(trade['mark_price'], 10.5)
        self.assertEqual(trade['mark_time'], now.isoformat())
        self.assertIsNone(trade['exit_event_id'])
        self.assertIsNone(trade['gross_pnl'])
        self.assertEqual(hashlib.sha256(paper.db_path().read_bytes()).hexdigest(), before)

        invalid = [
            quote(10.5, now - timedelta(minutes=6)), quote(10.5, now + timedelta(seconds=1)),
            {**quote(10.5, now), 'stale':True}, quote(float('nan'), now), {},
        ]
        with patch.object(store, 'now', return_value=now):
            for item in invalid:
                self.assertIsNone(paper.review(quotes={'sh512980':item})['trades'][0]['floating_pnl'])

    def test_steel_cannot_create_a_new_simulated_buy_from_an_old_discovery(self):
        event={**actions.evaluate(plan(),quote(),samples(),moment()),'code':'sh515210',
               'event_id':'steel','observed_at':moment().isoformat(),'live_recorded':True}
        with paper.connect() as db:
            self.assertFalse(paper.buy(db,event,moment()))
            self.assertEqual(paper.snapshot(db)['trades'],[])

    def test_removed_trade_cannot_be_recreated_by_its_signal_or_a_same_day_rearm(self):
        event={**actions.evaluate(plan(),quote(),samples(),moment()),'event_id':'remove-me',
               'observed_at':moment().isoformat(),'live_recorded':True}
        with paper.connect() as db:
            self.assertTrue(paper.buy(db,event,moment()))
            self.assertTrue(paper.remove_trade(db,'remove-me','user requested removal',moment()))
            self.assertFalse(paper.remove_trade(db,'remove-me','repeat',moment()))
            self.assertFalse(paper.buy(db,event,moment()))
            self.assertFalse(paper.buy(db,{**event,'event_id':'rearmed'},moment()))
            self.assertEqual(paper.snapshot(db)['open_count'],0)
            removed=db.execute('SELECT original_trade FROM removed_trades').fetchone()[0]
            self.assertIn('remove-me',removed)
            future=moment('2026-11-11T09:36:10+08:00')
            self.assertTrue(paper.buy(db,{**event,'event_id':'next-day',
                'observed_at':future.isoformat(),'trigger_time':future.isoformat()},future))

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
