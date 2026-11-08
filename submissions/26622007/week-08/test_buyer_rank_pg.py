import json
import os
import tempfile
import time
import unittest
from unittest import mock

import buyer_rank_pg as rp
import market_buyer

NOW = 1794140000.0


def row(shop, kind, ask, price=None, talk=10.0, ts=NOW - 600, state="TASK_STATE_COMPLETED", order=None):
    return {"ts": ts, "shop": shop, "kind": kind, "order": order, "quotes": [{"shop": shop, "ask": ask}], "asks": [ask],
            "state": state if price is not None or state != "TASK_STATE_COMPLETED" else "TASK_STATE_CANCELED",
            "price": price, "passed_to_kit": price is not None, "seconds": {"quotes": 5.0, "end": 5.0 + talk}}


class StatsTest(unittest.TestCase):
    def test_shrunk_to_the_kinds_pool(self):
        rows = [row("a", "goods", 10000, 9000) for _ in range(10)] + [row("b", "goods", 10000) for _ in range(30)]
        st = rp.stats_from_rows(rows, {}, NOW)
        pool = st["pool"]["goods"]
        self.assertAlmostEqual(pool["p"], 10 / 40)
        self.assertAlmostEqual(pool["ratio"], 0.9)                         # final price / first ask, of the deals
        a = st["shops"]["a|goods"]
        self.assertAlmostEqual(a["p"], (10 + 20 * 0.25) / (10 + 20))       # (k + 20 p) / (n + 20)
        self.assertAlmostEqual(st["shops"]["b|goods"]["p"], (0 + 20 * 0.25) / (30 + 20))
        self.assertAlmostEqual(a["talk"], 10.0)

    def test_old_unpaid_and_no_value_rows_do_not_count(self):
        rows = [row("a", "goods", 10000, 9000, ts=NOW - 25 * 3600), row("a", "goods", 10000, 9000, order="o-1"),
                row("a", "goods", 10000, state="NO_VALUE"), row("a", "food", 5000, 4000)]
        st = rp.stats_from_rows(rows, {"o-1": False}, NOW)
        self.assertEqual((st["shops"]["a|goods"]["n"], st["shops"]["a|goods"]["k"]), (1, 0))
        self.assertEqual(st["shops"]["a|food"]["k"], 1)


class ScoreTest(unittest.TestCase):
    RK = {"stats": {"pool": {"goods": {"p": 0.5, "ratio": 1.0, "talk": None}},
                    "shops": {"a|goods": {"p": 0.8, "ratio": 0.9, "talk": 15.0}}}}

    def test_p_times_the_final_share_of_list_per_second(self):
        g = lambda p: 0.9 * (1 - p)
        # E[final] = 8,000 x .9 = 7,200 of 10,000: g = .9 x .28; T = 5 + 15 + 20
        self.assertAlmostEqual(rp.score(self.RK, "a", "goods", 8000, 10000, g, 5.0), 0.8 * 0.9 * 0.28 / 40)
        # an unknown shop: the kind's pool, no talk seconds, so no ÷T
        self.assertAlmostEqual(rp.score(self.RK, "z", "goods", 8000, 10000, g, 5.0), 0.5 * 0.9 * 0.2)
        self.assertIsNone(rp.score(self.RK, "a", "food", 8000, 10000, g, 5.0))     # no pool for the kind
        self.assertIsNone(rp.score(self.RK, "a", "goods", None, 10000, g, 5.0))

    def test_switch(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(rp.state(d, {}, env={}))
            for body in ('{"enabled": false}', '{"enabled": "true"}', "x"):
                with open(os.path.join(d, rp.SWITCH), "w") as f:
                    f.write(body)
                self.assertIsNone(rp.state(d, {}, env={}))
            with open(os.path.join(d, rp.SWITCH), "w") as f:
                f.write('{"enabled": true}')
            self.assertIsNotNone(rp.state(d, {}, env={}))
            self.assertIsNone(rp.state(d, {}, env={"BUYER_RANK_PG": "0"}))
            with open(os.path.join(d, rp.CACHE)) as f:
                self.assertIn("stats", json.load(f))                     # computed once and cached


class BestQuoteTest(unittest.TestCase):
    """best_quote: off keeps the current order; on ranks the fits by P x g / T and never puts an ask over my limit first."""
    ASKS = {"a": 6000, "b": 6400, "c": 9900}         # c: over the 6 h cap (7,000 at b 0)
    STATS = {"pool": {"goods": {"p": 0.5, "ratio": 1.0, "talk": 10.0}},
             "shops": {"a|goods": {"p": 0.2, "ratio": 1.0, "talk": 10.0}, "b|goods": {"p": 0.9, "ratio": 1.0, "talk": 10.0},
                       "c|goods": {"p": 1.0, "ratio": 0.5, "talk": 10.0}}}

    def run_bq(self, order):
        s = [{"name": n, "url": f"https://{n}.example", "usable": [{"sku": "H1", "name": "x", "qty": 1, "list_price": 10000}]}
             for n in "abc"]

        def fake_quote(sh, label, key, order, out, ctl=None):
            out[sh["name"]] = {"ask": self.ASKS[sh["name"]], "text": "", "refused": None}
        with mock.patch.object(market_buyer, "quote", fake_quote), mock.patch.object(market_buyer, "log"), \
                mock.patch.object(market_buyer, "cancel_quietly", lambda q, n: None), \
                mock.patch.object(market_buyer, "STARTED", time.time()):
            best, _ = market_buyer.best_quote(s[0], s[1:], order, {}, 20000, lambda x: None, {"quote_shops": 3}, wait=2)
        return best["name"]

    def test_off_is_the_current_order(self):
        self.assertEqual(self.run_bq({"kind": "goods"}), "a")              # the most (1 - b)(list - ask) / list

    def test_on_picks_the_likelier_deal_within_the_limit(self):
        order = {"kind": "goods", "_rank_pg": {"stats": self.STATS}}
        self.assertEqual(self.run_bq(order), "b")                          # .9 x .36 > .2 x .40; c does not fit
        self.assertEqual((order["_rank_pg"]["plain"], order["_rank_pg"]["picked"]), ("a", "b"))

    def test_on_with_a_broken_cache_keeps_the_current_order(self):
        for stats in ({"pool": {}, "shops": {"a|goods": 1}}, {"pool": [], "shops": None}, {"pool": {"goods": {"p": "x"}}}):
            order = {"kind": "goods", "_rank_pg": {"stats": stats}}
            self.assertEqual(self.run_bq(order), "a")
            self.assertEqual(order["_rank_pg"]["picked"], "a")

    def test_on_without_stats_for_the_kind_keeps_the_current_order(self):
        order = {"kind": "goods", "_rank_pg": {"stats": {"pool": {}, "shops": {}}}}
        self.assertEqual(self.run_bq(order), "a")


class OurOffersTest(unittest.TestCase):
    def test_order_record_has_our_offers_besides_the_shops_asks(self):
        out = {"state": "TASK_STATE_COMPLETED", "history": [("shop asked", 9000), ("you offered", 7000), ("shop asked", 8500),
                                                            ("you offered", 7800), ("shop asked", 8000)]}
        args = mock.Mock(budget=20000)
        shop = {"name": "a"}
        r = market_buyer.order_record({}, args, [shop], {}, "a", "", shop, "", {}, 9000, None, out, None, {}, [], [], None,
                                      None, {}, {})
        self.assertEqual(r["our_offers"], [7000, 7800])
        self.assertEqual((r["asks"], r["counters"]), ([9000, 8500, 8000], 3))


if __name__ == "__main__":
    unittest.main()
