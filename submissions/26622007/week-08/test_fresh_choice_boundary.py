"""Real selection/dispatch/quote flow; only external transport is deterministic."""
import collections
import contextlib
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import buyer_fresh_rotation as rotation
import market_buyer as buyer


class FreshChoiceBoundary(unittest.TestCase):
    @contextlib.contextmanager
    def flow(self, arm, names="abc", *, different_sku=False, asks=None):
        shops = [{"name": n, "url": f"https://{n}.example", "reviews": [],
                  "usable": [{"sku": "sku-" + (n if different_sku else "a"),
                              "name": "item", "list_price": 10000}]} for n in names]
        sent, canceled = [], []
        def card(url, **kwargs):
            return Mock(json=lambda: {"supportedInterfaces": [{"protocolBinding": "JSONRPC",
                "protocolVersion": "1.0", "url": url.split("/.well-known")[0] + "/a2a"}]})
        def send(url, text):
            n = url.split("//")[1].split(".")[0]
            sent.append(n)
            return {"id": "t-" + n, "status": {"state": "TASK_STATE_INPUT_REQUIRED",
                    "message": {"role": "agent", "parts": [],
                                "metadata": {"ask": (asks or {}).get(n, 6500)}}}}
        with tempfile.TemporaryDirectory() as md, contextlib.ExitStack() as stack:
            now = time.time()
            stack.enter_context(patch.multiple(rotation, ENABLED=True, START=now-1, END=now+120))
            stack.enter_context(patch.dict("os.environ", {"BUYER_CHOOSE_ENGINE": "rule"}))
            stack.enter_context(patch.object(buyer, "STARTED", now))
            stack.enter_context(patch.object(buyer.httpx, "get", side_effect=card))
            stack.enter_context(patch.object(buyer, "serves_acp", return_value=True))
            stack.enter_context(patch.object(buyer, "buyer_send", side_effect=send))
            stack.enter_context(patch.object(buyer, "buyer_rpc", side_effect=lambda u, m, p: canceled.append(p["id"])))
            order = {"order": "00000000-0000-4000-8000-000000000001", "kind": "goods",
                     "_buyer_started_at": now, "_stock_phase": "prefetch"}
            order["_fresh_rotation"] = rotation.reserve(md, order)
            order["_fresh_rotation"]["arm"] = arm
            yield shops, order, md, sent, canceled

    def prefetch(self, shops, order, md, k=2, rival=frozenset()):
        early, full = buyer.prefetch_pool(shops, {"trades": []}, md, None, k,
            rival, kind="goods", order=order, budget=20000)
        pre = {"got": {}, "ctl": buyer.quote_ctl()}
        pre["threads"] = buyer.start_quotes(early, order, 20000, lambda _: 0, pre["got"], pre["ctl"])
        pre["asked"] = set(pre["threads"])
        # Same consumer contract as main; no invented actual dispatch membership.
        if "_fresh_selection_asked" in order:
            pre["selection_asked"] = order["_fresh_selection_asked"]
        for thread in pre["threads"].values():
            thread.join(1)
        return full, pre

    def test_rotation_changes_dispatch_but_keeps_first_choice_and_baseline_pool(self):
        for different in (False, True):
            for arm, actual in (("A", {"a"}), ("B", {"c"})):
                with self.subTest(different=different, arm=arm), self.flow(arm, different_sku=different) as f:
                    shops, order, md, sent, _ = f
                    full, pre = self.prefetch(shops, order, md)
                    chosen, _ = buyer.choose_racing(shops, {"trades": []}, "item", 20000, pre)
                    self.assertEqual(pre["asked"], actual)
                    self.assertEqual(set(sent), actual)
                    self.assertEqual(chosen["name"], "b")
                    self.assertEqual([s["name"] for s in full], list("abc"))
                    buyer.close_prefetch(pre)

    def test_downstream_ties_keep_baseline_preference_after_actual_prefetch(self):
        with self.flow("B", asks={"a": 7500, "b": 9000, "c": 7500}) as f:
            shops, order, md, sent, _ = f
            full, pre = self.prefetch(shops, order, md)
            chosen, _ = buyer.choose_racing(shops, {"trades": []}, "item", 20000, pre)
            order["_stock_phase"] = "best_quote"
            # No first-batch ask fits, so the canonical next shop is asked.
            # Equal surplus must use baseline b,a,c preference, not c,b,a arrival.
            best, got = buyer.best_quote(chosen, [s for s in full if s is not chosen], order, {}, 20000,
                lambda _: 0, {"quote_shops": 2}, wait=.2, rounds=2, pre=pre)
            self.assertEqual(best["name"], "a")
            self.assertEqual(collections.Counter(sent), collections.Counter("abc"))
            self.assertTrue(buyer.resumable(got["a"]))
            self.assertFalse(buyer.resumable(got["c"]))


if __name__ == "__main__":
    unittest.main()
