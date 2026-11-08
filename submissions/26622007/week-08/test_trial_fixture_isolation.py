"""Regression: synthetic Seller E2E must never write ambient trial namespaces."""
import hashlib
import json
import pathlib
import tempfile
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch
import seller
import test_round3 as round3

NAMES = ("READER_TRIAL", "CONCESSION_TRIAL", "LATE_CONCESSION",
         "OPENING_DEFAULT", "DRAIN", "OPENING_CUE", "LATE_SPEED",
         "JEV_TRANSPORT", "SHELF_REPLY", "EXACT_ACCEPT", "EXACT_COUNTER",
         "GRADE_PREFERENCE", "FOOD_OPENING", "AT_FLOOR_EXACT", "DEEP_LOWBALL")


def manifest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


class FixtureIsolation(unittest.TestCase):
    def test_active_ambient_trial_is_untouched_by_real_round3_fixture(self):
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            outside = pathlib.Path(td) / "ambient"
            controls = {name: type(getattr(seller, name))(root=outside)
                        for name in NAMES}
            deep = controls["DEEP_LOWBALL"]
            deep.root.mkdir(parents=True)
            now = time.time()
            (deep.root / "seller_concession_trial.json").write_text(json.dumps(
                dict(enabled=True, id="isolated-regression", start=now-10,
                     end=now+1790, seed=7)))
            for name, value in controls.items():
                stack.enter_context(patch.object(seller, name, value))
            before = manifest(outside)
            case = round3.EndToEnd(methodName="test_food_deal_goes_all_the_way_with_a_checkable_certificate")
            try:
                case.setUp()
                result = case.deal("G1", 10000)
                self.assertEqual(result["receipt"]["unit"], "u-food")
                self.assertEqual(manifest(outside), before)
                for name in NAMES:
                    self.assertTrue(pathlib.Path(getattr(seller, name).root).is_relative_to(case.tmp.name),
                                    name)
            finally:
                case.doCleanups()
            for name in NAMES:
                self.assertIs(getattr(seller, name), controls[name])


if __name__ == "__main__":
    unittest.main()
