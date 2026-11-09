import collections
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import buyer_food_ladder as fl     # noqa: E402
import buyer_goods_first_one as gfo  # noqa: E402
import buyer_goods_ladder as gl    # noqa: E402
import oneshot                     # noqa: E402

ON = {"enabled": True, "study": "goods-ladder-test", "salt": "goods-ladder-test"}
# A goods quote as ddolcom wrote it (orders.jsonl 11/09 13:4x: asks [552960], our offer 497664 = 0.9, taken)
ASK, LIST, LIMIT = 552960, 921600, 626772
TEXT = "G8ec51935 grade A, 72 h warranty, ask 552960"


def switch(md, value):
    with open(os.path.join(md, gl.SWITCH), "w") as f:
        f.write(value if isinstance(value, str) else json.dumps(value))


def order_in(a, salt=ON["salt"]):
    for i in range(400):
        o = f"o-gladder{i:04d}"
        if gl.arm({"salt": salt}, o) == a:
            return {"order": o, "kind": "goods"}
    raise AssertionError


def decide(md, order, steps=(0.9,), resume=True, probing=False, ask=ASK, env=None, code_priced=True):
    return gl.decide(md, order, {"ask": ask, "text": TEXT}, steps, resume, probing=probing, code_priced=code_priced,
                     env=env or {})


def talk(steps, shop):
    """Plays oneshot.code_turn against a shop rule until an offer is taken or someone walks: (price or None, turns)."""
    history, ask = [], ASK
    for n in range(1, 8):
        d = oneshot.code_turn(ask, history, LIMIT, steps)
        assert d["price"] <= max(LIMIT, 0) and d["price"] <= ask
        if d["action"] == "walk_away":
            return None, n
        history += [("shop asked", ask), ("you offered", d["price"])]
        nxt = shop(d["price"], ask)
        if nxt is None:
            return d["price"], n
        ask = nxt
    return None, n


class SwitchTest(unittest.TestCase):
    def test_off_missing_broken_or_env_keeps_steps_and_records_nothing(self):
        with tempfile.TemporaryDirectory() as md:
            o = order_in("B")
            self.assertEqual(decide(md, o), ((0.9,), None))           # no switch file
            switch(md, "{not json")
            self.assertEqual(decide(md, o), ((0.9,), None))
            switch(md, dict(ON, enabled=False))
            self.assertEqual(decide(md, o), ((0.9,), None))
            switch(md, dict(ON, enabled="true"))
            self.assertEqual(decide(md, o), ((0.9,), None))
            switch(md, dict(ON, salt=""))
            self.assertEqual(decide(md, o), ((0.9,), None))
            switch(md, ON)
            self.assertEqual(decide(md, o, env={"BUYER_GOODS_LADDER": "0"}), ((0.9,), None))
            self.assertEqual(decide(md, o)[0], (0.8, 0.9))

    def test_food_ladder_switch_does_not_turn_this_on(self):
        with tempfile.TemporaryDirectory() as md:
            with open(os.path.join(md, fl.SWITCH), "w") as f:
                json.dump({"enabled": True, "study": "food", "salt": "food"}, f)
            self.assertEqual(decide(md, order_in("B")), ((0.9,), None))


class ArmTest(unittest.TestCase):
    def test_arms_record_and_steps(self):
        with tempfile.TemporaryDirectory() as md:
            switch(md, ON)
            self.assertEqual(decide(md, order_in("A")), ((0.9,), {"study": ON["study"], "arm": "A", "steps": [0.9]}))
            self.assertEqual(decide(md, order_in("B")),
                             ((0.8, 0.9), {"study": ON["study"], "arm": "B", "steps": [0.8, 0.9]}))

    def test_hash_is_fixed_and_independent_of_other_studies(self):
        # fixed vector: the arm of an order id never moves between runs
        self.assertEqual([gl.arm({"salt": "goods-ladder-1338"}, f"o{i}") for i in range(8)],
                         ["B" if hashlib.sha256(f"goods-ladder-1338:o{i}".encode()).digest()[0] % 2 else "A"
                          for i in range(8)])
        grade = lambda o: "B" if hashlib.sha256(f"goods-grade-b-1033:{o}".encode()).digest()[0] % 2 else "A"
        nofit = lambda o: int(hashlib.sha256(o.encode()).hexdigest(), 16) % 2
        food = {"salt": "food-ladder-1033-35ba16a7d92ca5c5"}
        cells = collections.Counter()
        n = 20000
        for i in range(n):
            o = f"ord-{i:06d}"
            a = gl.arm({"salt": "goods-ladder-1338"}, o)
            cells["grade", a, grade(o)] += 1
            cells["nofit", a, nofit(o)] += 1
            cells["food", a, fl.arm(food, o)] += 1
        for k, v in cells.items():
            self.assertAlmostEqual(v / n, .25, delta=.02, msg=k)


