import tempfile
import unittest
from pathlib import Path
from meta_harness import locate_evidence, verify_evidence

class EvidenceLocator(unittest.TestCase):
    def test_real_unittest_line_with_blank_lines(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, 'run.txt').write_text('noise\n\nRan 4 tests in 0.1s\n\nOK\n')
            e = locate_evidence('run.txt', 'OK', '실제 종료행', d)
            self.assertEqual(e['ref'], 'run.txt:5')
            self.assertEqual(verify_evidence([e], d), [])
    def test_ambiguous_or_substring_fails(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, 'run.txt').write_text('OK\nOK\nnot OK\n')
            for excerpt in ['OK', 'not', '']:
                with self.assertRaises(ValueError):
                    locate_evidence('run.txt', excerpt, '', d)
    def test_external_path_fails(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                locate_evidence('../outside', 'OK', '', d)
