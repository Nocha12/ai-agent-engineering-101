"""Approved pure trial seam; no market IO."""
import unittest
from unittest.mock import patch

import buyer_firm_trial as trial


class TrialTests(unittest.TestCase):
    def test_off_preserves_baseline_and_does_not_assign(self):
        order = {"order": "o-test", "kind": "goods", "_buyer_started_at": 100}
        events = []
        self.assertIsNone(trial.turn(None, None,
            "Counteroffer: 120. This is my lowest price.",
            [("shop asked", 120), ("you offered", 90)], 100,
            order=order, shop="fixture", sku="G12345678", policy_ok=True,
            now=100, log=events.append))
        self.assertEqual(events, [])
        self.assertNotIn("_firm_trial", order)


if __name__ == "__main__":
    unittest.main()