class EligibleTest(unittest.TestCase):
    def test_left_alone(self):
        with tempfile.TemporaryDirectory() as md:
            switch(md, ON)
            b = order_in("B")
            for kw in ({"steps": (0.8, 0.9)}, {"steps": None}, {"resume": None}, {"probing": True},
                       {"code_priced": False}, {"ask": None}, {"ask": True}, {"ask": "552960"}):
                self.assertEqual(decide(md, b, **{"steps": (0.9,), **kw}), (kw.get("steps", (0.9,)), None), kw)
            self.assertEqual(decide(md, dict(b, kind="food")), ((0.9,), None))
            self.assertEqual(decide(md, dict(b, _b3={"applied": False})), ((0.9,), None))
            self.assertEqual(decide(md, dict(b, _b3=None))[0], (0.8, 0.9))

    def test_never_raises(self):
        with tempfile.TemporaryDirectory() as md:
            switch(md, ON)
            self.assertEqual(gl.decide(md, None, None, (0.9,), True, probing=False, code_priced=True), ((0.9,), None))
            self.assertEqual(gl.decide(md, {"kind": "goods", "order": 5}, {"ask": 1}, (0.9,), True, probing=False,
                                       code_priced=True), ((0.9,), None))

    def test_code_priced_rule_is_the_goods_opening_rule(self):
        q = {"ask": ASK, "ask_src": "metadata", "text": TEXT}
        self.assertIsNotNone(gfo.first_decision(q, LIMIT, (0.9,)))
        self.assertIsNone(gfo.first_decision(dict(q, text="no warranty, as is"), LIMIT, (0.9,)))
        self.assertIsNone(gfo.first_decision(dict(q, ask_src=None), LIMIT, (0.9,)))


class LazyCodePricedTest(unittest.TestCase):
    """independent review 459089c N1: the goods opening rule is read only with the study on, inside decide's guard"""
    def test_not_called_off_and_raising_is_the_current_talk(self):
        calls = []
        def boom():
            calls.append(1)
            raise TypeError("text is not a str")
        with tempfile.TemporaryDirectory() as md:
            self.assertEqual(decide(md, order_in("B"), code_priced=boom), ((0.9,), None))
            self.assertEqual(calls, [])                     # switch off: never read
            switch(md, ON)
            self.assertEqual(decide(md, order_in("B"), code_priced=boom), ((0.9,), None))
            self.assertEqual(decide(md, order_in("B"), code_priced=lambda: False), ((0.9,), None))
            self.assertEqual(decide(md, order_in("B"), code_priced=lambda: True)[0], (0.8, 0.9))

    def test_text_quote_is_code_priced_like_the_goods_opening(self):
        q = {"ask": ASK, "ask_src": "text", "text": f"G8ec51935 grade A, 72 h warranty, {ASK} won"}
        self.assertEqual(gfo.first_decision(q, LIMIT, (0.9,)) is not None,
                         oneshot.turn(None, q["text"], [], LIMIT, (0.9,), ASK, True) is not None)


