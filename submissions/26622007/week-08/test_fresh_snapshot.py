"""Actual independent filesystem writer vs a verified in-memory buyer image."""
import json
import os
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
    def actual_files(self, probe=None):
        data, _ = image.snapshot_from_git(REPO, PIN)
        manifest = json.loads(data)
        files = {n: v.encode() for n, v in manifest["files"].items()}
        files["buyer_fresh_rotation.py"] = files.pop(manifest["helper"] + ".py")
        files["market_buyer.py"] = subprocess.check_output(
            ["git", "show", PIN + ":submissions/26622007/week-08/market_buyer.py"], cwd=REPO)
        if probe:
            tail = b'if __name__ == "__main__":\n    sys.exit(main())'
            self.assertIn(tail, files["market_buyer.py"])
            files["market_buyer.py"] = files["market_buyer.py"].replace(tail, probe.encode())
        return files

    def run_actual_probe(self, probe, *, engine="rule", pricing=None):
        files = self.actual_files(probe)
        if pricing is not None:
            files["pricing.json"] = json.dumps(pricing).encode()
        data, digest = image.make_snapshot(files, PIN)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tool = root / "tools" / "hostile"
            tool.mkdir(parents=True)
            (tool / "manifest.json").write_text(json.dumps(dict(description="fixture", selftest=dict(args={}), parameters={})))
            (tool / "tool.py").write_text("from pathlib import Path; Path('tool-sentinel').write_text('executed')")
            (root / "shops.json").write_text("[]")
            path = root / "image.json"
            path.write_bytes(data)
            env = dict(os.environ, AGENT_DIR=str(root), AGENT_TOOLS=str(root / "tools"),
                       HARNESS="snapshot-fixture-no-backend")
            if engine is None:
                env.pop("BUYER_CHOOSE_ENGINE", None)
            else:
                env["BUYER_CHOOSE_ENGINE"] = engine
            p = subprocess.run(shlex.split(image.snapshot_command(path, digest)) +
                               ["--shops", str(root / "shops.json"), "--item", "fixture", "--budget", "100"],
                               cwd=root, env=env, capture_output=True, text=True, timeout=15)
            sentinels = [p.name for p in root.rglob("*sentinel")]
            return p, sentinels

    def test_snapshot_actual_main_rejects_nonrule_before_any_order_side_effect(self):
        for engine in ("agent", "unknown", "{}"):
            with self.subTest(engine=engine):
                p, sentinels = self.run_actual_probe(None, engine=engine)
                self.assertEqual(p.returncode, 78, p.stderr)
                self.assertNotIn('"receipt"', p.stdout)
                self.assertFalse(sentinels)
        for pricing in ({"buyer_choose": {"engine": "agent"}}, {"buyer_choose": []}, {}):
            p, sentinels = self.run_actual_probe(None, engine=None, pricing=pricing)
            self.assertEqual(p.returncode, 78, p.stderr)
            self.assertFalse(sentinels)

    def test_snapshot_rule_without_prefetch_and_hurry_cannot_reach_first_ask(self):
        setup = """
from pathlib import Path
shops=[dict(name=n,url='http://'+n,reviews=[]) for n in ('a','b','c')]
memory={'trades':[]}
AGENT_DIR=None
def forbidden(*a,**k):
    Path('ask-sentinel').write_text('called')
    raise AssertionError('ask reached')
ask=forbidden
"""
        for call in ("choose_racing(shops,memory,'fixture',100,None,rule_engine=True)",
                     "choose(shops,memory,'fixture',100,hurry=lambda:True)",
                     "AGENT_DIR=os.environ['AGENT_DIR']; choose(shops,memory,'fixture',100,hurry=lambda:True)",
                     "choose_racing(shops,memory,'fixture',100,{'enough':lambda:True},rule_engine=False)",
                     "import threading; t=threading.Thread(target=lambda:choose(shops,memory,'fixture',100,hurry=lambda:True),daemon=True); t.start(); t.join(); raise AssertionError('daemon returned')"):
            p, sentinels = self.run_actual_probe(setup + "\n" + call)
            self.assertFalse(sentinels, (call, sentinels, p.stderr))
            self.assertEqual(p.returncode, 78, p.stderr)

    def test_snapshot_agent_and_tool_public_boundaries_are_fatal(self):
        for call in ("agent_loop.decide('fixture',{},'.','buyer')",
                     "agent_tools.discover(md='.')", "agent_tools.selftest('hostile',{'selftest':{'args':{}}},'.')",
                     "agent_tools.run('hostile',{},'.')", "agent_tools.execute('hostile',{},'.')",
                     "agent_tools.boxed(['python','tool.py'],'.','.')"):
            probe = """
import agent_tools,builtins,io
from pathlib import Path
def forbidden(*a,**k):
    Path('backend-sentinel').write_text('called')
    raise AssertionError('external side effect reached')
agent_loop.ask=forbidden
agent_tools.subprocess.run=forbidden
saved_open=builtins.open
def watched(file,*a,**k):
    if str(file).endswith(('manifest.json','tool.py')):
        forbidden()
    return saved_open(file,*a,**k)
builtins.open=watched
io.open=watched
""" + call
            p, sentinels = self.run_actual_probe(probe)
            self.assertEqual(p.returncode, 78, (call, p.stderr))
            self.assertFalse(sentinels)

    def test_snapshot_actual_rule_with_prefetch_preserves_first_choice(self):
        probe = """
shops=[dict(name=n,url='http://'+n,reviews=[]) for n in ('a','b','c')]
picked,reason=choose_racing(shops,{'trades':[]},'fixture',100,{'asked':{'a'},'selection_asked':{'a'}},rule_engine=True)
assert picked['name']=='b' and ' rule: ' in reason
print('RULE_PREFETCH_PRESERVED')
"""
        p, sentinels = self.run_actual_probe(probe)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('RULE_PREFETCH_PRESERVED', p.stdout)
        self.assertFalse(sentinels)

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
