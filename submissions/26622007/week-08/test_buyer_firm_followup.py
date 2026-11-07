"""Pure public seam; synthetic values, no market IO."""
import unittest
import buyer_firm_followup as firm

class FirmTests(unittest.TestCase):
    def test_verified_followup_uses_existing_code_policy(self):
        self.assertEqual(firm.turn(None, TEXT, [("shop asked",120),("you offered",90)], 100, arm="B", goods=True, policy_ok=True), {"shop_price":120,"action":"offer","price":100})

TEXT = 'Counteroffer: 120. This is my lowest price.'
if __name__ == "__main__":
    unittest.main()
