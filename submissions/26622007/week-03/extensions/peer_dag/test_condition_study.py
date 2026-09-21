"""Check that failed attempts and repeat consistency are reported honestly."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import condition_study as study


class StudyTests(unittest.TestCase):
    def test_fact_comparison_keeps_boolean_missing_and_numeric_distinctions(self):
        expected = {"number": 2, "flag": True}
        first = study.canonical_facts({"number": 2, "flag": True, "extra": "ignored"}, expected)
        self.assertEqual(first, study.canonical_facts({"number": 2.0, "flag": True}, expected))
        self.assertNotEqual(study.fingerprint(first),
                            study.fingerprint(study.canonical_facts({"number": 2, "flag": 1}, expected)))
        self.assertEqual(study.canonical_facts({}, expected)["flag"], {"missing": True})
        self.assertIsNone(study.canonical_facts(None, expected))

    def test_failed_runs_stay_in_denominator_and_wrong_agreement_is_separate(self):
        template = {"passed": False, "checks_passed": 0, "checks_total": 12, "calls": 20,
                    "elapsed_seconds": 10, "reported_cost_usd": 0.01, "awards": {"C": 1},
                    "canonical_facts": {"wrong": 42}, "root_award": "C", "max_depth": 1,
                    "proposal_rejections": 0, "c_high_bids": 1, "c_proposals": 1}
        metrics = []
        for condition in study.CONDITIONS:
            for block in range(3):
                item = dict(copy.deepcopy(template), condition=condition)
                if block == 2:
                    item.update(canonical_facts=None, reported_cost_usd=None)
                metrics.append(item)
        groups = study.aggregate(metrics)
        for group in groups.values():
            self.assertEqual((group["attempts"], group["passed"], group["checks_total"]), (3, 0, 36))
            self.assertEqual((group["runs_with_facts"], group["distinct_fact_vectors"]), (2, 1))
            self.assertEqual(group["runs_with_cost"], 2)

    def test_timeout_preserves_partial_trace_without_fabricating_result(self):
        with tempfile.TemporaryDirectory(dir=study.ROOT) as folder:
            root = Path(folder)
            (root / "logs").mkdir()
            log = root / "logs/interrupted.jsonl"
            original = '{"event":"call_start","phase":"propose","worker":"A"}\n{"event":'
            log.write_text(original)
            attempt = {"run_id": "interrupted", "condition": "baseline", "block": 1,
                       "exit_code": -15, "timed_out": True, "wall_seconds": 600}
            with patch.object(study, "ROOT", root):
                metric, setting = study.inspect_run(attempt, {"expected": 1})
            self.assertIsNone(setting)
            self.assertEqual(metric["status"], "timeout")
            self.assertFalse(metric["passed"])
            self.assertFalse(metric["trace_passed"])
            self.assertEqual(metric["calls"], 1)
            self.assertIsNone(metric["canonical_facts"])
            self.assertIsNone(metric["reported_cost_usd"])
            self.assertEqual(log.read_text(), original)


if __name__ == "__main__":
    unittest.main()
