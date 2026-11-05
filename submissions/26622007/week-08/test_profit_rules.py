"""The GPT Pro profit review applied (review/W-20261103-2120/gpt-pro-answer1.md): which unit a buyer can be offered, the
life of a unit's lock, the floor and first ask, the restock guard over held + waiting + new stock, the next-window buy.

    python -m unittest test_profit_rules -v        (from submissions/26622007/week-08)
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import hard_limits
import profit_price
import restock
import shop_stock
from shop_stock import StockBook, needs

HERE = os.path.dirname(os.path.abspath(__file__))


def unit(uid, sku="G1", hours=500.0, grade="A", kind="food", cost=6000, list_price=10000, supplied_ago=3600):
    return {"unit": uid, "sku": sku, "name": f"포기김치 {sku}", "category": "식품>김치", "kind": kind, "cost": cost,
            "list_price": list_price, "grade": grade, "supplied_at": time.time() - supplied_ago,
            "expires_at": time.time() + hours * 3600 if kind == "food" else None, "cert": {"cert": {}, "sig": "s"}}


class NeedsTest(unittest.TestCase):
    def test_reads_what_real_buyers_wrote(self):
        self.assertEqual(needs("I want to buy G200e39a3. Please quote this exact SKU, certificate and warranty hours. "
                               "Food must last at least 126.0 hours after payment.")["hours"], 126.0)
        self.assertEqual(needs("I want to buy 빼빼로 (Gfbb10ae2). I need at least 72.0 hours of shelf life.")["hours"], 72.0)
        self.assertEqual(needs("I want to buy 육개장 (G1234abcd). Please include the unit in the receipt. "
                               "I need it to last 48 hours.")["hours"], 48.0)
        self.assertEqual(needs("with market certificate (cert=true) and at least 96h shelf life")["hours"], 96.0)
        self.assertEqual(needs("I want to buy G1234abcd")["skus"], {"G1234abcd"})

    def test_warranty_hours_are_not_shelf_life_and_no_grade_is_not_a_grade(self):
        n = needs("I want to buy (G2) with a 6 h warranty. Quote price, grade and warranty.")
        self.assertIsNone(n["hours"])
        self.assertIsNone(n["grades"])
        self.assertEqual(needs("grade A or B only")["grades"], {"A", "B"})
        self.assertEqual(needs("grade b or better")["grades"], {"A", "B"})
        self.assertEqual(needs("B등급 이상")["grades"], {"A", "B"})
        self.assertIsNone(needs("Quote price, a grade and warranty")["grades"])


class EligibleFirstTest(unittest.TestCase):
    def test_a_unit_that_will_not_last_is_skipped_even_though_it_expires_first(self):
        b = StockBook(lambda: {"round": 3, "units": [unit("short", hours=60), unit("long", hours=500)]})
        self.assertEqual(b.find("I want to buy (G1). I need at least 72 hours of shelf life.")["unit"], "long")
        self.assertEqual(b.find("I want to buy (G1)")["unit"], "short")              # no need named: first to expire

    def test_the_margin_to_pay_counts(self):
        b = StockBook(lambda: {"round": 3, "units": [unit("edge", hours=72.05)]})
        u, why = b.pick("I want to buy (G1). It must last 72 hours.")
        self.assertIsNone(u)
        self.assertIn("72 h asked", why)

    def test_grade_and_named_sku(self):
        b = StockBook(lambda: {"round": 3, "units": [unit("c", grade="C"), unit("a", sku="G2", grade="A")]})
        self.assertIsNone(b.find("I want to buy (G1), grade A or B"))
        self.assertEqual(b.find("I want to buy (G2), grade A")["unit"], "a")


class LockTest(unittest.TestCase):
    def setUp(self):
        self.units = [unit("a"), unit("b", hours=600)]
        self.paid, self.unpaid = [], []
        self.b = StockBook(lambda: {"round": 3, "units": list(self.units)})
        self.b.on_paid = self.paid.append
        self.b.on_unpaid = lambda info, why: self.unpaid.append((info["task"], why))

    def test_a_paid_unit_leaves_the_stock_and_is_reported_paid(self):
        self.b.agree("a", {"task": "t1", "price": 9000})
        self.assertEqual(self.b.find("G1")["unit"], "b")
        self.units.pop(0)                                       # the kit's checkout settled: inventory.json lost it
        self.b.units()
        self.assertEqual([p["task"] for p in self.paid], ["t1"])
        self.assertIn("a", self.b.sold)

    def test_an_unpaid_deal_frees_its_unit_after_the_wait(self):
        self.b.agree("a", {"task": "t1", "price": 9000})
        self.b.held["a"] -= shop_stock.PAY_SECONDS + 1
        self.assertEqual(self.b.find("G1")["unit"], "a")
        self.assertEqual(self.unpaid[0][0], "t1")
        self.assertEqual(self.paid, [])

    def test_a_payment_in_progress_keeps_the_unit(self):
        self.b.agree("a", {"task": "t1", "price": 9000})
        self.b.held["a"] -= shop_stock.PAY_SECONDS - 5
        self.b.hold("a")                                        # our checkout's complete renews the hold
        self.b.agreed["a"]["at"] -= 1000
        self.assertEqual(self.b.find("G1")["unit"], "b")
        self.assertEqual(self.unpaid, [])

    def test_a_cancelled_or_refused_checkout_frees_the_unit_at_once(self):
        self.b.agree("a", {"task": "t1", "price": 9000})
        self.b.release("a")
        self.assertEqual(self.b.find("G1")["unit"], "a")
        self.assertEqual(self.unpaid, [("t1", "checkout cancelled or refused")])

    def test_food_that_expired_while_waiting_is_not_a_sale(self):
        self.units[0] = dict(self.units[0], expires_at=time.time() + 1)
        self.b.agree("a", {"task": "t1", "price": 9000, "expires_at": time.time() - 1})
        self.units.pop(0)
        self.b.units()
        self.assertEqual(self.paid, [])
        self.assertEqual(self.unpaid[0][1], "expired before payment")

    def test_a_unit_sold_while_a_talk_was_silent_is_not_promised_again(self):
        self.b.hold("a", "t1")
        self.b.held["a"] -= shop_stock.HOLD_SECONDS               # t1 went quiet
        self.assertEqual(self.b.find("G1")["unit"], "a")
        self.b.hold("a", "t2")
        self.b.agree("a", {"task": "t2", "price": 9000})
        self.units.pop(0)                                       # t2 paid
        self.assertFalse(self.b.available("a", "t1"))
        self.assertEqual([p["task"] for p in self.paid], ["t2"])
        self.assertTrue(self.b.available("b", "t1"))

    def test_a_payment_after_the_wait_is_still_credited_in_kit_mode(self):
        self.b.agree("a", {"task": "t1", "price": 9000})
        self.b.held["a"] -= shop_stock.PAY_SECONDS + 1
        self.assertEqual(self.b.find("G1")["unit"], "a")          # on sale again
        self.units.pop(0)                                       # the slow buyer paid
        self.b.units()
        self.assertEqual([(p["task"], p["late"]) for p in self.paid], [("t1", True)])

    def test_a_newer_deal_on_the_unit_wins_over_the_late_one(self):
        self.b.agree("a", {"task": "t1", "price": 9000})
        self.b.held["a"] -= shop_stock.PAY_SECONDS + 1
        self.b.units()
        self.b.agree("a", {"task": "t2", "price": 9500})
        self.units.pop(0)
        self.b.units()
        self.assertEqual([p["task"] for p in self.paid], ["t2"])

    def test_paid_food_checked_after_its_date_is_still_a_sale(self):
        self.units[0] = dict(self.units[0], expires_at=time.time() + 100)
        self.b.agree("a", {"task": "t1", "price": 9000, "expires_at": time.time() + 100})
        self.b.units()                                          # seen in stock, still good
        self.units.pop(0)
        self.b.agreed["a"].update(seen=time.time() - 200, expires_at=time.time() - 100)   # last seen good, looked at after its date
        self.b.units()
        self.assertEqual([p["task"] for p in self.paid], ["t1"])

    def test_a_silent_talk_lets_go_sooner_than_before(self):
        self.assertLessEqual(shop_stock.HOLD_SECONDS, 300)
        self.b.hold("a", "t1")
        self.assertEqual(self.b.holder("a"), "t1")
        self.b.held["a"] -= shop_stock.HOLD_SECONDS
        self.assertIsNone(self.b.holder("a"))


class PriceTest(unittest.TestCase):
    def test_floor_matches_the_review(self):
        stand = {"kind": "goods", "cost": 8518, "list_price": 11900}
        self.assertEqual(profit_price.floor(8518, 11900, "goods", 0.15), 11223)     # §2-b table
        self.assertEqual(profit_price.floor(8518, 11900, "goods", 0.25), 12720)
        self.assertEqual(profit_price.floor(6000, 10000, "food", 0.15), 7015)      # food: b = 0 whatever is passed
        q = profit_price.quote(stand)
        self.assertEqual((q["floor"], q["stress_floor"], q["refuse"]), (11223, 12720, False))

    def test_first_ask_is_105_percent_of_the_median_capped_at_list_and_never_below_the_floor(self):
        self.assertEqual(profit_price.opening(7000, 10000, median=8000)[0], 8400)
        self.assertEqual(profit_price.opening(7000, 10000, median=9800)[0], 10000)
        self.assertEqual(profit_price.opening(9000, 10000, median=7000)[0], 9000)
        self.assertEqual(profit_price.opening(7000, 10000, kind="food")[0], 7434)            # no median: 70.8% of list
        self.assertEqual(profit_price.opening(7000, 10000, median=8000, paid=[9500])[0], 9500)  # a price we were paid stays

    def test_scarce_ask_is_a_fixed_candidate_that_does_not_climb_with_the_median(self):
        pricing = {"price": {"scarce_asks": {"M": 262996}}}
        m = {"sku": "M", "kind": "goods", "cost": 150000, "list_price": 265050}
        other = {**m, "sku": "N"}
        self.assertEqual(profit_price.quote(other, median=238772, pricing=pricing)["open"], 250711)
        q = profit_price.quote(m, median=238772, pricing=pricing)
        self.assertEqual((q["open"], q["why"]), (262996, "scarce ask 262996"))
        self.assertEqual(profit_price.quote(m, median=245000, pricing=pricing)["open"], 262996)    # a higher median: same ask
        self.assertEqual(profit_price.quote(m, median=252500, pricing=pricing)["open"], 265050)    # median above it wins, list caps
        self.assertEqual(profit_price.quote(m, median=200000, paid=[264000], pricing=pricing)["open"], 264000)
        self.assertEqual(profit_price.quote(m, pricing=pricing)["open"], 262996)                   # no median: kind ratio path
        high = {"price": {"scarce_asks": {"M": 900000}}}
        self.assertEqual(profit_price.quote(m, median=238772, pricing=high)["open"], 265050)       # never above list
        for bad in (None, "x", True, -5, [1]):
            self.assertEqual(profit_price.quote(m, median=238772, pricing={"price": {"scarce_asks": {"M": bad}}})["open"], 250711)
        self.assertEqual(profit_price.quote(m, median=238772, pricing={"price": {"scarce_asks": [1]}})["open"], 250711)
        held = {**m, "cost": 260000}                                         # floor above the scarce ask: floor wins
        self.assertEqual(profit_price.quote(held, median=238772, pricing=pricing)["open"], profit_price.quote(held)["floor"])

    def test_ask_cap_lowers_the_first_ask_per_grade_but_never_under_the_floor(self):
        caps = {"price": {"scarce_asks": {"S": 392986}, "ask_caps": {"S": {"A": 350000, "C": 279000}, "W": {"*": 80000}}}}
        c = {"sku": "S", "kind": "goods", "grade": "C", "cost": 131904, "list_price": 420000}
        a = {**c, "grade": "A", "cost": 273599}
        q = profit_price.quote(c, median=330000, pricing=caps)                 # the scarce ask 392,986 loses to the cap
        self.assertEqual((q["open"], q["floor"]), (279000, 170393))
        self.assertTrue(q["why"].startswith("ask cap 279000 for grade C"))
        self.assertEqual(profit_price.quote(a, median=330000, pricing=caps)["open"], 350000)
        self.assertEqual(profit_price.quote({**a, "grade": "B"}, median=330000, pricing=caps)["open"], 392986)  # no B cap
        w = {"sku": "W", "kind": "goods", "grade": "A", "cost": 72637, "list_price": 96900}
        self.assertEqual(profit_price.quote(w, median=86555, pricing=caps)["open"], 89089)   # floor 89,089 above the cap
        self.assertEqual(profit_price.quote({**w, "cost": 55025}, median=86555, pricing=caps)["open"], 80000)
        low = {"price": {"ask_caps": {"W": {"*": 99000}}}}                     # a cap above the ask changes nothing
        self.assertEqual(profit_price.quote(w, median=86555, pricing=low)["open"], profit_price.quote(w, median=86555)["open"])
        for bad in (None, "x", True, -5, 0, [1], float("nan"), float("inf")):
            self.assertEqual(profit_price.quote(w, median=86555, pricing={"price": {"ask_caps": {"W": {"*": bad}}}})["open"], 90883)
        self.assertEqual(profit_price.quote(w, median=86555, pricing={"price": {"ask_caps": [1]}})["open"], 90883)
        self.assertEqual(profit_price.quote(w, median=86555, pricing={"price": {"ask_caps": {"W": 5}}})["open"], 90883)

    def test_goods_above_the_warranty_limit_are_refused_and_the_ledger_rate_counts(self):
        g = {"kind": "goods", "cost": 6000, "list_price": 10000}
        self.assertTrue(profit_price.quote(g, measured=0.3)["refuse"])
        self.assertEqual(profit_price.quote(g, measured=0.2)["b"], 0.2)
        self.assertEqual(profit_price.quote(g, measured=0.05)["b"], 0.15)

    def test_shipped_settings(self):
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            p = json.load(f)
        self.assertEqual(p["concession"], [0.6, 0.4, 0.2])
        self.assertEqual(profit_price.cfg_of(p)["goods_break"], 0.15)
        r = p["restock"]
        self.assertEqual((r["max_stock_cost"], r["max_units_per_sku"], r["cover_hours"], r["keep_cash"],
                          r["food_max_stock_cost"], r["high_price_goods"], r["high_price_max_units"]),
                         (15000000, 6, 6, 4500000, 650000, 100000, 3))   # keep_cash: 21.9M open warranties at 05:00
        # scale 2 (a person's 15M cap, 11/05 11:48): cover 6 h and 3 dear units so the model asks for the 60만+ lines
        # (our share 0.35-0.76/h); six runs without a sale fill ~15.0M, 10.5M of it 60만+ (scale2-result.md)
        # food 65만: food sells in 0.1 h with nothing spoiled, and its 45만 cap cut 416 of the last 2,000 guard lines
        # (review/W-20261105-0953-AX패치3.3/scale-analysis.md)
        # 6 per SKU: G1515d951 147 of 147 sold, out of stock 45% of 11-04 10-16 h (review/W-20261104-1507 all-shops review)
        self.assertEqual(r["max_sku_share"], 0.2)
        proven = ["G1515d951", "G6b9db161"]           # each sold out before the next window (G1515d951 3 in 6 min, twice;
        # G6b9db161 2 per window 04:03-04:44); G0f415407 and G9c02e68e left extra units unsold twice: no more buying
        self.assertEqual((r["high_price_proven_max_units"], r["high_price_proven_skus"]), (6, proven))
        # G1515d951 at 6 also cut stock-outs 35% -> 0% while our sales went 41 -> 35 and the market's rose 921 -> 954
        # (13:25-16:46 vs 17:07-20:27), so 4 holds; the four foods out of stock 35-54% of 17:30-20:31 go to the front
        # (review/W-20261104-2030, GPT Pro 3판 §4.2). Under 15M the 60만+ lines go first: at 12:24 they came after five food
        # lines and G03d0199f was refused when the window closed (scale2-result.md §8)
        self.assertEqual((r["priority_skus"], r["sku_target_units"]),
                         (["G8ec51935", "Gb5d0dde7", "G03d0199f", "G05277ffe", "G00327e80", "G75f11c19", "Gef4d3036",
                           "Gb2392fa9", "G200e39a3", "G66ded25b", "G1465c678"],
                          {"G1515d951": 4, "G6b9db161": 1, "G75f11c19": 1, "G05277ffe": 3, "Gb5d0dde7": 3,
                           "G8ec51935": 3, "G03d0199f": 2, "G00327e80": 2, "G1465c678": 1}))   # G1515 4: Astra 21:19, same five units across two supply windows; G6 2: undo the reduction triggered by mislinked refunds
        # Patch 3.3 (book): one unit each of two 60만+ goods 12-14 shops sell 39-49/h at 3-4% mature break, after the proven
        # SKUs and food (scale-analysis.md); the 2.32M sofa G8ec51935 waits for a larger cap
        cat = {"G05277ffe": {"kind": "goods", "list_price": 600000}, "Gb5d0dde7": {"kind": "goods", "list_price": 1094400},
               "G8ec51935": {"kind": "goods", "list_price": 2320000}, "G8095ddd6": {"kind": "goods", "list_price": 851400}}
        cfg = hard_limits.clamp_cfg("restock", {**restock.DEFAULTS, **r})
        buy = [{"sku": s, "qty": 5} for s in cat]
        inv = {"balance": 50000000, "units": [{"sku": "G05277ffe", "kind": "goods", "cost": 360000}]}
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(restock.guard(buy, inv, cat, cfg), [{"sku": "G05277ffe", "qty": 2}, {"sku": "Gb5d0dde7", "qty": 3},
                                                              {"sku": "G8ec51935", "qty": 3}])     # 3 each under 15M, one held
            self.assertEqual(restock.guard(buy, {**inv, "units": []}, cat, cfg),
                             [{"sku": "G05277ffe", "qty": 3}, {"sku": "Gb5d0dde7", "qty": 3}, {"sku": "G8ec51935", "qty": 3}])   # unlisted stay out
        self.assertEqual(r["food_max_qty"], 4)     # every food line refills up to 4 units, not 3 (the SKU cap stays max_units_per_sku 6)
        self.assertTrue(r["high_price_listed_only"])
        # no cap per deal (hard_limits.UNCAPPED; at 800k my buyer settled 1 of 113 dear orders) and the whole list price:
        # 27 orders in 4.5 h ended with the shop's lowest ask between 0.9 and 1.0 of list, while only 6 of 271 deals paid
        # within 1% of the limit, so the limit is rarely where a deal lands
        self.assertNotIn("max_spend_per_deal", p["buyer"])
        self.assertEqual((p["buyer"]["max_price_share"], p["forum"]["every_minutes"]), (1.0, 10))
        self.assertTrue({"G054ff120", "G0f415407", "G9c02e68e", "Gf0293c0e", "G9c1a6eee", "Gbb0cbbff"} <= set(r["skip_skus"]))
        self.assertNotIn("G6b9db161", r["skip_skus"])   # held units sold at capped asks; allow replenishment
        self.assertEqual(p["price"]["scarce_asks"], {"G1515d951": 380911})
        self.assertEqual(p["price"]["ask_caps"]["G6b9db161"], {"A": 350000, "C": 279000})
        first = p["next_window"]["done_plans"][0]["targets"]          # the review's table: 10 SKUs, 13 units
        self.assertEqual((len(first), sum(first.values())), (10, 13))
        self.assertNotIn("G07ec17d5", first)
        self.assertEqual(p["next_window"]["targets"], {"G1465c678": 1})     # one-unit demand and mature-margin trial


CAT = {"F1": {"kind": "food", "list_price": 40000}, "F2": {"kind": "food", "list_price": 40000},
       "H1": {"kind": "goods", "list_price": 20000}, "BIG": {"kind": "goods", "list_price": 400000}}
CFG = {**restock.DEFAULTS, "keep_cash": 2500000, "max_stock_cost": 900000, "max_units_per_sku": 3, "food_max_qty": 3,
       "food_max_stock_cost": 200000, "high_price_goods": 100000, "high_price_max_units": 1, "skip_skus": ["X"]}


class GuardTest(unittest.TestCase):
    def held(self, sku, n, cost=10000, kind="goods"):
        return [{"unit": f"{sku}{i}", "sku": sku, "kind": kind, "cost": cost} for i in range(n)]

    def test_units_per_sku_count_stock_waiting_and_new(self):
        inv = {"balance": 5000000, "units": self.held("H1", 1)}
        waiting = [{"sku": "H1", "qty": 1, "worst": 15000}]
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 5}], inv, CAT, CFG, None, waiting), [{"sku": "H1", "qty": 1}])
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 2}, {"sku": "H1", "qty": 2}], inv, CAT, CFG), [{"sku": "H1", "qty": 2}])

    def test_one_unit_of_expensive_goods_and_skipped_skus(self):
        inv = {"balance": 5000000, "units": []}
        self.assertEqual(restock.guard([{"sku": "BIG", "qty": 2}, {"sku": "X", "qty": 1}], inv, CAT, CFG), [{"sku": "BIG", "qty": 1}])
        self.assertEqual(restock.guard([{"sku": "BIG", "qty": 1}], {"balance": 5000000, "units": self.held("BIG", 1)}, CAT, CFG), [])

    def test_two_units_of_expensive_goods_and_the_cut_is_logged(self):
        inv = {"balance": 5000000, "units": []}
        two = {**CFG, "high_price_max_units": 2}
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(restock.guard([{"sku": "BIG", "qty": 3}], inv, CAT, two), [{"sku": "BIG", "qty": 2}])
            self.assertEqual(restock.guard([{"sku": "BIG", "qty": 3}], inv, CAT, {**two, "max_stock_cost": 400000}),
                             [{"sku": "BIG", "qty": 1}])                    # 300,000 worst each
        self.assertIn("BIG 3 -> 2 (units per SKU)", err.getvalue())
        self.assertIn("BIG 3 -> 1 (units per SKU, stock cost)", err.getvalue())

    def test_three_units_only_for_proven_expensive_goods(self):
        inv = {"balance": 5000000, "units": []}
        cat = {**CAT, "BIG2": {"kind": "goods", "list_price": 400000}}
        cfg = {**CFG, "max_stock_cost": 4000000, "high_price_max_units": 2, "high_price_proven_max_units": 3,
               "high_price_proven_skus": ["BIG"]}
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(restock.guard([{"sku": "BIG", "qty": 4}, {"sku": "BIG2", "qty": 4}], inv, cat, cfg),
                             [{"sku": "BIG", "qty": 3}, {"sku": "BIG2", "qty": 2}])
            no_key = {k: v for k, v in cfg.items() if k != "high_price_proven_max_units"}   # falls back to the plain cap
            self.assertEqual(restock.guard([{"sku": "BIG", "qty": 4}], inv, cat, no_key), [{"sku": "BIG", "qty": 2}])

    def test_food_cost_cap_counts_what_is_held(self):
        inv = {"balance": 5000000, "units": self.held("F2", 3, cost=50000, kind="food")}       # 150,000 of food
        self.assertEqual(restock.guard([{"sku": "F1", "qty": 3}], inv, CAT, CFG), [{"sku": "F1", "qty": 1}])   # 30,000 worst each

    def test_stock_cost_cap_counts_waiting_lines(self):
        inv = {"balance": 5000000, "units": self.held("H1", 0)}
        waiting = [{"sku": "BIG", "qty": 1, "worst": 300000}, {"sku": "F2", "qty": 1, "worst": 590000}]
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 3}], inv, CAT, CFG, None, waiting), [])

    def test_cash_kept(self):
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 3}], {"balance": 2520000, "units": []}, CAT, CFG), [{"sku": "H1", "qty": 1}])


class WithTrialTest(unittest.TestCase):
    def test_trial_goes_first_and_the_usual_lines_fill_the_room_left(self):
        inv = {"balance": 5000000, "units": []}
        # BIG's trial unit (worst 300,000) leaves 600,000 of the 900,000 stock cap: H1 3 still fits, BIG is not bought twice
        out = restock.with_trial([{"sku": "BIG", "qty": 1}], [{"sku": "BIG", "qty": 1}, {"sku": "H1", "qty": 3}],
                                 inv, CAT, CFG)
        self.assertEqual(out, [{"sku": "BIG", "qty": 1}, {"sku": "H1", "qty": 3}])
        self.assertEqual(restock.with_trial([{"sku": "BIG", "qty": 1}], [{"sku": "F2", "qty": 3}], inv, CAT,
                                            dict(CFG, max_stock_cost=320000)), [{"sku": "BIG", "qty": 1}])

    def test_no_trial_leaves_the_lines_alone(self):
        lines = [{"sku": "H1", "qty": 9}]
        self.assertEqual(restock.with_trial(None, lines, {}, CAT, CFG), lines)
        self.assertEqual(restock.with_trial([], lines, {}, CAT, CFG), lines)


class TrialInMainTest(unittest.TestCase):
    """restock.main() with a next_window trial: it joins the window's usual lines instead of replacing them."""
    CATALOG = {"H1": {"kind": "goods", "category": "a", "list_price": 20000},
               "H2": {"kind": "goods", "category": "b", "list_price": 20000}}
    PRICING = {"restock": {"keep_cash": 1500000, "max_stock_cost": 900000}, "next_window": {"id": "t", "targets": {"H1": 1}}}

    def run_main(self, trial, restock_state):
        with tempfile.TemporaryDirectory() as d:
            inv = {"round": 3, "balance": 5000000, "units": [], "restock": restock_state}
            for name, data in (("inventory.json", inv), ("catalog.json", self.CATALOG)):
                with open(os.path.join(d, name), "w") as f:
                    json.dump(data, f)
            printed, real_load = [], restock.load
            env = {k: v for k, v in os.environ.items() if k != "HARNESS"}
            with mock.patch.object(restock, "read_model", lambda *a: None), \
                    mock.patch.object(restock, "load", lambda p, dflt: self.PRICING if p == "pricing.json" else real_load(p, dflt)), \
                    mock.patch.object(restock.opening, "lines", lambda *a, **k: (None, "test")), \
                    mock.patch.object(restock, "next_window", lambda *a, **k: (trial, "trial")), \
                    mock.patch.object(restock, "plan", lambda *a, **k: {"buy": [{"sku": "H1", "qty": 2}, {"sku": "H2", "qty": 2}]}), \
                    mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "argv", ["x", "--state", d]), \
                    mock.patch("builtins.print", lambda *a, **k: printed.append(a[0] if a else "")):
                restock.main()
        return json.loads(printed[-1])["buy"]

    def test_the_trial_and_the_usual_lines_go_out_together(self):
        now = time.time()
        self.assertEqual(self.run_main([{"sku": "H1", "qty": 1}], {"open": True, "closes_at": now + 170}),
                         [{"sku": "H1", "qty": 1}, {"sku": "H2", "qty": 2}])

    def test_once_the_trial_is_sent_the_window_still_buys_but_not_its_skus(self):
        now = time.time()
        self.assertEqual(self.run_main([], {"open": True, "closes_at": now + 120}), [{"sku": "H2", "qty": 2}])
        self.assertEqual(self.run_main(None, {"open": True, "closes_at": now + 120}),     # no trial: all of the plan
                         [{"sku": "H1", "qty": 2}, {"sku": "H2", "qty": 2}])

    def test_completed_trial_is_not_replenished_when_held_for_judgment(self):
        pricing = {**self.PRICING, "next_window": {**self.PRICING["next_window"], "hold_after_done": True}}
        with mock.patch.object(self, "PRICING", pricing):
            self.assertEqual(self.run_main(None, {"open": True, "closes_at": time.time() + 120}), [{"sku": "H2", "qty": 2}])

    def test_a_trial_waiting_for_its_window_keeps_the_lead_time_empty(self):
        self.assertEqual(self.run_main([], {"open": False, "opens_at": time.time() + 30}), [])


class PendingAndNextWindowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = self.tmp.name
        now = time.time()
        self.open_inv = {"balance": 5000000, "units": [], "restock": {"open": True, "closes_at": now + 170}}
        self.cfg = {"id": "t1", "targets": {"F1": 2, "H1": 1, "BIG": 1}}

    def test_sent_lines_wait_until_their_units_show_up(self):
        restock.note_sent(self.md, [{"sku": "H1", "qty": 2}], CAT)
        self.assertEqual(restock.pending(self.md, {"units": []})[0]["qty"], 2)
        arrived = {"units": [dict(unit("x", sku="H1", kind="goods"), supplied_at=time.time())]}
        self.assertEqual(restock.pending(self.md, arrived)[0]["qty"], 1)
        self.assertEqual(restock.pending(self.md, {"units": []}, now=time.time() + restock.PENDING_SECONDS + 1), [])

    def test_buys_the_targets_less_what_is_held_or_waiting_once(self):
        inv = dict(self.open_inv, units=[unit("k", sku="F1")])
        keep = lambda b: restock.guard(b, inv, CAT, CFG, None, [{"sku": "H1", "qty": 1, "worst": 15000}])
        out, why = restock.next_window(self.md, inv, CAT, self.cfg, [{"sku": "H1", "qty": 1, "worst": 15000}], keep)
        self.assertEqual(out, [{"sku": "F1", "qty": 1}, {"sku": "BIG", "qty": 1}])
        came = inv["units"] + [unit("k2", sku="F1", supplied_ago=0), unit("b", sku="BIG", kind="goods", supplied_ago=0)]
        out, why = restock.next_window(self.md, dict(inv, units=came), CAT, self.cfg, [{"sku": "H1", "qty": 1, "worst": 15000}], keep)
        self.assertEqual(out, [])                                # all there: nothing else is bought in this window
        closed = dict(inv, units=came, restock={"open": False, "opens_at": time.time() + 900})
        self.assertIn("bought in the window", restock.next_window(self.md, closed, CAT, self.cfg)[1])
        self.assertIn("done", restock.next_window(self.md, self.open_inv, CAT, self.cfg)[1])

    def test_waits_for_a_window_and_expires(self):
        closed = dict(self.open_inv, restock={"open": False, "opens_at": time.time() + 900})
        self.assertEqual(restock.next_window(self.md, closed, CAT, self.cfg)[0], [])
        later = time.time() + 3 * 3600
        out, why = restock.next_window(self.md, self.open_inv, CAT, self.cfg, now=later)
        self.assertIsNone(out)
        self.assertIn("expired", why)

    def test_a_damaged_record_of_sent_lines_counts_as_nothing(self):
        for junk in ({"a": 1}, [1, {"sku": "H1"}, {"sku": "H1", "qty": "2", "at": time.time()}]):
            with open(os.path.join(self.md, restock.SENT), "w") as f:
                json.dump(junk, f)
            self.assertEqual(restock.pending(self.md, {"units": []}), [])
            restock.note_sent(self.md, [{"sku": "H1", "qty": 1}], CAT)

    def test_sends_once_per_plan(self):
        out, _ = restock.next_window(self.md, self.open_inv, CAT, self.cfg)
        self.assertTrue(out)
        self.assertEqual(restock.next_window(self.md, self.open_inv, CAT, self.cfg)[0], [])     # units sold meanwhile: not rebought

    def test_never_sends_before_the_window_opens(self):
        soon = dict(self.open_inv, restock={"open": False, "opens_at": time.time() + 20, "closes_at": time.time() + 200})
        self.assertEqual(restock.next_window(self.md, soon, CAT, self.cfg)[0], [])

    def test_a_line_that_never_arrived_is_tried_once_more_alone(self):
        out, _ = restock.next_window(self.md, self.open_inv, CAT, self.cfg)
        self.assertEqual({b["sku"] for b in out}, {"F1", "H1", "BIG"})
        came = [dict(unit("f1", sku="F1"), supplied_at=time.time()), dict(unit("f2", sku="F1"), supplied_at=time.time()),
                dict(unit("h", sku="H1", kind="goods"), supplied_at=time.time())]
        closed = dict(self.open_inv, units=came, restock={"open": False, "opens_at": time.time() + 900})
        self.assertIn("retried", restock.next_window(self.md, closed, CAT, self.cfg)[1])
        out, _ = restock.next_window(self.md, dict(self.open_inv, units=came), CAT, self.cfg)
        self.assertEqual(out, [{"sku": "BIG", "qty": 1}])
        why = restock.next_window(self.md, dict(closed), CAT, self.cfg)[1]
        self.assertIn("never arrived: ['BIG']", why)                # once only
        self.assertIn("done", restock.next_window(self.md, self.open_inv, CAT, self.cfg)[1])

    def test_off_without_a_plan(self):
        self.assertEqual(restock.next_window(self.md, self.open_inv, CAT, {}), (None, "no next_window plan"))


