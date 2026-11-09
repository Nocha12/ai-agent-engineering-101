"""No goods are bought without a warranty, with buyer_short_warranty on too (fixed rule: 무보증 비식품 구매 금지; decision
ax-0h-nonfood-block, 11/09 22:44 default). Before this, the switch priced a goods unit expected with no warranty at
p_max(0 h) and paid a 0 h (or unnamed) receipt under it: 9 such receipts were passed to the kit 11/08-11/09 12:09
(orders.jsonl short_warranty_buy h 0 ok true). The p_max(h) formula itself is not touched: the block stands before it.
block_zero_nonfood in the switch file (default true) is the one rollback flag: false is the 11/08 behaviour exactly."""
import json
import math
import os
import tempfile
import time
import unittest
from unittest import mock

import buyer_short_warranty as sw
import market_buyer
import test_buyer_short_warranty as base


def st(**kw):
    return {"S": 0.2955, "buffer": 34000000, "hist": {}, **kw}


class SwitchFlagTest(unittest.TestCase):
    def state(self, switch):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, sw.SWITCH), "w") as f:
                json.dump(switch, f)
            return sw.state(d, {"kind": "goods"}, env={})

    def test_on_unless_the_switch_says_false(self):
        self.assertTrue(self.state({"enabled": True})["block_zero"])                 # missing: on
        self.assertTrue(self.state({"enabled": True, "block_zero_nonfood": True})["block_zero"])
        self.assertTrue(self.state({"enabled": True, "block_zero_nonfood": "false"})["block_zero"])   # not JSON false
        self.assertTrue(self.state({"enabled": True, "block_zero_nonfood": 0})["block_zero"])
        self.assertFalse(self.state({"enabled": True, "block_zero_nonfood": False})["block_zero"])

    def test_block_zero_reads_fail_closed(self):
        self.assertTrue(sw.block_zero(st()))                     # a state built without the key: on
        self.assertTrue(sw.block_zero(st(block_zero=None)))
        self.assertFalse(sw.block_zero(st(block_zero=False)))
        self.assertFalse(sw.block_zero(None))                    # switch off: nothing to block, the 6 h rule holds

    def test_food_has_no_state(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, sw.SWITCH), "w") as f:
                json.dump({"enabled": True}, f)
            self.assertIsNone(sw.state(d, {"kind": "food"}, env={}))


class FormulaKeptTest(unittest.TestCase):
    def test_p_max_is_the_11_08_formula(self):
        # the owner's example (b .072, S .2955): 6 h 68.16%, 3 h 64.28%, 0 h 60.40%; a break outside counts -1
        for h, want in ((6, .6816), (3, .6428), (0, .6040)):
            self.assertAlmostEqual(sw.p_max(h, .072, .2955), want, places=4)
        self.assertAlmostEqual(sw.expected(.604, 0, .072), .2955, places=3)


