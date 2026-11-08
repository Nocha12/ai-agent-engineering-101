import tempfile
import unittest
from pathlib import Path
from meta.evidence_citations import cite
from meta_harness import verify_evidence


class EvidenceCitations(unittest.TestCase):
    def test_matches_verifier_and_detects_later_change(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / 'test.log'
            path.write_text('diagnostic\u2028inside one file line\nRan 14 tests\n\nOK\n')
            items = cite('test.log', 'Ran 14', root)
            self.assertEqual(items[0]['ref'], 'test.log:2')
            self.assertEqual(verify_evidence(items, root=d), [])
            path.write_text('still running\n')
            self.assertTrue(verify_evidence(items, root=d))

    def test_missing_match_and_outside_root_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, 'test.log').write_text('pending\n')
            with self.assertRaises(ValueError):
                cite('test.log', 'Ran ', d)
            with self.assertRaises(ValueError):
                cite('../test.log', 'Ran ', d)
