"""Real selection/dispatch/quote flow; only external transport is deterministic."""
import collections
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
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
            return Mock(status_code=200, json=lambda: {"supportedInterfaces": [{"protocolBinding": "JSONRPC",
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
            stack.enter_context(patch.object(buyer, "buyer_send", side_effect=send))
            stack.enter_context(patch.object(buyer, "buyer_rpc", side_effect=lambda u, m, p: canceled.append(p["id"])))
            order = {"order": "00000000-0000-4000-8000-000000000001", "kind": "goods",
                     "_buyer_started_at": now, "_stock_phase": "prefetch"}
            order["_fresh_rotation"] = rotation.reserve(md, order, rule_engine=True)
            order["_fresh_rotation"]["arm"] = arm
            existing = set(threading.enumerate())
            try:
                yield shops, order, md, sent, canceled
            finally:
                # Drain fixture workers before restoring transport patches.
                for th in set(threading.enumerate()) - existing:
                    th.join(2)
                    self.assertFalse(th.is_alive(), "fixture worker did not finish")

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

    def test_persisted_b_ctx_cannot_dispatch_with_nonrule_engine(self):
        for engine in ("agent", "invalid"):
            with self.subTest(engine=engine), self.flow("B") as f:
                shops, order, md, sent, _ = f
                # A restart loads an existing B allocation; no persisted engine
                # field is authority for this process's actual chooser.
                order = json.loads(json.dumps(order))
                with patch.dict(os.environ, {"BUYER_CHOOSE_ENGINE": engine}):
                    full, pre = self.prefetch(shops, order, md)
                    self.assertEqual(pre["asked"], {"a"})
                    self.assertEqual(set(sent), {"a"})
                    self.assertNotIn("selection_asked", pre)
                    for enough in (False, True):
                        pre["enough"] = lambda: enough
                        with patch.object(buyer, "choose", return_value=(shops[1], "agent fixture")) as choose:
                            if enough:
                                with self.assertRaises(buyer.HarnessError):
                                    buyer.choose_racing(shops, {"trades": []}, "item", 20000, pre)
                                choose.assert_not_called()
                            else:
                                self.assertEqual(buyer.choose_racing(shops, {"trades": []}, "item", 20000, pre)[0], shops[1])
                                choose.assert_called_once()
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

    def test_chosen_matches_treatment_prefetch_top_up_without_duplicate(self):
        with self.flow("B") as f:
            shops, order, md, sent, _ = f
            full, pre = self.prefetch(shops, order, md)
            order["_stock_phase"] = "best_quote"
            best, got = buyer.best_quote(shops[2], full[:2], order, {}, 20000, lambda _: 0,
                {"quote_shops": 2}, wait=.2, pre=pre)
            self.assertEqual(collections.Counter(sent), collections.Counter("ac"))
            self.assertEqual(best["name"], "c")
            self.assertTrue(buyer.resumable(got["c"]))
            self.assertFalse(buyer.resumable(got["a"]))
            # Continue the actual winning task, not another opening request.
            import buyer as negotiation
            resumed = []
            def rpc(url, method, params):
                resumed.append(params["message"]["taskId"])
                return {"id": "t-c", "status": {"state": "TASK_STATE_REJECTED"}}
            with patch.object(negotiation, "rpc", side_effect=rpc):
                result = buyer.negotiate(best["url"], "item", 7000,
                    resume=(got["c"]["url"], got["c"]["task"]), take_at=7000)
            self.assertEqual(resumed, ["t-c"])
            self.assertEqual(result["state"], "TASK_STATE_REJECTED")

    def test_fit_and_rival_partitions_keep_selection_and_actual_membership_distinct(self):
        with self.flow("B", names="abcdef") as f:
            shops, order, md, sent, _ = f
            # Genuine recent measure schema: a is fitted, d/e are rivals.
            os.mkdir(os.path.join(md, "measure"))
            with open(os.path.join(md, "measure", "orders.jsonl"), "w") as out:
                out.write(json.dumps({"ts": time.time(), "category": "fixture", "budget": 20000,
                    "quotes": [{"shop": "a", "ask": 6500, "list_price": 10000, "limit": 7000}]}) + "\n")
            early, full = buyer.prefetch_pool(shops, {"trades": []}, md, "fixture", 3,
                {"d", "e"}, kind="goods", order=order, budget=20000)
            pre = {"got": {}, "ctl": buyer.quote_ctl()}
            pre["threads"] = buyer.start_quotes(early, order, 20000, lambda _: 0, pre["got"], pre["ctl"])
            pre["asked"] = set(pre["threads"])
            pre["selection_asked"] = order["_fresh_selection_asked"]
            for th in pre["threads"].values():
                th.join(1)
            self.assertEqual(pre["selection_asked"], {"a", "b"})
            self.assertEqual(pre["asked"], {"a", "f"})
            self.assertEqual([s["name"] for s in full], list("abcfde"))
            chosen, _ = buyer.choose_racing(shops[:3] + shops[5:], {"trades": []}, "item", 20000, pre)
            self.assertEqual(chosen["name"], "c")
            buyer.close_prefetch(pre)

    def test_late_quote_after_cancel_closes_and_cannot_resume(self):
        for close_early in (False, True):
            with self.subTest(cancel=close_early), self.flow("B") as f:
                shops, order, md, sent, canceled = f
                entered, release, closed = threading.Event(), threading.Event(), threading.Event()
                original_send, original_rpc = buyer.buyer_send, buyer.buyer_rpc
                def delayed(url, text):
                    if "c.example" in url:
                        entered.set()
                        if not release.wait(2):
                            raise RuntimeError("fixture release missing")
                    return original_send(url, text)
                def rpc(url, method, params):
                    result = original_rpc(url, method, params)
                    if params["id"] == "t-c":
                        closed.set()
                    return result
                with patch.object(buyer, "buyer_send", side_effect=delayed), patch.object(buyer, "buyer_rpc", side_effect=rpc):
                    early, full = buyer.prefetch_pool(shops, {"trades": []}, md, None, 2,
                        kind="goods", order=order, budget=20000)
                    pre = {"got": {}, "ctl": buyer.quote_ctl()}
                    pre["threads"] = buyer.start_quotes(early, order, 20000, lambda _: 0, pre["got"], pre["ctl"])
                    self.assertTrue(entered.wait(1))
                    try:
                        if close_early:
                            buyer.close_prefetch(pre)
                        else:
                            order["_stock_phase"] = "best_quote"
                            buyer.best_quote(shops[1], [s for s in full if s is not shops[1]], order, {},
                                20000, lambda _: 0, {"quote_shops": 2}, wait=.01, pre=pre)
                    finally:
                        release.set()
                        for th in pre["threads"].values():
                            th.join(1)
                    self.assertTrue(closed.wait(1))
                    self.assertFalse(buyer.resumable(pre["got"]["c"]))
                    self.assertEqual(canceled.count("t-c"), 1)

    def test_main_real_rule_and_acp_fallback_preserve_first_choice(self):
        self.main_engine_case("rule")

    def test_main_nonrule_override_rejects_fresh_assignment(self):
        for engine in ("agent", "invalid"):
            with self.subTest(engine=engine):
                self.main_engine_case(engine)

    def main_engine_case(self, engine):
        # Keep shipped k and all selection/quote functions. Only HTTP/RPC and
        # temporary input files differ from the product invocation.
        for acp_missing in (None, "j"):
            results = []
            for arm in ("A", "B"):
                with self.subTest(arm=arm, acp_missing=acp_missing), self.flow(arm, names="abcdefghijkl",
                        asks={n: 9900 for n in "abcdefghijkl"}) as f:
                    shops, order, md, sent, _ = f
                    # Source fixture study determines a real sticky arm with a
                    # replacement outside the nine-shop baseline prefetch.
                    for i in range(1, 500):
                        oid = f"00000000-0000-4000-8000-{i:012d}"
                        candidate = dict(order, order=oid)
                        ctx = rotation.reserve(md, candidate, rule_engine=True)
                        slot = int.from_bytes(hashlib.sha256(oid.encode()).digest(), "big") % 12
                        if ctx["arm"] == arm and slot >= 9:
                            order = candidate
                            break
                    else:
                        self.fail("no bounded fixture allocation")
                    order["acceptable"] = ["sku-a"]
                    for s in shops:
                        s["offers"] = [{"sku": "sku-a", "name": "item", "qty": 1}]
                    for filename, obj in (("shops.json", shops), ("order.json", order),
                            ("catalog.json", {"sku-a": {"list_price": 10000, "kind": "goods"}})):
                        with open(os.path.join(md, filename), "w") as out:
                            json.dump(obj, out)
                    def http(url, **kwargs):
                        if url.endswith("/api/stats"):
                            return Mock(json=lambda: {"round": 3})
                        if url.endswith("/acp.json"):
                            return Mock(status_code=404 if acp_missing and f"//{acp_missing}." in url else 200)
                        return Mock(json=lambda: {"supportedInterfaces": [{"protocolBinding": "JSONRPC",
                            "protocolVersion": "1.0", "url": url.split("/.well-known")[0] + "/a2a"}]})
                    def ask(prompt, schema, system, **kwargs):
                        final_schema = schema["properties"]["final"]["anyOf"][0]
                        allowed = final_schema["properties"]["shop"]["enum"]
                        final = {"shop": "j" if "j" in allowed else "k", "reason": "fixture"}
                        return {"action": "finish", "reason": "fixture", "evidence": ["c0"],
                                "tool": "", "args_json": "{}", "final": final}
                    with patch.dict(os.environ, {"BUYER_CHOOSE_ENGINE": engine}), \
                            patch.object(buyer, "ask", side_effect=ask), \
                            patch.object(buyer, "MEMORY", os.path.join(md, "memory.json")), \
                            patch.object(buyer.httpx, "get", side_effect=http), \
                            patch.object(sys, "argv", ["buyer", "--shops", os.path.join(md, "shops.json"),
                                "--order", os.path.join(md, "order.json"), "--item", "item", "--budget", "20000"]), \
                            patch("builtins.print"):
                        self.assertEqual(buyer.main(), 0)
                    with open(os.path.join(md, "measure", "orders.jsonl")) as out:
                        row = json.loads(out.readlines()[-1])
                    attempts = [json.loads(p.read_text()) for p in Path(ctx["directory"]).glob("attempt-*.json")]
                    prefetched = {a["shop"] for a in attempts if a["phase"] == "prefetch"}
                    if engine == "rule":
                        self.assertEqual(len(prefetched), 9)
                        self.assertEqual(prefetched, set("abcdefghi") if arm == "A" else
                            (set("abcdefghi") - {"a"}) | {"abcdefghijkl"[slot]})
                    else:
                        self.assertIsNone(row["fresh_rotation"]["arm"])
                        self.assertEqual(prefetched, set())
                        self.assertEqual(set(sent[:9]), set("abcdefghi"))
                    results.append(row["first_choice"])
                    self.assertEqual(len(sent), len(set(sent)))
            self.assertEqual(results, ["j", "j"] if acp_missing is None else ["k", "k"])


if __name__ == "__main__":
    unittest.main()
