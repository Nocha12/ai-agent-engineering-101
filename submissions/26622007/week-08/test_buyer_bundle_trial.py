"""Instruction 68: extra goods counter plus settled-payment scheduling, default OFF."""
import unittest
import tempfile
import time
import json
from pathlib import Path
import socket
import subprocess
from unittest.mock import patch

import oneshot


class BuyerBundleTrial(unittest.TestCase):
    def setUp(self):
        import httpx
        blocked = AssertionError('fixture external boundary blocked')
        for target in (('socket.socket.connect', blocked), ('socket.socket.connect_ex', blocked),
                       ('socket.create_connection', blocked), ('subprocess.Popen', blocked),
                       ('subprocess.run', blocked), ('httpx.Client.send', blocked),
                       ('httpx.AsyncClient.send', blocked)):
            p = patch(target[0], side_effect=target[1])
            p.start()
            self.addCleanup(p.stop)

    def test_turn_revalidates_engine_and_keeps_unparsed_harness_baseline(self):
        import buyer_bundle_trial as trial
        with tempfile.TemporaryDirectory() as directory, patch.multiple(
                trial, ENABLED=True, START=1000, END=2800, STUDY='bundle-turn-fixture'):
            oid = next('o-' + f'{i:012x}' for i in range(100)
                       if trial.allocation('o-' + f'{i:012x}')[1] == 'B')
            order = dict(order=oid, kind='goods', _buyer_started_at=1001)
            order['_buyer_bundle'] = trial.reserve(directory, order, rule_engine=True, now=1002)
            history = [('shop asked', 10000), ('you offered', 9000)]
            baseline = oneshot.code_turn(9500, history, 9800)
            self.assertEqual(trial.turn(baseline, 9500, '', history, 9800, (.9,), order=order,
                                       rule_engine=False, now=1003), baseline)
            result = trial.turn(baseline, 9500, '', history, 9800, (.9,), order=order,
                                rule_engine=True, now=1003)
            self.assertEqual(result['price'], 9200)
            self.assertIsNone(trial.turn(None, None, 'Ambiguous price, ask the agent', history,
                                        9800, (.9,), order=order, rule_engine=True, now=1003))
            self.assertEqual(trial.turn(baseline, 9500, '', history, 9800, (.9,), order=order,
                                       rule_engine=True, now=2800), baseline)

    def test_actual_prefetch_dispatch_keeps_canonical_choice_and_exploration(self):
        import collections
        import buyer_bundle_trial as trial
        import market_buyer as mb
        now = int(time.time())
        with tempfile.TemporaryDirectory() as directory, patch.multiple(
                trial, ENABLED=True, START=now - 1, END=now + 1799, STUDY='bundle-dispatch-fixture'):
            oid = next('o-' + f'{i:012x}' for i in range(100)
                       if trial.allocation('o-' + f'{i:012x}')[1] == 'B')
            order = dict(order=oid, kind='goods', _buyer_started_at=now, _stock_phase='prefetch')
            order['_buyer_bundle'] = trial.reserve(directory, order, rule_engine=True)
            shops = [dict(name=n, url='fixture:' + n, reviews=[], usable=[dict(sku='G1', name='goods', list_price=100)])
                     for n in ['weak', 'good', 'fresh']]
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(now - 30000))
            trades = [dict(order=n+str(i), shop=n, item='G1', category='goods', time=stamp,
                           paid=(n == 'good'), price=70, list_price=100)
                      for n in ('weak', 'good') for i in range(3)]
            rows = [dict(order='good'+str(i), shop='good', sku='G1', time=now-30000,
                         price=70, warranty_hours=6, claim_honest=True) for i in range(3)]
            ledger = Path(directory)/'settled.jsonl'
            ledger.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            sent = []
            with patch.object(mb, 'recent_fits', return_value=collections.Counter(weak=5, good=4)), \
                    patch.object(mb, 'SETTLED', str(ledger)), patch.object(mb, 'quote', side_effect=lambda s,*a: sent.append(s['name'])):
                early, pool = mb.prefetch_pool(shops, {'trades': trades}, directory, 'goods', 3,
                                              kind='goods', order=order, budget=100)
                self.assertEqual([s['name'] for s in early], ['weak', 'fresh'])
                threads = mb.start_quotes(early, order, 100, lambda _: 0, {}, mb.quote_ctl(), rule_engine=True)
                for thread in threads.values():
                    thread.join(1)
            self.assertEqual(set(sent), {'good', 'fresh'})
            self.assertEqual(order['_bundle_selection_asked'], {'weak', 'fresh'})
            self.assertEqual([s['name'] for s in pool], ['weak', 'fresh', 'good'])

    def test_actual_main_assigns_before_empty_stock_without_external_calls(self):
        import buyer_bundle_trial as trial
        import market_buyer as mb
        import httpx
        now = int(time.time())
        with tempfile.TemporaryDirectory() as directory, patch.multiple(
                trial, ENABLED=True, START=now - 1, END=now + 1799, STUDY='bundle-main-fixture'):
            root = Path(directory)
            shops, order = root/'test-shops.json', root/'order.json'
            shops.write_text('[]')
            order.write_text(json.dumps(dict(order='o-0123456789ab', kind='goods', acceptable=['G1'])))
            argv = ['buyer', '--shops', str(shops), '--order', str(order), '--item', 'goods', '--budget', '100']
            blocked = AssertionError('fixture external boundary blocked')
            with patch('sys.argv', argv), patch.dict('os.environ', {'BUYER_CHOOSE_ENGINE': 'rule'}), \
                    patch.object(mb, 'warm_ledger'), patch.object(mb, 'ask', side_effect=blocked), \
                    patch.object(socket.socket, 'connect', side_effect=blocked), \
                    patch.object(httpx.Client, 'send', side_effect=blocked), \
                    patch.object(subprocess, 'Popen', side_effect=blocked):
                self.assertEqual(mb.main(), 0)
            self.assertEqual(len(list((root/trial.ROOT).glob('*/assignment.json'))), 1)

    def test_scores_require_matching_official_payment_and_settlement(self):
        import buyer_bundle_trial as trial
        stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(10000))
        trades, settled = [], []
        for shop, paid in [('good', True), ('unpaid', False), ('unknown', None)]:
            for i in range(3):
                oid = shop + str(i)
                trades.append(dict(order=oid, shop=shop, item='G1', category='goods',
                                   time=stamp, paid=paid, price=70, list_price=100))
                settled.append(dict(order=oid, shop=shop, sku='G1', time=10000,
                                    price=70, warranty_hours=6, claim_honest=True))
        scores, audit = trial.payment_scores(trades, settled, 'goods', now=40000)
        # Three successes: pay posterior 4/5; quality (3*.30 + .50)/5 = .28.
        self.assertAlmostEqual(scores[('good', 'G1')], .512)
        self.assertLess(scores[('unpaid', 'G1')], scores[('good', 'G1')])
        self.assertNotIn(('unknown', 'G1'), scores)
        self.assertEqual(audit['unknown'], 3)
        # A paid receipt without one matching official settlement is unresolved.
        scores, audit = trial.payment_scores(trades, [], 'goods', now=40000)
        self.assertNotIn(('good', 'G1'), scores)
        self.assertEqual(audit['unknown'], 6)

    def test_payment_schedule_preserves_sku_rival_and_exploration_partitions(self):
        import buyer_bundle_trial as trial
        shops = [{'name': n} for n in ['weak', 'fresh', 'good', 'other', 'rival']]
        fits = dict(weak=4, fresh=0, good=3, other=9, rival=5)
        skus = dict(weak='G1', fresh='G1', good='G1', other='G2', rival='G1')
        scores = dict(weak=.1, good=.8, other=1., rival=1.)
        reordered = trial.schedule(shops, fits, {'rival'}, skus, scores)
        self.assertEqual([s['name'] for s in reordered], ['good', 'fresh', 'weak', 'other', 'rival'])
        self.assertEqual([s['name'] for s in shops], ['weak', 'fresh', 'good', 'other', 'rival'])

    def test_assignment_survives_retry_and_refuses_agent_or_expired_context(self):
        import buyer_bundle_trial as trial
        order = {'order': 'o-0123456789ab', 'kind': 'goods', '_buyer_started_at': 1001}
        with tempfile.TemporaryDirectory() as directory, patch.multiple(
                trial, ENABLED=True, START=1000, END=2800, STUDY='bundle-fixture'):
            self.assertIsNone(trial.reserve(directory, order, rule_engine=False, now=1002))
            first = trial.reserve(directory, order, rule_engine=True, now=1002)
            retry = trial.reserve(directory, order, rule_engine=True, now=1003)
            self.assertEqual(first, retry)
            self.assertTrue(trial.valid(first, rule_engine=True, now=1003))
            self.assertFalse(trial.valid(first, rule_engine=False, now=1003))
            self.assertFalse(trial.valid(first, rule_engine=True, now=2800))

    def test_goods_extra_counter_is_bounded_by_first_ask_and_original_limit(self):
        import buyer_bundle_trial as trial
        history = [('shop asked', 10000), ('you offered', 9000)]
        baseline = oneshot.code_turn(9500, history, 9800)
        self.assertEqual(baseline['price'], 9500)
        result = trial.counter(baseline, 9500, history, 9800, (.9,))
        self.assertEqual(result, {'shop_price': 9500, 'action': 'offer', 'price': 9200})
        history += [('shop asked', 9500), ('you offered', 9200)]
        self.assertEqual(trial.counter(oneshot.code_turn(9400, history, 9800), 9400,
                                       history, 9800, (.9,))['action'], 'walk_away')
        self.assertEqual(trial.counter(oneshot.code_turn(9100, history, 9800), 9100,
                                       history, 9800, (.9,))['price'], 9100)
        self.assertEqual(trial.counter(None, None, history, 9800, (.9,)), None)


if __name__ == '__main__':
    unittest.main()
