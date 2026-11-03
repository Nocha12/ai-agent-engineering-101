"""opening.py: Round 3's first three supply windows, as the GPT Pro review proposed."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import opening
import restock

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = 1800000000.0
CAT = {"F1": {"kind": "food", "category": "식품>과자", "list_price": 12000, "shelf_hours": 720},
       "F2": {"kind": "food", "category": "식품>과자", "list_price": 13000, "shelf_hours": 720},
       "F9": {"kind": "food", "category": "식품>야채", "list_price": 9000, "shelf_hours": 48},
       "G1": {"kind": "goods", "category": "가전>램프", "list_price": 12000},
       "G2": {"kind": "goods", "category": "가구>침대", "list_price": 40000},
       "G3": {"kind": "goods", "category": "가전>TV", "list_price": 5300}}
DEMAND = [{"category": "식품>과자", "median_price": 12000}, {"category": "식품>야채", "median_price": 9000},
          {"category": "가전>램프", "median_price": 12000}, {"category": "가구>침대", "median_price": 344640},
          {"category": "가전>TV", "median_price": 299000}]
CFG = {**opening.DEFAULTS, "phases": [["F1", "G1", "F9", "G2", "G3"], ["F2"], ["G1"]]}


class OpeningTest(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.md)

    def run_it(self, now, units=(), rows=(), restock_info=None, cfg=CFG):
        inv = {"round": 3, "balance": 5000000, "units": [{"sku": s} for s in units]}
        if restock_info:
            inv["restock"] = restock_info
        return opening.lines(self.md, inv, CAT, DEMAND, list(rows), cfg, now=now)

    def test_phase_one_only_what_fits_one_each(self):
        out, why = self.run_it(NOW)
        self.assertEqual(out, [{"sku": "F1", "qty": 1}, {"sku": "G1", "qty": 1}])   # short shelf life, far from the median
        self.assertIn("phase 1", why)

    def test_next_phase_waits_for_arrival_and_the_gap_then_retries_a_missed_window(self):
        self.run_it(NOW)
        self.assertEqual(self.run_it(NOW + 60)[0], [])                 # nothing arrived yet
        self.assertEqual(self.run_it(NOW + 400)[0], [{"sku": "F1", "qty": 1}, {"sku": "G1", "qty": 1}])  # missed: again
        self.assertEqual(self.run_it(NOW + 460, units=["F1"])[0], [])  # arrived, gap not over
        self.assertEqual(self.run_it(NOW + 400 + 16 * 60, units=["F1"])[0], [{"sku": "F2", "qty": 1}])
        self.run_it(NOW + 400 + 17 * 60, units=["F1", "F2"])
        self.assertEqual(self.run_it(NOW + 400 + 40 * 60, units=["F1", "F2"])[0], [{"sku": "G1", "qty": 1}])
        self.run_it(NOW + 400 + 41 * 60, units=["F1", "F2", "G1"])
        self.assertIsNone(self.run_it(NOW + 400 + 60 * 60, units=["F1", "F2", "G1"])[0])

    def test_a_closed_window_buys_nothing_and_does_not_use_up_the_phase(self):
        self.assertEqual(self.run_it(NOW, restock_info={"open": False, "opens_at": NOW + 600})[0], [])
        self.assertEqual(len(self.run_it(NOW + 600, restock_info={"open": True})[0]), 2)

    def test_the_ledger_drops_or_replaces_conditional_candidates(self):
        rows = [{"k": "settle", "sku": "F1"}] * 3 + [{"k": "settle", "sku": "G3"}] * 60
        cand, notes = opening.revise(["F2", "G1"], rows, CAT, {d["category"]: d["median_price"] for d in DEMAND}, CFG)
        self.assertEqual(cand, ["F1"])                                 # F2 -> F1 (sells more, same rules); 램프 sold nothing
        self.assertEqual(len(notes), 2)
        self.assertEqual(opening.revise(["F2"], rows[:10], CAT, {}, CFG)[0], ["F2"])   # too few settles to judge

    def test_the_exploration_budget(self):
        out, why = self.run_it(NOW, cfg={**CFG, "explore_budget_worst": 10000})
        self.assertEqual(out, [{"sku": "F1", "qty": 1}])
        self.assertIn("budget", why)


class ShippedOpeningTest(unittest.TestCase):
    def test_the_review_list_is_what_pricing_json_ships(self):
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            o = json.load(f)["opening"]
        self.assertEqual([len(p) for p in o["phases"]], [10, 10, 4])
        self.assertEqual(o["phases"][0][:2], ["Gdf79cd07", "G2108d9bc"])

    def test_restock_main_sends_the_opening_in_round_three_only(self):
        for rnd, expect in ((3, [{"sku": "F1", "qty": 1}]), (2, None)):
            md = tempfile.mkdtemp()
            try:
                for name, data in (("inventory.json", {"round": rnd, "balance": 5000000, "units": []}), ("catalog.json", CAT),
                                   ("demand.json", DEMAND)):
                    with open(os.path.join(md, name), "w") as f:
                        json.dump(data, f)
                printed = []
                env = {k: v for k, v in os.environ.items() if k != "HARNESS"}
                with mock.patch.object(restock, "read_model", lambda *a: None), \
                        mock.patch.object(restock, "load", lambda p, d, real=restock.load: {"opening": {"phases": [["F1"]]}}
                                          if p == "pricing.json" else real(p, d)), \
                        mock.patch.dict(os.environ, {**env, "AGENT_MEMORY_DIR": os.path.join(md, "mem")}, clear=True), \
                        mock.patch.object(sys, "argv", ["x", "--state", md]), \
                        mock.patch("builtins.print", lambda *a, **k: printed.append(a[0] if a else "")):
                    restock.main()
                out = json.loads(printed[-1])["buy"]
                if expect is None:
                    self.assertFalse(os.path.exists(os.path.join(md, "opening_state.json")))
                else:
                    self.assertEqual(out, expect)
            finally:
                shutil.rmtree(md)


if __name__ == "__main__":
    unittest.main()
