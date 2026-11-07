"""Quote scheduling at its public prefetch boundary; no market or model calls."""
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import market_buyer as mb


class FoodQuoteYield(unittest.TestCase):
    def test_food_prefetch_values_valid_saved_share_not_review_count(self):
        now = time.time()
        shops = [{"name": "cheap", "reviews": [{}], "usable": []},
                 {"name": "famous", "reviews": [{}] * 100, "usable": []},
                 {"name": "new", "reviews": [], "usable": []}]
        rows = [{"ts": now - 60 - i, "category": "c", "kind": "food", "budget": 1000,
                 "quotes": [{"shop": "cheap", "ask": 50, "list_price": 100, "fit": True},
                            {"shop": "famous", "ask": 69, "list_price": 100, "fit": True}]}
                for i in range(3)]
        with tempfile.TemporaryDirectory() as md:
            os.mkdir(os.path.join(md, "measure"))
            with open(os.path.join(md, "measure", "orders.jsonl"), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
            with mock.patch.dict(os.environ, {"BUYER_QUOTE_YIELD": "1"}):
                early, all_shops = mb.prefetch_pool(shops, {"trades": []}, md, "c", 3, kind="food")
            self.assertEqual([s["name"] for s in all_shops], ["cheap", "new", "famous"])
            self.assertEqual([s["name"] for s in early], ["cheap", "new"])
            with mock.patch.dict(os.environ, {"BUYER_QUOTE_YIELD": "0"}):
                _, old = mb.prefetch_pool(shops, {"trades": []}, md, "c", 3, kind="food")
            self.assertEqual([s["name"] for s in old], ["famous", "new", "cheap"])
            _, goods = mb.prefetch_pool(shops, {"trades": []}, md, "c", 3, kind="goods")
            self.assertEqual([s["name"] for s in goods], ["famous", "new", "cheap"])

    def test_failures_count_and_future_other_category_and_goods_do_not_train_food(self):
        now = time.time()
        quotes = lambda fit: [{"shop": "a", "ask": 50, "list_price": 100, "fit": fit}]
        rows = [{"ts": now - 10, "category": "c", "kind": "food", "quotes": quotes(True)} for _ in range(3)]
        rows += [{"ts": now - 5, "category": "c", "kind": "food", "quotes": quotes(False)}]
        rows += [{"ts": now + 1, "category": "c", "kind": "food", "quotes": quotes(True)},
                 {"ts": now - 20, "category": "other", "kind": "food", "quotes": quotes(True)},
                 {"ts": now - 20, "category": "c", "kind": "goods", "quotes": quotes(True)}]
        with tempfile.TemporaryDirectory() as md:
            os.mkdir(os.path.join(md, "measure"))
            with open(os.path.join(md, "measure", "orders.jsonl"), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
            score = mb.recent_quote_yields(md, "c", now=now)
        self.assertAlmostEqual(score["a"], 1.675 / 6)  # 3 x .5 plus prior .175, 4 attempts + 2 prior
