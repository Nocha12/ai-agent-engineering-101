"""Instruction 68: extra goods counter plus settled-payment scheduling, default OFF."""
import unittest
import tempfile
import time
import json
import ast
import inspect
from types import SimpleNamespace
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

    def test_actual_buyer_talk_replays_both_arms_without_backend(self):
        import buyer
        import buyer_bundle_trial as trial
        import market_buyer as mb
        callback = next(n for n in ast.walk(ast.parse(inspect.getsource(mb.main)))
                        if isinstance(n, ast.FunctionDef) and n.name == 'price_ladder')
        now = int(time.time())
        def task(state, ask=None):
            msg = {'parts': [{'text': 'Counteroffer: ' + str(ask) + '.'}]}
            if ask is not None:
                msg['metadata'] = {'ask': ask}
            return {'id': 'fixture-task', 'status': {'state': state, 'message': msg}}
        with tempfile.TemporaryDirectory() as directory, patch.multiple(
                trial, ENABLED=True, START=now-1, END=now+1799, STUDY='bundle-talk-fixture'):
            for arm, expected in [('A', ['9000', '9500']), ('B', ['9000', '9200'])]:
                oid = next('o-' + f'{i:012x}' for i in range(100)
                           if trial.allocation('o-' + f'{i:012x}')[1] == arm)
                order = dict(order=oid, kind='goods', _buyer_started_at=now)
                order['_buyer_bundle'] = trial.reserve(directory, order, rule_engine=True)
                scope = {**vars(mb), 'discount_arm': None, 'probe_arm': None, 'goods_first_arm': None,
                         'limit': 9800, 'steps': (.9,), 'q': {'ask': 10000}, 'resume': True,
                         'order': order, 'args': SimpleNamespace(budget=12000), 'shop': {'name': 'fixture'},
                         'key': 'G1', 'r3': True, 'rule_engine': True}
                exec(compile(ast.Module(body=[callback], type_ignores=[]), '<actual bundle ladder>', 'exec'), scope)
                sent = []
                def send(url, text, *args):
                    sent.append(text)
                    return task('TASK_STATE_INPUT_REQUIRED', 9500) if len(sent) == 1 else task('TASK_STATE_CANCELED')
                out = dict(history=[], agreed=None, receipt=None, holds=0)
                with patch.object(buyer, 'send', side_effect=send), patch.object(
                        buyer, 'decide', side_effect=AssertionError('backend forbidden')):
                    buyer.talk('fixture', task('TASK_STATE_INPUT_REQUIRED', 10000), 'goods', 9800,
                               '', out, ladder=scope['price_ladder'])
                self.assertEqual(sent, expected)
                if arm == 'B':
                    self.assertEqual(trial.measurement(order, out)['counter_decisions'][0]['history_offer_attempt'], 9200)

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
            (Path(directory)/'payments.jsonl').write_text(''.join(json.dumps(dict(
                order=t['order'], shop=t['shop'], item='G1', price=70, paid=t['paid']))+'\n' for t in trades))
            (Path(directory)/'measure').mkdir()
            (Path(directory)/'measure/orders.jsonl').write_text(''.join(json.dumps(dict(
                order=t['order'], category='goods', shop=t['shop'], sku='G1'))+'\n' for t in trades))
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
        trades, settled, payments, orders = [], [], [], []
        for shop, paid in [('good', True), ('unpaid', False), ('unknown', None)]:
            for i in range(3):
                oid = shop + str(i)
                trades.append(dict(order=oid, shop=shop, item='G1', category='display-label',
                                   time=stamp, paid=paid, price=70, list_price=100))
                orders.append(dict(order=oid, category='goods', shop=shop, sku='G1'))
                settled.append(dict(order=oid, shop='fixture:' + shop, sku='G1', time=10000,
                                    price=70, warranty_hours=6, claim_honest=True))
                payments.append(dict(order=oid, shop='fixture:' + shop, item='G1', price=70, paid=paid))
        scores, audit = trial.payment_scores(trades, settled, 'goods', now=40000, payments=payments, orders=orders)
        # Three successes: pay posterior 4/5; quality (3*.30 + .50)/5 = .28.
        self.assertAlmostEqual(scores[('good', 'G1')], .512)
        self.assertLess(scores[('unpaid', 'G1')], scores[('good', 'G1')])
        self.assertNotIn(('unknown', 'G1'), scores)
        self.assertEqual(audit['unknown'], 3)
        # A paid receipt without one matching official settlement is unresolved.
        scores, audit = trial.payment_scores(trades, [], 'goods', now=40000, payments=payments, orders=orders)
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
