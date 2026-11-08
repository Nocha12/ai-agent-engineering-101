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


def offline_await(coroutine, before=None):
    """Deterministic scheduler boundary; run real business/SQLite functions inline.

    Concurrency/HTTP belong to test_round3 on the approved host runner. The strong
    offline guard forbids asyncio's socketpair, including its self-pipe.
    """
    async def inline(function, *args, **kwargs):
        if before:
            before(function, args)
        return function(*args, **kwargs)
    with mock.patch.object(asyncio, 'to_thread', inline):
        try:
            coroutine.send(None)
        except StopIteration as done:
            return done.value
        finally:
            coroutine.close()
    raise AssertionError('offline seam unexpectedly suspended')

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
        baseline = margin.baseline_snapshot(self.pricing, updates.pop('others', ()))
        cfg = dict(enabled=True, version=margin.VERSION, study_id='sol-margin-new-001', epoch=1000,
                   start=1000, end=44200, salt='fresh-sol-salt-001', source_sha256=margin.SOURCE_BINDING)
        cfg.update(baseline=baseline, baseline_sha256=margin.digest(baseline))
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

    def test_missing_or_unknown_grade_never_blocks_baseline_or_gets_b_price(self):
        self.enable()
        for kind in ('food', 'goods'):
            for grade in (None, '', 'unknown'):
                with self.subTest(kind=kind, grade=grade):
                    self.unit.update(kind=kind, grade=grade)
                    q, a = self.quote(task=f'grade-{kind}-{grade}')
                    self.assertEqual(q['open'], 315000 if kind == 'food' else 291000)
                    self.assertFalse(a['eligible'])
                    self.assertFalse(a['prepared_treatment'])
                    self.assertEqual(a['reason'], 'outside_target' if kind == 'food' else 'grade_unknown')

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

    def test_caps_and_food_keep_baseline_policy(self):
        self.pricing['price']['ask_caps'] = {self.unit['sku']: {'A': 280000}}
        self.enable()
        q, a = self.quote()
        self.assertEqual((q['open'], a['candidate']), (280000, 280000))
        self.unit['kind'] = 'food'
        _, a = self.quote(task='task-food')
        self.assertFalse(a['eligible'])
        self.assertFalse(a['prepared_treatment'])

    def assert_scarce_excluded(self, fixed_ask, expected_baseline):
        self.enable()
        arms = self.both_arms()
        self.unit['sku'] = arms['B'][1]['sku']
        self.pricing['price']['scarce_asks'] = {self.unit['sku']: fixed_ask}
        # A distinct excluded input is still baseline-only, never a new price treatment.
        q, assignment = self.quote(task='scarce-case')
        self.assertEqual(assignment['arm'], 'B')
        self.assertEqual(q['open'], expected_baseline)
        self.assertEqual(assignment['selected'], expected_baseline)
        self.assertEqual(assignment['candidate'], expected_baseline)
        self.assertFalse(assignment['eligible'])  # logged exclusion, outside target ITT
        self.assertFalse(assignment['prepared_treatment'])
        self.assertEqual(assignment['reason'], 'scarce_fixed_ask')

    def test_valid_scarce_below_median_is_excluded_even_without_scarce_why(self):
        # Baseline .97*300000 = 291000; a valid 280000 fixed ask is hidden in why.
        self.assert_scarce_excluded(280000, 291000)

    def test_valid_scarce_above_median_is_excluded_from_target_itt(self):
        self.assert_scarce_excluded(310000, 310000)

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

    def test_baseline_price_and_warranty_drift_pause_same_epoch(self):
        self.enable()
        self.quote()
        import copy
        original = copy.deepcopy(self.pricing)
        for field in ('cap', 'warranty', 'markdown'):
            self.pricing = copy.deepcopy(original)
            if field == 'cap': self.pricing['price']['ask_caps'] = {self.unit['sku']: {'A': 280000}}
            if field == 'warranty': self.pricing['warranty']['max_hours'] = 6
            if field == 'markdown': self.pricing['markdown'] = {'min_floor_share': .2}
            with self.subTest(field=field), self.assertRaises(margin.StudyError):
                self.quote(task='changed-'+field)

    def test_ready_detects_common_policy_drift_and_retains_unknown_event(self):
        from seller_late_concession import LateConcession
        peer = LateConcession(Path(self.temp.name)/'peers')
        peer.root.mkdir(parents=True)
        (peer.root/'adopted').touch()
        cfg = self.enable(others=(peer,))
        _, a = self.quote(others=(peer,))
        self.assertEqual(a['baseline_sha256'], cfg['baseline_sha256'])
        self.assertEqual(margin.MarginStudy(self.root).observations()[0], a)
        (peer.root/'adopted').unlink()
        with self.assertRaises(margin.StudyError):
            self.trial.ready(a['task'], a, dict(ask=291000, reserve=119118), 1,
                             others=(peer,), now=1100, pricing=self.pricing)
        self.assertEqual(self.trial.events()[-1]['economic_status'], 'unknown')

    def test_changed_snapshot_and_source_are_rejected(self):
        self.enable()
        self.quote()
        self.unit['cost'] = 200000
        with self.assertRaises(margin.StudyError):
            self.quote()
        self.enable(source_sha256='0'*64)
        with self.assertRaises(margin.StudyError):
            self.quote(task='task-source')

    def test_reopened_controller_never_reconsumes_a_prepared_task(self):
        self.enable()
        self.quote()
        self.trial = margin.MarginStudy(self.root)  # process-local tombstones are gone
        with self.assertRaises(margin.StudyError):
            self.quote()

    def test_public_reader_rejects_active_journal_or_wal_without_touching_it(self):
        self.enable()
        self.quote()
        for suffix in ('-journal', '-wal'):
            sidecar = self.root/('journal.sqlite3'+suffix)
            sidecar.write_bytes(b'anonymous in-progress fixture')
            with self.subTest(suffix=suffix), self.assertRaises(margin.StudyError):
                self.trial.observations()
            self.assertEqual(sidecar.read_bytes(), b'anonymous in-progress fixture')
            sidecar.unlink()

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

    def test_existing_adopted_common_policy_is_allowed_but_live_ab_is_not(self):
        from seller_late_concession import LateConcession
        peer = LateConcession(Path(self.temp.name)/'peers')
        peer.root.mkdir(parents=True)
        (peer.root/'adopted').touch()
        self.enable()
        self.assertTrue(self.trial.pre_pick_ok((peer,), now=1100))
        (peer.root/'adopted').unlink()
        (peer.root/'seller_concession_trial.json').write_text(json.dumps(dict(
            enabled=True, id='peer-live-ab-001', start=1000, end=1200, seed=1)))
        self.assertFalse(self.trial.pre_pick_ok((peer,), now=1100))

    def test_rollback_after_prepare_blocks_public_consumption(self):
        self.enable()
        _, a = self.quote()
        self.enable(enabled=False)
        with self.assertRaises(margin.StudyError):
            self.trial.ready(a['task'], a, dict(ask=300000, reserve=100000), 1, now=1100)


