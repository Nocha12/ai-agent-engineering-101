import collections
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import buyer_food_ladder as fl     # noqa: E402
import buyer_food_shelf as fs      # noqa: E402
import oneshot                     # noqa: E402

ON = {"enabled": True, "study": "food-ladder-test", "salt": "food-ladder-test"}
# Food quote fragments as shops wrote them (logs/market-kit.log, .market/measure/orders.jsonl 11/09 09:4x)
TEXT_C = "사세 직화K버팔로윙 1kg (G2560a8c9), grade C, market certificate included, 133 hours of shelf life left, list price 17900."
TEXT_B = "아삭하고 깊은맛 치즈 (G66ded25b), grade B, 89 h of shelf life left, market certificate included."
TEXT_EQ_C = "G3434758a grade=C cert=market-signed shelf=31.0h ask=9800"           # dragonash
TEXT_CAP_C = "G233e6ea4: Grade C (close to its date), 279.1 h shelf life left."   # ddolcom
TEXT_HELP = "G7fa48eaa, 665 h left | need a shelf life, grade or warranty? say e.g. 'need 48h, grade A/B'"
TEXT_NO_WORD = "G66ded25b: B (normal shelf life), about 89 h of shelf life left"      # bjeon01: no 'grade' word


def switch(md, value):
    with open(os.path.join(md, fl.SWITCH), "w") as f:
        f.write(value if isinstance(value, str) else json.dumps(value))


def order_in(a, salt=ON["salt"]):
    for i in range(400):
        o = f"o-ladder{i:04d}"
        if fl.arm({"salt": salt}, o) == a:
            return {"order": o, "kind": "food", "use_within_hours": 30.0}
    raise AssertionError


def decide(md, order, text=TEXT_C, steps=(0.9,), resume=True, probing=False, ask=9800, env=None, code_priced=True):
    return fl.decide(md, order, {"ask": ask, "text": text}, steps, resume, probing=probing, code_priced=code_priced,
                     env=env or {})


class GradeTest(unittest.TestCase):
    def test_real_formats(self):
        self.assertEqual(fl.grade(TEXT_C), "C")
        self.assertEqual(fl.grade(TEXT_B), "B")
        self.assertEqual(fl.grade(TEXT_EQ_C), "C")
        self.assertEqual(fl.grade(TEXT_CAP_C), "C")
        self.assertIsNone(fl.grade(TEXT_HELP))          # a question about grades names none
        self.assertIsNone(fl.grade(TEXT_NO_WORD))       # left unlabeled by design: the ladder without the C rung
        self.assertIsNone(fl.grade("grade A, or grade C if you prefer"))


class SwitchTest(unittest.TestCase):
    def test_off_missing_broken_or_env_keeps_steps_and_records_nothing(self):
        with tempfile.TemporaryDirectory() as md:
            o = order_in("B")
            self.assertEqual(decide(md, o), ((0.9,), None))           # no switch file
            switch(md, "{not json")
            self.assertEqual(decide(md, o), ((0.9,), None))
            switch(md, dict(ON, enabled=False))
            self.assertEqual(decide(md, o), ((0.9,), None))
            switch(md, dict(ON, salt=""))
            self.assertEqual(decide(md, o), ((0.9,), None))
            switch(md, ON)
            self.assertEqual(decide(md, o, env={"BUYER_FOOD_LADDER": "0"}), ((0.9,), None))


class ArmTest(unittest.TestCase):
    def test_arm_is_fixed_by_salt_and_order_id(self):
        self.assertEqual(fl.arm({"salt": "x"}, "o-1"), fl.arm({"salt": "x"}, "o-1"))
        self.assertIsNone(fl.arm({"salt": "x"}, ""))
        self.assertIsNone(fl.arm(None, "o-1"))

    def test_independent_of_food_shelf_arm(self):
        cells = collections.Counter(
            (fl.arm({"salt": "food-ladder-1033"}, f"o-{i:012x}"), fs.arm({"salt": "d99c80df743e2cc9"}, f"o-{i:012x}"))
            for i in range(20000))
        for cell in (("A", "A"), ("A", "B"), ("B", "A"), ("B", "B")):
            self.assertAlmostEqual(cells[cell] / 20000, 0.25, delta=0.02)


class EligibilityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.md = self.tmp.name
        switch(self.md, ON)

    def tearDown(self):
        self.tmp.cleanup()

    def test_treatment_c_grade_ladder(self):
        o = order_in("B")
        steps, rec = decide(self.md, o, TEXT_C)
        self.assertEqual(steps, (0.7, 0.8, 0.9))
        self.assertEqual(rec, {"study": "food-ladder-test", "arm": "B", "grade": "C", "steps": [0.7, 0.8, 0.9]})

    def test_treatment_c_grade_written_with_equals(self):
        self.assertEqual(decide(self.md, order_in("B"), TEXT_EQ_C)[0], (0.7, 0.8, 0.9))

    def test_treatment_other_grade_ladder(self):
        steps, rec = decide(self.md, order_in("B"), TEXT_B)
        self.assertEqual(steps, (0.8, 0.9))
        self.assertEqual(rec["grade"], "B")

    def test_control_keeps_steps_but_is_recorded(self):
        steps, rec = decide(self.md, order_in("A"), TEXT_C)
        self.assertEqual(steps, (0.9,))
        self.assertEqual(rec, {"study": "food-ladder-test", "arm": "A", "grade": "C", "steps": [0.9]})

    def test_ineligible_orders_are_untouched_and_unrecorded(self):
        b = order_in("B")
        cases = [
            dict(order=dict(b, kind="goods")),
            dict(order=b, steps=(0.85, 0.9)),             # pricing.json oneshot_steps set: not this study's baseline
            dict(order=b, steps=None),                    # oneshot off
            dict(order=b, resume=None),                   # no quote talk to resume
            dict(order=b, ask=None),
            dict(order=b, ask=True),
            dict(order=b, probing=True),                  # one-won / opening-discount probes own the opening
            dict(order=dict(b, _one_won_default=True)),
            dict(order=dict(b, _food_quote_yield="B")),
            dict(order=dict(b, _food_shelf={"arm": "B", "would_drop": ["olstjr"]})),
            dict(order=dict(b, _b3={"applied": False})),   # B3 hold rung on: the two studies stay disjoint
            dict(order=b, code_priced=False),              # the opening goes to the harness, not the ladder
        ]
        for c in cases:
            steps = c.get("steps", (0.9,))
            got = decide(self.md, c["order"], steps=steps, resume=c.get("resume", True),
                         probing=c.get("probing", False), ask=c.get("ask", 9800),
                         code_priced=c.get("code_priced", True))
            self.assertEqual(got, (steps, None), c)

    def test_food_shelf_without_would_drop_stays_eligible(self):
        o = dict(order_in("B"), _food_shelf={"arm": "B", "would_drop": []})
        self.assertEqual(decide(self.md, o)[0], (0.7, 0.8, 0.9))
        o = dict(order_in("B"), _food_shelf={"arm": "A", "left": {}})    # pick never reached keep()
        self.assertEqual(decide(self.md, o)[0], (0.7, 0.8, 0.9))


def talk(shop, steps, limit, first_ask):
    """Final price of a code-priced talk with a shop answering by rule, or None when it ends with no deal."""
    history, ask = [], first_ask
    for _ in range(10):
        d = oneshot.code_turn(ask, history, limit, steps)
        assert d["price"] <= limit and (d["action"] != "offer" or d["price"] <= ask), (d, limit, ask)
        if d["action"] != "offer":
            return None
        history += [("shop asked", ask), ("you offered", d["price"])]
        nxt = shop(d["price"], first_ask)
        if nxt is None:
            return d["price"]          # taken
        ask = nxt
    return None


class LadderOutcomeTest(unittest.TestCase):
    """Shop types seen in the 11/08-11/09 food talks (review/W-20261109-1033-AX취약점루프/weakness-02-buyer.md 2.3)."""
    LIMIT, ASK = 7000, 6300

    def test_accepting_shop_sells_lower_in_b(self):
        taker = lambda offer, first: None if offer >= 0.75 * first else round(0.8 * first)
        a = talk(taker, (0.9,), self.LIMIT, self.ASK)
        b = talk(taker, (0.8, 0.9), self.LIMIT, self.ASK)
        self.assertEqual(a, int(0.9 * self.ASK))
        self.assertEqual(b, int(0.8 * self.ASK))

    def test_fixed_price_shop_same_price_in_both(self):
        fixed = lambda offer, first: None if offer >= first else first
        self.assertEqual(talk(fixed, (0.9,), self.LIMIT, self.ASK), self.ASK)
        self.assertEqual(talk(fixed, (0.7, 0.8, 0.9), self.LIMIT, self.ASK), self.ASK)

    def test_midpoint_shop_never_worse_in_b(self):
        mid = lambda offer, first: None if offer >= 0.85 * first else round((offer + first) / 2)
        a = talk(mid, (0.9,), self.LIMIT, self.ASK)
        b = talk(mid, (0.7, 0.8, 0.9), self.LIMIT, self.ASK)
        self.assertIsNotNone(b)
        self.assertLessEqual(b, a)

    def test_offers_stay_within_limit_when_ask_over_limit(self):
        mid = lambda offer, first: None if offer >= 0.95 * first else round((offer + first) / 2)
        b = talk(mid, (0.7, 0.8, 0.9), 5000, self.ASK)
        self.assertTrue(b is None or b <= 5000)


if __name__ == "__main__":
    unittest.main()
