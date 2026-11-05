import json, os, tempfile, time, unittest

import market_buyer


class RecentFitsTest(unittest.TestCase):
    def test_counts_asks_within_my_limit_for_the_category_in_the_window(self):
        now = time.time()
        rows = [{"ts": now - 60, "category": "c", "budget": 50,
                 "quotes": [{"shop": "a", "ask": 10, "list_price": 20, "lost": None},
                            {"shop": "b", "ask": 12, "list_price": 20, "lost": "less expected surplus"},
                            {"shop": "c", "ask": 99, "list_price": 120, "lost": "over my limit (budget / share / deal cap)"},
                            {"shop": "d", "ask": None, "list_price": 20, "lost": "no ask (no free unit or no answer)"}]},
                # the chosen shop (lost None) asked over the budget, or over the list price: not a fit
                {"ts": now - 60, "category": "c", "budget": 30, "quotes": [{"shop": "e", "ask": 40, "list_price": 90, "lost": None},
                                                                          {"shop": "f", "ask": 25, "list_price": 20, "lost": None},
                                                                          {"shop": "g", "ask": 5, "lost": None}]},
                # lines with fit say so themselves
                {"ts": now - 60, "category": "c", "budget": 30, "quotes": [{"shop": "a", "ask": 40, "list_price": 90, "lost": None, "fit": True},
                                                                          {"shop": "h", "ask": 5, "list_price": 9, "lost": None, "fit": False}]},
                {"ts": now - 4 * 3600, "category": "c", "budget": 50, "quotes": [{"shop": "c", "ask": 5, "list_price": 9, "lost": None}]},
                {"ts": now - 60, "category": "other", "budget": 50, "quotes": [{"shop": "c", "ask": 5, "list_price": 9, "lost": None}]}]
        with tempfile.TemporaryDirectory() as md:
            os.makedirs(os.path.join(md, "measure"))
            with open(os.path.join(md, "measure", "orders.jsonl"), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
                f.write('{"ts": cut')
            got = market_buyer.recent_fits(md, "c", now=now)
            self.assertEqual(dict(got), {"a": 2, "b": 1})
            self.assertEqual(market_buyer.recent_fits(md + "/none", "c"), {})
            self.assertEqual(market_buyer.recent_fits(md, None), {})

    def test_the_first_batch_keeps_one_slot_for_a_shop_that_has_not_fitted(self):
        import collections
        shop = lambda n, r: {"name": n, "reviews": [0] * r}
        others = [shop("new", 9), shop("fit1", 1), shop("old", 5), shop("fit2", 3)]
        fits = collections.Counter({"fit1": 52, "fit2": 1})
        order = [s["name"] for s in market_buyer.quote_order(others, fits, 3)]
        self.assertEqual(order, ["fit2", "new", "fit1", "old"])      # fitters by reviews, not by their fit count
        self.assertEqual([s["name"] for s in market_buyer.quote_order(others, fits, 2)], ["new", "fit2", "fit1", "old"])


if __name__ == "__main__":
    unittest.main()
