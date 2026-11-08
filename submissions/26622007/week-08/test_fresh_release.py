"""Pinned full buyer release, including the baseline agent/tool fallback."""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

from tools import fresh_release


class FreshRelease(unittest.TestCase):
    def test_complete_release_preserves_baseline_choice_and_tools(self):
        files = {
            "market_buyer.py": b"from pathlib import Path\nimport policy\nassert policy.VALUE == 7\nprint(Path(__file__).parent.joinpath('tools/probe/manifest.json').read_text())\n",
            "policy.py": b"VALUE = 7\n",
            "tools/probe/manifest.json": b'{"purpose":"fixture"}',
            "tools/probe/tool.py": b"print('fixture')\n",
            "pricing.json": b"{}",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = root / "release"
            digest = fresh_release.publish(release, files, "a" * 40)
            self.assertEqual(fresh_release.verify(release, digest), "a" * 40)
            command = fresh_release.command(release, digest, sys.executable)
            process = subprocess.run(shlex.split(command), cwd=root, capture_output=True,
                                     text=True, timeout=10)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout), {"purpose": "fixture"})
            self.assertFalse((root / "__pycache__").exists())


if __name__ == "__main__":
    unittest.main()
