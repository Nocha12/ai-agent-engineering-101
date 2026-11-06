"""Quotes run while the harness chooses, quote_shops at its hard_limits maximum (a person, 11/06 15:24: "as parallel as
possible"). The market gives one open order per buyer, 20 s apart at the earliest, so this is the parallelism there is."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hard_limits                 # noqa: E402
import market_buyer                # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def shop(n, sku="H1", lp=10000):
    return {"name": n, "url": f"https://{n}.example", "reviews": [], "usable": [{"sku": sku, "name": "청소기", "list_price": lp}]}


def open_task(n):
    return {"id": f"t-{n}", "status": {"state": "TASK_STATE_INPUT_REQUIRED"}}


class Pinned(unittest.TestCase):
    def test_quote_shops_stays_at_the_hard_limit_and_the_prefetch_stays_on(self):
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            shipped = json.load(f)["buyer"]["quote_shops"]
        self.assertEqual(shipped, hard_limits.BOUNDS["buyer.quote_shops"][1])
        self.assertTrue(market_buyer.PREFETCH_QUOTES)
        with open(os.path.join(HERE, "meta", "terra.md"), encoding="utf-8") as f:
            terra = f.read()
        self.assertIn("Do not lower the parallelism", terra)
        self.assertIn("never cut quote_shops or turn the prefetch off", terra)


class BestQuoteWithPrefetch(unittest.TestCase):
    def run_quotes(self, asks, chosen, prefetched, others, k=5, delay=0.0, wait=5):
        seen, lock, live, peak = [], threading.Lock(), [0], [0]
        cancelled = []

        def fake_quote(s, label, key, order, out, ctl=None):
            with lock:
                seen.append(s["name"])
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(delay)
            q = {"ask": asks.get(s["name"]), "text": "", "url": s["url"], "task": open_task(s["name"]), "refused": None}
            out[s["name"]] = q
            if ctl is not None and ctl.get("done") and ctl.get("winner") != s["name"]:
                market_buyer.cancel_quote(q, s["name"])
            with lock:
                live[0] -= 1
        with mock.patch.object(market_buyer, "quote", fake_quote), mock.patch.object(market_buyer, "log"), \
                mock.patch.object(market_buyer, "buyer_rpc", lambda url, m, p: cancelled.append(p["id"])), \
                mock.patch.object(market_buyer, "STARTED", time.time()):
            pre = {"got": {}, "ctl": {"done": False, "winner": None}}
            pre["threads"] = market_buyer.start_quotes(prefetched, {"kind": "goods"}, 20000, lambda s: None,
                                                       pre["got"], pre["ctl"])
            best, got = market_buyer.best_quote(chosen, others, {"kind": "goods"}, {}, 20000, lambda s: None,
                                                {"quote_shops": k}, wait=wait, pre=pre)
            time.sleep(0.1)
        return best, got, seen, peak[0], cancelled

    def test_prefetched_shops_are_not_asked_again_and_the_first_batch_is_quote_shops(self):
        s = [shop(n) for n in "abcdefgh"]
        best, got, seen, peak, _ = self.run_quotes({"c": 7000}, s[0], s[1:5], s[1:])
        self.assertEqual(sorted(seen), list("abcde"))     # b-e while choosing, then the chosen a; nobody twice
        self.assertLessEqual(peak, 5)                     # never more at once than quote_shops
        self.assertEqual(best["name"], "c")

    def test_the_chosen_shop_among_the_prefetched_tops_up_the_batch_with_the_next(self):
        s = [shop(n) for n in "abcdefgh"]
        best, got, seen, peak, _ = self.run_quotes({"b": 7000}, s[1], s[1:5], [x for x in s if x is not s[1]])
        self.assertEqual(sorted(seen), list("abcde"))     # b-e prefetched, b chosen: a fills the fifth slot
        self.assertEqual(seen.count("b"), 1)
        self.assertLessEqual(peak, 5)

    def test_only_the_kept_talk_stays_open_so_one_order_can_never_close_twice(self):
        s = [shop(n) for n in "abcdefgh"]
        asks = {n: 7000 + i for i, n in enumerate("abcdefgh")}     # all within the 7,500 limit
        best, got, seen, peak, cancelled = self.run_quotes(asks, s[0], s[1:5], s[1:], delay=0.05)
        open_ = [n for n, q in got.items() if market_buyer.resumable(q)]
        self.assertEqual(open_, [best["name"]])
        self.assertEqual(sorted(cancelled), sorted(f"t-{n}" for n in got if n != best["name"]))

    def test_a_prefetched_quote_answering_after_the_choice_closes_itself(self):
        s = [shop(n) for n in "abc"]
        slow = threading.Event()

        def fake_quote(sh, label, key, order, out, ctl=None):
            if sh["name"] == "c":
                slow.wait(2)
            q = {"ask": 7000, "text": "", "url": sh["url"], "task": open_task(sh["name"]), "refused": None}
            out[sh["name"]] = q
            if ctl is not None and ctl.get("done") and ctl.get("winner") != sh["name"]:
                market_buyer.cancel_quote(q, sh["name"])
        cancelled = []
        with mock.patch.object(market_buyer, "quote", fake_quote), mock.patch.object(market_buyer, "log"), \
                mock.patch.object(market_buyer, "buyer_rpc", lambda url, m, p: cancelled.append(p["id"])), \
                mock.patch.object(market_buyer, "STARTED", time.time()):
            pre = {"got": {}, "ctl": {"done": False, "winner": None}}
            pre["threads"] = market_buyer.start_quotes(s[1:], {}, 20000, lambda x: None, pre["got"], pre["ctl"])
            best, got = market_buyer.best_quote(s[0], s[1:], {}, {}, 20000, lambda x: None, {"quote_shops": 3},
                                                wait=0.3, pre=pre)
            self.assertNotIn("c", got)                    # c was left out: too slow
            slow.set()
            time.sleep(0.2)
        self.assertIn("t-c", cancelled)
        self.assertFalse(market_buyer.resumable(got["c"]))
        self.assertNotEqual(best["name"], "c")


class MainOverlap(unittest.TestCase):
    CATALOG = {"H1": {"list_price": 10000, "kind": "goods"}}
    ORDER = {"order": "o-1", "item": "청소기", "budget": 20000, "acceptable": ["H1"], "kind": "goods", "category": "가전>청소기"}

    def run_main(self, shops, hopeless=False, choose_s=0.5, quote_s=0.5):
        printed, events = [], []
        lock = threading.Lock()

        def note(what):
            with lock:
                events.append((what, time.time()))

        def fake_quote(s, label, key, order, out, ctl=None):
            note(f"quote {s['name']}")
            time.sleep(quote_s)
            out[s["name"]] = {"ask": 7000 if s["name"] == "c" else 9000, "text": "", "url": s["url"],
                              "task": open_task(s["name"]), "refused": None}

        def fake_choose(s, m, i, b):
            note("choose start")
            time.sleep(choose_s)
            note("choose end")
            return s[0], "t"

        def neg(*a, **k):
            note("talk")
            return {"state": "TASK_STATE_REJECTED", "task": None, "receipt": None, "history": [], "agreed": None, "holds": 0}
        with tempfile.TemporaryDirectory() as d:
            sp, op = os.path.join(d, "shops.json"), os.path.join(d, "order.json")
            with open(sp, "w") as f:
                json.dump(shops, f)
            with open(op, "w") as f:
                json.dump(self.ORDER, f)
            with open(os.path.join(d, "catalog.json"), "w") as f:
                json.dump(self.CATALOG, f)
            with mock.patch.object(market_buyer, "MEMORY", os.path.join(d, "m.json")), \
                    mock.patch.object(market_buyer, "STARTED", time.time()), \
                    mock.patch.object(market_buyer, "market_round", lambda: 3), \
                    mock.patch.object(market_buyer, "serves_acp", lambda u: True), \
                    mock.patch.object(market_buyer, "buyer_cfg", lambda: {"quote_shops": 5}), \
                    mock.patch.object(market_buyer, "choose", fake_choose), \
                    mock.patch.object(market_buyer, "quote", fake_quote), \
                    mock.patch.object(market_buyer, "buyer_rpc", lambda *a: None), \
                    mock.patch.object(market_buyer, "hopeless", lambda *a, **k: hopeless), \
                    mock.patch.object(market_buyer, "negotiate", neg), \
                    mock.patch.object(market_buyer, "log"), \
                    mock.patch("builtins.print", lambda *a, **k: printed.append(a[0])), \
                    mock.patch.object(sys, "argv", ["x", "--shops", sp, "--item", "청소기", "--budget", "20000", "--order", op]):
                t0 = time.time()
                market_buyer.main()
                took = time.time() - t0
            with open(os.path.join(d, "measure", "orders.jsonl"), encoding="utf-8") as f:
                orders = [json.loads(x) for x in f]
        return json.loads(printed[-1]), events, took, orders

    def test_quotes_start_before_the_choice_ends_and_the_two_overlap(self):
        shops = [dict(shop(n), offers=[{"sku": "H1", "name": "청소기", "qty": 1}]) for n in "abcdef"]
        out, events, took, orders = self.run_main(shops)
        t = {w: ts for w, ts in events}
        quoted = [w for w, _ in events if w.startswith("quote ")]
        self.assertEqual(len(quoted), 5)                              # quote_shops in all, nobody twice
        self.assertEqual(len(set(quoted)), 5)
        early = [ts for w, ts in events if w.startswith("quote ") and ts < t["choose end"]]
        self.assertEqual(len(early), 4)                               # quote_shops - 1 asked while choosing
        self.assertLess(took, 0.5 + 0.5 + 0.3)                        # side by side, not one after the other
        self.assertEqual(out["shop"], "https://c.example")            # the best ask still wins
        self.assertEqual(len(orders[-1]["quotes"]), 5)

    def test_a_hopeless_order_asks_no_shop_and_skips_the_choice(self):
        shops = [dict(shop(n), offers=[{"sku": "H1", "name": "청소기", "qty": 1}]) for n in "abc"]
        out, events, took, orders = self.run_main(shops, hopeless=True)
        self.assertEqual(events, [])                                  # no quote, no harness, no talk
        self.assertIsNone(out["receipt"])
        self.assertTrue(orders[-1]["gave_up"])


if __name__ == "__main__":
    unittest.main()
