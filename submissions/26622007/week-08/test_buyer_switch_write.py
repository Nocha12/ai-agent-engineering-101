"""buyer_switch_write: the only way the windowed buyer switches are written — a finite window inside each study's bounds,
written before its start, never over a window already running (ax-pro-cycle step 5, main instruction 10)."""
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

NOW = float(int(time.time()))      # the file's mtime is the real clock: windows here start after it


def body(md, name):
    with open(os.path.join(md, sw.STUDIES[name]["file"]), encoding="utf-8") as f:
        return json.load(f)


class Write(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.mkdtemp()

    def test_each_study_reads_on_at_its_start_what_it_writes(self):
        for name, hours, salt in (("final_m1", 0.5, None), ("moss_first", 12, "moss-t"), ("our_ask", 9, "our-ask-t")):
            start = NOW + 600
            out = sw.write(self.md, name, start, start + hours * 3600, salt=salt, now=NOW)
            self.assertLessEqual(out["written"], start, name)
            self.assertEqual(body(self.md, name), dict({"enabled": True, "start": start, "end": start + hours * 3600},
                                                       **({"salt": salt} if salt else {})), name)
        self.assertTrue(oneshot.final_m1_on(self.md, env={}, now=NOW + 600))
        self.assertFalse(oneshot.final_m1_on(self.md, env={}, now=NOW))           # not started yet
        self.assertIsNotNone(mf.assign(self.md, {"order": "o-1"}, now=NOW + 600))
        self.assertIsNotNone(oa.assign(self.md, {"order": "o-1"}, now=NOW + 600))

    def test_refused_windows_write_nothing(self):
        start = NOW + 600
        for name, s, e, salt in (("final_m1", start, start, None),                       # empty
                                 ("final_m1", start, start + 12 * 3600 + 1, None),       # over 12 h
                                 ("final_m1", NOW - 1, NOW + 1800, None),                # already started
                                 ("final_m1", NOW, NOW + 1800, None),                    # starts now: not before
                                 ("final_m1", start, float("inf"), None),
                                 ("final_m1", float("nan"), start, None),
                                 ("final_m1", True, start, None),
                                 ("moss_first", start, start + 9 * 3600 - 1, "m"),        # under 9 h
                                 ("moss_first", start, start + 13 * 3600, "m"),
                                 ("moss_first", start, start + 9 * 3600, None),           # salt needed
                                 ("our_ask", start, start + 9 * 3600, "bad salt"),
                                 ("final_m1", start, start + 1800, "s"),                  # no salt for M1
                                 ("nope", start, start + 1800, None)):
            with self.assertRaises(sw.Refused, msg=(name, s, e, salt)):
                sw.write(self.md, name, s, e, salt=salt, now=NOW)
        self.assertEqual(os.listdir(self.md), [])

    def test_a_running_window_is_never_rewritten_or_moved(self):
        start = NOW + 600
        sw.write(self.md, "moss_first", start, start + 9 * 3600, salt="moss-t", now=NOW)
        before = body(self.md, "moss_first")
        for now in (start, start + 3600):                     # running: no new window, no later end
            with self.assertRaises(sw.Refused):
                sw.write(self.md, "moss_first", now + 60, now + 60 + 12 * 3600, salt="moss-t2", now=now)
        self.assertEqual(body(self.md, "moss_first"), before)
        # a window not yet started can be replaced by another future one; one that is over can be followed
        sw.write(self.md, "moss_first", start + 60, start + 60 + 9 * 3600, salt="moss-t3", now=NOW)
        later = start + 60 + 9 * 3600
        sw.write(self.md, "moss_first", later + 600, later + 600 + 9 * 3600, salt="moss-t4", now=later)
        self.assertEqual(body(self.md, "moss_first")["salt"], "moss-t4")
        p = os.path.join(self.md, sw.STUDIES["moss_first"]["file"])
        sw.off(self.md, "moss_first")                        # stopped before it started: the next can come at once
        sw.write(self.md, "moss_first", NOW + 120, NOW + 120 + 9 * 3600, salt="moss-t5", now=NOW)
        self.assertTrue(os.path.exists(p))

    def test_off_always_writes_and_stops_at_once(self):
        start = NOW + 600
        sw.write(self.md, "final_m1", start, start + 1800, now=NOW)
        sw.off(self.md, "final_m1")
        self.assertIs(body(self.md, "final_m1")["enabled"], False)
        self.assertEqual(body(self.md, "final_m1")["stopped"]["end"], start + 1800)
        self.assertFalse(oneshot.final_m1_on(self.md, env={}, now=start + 60))
        sw.off(self.md, "final_m1")                          # off again keeps what it stopped
        self.assertEqual(body(self.md, "final_m1")["stopped"]["end"], start + 1800)
        with self.assertRaises(sw.Refused):
            sw.off(self.md, "nope")

    def test_cli(self):
        start = time.time() + 600
        self.assertEqual(sw.main(["--market-dir", self.md, "final_m1", "--start", str(start), "--end", str(start + 1800)]), 0)
        self.assertEqual(sw.main(["--market-dir", self.md, "final_m1", "--start", str(start + 60),
                                  "--end", str(start + 13 * 3600)]), 2)
        self.assertEqual(sw.main(["--market-dir", self.md, "final_m1", "--off"]), 0)
        self.assertIs(body(self.md, "final_m1")["enabled"], False)
        with self.assertRaises(SystemExit):        # no clock of one's own from the command line (review 7b17f3f I)
            sw.main(["--market-dir", self.md, "--now", "0", "final_m1", "--start", str(start), "--end", str(start + 60)])

    def test_the_real_clock_guards_a_running_window(self):
        # review 7b17f3f I: a now in the past let a running window be replaced by a later, longer one.
        real = time.time()
        p = os.path.join(self.md, sw.STUDIES["final_m1"]["file"])
        with open(p, "w") as f:
            json.dump({"enabled": True, "start": real - 3600, "end": real + 11 * 3600}, f)
        os.utime(p, (real - 3601, real - 3601))
        self.assertTrue(oneshot.final_m1_on(self.md, env={}))
        before = body(self.md, "final_m1")
        for now in (0, real - 7200):
            with self.assertRaises(sw.Refused, msg=now):
                sw.write(self.md, "final_m1", real + 5, real + 5 + 12 * 3600, now=now)
        self.assertEqual(body(self.md, "final_m1"), before)

    def test_off_keeps_the_stopped_window_and_a_restart_needs_a_new_study(self):
        # review 7b17f3f M1: off then the same study again at once would chain windows past the original end.
        start = NOW + 600
        sw.write(self.md, "moss_first", start, start + 9 * 3600, salt="moss-t", now=NOW)
        with unittest.mock.patch.object(sw.time, "time", return_value=start + 60):    # stopped while it ran
            sw.off(self.md, "moss_first")
        b = body(self.md, "moss_first")
        self.assertEqual((b["enabled"], b["stopped"]["salt"], b["stopped"]["end"]), (False, "moss-t", start + 9 * 3600))
        with self.assertRaises(sw.Refused):
            sw.write(self.md, "moss_first", start + 3600, start + 3600 + 9 * 3600, salt="moss-t", now=NOW)
        with self.assertRaises(sw.Refused):         # a new salt alone is not enough before the old end
            sw.write(self.md, "moss_first", start + 3600, start + 3600 + 9 * 3600, salt="moss-t2", now=NOW)
        sw.write(self.md, "moss_first", start + 3600, start + 3600 + 9 * 3600, salt="moss-t2", now=NOW, new_study=True)
        self.assertEqual(body(self.md, "moss_first")["salt"], "moss-t2")
        with unittest.mock.patch.object(sw.time, "time", return_value=start + 3700):
            sw.off(self.md, "moss_first")
        later = start + 3600 + 9 * 3600
        sw.write(self.md, "moss_first", later, later + 9 * 3600, salt="moss-t3", now=NOW)    # after the old end: fine

    def test_the_switch_file_is_readable_as_before(self):
        start = NOW + 600
        sw.write(self.md, "final_m1", start, start + 1800, now=NOW)
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
