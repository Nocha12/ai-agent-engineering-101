"""buyer_our_ask before its first ON: a fixed 9-12 h window written before it starts, arm B stopped mid-order when the
switch changes, and the assignment and every read journaled when they happen (GPT Pro 0310 critique, W-20261109-2225 cycle 9)."""
import hashlib
import json
import os
import tempfile
import unittest

import buyer_our_ask as oa
import market_buyer

NOW = 1_794_300_000.0


def write(md, mtime=None, **kw):
    """The switch, its file dated before its start (mtime: start - 1 unless given)."""
    d = {"enabled": True, "salt": "our-ask-1110", "start": NOW - 60, "end": NOW - 60 + 9 * 3600, **kw}
    p = os.path.join(md, oa.SWITCH)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f)
    st = d["start"]
    m = mtime if mtime is not None else (st - 1 if isinstance(st, (int, float)) and not isinstance(st, bool)
                                         and 1 <= st < 1e11 else 1)
    os.utime(p, (m, m))


def arm_of(order, salt="our-ask-1110"):
    return "AB"[hashlib.sha256(("buyer_our_ask:" + salt + ":" + order).encode()).digest()[0] % 2]


def order_in(arm):
    for i in range(200):
        o = f"o-{i:012x}"
        if arm_of(o) == arm:
            return o
    raise AssertionError


def events(md):
    p = os.path.join(md, oa.JOURNAL)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(l) for l in f]


