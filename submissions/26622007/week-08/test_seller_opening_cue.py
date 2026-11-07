"""Public message seam: wording changes while price/terms remain identical."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

class OpeningCueTests(unittest.TestCase):
    def test_new_adopted_quote_changes_only_the_text_not_prices_or_terms(self):
        from seller_opening_cue import OpeningCue
        with tempfile.TemporaryDirectory() as root:
            cue = OpeningCue(root)
            cue.root.mkdir()
            (cue.root / "adopted").touch()
            deal = dict(id="sku", unit="u", kind="goods", cost=8000, reserve=9000,
                        open=11000, ask=10000, rounds=0, warranty=72,
                        history=[("you asked", 10000)])
            before = copy.deepcopy(deal)
            result = cue.message("00000000-0000-0000-0000-000000000001", deal,
                                 "Asking 10000.", now=101)
            self.assertEqual(result, "Asking 10000. Please send your offer as a number.")
            self.assertEqual({k: deal[k] for k in before}, before)

    def test_full_uuid_choice_is_durable_across_adoption_and_disable(self):
        from seller_opening_cue import OpeningCue
        def deal():
            return dict(id="sku", unit="u", kind="goods", cost=8000, reserve=9000,
                        open=11000, ask=10000, rounds=0, history=[("you asked", 10000)])
        first = "00000000-0000-0000-0000-000000000001"
        second = "00000000-0000-0000-0000-000000000002"
        with tempfile.TemporaryDirectory() as root:
            cue = OpeningCue(root)
            cue.root.mkdir()
            self.assertEqual(cue.message(first, deal(), "Asking 10000.", 101), "Asking 10000.")
            (cue.root / "adopted").touch()
            self.assertEqual(OpeningCue(root).message(first, deal(), "Asking 10000.", 102),
                             "Asking 10000.")
            treated = "Asking 10000. Please send your offer as a number."
            self.assertEqual(cue.message(second, deal(), "Asking 10000.", 103), treated)
            (cue.root / "disabled").touch()
            self.assertEqual(OpeningCue(root).message(second, deal(), "Asking 10000.", 104), treated)
            self.assertEqual(cue.message("new-control", deal(), "Asking 10000.", 105), "Asking 10000.")

    def test_active_pilot_balances_two_same_stratum_quotes_without_changing_ask(self):
        from seller_opening_cue import OpeningCue
        with tempfile.TemporaryDirectory() as root:
            cue = OpeningCue(root)
            cue.root.mkdir()
            (cue.root / "seller_concession_trial.json").write_text(
                json.dumps(dict(enabled=True, id="cue-pilot", seed=7, start=100, end=1900)))
            shown = []
            for task in ("00000000-0000-0000-0000-000000000011",
                         "00000000-0000-0000-0000-000000000012"):
                d = dict(id="sku", unit=task, kind="goods", cost=8000, reserve=9000,
                         open=11000, ask=10000, rounds=0, history=[("you asked", 10000)])
                shown.append(cue.message(task, d, "Asking 10000.", now=101))
                self.assertEqual((d["ask"], d["open"], d["reserve"]), (10000, 11000, 9000))
            self.assertCountEqual(shown, ["Asking 10000.",
                "Asking 10000. Please send your offer as a number."])

if __name__ == "__main__":
    unittest.main()