class ReceiptGateTest(unittest.TestCase):
    def test_a_receipt_without_warranty_hours_is_refused_before_pricing_and_reserve(self):
        for w in (0, 0.0, None, -1, float("nan"), float("inf"), True, "3", "6 h"):
            with tempfile.TemporaryDirectory() as d:
                rec = {"price": 1000, "warranty_hours": w}
                with mock.patch.object(sw, "reserve", side_effect=AssertionError("reserved")), \
                        mock.patch.object(sw, "p_max", side_effect=AssertionError("priced")):
                    ok, note, why = sw.receipt_check(st(), d, rec, 10000, 0.072, "o-1")
                self.assertFalse(ok, w)
                self.assertEqual(note, {"h": 0, "ok": False, "blocked": "no_warranty"}, w)
                self.assertIn("no warranty", why)
                self.assertEqual(os.listdir(d), [], w)               # no exposure line, no lock file
        with tempfile.TemporaryDirectory() as d:                     # the key missing from the receipt
            ok, note, _ = sw.receipt_check(st(), d, {"price": 1000}, 10000, 0.072)
            self.assertEqual((ok, note["blocked"]), (False, "no_warranty"))

    def test_a_positive_short_warranty_is_priced_as_before(self):
        with tempfile.TemporaryDirectory() as d:
            ok, note, why = sw.receipt_check(st(), d, {"price": 6400, "warranty_hours": 3}, 10000, 0.072)
            self.assertTrue(ok, why)
            self.assertNotIn("blocked", note)
            ok, _, why = sw.receipt_check(st(), d, {"price": 6450, "warranty_hours": 3}, 10000, 0.072)
            self.assertFalse(ok)
            self.assertIn("over p_max", why)
            ok, _, why = sw.receipt_check(st(), d, {"price": 3000, "warranty_hours": 0.5}, 10000, 0.072)
            self.assertTrue(ok, why)

    def test_rollback_false_is_the_old_0h_check(self):
        with tempfile.TemporaryDirectory() as d:
            ok, note, why = sw.receipt_check(st(block_zero=False), d, {"price": 6039, "warranty_hours": 0}, 10000, 0.072)
            self.assertTrue(ok, why)
            self.assertEqual((note["h"], note["p_max"]), (0, 0.604))


class ZeroExpectedTest(unittest.TestCase):
    def test_what_blocks_before_pricing(self):
        s = st(hist={"z": 0})
        self.assertTrue(sw.zero_expected(s, "a", {"warranty_h": 0}))                      # the quote says none / 0 h
        self.assertTrue(sw.zero_expected(s, "z", {"warranty_h": None}))                   # 0 h history, quote silent
        self.assertTrue(sw.zero_expected(s, "z", None))                                   # 0 h history, no quote
        self.assertTrue(sw.zero_expected(s, "z", {"warranty_h": None, "warranty_said_ok": False}))

    def test_what_does_not(self):
        s = st(hist={"z": 0})
        self.assertIsNone(sw.zero_expected(s, "a", {"warranty_h": None}))                 # no history, quote silent
        self.assertIsNone(sw.zero_expected(s, "a", None))
        self.assertIsNone(sw.zero_expected(s, "a", {"warranty_h": 3}))                    # a short warranty: priced
        self.assertIsNone(sw.zero_expected(s, "z", {"warranty_h": 3}))                    # its own words over the history
        # BW1 (any arm, or off): a quote naming only 6 h or more is not blocked by the 0 h history
        for said in (None, {"arm": "A"}, {"arm": "B"}):
            t = dict(s, said=said)
            self.assertIsNone(sw.zero_expected(t, "z", {"warranty_h": None, "warranty_said_ok": True}), said)
        self.assertIsNone(sw.zero_expected(st(hist={"z": 0}, block_zero=False), "z", {"warranty_h": 0}))
        self.assertIsNone(sw.zero_expected(None, "z", {"warranty_h": 0}))

    def test_hours_for_is_untouched(self):
        s = st(hist={"z": 0})
        self.assertEqual(sw.hours_for(s, "z", {"warranty_h": 3}), 0)      # the least, as before (the cap stays p_max(0))
        self.assertEqual(sw.hours_for(s, "a", {"warranty_h": 3}), 3)
        self.assertIsNone(sw.hours_for(s, "a", {"warranty_h": None}))


