"""Public pricing/assignment seam; seller exposure seam is tested without sockets."""
import json
import asyncio
import contextlib
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest import mock

import sol_seller_margin as margin


class MarginTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'new-study'
        self.trial = margin.MarginStudy(self.root)
        self.unit = dict(unit='u-anonymous', sku='G-anonymous', grade='A', kind='goods',
                         cost=100000, list_price=500000)
        self.pricing = dict(price=dict(min_profit_share=0, goods_break=.04,
                            high_median_up=dict(min_list=300000, factor=.97),
                            high_price_trial=dict(share=0)), warranty=dict(max_break=.5))

    def enable(self, **updates):
        self.root.mkdir(exist_ok=True)
        cfg = dict(enabled=True, version=1, study_id='sol-margin-new-001', epoch=1000,
                   start=1000, end=44200, salt='fresh-sol-salt-001', source_sha256=margin.SOURCE_BINDING)
        cfg.update(updates)
        (self.root/'controller.json').write_text(json.dumps(cfg))
        return cfg

    def quote(self, task='task-anonymous', **kwargs):
        return self.trial.quote(task, self.unit, 300000, .04, (), self.pricing, now=1100, **kwargs)

    def test_default_off_preserves_price_and_creates_nothing(self):
        q, assignment = self.quote()
        self.assertEqual(q['open'], 291000)
        self.assertIsNone(assignment)
        self.assertFalse(self.root.exists())

    def both_arms(self):
        found = {}
        for n in range(100):
            self.unit['sku'] = 'G-anonymous-' + str(n)
            q, a = self.quote(task='task-' + str(n))
            found.setdefault(a['arm'], (q, a))
            if len(found) == 2:
                return found
        self.fail('both fixed hash arms must be reachable')

    def test_fresh_fixed_assignment_changes_only_b_and_survives_reopen(self):
        self.enable()
        arms = self.both_arms()
        self.assertEqual(arms['A'][0]['open'], 291000)
        self.assertEqual(arms['B'][0]['open'], 300000)
        self.assertEqual(arms['A'][0]['floor'], arms['B'][0]['floor'])
        self.assertEqual(arms['A'][0]['b'], arms['B'][0]['b'])
        persisted = margin.MarginStudy(self.root).observations()
        self.assertIn(arms['B'][1], persisted)
        self.unit['sku'] = arms['B'][1]['sku']
        self.unit['unit'] = 'u-another'
        _, repeated = self.quote(task='task-repeat')
        self.assertEqual(repeated['arm'], 'B')
        self.assertEqual(repeated['cluster'], arms['B'][1]['cluster'])

    def test_clipped_and_missing_median_are_retained_in_itt(self):
        self.enable()
        self.unit['cost'] = 400000
        q, a = self.quote()
        self.assertTrue(a['eligible'])
        self.assertEqual(q['open'], q['floor'])
        self.assertFalse(a['prepared_treatment'])
        _, missing = self.trial.quote('task-missing', self.unit, None, .04, (), self.pricing, now=1100)
        self.assertTrue(missing['eligible'])
        self.assertEqual(missing['reason'], 'no_median')

    def test_caps_scarce_refusal_and_food_keep_baseline_policy(self):
        self.enable()
        self.pricing['price']['ask_caps'] = {self.unit['sku']: {'A': 280000}}
        q, a = self.quote()
        self.assertEqual((q['open'], a['candidate']), (280000, 280000))
        self.unit['kind'] = 'food'
        _, a = self.quote(task='task-food')
        self.assertFalse(a['eligible'])
        self.assertFalse(a['prepared_treatment'])

    def test_warranty_refused_stays_in_eligible_itt_without_treatment(self):
        self.enable()
        q, a = self.trial.quote('refuse', self.unit, 300000, .8, (), self.pricing, now=1100)
        self.assertTrue(q['refuse'])
        self.assertTrue(a['eligible'])
        self.assertFalse(a['prepared_treatment'])
        self.assertEqual(a['reason'], 'warranty_refusal')

    def test_failed_persistence_never_returns_unrecorded_treatment(self):
        self.enable()
        with mock.patch('sqlite3.connect', side_effect=OSError('fixture disk failure')):
            with self.assertRaises(margin.StudyError):
                self.quote()

    def test_epoch_reuse_or_stale_task_is_rejected(self):
        self.enable()
        self.quote()
        self.enable(epoch=1050, start=1050, end=44250, study_id='sol-margin-new-002')
        with self.assertRaises(margin.StudyError):
            self.quote(task='task-new')

    def test_changed_snapshot_and_source_are_rejected(self):
        self.enable()
        self.quote()
        self.unit['cost'] = 200000
        with self.assertRaises(margin.StudyError):
            self.quote()
        self.enable(source_sha256='0'*64)
        with self.assertRaises(margin.StudyError):
            self.quote(task='task-source')

    def test_epoch_boundary_off_and_rollback_do_not_reclassify_old_tasks(self):
        self.enable()
        self.trial.quote('before', self.unit, 300000, .04, (), self.pricing, now=999)
        self.assertEqual(self.trial.observations(), [])
        self.quote()
        self.enable(enabled=False)
        _, a = self.quote(task='after-off')
        self.assertIsNone(a)
        self.assertEqual(len(self.trial.observations()), 1)

    def test_peers_are_exclusive_including_adopted_policy(self):
        self.enable()
        peer_root = Path(self.temp.name)/'peer'
        peer_root.mkdir()
        class Peer:
            root = peer_root
            def config(self):
                return dict(start=1000, end=1200)
        self.assertFalse(self.trial.pre_pick_ok((Peer(),), now=1100))
        (peer_root/'disabled').touch()
        self.assertTrue(self.trial.pre_pick_ok((Peer(),), now=1100))

    def test_rollback_after_prepare_blocks_public_consumption(self):
        self.enable()
        _, a = self.quote()
        self.enable(enabled=False)
        with self.assertRaises(margin.StudyError):
            self.trial.ready(a['task'], a, dict(ask=300000, reserve=100000), 1, now=1100)


