"""Existing supervisor dry-check; fixture has no credentials or running kit."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class FreshJoinSelector(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.here = self.root / 'repo/submissions/26622007/week-08'
        self.here.mkdir(parents=True)
        self.script = self.here / 'run_market.sh'
        shutil.copyfile(Path(__file__).with_name('run_market.sh'), self.script)
        (self.here.parent / '.env').write_text('')
        self.store = self.root / 'managed'

    def tearDown(self):
        self.temporary.cleanup()

    def check(self):
        return subprocess.run(['/bin/bash', str(self.script), '--check-join-selector', str(self.store)],
                              capture_output=True, text=True, timeout=5)

    def managed(self):
        generation = self.store / 'join-generations' / 'fixture'
        generation.mkdir(parents=True)
        (generation / 'agent.json').write_text('{"market_buyer":{"cmd":"fixture"}}')
        (generation / 'agent.json').chmod(0o444)
        generation.chmod(0o555)
        (self.store / 'current-join').symlink_to(generation)
        return generation

    def test_absent_selector_preserves_original_directory_without_loading_env(self):
        process = self.check()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), str(self.here))
        self.assertFalse((self.here / '.market').exists())

    def test_readonly_publisher_generation_and_rollback_reentry(self):
        original = self.managed()
        process = self.check()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), str(original))
        rollback = original.with_name('rollback')
        rollback.mkdir()
        (rollback / 'agent.json').write_text('{"market_buyer":{"cmd":"rollback fixture"}}')
        (rollback / 'agent.json').chmod(0o444)
        rollback.chmod(0o555)
        (self.store / 'current-join').unlink()
        (self.store / 'current-join').symlink_to(rollback)
        self.assertEqual(self.check().stdout.strip(), str(rollback))

    def test_writable_config_or_ancestor_and_external_target_are_rejected(self):
        generation = self.managed()
        (generation / 'agent.json').chmod(0o644)
        self.assertNotEqual(self.check().returncode, 0)
        (generation / 'agent.json').chmod(0o444)
        self.store.chmod(0o777)
        self.assertNotEqual(self.check().returncode, 0)
        self.store.chmod(0o755)
        (self.store / 'current-join').unlink()
        (self.store / 'current-join').symlink_to(self.here)
        process = self.check()
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(process.stdout)

    def test_runner_remains_protected_from_ordinary_meta_changes(self):
        import hard_limits
        self.assertTrue(hard_limits.guard_problems(['run_market.sh'], ''))


if __name__ == '__main__':
    unittest.main()