class QuoteAndTextTest(unittest.TestCase):
    ON = {"kind": "goods", "_sw": st(hist={"z": 0})}

    def q(self, text, order, name="a"):
        q = {"ask": 5000, "text": text, "refused": market_buyer.quote_refused(text, order)}
        market_buyer.short_warranty_quote(q, text, order, name)
        return q

    def test_a_quote_without_warranty_is_refused(self):
        for text in ("Warranty: 0 hours. Price 9000", "sold without a warranty", "no warranty on this unit, 5000"):
            q = self.q(text, self.ON)
            self.assertEqual(q["warranty_h"], 0, text)
            self.assertIn("no warranty", q["refused"], text)

    def test_a_silent_quote_from_a_0h_shop_is_refused_others_kept(self):
        self.assertIn("no warranty", self.q("asking 5000", self.ON, "z")["refused"])
        self.assertIsNone(self.q("asking 5000", self.ON, "a")["refused"])
        self.assertIsNone(self.q("three-hour full-refund warranty, 5000", self.ON, "a")["refused"])
        self.assertIsNone(self.q("three-hour full-refund warranty, 5000", self.ON, "z")["refused"])

    def test_a_72h_quote_from_a_0h_shop_is_kept_in_every_arm(self):
        text = "Grade A, market certificate attached, 72 h warranty. Asking 5000."
        for said in (None, {"arm": "A"}, {"arm": "B"}):
            order = {"kind": "goods", "_sw": st(hist={"z": 0}, said=said)}
            q = self.q(text, order, "z")
            self.assertIsNone(q["refused"], said)
            self.assertTrue(q["warranty_said_ok"])

    def test_rollback_and_off_and_food_unchanged(self):
        rb = {"kind": "goods", "_sw": st(hist={"z": 0}, block_zero=False)}
        self.assertIsNone(self.q("Warranty: 0 hours. Price 9000", rb)["refused"])
        self.assertIsNone(self.q("asking 5000", rb, "z")["refused"])
        off = {"kind": "goods"}
        q = self.q("Warranty: 0 hours. Price 9000", off)
        self.assertNotIn("warranty_h", q)                                   # off: the old refusal, nothing added
        self.assertEqual(q["refused"], market_buyer.quote_refused("Warranty: 0 hours. Price 9000", off))
        food = {"kind": "food", "use_within_hours": 24}
        self.assertNotIn("warranty_h", self.q("no warranty", food))

    def test_talk_text_without_warranty_stops_the_talk(self):
        for text in ("Warranty: 0 hours. Price 9000", "sold without a warranty", "no warranty on this unit"):
            self.assertIsNotNone(market_buyer.goods_text_refused(text, self.ON), text)
            self.assertIsNone(market_buyer.goods_text_refused(text, {"kind": "goods", "_sw": st(block_zero=False)}), text)
        for text in ("three-hour full-refund warranty, 9000", "72 h warranty", "price 9000"):
            self.assertIsNone(market_buyer.goods_text_refused(text, self.ON), text)
        self.assertIsNone(market_buyer.goods_text_refused("no warranty", {"kind": "food"}))

    def test_quote_end_to_end(self):
        card = mock.Mock()
        card.json.return_value = {"supportedInterfaces": [{"protocolBinding": "JSONRPC", "protocolVersion": "1.0",
                                                             "url": "https://z.example/rpc"}]}

        def ask(name, text):
            out = {}
            with mock.patch.object(market_buyer, "serves_acp", lambda u: True), \
                    mock.patch.object(market_buyer.httpx, "get", lambda *a, **k: card), \
                    mock.patch.object(market_buyer, "buyer_send", lambda *a, **k: {
                        "id": "t1", "status": {"state": "TASK_STATE_INPUT_REQUIRED"}}), \
                    mock.patch.object(market_buyer, "buyer_message_text", lambda t: text), \
                    mock.patch.object(market_buyer, "buyer_metadata_ask", lambda t: 5000), \
                    mock.patch.object(market_buyer, "log"):
                market_buyer.quote({"name": name, "url": f"https://{name}.example", "_offer": {"sku": "G1", "list_price": 10000}},
                                   "cup", "G1", dict(self.ON), out)
            return out[name]
        self.assertIn("no warranty", ask("z", "asking 5000")["refused"])
        self.assertIn("no warranty", ask("a", "Warranty: 0 hours. asking 5000")["refused"])
        self.assertIsNone(ask("a", "asking 5000")["refused"])
        self.assertIsNone(ask("z", "72 h warranty, grade A. asking 5000")["refused"])
        self.assertFalse(market_buyer.resumable(ask("z", "asking 5000")))      # never the talk that goes on

    def test_quote_calls_the_helper_once(self):
        import inspect
        src = inspect.getsource(market_buyer.quote)
        self.assertEqual(src.count("short_warranty_quote(q, text, order, shop[\"name\"])"), 1)
        self.assertNotIn('q["warranty_h"] = ', src)


