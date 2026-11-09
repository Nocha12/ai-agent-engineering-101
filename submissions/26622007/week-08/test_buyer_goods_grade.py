import collections
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import buyer_food_ladder as fl     # noqa: E402
import buyer_goods_grade as gg     # noqa: E402
import market_buyer as mb          # noqa: E402
import market_model                # noqa: E402

ON = {"enabled": True, "study": "goods-grade-test", "salt": "goods-grade-b-1033"}
# Goods quote fragments as shops wrote them (.market/measure/orders.jsonl quotes[].text, 11/09 13:xx)
TEXT_B = "QCY T13 APP 무선 블루투스이어폰 ENC 노이즈캔슬링 (G5a3639e8), grade B (certified, 2h warranty). List 23500. Asking 16450."
TEXT_C = "Gx 무선 청소기, grade C (refurbished), 6h warranty. List 100000. Asking 52000."
TEXT_CAP_C = "Gx: Grade C (refurbished), warranty 6 h"                                       # ddolcom
TEXT_A = "Gx 무선 청소기, grade A, 6h warranty. List 100000. Asking 80000."
TEXT_TWO = "Gx grade A at 80000, we also have a cheaper grade C one"
TEXT_NONE = "Gx 무선 청소기, 6h warranty. List 100000. Asking 80000."
TEXT_EQ_C = "Gx grade=C cert=market-signed warranty=6h ask=52000"                           # dragonash: no space form

# 1,000 goods sold: SKU Gx 100 (5 broke), grade A 600/15, B 250/30, C 150/50; Gx grade C 10/4
STATS = {"sold": 1000, "broke": 100, "rate": 0.1,
         "by_sku": {"Gx": {"sold": 100, "broke": 5}},
         "by_grade": {"A": {"sold": 600, "broke": 15}, "B": {"sold": 250, "broke": 30}, "C": {"sold": 150, "broke": 50}},
         "by_shop": {}, "by_sku_grade": {"Gx|C": {"sold": 10, "broke": 4}, "Gx|A": {"sold": 80, "broke": 1}}}
SKU_CHANCE = market_model.break_chance(STATS, "Gx")


def switch(md, value):
    with open(os.path.join(md, gg.SWITCH), "w") as f:
        f.write(value if isinstance(value, str) else json.dumps(value))


def order_in(a, salt=ON["salt"]):
    for i in range(400):
        o = f"o-grade{i:04d}"
        if gg.arm({"salt": salt}, o) == a:
            return {"order": o, "kind": "goods"}
    raise AssertionError


def adjust(md, order, text=TEXT_C, stats=STATS, chance=SKU_CHANCE, sw_h=None, env=None, sku="Gx"):
    return gg.adjust(md, order, {"ask": 52000, "text": text}, stats, sku, chance, sw_h, env=env or {})