class SellerExposureTest(unittest.TestCase):
    def test_real_seller_first_quote_records_final_treatment_after_common_opening(self):
        # All filesystem/controller boundaries use this fresh fixture. No HTTP/model stubs
        # can conceal a model attempt: direct_runner's process/network sentinel rejects it.
        with tempfile.TemporaryDirectory() as td, contextlib.ExitStack() as stack:
            root = Path(td)
            stack.enter_context(mock.patch.dict(os.environ, MARKET_DIR=td))
            import seller
            import shop_memory
            from shop_stock import StockBook
            trial = margin.MarginStudy(root/'new-study')
            stack.enter_context(mock.patch.object(seller, 'MARGIN_STUDY', trial))
            controllers = ('READER_TRIAL', 'CONCESSION_TRIAL', 'LATE_CONCESSION', 'OPENING_DEFAULT',
                           'DRAIN', 'OPENING_CUE', 'LATE_SPEED', 'JEV_TRANSPORT', 'SHELF_REPLY',
                           'EXACT_ACCEPT', 'EXACT_COUNTER', 'GRADE_PREFERENCE', 'FOOD_OPENING',
                           'AT_FLOOR_EXACT', 'DEEP_LOWBALL', 'QUOTE_HOLD')
            for name in controllers:
                stack.enter_context(mock.patch.object(getattr(seller, name), 'root', root/name))
            pricing = dict(price=dict(min_profit_share=0, goods_break=.04,
                           high_median_up=dict(min_list=300000, factor=.97),
                           high_price_trial=dict(share=0)), warranty=dict(max_break=.5))
            (root/'pricing.json').write_text(json.dumps(pricing))
            stack.enter_context(mock.patch.object(shop_memory, 'PRICING', str(root/'pricing.json')))
            stack.enter_context(mock.patch.object(shop_memory, 'MEMORY', str(root/'memory.json')))
            now = time.time()
            trial.root.mkdir()
            (trial.root/'controller.json').write_text(json.dumps(dict(
                enabled=True, version=1, study_id='sol-seller-integration-001',
                epoch=now-1, start=now-1, end=now+43199, salt='new-integration-salt',
                source_sha256=margin.SOURCE_BINDING)))
            # Choose an actually assigned B cluster through the public price seam.
            u = dict(unit='u-000000000001', sku='G00000000', grade='A', kind='goods', cost=100000,
                     list_price=500000, name='anonymous goods', category='goods', supplied_at=now, expires_at=None)
            for n in range(100):
                u['sku'] = f'G{n:08x}'
                _, a = trial.quote('probe-'+str(n), u, 300000, None, (), pricing)
                if a['arm'] == 'B':
                    break
            self.assertEqual(a['arm'], 'B')
            (root/'briefing.json').write_text(json.dumps(dict(skus={u['sku']: dict(price_ratio=.6)})))
            stock = StockBook(lambda: dict(round=3, units=[u]))
            shop = seller.Seller(None, 'anonymous-shop', stock=stock)
            task = SimpleNamespace(id=str(uuid.uuid4()))
            class Updater:
                quote = None
                async def requires_input(self, message): self.quote = message
                async def reject(self, message): raise AssertionError('unexpected rejection: '+str(message))
            up = Updater()
            asyncio.run(shop.open_from_stock(task, u['sku'], up, lambda text, price=None: (text, price)))
            self.assertEqual(up.quote[1], 291000)  # B 300000 then common .97 opening
            deal = shop.open[task.id]
            self.assertEqual(deal['open'], 300000)
            self.assertEqual(deal['sol_margin']['baseline'], 291000)
            self.assertEqual(deal['warranty'], 6)
            self.assertIn(deal['sol_margin'], trial.observations())
            ready = next(e for e in trial.events() if e['task'] == task.id and e['event'] == 'quote_ready')
            self.assertEqual(ready['baseline_final'], 282270)
            self.assertEqual(ready['final_price'], 291000)
            self.assertTrue(ready['actual_treatment'])
            self.assertEqual([e['price'] for e in trial.events()
                              if e['task'] == task.id and e['event'] == 'quoted'], [291000])