class SellerExposureTest(unittest.TestCase):
    def test_real_seller_first_quote_records_final_treatment_after_common_opening(self):
        self.exercise_case()

    def test_off_before_publication_cleans_open_and_owned_hold(self):
        self.exercise_case('off_before_ready')

    def test_peer_change_before_publication_aborts_prepared_quote(self):
        self.exercise_case('peer_before_ready')

    def test_baseline_change_before_publication_aborts_prepared_quote(self):
        self.exercise_case('baseline_before_ready')

    def test_all_four_adopted_policies_are_the_common_baseline(self):
        self.exercise_case('adopted_common')

    def test_unknown_grade_real_seller_keeps_baseline_quote(self):
        self.exercise_case('unknown_grade')

    def test_journal_io_before_publication_cleans_open_and_owned_hold(self):
        self.exercise_case('io_before_ready')

    def test_publication_transport_failure_retains_unknown_not_unpublished(self):
        self.exercise_case('transport_failure')

    def test_publication_cancel_retains_unknown_not_unpublished(self):
        self.exercise_case('transport_cancel')

    def test_published_off_and_sqlite_error_do_not_block_common_record_or_flush(self):
        self.exercise_case('published_journal_failure')

    def exercise_case(self, action=None):
        # All filesystem/controller boundaries use this fresh fixture. No HTTP/model stubs
        # can conceal a model attempt: direct_runner's process/network sentinel rejects it.
        with tempfile.TemporaryDirectory() as td, contextlib.ExitStack() as stack:
            root = Path(td)
            # zoneinfo must not discover host TZPATH through sysconfig/macOS metadata.
            stack.enter_context(mock.patch.dict(os.environ, MARKET_DIR=td, PYTHONTZPATH=''))
            import seller
            import shop_memory
            from shop_stock import StockBook
            trial = margin.MarginStudy(root/'new-study')
            # The approved historical seller source lacks this binding. Creating
            # the injected controller lets its real price path run and fail on
            # unchanged public-price assertions instead of a missing attribute.
            stack.enter_context(mock.patch.object(seller, 'MARGIN_STUDY', trial, create=True))
            controllers = ('READER_TRIAL', 'CONCESSION_TRIAL', 'LATE_CONCESSION', 'OPENING_DEFAULT',
                           'DRAIN', 'OPENING_CUE', 'LATE_SPEED', 'JEV_TRANSPORT', 'SHELF_REPLY',
                           'EXACT_ACCEPT', 'EXACT_COUNTER', 'GRADE_PREFERENCE', 'FOOD_OPENING',
                           'AT_FLOOR_EXACT', 'DEEP_LOWBALL', 'QUOTE_HOLD')
            for name in controllers:
                stack.enter_context(mock.patch.object(getattr(seller, name), 'root', root/name))
            if action == 'adopted_common':
                for name in ('LATE_CONCESSION', 'AT_FLOOR_EXACT', 'EXACT_COUNTER', 'EXACT_ACCEPT'):
                    peer_root = getattr(seller, name).root
                    peer_root.mkdir(parents=True)
                    (peer_root/'adopted').touch()
            pricing = dict(price=dict(min_profit_share=0, goods_break=.04,
                           high_median_up=dict(min_list=300000, factor=.97),
                           high_price_trial=dict(share=0)), warranty=dict(max_break=.5))
            (root/'pricing.json').write_text(json.dumps(pricing))
            stack.enter_context(mock.patch.object(shop_memory, 'PRICING', str(root/'pricing.json')))
            stack.enter_context(mock.patch.object(shop_memory, 'MEMORY', str(root/'memory.json')))
            pricing = shop_memory.load_pricing()
            now = time.time()
            trial.root.mkdir()
            (trial.root/'controller.json').write_text(json.dumps(dict(
                enabled=True, version=margin.VERSION, study_id='sol-seller-integration-001',
                epoch=now-1, start=now-1, end=now+43199, salt='new-integration-salt',
                source_sha256=margin.SOURCE_BINDING,
                baseline=margin.baseline_snapshot(pricing, [getattr(seller, name) for name in controllers if name != 'DRAIN']),
                baseline_sha256=margin.digest(margin.baseline_snapshot(pricing, [getattr(seller, name) for name in controllers if name != 'DRAIN'])))))
            # Choose an actually assigned B cluster through the public price seam.
            u = dict(unit='u-000000000001', sku='G00000000', grade='A', kind='goods', cost=100000,
                     list_price=500000, name='anonymous goods', category='goods', supplied_at=now, expires_at=None)
            for n in range(100):
                u['sku'] = f'G{n:08x}'
                _, a = trial.quote('probe-'+str(n), u, 300000, None, (), pricing,
                                   others=[getattr(seller, name) for name in controllers if name != 'DRAIN'])
                if a['arm'] == 'B':
                    break
            self.assertEqual(a['arm'], 'B')
            if action == 'unknown_grade':
                u['grade'] = None
            (root/'briefing.json').write_text(json.dumps(dict(skus={u['sku']: dict(price_ratio=.6)})))
            stock = StockBook(lambda: dict(round=3, units=[u]))
            shop = seller.Seller(None, 'anonymous-shop', stock=stock)
            task = SimpleNamespace(id=str(uuid.uuid4()))
            class Updater:
                quote = None
                rejected = None
                async def requires_input(self, message):
                    self.quote = message
                    if action == 'transport_failure': raise OSError('fixture ambiguous delivery')
                    if action == 'transport_cancel': raise asyncio.CancelledError()
                async def reject(self, message): self.rejected = message
            up = Updater()
            def before(function, args):
                if getattr(function, '__self__', None) is trial and function.__name__ == 'ready':
                    if action == 'off_before_ready':
                        (trial.root/'controller.json').write_text('{"enabled":false}')
                    if action == 'io_before_ready':
                        (trial.root/'journal.sqlite3').rename(trial.root/'saved-journal.sqlite3')
                        (trial.root/'journal.sqlite3').mkdir()  # real sqlite open error, no permissions changed
                    if action == 'peer_before_ready':
                        peer_root = seller.LATE_CONCESSION.root
                        peer_root.mkdir(parents=True, exist_ok=True)
                        (peer_root/'seller_concession_trial.json').write_text(json.dumps(dict(
                            enabled=True, id='new-live-peer', start=time.time()-1, end=time.time()+600, seed=1)))
                    if action == 'baseline_before_ready':
                        changed = json.loads((root/'pricing.json').read_text())
                        changed['price']['ask_caps'] = {u['sku']: {'A': 280000}}
                        (root/'pricing.json').write_text(json.dumps(changed))
            if action in ('off_before_ready', 'io_before_ready', 'peer_before_ready', 'baseline_before_ready'):
                with self.assertRaises(margin.StudyError):
                    offline_await(shop.open_from_stock(task, u['sku'], up, lambda text, price=None: (text, price)), before)
                self.assertIsNone(up.quote)
                self.assertFalse(task.id in shop.open, 'prepared task retained')
                self.assertNotEqual(stock.holder(u['unit']), task.id)
                # The same task must not consume its previous prepared B on retry.
                offline_await(shop.open_from_stock(task, u['sku'], up, lambda text, price=None: (text, price)))
                self.assertIsNone(up.quote)
                self.assertIsNotNone(up.rejected)
                return
            if action in ('transport_failure', 'transport_cancel'):
                with self.assertRaises(OSError if action == 'transport_failure' else asyncio.CancelledError):
                    offline_await(shop.open_from_stock(task, u['sku'], up, lambda text, price=None: (text, price)))
                self.assertEqual(shop.open[task.id]['sol_margin_publication'], 'unknown')
                self.assertEqual(stock.holder(u['unit']), task.id)
                offline_await(shop.open_from_stock(task, u['sku'], up, lambda text, price=None: (text, price)))
                self.assertIsNotNone(up.rejected)
                return
            offline_await(shop.open_from_stock(task, u['sku'], up, lambda text, price=None: (text, price)))
            if action == 'unknown_grade':
                self.assertEqual(up.quote[1], 282270)
                self.assertEqual(shop.open[task.id]['sol_margin']['reason'], 'grade_unknown')
                self.assertFalse(shop.open[task.id]['sol_margin']['eligible'])
                return
            self.assertEqual(up.quote[1], 291000)  # B 300000 then common .97 opening
            deal = shop.open[task.id]
            self.assertEqual(deal['open'], 300000)
            self.assertEqual(deal['sol_margin']['baseline'], 291000)
            # hard_limits.SELLER_GOODS_WARRANTY_HOURS is 72. Both arms use
            # the unchanged warranty_terms; a pricing max_hours=6 cannot lower it.
            baseline_deal = dict(kind='goods', id=u['sku'], reserve=deal['reserve'],
                                 **{'break': deal['break']})
            shop.warranty_terms(baseline_deal, u['sku'])
            self.assertEqual(baseline_deal['warranty'], 72)
            self.assertEqual(deal['warranty'], baseline_deal['warranty'])
            self.assertIn(deal['sol_margin'], trial.observations())
            ready = next(e for e in trial.events() if e['task'] == task.id and e['event'] == 'quote_ready')
            self.assertEqual(ready['baseline_final'], 282270)
            self.assertEqual(ready['final_price'], 291000)
            self.assertTrue(ready['actual_treatment'])
            self.assertEqual([e['price'] for e in trial.events()
                              if e['task'] == task.id and e['event'] == 'quoted'], [291000])
            if action == 'published_journal_failure':
                self.assertEqual(deal['sol_margin_publication'], 'published')
                (trial.root/'controller.json').write_text('{"enabled":false}')
                self.assertIs(shop.open[task.id], deal)  # published terms survive OFF
                (trial.root/'journal.sqlite3').rename(trial.root/'saved-journal.sqlite3')
                (trial.root/'journal.sqlite3').mkdir()
                shop._remember_now(task.id, deal, 'canceled', None, time.time())
                shop.free(task.id, deal)
                seller.flush_memory(strict=True)
                records = json.loads((root/'memory.json').read_text())['negotiations']
                self.assertTrue(any(r['task'] == task.id and r['result'] == 'canceled' for r in records))
                self.assertNotEqual(stock.holder(u['unit']), task.id)
                self.assertGreater(trial.observation_unknown, 0)

    def test_baseline_and_treatment_keep_the_72_hour_product_warranty(self):
        with tempfile.TemporaryDirectory() as td, contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, MARKET_DIR=td, PYTHONTZPATH=''))
            import seller
            import shop_memory
            stack.enter_context(mock.patch.object(shop_memory, 'PRICING', str(Path(td)/'pricing.json')))
            # Explicit low max_hours proves the hard-limit minimum, not an output snapshot.
            (Path(td)/'pricing.json').write_text(json.dumps(dict(warranty=dict(max_hours=6))))
            shop = seller.Seller(None, 'anonymous-shop')
            baseline = dict(kind='goods', id='G00000000', reserve=119118, open=291000)
            treatment = dict(baseline, open=300000)
            shop.warranty_terms(baseline, 'G00000000')
            shop.warranty_terms(treatment, 'G00000000')
            self.assertEqual(baseline['warranty'], 72)
            self.assertEqual(treatment['warranty'], 72)


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

    def test_malformed_rows_are_unknown_and_later_valid_rows_remain_accounted(self):
        original = self.ledger
        for bad in (None, [None, *original], [dict(seq=1, ts=1, kind='result', data=dict(
                shop=self.shop, receipt=dict(task='task-anonymous'), order=[], buyer={})) , *original],
                [*original, dict(seq=9, ts=1003, kind='broken', data=dict(
                    order='o-anonymous', buyer='anonymous-buyer', shop=self.shop))]):
            with self.subTest(case=type(bad).__name__):
                self.ledger = bad
                # The last broken row is well formed here; remove its seq to test the actual bad case.
                if isinstance(bad, list) and len(bad) == 3 and isinstance(bad[-1], dict) and bad[-1].get('kind') == 'broken':
                    bad[-1].pop('seq')
                outcome = self.outcome()
                self.assertEqual(outcome['status'], 'unknown')
                if bad is not None:
                    self.assertGreaterEqual(outcome['valid_rows'], 2)
        self.ledger = original

    def test_exact_result_task_payment_seq_buyer_amount_join(self):
        self.assertEqual(self.outcome()['status'], 'confirmed')

    def test_verified_alias_binding_preserves_receipt_alias_and_ledger_url(self):
        self.ledger[0]['data']['receipt']['shop'] = 'anonymous-shop-alias'
        binding = dict(ledger_url=self.shop, receipt_alias='anonymous-shop-alias',
                       provenance='parent-verified-shop-binding', evidence_sha256='a'*64)
        result = margin.exact_outcome('task-anonymous', self.payments, self.ledger, binding)
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(result['identity_evidence_sha256'], 'a'*64)
        binding['receipt_alias'] = 'wrong-alias'
        self.assertEqual(margin.exact_outcome('task-anonymous', self.payments, self.ledger, binding)['status'], 'unknown')
        self.assertEqual(self.outcome()['status'], 'unknown')
        self.payments.clear()
        binding['receipt_alias'] = 'anonymous-shop-alias'
        self.assertEqual(margin.exact_outcome('task-anonymous', self.payments, self.ledger, binding)['status'], 'unknown')

    def test_settle_without_payment_is_unknown(self):
        self.payments.clear()
        self.assertEqual(self.outcome()['status'], 'unknown')

    def test_official_settle_observation_is_separate_from_private_payment_confirmation(self):
        self.payments.clear()
        result = self.outcome()
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['official_settle']['status'], 'observed')
        self.assertEqual(result['official_settle']['seq'], 2129982)
        self.ledger[1]['data']['amount'] += 1
        self.assertEqual(self.outcome()['official_settle']['status'], 'unknown')
        self.ledger[1]['data']['amount'] -= 1
        self.ledger[0]['data']['receipt']['shop'] = 'wrong-alias'
        self.assertEqual(self.outcome()['official_settle']['status'], 'unknown')

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
