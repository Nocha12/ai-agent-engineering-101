"""Instruction 68: extra goods counter plus settled-payment scheduling, default OFF."""
import unittest
import tempfile
from unittest.mock import patch

import oneshot


class BuyerBundleTrial(unittest.TestCase):
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
