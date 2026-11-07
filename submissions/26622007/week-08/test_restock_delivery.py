"""Delivery evidence survives sale; stock and cash reservations are distinct."""
import json
import os
import tempfile
import unittest
from unittest import mock

import market_model
import restock
from test_profit_rules import CAT, CFG


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = self.tmp.name

    def sent(self, qty=2, at=1000):
        restock.note_sent(self.md, [{"sku": "H1", "qty": qty}], CAT, now=at)

    def supply(self, units, ts=1010, shop="nocha12", seq=1, sold=True):
        row = market_model.compact({"kind": "supply", "seq": seq, "ts": ts,
                                   "data": {"shop": shop, "sku": "H1", "units": units}})
        if row:
            with open(os.path.join(self.md, market_model.ROWS), "a") as f:
                f.write(json.dumps(row) + "\n")
                if sold:
                    for i, uid in enumerate(units):
                        sale = market_model.compact({"kind": "settle", "seq": seq + 100 + i, "ts": ts + 1,
                                                     "data": {"shop": shop, "item": "H1", "unit": uid}})
                        f.write(json.dumps(sale) + "\n")

    def test_supplied_then_sold_never_returns_as_pending_stock(self):
        self.sent()
        self.supply(["u1", "u2"])
        waiting = restock.pending(self.md, {"units": []}, now=1080)
        self.assertEqual(sum(w["qty"] for w in waiting), 0)
        # Inventory/wallet could predate the supply: do not free its cash.
        self.assertEqual(sum(w["worst"] * w.get("cash_qty", w["qty"]) for w in waiting), 30000)

    def test_partial_supply_broken_or_sold_units_still_delivered(self):
        self.sent()
        self.supply(["u1"])
        self.assertEqual(sum(w["qty"] for w in restock.pending(self.md, {"units": []}, now=1080)), 1)

    def test_other_shop_and_unconfirmed_requests_stay_reserved(self):
        self.sent()
        self.supply(["u1", "u2"], shop="other")
        self.assertEqual(restock.pending(self.md, {"units": []}, now=1080)[0]["qty"], 2)

    def test_same_units_do_not_confirm_two_requests_or_count_twice(self):
        self.sent(1)
        self.sent(1, at=1020)
        self.supply(["u1"], ts=1022)
        self.supply(["u1"], ts=1022, seq=2)
        waiting = restock.pending(self.md, {"units": [{"sku": "H1", "unit": "u1", "supplied_at": 1022}]}, now=1080)
        self.assertEqual(sum(w["qty"] for w in waiting), 1)

    def test_cash_reservation_still_blocks_a_purchase_with_stale_wallet(self):
        inv = {"balance": 5000000, "units": []}
        cfg = {**CFG, "keep_cash": 4970000}
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 1}], inv, CAT, cfg,
                                      waiting=[{"sku": "H1", "qty": 0, "cash_qty": 2, "worst": 15000}]), [])

    def test_delivery_without_sale_in_stale_snapshot_keeps_stock_reserved(self):
        self.sent()
        self.supply(["u1", "u2"], sold=False)
        waiting = restock.pending(self.md, {"units": []}, now=1080)
        self.assertEqual(sum(w["qty"] for w in waiting), 2)
        cfg = {**CFG, "max_units_per_sku": 2, "sku_target_units": {"H1": 2}}
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 2}],
                                      {"balance": 5000000, "units": []}, CAT, cfg, waiting=waiting), [])

    def test_old_delivery_cannot_confirm_a_later_request_after_ttl_or_prune(self):
        self.sent(1)
        self.supply(["u1"], ts=1060)
        self.sent(1, at=1062)
        self.assertEqual(sum(w["qty"] for w in restock.pending(self.md, {"units": []}, now=1100)), 1)
        self.assertEqual(sum(w["qty"] for w in restock.pending(self.md, {"units": []}, now=1241)), 1)
        self.sent(1, at=1242)  # note_sent prunes the expired request too
        self.assertEqual(sum(w["qty"] for w in restock.pending(self.md, {"units": []}, now=1243)), 2)

    def test_expired_request_releases_reservation(self):
        self.sent()
        self.assertEqual(restock.pending(self.md, {"units": []}, now=1241), [])


class ForumSchedulingTest(unittest.TestCase):
    def test_only_explicitly_closed_far_window_runs_the_same_forum_callback(self):
        for window, safe in [
            ({"open": True, "closes_at": 1200}, False),
            ({"open": False, "opens_at": 1150}, False),
            ({"open": False, "opens_at": 1151}, True),
            ({"open": False, "opens_at": 999}, False),
            ({}, False),
        ]:
            callback = mock.Mock()
            with self.subTest(window=window), mock.patch.object(restock.forum, "maybe_run", callback):
                restock.forum_off_window("state", {"restock": window}, now=1000)
                self.assertEqual(callback.call_count, int(safe))


if __name__ == "__main__":
    unittest.main()
