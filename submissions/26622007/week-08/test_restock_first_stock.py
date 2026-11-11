"""Behavior checks for the finite first-stock supply allocation."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import restock
import restock_first_stock as trial


class FirstStockTest(unittest.TestCase):
    def setUp(self):
        self.inv = {"restock": {"opens_at": 120000, "closes_at": 120180}}
        self.catalog = {trial.SKU: {"kind": "food", "shelf_hours": 48}}
        self.model = {"active": True, "skus": {trial.SKU: {"mine_rate": 0, "deals": 20}}}
        self.switch = {"enabled": True, "study": "first-stock-test", "start": 119950,
                       "stop": 123550, "b_parity": 0}

    def config(self, switch=None, **kwargs):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / trial.SWITCH).write_text(json.dumps(self.switch if switch is None else switch))
            return trial.config(td, self.inv, kwargs.get("model", self.model), self.catalog,
                                kwargs.get("listed", []), now=kwargs.get("now", 120001))

    def test_finite_eligibility_and_arm_boundaries(self):
        self.assertEqual(self.config()["arm"], "B")
        self.assertEqual(self.config({**self.switch, "b_parity": 1})["arm"], "A")
        for now in (119949, 120180, 123550):
            self.assertIsNone(self.config(now=now))
        self.assertIsNone(self.config(listed=[trial.SKU]))
        self.assertIsNone(self.config(model={"active": True, "skus": {trial.SKU: {"mine_rate": 1, "deals": 20}}}))
        self.assertIsNone(self.config(model={"active": True, "skus": {trial.SKU: {"deals": 20}}}))
        self.assertIsNone(self.config({**self.switch, "stop": 999999}))

    def test_existing_guard_counts_pending_and_duplicate_lines_before_cash(self):
        catalog = {trial.SKU: {"kind": "food", "list_price": 100, "shelf_hours": 48}}
        inv = {"balance": 10**9, "units": []}
        cfg = {**restock.DEFAULTS, "first_stock": {"arm": "B"}, "keep_cash": 0,
               "cash_buffer": 0, "food_max_stock_cost": 10**8, "max_stock_cost": 10**8}
        rows = [{"sku": trial.SKU, "qty": 3}, {"sku": trial.SKU, "qty": 5}]
        self.assertEqual(restock.guard(rows, inv, catalog, cfg), [{"sku": trial.SKU, "qty": 1}])
        waiting = [{"sku": trial.SKU, "qty": 1, "worst": 75}]
        self.assertEqual(restock.guard(rows, inv, catalog, cfg, waiting=waiting), [])
        self.assertEqual(restock.guard(rows, {**inv, "balance": 0}, catalog, cfg), [])
        # Off/A keep the original guard behavior and all original caps.
        self.assertEqual(restock.guard(rows[:1], inv, catalog, {**cfg, "first_stock": {"arm": "A"}}), rows[:1])
        self.assertEqual(restock.guard(rows, inv, catalog, {**cfg, "skip_skus": [trial.SKU]}), [])

    def test_absent_or_malformed_switch_does_not_change_supply(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(trial.config(td, self.inv, self.model, self.catalog, [], now=120001))
            (Path(td) / trial.SWITCH).write_text("broken")
            self.assertIsNone(trial.config(td, self.inv, self.model, self.catalog, [], now=120001))
        self.assertEqual(trial.quantity(None, trial.SKU, 3, 0), 3)
        self.assertEqual(trial.quantity({"arm": "B"}, "other", 3, 0), 3)
