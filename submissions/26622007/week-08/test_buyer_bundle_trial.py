"""Instruction 68: extra goods counter plus settled-payment scheduling, default OFF."""
import unittest

import oneshot


class BuyerBundleTrial(unittest.TestCase):
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