class Switch(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.mkdtemp()

    def test_off_missing_broken_or_env_zero_keeps_the_sku_chance_unrecorded(self):
        b = order_in("B")
        self.assertEqual(adjust(self.md, b), (SKU_CHANCE, None))                   # no switch
        for bad in ("{", "[]", {"enabled": "yes", "study": "s", "salt": "s"}, {"enabled": True, "study": "", "salt": "s"},
                    {"enabled": True, "study": "s"}, {"enabled": False, "study": "s", "salt": "s"}):
            switch(self.md, bad)
            self.assertEqual(adjust(self.md, b), (SKU_CHANCE, None), bad)
        switch(self.md, ON)
        for v in ("0", "false", "off", "No"):
            self.assertEqual(adjust(self.md, b, env={"BUYER_GOODS_GRADE": v}), (SKU_CHANCE, None))
        self.assertNotEqual(adjust(self.md, b)[0], SKU_CHANCE)                     # on: arm B moves it


class Arms(unittest.TestCase):
    def test_the_arm_is_the_first_byte_of_sha256_salt_order(self):
        cfg = {"salt": "goods-grade-b-1033"}
        for i in range(200):
            o = f"ord-{i}"
            want = "B" if hashlib.sha256(f"goods-grade-b-1033:{o}".encode()).digest()[0] % 2 else "A"
            self.assertEqual(gg.arm(cfg, o), want)
        self.assertIsNone(gg.arm(None, "x"))
        self.assertIsNone(gg.arm(cfg, ""))
        self.assertIsNone(gg.arm(cfg, None))

    def test_independent_of_the_food_ladder_and_the_nofit_last_bit(self):
        cells = collections.Counter()
        ladder = {"salt": "food-ladder-1033-35ba16a7d92ca5c5"}
        for i in range(10000):
            o = f"2026-11-09T{i:05d}"
            nofit = "B" if int(hashlib.sha256(o.encode()).hexdigest(), 16) % 2 else "A"
            cells[(gg.arm(ON, o), fl.arm(ladder, o), nofit)] += 1
        self.assertEqual(len(cells), 8)
        for k, n in cells.items():
            self.assertLess(abs(n - 1250), 1250 * 0.1, k)

    def test_grade_reads_the_short_warranty_pattern_one_grade_only(self):
        self.assertEqual(gg.grade(TEXT_B), "B")
        self.assertEqual(gg.grade(TEXT_C), "C")
        self.assertEqual(gg.grade(TEXT_CAP_C), "C")
        self.assertEqual(gg.grade(TEXT_A), "A")
        self.assertIsNone(gg.grade(TEXT_TWO))
        self.assertIsNone(gg.grade(TEXT_NONE))
        self.assertIsNone(gg.grade(TEXT_EQ_C))      # the same reading as market_buyer.short_cap
        self.assertIsNone(gg.grade(None))


class Treatment(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.mkdtemp()
        switch(self.md, ON)
        self.c = market_model.unit_break_chance(STATS, "Gx", "C")

    def test_arm_b_grade_c_takes_the_grade_chance_and_lowers_the_cap(self):
        chance, rec = adjust(self.md, order_in("B"))
        self.assertAlmostEqual(chance, self.c)
        self.assertGreater(self.c, SKU_CHANCE * 3)
        self.assertEqual(rec["arm"], "B")
        self.assertEqual(rec["grade"], "C")
        self.assertEqual(rec["study"], "goods-grade-test")
        self.assertAlmostEqual(rec["sku_chance"], round(SKU_CHANCE, 4))
        self.assertAlmostEqual(rec["grade_chance"], round(self.c, 4))
        self.assertTrue(rec["raised"])
        offer = {"list_price": 100000}
        self.assertLess(mb.saved_cap(offer, chance), mb.saved_cap(offer, SKU_CHANCE))

    def test_arm_a_keeps_the_sku_chance_and_records_what_b_would_use(self):
        chance, rec = adjust(self.md, order_in("A"))
        self.assertEqual(chance, SKU_CHANCE)
        self.assertEqual(rec["arm"], "A")
        self.assertAlmostEqual(rec["grade_chance"], round(self.c, 4))
        self.assertTrue(rec["raised"])          # would raise in B: the same eligible set in both arms

    def test_arm_b_never_lowers_the_chance(self):
        chance, rec = adjust(self.md, order_in("B"), text=TEXT_A)
        self.assertLess(market_model.unit_break_chance(STATS, "Gx", "A"), SKU_CHANCE)
        self.assertEqual(chance, SKU_CHANCE)
        self.assertFalse(rec["raised"])

    def test_untouched_and_unrecorded_outside_the_study(self):
        b = order_in("B")
        self.assertEqual(adjust(self.md, dict(b, kind="food")), (SKU_CHANCE, None))
        self.assertEqual(adjust(self.md, b, sw_h=2.0), (SKU_CHANCE, None))          # short warranty: its own cap
        self.assertEqual(adjust(self.md, b, sw_h=0), (SKU_CHANCE, None))
        self.assertEqual(adjust(self.md, b, text=TEXT_TWO), (SKU_CHANCE, None))
        self.assertEqual(adjust(self.md, b, text=TEXT_NONE), (SKU_CHANCE, None))
        self.assertEqual(adjust(self.md, b, stats=None), (SKU_CHANCE, None))
        self.assertEqual(adjust(self.md, b, stats=None, chance=None), (None, None))
        self.assertEqual(adjust(self.md, b, chance=None), (None, None))              # too few sales: no chance at all
        few = dict(STATS, sold=10)
        self.assertEqual(adjust(self.md, b, stats=few), (SKU_CHANCE, None))
        self.assertEqual(adjust(self.md, {"kind": "goods"}), (SKU_CHANCE, None))     # no order id

    def test_never_raises(self):
        b = order_in("B")
        self.assertEqual(adjust(self.md, b, stats={"sold": 1000, "by_grade": "x"}), (SKU_CHANCE, None))
        self.assertEqual(gg.adjust(self.md, b, None, STATS, "Gx", SKU_CHANCE, None, env={}), (SKU_CHANCE, None))
        self.assertEqual(gg.adjust(self.md, None, {}, STATS, "Gx", SKU_CHANCE, None, env={}), (SKU_CHANCE, None))

    def test_the_chance_stays_a_probability(self):
        bad = dict(STATS, by_sku_grade={"Gx|C": {"sold": 1, "broke": 50}})
        chance, rec = adjust(self.md, order_in("B"), stats=bad)
        self.assertEqual((chance, rec), (SKU_CHANCE, None))


class Wiring(unittest.TestCase):
    def test_market_buyer_calls_it_where_the_limit_is_set(self):
        src = open(mb.__file__, encoding="utf-8").read()
        i = src.index("chance = market_model.break_chance(ledger_breaks(md, catalog), key)")
        j = src.index("limit = min(value_limit(offer, args.budget, chance, buyer_cfg()), saved_cap(offer, chance))", i)
        self.assertIn("buyer_goods_grade.adjust(", src[i:j])
        self.assertIn('"goods_grade": order.get("_goods_grade")', src)


if __name__ == "__main__":
    unittest.main()