class Window(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.mkdtemp()

    def test_window_must_be_fixed_9_to_12_hours(self):
        for start, end in ((NOW - 60, NOW + 3600),                  # 1 h: under the 9 h minimum
                           (NOW - 60, NOW - 60 + 9 * 3600 - 1),
                           (NOW - 60, NOW - 60 + 12 * 3600 + 1),    # over the 12 h maximum (13 h was the old cap)
                           (NOW - 60, NOW - 60 + 13 * 3600),
                           (NOW + 60, NOW + 60 + 9 * 3600),         # not started
                           (NOW - 10 * 3600, NOW - 60),             # over
                           (NOW - 60, float("inf")), (True, NOW + 9 * 3600), (NOW - 60, 10 ** 400)):
            write(self.md, start=start, end=end)
            self.assertIsNone(oa.assign(self.md, {"order": "o-1"}, now=NOW), (start, end))
        for hours in (9, 12):
            write(self.md, start=NOW - 60, end=NOW - 60 + hours * 3600)
            self.assertIsNotNone(oa.assign(self.md, {"order": "o-1"}, now=NOW), hours)

    def test_written_after_its_start_is_off(self):
        write(self.md, mtime=NOW - 30)            # start NOW - 60: the file came after the window began
        self.assertIsNone(oa.assign(self.md, {"order": "o-1"}, now=NOW))
        write(self.md, mtime=NOW - 60)
        self.assertEqual(oa.assign(self.md, {"order": "o-1"}, now=NOW)["written"], NOW - 60)

    def test_order_id_checked(self):
        write(self.md)
        for oid in ("\ud800", "o 1", "o-" + "x" * 95, None, 5, ""):
            self.assertIsNone(oa.assign(self.md, {"order": oid}, now=NOW), repr(oid))

    def test_arm_by_hash_and_both_arms(self):
        write(self.md)
        for arm in "AB":
            o = order_in(arm)
            self.assertEqual(oa.assign(self.md, {"order": o}, now=NOW)["arm"], arm)
        self.assertEqual({oa.assign(self.md, {"order": f"o-{i}"}, now=NOW)["arm"] for i in range(40)}, {"A", "B"})


class Live(unittest.TestCase):
    """reads(): arm B reads only while the switch still says the same window (the file is read again each time)."""

    def setUp(self):
        self.md = tempfile.mkdtemp()
        write(self.md)
        self.b = oa.assign(self.md, {"order": order_in("B")}, now=NOW)

    def test_arm_b_reads_inside_the_window(self):
        self.assertTrue(oa.reads(self.b, now=NOW))
        self.assertFalse(oa.reads(oa.assign(self.md, {"order": order_in("A")}, now=NOW), now=NOW))

    def test_switch_off_or_gone_mid_order_stops_arm_b(self):
        write(self.md, enabled=False)
        self.assertFalse(oa.reads(self.b, now=NOW))
        os.remove(os.path.join(self.md, oa.SWITCH))
        self.assertFalse(oa.reads(self.b, now=NOW))

    def test_switch_changed_or_window_over_stops_arm_b(self):
        write(self.md, salt="other")
        self.assertFalse(oa.reads(self.b, now=NOW))
        write(self.md, end=NOW - 60 + 10 * 3600)       # another window, same salt
        self.assertFalse(oa.reads(self.b, now=NOW))
        write(self.md)
        self.assertTrue(oa.reads(self.b, now=NOW))
        self.assertFalse(oa.reads(self.b, now=NOW + 9 * 3600))

    def test_a_context_without_its_market_dir_reads_none(self):
        for ctx in (dict(self.b, md=None), {k: v for k, v in self.b.items() if k != "md"}, None, "B",
                    dict(self.b, md=tempfile.mkdtemp())):
            self.assertFalse(oa.reads(ctx, now=NOW), ctx)


class Journal(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.mkdtemp()
        write(self.md)

    def test_assignment_journaled_when_it_is_made(self):
        o = order_in("B")
        oa.assign(self.md, {"order": o}, now=NOW)
        (e,) = events(self.md)
        self.assertEqual({k: e[k] for k in ("k", "order", "arm", "salt", "start", "end", "started_at")},
                         {"k": "assign", "order": o, "arm": "B", "salt": "our-ask-1110", "start": NOW - 60,
                          "end": NOW - 60 + 9 * 3600, "started_at": NOW})
        self.assertEqual(e["written"], NOW - 61)

    def test_read_journaled_and_kept(self):
        ctx = oa.assign(self.md, {"order": order_in("B")}, now=NOW)
        oa.applied(ctx, "kimauve", 29617)
        self.assertEqual(ctx["applied"], [{"shop": "kimauve", "ask": 29617}])
        self.assertEqual([(e["k"], e.get("shop"), e.get("ask"), e["arm"]) for e in events(self.md)],
                         [("assign", None, None, "B"), ("applied", "kimauve", 29617, "B")])

    def test_off_journals_nothing(self):
        write(self.md, enabled=False)
        self.assertIsNone(oa.assign(self.md, {"order": "o-1"}, now=NOW))
        self.assertEqual(events(self.md), [])

    def test_a_journal_that_cannot_be_written_changes_nothing(self):
        os.makedirs(os.path.join(self.md, os.path.dirname(oa.JOURNAL)))
        os.chmod(os.path.join(self.md, os.path.dirname(oa.JOURNAL)), 0o500)
        try:
            ctx = oa.assign(self.md, {"order": order_in("B")}, now=NOW)
            self.assertEqual(ctx["arm"], "B")
            oa.applied(ctx, "kimauve", 1)
            self.assertEqual(ctx["applied"], [{"shop": "kimauve", "ask": 1}])
        finally:
            os.chmod(os.path.join(self.md, os.path.dirname(oa.JOURNAL)), 0o700)

    def test_measurement_has_no_market_dir(self):
        ctx = oa.assign(self.md, {"order": order_in("B")}, now=NOW)
        self.assertEqual(oa.measurement(ctx), {"salt": "our-ask-1110", "start": NOW - 60, "end": NOW - 60 + 9 * 3600,
                                               "written": NOW - 61, "arm": "B", "applied": []})
        self.assertIsNone(oa.measurement(None))


class Wired(unittest.TestCase):
    def test_quote_reads_the_switch_and_journals_outside_its_lock(self):
        import inspect
        src = inspect.getsource(market_buyer.quote)
        lock_at = src.index("with lock if lock is not None")
        self.assertLess(src.index('buyer_our_ask.reads(order.get("_our_ask"))'), lock_at)
        self.assertGreater(src.index('our_ask_read = buyer_our_ask.note(order["_our_ask"], shop["name"], rank_ask)'), lock_at)
        self.assertGreater(src.index('buyer_our_ask.journal(order["_our_ask"], our_ask_read)'),
                           src.index('out[shop["name"]] = dict(q, closed=True) if late else q'))
        self.assertNotIn('order["_our_ask"]["applied"].append', src)

    def test_note_does_no_file_io(self):
        md = tempfile.mkdtemp()
        write(md)
        ctx = oa.assign(md, {"order": order_in("B")}, now=NOW)
        n = len(events(md))
        read = oa.note(ctx, "kimauve", 5)
        self.assertEqual((read, ctx["applied"], len(events(md))), ({"shop": "kimauve", "ask": 5}, [read], n))
        oa.journal(ctx, read)
        self.assertEqual(events(md)[-1]["k"], "applied")

    def test_registered_with_the_meta_harness_switches(self):
        import meta_harness
        self.assertIn(".market/buyer_our_ask.json", meta_harness.SWITCHES)


if __name__ == "__main__":
    unittest.main()
