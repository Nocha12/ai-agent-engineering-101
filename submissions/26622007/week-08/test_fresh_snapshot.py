"""Actual independent filesystem writer vs a verified in-memory buyer image."""
import json
from pathlib import Path
import select
import shlex
import subprocess
import sys
import tempfile
import unittest

from tools import fresh_snapshot as image

REPO = Path(__file__).resolve().parents[3]
PIN = "3e243d43f7ed8bfe6f85541f6db08dc24addf87f"
ROLLBACK = "6a23d40dc04c03aa0242bedcce55436dcb7a47a5"


class FreshSnapshot(unittest.TestCase):
    def files(self, wait=False):
        entry = "import json,os,sys\n"
        if wait:
            entry += "print('READY',flush=True)\nsys.stdin.readline()\n"
        entry += (
            "import buyer_fresh_rotation\nimport late\nfrom pathlib import Path\n"
            "resource=json.loads(Path(__file__).with_name('pricing.json').read_text())\n"
            "print(json.dumps(dict(flag=buyer_fresh_rotation.ENABLED,start=buyer_fresh_rotation.START,"
            "end=buyer_fresh_rotation.END,study=buyer_fresh_rotation.STUDY,resource=resource['value'],late=late.VALUE)))\n"
        )
        return {"market_buyer.py": entry.encode(), "buyer_fresh_rotation.py":
                b"ENABLED = False\nSTART = 100\nEND = 1900\nSTUDY = 'old'\n",
                "late.py": b"VALUE = 'PINNED'\n", "pricing.json": b'{"value":"PINNED"}'}

    def start(self, root, data, digest):
        path = root / (digest + ".json")
        path.write_bytes(data)
        cmd = shlex.split(image.snapshot_command(path, digest, sys.executable))
        return path, subprocess.Popen(cmd, cwd=root, stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def ready(self, proc):
        self.assertTrue(select.select([proc.stdout], [], [], 10)[0], "snapshot reader not ready")
        line = proc.stdout.readline()
        if line != "READY\n":
            _, err = proc.communicate(timeout=5)
            self.fail("snapshot bootstrap failed: " + err)

    def test_real_noncooperating_writer_preserved_after_verification_and_late_import(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            files = self.files(wait=True)
            for name, data in files.items():
                (root / name).write_bytes(data)
            data, digest = image.make_snapshot(files, PIN)
            path, proc = self.start(root, data, digest)
            try:
                self.ready(proc)  # The actual new buyer has verified and entered.
                # Independent OS process, no write-seam callback or tiny delay.
                subprocess.run([sys.executable, "-I", "-B", "-c",
                    "from pathlib import Path; import sys; r=Path(sys.argv[1]); "
                    "[(r/n).write_bytes(b'FOREIGN') for n in "
                    "['market_buyer.py','buyer_fresh_rotation.py','late.py','pricing.json']]; "
                    "Path(sys.argv[2]).write_bytes(b'FOREIGN-IMAGE')", str(root), str(path)],
                    check=True, timeout=10)
                out, err = proc.communicate("GO\n", timeout=10)
                self.assertEqual(proc.returncode, 0, err)
                row = json.loads(out)
                self.assertEqual(row, dict(flag=False, start=100, end=1900, study="old", resource="PINNED", late="PINNED"))
                for name in files:
                    self.assertEqual((root / name).read_bytes(), b"FOREIGN")
                self.assertEqual(path.read_bytes(), b"FOREIGN-IMAGE")
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.communicate()

    def test_tampered_before_entry_is_rejected_without_mutating_anything(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            data, digest = image.make_snapshot(self.files(), PIN)
            path = root / "image.json"
            path.write_bytes(data + b" ")
            before = path.read_bytes()
            proc = subprocess.run(shlex.split(image.snapshot_command(path, digest)), cwd=root,
                                  capture_output=True, text=True, timeout=10)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("snapshot digest mismatch", proc.stderr)
            self.assertEqual(path.read_bytes(), before)

    def test_killed_reader_retry_and_rollback_keep_old_new_full_settings(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old, old_hash = image.make_snapshot(self.files(), PIN)
            on_settings = dict(ENABLED=True, START=1000, END=2800, STUDY="synthetic_new")
            new, new_hash = image.make_snapshot(self.files(), PIN, settings=on_settings)
            self.assertNotEqual(old_hash, new_hash)
            blocked, blocked_hash = image.make_snapshot(self.files(wait=True), PIN, settings=on_settings)
            _, proc = self.start(root, blocked, blocked_hash)
            self.ready(proc)
            proc.kill()
            proc.communicate(timeout=10)
            for data, digest, flag, study in ((new, new_hash, True, "synthetic_new"),
                                             (old, old_hash, False, "old"), (new, new_hash, True, "synthetic_new")):
                _, proc = self.start(root, data, digest)
                out, err = proc.communicate(timeout=10)
                self.assertEqual(proc.returncode, 0, err)
                row = json.loads(out)
                self.assertEqual((row["flag"], row["study"]), (flag, study))
                self.assertEqual(row["end"] - row["start"], 1800)
            self.assertTrue((root / (old_hash + ".json")).exists())
            self.assertTrue((root / (new_hash + ".json")).exists())

    def test_same_module_is_read_only_resource_and_code_identity(self):
        files = self.files()
        files["market_buyer.py"] = (b"import buyer_fresh_rotation\nfrom pathlib import Path\n"
            b"p=Path(__file__).with_name('late.py')\nassert p.read_bytes()==b\"VALUE = 'PINNED'\\n\"\n"
            b"try:\n p.write_text('changed')\nexcept PermissionError:\n print('READ_ONLY')\n"
            b"else:\n raise AssertionError('snapshot write allowed')\n")
        data, digest = image.make_snapshot(files, PIN)
        with tempfile.TemporaryDirectory() as d:
            _, proc = self.start(Path(d), data, digest)
            out, err = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0, err)
            self.assertEqual(out.strip(), "READ_ONLY")

    def test_held_new_reader_late_import_survives_selecting_old_rollback_image(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            settings = dict(ENABLED=True, START=1000, END=2800, STUDY="held_new")
            new, new_hash = image.make_snapshot(self.files(wait=True), PIN, settings=settings)
            new_path, held = self.start(root, new, new_hash)
            try:
                self.ready(held)
                old, old_hash = image.make_snapshot(self.files(), PIN)
                old_path, rollback = self.start(root, old, old_hash)
                out, err = rollback.communicate(timeout=10)
                self.assertEqual(rollback.returncode, 0, err)
                self.assertFalse(json.loads(out)["flag"])
                out, err = held.communicate("GO\n", timeout=10)
                self.assertEqual(held.returncode, 0, err)
                self.assertEqual((json.loads(out)["flag"], json.loads(out)["study"]), (True, "held_new"))
                self.assertEqual(new_path.read_bytes(), new)
                self.assertEqual(old_path.read_bytes(), old)
            finally:
                if held.poll() is None:
                    held.kill()
                held.communicate()

    def test_real_market_kit_launches_pinned_actual_buyer_with_mutable_entry_foreign(self):
        sys.path.insert(0, str(REPO / "scripts"))
        import market_kit as kit
        data, digest = image.snapshot_from_git(REPO, PIN)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "image.json"
            path.write_bytes(data)
            (root / "market_buyer.py").write_bytes(b"raise AssertionError('mutable entry ran')\n")
            shops = root / "shops.json"
            shops.write_text("[]")
            command = image.snapshot_command(path, digest) + " --shops {shops} --item {item} --budget {budget}"
            command = kit.fill(command, shops=shops, item="fixture ; touch never-created", budget=100)
            # The actual supported run_buyer/start API; no kit/seller restart.
            lines = kit.run_buyer(command, root)
            self.assertEqual(kit.last_json(lines), {"shop": "", "receipt": None, "stars": 1,
                                                  "review": "No shops to trade with."})
            self.assertEqual((root / "market_buyer.py").read_bytes(), b"raise AssertionError('mutable entry ran')\n")
            self.assertFalse((root / "never-created").exists())

    def test_publication_crash_retry_and_foreign_destination_never_overwrite(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "image.json"
            data, digest = image.make_snapshot(self.files(), PIN)
            with patch.object(image.os, "link", side_effect=OSError("fixture publish crash")):
                with self.assertRaises(OSError):
                    image.publish_snapshot(path, data, digest)
            self.assertFalse(path.exists())
            self.assertEqual(list(root.glob(".buyer-image-*")), [])
            image.publish_snapshot(path, data, digest)
            self.assertEqual(path.read_bytes(), data)
            path.write_bytes(b"FOREIGN")
            with self.assertRaises(FileExistsError):
                image.publish_snapshot(path, data, digest)
            self.assertEqual(path.read_bytes(), b"FOREIGN")

    def test_actual_retained_rollback_image_keeps_baseline_entry_and_helper(self):
        data, digest = image.snapshot_from_git(REPO, ROLLBACK)
        manifest = json.loads(data)
        baseline = subprocess.check_output(["git", "show", ROLLBACK +
            ":submissions/26622007/week-08/market_buyer.py"], cwd=REPO)
        self.assertEqual(manifest["files"]["market_buyer.py"].encode(), baseline)
        self.assertIn(manifest["helper"] + ".py", manifest["files"])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "rollback.json"
            image.publish_snapshot(path, data, digest)
            shops = root / "shops.json"
            shops.write_text("[]")
            command = shlex.split(image.snapshot_command(path, digest))
            p = subprocess.run(command + ["--shops", str(shops), "--item", "fixture", "--budget", "100"],
                               cwd=root, capture_output=True, text=True, timeout=10)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(p.stdout.splitlines()[-1])["review"], "No shops to trade with.")

    def test_rejects_symlink_fifo_and_incomplete_or_short_window_settings(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            data, digest = image.make_snapshot(self.files(), PIN)
            original = root / "original"
            original.write_bytes(data)
            link = root / "link"
            link.symlink_to(original)
            fifo = root / "fifo"
            os.mkfifo(fifo)
            for path in (link, fifo):
                p = subprocess.run(shlex.split(image.snapshot_command(path, digest)), capture_output=True,
                                   text=True, timeout=10)
                self.assertNotEqual(p.returncode, 0)
            for settings in (dict(ENABLED=True), dict(ENABLED=1, START=1000, END=2800, STUDY="s"),
                             dict(ENABLED=True, START=1000, END=2799, STUDY="s")):
                with self.assertRaises(ValueError):
                    image.make_snapshot(self.files(), PIN, settings=settings)


if __name__ == "__main__":
    unittest.main()
