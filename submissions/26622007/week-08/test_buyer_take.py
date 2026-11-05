"""buyer.talk take_at: a metadata ask within it is offered back at once, with no harness call; otherwise the talk goes on."""
import unittest
from unittest import mock

import buyer


def task(state, ask=None, receipt=None):
    t = {"id": "t-1", "status": {"state": state, "message": {"parts": [{"text": f"asking {ask}"}],
                                                          **({"metadata": {"ask": ask}} if ask is not None else {})}}}
    if receipt:
        t["artifacts"] = [{"parts": [{"data": receipt}]}]
    return t


class TakeAt(unittest.TestCase):
    def test_ask_within_is_taken_without_the_harness(self):
        sent = []

        def send(url, text, task_id=None):
            sent.append(text)
            return task("TASK_STATE_COMPLETED", receipt={"price": int(text), "item": "G1"})
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", side_effect=AssertionError("no harness")):
            out = buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", 9000), "김치", 10000, "", {"history": [], "agreed": None, "holds": 0},
                             take_at=10000)
        self.assertEqual(sent, ["9000"])
        self.assertEqual(out["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(out["agreed"], 9000)
        self.assertTrue(out["took"])

    def test_ask_over_take_at_goes_to_the_harness(self):
        calls = []

        def decide(*a, **k):
            calls.append(a)
            return {"shop_price": 12000, "action": "walk_away", "price": 0}
        with mock.patch.object(buyer, "send", lambda *a, **k: None), mock.patch.object(buyer, "decide", decide), \
                mock.patch.object(buyer, "rpc", lambda *a: task("TASK_STATE_CANCELED")):
            out = buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", 12000), "김치", 10000, "", {"history": [], "agreed": None, "holds": 0},
                             take_at=10000)
        self.assertEqual(len(calls), 1)
        self.assertFalse(out.get("took"))

    def test_take_at_never_above_budget(self):
        with mock.patch.object(buyer, "send", lambda *a, **k: None), \
                mock.patch.object(buyer, "decide", return_value={"shop_price": 10500, "action": "walk_away", "price": 0}), \
                mock.patch.object(buyer, "rpc", lambda *a: task("TASK_STATE_CANCELED")):
            out = buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", 10500), "김치", 10000, "", {"history": [], "agreed": None, "holds": 0},
                             take_at=11000)
        self.assertFalse(out.get("took"))

    def test_no_metadata_goes_to_the_harness(self):
        with mock.patch.object(buyer, "send", lambda *a, **k: None), \
                mock.patch.object(buyer, "decide", return_value={"shop_price": 9000, "action": "walk_away", "price": 0}) as d, \
                mock.patch.object(buyer, "rpc", lambda *a: task("TASK_STATE_CANCELED")):
            t = task("TASK_STATE_INPUT_REQUIRED")
            t["status"]["message"]["parts"] = [{"text": "asking 9000"}]
            buyer.talk("u", t, "김치", 10000, "", {"history": [], "agreed": None, "holds": 0}, take_at=10000)
        self.assertEqual(d.call_count, 1)

    def test_same_ask_after_taking_it_goes_to_the_harness(self):
        sent = []

        def send(url, text, task_id=None):
            sent.append(text)
            return task("TASK_STATE_INPUT_REQUIRED", 9000)
        with mock.patch.object(buyer, "send", send), \
                mock.patch.object(buyer, "decide", return_value={"shop_price": 9000, "action": "walk_away", "price": 0}) as d, \
                mock.patch.object(buyer, "rpc", lambda *a: task("TASK_STATE_CANCELED")):
            out = buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", 9000), "김치", 10000, "", {"history": [], "agreed": None, "holds": 0},
                             take_at=10000)
        self.assertEqual(sent, ["9000"])
        self.assertEqual(d.call_count, 1)
        self.assertIsNone(out["agreed"])
        self.assertEqual(out["history"][:2], [("shop asked", 9000), ("you offered", 9000)])

    def test_lower_ask_after_taking_is_taken_again(self):
        sent, answers = [], [task("TASK_STATE_INPUT_REQUIRED", 8500), task("TASK_STATE_COMPLETED", receipt={"price": 8500})]

        def send(url, text, task_id=None):
            sent.append(text)
            return answers.pop(0)
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", side_effect=AssertionError("no harness")):
            out = buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", 9000), "김치", 10000, "", {"history": [], "agreed": None, "holds": 0},
                             take_at=10000)
        self.assertEqual(sent, ["9000", "8500"])
        self.assertEqual(out["agreed"], 8500)


class MarketBuyerPassesTakeAtForFoodOnly(unittest.TestCase):
    def test_source_sets_take_at_only_for_food(self):
        import inspect
        import market_buyer
        src = inspect.getsource(market_buyer.main)
        self.assertIn('take_at=min(limit, int(offer["list_price"] * FOOD_TAKE_SHARE))', src)
        self.assertIn('if r3 and FOOD_TAKE_ASK and order.get("kind") == "food" else None)', src)
        self.assertIn('limit = min(value_limit(offer, args.budget, chance, buyer_cfg()), saved_cap(offer, chance)) if r3', src)


class SavedCap(unittest.TestCase):
    """Patch 3.4 Saved: a deal must be expected to keep SAVED_MIN_SHARE of list; food is taken at once only at FOOD_TAKE_SHARE."""

    def test_food_cap_is_75_percent_of_list(self):
        import market_buyer as mb
        self.assertEqual(mb.SAVED_MIN_SHARE, 0.25)
        self.assertEqual(mb.saved_cap({"list_price": 10000}, None), 7500)
        self.assertEqual(mb.saved_cap({"list_price": 10000}, 0), 7500)

    def test_goods_cap_counts_the_refunded_breaks(self):
        import market_buyer as mb
        self.assertEqual(mb.saved_cap({"list_price": 100000}, 0.10), 72222)   # (1 - 0.1)(1 - p/L) >= 0.25
        self.assertEqual(mb.saved_cap({"list_price": 100000}, 0.75), 0)
        self.assertEqual(mb.saved_cap({"list_price": 100000}, 1.0), 0)

    def test_food_take_share_is_below_the_cap(self):
        import market_buyer as mb
        self.assertEqual(mb.FOOD_TAKE_SHARE, 0.70)
        self.assertLess(mb.FOOD_TAKE_SHARE, 1 - mb.SAVED_MIN_SHARE)

    def test_best_quote_ranks_by_share_kept_not_money(self):
        import market_buyer as mb
        cheap = {"name": "a", "usable": [{"sku": "S1", "name": "snack", "qty": 1, "list_price": 10000}]}
        dear = {"name": "b", "usable": [{"sku": "S2", "name": "sofa", "qty": 1, "list_price": 1000000}]}
        asks = {"a": 6000, "b": 740000}     # a keeps 40% (4,000), b keeps 26% (260,000)

        def quote(s, name, sku, order, got, ctl):
            got[s["name"]] = {"ask": asks[s["name"]], "url": "u", "task": "t"}
        with mock.patch.object(mb, "quote", quote), mock.patch.object(mb, "cancel_quote", lambda *a: None), \
                mock.patch.object(mb, "log", lambda *a: None):
            best, got = mb.best_quote(dear, [cheap], {}, {}, 10 ** 7, lambda sku: None, {"quote_shops": 2}, wait=1)
        self.assertEqual(best["name"], "a")


if __name__ == "__main__":
    unittest.main()
