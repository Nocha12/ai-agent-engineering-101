"""Entry-last installation in isolated filesystem fixtures; no live execution."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import fresh_entry_install as install

SOURCE_REPO = Path(__file__).resolve().parents[3]


class FreshEntryInstall(unittest.TestCase):
    def fixture(self, root):
        old = b"def later():\n import shared\n return shared.VALUE\n"
        helper = b"ENABLED = False\nVALUE = 'new'\n"
        new = b"def later():\n import buyer_fresh_rotation\n import shared\n return buyer_fresh_rotation.VALUE + shared.VALUE\n"
        blobs = {"market_buyer.py": new, "buyer_fresh_rotation.py": helper,
                 "fresh-rotation-contract.md": b"fixture", "test_buyer_fresh_rotation.py": b"",
                 "test_fresh_choice_boundary.py": b""}
        common = b"VALUE = 'shared'\n"
        (root / "market_buyer.py").write_bytes(old)
        (root / "shared.py").write_bytes(common)
        (root / "seller.py").write_bytes(b"# unrelated dirty seller\n")
        (root / "harness.py").write_bytes(b"# retained harness\n")
        return {"base": "1" * 40, "candidate": "2" * 40, "old_entry": old,
                "blobs": blobs, "common": {"shared.py": install.blob_id(common)}}

    def load_entry(self, root):
        # Read source as a process opening the entry does; no cached pyc shortcut.
        namespace = {}
        exec(compile((root / "market_buyer.py").read_bytes(), "fixture_entry", "exec"), namespace)
        return namespace["later"]

    def test_entry_last_and_old_late_import_survive_install_and_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            release = self.fixture(root)
            old_later = self.load_entry(root)
            retained = {n: (root / n).read_bytes() for n in ("seller.py", "harness.py")}
            seen = []
            def write(path, data):
                # All reader-visible intermediate states have a valid entry.
                seen.append(path.name)
                self.assertTrue((root / "buyer_fresh_rotation.py").is_file())
                self.assertEqual((root / "market_buyer.py").read_bytes(), release["old_entry"])
                install.atomic_write(path, data)
            self.assertEqual(install.apply(root, release, write=write)["status"], "installed")
            self.assertEqual(seen, ["fresh-rotation-contract.md", "test_buyer_fresh_rotation.py",
                                    "test_fresh_choice_boundary.py", "market_buyer.py"])
            new_later = self.load_entry(root)
            with patch.dict("sys.modules", {}), patch("sys.path", [str(root)] + __import__("sys").path):
                import sys
                saved = {n: sys.modules.pop(n, None) for n in ("shared", "buyer_fresh_rotation")}
                try:
                    self.assertEqual(old_later(), "shared")
                    install.rollback(root, release)
                    # New process code opened before rollback may import late.
                    self.assertEqual(new_later(), "newshared")
                    self.assertEqual(self.load_entry(root)(), "shared")
                finally:
                    for n, value in saved.items():
                        sys.modules.pop(n, None)
                        if value is not None:
                            sys.modules[n] = value
            for n, data in retained.items():
                self.assertEqual((root / n).read_bytes(), data)

    def test_failure_before_or_after_entry_commit_keeps_complete_readable_version(self):
        for fail_name in install.FILES:
            for after in (False, True):
                with self.subTest(file=fail_name, after=after), tempfile.TemporaryDirectory() as d:
                    root = Path(d)
                    release = self.fixture(root)
                    def write(path, data):
                        if path.name == fail_name and not after:
                            raise OSError("fixture before replace")
                        install.atomic_write(path, data)
                        if path.name == fail_name:
                            raise OSError("fixture after replace")
                    result = install.apply(root, release, write=write)
                    self.assertEqual(result["status"], "failed")
                    self.assertEqual((root / "market_buyer.py").read_bytes(), release["old_entry"])
                    # Retain helper for processes that opened new entry already.
                    self.assertEqual((root / "buyer_fresh_rotation.py").read_bytes(), release["blobs"]["buyer_fresh_rotation.py"])

    def test_conflict_downgrade_dirty_and_symlink_fail_before_entry(self):
        for kind in ("helper", "shared", "entry", "symlink", "pyc"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                release = self.fixture(root)
                if kind == "helper":
                    (root / "buyer_fresh_rotation.py").write_bytes(b"ENABLED = True\n")
                elif kind in ("shared", "entry"):
                    (root / ("shared.py" if kind == "shared" else "market_buyer.py")).write_bytes(b"# drift\n")
                elif kind == "symlink":
                    (root / "buyer_fresh_rotation.py").symlink_to(root / "shared.py")
                else:
                    (root / "__pycache__").mkdir()
                    (root / "__pycache__/buyer_fresh_rotation.fixture.pyc").write_bytes(b"collision")
                before = (root / "market_buyer.py").read_bytes()
                with self.assertRaises(ValueError):
                    install.apply(root, release)
                self.assertEqual((root / "market_buyer.py").read_bytes(), before)

    def test_rollback_never_overwrites_drift_or_downgrades_helper(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            release = self.fixture(root)
            install.apply(root, release)
            (root / "market_buyer.py").write_bytes(b"# another writer\n")
            with self.assertRaises(ValueError):
                install.rollback(root, release)
            self.assertEqual((root / "market_buyer.py").read_bytes(), b"# another writer\n")

    def test_helper_publication_failure_never_changes_entry(self):
        for after in (False, True):
            with self.subTest(after=after), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                release = self.fixture(root)
                link = os.link
                def broken(*args, **kwargs):
                    if after:
                        link(*args, **kwargs)
                    raise OSError("fixture publication failure")
                with patch.object(install.os, "link", side_effect=broken):
                    with self.assertRaises(OSError):
                        install.apply(root, release)
                self.assertEqual((root / "market_buyer.py").read_bytes(), release["old_entry"])
                self.assertFalse(list(root.glob(".fresh-stage-*")))

    def test_real_pinned_sources_import_before_after_and_after_rollback(self):
        repo = SOURCE_REPO  # archive runner supplies the original object store
        base = "38a80667c02bd6a18c4bf6527371c153708d67bc"
        candidate = "1567b832adc05b06025c6c6f06c7ba1ea5c957da"
        release = install.release(repo, base, candidate)
        self.assertEqual(set(release["blobs"]), set(install.PAYLOAD))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            # Plain isolated filesystem fixture, no clone/worktree/checkout.
            for n in release["common"]:
                (root / n).write_bytes(install.git(repo, "show", base + ":" + install.PREFIX + n))
            (root / install.ENTRY).write_bytes(release["old_entry"])
            def imported(new):
                subprocess.run([sys.executable, "-B", "-c",
                    "import market_buyer as m; import buyer_post; "
                    f"assert hasattr(m, 'buyer_fresh_rotation') is {new!r}"],
                    cwd=root, check=True, capture_output=True, timeout=15)
            imported(False)
            previous = os.umask(0o077)
            try:
                self.assertEqual(install.apply(root, release)["status"], "installed")
            finally:
                os.umask(previous)
            self.assertEqual((root / install.HELPER).stat().st_mode & 0o777, 0o644)
            imported(True)
            install.rollback(root, release)
            imported(False)
            self.assertTrue((root / install.HELPER).exists())


if __name__ == "__main__":
    unittest.main()
