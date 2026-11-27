"""Tests for drain_gate.py (offline, fixture logs in a tmp dir). Run: python3 -I ops/deploy/test_drain_gate.py"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drain_gate as g  # noqa: E402

NOW = 1795760000.0


class Clock:
    """A fake clock: sleep() moves it, so a 15-minute wait runs in no time."""
    def __init__(self, t=NOW):
        self.t, self.slept = t, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        (self.d / "logs").mkdir()
        for n in ("messages", "checkouts"):
            (self.d / "logs" / f"{n}.jsonl").write_text("")

    def log(self, name, *rows):
        with open(self.d / "logs" / name, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def msg(self, mid, t_in, t_out=None):
        self.log("messages.jsonl", {"dir": "in", "id": mid, "kind": "a2a", "task": "t-" + mid, "ts": t_in})
        if t_out is not None:
            self.log("messages.jsonl", {"dir": "out", "id": mid, "kind": "a2a", "task": "t-" + mid, "ts": t_out})

    def run_gate(self, cmd, clock=None, **env):
        clock = clock or Clock()
        env = dict({"AXD_GATE_QUIET_MAX": "900", "AXD_GATE_SLEEP": "20", "AXD_GATE_DRAIN_MAX": "900",
                    "AXD_GATE_DRAIN_SLEEP": "5"}, **env)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = g.main([cmd, "--dir", str(self.d)], env=env, clock=clock, sleep=clock.sleep)
        return rc, out.getvalue(), clock

    # --- phase 1: the activity.py rule
    def test_phase1_passes_when_answered_and_no_checkout_issued_in_300s(self):
        self.msg("m1", NOW - 50, NOW - 40)
        self.log("checkouts.jsonl", {"event": "issued", "order": "o-old", "ts": NOW - 400})
        rc, out, clock = self.run_gate("wait")
        self.assertEqual(rc, 0, out)
        self.assertIn("phase 1", out)
        self.assertEqual(clock.slept, [])

    def test_phase1_one_shot_refuses_a_recent_issue_like_activity_py(self):
        self.log("checkouts.jsonl", {"event": "issued", "order": "o-1", "ts": NOW - 100})
        rc, out, _ = self.run_gate("quiet")
        self.assertEqual(rc, 1, out)

    # --- phase 2: drain after the phase 1 cap
    def test_phase1_timeout_then_phase2_passes_when_only_recent_issues_block(self):
        self.msg("m1", NOW - 30, NOW - 20)
        self.log("checkouts.jsonl", {"event": "issued", "order": "o-1", "ts": NOW - 10})   # unpaid, just issued
        clock = Clock()
        with open(self.d / "logs" / "checkouts.jsonl", "a") as f:   # a customer gets a checkout every few minutes
            for k in range(1, 6):
                f.write(json.dumps({"event": "issued", "order": f"o-{k + 1}", "ts": NOW + 200 * k}) + "\n")
        rc, out, clock = self.run_gate("wait", clock=clock, AXD_GATE_QUIET_MAX="900")
        self.assertEqual(rc, 0, out)
        self.assertIn("phase 2", out)
        self.assertGreaterEqual(clock.t - NOW, 900)          # phase 1 ran its full 15 minutes first
        self.assertLess(clock.t - NOW, 900 + 30)

    def test_phase2_blocks_on_an_inbound_message_unanswered_for_more_than_90s(self):
        self.msg("m-long", NOW - 200)                          # a turn longer than 90 s: activity.py no longer sees it
        rc, out, _ = self.run_gate("quiet")
        self.assertEqual(rc, 0, out)                           # the old rule would stop the kit now
        rc, out, _ = self.run_gate("drain")
        self.assertEqual(rc, 1, out)
        self.assertIn("unanswered", out)

    def test_a_message_unanswered_beyond_the_lookback_does_not_block_forever(self):
        self.msg("m-dropped", NOW - 700)
        rc, out, _ = self.run_gate("drain", AXD_GATE_LOOKBACK="600")
        self.assertEqual(rc, 0, out)

    def test_phase2_blocks_on_a_turn_in_flight(self):
        self.log("react.jsonl", {"thought": "x", "action": "check_item_inventory", "args": {}, "ts": NOW - 10})
        rc, out, _ = self.run_gate("drain")
        self.assertEqual(rc, 1, out)
        self.assertIn("in flight", out)
        self.log("react.jsonl", {"thought": "x", "action": "sleep", "args": {"seconds": 600}, "ts": NOW - 5})
        rc, out, _ = self.run_gate("drain")                    # the run ended with sleep
        self.assertEqual(rc, 0, out)

    def test_phase2_blocks_on_a_wallet_send_with_no_tx_hash_yet(self):
        self.log("sends.jsonl", {"ts": int(NOW - 30), "purpose": "supply", "order": "q-1", "to": "0xm", "amount": 9000,
                                 "nonce": 7})
        rc, out, _ = self.run_gate("drain")
        self.assertEqual(rc, 1, out)
        self.assertIn("wallet send", out)
        self.log("sends.jsonl", {"ts": int(NOW - 20), "purpose": "supply", "order": "q-1", "to": "0xm", "amount": 9000,
                                 "tx_hash": "0x" + "ab" * 32})
        rc, out, _ = self.run_gate("drain")
        self.assertEqual(rc, 0, out)

    def test_drain_cap_exits_non_zero_without_passing(self):
        self.msg("m-busy", NOW - 5)                            # never answered within the test
        rc, out, clock = self.run_gate("wait", AXD_GATE_QUIET_MAX="60", AXD_GATE_DRAIN_MAX="120")
        self.assertEqual(rc, 3, out)
        self.assertIn("drain cap", out)
        self.assertLess(clock.t - NOW, 60 + 120 + 30)

    def test_unreadable_messages_log_fails_closed(self):
        (self.d / "logs" / "messages.jsonl").unlink()
        self.assertEqual(self.run_gate("drain")[0], 1)
        self.assertEqual(self.run_gate("quiet")[0], 1)


if __name__ == "__main__":
    unittest.main()
