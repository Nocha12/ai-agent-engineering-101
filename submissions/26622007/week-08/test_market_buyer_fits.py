import json, os, tempfile, time, unittest

import market_buyer


class RecentFitsTest(unittest.TestCase):
    def test_counts_asks_within_my_limit_for_the_category_in_the_window(self):
        now = time.time()
        rows = [{"ts": now - 60, "category": "c", "quotes": [{"shop": "a", "ask": 10, "lost": None},
                                                            {"shop": "b", "ask": 12, "lost": "less expected surplus"},
                                                            {"shop": "c", "ask": 99, "lost": "over my limit (budget / share / deal cap)"},
                                                            {"shop": "d", "ask": None, "lost": "no ask (no free unit or no answer)"}]},
                {"ts": now - 4 * 3600, "category": "c", "quotes": [{"shop": "c", "ask": 5, "lost": None}]},
                {"ts": now - 60, "category": "other", "quotes": [{"shop": "c", "ask": 5, "lost": None}]}]
        with tempfile.TemporaryDirectory() as md:
            os.makedirs(os.path.join(md, "measure"))
            with open(os.path.join(md, "measure", "orders.jsonl"), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
                f.write('{"ts": cut')
            got = market_buyer.recent_fits(md, "c", now=now)
            self.assertEqual(dict(got), {"a": 1, "b": 1})
            self.assertEqual(market_buyer.recent_fits(md + "/none", "c"), {})


if __name__ == "__main__":
    unittest.main()
