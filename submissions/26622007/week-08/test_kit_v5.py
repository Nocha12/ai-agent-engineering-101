"""Kit v5 (Round 4, Maroo testnet): the shop signs with the kit's wallet, and the buyer pays on chain only when the
market's order names a chain. Until then (Round 3, /api/chain 404) a wallet with no tOKRW deals as before."""
import os
import tempfile
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import test_round3
from test_round3 import kit, maroo, seller

CHAIN = {"credit_wei": 10 ** 15, "market_fee_bps": 100, "network_fee_credits": 420,
         "market_address": "0x" + "11" * 20}


class WalletDeal(test_round3.EndToEnd):
    """test_round3's seller + checkout + market stub, with maroo's chain calls stubbed out."""

    def setUp(self):
        super().setUp()
        self.paid = []
        self.balance = 0
        for name, fn in (("balance", lambda address: self.balance), ("pay", self.pay)):
            p = mock.patch.object(maroo, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def pay(self, wallet, to, wei, data=b""):
        self.paid.append((wallet.address, to, wei, data))
        return "0x" + f"{len(self.paid):064x}"

    def buy(self, chain=None):
        rec = self.deal("G1")["receipt"]
        o = self.order(["G1"])
        if chain:
            o["chain"] = chain
        why = []
        mr = kit.acp_pay(o, self.url, self.key.address, rec, maroo.Wallet.generate(),
                         {"cert": True, "min_hours_left": 48.1}, why)
        return rec, mr, why

    def test_no_chain_an_unfunded_wallet_deals_as_in_round_3(self):
        self.balance = None              # any balance read would fail the comparison
        rec, mr, why = self.buy()
        self.assertEqual((mr["ledger"], why, self.paid), (7, [], []))
        self.assertEqual(self.settled[0]["price"], rec["price"])

    def test_with_a_chain_a_short_wallet_does_not_pay_and_says_why(self):
        rec, mr, why = self.buy(CHAIN)
        self.assertIsNone(mr)
        self.assertEqual((self.paid, self.settled), ([], []))
        self.assertTrue(any("short of tOKRW" in w and maroo.FAUCET in w for w in why), why)

    def test_with_a_chain_a_funded_wallet_pays_the_shop_and_the_market(self):
        self.balance = 10 ** 24
        rec, mr, why = self.buy(CHAIN)
        self.assertEqual(mr["ledger"], 7)
        fee = maroo.fees(rec["price"], 100, 420)
        self.assertEqual([(to, wei) for _, to, wei, _ in self.paid],
                         [(self.key.address, (rec["price"] - fee["shop"]) * 10 ** 15),
                          (CHAIN["market_address"], fee["market"] * 10 ** 15)])


for _name in dir(test_round3.EndToEnd):      # run only the tests above here; test_round3 runs its own
    if _name.startswith("test_") and _name not in vars(WalletDeal):
        setattr(WalletDeal, _name, None)


class ShopKey(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, self.cwd)
        for var in ("SHOP_WALLET_KEY", "SHOP_KEY_PEM"):
            p = mock.patch.dict(os.environ, {var: ""})
            p.start()
            self.addCleanup(p.stop)
        pem = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                        serialization.NoEncryption())
        with open("shop_key.pem", "wb") as f:
            f.write(pem)

    def test_before_the_v5_kit_made_a_wallet_the_seller_signs_with_the_v4_key(self):
        key = seller.load_shop_key()
        self.assertFalse(hasattr(key, "address"))
        self.assertIsInstance(key, Ed25519PrivateKey)

    def test_once_the_wallet_exists_the_seller_signs_receipts_the_kit_checks(self):
        wallet = maroo.Wallet.load("shop_wallet.key")       # what the v5 kit creates on join
        key = seller.load_shop_key()
        self.assertEqual(key.address, wallet.address)
        rec = seller.sign_receipt({"shop": "s", "item": "G1", "price": 100, "task": "t"}, key)
        self.assertTrue(kit.verify_seller(wallet.address, rec))
        self.assertFalse(kit.verify_seller(maroo.Wallet.generate().address, rec))

    def test_the_checkout_jwt_from_a_wallet_is_eip191_and_verifies(self):
        wallet = maroo.Wallet.load("shop_wallet.key")
        tok = test_round3.acp_shop.jwt_sign(wallet, {"item": "G1", "price": 100})
        self.assertEqual(kit.jwt_payload(tok, wallet.address), {"item": "G1", "price": 100})
        rec = seller.sign_receipt({"shop": "s", "item": "G1", "price": 100, "task": "t"}, wallet)
        self.assertTrue(test_round3.acp_shop.signed_by(wallet, rec))


if __name__ == "__main__":
    unittest.main()
