"""Owner CLI contracts exercised only in newly created temporary Git repositories."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CLI = Path(__file__).parent / 'tools/quote_owner_executor.py'
PREFIX = 'submissions/26622007/week-08/'
FILES = [PREFIX + name for name in (
    'buyer_quote_alignment_policy.py', 'buyer_quote_alignment.py',
    'tools/quote_alignment_measure.py', 'market_buyer.py')]
OFF = b"SOURCE_CONTROL = True\nENABLED = False\nSTART = 0\nSTUDY = ''\n"


class OwnerCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.target = self.root / 'target'
        self.stage = self.root / 'stage'
        for repo in (self.source, self.target):
            repo.mkdir()
            self.git(repo, 'init', '-q')
            self.git(repo, 'config', 'user.name', 'Fixture')
            self.git(repo, 'config', 'user.email', 'fixture@example.invalid')
            for name in FILES:
                p = repo / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(OFF if name == FILES[0] else
                              ('# ' + repo.name + '\n').encode())
            self.git(repo, 'add', '.')
            self.git(repo, 'commit', '-qm', 'fixture')
        self.commit = self.git(self.source, 'rev-parse', 'HEAD').strip()
        self.head = self.git(self.target, 'rev-parse', 'HEAD').strip()

    def git(self, repo, *args):
        return subprocess.check_output(['git', '-C', str(repo), *args],
                                       stderr=subprocess.PIPE, text=True, timeout=10)

    def cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(CLI), *map(str, args)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def prepare(self):
        return self.cli('prepare', '--source', self.source, '--commit', self.commit,
                        '--target', self.target, '--expected-head', self.head,
                        '--staging', self.stage)

    def test_prepare_pins_four_blobs_outside_target(self):
        result = self.prepare()
        manifest = json.loads((self.stage / 'manifest.json').read_text())
        self.assertEqual(set(manifest['files']), set(FILES))
        self.assertEqual(manifest['commit'], self.commit)
        self.assertEqual(manifest['expected_head'], self.head)
        for name in FILES:
            self.assertEqual((self.stage / name).read_bytes(),
                             (self.source / name).read_bytes())
        self.assertEqual(result['manifest_sha256'], hashlib.sha256(
            (self.stage / 'manifest.json').read_bytes()).hexdigest())
        self.assertEqual(self.git(self.target, 'status', '--porcelain'), '')


if __name__ == '__main__':
    unittest.main()
