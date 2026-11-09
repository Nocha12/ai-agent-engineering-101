"""buyer_moss_first: moss-and-oak's whole "Also in stock" quote, first turn, priced by code in arm B (default off)."""
import hashlib
import json
import os
import tempfile
import unittest
import unittest.mock

import buyer_moss_first as mf
import market_buyer
import oneshot

G = ("안내를 요청해 주셔서 반갑습니다, 고객님—항상 신뢰할 수 있는 품질과 정직한 인증으로 모십니다. 사이즈오브체어 프로 사무용 의자 "
     "(G4a74caa4), grade A, 12-hour warranty included (full refund if it breaks), market certificate attached. Also in stock in "
     "this category: G00327e80, G04ee8423. Send your offer as a number, or reply 'accept' to buy now. Asking: 518149. ")
NOW = 1_794_300_000.0


def write(md, **kw):
    d = {"enabled": True, "salt": "moss-1110", "start": NOW - 60, "end": NOW - 60 + 9 * 3600, **kw}
    with open(os.path.join(md, mf.SWITCH), "w", encoding="utf-8") as f:
        json.dump(d, f)


def arm_of(order, salt="moss-1110"):
    return "AB"[hashlib.sha256(("buyer_moss_first:" + salt + ":" + order).encode()).digest()[0] % 2]


def order_in(arm):
    for i in range(200):
        o = f"o-{i:012x}"
        if arm_of(o) == arm:
            return o
    raise AssertionError