class _Main:
    CATALOG = base.MainTest.CATALOG
    ORDER = base.MainTest.ORDER
    CFG = {}
    run_main = base.MainTest.run_main


class MainZeroTest(unittest.TestCase):
    """market_buyer.main end to end, the switch on with block_zero_nonfood as given."""
    ON = {"enabled": True, "cash_buffer": 34000000}
    ROLLBACK = {"enabled": True, "cash_buffer": 34000000, "block_zero_nonfood": False}
    MEM = {"trades": [{"shop": "s", "item": "G1", "result": "no_warranty",
                       "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 60))}]}

    def main(self, switch, price, warranty, known_short=False):
        def setup(d):
            if known_short:
                with open(os.path.join(d, "m.json"), "w") as f:
                    json.dump(self.MEM, f)
        reserved = []
        real = sw.reserve
        with mock.patch.object(sw, "reserve", lambda *a, **k: reserved.append(a) or real(*a, **k)), \
                mock.patch.dict(market_buyer._SW_CTX):        # main() sets the run's context: put back after
            out, calls, o = _Main().run_main(switch, price, warranty, setup)
        return out, calls, o, reserved

    def test_a_0h_receipt_is_not_passed_to_the_kit(self):
        for w in (0, None, -2, "0"):
            out, calls, o, reserved = self.main(self.ON, 5800, w)
            self.assertIsNone(out["receipt"], w)
            self.assertEqual(out["accept"], {"warranty_hours": 6}, w)
            self.assertEqual(o["short_warranty_buy"], {"h": 0, "ok": False, "blocked": "no_warranty"}, w)
            self.assertFalse(o["passed_to_kit"], w)
            self.assertEqual(reserved, [], w)
            self.assertIn("no warranty", " ".join(o["warnings"]))

    def test_rollback_pays_a_0h_receipt_within_p_max_as_on_11_08(self):
        out, calls, o, reserved = self.main(self.ROLLBACK, 5800, 0)
        self.assertIsNotNone(out["receipt"])
        self.assertEqual(out["accept"], {"warranty_hours": 0, "grades": ["A", "B", "C"]})
        self.assertTrue(o["short_warranty_buy"]["ok"])
        self.assertEqual(len(reserved), 1)

    def test_a_3h_receipt_within_p_max_is_still_paid(self):
        out, calls, o, reserved = self.main(self.ON, 6000, 3)
        self.assertIsNotNone(out["receipt"])
        self.assertEqual(out["accept"]["warranty_hours"], 3)
        self.assertTrue(o["short_warranty_buy"]["ok"])
        self.assertEqual(len(reserved), 1)

    def test_a_6h_receipt_is_the_usual_deal(self):
        on = self.main(self.ON, 6900, 6)
        off = self.main(None, 6900, 6)
        self.assertEqual(on[0], off[0])
        self.assertEqual(on[1], off[1])

    def test_a_shop_known_0h_is_not_talked_to(self):
        out, calls, o, reserved = self.main(self.ON, 5800, 0, known_short=True)
        self.assertEqual(calls, [])                                # no talk, so no price is offered
        self.assertIsNone(out["receipt"])
        self.assertEqual(reserved, [])
        self.assertEqual(o["short_warranty"]["zero"], "no warranty expected: the shop's last goods receipts were short")
        rb = self.main(self.ROLLBACK, 5800, 0, known_short=True)  # rollback: talked to at p_max(0) as before
        self.assertEqual(len(rb[1]), 1)
        self.assertNotIn("zero", rb[2]["short_warranty"])


if __name__ == "__main__":
    unittest.main()
