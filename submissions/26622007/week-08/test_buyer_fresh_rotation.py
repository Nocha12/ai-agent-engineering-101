"""Synthetic, anonymized boundary fixtures; no historical opportunity claims."""
import collections
import tempfile
import threading
import unittest
from unittest.mock import patch

import buyer_fresh_rotation as r


class Rotation(unittest.TestCase):
    def test_dispatch_off_and_treatment_keep_original_objects_and_quota(self):
        import market_buyer as mb
        shops = [{"name": n, "usable": [{"name": "item", "sku": "sku-a", "list_price": 100}]} for n in "abc"]
        fits = collections.defaultdict(int)
        with tempfile.TemporaryDirectory() as md, patch.multiple(r, ENABLED=True, START=100, END=200), \
                patch.object(mb, "quote", return_value=None), patch.object(r.time, "time", return_value=102):
            ctx = r.reserve(md, self.order(), now=102)
            ctx["arm"] = "B"
            ordered = list(shops)
            order = {**self.order(), "_fresh_rotation": ctx, "_stock_phase": "prefetch",
                     "_fresh_inputs": (list(shops), fits, set(), 2, ordered)}
            batch = shops[:1]
            started = mb.start_quotes(batch, order, 100, lambda sku: 0, {}, mb.quote_ctl())
            self.assertEqual(list(started), ["c"])
            self.assertIs(batch[0], shops[2])
            self.assertEqual(len(batch), 1)
            for t in started.values():
                t.join()
            # END/off is baseline even with a retained context.
            order.pop("_fresh_dispatched")
            order["_fresh_inputs"] = (list(shops), fits, set(), 2, ordered)
            with patch.object(r, "ENABLED", False):
                batch = shops[:1]
                started = mb.start_quotes(batch, order, 100, lambda sku: 0, {}, mb.quote_ctl())
                self.assertEqual(list(started), ["a"])
                for t in started.values():
                    t.join()

    def order(self):
        return {"order": "o-0123456789ab", "kind": "goods", "_buyer_started_at": 101}

    def test_retry_off_end_unknown_and_conflict(self):
        with tempfile.TemporaryDirectory() as md, patch.multiple(r, ENABLED=True, START=100, END=200):
            a = r.reserve(md, self.order(), now=102)
            b = r.reserve(md, self.order(), now=103)
            self.assertEqual(a, b)
            self.assertIn(a["arm"], "AB")
            self.assertIsNone(r.reserve(md, self.order(), now=200))
            self.assertIsNone(r.reserve(md, self.order(), now=99))
            self.assertIsNone(r.reserve(md, self.order(), now=102, conflict=True))
            for extra in ({"order": "unknown"}, {"_buyer_started_at": 103},
                          {"state": "REJECTED"}, {"resume": True}, {"kind": "food"}):
                self.assertIsNone(r.reserve(md, {**self.order(), **extra}, now=102))
            with patch.object(r, "ENABLED", False):
                self.assertIsNone(r.reserve(md, self.order(), now=102))

    def test_failed_io_and_thread_start_return_baseline(self):
        with tempfile.TemporaryDirectory() as md, patch.multiple(r, ENABLED=True, START=100, END=200):
            with patch("buyer_stock_evidence.os.link", side_effect=OSError):
                self.assertIsNone(r.reserve(md, self.order(), now=102))
            # Failed starts must release admission; more failures than slots.
            for _ in range(8):
                with patch("buyer_stock_evidence.threading.Thread.start", side_effect=RuntimeError):
                    self.assertIsNone(r.reserve(md, self.order(), now=102))
            self.assertIsNotNone(r.reserve(md, self.order(), now=102))

    def test_timeout_cannot_authorize_late_treatment(self):
        import buyer_stock_evidence as io
        released, entered, finished = threading.Event(), threading.Event(), threading.Event()
        original = io._once
        def stalled(path, row):
            entered.set()
            released.wait(2)
            try:
                return original(path, row)
            finally:
                finished.set()
        with tempfile.TemporaryDirectory() as md, patch.multiple(r, ENABLED=True, START=100, END=200):
            ctx = r.reserve(md, self.order(), now=102)
            ctx["arm"] = "B"
            shops = [{"name": n} for n in "abc"]
            p = r.plan(shops, collections.defaultdict(int), set(), k=2, order_id=self.order()["order"])
            with patch.object(r, "_once", side_effect=stalled):
                self.assertEqual(r.decide(ctx, p, now=102), p["baseline"])
                self.assertTrue(entered.is_set())
                released.set()
                self.assertTrue(finished.wait(2))
            self.assertEqual(r.decide(ctx, p, now=200), p["baseline"])

    def test_single_swap_preserves_partitions_and_detects_membership(self):
        shops = [{"name": n, "reviews": [], "usable": [{"sku": "sku-a"}]} for n in "abcdef"]
        fits = collections.defaultdict(int, a=2, f=1)
        p = r.plan(shops, fits, {"d", "e"}, k=3, order_id="o-0123456789ab")
        self.assertEqual(p["baseline"], list("abcdef"))
        self.assertEqual(p["treatment"], list("acbdef"))
        self.assertTrue(p["eligible"])
        self.assertEqual(p["partitions"], [[True, False], [False, False], [False, False],
                                             [False, True], [False, True], [True, False]])
        self.assertFalse(r.plan(shops, fits, {"d", "e"}, k=6,
                                order_id="o-0123456789ab")["eligible"])


if __name__ == "__main__":
    unittest.main()