class ExactOutcomeTest(unittest.TestCase):
    def setUp(self):
        # Actual full ledger envelope and URL-valued shop; anonymized receipt/task/order.
        self.shop = 'https://anonymous.invalid/shop'
        self.ledger = [
            dict(seq=2129981, ts=1001.0, kind='result', data=dict(
                order='o-anonymous', buyer='anonymous-buyer', shop=self.shop, verified=True,
                receipt=dict(task='task-anonymous', price=36857, shop=self.shop, item='anonymous-item'))),
            dict(seq=2129982, ts=1002.0, kind='settle', data=dict(
                order='o-anonymous', buyer='anonymous-buyer', shop=self.shop,
                amount=36857, unit='u-anonymous', sku='G-anonymous', warranty_hours=6.0))]
        self.payments = [dict(order='o-anonymous', paid=True, ledger='2129982', price=36857)]

    def outcome(self):
        return margin.exact_outcome('task-anonymous', self.payments, self.ledger, self.shop)

    def test_exact_result_task_payment_seq_buyer_amount_join(self):
        self.assertEqual(self.outcome()['status'], 'confirmed')

    def test_settle_without_payment_is_unknown(self):
        self.payments.clear()
        self.assertEqual(self.outcome()['status'], 'unknown')

    def test_last_refusal_and_mismatched_amount_never_confirm(self):
        self.payments.append(dict(order='o-anonymous', paid=False))
        self.assertEqual(self.outcome()['status'], 'refused')
        self.payments.pop()
        self.ledger[1]['data']['amount'] += 1
        self.assertEqual(self.outcome()['status'], 'unknown')

    def test_buyer_task_or_shop_mismatch_and_duplicate_settle_are_unknown(self):
        self.ledger[1]['data']['buyer'] = 'other-anonymous-buyer'
        self.assertEqual(self.outcome()['status'], 'unknown')
        self.ledger[1]['data']['buyer'] = 'anonymous-buyer'
        self.ledger.append(self.ledger[1])
        self.assertEqual(self.outcome()['status'], 'unknown')

    def test_unverified_result_cannot_link_a_seller_task_to_payment(self):
        self.ledger[0]['data']['verified'] = False
        self.assertEqual(self.outcome()['status'], 'unknown')
