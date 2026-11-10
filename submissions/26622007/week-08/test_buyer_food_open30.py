"""Public pilot seam: a real food opening, immutable finite assignment, no payment API."""
import json
import os
import tempfile
import unittest

import buyer_food_open30 as pilot


class FoodOpen30Test(unittest.TestCase):
    def test_real_food_first_offer_is_one_percent_lower_only_in_recorded_B(self):
        # Actual cheapest-in-town G2108d9bc opening: 9812, paid first baseline8830.
        with tempfile.TemporaryDirectory() as md:
            cfg = {'enabled': True, 'study': 'food-open30-test', 'salt': 'food30-test',
                   'start': 2000000000, 'end': 2000001800}
            path = os.path.join(md, 'buyer_food_open30.json')
            with open(path, 'w') as f:
                json.dump(cfg, f)
            os.utime(path, (1999999990, 1999999990))
            order = {'order': 'o-food30-0', 'kind': 'food'}
            q = {'ask': 9812, 'ask_src': 'text', 'sku': 'G2108d9bc',
                 'text': '시티델리 풍미가득 볶음밥 5종 10봉 택 (G2108d9bc), grade C, about 171.8 h shelf life left. Market certificate included; warranty 0 h. Asking price: 9812. Send one price as a number, or accept.'}
            note = pilot.assign(md, order, 'cheapest-in-town', q, (.9,), True,
                                probing=False, code_priced=True, limit=13230, env={}, now=2000000001)
            self.assertEqual(note['arm'], 'B')
            self.assertEqual(note['steps'], [.9])
            baseline = {'shop_price': 9812, 'action': 'offer', 'price': 8830}
            out = pilot.apply(baseline, [], 13230, (.9,), note, md=md, env={}, now=2000000001)
            self.assertEqual(out['price'], 8732)
            self.assertEqual(baseline['price'], 8830)
            self.assertTrue(note['applied'])
            # After any actual offer, all counter and walk rules are exactly unchanged.
            counter = {'shop_price': 9300, 'action': 'offer', 'price': 9300}
            self.assertEqual(pilot.apply(counter, [('you offered',8732)], 13230, (.9,), note,
                                         md=md, env={}, now=2000000002), counter)
            self.assertEqual(pilot.apply(baseline, [], 13230, (.9,), note, md=md, env={}, now=2000001800), baseline)


class FoodOpen30Guards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = self.tmp.name
        self.cfg = {'enabled': True, 'study': 'food-open30-test', 'salt': 'food30-test',
                    'start': 2000000000, 'end': 2000001800}
        self.path = os.path.join(self.md, 'buyer_food_open30.json')
        self.quote = {'ask': 9812, 'ask_src': 'text', 'sku': 'G2108d9bc',
                      'text': '시티델리 볶음밥 (G2108d9bc), grade C. Asking price: 9812.'}
        self.order = {'order': 'o-food30-0', 'kind': 'food'}
        self.baseline = {'shop_price': 9812, 'action': 'offer', 'price': 8830}
        self.flag()

    def flag(self, **changes):
        with open(self.path, 'w') as f:
            json.dump(dict(self.cfg, **changes), f)
        os.utime(self.path, (1999999990, 1999999990))

    def assign(self, order=None, quote=None, shop='cheapest-in-town', **kwargs):
        args = dict(probing=False, code_priced=True, limit=13230, env={}, now=2000000001)
        args.update(kwargs)
        return pilot.assign(self.md, self.order if order is None else order, shop,
                            self.quote if quote is None else quote, (.9,), True, **args)

    def test_A_is_unmodified_and_B_price_cannot_enter_A_trade_learning(self):
        import buyer_food_ladder
        note = self.assign(order=dict(self.order, order='o-food30-2'))
        self.assertEqual(note['arm'], 'A')
        self.assertEqual(pilot.apply(self.baseline, [], 13230, (.9,), note,
                                    md=self.md, env={}, now=2000000001), self.baseline)
        a, b = {'order':'control', 'food_ladder':'A'}, {'order':'treatment','food_ladder':'B'}
        self.assertEqual(buyer_food_ladder.priced([a,b]), [a])
        self.assertTrue(buyer_food_ladder.laddered({'food_ladder':dict(note, arm='B')}))

    def test_only_selected_single_food_SKU_without_other_opening_policy(self):
        cases = [dict(order=dict(self.order, kind='goods')),
                 dict(quote=dict(self.quote, sku='G1c830e4a')),
                 dict(quote=dict(self.quote, text=self.quote['text']+' Also G1c830e4a')),
                 dict(shop='olstjr'), dict(probing=True), dict(code_priced=False),
                 dict(order=dict(self.order, _one_won_default=True)),
                 dict(order=dict(self.order, _food_quote_yield='B')),
                 dict(order=dict(self.order, _b3={'applied':False})),
                 dict(order=dict(self.order, _food_ladder={'arm':'A'})),
                 dict(order=dict(self.order, _food_shelf={'would_drop':['x']})),
                 dict(quote=dict(self.quote, ask=True)), dict(limit=8000)]
        for case in cases:
            with self.subTest(case=case):
                self.assertIsNone(self.assign(**case))
        self.assertFalse(os.path.exists(os.path.join(self.md, pilot.JOURNAL)))

    def test_missing_bad_or_late_flag_and_finite_boundaries_keep_baseline(self):
        for now in (1999999999,2000001800,2000001801,float('nan'),float('inf'),10**400):
            self.assertIsNone(self.assign(now=now))
        self.assertIsNotNone(self.assign(now=2000000000))
        self.assertIsNotNone(self.assign(now=2000001799))
        for changes in ({'end':2000001801},{'end':float('inf')},{'start':True},{'enabled':False}):
            self.flag(**changes)
            self.assertIsNone(self.assign())
        self.flag()
        os.utime(self.path,(2000000001,2000000001))
        self.assertIsNone(self.assign())
        os.unlink(self.path)
        self.assertIsNone(self.assign())
        with open(self.path,'w') as f:
            f.write('{broken')
        self.assertIsNone(self.assign())

    def test_journal_failure_is_fail_closed_and_existing_assignment_is_not_duplicated(self):
        path = os.path.join(self.md,pilot.JOURNAL)
        os.mkdir(path)
        self.assertIsNone(self.assign())
        os.rmdir(path)
        with open(path,'w') as f:
            f.write('{broken')
        self.assertIsNone(self.assign())
        os.unlink(path)
        first = self.assign()
        with open(path) as f:
            original = f.read()
        self.assertEqual(self.assign(),first)
        with open(path) as f:
            self.assertEqual(f.read(),original)
        self.assertIsNone(self.assign(quote=dict(self.quote,ask=9813)))
        self.assertEqual(len(json.loads(original)['orders']),1)

    def test_switch_off_after_assignment_blocks_first_treatment_and_limits_remain(self):
        note = self.assign()
        self.flag(enabled=False)
        self.assertEqual(pilot.apply(self.baseline, [], 13230, (.9,), note,
                                    md=self.md, env={}, now=2000000002), self.baseline)
        self.assertFalse(note['applied'])
        self.flag()
        self.assertEqual(pilot.apply(self.baseline, [], 8700, (.9,), note,
                                    md=self.md, env={}, now=2000000002), self.baseline)
        self.assertIsNone(self.assign(env={'BUYER_FOOD_OPEN30':'0'}))
        self.assertIsNone(self.assign(env={'BUYER_FOOD_LADDER':'0'}))


if __name__ == '__main__':
    unittest.main()
