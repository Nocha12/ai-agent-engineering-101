"""The talk's short-warranty cap is the receipt check's: the cheapest acceptable list and the grades' break chances.
11/08 19:32-20:00: ddolcom asked 60% of the SKU's list for grade C units, the talk capped at p_max(0) of that SKU's
list and its own chance (limit 660,913 of 1,094,400) and agreed; the receipt check, at the cheapest acceptable list
and the grade chances, refused all 12 (p 99-162% over p_max 0-31%). 12 of 21 ddolcom orders ended so."""
import unittest
from unittest import mock

import buyer_short_warranty as sw
import market_buyer

CAT = {"G1": {"list_price": 1313970}, "G2": {"list_price": 707000}}
BY = {"A": .03, "B": .12, "C": .39}


def chance(stats, sku, grade):
    return BY[grade]


class ShortTalkCapTest(unittest.TestCase):
    def setUp(self):
        self.order = {"kind": "goods", "order": "o-1", "acceptable": ["G1", "G2"],
                      "_sw": {"S": .2964, "buffer": 2e7, "hist": {"ddolcom": 0},
                              # its short receipts named hours over 0: priced at its 0 h history (one with none: never
                              # bought, test_buyer_zero_block)
                              "zero_hist": []}}
        p = [mock.patch.object(market_buyer, "ledger_breaks", return_value={"sold": 1000}),
             mock.patch.object(market_buyer.market_model, "unit_break_chance", side_effect=chance),
             mock.patch.dict(market_buyer._SW_CTX, {"md": "/md", "catalog": CAT}, clear=True)]
        for x in p:
            x.start(); self.addCleanup(x.stop)

    def test_cap_is_what_the_receipt_check_passes(self):
        o = {"sku": "G1", "list_price": 1313970}
        cap = market_buyer.short_cap(o, .07, self.order, "ddolcom", {"ask": 788382})
        self.assertEqual(cap, sw.cap(707000, 0, BY["A"], .2964))
        self.assertLess(cap, 788382)       # ddolcom's ask no longer fits, so the quote ranking passes it by
        lp, by_grade = market_buyer.short_terms(self.order, "/md", CAT, "G1")
        for price in (cap - 1, cap, cap + 1, 788382):
            grades, _ = sw.grades_ok(price, lp, 0, .2964, by_grade)
            self.assertEqual(bool(grades), price <= cap, price)

    def test_a_grade_the_quote_names_is_priced_at_its_chance(self):
        o = {"sku": "G1", "list_price": 1313970}
        q = {"ask": 788382, "text": "sofa: asking 788382. Grade C (refurbished), market certificate included."}
        self.assertEqual(market_buyer.short_cap(o, .07, self.order, "ddolcom", q), sw.cap(707000, 0, BY["C"], .2964))
        q["text"] = "SKU G1, grade c; market certificate at checkout."
        self.assertEqual(market_buyer.short_cap(o, .07, self.order, "ddolcom", q), sw.cap(707000, 0, BY["C"], .2964))
        q["text"] = "Grade A unit, asking 788382. We also have a cheaper refurbished (grade C) one."
        self.assertEqual(market_buyer.short_cap(o, .07, self.order, "ddolcom", q), sw.cap(707000, 0, BY["A"], .2964))
        q["text"] = "sofa: asking 788382. Grade Z."
        self.assertEqual(market_buyer.short_cap(o, .07, self.order, "ddolcom", q), sw.cap(707000, 0, BY["A"], .2964))

    def test_shop_usable_skus_count(self):
        cat = dict(CAT, G3={"list_price": 500000})
        with mock.patch.dict(market_buyer._SW_CTX, {"catalog": cat}):
            lp, _ = market_buyer.short_terms(self.order, "/md", cat, "G1", {"usable": [{"sku": "G3"}]})
        self.assertEqual(lp, 500000)

    def test_without_context_the_old_cap(self):
        market_buyer._SW_CTX.clear()
        o = {"sku": "G1", "list_price": 10000}
        self.assertEqual(market_buyer.short_cap(o, .072, self.order, "ddolcom", {}), sw.cap(10000, 0, .072, .2964))

    def test_failure_fails_closed(self):
        with mock.patch.object(market_buyer, "ledger_breaks", side_effect=OSError("x")):
            self.assertEqual(market_buyer.short_cap({"sku": "G1", "list_price": 10000}, .07, self.order, "ddolcom", {}), 0)


class LowestPriceSentenceTest(unittest.TestCase):
    """ggari-shop quotes "Our lowest price for this unit: N. Reply ok to take it.": 55 quotes in 6 h (11/08 14:31-20:31) had
    no ask, 36 of them within my limit, in 16 unpaid orders (GPT Pro 19:53 §2.3, checked on orders.jsonl)."""
    def test_read(self):
        o = {"_offer": {"sku": "G1", "list_price": 1000000}}
        self.assertEqual(market_buyer.text_ask_checked("Our lowest price for this unit: 570046. Reply ok to take it.", o), 570046)
        self.assertEqual(market_buyer.text_ask_checked("  Our lowest price for this unit: 570046. Reply ok to take it.\n", o), 570046)

    def test_only_that_sentence(self):
        o = {"_offer": {"sku": "G1", "list_price": 1000000}}
        for t in ("Our lowest price for this unit: 570046. Reply ok to take it. Or 500000 for two.",
                  "Our lowest price for this unit: 570046 for Gabcdef12. Reply ok to take it.",
                  "Our lowest price for this unit: 1570046. Reply ok to take it.",      # over list: a misread
                  "Our lowest price for this unit: 0. Reply ok to take it."):
            self.assertIsNone(market_buyer.text_ask_checked(t, o), t)


if __name__ == "__main__":
    unittest.main()