class Switch(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.mkdtemp()

    def test_missing_or_off_is_none(self):
        self.assertIsNone(mf.assign(self.md, {"order": "o-1"}, now=NOW))
        write(self.md, enabled=False)
        self.assertIsNone(mf.assign(self.md, {"order": "o-1"}, now=NOW))
        with open(os.path.join(self.md, mf.SWITCH), "w") as f:
            f.write("{not json")
        self.assertIsNone(mf.assign(self.md, {"order": "o-1"}, now=NOW))

    def test_window_must_be_fixed_9_to_12_hours(self):
        for start, end in ((NOW - 60, NOW + 3600),                  # 1 h: under the 9 h minimum
                           (NOW - 60, NOW - 60 + 13 * 3600),        # over the 12 h maximum
                           (NOW + 60, NOW + 60 + 9 * 3600),         # not started
                           (NOW - 10 * 3600, NOW - 60),             # over
                           (NOW - 60, float("inf")), (True, NOW + 9 * 3600), (NOW - 60, 10 ** 400)):
            write(self.md, start=start, end=end)
            self.assertIsNone(mf.assign(self.md, {"order": "o-1"}, now=NOW), (start, end))
        write(self.md, start=NOW - 60, end=NOW - 60 + 12 * 3600)
        self.assertIsNotNone(mf.assign(self.md, {"order": "o-1"}, now=NOW))

    def test_salt_checked(self):
        for salt in ("", "a b", "x" * 65, 5, None):
            write(self.md, salt=salt)
            self.assertIsNone(mf.assign(self.md, {"order": "o-1"}, now=NOW))

    def test_every_order_in_the_window_gets_an_arm_by_hash(self):
        write(self.md)
        for o in ("o-1", "o-2", "o-abc"):
            ctx = mf.assign(self.md, {"order": o}, now=NOW)
            self.assertEqual(ctx["arm"], arm_of(o))
            self.assertEqual(ctx["seen"], [])
            self.assertEqual(ctx["applied"], [])
        self.assertIsNone(mf.assign(self.md, {"order": ""}, now=NOW))
        self.assertIsNone(mf.assign(self.md, {"order": "o-1"}, now=float("nan")))

    def test_arms_split_about_half(self):
        n = sum(arm_of(f"o-{i:012x}") == "B" for i in range(4000))
        self.assertLess(abs(n - 2000), 150)

    def test_arm_independent_of_our_ask_hash(self):
        both = sum(arm_of(f"o-{i:012x}", "s") ==
                   "AB"[hashlib.sha256(("buyer_our_ask:s:" + f"o-{i:012x}").encode()).digest()[0] % 2]
                   for i in range(4000))
        self.assertLess(abs(both - 2000), 150)


class Turn(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.mkdtemp()
        write(self.md)

    def ctx(self, arm):
        return mf.assign(self.md, {"order": order_in(arm)}, now=NOW)

    def run_turn(self, ctx, d=None, meta=None, text=G, history=(), limit=600000, steps=(0.9,), quoted=518149,
                 moss=True, goods=True, now=NOW):
        return mf.turn(d, meta, text, list(history), limit, steps, quoted, moss, goods, ctx, self.md, "moss-and-oak", now=now)

    def test_arm_b_prices_the_first_turn_at_the_step_share(self):
        ctx = self.ctx("B")
        d = self.run_turn(ctx)
        self.assertEqual(d, {"shop_price": 518149, "action": "offer", "price": int(0.9 * 518149)})
        self.assertEqual(ctx["applied"], [{"shop": "moss-and-oak", "ask": 518149, "offer": int(0.9 * 518149)}])
        self.assertEqual(ctx["seen"], ctx["applied"])

    def test_never_over_my_limit(self):
        ctx = self.ctx("B")
        self.assertEqual(self.run_turn(ctx, limit=400000)["price"], 400000)

    def test_arm_a_keeps_the_harness_and_notes_what_b_would_do(self):
        ctx = self.ctx("A")
        self.assertIsNone(self.run_turn(ctx))
        self.assertEqual(ctx["applied"], [])
        self.assertEqual(ctx["seen"], [{"shop": "moss-and-oak", "ask": 518149, "offer": int(0.9 * 518149)}])

    def test_no_context_is_legacy(self):
        self.assertIsNone(self.run_turn(None))

    def test_only_the_first_turn_of_a_moss_quote_with_no_code_answer(self):
        for kw in ({"d": {"shop_price": 1, "action": "offer", "price": 1}},     # code already priced it: kept
                   {"meta": 518149},                                            # a metadata ask: oneshot's
                   {"history": [("shop asked", 518149), ("you offered", 466334)]},
                   {"moss": False},                                             # not moss's whole quote
                   {"quoted": None}, {"quoted": True}, {"quoted": 0},
                   {"steps": None}, {"steps": ()},
                   {"text": G.replace("12-hour warranty included (full refund if it breaks)", "no warranty")}):
            ctx = self.ctx("B")
            d = self.run_turn(ctx, **kw)
            self.assertEqual(d, kw.get("d"), kw)
            self.assertEqual(ctx["applied"], [], kw)

    def test_food_without_warranty_words_is_priced(self):
        ctx = self.ctx("B")
        self.assertEqual(self.run_turn(ctx, goods=False, text=G.replace("12-hour warranty included (full refund if it breaks)",
                                                                       "no warranty"))["action"], "offer")

    def test_switch_turned_off_mid_order_stops_arm_b(self):
        ctx = self.ctx("B")
        write(self.md, enabled=False)
        self.assertIsNone(self.run_turn(ctx))
        self.assertEqual(ctx["applied"], [])
        self.assertEqual(len(ctx["seen"]), 1)

    def test_switch_changed_or_window_over_stops_arm_b(self):
        ctx = self.ctx("B")
        write(self.md, salt="other")
        self.assertIsNone(self.run_turn(ctx))
        write(self.md)
        self.assertIsNone(self.run_turn(ctx, now=NOW + 10 * 3600))
        self.assertEqual(ctx["applied"], [])

    def test_measurement(self):
        self.assertIsNone(mf.measurement(None))
        ctx = self.ctx("B")
        self.run_turn(ctx)
        m = mf.measurement(ctx)
        self.assertEqual((m["arm"], m["salt"], m["start"], m["end"]), ("B", "moss-1110", ctx["start"], ctx["end"]))
        self.assertEqual(len(m["applied"]), 1)
        json.dumps(m)


class Wiring(unittest.TestCase):
    def test_moss_quote_predicate_is_market_buyers(self):
        self.assertTrue(market_buyer.also_in_stock_only(G.strip(), "G4a74caa4", 518149))

    def test_registered_with_the_meta_harness_switches(self):
        import meta_harness
        self.assertIn(".market/buyer_moss_first.json", meta_harness.SWITCHES)
        self.assertIn(".market/buyer_final_m1.json", meta_harness.SWITCHES)


class Wired(unittest.TestCase):
    """market_buyer.moss_first_and_final_m1, which main's price_ladder calls right after the oneshot turn."""

    def setUp(self):
        self.md = tempfile.mkdtemp()
        write(self.md, start=1, end=1 + 9 * 3600)

    def call(self, order, text=G, history=(), d=None, q=None, limit=600000):
        return market_buyer.moss_first_and_final_m1(d, None, text, list(history), limit, (.9,),
                                                    q or {"ask": 518149, "ask_src": "text"}, True, "G4a74caa4", order,
                                                    self.md, "moss-and-oak")

    def order(self, arm, **kw):
        o = {"kind": "goods", "order": order_in(arm), **kw}
        o["_moss_first"] = mf.assign(self.md, o, now=2)
        return o

    def test_off_is_the_turn_as_given(self):
        for d in (None, {"shop_price": 1, "action": "walk_away", "price": 0}):
            self.assertIs(self.call({"kind": "goods"}, d=d), d)

    def test_arm_b_prices_the_moss_quote_arm_a_notes_it(self):
        b, a = self.order("B"), self.order("A")
        with unittest.mock.patch.object(mf.time, "time", return_value=3):
            self.assertEqual(self.call(b), {"shop_price": 518149, "action": "offer", "price": int(.9 * 518149)})
            self.assertIsNone(self.call(a))
        self.assertEqual((len(b["_moss_first"]["applied"]), len(a["_moss_first"]["seen"]), a["_moss_first"]["applied"]),
                         (1, 1, []))

    def test_another_quote_or_a_rank_ask_is_left_alone(self):
        o = self.order("B")
        with unittest.mock.patch.object(mf.time, "time", return_value=3):
            self.assertIsNone(self.call(o, q={"ask": 518149, "ask_src": "text_rank"}))
            self.assertIsNone(self.call(o, q={"ask": 500000, "ask_src": "text"}))
        self.assertEqual(o["_moss_first"]["seen"], [])

    def test_final_m1_only_when_on(self):
        walk = oneshot.code_turn(9600, FinalM1.H, 10000, (.9,))
        self.assertIs(self.call({"kind": "food"}, "Final price: 9600. ", FinalM1.H, walk, limit=10000), walk)
        on = {"kind": "food", "_final_m1": []}
        self.assertEqual(self.call(on, "Final price: 9600. ", FinalM1.H, walk, limit=10000),
                         {"shop_price": 9600, "action": "offer", "price": 9600})
        self.assertEqual(on["_final_m1"], [{"shop": "moss-and-oak", "price": 9600}])

    def test_main_price_ladder_calls_it_only_when_on(self):
        import test_buyer_accept_band as tb
        history = [("shop asked", 11000), ("you offered", 9900)]
        with unittest.mock.patch.object(market_buyer, "moss_first_and_final_m1", side_effect=AssertionError("called")):
            ladder = tb.RealLadderTest("test_on_takes_the_final_ask_within_105_after_my_raise_off_walks").ladder(
                {"kind": "goods"}, 10000)
            self.assertEqual(ladder(10500, "", history)["price"], 10000)
        seen = []
        with unittest.mock.patch.object(market_buyer, "moss_first_and_final_m1",
                                        side_effect=lambda d, *a: seen.append(d) or d), \
                unittest.mock.patch.object(market_buyer, "md", self.md, create=True):
            ladder = tb.RealLadderTest("test_on_takes_the_final_ask_within_105_after_my_raise_off_walks").ladder(
                {"kind": "goods", "_final_m1": []}, 10000)
            self.assertEqual(ladder(10500, "", history)["price"], 10000)
        self.assertEqual(seen, [{"shop_price": 10500, "action": "offer", "price": 10000}])


class FinalM1(unittest.TestCase):
    """oneshot.code_turn walks once the steps and both last offers are spent, before it looks at an ask at or under my
    last offer; a text-only "Final price: N" at my last offer then ends the talk (GPT Pro mw9q5kxg §1.5, §4.1 M1)."""
    H = [("shop asked", 12000), ("you offered", 9000), ("shop asked", 9500), ("you offered", 9500),
         ("shop asked", 9800), ("you offered", 9600)]

    def test_code_turn_walks_there(self):
        self.assertEqual(oneshot.code_turn(9600, self.H, 10000, (0.9,))["action"], "walk_away")

    def test_taken_when_on(self):
        d = oneshot.code_turn(9600, self.H, 10000, (0.9,))
        self.assertEqual(oneshot.final_m1(d, None, "Final price: 9600. ", self.H, 10000, (0.9,)),
                         {"shop_price": 9600, "action": "offer", "price": 9600})
        self.assertEqual(oneshot.final_m1(d, None, "Two offers left. Final price: 9600.", self.H, 10000, (0.9,))["action"],
                         "offer")

    def test_only_that_case(self):
        d = oneshot.code_turn(9600, self.H, 10000, (0.9,))
        own = self.H[:-1] + [("you offered", 9800)]
        for args in ((d, 9600, "Final price: 9600. ", self.H, 10000),            # a metadata ask
                     (d, None, "Counteroffer: 9600", self.H, 10000),             # not Final price
                     (d, None, "Final price: 9600. ", self.H, 9500),             # over my limit now
                     (d, None, "Final price: 9600. ", self.H[:4], 10000),        # offers not spent: code_turn's own path
                     (oneshot.code_turn(9800, own, 10000, (0.9,)), None, "Final price: 9800. ", own, 10000),  # its own ask
                     ({"shop_price": 9600, "action": "offer", "price": 9600}, None, "Final price: 9600. ", self.H, 10000),
                     (None, None, "Final price: 9600. ", self.H, 10000)):
            self.assertEqual(oneshot.final_m1(*args, (0.9,)), args[0], args[1:3])
        lower = {"shop_price": 9500, "action": "walk_away", "price": 0}
        self.assertEqual(oneshot.final_m1(lower, None, "Final price: 9500. ", self.H, 10000, (0.9,)), lower)

    def test_switch(self):
        md = tempfile.mkdtemp()
        self.assertFalse(oneshot.final_m1_on(md))
        for body in ('{"enabled": false}', "{bad", '{"enabled": "true"}', "[]"):
            with open(os.path.join(md, oneshot.FINAL_M1_SWITCH), "w") as f:
                f.write(body)
            self.assertFalse(oneshot.final_m1_on(md), body)
        with open(os.path.join(md, oneshot.FINAL_M1_SWITCH), "w") as f:
            f.write('{"enabled": true}')
        self.assertTrue(oneshot.final_m1_on(md))
        self.assertFalse(oneshot.final_m1_on(md, env={"BUYER_FINAL_M1": "0"}))


if __name__ == "__main__":
    unittest.main()