class LearningTest(unittest.TestCase):
    """independent review 459089c I2: arm B's talks stay out of the shop learning both arms read"""
    NOW = 1794140000.0

    def test_laddered(self):
        self.assertTrue(gl.laddered({"goods_ladder": {"arm": "B"}}))
        self.assertFalse(gl.laddered({"goods_ladder": {"arm": "A"}}))
        self.assertFalse(gl.laddered({"goods_ladder": None}))
        self.assertFalse(gl.laddered({}))
        self.assertFalse(gl.laddered(None))

    def test_shop_profile_skips_arm_b(self):
        import buyer_shop_profile as sp
        rows = [{"ts": self.NOW - 60, "order": f"f{i}", "shop": "H", "limit": 1000, "asks": [1080, 1080],
                 "state": "TASK_STATE_CANCELED", "price": None, "goods_ladder": {"arm": "B"}} for i in range(20)]
        rows += [{"ts": self.NOW - 60, "order": f"d{i}", "shop": "H", "limit": 1000, "asks": [1000, 1000],
                  "state": "TASK_STATE_COMPLETED", "price": 1000, "passed_to_kit": True} for i in range(10)]
        self.assertEqual(sp.profile_from_rows(rows, {}, now=self.NOW), {})
        for r in rows[:20]:
            r["goods_ladder"] = {"arm": "A"}
        self.assertEqual(sp.profile_from_rows(rows, {}, now=self.NOW), {"H": 108})

    def test_rank_pg_skips_arm_b(self):
        import buyer_rank_pg as rp
        def row(i, g=None, price=None):
            return {"ts": self.NOW - 600, "shop": "a", "kind": "goods", "order": f"o-{i}", "limit": 1000,
                    "quotes": [{"shop": "a", "ask": 1200}], "asks": [1200], "price": price,
                    "passed_to_kit": price is not None, "state": "TASK_STATE_COMPLETED" if price else "NO_VALUE",
                    "seconds": {"quotes": 5.0, "end": 15.0}, "goods_ladder": g}
        paid = {f"o-{i}": "confirmed" for i in range(5)}
        rows = [row(i, price=1100) for i in range(5)] + [row(10 + i, g={"arm": "B"}) for i in range(5)]
        st = rp.stats_from_rows(rows, paid, self.NOW)
        self.assertEqual((st["shops"]["a|goods"]["n"], st["counts"]["excluded_rows"]), (5, 5))
        rows = [row(i, price=1100) for i in range(5)] + [row(10 + i, g={"arm": "A"}) for i in range(5)]
        self.assertEqual(rp.stats_from_rows(rows, paid, self.NOW)["shops"]["a|goods"]["n"], 10)


class TalkTest(unittest.TestCase):
    def test_taker_fixed_and_midpoint_shops(self):
        takes = lambda off, ask: None                                  # takes any offer (ddolcom, yms979 pattern)
        fixed = lambda off, ask: None if off >= ASK else ASK            # counters with its first ask (thisjonghoon)
        mid = lambda off, ask: None if off >= ask - 1000 else (off + ask) // 2   # meets halfway
        floor_ = lambda off, ask: None if off >= int(.9 * ASK) else int(.9 * ASK)  # takes 0.9, counters 0.9 below
        a, b = talk((0.9,), takes), talk((0.8, 0.9), takes)
        self.assertEqual(a[0], int(.9 * ASK))
        self.assertEqual(b[0], int(.8 * ASK))
        self.assertEqual(talk((0.9,), fixed)[0], None if ASK > LIMIT else ASK)
        self.assertEqual(talk((0.8, 0.9), fixed)[0], talk((0.9,), fixed)[0])
        self.assertLessEqual(talk((0.8, 0.9), mid)[0], talk((0.9,), mid)[0])
        self.assertEqual(talk((0.8, 0.9), floor_)[0], int(.9 * ASK))  # worst case: today's price, one turn later

    def test_accept_band_after_the_ladder(self):
        # review N2/N3: a shop that holds over my limit is taken by the band once both rungs and the raise are spent
        import buyer_accept_band as ab
        top = int(LIMIT * 1.05)
        hold = LIMIT + 1000
        for steps in ((0.9,), (0.8, 0.9)):
            history, ask, d = [], ASK, None
            for _ in range(8):
                d = oneshot.code_turn(ask, history, LIMIT, steps)
                d, _note = ab.turn(d, history, LIMIT, top, steps)
                if d["action"] == "walk_away" or d.get("accept_band"):
                    break
                history += [("shop asked", ask), ("you offered", d["price"])]
                ask = max(hold, d["price"]) if d["price"] < hold else d["price"]
            self.assertTrue(d.get("accept_band"), steps)
            self.assertEqual(d["price"], hold)


class HookTest(unittest.TestCase):
    def test_market_buyer_calls_it_after_food_ladder_and_records_it(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_buyer.py"), encoding="utf-8").read()
        i, j = src.index("buyer_food_ladder.decide("), src.index("buyer_goods_ladder.decide(")
        self.assertLess(i, j)
        self.assertLess(j, src.index("def price_ladder(meta, text, history):"))
        self.assertIn('"goods_ladder": order.get("_goods_ladder"),', src)


if __name__ == "__main__":
    unittest.main()
