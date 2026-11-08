"""Grade warranty experiment through Seller's quote/receipt and measurement seams.

Run with logs/sol-warranty-0438/run_checks.py to isolate all external state.
"""
import copy
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import seller
import shop_memory
from shop_stock import StockBook


# Inventory contract: unit/sku/grade strings, integer list_price/cost,
# nullable expires_at, cert object. No live inventory is consulted.
UNIT = dict(unit="unit-fixture-c", sku="G00000001", name="fixture box",
            category="goods > box", kind="goods", grade="C", cost=7000,
            list_price=10000, expires_at=None, supplied_at=100.0,
            cert={"cert": {"unit": "unit-fixture-c", "grade": "C"}, "sig": "fixture"})


class WarrantyTrialTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / "seller_warranty_trial.json"
        self.market = self.root / "market"
        self.pricing = self.root / "pricing.json"
        self.pricing.write_text(json.dumps({"warranty_hours": 72}))
        env = mock.patch.dict(os.environ, MARKET_DIR=str(self.market),
                              SELLER_WARRANTY_TRIAL_CONFIG=str(self.config))
        env.start()
        self.addCleanup(env.stop)
        pricing = mock.patch.object(shop_memory, "PRICING", str(self.pricing))
        pricing.start()
        self.addCleanup(pricing.stop)
        clock = mock.patch("time.time", return_value=150.0)
        clock.start()
        self.addCleanup(clock.stop)
        self.unit = copy.deepcopy(UNIT)
        self.stock = StockBook(load=lambda: {"round": 3, "units": [self.unit]})
        self.shop = seller.Seller(None, "fixture shop", self.stock)
        self.cfg = dict(study="grade-warranty-v1", version=1, epoch="fixture-1",
                        salt="fixture-salt", enabled=True, share=1.0,
                        start_at=100, stop_at=200, grades=["C"])
        self.write_config()

    def write_config(self, **changes):
        self.cfg.update(changes)
        self.config.write_text(json.dumps(self.cfg))

    def deal(self, **changes):
        return dict(dict(id=self.unit["sku"], unit=self.unit["unit"], kind="goods",
                         name="fixture box", cost=7000, reserve=8000, ask=9000,
                         open=9000, rounds=0, history=[("you asked", 9000)]), **changes)

    def quote(self, deal, text="Please quote G00000001", task="task-fixture"):
        return self.shop.warranty_terms(deal, text, task_id=task, unit=self.unit)

    def rows(self):
        path = self.market / "measure" / "seller_warranty_trial.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_treatment_discloses_as_is_and_receipt_omits_warranty_even_with_pricing_fallback(self):
        deal = self.deal()
        prices = {key: copy.deepcopy(deal[key]) for key in deal}
        note = self.quote(deal)
        self.assertEqual(note, "This grade C unit is sold as is, without a warranty.")
        self.assertNotIn("warranty_hours", self.shop.stock_receipt("task-fixture", deal, 9000))
        self.assertEqual({key: deal[key] for key in prices}, prices)
        self.assertEqual(self.rows()[-1]["arm"], "treatment")

    def test_later_turn_keeps_terms_then_warranty_request_permanently_restores_control(self):
        deal = self.deal()
        self.quote(deal)
        self.write_config(enabled=False)
        self.shop.warranty_terms(deal, "I offer 9000", task_id="task-fixture")
        self.assertNotIn("warranty_hours", self.shop.stock_receipt("task-fixture", deal, 9000))
        note = self.shop.warranty_terms(deal, "Please give a 6 hours warranty", task_id="task-fixture")
        self.assertIn("72 h warranty", note)
        self.assertEqual(self.shop.stock_receipt("task-fixture", deal, 9000)["warranty_hours"], 72)
        row = self.rows()[-1]
        self.assertEqual((row["assigned_arm"], row["arm"], row["reason"], row["offered_hours"]),
                         ("treatment", "control", "followup_warranty_request", 72))
        self.write_config(enabled=True)
        self.shop.warranty_terms(deal, "9000", task_id="task-fixture")
        self.assertEqual(self.rows()[-1]["arm"], "control")

    def test_off_missing_invalid_and_outside_window_keep_baseline(self):
        configurations = [None, "{", "[]", {**self.cfg, "enabled": False},
                          {**self.cfg, "start_at": 151}, {**self.cfg, "stop_at": 150}]
        for field, value in (("enabled", 1), ("version", True), ("version", 0),
                             ("share", -0.1), ("share", 1.1), ("share", float("nan")),
                             ("start_at", None), ("stop_at", None), ("start_at", 201),
                             ("epoch", ""), ("epoch", True), ("salt", ""),
                             ("study", ""), ("grades", []), ("grades", ["Q"]),
                             ("grades", "C")):
            configurations.append({**self.cfg, field: value})
        for cfg in configurations:
            with self.subTest(config=cfg):
                if cfg is None:
                    self.config.unlink(missing_ok=True)
                else:
                    self.config.write_text(cfg if isinstance(cfg, str) else json.dumps(cfg))
                deal = self.deal()
                self.assertIn("72 h warranty", self.quote(deal))
                self.assertEqual(self.shop.stock_receipt("task-fixture", deal, 9000)["warranty_hours"], 72)
                self.assertEqual(self.rows()[-1]["arm"], "control")

    def test_other_grades_requests_existing_terms_and_unknowns_keep_baseline(self):
        for grade in ("A", "B", None, "Q"):
            self.unit["grade"] = grade
            deal = self.deal()
            self.quote(deal)
            self.assertEqual(deal["warranty"], 72)
            self.assertEqual(self.rows()[-1]["reason"], "grade_not_selected")
        self.unit["grade"] = "C"
        for text in ("Warranty please", "guarantee", "보증 부탁", "보장 부탁", "I need 6h", "3 hours", "2시간"):
            deal = self.deal()
            self.quote(deal, text)
            self.assertEqual(deal["warranty"], 72)
            self.assertEqual(self.rows()[-1]["reason"], "buyer_requested")
        deal = self.deal(warranty=96)
        self.quote(deal)
        self.assertEqual(deal["warranty"], 96)
        self.assertEqual(self.rows()[-1]["reason"], "existing_warranty")
        for field in ("unit", "sku", "list_price"):
            old = self.unit[field]
            self.unit[field] = None
            deal = self.deal()
            self.quote(deal)
            self.assertEqual(deal["warranty"], 72)
            self.unit[field] = old
        deal = self.deal(kind="food")
        self.assertIsNone(self.quote(deal))
        self.assertNotIn("warranty_hours", self.shop.stock_receipt("task-fixture", deal, 9000))

    def test_unit_assignment_is_deterministic_across_tasks_and_share_endpoints(self):
        self.write_config(share=0.5)
        arms = []
        for task in ("first-task", "second-task", "first-task"):
            deal = self.deal()
            self.quote(deal, task=task)
            arms.append(self.rows()[-1]["arm"])
        self.assertEqual(len(set(arms)), 1)
        # Known SHA-256 vector starts 394efccc: below the 0.5 threshold.
        self.assertEqual(arms[0], "treatment")
        self.assertEqual(self.rows()[-1]["randomization_unit"], "unit")
        for share, expected in ((0, "control"), (1, "treatment")):
            self.write_config(share=share)
            deal = self.deal()
            self.quote(deal)
            self.assertEqual(self.rows()[-1]["arm"], expected)
            receipt = self.shop.stock_receipt("task-fixture", deal, 9000)
            self.assertEqual(receipt.get("warranty_hours", 0), 72 if share == 0 else 0)

    def test_measurement_exists_before_receipt_and_preserves_join_and_assignment_fields(self):
        deal = self.deal()
        self.quote(deal)
        row = self.rows()[-1]  # already on disk, before constructing a receipt
        self.assertEqual({key: row[key] for key in ("study", "version", "epoch", "ts", "task", "sku", "unit", "grade", "list_price", "offered_hours")},
                         dict(study="grade-warranty-v1", version=1, epoch="fixture-1", ts=150.0,
                              task="task-fixture", sku="G00000001", unit="unit-fixture-c",
                              grade="C", list_price=10000, offered_hours=0))
        receipt = self.shop.stock_receipt("task-fixture", deal, 9000)
        self.assertEqual((receipt["unit"], receipt["claim"], receipt["price"]), (row["unit"], "C", 9000))

    def test_measurement_failure_forces_control_and_logs(self):
        self.market.mkdir()
        (self.market / "measure").write_text("not a directory")
        deal = self.deal()
        with mock.patch.object(seller, "log") as logged:
            self.assertIn("72 h warranty", self.quote(deal))
        self.assertTrue(any("measurement failed" in call.args[0] for call in logged.call_args_list))
        self.assertEqual(self.shop.stock_receipt("task-fixture", deal, 9000)["warranty_hours"], 72)
        self.assertEqual(deal["warranty_trial"]["arm"], "control")

    def test_dynamic_toggle_applies_to_new_talks(self):
        self.write_config(enabled=False)
        first = self.deal()
        self.quote(first)
        self.assertEqual(first["warranty"], 72)
        self.write_config(enabled=True)
        second = self.deal()
        self.assertIn("without a warranty", self.quote(second))
        self.write_config(enabled=False)
        third = self.deal()
        self.quote(third)
        self.assertEqual(third["warranty"], 72)

    def opening(self, text, task="00000000-0000-0000-0000-000000000043"):
        messages = []
        testcase = self

        class Updates:
            async def requires_input(self, message):
                testcase.assertTrue(testcase.rows())
                messages.append(message)

            async def reject(self, message):
                testcase.fail(f"Unexpected rejection: {message}")

        asyncio.run(self.shop.open_from_stock(SimpleNamespace(id=task), text, Updates(), lambda text, ask=None: text))
        return self.shop.open[task], messages[0]

    def test_full_opening_keeps_price_claim_and_cert_and_measurement_precedes_quote(self):
        control, control_text = None, None
        for share in (0, 1):
            self.write_config(share=share)
            deal, text = self.opening("Please quote G00000001")
            receipt = self.shop.stock_receipt("task-fixture", deal, deal["ask"])
            if share == 0:
                control = {key: copy.deepcopy(deal[key]) for key in ("id", "unit", "cost", "reserve", "ask", "open", "break", "expires_at", "history")}
                control_text = text
                self.assertEqual(receipt["warranty_hours"], 72)
                self.shop.free("00000000-0000-0000-0000-000000000043", deal)
                self.shop.open.clear()
            else:
                self.assertEqual({key: deal[key] for key in control}, control)
                self.assertEqual(receipt["claim"], "C")
                self.assertNotIn("warranty_hours", receipt)
                self.assertIn("without a warranty", text)
                self.assertEqual(text.replace("This grade C unit is sold as is, without a warranty.", "TERMS"),
                                 control_text.replace("This unit comes with a 72 h warranty: if it breaks within 72 h, you get the full price back.", "TERMS"))
        self.assertEqual(self.unit["cert"], UNIT["cert"])

    def test_warranty_request_after_message_limit_still_keeps_baseline(self):
        deal, text = self.opening("G00000001 " + "x" * 510 + " Please include a warranty.")
        self.assertIn("72 h warranty", text)
        self.assertEqual(self.shop.stock_receipt("task-fixture", deal, deal["ask"])["warranty_hours"], 72)

    def test_collected_settle_fixture_and_no_warranty_receipt_join_by_string_unit(self):
        # Actual captured settle schema, anonymized without changing field types.
        # This is a join-contract check, not a claim that a live settlement ran.
        fixture = json.loads((Path(__file__).parent / "logs" / "sol-warranty-0438" / "settle_fixture.json").read_text())
        compact = seller.market_model.compact(fixture)
        deal = self.deal()
        self.quote(deal)
        receipt = self.shop.stock_receipt("task-fixture", deal, 9000)
        self.assertEqual((compact["unit"], receipt["unit"], self.rows()[-1]["unit"]),
                         ("unit-fixture-c", "unit-fixture-c", "unit-fixture-c"))
        self.assertIsInstance(compact["warranty"], float)
        self.assertNotIn("warranty_hours", receipt)


if __name__ == "__main__":
    unittest.main()
