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

    def buy(self, chain=None, chain_log=None):
        rec = self.deal("G1")["receipt"]
        o = self.order(["G1"])
        if chain:
            o["chain"] = chain
        why = []
        mr = kit.acp_pay(o, self.url, self.key.address, rec, maroo.Wallet.generate(),
                         {"cert": True, "min_hours_left": 48.1}, why, chain_log=chain_log)
        return rec, mr, why

    def test_every_transfer_is_logged_and_an_order_is_never_paid_twice(self):
        self.balance = 10 ** 24
        log = os.path.join(self.tmp.name, "chain_sent.jsonl")
        rec, mr, why = self.buy(CHAIN, log)
        self.assertEqual(mr["ledger"], 7)
        self.assertEqual(kit.chain_sent(log, "o-1"), ["0x" + f"{1:064x}", "0x" + f"{2:064x}"])
        self.units.append(test_round3.unit("u-food2", "G1"))     # a restart brings the same order back
        rec, mr, why = self.buy(CHAIN, log)
        self.assertIsNone(mr)
        self.assertEqual(len(self.paid), 2)
        self.assertTrue(any("already sent" in w for w in why), why)

    def test_a_shop_transfer_that_fails_after_the_fee_names_the_fee_transaction(self):
        self.balance = 10 ** 24
        def pay(wallet, to, wei, data=b""):
            if to != CHAIN["market_address"]:
                raise RuntimeError("transfer failed on chain")
            return self.pay(wallet, to, wei, data)
        with mock.patch.object(maroo, "pay", pay):
            rec, mr, why = self.buy(CHAIN, os.path.join(self.tmp.name, "chain_sent.jsonl"))
        self.assertIsNone(mr)
        self.assertEqual([to for _, to, _, _ in self.paid], [CHAIN["market_address"]])   # the small fee went first
        self.assertTrue(any("already sent: 0x" in w for w in why), why)

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
                         [(CHAIN["market_address"], fee["market"] * 10 ** 15),
                          (self.key.address, (rec["price"] - fee["shop"]) * 10 ** 15)])


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

    def test_an_unreadable_wallet_signs_nothing_rather_than_falling_back_to_the_old_key(self):
        with open("shop_wallet.key", "w") as f:
            f.write("not hex\n")
        with mock.patch.object(seller, "log") as log:
            self.assertIsNone(seller.load_shop_key())
        self.assertIn("unreadable", log.call_args[0][0])
        self.assertNotIn("not hex", log.call_args[0][0])

    def test_the_v4_ed25519_checkout_jwt_still_verifies(self):
        key = seller.load_shop_key()
        tok = test_round3.acp_shop.jwt_sign(key, {"item": "G1"})
        self.assertIn('"alg":"EdDSA"', kit.unb64url(tok.split(".")[0]).decode())
        rec = seller.sign_receipt({"shop": "s", "item": "G1", "price": 100, "task": "t"}, key)
        self.assertTrue(test_round3.acp_shop.signed_by(key, rec))

    def test_the_checkout_jwt_from_a_wallet_is_eip191_and_verifies(self):
        wallet = maroo.Wallet.load("shop_wallet.key")
        tok = test_round3.acp_shop.jwt_sign(wallet, {"item": "G1", "price": 100})
        self.assertEqual(kit.jwt_payload(tok, wallet.address), {"item": "G1", "price": 100})
        rec = seller.sign_receipt({"shop": "s", "item": "G1", "price": 100, "task": "t"}, wallet)
        self.assertTrue(test_round3.acp_shop.signed_by(wallet, rec))


if __name__ == "__main__":
    unittest.main()
