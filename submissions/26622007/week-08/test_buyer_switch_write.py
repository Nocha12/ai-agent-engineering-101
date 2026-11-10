"""buyer_switch_write: the only way the windowed buyer switches are written — a finite window inside each study's bounds,
written before its start, never over a window already running, never one study made longer by off and on again
(ax-pro-cycle step 5, main instruction 10; independent reviews 7b17f3f, 77cdbef)."""
import json
import os
import tempfile
import time
import unittest
import unittest.mock

import buyer_moss_first as mf
import buyer_our_ask as oa
import buyer_switch_write as sw
import oneshot

H = 3600


def body(md, name):
    with open(os.path.join(md, sw.STUDIES[name]["file"]), encoding="utf-8") as f:
        return json.load(f)


def at(t):
    """The writer's clock at t (the files' mtimes stay the real clock, before every start here)."""
    return unittest.mock.patch.object(sw.time, "time", return_value=t)


class Write(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.mkdtemp()
        self.t = time.time()

    def test_each_study_reads_on_at_its_start_what_it_writes(self):
        start = self.t + 600
        for name, hours, salt in (("final_m1", 0.5, None), ("moss_first", 12, "moss-t"), ("our_ask", 9, "our-ask-t")):
            out = sw.write(self.md, name, start, start + hours * H, salt=salt)
            self.assertLessEqual(out["written"], start, name)
            self.assertEqual(body(self.md, name), dict({"enabled": True, "start": start, "end": start + hours * H},
                                                       **({"salt": salt} if salt else {})), name)
        self.assertTrue(oneshot.final_m1_on(self.md, env={}, now=start))
        self.assertFalse(oneshot.final_m1_on(self.md, env={}, now=self.t))          # not started yet
        self.assertIsNotNone(mf.assign(self.md, {"order": "o-1"}, now=start))
        self.assertIsNotNone(oa.assign(self.md, {"order": "o-1"}, now=start))
        with open(os.path.join(self.md, sw.HISTORY)) as f:
            self.assertEqual([json.loads(l)["study"] for l in f], ["final_m1", "moss_first", "our_ask"])

    def test_refused_windows_write_nothing(self):
        start = self.t + 600
        for name, s, e, salt in (("final_m1", start, start, None),                       # empty
                                 ("final_m1", start, start + 12 * H + 1, None),          # over 12 h
                                 ("final_m1", self.t - 1, self.t + 1800, None),          # already started
                                 ("final_m1", start, float("inf"), None),
                                 ("final_m1", float("nan"), start, None),
                                 ("final_m1", True, start, None),
                                 ("moss_first", start, start + 9 * H - 1, "m"),           # under 9 h
                                 ("moss_first", start, start + 13 * H, "m"),
                                 ("moss_first", start, start + 9 * H, None),              # salt needed
                                 ("our_ask", start, start + 9 * H, "bad salt"),
                                 ("final_m1", start, start + 1800, "s"),                  # no salt for M1
                                 ("nope", start, start + 1800, None)):
            with self.assertRaises(sw.Refused, msg=(name, s, e, salt)):
                sw.write(self.md, name, s, e, salt=salt)
        self.assertEqual(os.listdir(self.md), [])

    def test_a_running_window_is_never_rewritten_or_moved(self):
        start = self.t + 600
        sw.write(self.md, "moss_first", start, start + 9 * H, salt="moss-t")
        before = body(self.md, "moss_first")
        for now in (start, start + H):
            with at(now), self.assertRaises(sw.Refused):
                sw.write(self.md, "moss_first", now + 60, now + 60 + 12 * H, salt="moss-t2", new_study=True)
        self.assertEqual(body(self.md, "moss_first"), before)
        sw.write(self.md, "moss_first", start + 60, start + 60 + 9 * H, salt="moss-t3")   # not started: replaced
        self.assertEqual(body(self.md, "moss_first")["salt"], "moss-t3")

    def test_off_and_on_again_never_make_an_ab_study_longer(self):
        # review 77cdbef I1/M1: the stopped record lived only in the switch; the next window erased it.
        start = self.t + 600
        end = start + 9 * H
        sw.write(self.md, "moss_first", start, end, salt="moss-a")
        with at(start + 100):                                   # it ran, then off
            sw.off(self.md, "moss_first")
        self.assertEqual(body(self.md, "moss_first")["stopped"]["salt"], "moss-a")
        with at(start + 200):
            with self.assertRaises(sw.Refused):                  # the same arms again, even after its end
                sw.write(self.md, "moss_first", end + 60, end + 60 + 9 * H, salt="moss-a", new_study=True)
            with self.assertRaises(sw.Refused):                  # a new salt before the old end, not said
                sw.write(self.md, "moss_first", start + H, start + 10 * H, salt="moss-b")
            sw.write(self.md, "moss_first", end, end + 9 * H, salt="moss-b")             # after its end: fine
            with self.assertRaises(sw.Refused):                  # replacing that pending one does not wipe the past
                sw.write(self.md, "moss_first", start + H, start + 10 * H, salt="moss-c")
            sw.off(self.md, "moss_first")                        # off on the pending one: still known
            with self.assertRaises(sw.Refused):
                sw.write(self.md, "moss_first", start + H, start + 10 * H, salt="moss-c")
            sw.write(self.md, "moss_first", start + H, start + 10 * H, salt="moss-c", new_study=True)
        self.assertEqual(body(self.md, "moss_first")["salt"], "moss-c")

    def test_the_shadow_is_never_chained(self):
        # review 77cdbef M2: 12 h, off, --new-study 12 h gave a 24 h shadow.
        start = self.t + 600
        end = start + 12 * H
        sw.write(self.md, "final_m1", start, end)
        with at(start + 100):
            sw.off(self.md, "final_m1")
        with at(start + 200):
            with self.assertRaises(sw.Refused):
                sw.write(self.md, "final_m1", start + 300, start + 300 + 12 * H, new_study=True)
            with self.assertRaises(sw.Refused):
                sw.write(self.md, "final_m1", end + 60, end + 60 + H)
        with at(start + 200):
            sw.write(self.md, "final_m1", end + 60, end + 60 + H, new_study=True)
        self.assertEqual(body(self.md, "final_m1")["start"], end + 60)

    def test_a_window_written_by_hand_counts_once_it_ran(self):
        p = os.path.join(self.md, sw.STUDIES["our_ask"]["file"])
        with open(p, "w") as f:
            json.dump({"enabled": True, "salt": "hand", "start": self.t - 13 * H, "end": self.t - 4 * H}, f)
        with self.assertRaises(sw.Refused):
            sw.write(self.md, "our_ask", self.t + 600, self.t + 600 + 9 * H, salt="hand")
        sw.write(self.md, "our_ask", self.t + 600, self.t + 600 + 9 * H, salt="new")

    def test_a_window_by_hand_is_not_forgotten_by_off_or_overwriting(self):    # review 523fd38 M1
        p = os.path.join(self.md, sw.STUDIES["our_ask"]["file"])
        with open(p, "w") as f:
            json.dump({"enabled": True, "salt": "x", "start": self.t - 13 * H, "end": self.t - 4 * H}, f)
        sw.off(self.md, "our_ask")
        with self.assertRaises(sw.Refused):
            sw.write(self.md, "our_ask", self.t + 600, self.t + 600 + 9 * H, salt="x")
        with open(p, "w") as f:
            json.dump({"enabled": True, "salt": "x", "start": self.t - 13 * H, "end": self.t - 4 * H}, f)
        sw.write(self.md, "our_ask", self.t + 600, self.t + 600 + 9 * H, salt="y")
        with at(self.t + 600 + 10 * H):
            with self.assertRaises(sw.Refused):
                sw.write(self.md, "our_ask", self.t + 11 * H, self.t + 20 * H, salt="x")

    def test_the_stopped_record_stands_in_for_lost_history(self):              # review 523fd38 N3
        start = self.t + 600
        sw.write(self.md, "final_m1", start, start + 1800)
        with at(start + 60):
            sw.off(self.md, "final_m1")
        os.unlink(os.path.join(self.md, sw.HISTORY))
        with at(start + 120):
            with self.assertRaises(sw.Refused):
                sw.write(self.md, "final_m1", start + 600, start + 12 * H)
        with open(os.path.join(self.md, sw.STUDIES["final_m1"]["file"]), "w") as f:
            json.dump({"enabled": False}, f)                                   # history gone, nothing to go on
        with self.assertRaises(sw.Refused):
            sw.write(self.md, "final_m1", self.t + 13 * H, self.t + 14 * H)
        sw.write(self.md, "final_m1", self.t + 13 * H, self.t + 14 * H, new_study=True)   # said to be another

    def test_the_same_window_written_again_still_ran(self):                   # review 4eaf0b6 I1
        s = self.t + 600
        sw.write(self.md, "our_ask", s, s + 9 * H, salt="a")
        sw.write(self.md, "our_ask", s, s + 9 * H, salt="a")
        sw.write(self.md, "final_m1", s, s + 1800)
        sw.write(self.md, "final_m1", s, s + 1800)
        with at(s + 10 * H):
            with self.assertRaises(sw.Refused):
                sw.write(self.md, "our_ask", s + 11 * H, s + 20 * H, salt="a")
            with self.assertRaises(sw.Refused):
                sw.write(self.md, "final_m1", s + 11 * H, s + 12 * H)
        md2 = tempfile.mkdtemp()
        sw.write(md2, "our_ask", s, s + 9 * H, salt="a")
        sw.off(md2, "our_ask")                                                # stopped before it started
        sw.write(md2, "our_ask", s, s + 9 * H, salt="a")                    # then ran
        with at(s + 10 * H):
            with self.assertRaises(sw.Refused):
                sw.write(md2, "our_ask", s + 11 * H, s + 20 * H, salt="a")

    def test_a_stopped_record_is_carried_into_the_history(self):              # review 4eaf0b6 M1
        p = os.path.join(self.md, sw.STUDIES["our_ask"]["file"])
        with open(p, "w") as f:
            json.dump({"enabled": True, "salt": "x", "start": self.t - 13 * H, "end": self.t - 4 * H}, f)
        with unittest.mock.patch.object(sw, "_log", return_value=False):
            self.assertFalse(sw.off(self.md, "our_ask")["history"])
        sw.off(self.md, "our_ask")                                            # a second off keeps the record
        self.assertEqual(body(self.md, "our_ask")["stopped"]["salt"], "x")
        sw.write(self.md, "our_ask", self.t + 600, self.t + 600 + 9 * H, salt="y", new_study=True)
        with at(self.t + 10 * H):
            with self.assertRaises(sw.Refused):
                sw.write(self.md, "our_ask", self.t + 11 * H, self.t + 20 * H, salt="x")

    def test_an_unknown_past_needs_new_study(self):                           # review 4eaf0b6 N1
        p = os.path.join(self.md, sw.STUDIES["our_ask"]["file"])
        with open(p, "w") as f:
            json.dump({"enabled": False}, f)
        sw.off(self.md, "our_ask")                                            # an off knows no more than before
        with self.assertRaises(sw.Refused):
            sw.write(self.md, "our_ask", self.t + 600, self.t + 600 + 9 * H, salt="a")
        sw.write(self.md, "our_ask", self.t + 600, self.t + 600 + 9 * H, salt="a", new_study=True)
        md2 = tempfile.mkdtemp()                                               # nothing at all: a first use
        sw.write(md2, "our_ask", self.t + 600, self.t + 600 + 9 * H, salt="a")

    def test_a_salt_that_is_not_a_string_is_refused_not_a_crash(self):        # review 4eaf0b6 N2
        p = os.path.join(self.md, sw.STUDIES["our_ask"]["file"])
        with open(p, "w") as f:
            json.dump({"enabled": True, "salt": ["x"], "start": self.t - 13 * H, "end": self.t - 4 * H}, f)
        self.assertEqual(sw.main(["--market-dir", self.md, "our_ask", "--start", str(self.t + 600),
                                  "--end", str(self.t + 600 + 9 * H), "--salt", "b"]), 0)

    def test_a_window_about_to_start_is_not_overwritten(self):                 # review 523fd38 N1
        start = self.t + 600
        sw.write(self.md, "final_m1", start, start + 1800)
        with at(start - 2):
            with self.assertRaises(sw.Refused):
                sw.write(self.md, "final_m1", start + 60, start + 1800)

    def test_a_put_back_that_fails_leaves_the_switch_off(self):                # review 523fd38 M2
        sw.off(self.md, "final_m1")                                           # an old switch to put back
        real = sw._replace
        calls = []

        def replace(path, data):
            calls.append(data)
            if len(calls) > 1:
                raise OSError(28, "No space left on device")
            return real(path, data)
        with unittest.mock.patch.object(sw, "_log", return_value=False), \
                unittest.mock.patch.object(sw, "_replace", side_effect=replace):
            with self.assertRaises(sw.Refused):
                sw.write(self.md, "final_m1", self.t + 600, self.t + 1200)
        self.assertEqual(len(calls), 2)
        self.assertFalse(oneshot.final_m1_on(self.md, env={}, now=self.t + 700))
        with unittest.mock.patch.object(sw, "_replace", side_effect=OSError(28, "No space left on device")):
            self.assertEqual(sw.main(["--market-dir", self.md, "final_m1", "--start", str(self.t + 600),
                                      "--end", str(self.t + 1200)]), 2)

    def test_history_that_cannot_be_read_or_written_refuses(self):
        os.makedirs(os.path.join(self.md, sw.HISTORY))             # a directory where the file goes
        with self.assertRaises(sw.Refused):
            sw.write(self.md, "final_m1", self.t + 600, self.t + 1200)
        os.rmdir(os.path.join(self.md, sw.HISTORY))
        open(os.path.join(self.md, sw.HISTORY), "w").close()
        os.chmod(os.path.join(self.md, sw.HISTORY), 0o400)
        try:
            with self.assertRaises(sw.Refused):
                sw.write(self.md, "final_m1", self.t + 600, self.t + 1200)
        finally:
            os.chmod(os.path.join(self.md, sw.HISTORY), 0o600)
        self.assertFalse(os.path.exists(os.path.join(self.md, sw.STUDIES["final_m1"]["file"])))   # put back: none

    def test_off_always_writes_and_stops_at_once(self):
        start = self.t + 600
        sw.write(self.md, "final_m1", start, start + 1800)
        out = sw.off(self.md, "final_m1")
        self.assertIs(body(self.md, "final_m1")["enabled"], False)
        self.assertTrue(out["history"])
        self.assertFalse(oneshot.final_m1_on(self.md, env={}, now=start + 60))
        with self.assertRaises(sw.Refused):
            sw.off(self.md, "nope")

    def test_cli(self):
        start = self.t + 600
        self.assertEqual(sw.main(["--market-dir", self.md, "final_m1", "--start", str(start), "--end", str(start + 1800)]), 0)
        self.assertEqual(sw.main(["--market-dir", self.md, "final_m1", "--start", str(start + 60),
                                  "--end", str(start + 13 * H)]), 2)
        self.assertEqual(sw.main(["--market-dir", self.md, "final_m1", "--off"]), 0)
        self.assertIs(body(self.md, "final_m1")["enabled"], False)
        with self.assertRaises(SystemExit):        # no clock of one's own from the command line (review 7b17f3f I)
            sw.main(["--market-dir", self.md, "--now", "0", "final_m1", "--start", str(start), "--end", str(start + 60)])

    def test_the_real_clock_guards_a_running_window(self):
        p = os.path.join(self.md, sw.STUDIES["final_m1"]["file"])
        with open(p, "w") as f:
            json.dump({"enabled": True, "start": self.t - H, "end": self.t + 11 * H}, f)
        os.utime(p, (self.t - H - 1, self.t - H - 1))
        self.assertTrue(oneshot.final_m1_on(self.md, env={}))
        before = body(self.md, "final_m1")
        with self.assertRaises(sw.Refused):
            sw.write(self.md, "final_m1", self.t + 5, self.t + 5 + 12 * H, new_study=True)
        self.assertEqual(body(self.md, "final_m1"), before)

    def test_the_switch_file_is_readable_as_before(self):
        start = self.t + 600
        sw.write(self.md, "final_m1", start, start + 1800)
        self.assertEqual(os.stat(os.path.join(self.md, sw.STUDIES["final_m1"]["file"])).st_mode & 0o777, 0o644)

    def test_bounds_follow_the_studies(self):
        self.assertEqual((sw.STUDIES["moss_first"]["min_hours"], sw.STUDIES["moss_first"]["max_hours"]),
                         (mf.MIN_HOURS, mf.MAX_HOURS))
        self.assertEqual((sw.STUDIES["our_ask"]["min_hours"], sw.STUDIES["our_ask"]["max_hours"]),
                         (oa.MIN_HOURS, oa.MAX_HOURS))
        self.assertEqual(sw.STUDIES["final_m1"]["max_hours"], oneshot.FINAL_M1_MAX_HOURS)
        self.assertEqual({v["file"] for v in sw.STUDIES.values()}, {oneshot.FINAL_M1_SWITCH, mf.SWITCH, oa.SWITCH})


if __name__ == "__main__":
    unittest.main()
