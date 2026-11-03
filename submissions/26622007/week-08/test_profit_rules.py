"""The GPT Pro profit review applied (review/W-20261103-2120/gpt-pro-answer1.md): which unit a buyer can be offered, the
life of a unit's lock, the floor and first ask, the restock guard over held + waiting + new stock, the next-window buy.

    python -m unittest test_profit_rules -v        (from submissions/26622007/week-08)
"""
import json
import os
import tempfile
import time
import unittest

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
                         (900000, 3, 3, 2500000, 200000, 100000, 1))
        first = p["next_window"]["done_plans"][0]["targets"]          # the review's table: 10 SKUs, 13 units
        self.assertEqual((len(first), sum(first.values())), (10, 13))
        self.assertNotIn("G07ec17d5", first)
        self.assertEqual(p["next_window"]["targets"], {"G1515d951": 1})     # the mattress line the 22:44 window refused


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

    def test_food_cost_cap_counts_what_is_held(self):
        inv = {"balance": 5000000, "units": self.held("F2", 3, cost=50000, kind="food")}       # 150,000 of food
        self.assertEqual(restock.guard([{"sku": "F1", "qty": 3}], inv, CAT, CFG), [{"sku": "F1", "qty": 1}])   # 30,000 worst each

    def test_stock_cost_cap_counts_waiting_lines(self):
        inv = {"balance": 5000000, "units": self.held("H1", 0)}
        waiting = [{"sku": "BIG", "qty": 1, "worst": 300000}, {"sku": "F2", "qty": 1, "worst": 590000}]
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 3}], inv, CAT, CFG, None, waiting), [])

    def test_cash_kept(self):
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 3}], {"balance": 2520000, "units": []}, CAT, CFG), [{"sku": "H1", "qty": 1}])


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


if __name__ == "__main__":
    unittest.main()