class SurgeTest(unittest.TestCase):
    """Patch 3.2: the caps reserve the surge cost, a SKU high under surge buys less or nothing."""
    CFG = {**CFG, "max_units_per_sku": 20, "max_stock_cost": 10 ** 9, "priority_skus": ["F1"]}

    def inv(self, *rows):
        return {"balance": 50000000, "units": [], "surge": [{"sku": s, "units": 0, "factor": f} for s, f in rows]}

    def test_the_worst_cost_follows_the_factor_and_the_line_size(self):
        f = restock.surge(self.inv(("H1", 1.2), ("F1", 9)))
        self.assertEqual(f, {"H1": 1.2, "F1": 1.5})
        self.assertEqual(restock.worst("H2", 3, CAT, f), 0)                       # not in the catalog
        self.assertAlmostEqual(restock.worst("F2", 3, CAT, f), 40000 * 0.75)       # not under surge
        self.assertAlmostEqual(restock.worst("H1", 3, CAT, f), 20000 * 0.75 * (1.2 + 0.004 + 0.05))
        self.assertAlmostEqual(restock.worst("F1", 3, CAT, f), 40000 * 0.75 * 1.5)
        self.assertEqual(restock.surge({"surge": "x"}), {})
        self.assertEqual(restock.surge({"surge": [{"sku": "H1", "factor": True}, {"factor": 1.3}]}), {})

    def test_half_from_1_20_unless_listed_and_none_from_1_30(self):
        cfg = {**self.CFG, "max_units_per_sku": 4}
        buy = [{"sku": "H1", "qty": 4}, {"sku": "F1", "qty": 2}, {"sku": "F2", "qty": 2}]
        out = restock.guard(buy, self.inv(("H1", 1.22), ("F1", 1.25), ("F2", 1.3)), CAT, cfg)
        self.assertEqual(out, [{"sku": "H1", "qty": 2}, {"sku": "F1", "qty": 2}])      # half of the 4-unit cap, listed whole
        inv = dict(self.inv(("H1", 1.22), ("F1", 1.35)), units=[unit("h", sku="H1", kind="goods")])
        out = restock.guard(buy, inv, CAT, cfg)
        self.assertEqual(out, [{"sku": "H1", "qty": 1}, {"sku": "F1", "qty": 1}, {"sku": "F2", "qty": 2}])   # one held of two; listed keeps one
        inv["units"].append(unit("f", sku="F1"))
        self.assertEqual([b["sku"] for b in restock.guard(buy, inv, CAT, cfg)], ["H1", "F2"])          # F1 has its one unit

    def test_the_stock_cap_counts_the_surge_cost(self):
        cfg = {**self.CFG, "max_stock_cost": 100000}
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 8}], self.inv(), CAT, cfg), [{"sku": "H1", "qty": 6}])
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 8}], self.inv(("H1", 1.1)), CAT, cfg), [{"sku": "H1", "qty": 5}])

    def test_sent_lines_reserve_the_surge_cost(self):
        with tempfile.TemporaryDirectory() as md:
            restock.note_sent(md, [{"sku": "H1", "qty": 2}], CAT, factors={"H1": 1.2})
            self.assertAlmostEqual(restock.pending(md, {"units": []})[0]["worst"], 20000 * 0.75 * 1.252)


if __name__ == "__main__":
    unittest.main()
