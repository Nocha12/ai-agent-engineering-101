import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import buyer_food_floor30 as trial


class FoodFloor30(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = self.tmp.name
        self.cfg = {'enabled': True, 'study': 'food-floor-test', 'salt': 'test-salt',
                    'start': 1000, 'end': 2800}
        self.write_cfg()
        self.order = {'order': 'o-floor-2', 'kind': 'food', 'acceptable': ['Gef4d3036']}
        self.quote = {'sku': 'Gef4d3036', 'ask': 19652, 'ask_src': 'text',
                      'text': 'Food (Gef4d3036): 38 h shelf life left. Asking 19,652 credits.',
                      'task': {'id': '38fa492d-99df-4d5f-91f9-07654f0f812e'}}
        self.history = [('shop asked', 19652), ('you offered', 14000)]
        self.original = {'shop_price': 19652, 'action': 'offer', 'price': 19652}

    def write_cfg(self):
        self.path = Path(self.md) / trial.SWITCH
        self.path.write_text(json.dumps(self.cfg))
        os.utime(self.path, (999, 999))

    def decide(self, decision=None, history=None, **changes):
        kw = dict(md=self.md, order=self.order, shop='buzllightyear', sku='Gef4d3036',
                  quote=self.quote, list_price=28900, now=1001, env={})
        kw.update(changes)
        return trial.turn(self.original if decision is None else decision,
                          self.history if history is None else history, 20230, (.9,), **kw)

    def test_actual_apple_counter_gets_one_floor_bid(self):
        result, note = self.decide()
        self.assertEqual(result, {'shop_price': 19652, 'action': 'offer', 'price': 19363})
        self.assertEqual(note['arm'], 'B')
        self.assertTrue(note['applied'])
        self.assertEqual(note['baseline'], 19652)
        self.assertEqual(note['candidate'], 19363)
        saved = json.loads((Path(self.md) / trial.JOURNAL).read_text())
        self.assertEqual(saved['orders']['o-floor-2']['baseline'], 19652)

    def test_prior_treatment_requires_its_durable_assignment(self):
        _, note = self.decide()
        self.order['_food_floor30'] = note
        self.order['_food_ladder'] = note
        (Path(self.md) / trial.JOURNAL).unlink()
        history = self.history + [('shop asked', 19652), ('you offered', 19363)]
        result, returned = self.decide(history=history)
        self.assertEqual(result, self.original)
        self.assertIsNone(returned)

    def test_a_keeps_exact_baseline(self):
        self.order['order'] = 'o-floor-a'
        result, note = self.decide()
        self.assertIs(result, self.original)
        self.assertEqual(note['arm'], 'A')
        self.assertFalse(note['applied'])

    def test_b_walks_after_its_floor_bid(self):
        _, note = self.decide()
        self.order['_food_floor30'] = note
        self.order['_food_ladder'] = note
        history = self.history + [('shop asked', 19652), ('you offered', 19363)]
        result, _ = self.decide(history=history)
        self.assertEqual(result['action'], 'walk_away')
        self.assertEqual(result['price'], 0)
        lower, _ = self.decide({'shop_price': 19000, 'action': 'offer', 'price': 19000}, history)
        self.assertEqual(lower['price'], 19000)

    def test_expiry_and_off_restore_original_even_after_b(self):
        _, note = self.decide()
        self.order['_food_floor30'] = note
        self.order['_food_ladder'] = note
        for now in (999, 2800, 2801):
            with self.subTest(now=now):
                result, returned = self.decide(now=now)
                self.assertIs(result, self.original)
                self.assertIsNone(returned)
        self.cfg['enabled'] = False
        self.write_cfg()
        self.assertEqual(self.decide(), (self.original, None))

    def test_first_price_and_unrelated_quotes_are_identical(self):
        first = {'shop_price': 19652, 'action': 'offer', 'price': 17686}
        self.assertEqual(self.decide(first, []), (first, None))
        for changes in ({'shop': 'choo-chu'}, {'probing': True},
                        {'quote': dict(self.quote, text='ambiguous Gef4d3036 G3434758a')},
                        {'order': dict(self.order, kind='goods')},
                        {'env': {'BUYER_FOOD_FLOOR30': 'off'}}):
            with self.subTest(changes=changes):
                self.assertEqual(self.decide(**changes), (self.original, None))

    def test_invalid_clocks_and_late_mtime_are_off(self):
        for end in (None, True, float('inf'), 2801, 1000):
            self.cfg['end'] = end
            self.write_cfg()
            self.assertEqual(self.decide(), (self.original, None))
        self.cfg['end'] = 2800
        self.write_cfg()
        os.utime(self.path, (1002, 1002))
        self.assertEqual(self.decide(), (self.original, None))

    def test_failed_durability_never_changes_a_decision(self):
        with mock.patch.object(trial.os, 'fsync', side_effect=OSError('fixture no persistence')):
            self.assertEqual(self.decide(), (self.original, None))

    def test_changed_task_or_sku_cannot_reuse_assignment(self):
        _, note = self.decide()
        self.order['_food_floor30'] = note
        self.order['_food_ladder'] = note
        changed = dict(self.quote, task={'id': 'different-task'})
        self.assertEqual(self.decide(quote=changed), (self.original, None))
        path = Path(self.md) / trial.JOURNAL
        raw = json.loads(path.read_text())
        raw['orders']['o-floor-2']['arm'] = 'A'
        path.write_text(json.dumps(raw))
        self.assertEqual(self.decide(), (self.original, None))

    def test_corrupt_prior_journal_is_baseline_not_exception(self):
        _, note = self.decide()
        self.order['_food_floor30'] = note
        self.order['_food_ladder'] = note
        (Path(self.md) / trial.JOURNAL).write_text('[]')
        self.assertEqual(self.decide(), (self.original, None))

    def test_buyer_decision_boundary_records_learning_exclusion(self):
        import market_buyer
        result = market_buyer.food_floor30_turn(self.original, self.history, 20230, (.9,),
            md=self.md, order=self.order, shop='buzllightyear', sku='Gef4d3036',
            quote=self.quote, list_price=28900, now=1001)
        self.assertEqual(result['price'], 19363)
        self.assertEqual(self.order['_food_ladder']['policy'], trial.POLICY)
        self.assertEqual(self.order['_food_floor30']['arm'], 'B')
        self.assertTrue(market_buyer.buyer_food_ladder.laddered({'food_ladder': self.order['_food_ladder']}))

    def test_b_policy_receipt_guard_uses_actual_shipped_sku(self):
        _, note = self.decide()
        self.order['_food_floor30'] = note
        catalog = {'Gef4d3036': {'list_price': 28900}, 'G3434758a': {'list_price': 20400}}
        rec = {'item': 'Gef4d3036', 'price': 19652}
        self.assertIsNotNone(trial.receipt_problem(self.md, self.order, rec, catalog, now=1001, env={}))
        self.assertIsNone(trial.receipt_problem(self.md, self.order, dict(rec, price=19363), catalog, now=1001, env={}))
        self.assertIsNotNone(trial.receipt_problem(self.md, self.order, {'item': 'G3434758a', 'price': 14000}, catalog, now=1001, env={}))
        self.assertIsNone(trial.receipt_problem(self.md, self.order, rec, catalog, now=2800, env={}))

    def test_prior_high_bid_walks_instead_of_bidding_backwards(self):
        history = [('shop asked', 19652), ('you offered', 19500)]
        result, note = self.decide(history=history)
        self.assertEqual(result['action'], 'walk_away')
        self.assertTrue(note['applied'])


if __name__ == '__main__':
    unittest.main()
