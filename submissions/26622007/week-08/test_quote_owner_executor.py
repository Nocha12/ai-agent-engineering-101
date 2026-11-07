"""Owner CLI contracts exercised only in newly created temporary Git repositories."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace


CLI = Path(__file__).parent / 'tools/quote_owner_executor.py'
PREFIX = 'submissions/26622007/week-08/'
FILES = [PREFIX + name for name in (
    'buyer_quote_alignment_policy.py', 'buyer_quote_alignment.py',
    'tools/quote_alignment_measure.py', 'market_buyer.py')]
OFF = b"SOURCE_CONTROL = True\nENABLED = False\nSTART = 0\nSTUDY = ''\n"


def executor():
    spec = importlib.util.spec_from_file_location('quote_owner_executor', CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        self.prepared = self.cli('prepare', '--source', self.source, '--commit', self.commit,
                        '--target', self.target, '--expected-head', self.head,
                        '--staging', self.stage)
        return self.prepared

    def plan(self, ok=True):
        return self.cli('plan', '--target', self.target, '--expected-head', self.head,
                        '--staging', self.stage, '--manifest-sha256',
                        self.prepared['manifest_sha256'], ok=ok)

    def apply(self, confirm=None, ok=True):
        return self.cli('apply', '--target', self.target, '--expected-head', self.head,
                        '--staging', self.stage, '--manifest-sha256',
                        self.prepared['manifest_sha256'], '--confirm',
                        confirm or self.plan()['confirm'], ok=ok)

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

    def test_plan_is_read_only_and_rejects_tampering_head_and_dirty(self):
        self.prepare()
        before = {str(p.relative_to(self.target)): p.read_bytes()
                  for p in self.target.rglob('*') if p.is_file()}
        plan = self.plan()
        self.assertEqual(plan['order'], FILES)
        self.assertEqual(before, {str(p.relative_to(self.target)): p.read_bytes()
                                 for p in self.target.rglob('*') if p.is_file()})
        p = self.stage / FILES[1]
        p.write_bytes(b'# tampered\n')
        self.assertEqual(self.plan(ok=False)['status'], 'rejected')
        p.write_bytes((self.source / FILES[1]).read_bytes())
        (self.target / FILES[3]).write_bytes(b'# dirty\n')
        self.plan(ok=False)
        (self.target / FILES[3]).write_bytes(b'# target\n')
        self.git(self.target, 'commit', '--allow-empty', '-qm', 'changed head')
        self.plan(ok=False)

    def test_apply_requires_exact_confirmation_preserves_other_dirty_and_stays_off(self):
        self.prepare()
        others = []
        for name in ('seller.py', 'restock.py', 'meta/evidence.json'):
            p = self.target / PREFIX / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'fixture dirty bytes\x00\xff')
            others.append(p)
        self.apply(confirm='wrong', ok=False)
        result = self.apply()
        self.assertTrue(result['sourcedOFF'])
        self.assertEqual(result['written'], FILES)
        self.assertEqual((self.target / FILES[0]).read_bytes(), OFF)
        for name in FILES[1:]:
            self.assertEqual((self.target / name).read_bytes(),
                             (self.source / name).read_bytes())
        self.assertEqual((self.stage / 'backup' / FILES[3]).read_bytes(), b'# target\n')
        for p in others:
            self.assertEqual(p.read_bytes(), b'fixture dirty bytes\x00\xff')

    def test_transition_order_and_partial_failure_leave_off_restore_entry(self):
        self.prepare()
        module = executor()
        args = SimpleNamespace(target=str(self.target), expected_head=self.head,
                               staging=str(self.stage),
                               manifest_sha256=self.prepared['manifest_sha256'],
                               confirm=self.plan()['confirm'])
        seen = []

        def disk_write(path, data):
            name = str(path.relative_to(self.target))
            module.atomic_write(path, data)
            seen.append(name)
            if len(seen) > 1:
                self.assertEqual((self.target / FILES[0]).read_bytes(), OFF)
            if len(seen) == 4:
                raise OSError('fixture failure after entry replace')

        result = module.execute_apply(args, write=disk_write)
        self.assertEqual(seen[:4], FILES)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['sourcedOFF'])
        self.assertEqual(result['recovery_errors'], [])
        self.assertEqual((self.target / FILES[3]).read_bytes(), b'# target\n')
        self.assertEqual((self.target / FILES[0]).read_bytes(), OFF)

    def test_off_requires_confirmation_and_disables_an_on_fixture(self):
        on = b"SOURCE_CONTROL = True\nENABLED = True\nSTART = 2000000000\nSTUDY = 'fixture'\n"
        (self.target / FILES[0]).write_bytes(on)
        args = ('off', '--target', self.target, '--expected-head', self.head, '--confirm')
        self.cli(*args, 'wrong', ok=False)
        self.assertEqual((self.target / FILES[0]).read_bytes(), on)
        result = self.cli(*args, f'off:{self.target}:{self.head}')
        self.assertTrue(result['sourcedOFF'])
        self.assertEqual((self.target / FILES[0]).read_bytes(), OFF)


if __name__ == '__main__':
    unittest.main()
