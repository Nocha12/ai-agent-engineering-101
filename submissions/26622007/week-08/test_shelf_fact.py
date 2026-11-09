"""S-shelf (11/10): the exact food shelf-life question answered from the quoted unit's expiry. Real turns from the 11/10
00:25-01:05 ops log are in s_shelf_real.json (text, ask, unit and its units_in expiry, the harness answer, the end)."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import shelf_fact
import shelf_jev_skip

HERE = Path(__file__).parent
CASES = json.loads((HERE / "s_shelf_real.json").read_text())
Q = "How many hours of shelf life does this unit have left? It must last at least {} hours."


def config(**kw):
    c = dict(study="s-shelf-test", salt="t1", version=1, epoch="2026-11-10T06:10", enabled=True, mode="ab",
             share=0.5, start_at=1794230000.0, stop_at=1794230000.0 + 20 * 3600)
    c.update(kw)
    return c


def deal_of(case, **kw):
    d = dict(id=case["sku"], name=case["sku"], kind=case["kind"], unit=case["unit"], expires_at=case["expires_at"],
             reserve=case["ask"], ask=case["ask"], open=case["ask"], rounds=1, history=[("you asked", case["ask"])])
    d.update(kw)
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for k in ("SHELF_FACT_CONFIG", "SHELF_JEV_SKIP_CONFIG"):
            os.environ.pop(k, None)
        self.trial = shelf_fact.Trial(root=self.tmp.name)

    def on(self, **kw):
        self.trial.root.mkdir(parents=True, exist_ok=True)
        (self.trial.root / "config.json").write_text(json.dumps(config(**kw)))

    def r1_on(self, **kw):
        self.trial.r1.root.mkdir(parents=True, exist_ok=True)
        c = dict(config(study="r1", salt="r1"), **kw)
        (self.trial.r1.root / "config.json").write_text(json.dumps(c))

    def rows(self):
        p = self.trial.root / "events.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def arm_of(self, task, cfg=None):
        cfg = cfg or config()
        key = f"{cfg['salt']}|{cfg['study']}|{cfg['version']}|{task}".encode()
        return "B" if int.from_bytes(hashlib.sha256(key).digest(), "big") < cfg["share"] * (1 << 256) else "A"


class FixtureShape(Base):
    def test_real_turns(self):
        self.assertEqual(len(CASES), 8)
        for c in CASES:
            self.assertTrue(shelf_jev_skip.QUESTION.fullmatch(c["text"]), c["source"])
            self.assertEqual(c["kind"], "food")
            self.assertGreaterEqual((c["expires_at"] - c["ts"]) / 3600, c["required"])   # the unit could last
            self.assertEqual(round((c["expires_at"] - c["ts"]) / 3600, 2), c["left_h"])
        self.assertEqual(sum(c["harness_negative"] for c in CASES), 6)
        self.assertTrue(all("shelf life" in c["harness_message"] for c in CASES if c["harness_negative"]))


class Answer(Base):
    def test_off_by_default(self):
        c = CASES[0]
        self.assertIsNone(self.trial.answer(c["task"], c["text"], deal_of(c), now=c["ts"]))
        self.assertEqual(self.rows(), [])

    def test_real_turns_get_the_true_hours_in_b_and_nothing_in_a(self):
        self.on()
        for c in CASES:
            deal = deal_of(c)
            got = self.trial.answer(c["task"], c["text"], deal, now=c["ts"])
            arm = self.arm_of(c["task"])
            self.assertEqual(deal["shelf_fact"]["arm"], arm)
            if arm == "B":
                hours = int((c["expires_at"] - c["ts"]) / 36) / 100
                self.assertEqual(got, f"This unit has {hours:.2f} hours of shelf life remaining at this quote. "
                                      f"My price: {c['ask']}.")
            else:
                self.assertIsNone(got)
        rows = self.rows()
        self.assertEqual(len(rows), 8)
        self.assertEqual({r["arm"] for r in rows}, {"A", "B"})        # both arms in the real sample
        for r, c in zip(rows, CASES):
            self.assertEqual((r["event"], r["task"], r["unit"], r["required_hours"]), ("turn", c["task"], c["unit"],
                                                                                       c["required"]))
            self.assertEqual(r["answered"], r["arm"] == "B")
            self.assertEqual(r["randomization_unit"], "task")
            self.assertNotIn("text", r)                                # no customer text, only its hash
            self.assertEqual(r["input_sha256"], hashlib.sha256(c["text"].encode()).hexdigest())

    def test_the_arm_is_sticky_for_the_talk(self):
        self.on()
        c = CASES[0]
        deal = deal_of(c)
        self.trial.answer(c["task"], c["text"], deal, now=c["ts"])
        first = dict(deal["shelf_fact"])
        self.trial.answer(c["task"], Q.format(10), deal, now=c["ts"] + 60)
        self.assertEqual(deal["shelf_fact"], first)
        self.assertEqual([r["assigned_at"] for r in self.rows()], [c["ts"], c["ts"]])

    def test_not_enough_life_left_is_the_original_path_in_both_arms(self):
        self.on()
        for c in CASES:
            deal = deal_of(c)
            need = int(c["left_h"]) + 1
            self.assertIsNone(self.trial.answer(c["task"], Q.format(need), deal, now=c["ts"]))
            self.assertNotIn("shelf_fact", deal)
        self.assertEqual(self.rows(), [])

    def test_other_messages_goods_and_unknown_units_are_left_alone(self):
        self.on()
        c = CASES[0]
        for text, deal in ((c["text"] + " I can pay 9000.", deal_of(c)),
                           ("I can pay 9000. " + c["text"], deal_of(c)),
                           ("how many hours of shelf life does this unit have left? It must last at least 1 hours.",
                            deal_of(c)),
                           (c["text"], deal_of(c, kind="goods")),
                           (c["text"], deal_of(c, unit=None)),
                           (c["text"], deal_of(c, expires_at=None)),
                           (c["text"], deal_of(c, expires_at=float("nan"))),
                           (c["text"], deal_of(c, ask=0)),
                           (None, deal_of(c))):
            self.assertIsNone(self.trial.answer(c["task"], text, deal, now=c["ts"]))
            self.assertNotIn("shelf_fact", deal)
        self.assertEqual(self.rows(), [])

    def test_less_than_a_minute_left_is_not_answered(self):
        self.on()
        c = CASES[0]
        deal = deal_of(c, expires_at=c["ts"] + 59)
        self.assertIsNone(self.trial.answer(c["task"], Q.format(0), deal, now=c["ts"]))


class Window(Base):
    def test_outside_the_window_or_too_long_or_disabled_is_off(self):
        c = CASES[0]
        for kw in (dict(start_at=c["ts"] + 1), dict(stop_at=c["ts"]), dict(enabled=False),
                   dict(stop_at=1794230000.0 + 20 * 3600 + 1), dict(share=1.5), dict(mode="other"),
                   dict(start_at=c["ts"] + 10, stop_at=c["ts"] + 5)):
            self.on(**kw)
            deal = deal_of(c)
            self.assertIsNone(self.trial.answer(c["task"], c["text"], deal, now=c["ts"]), kw)
            self.assertNotIn("shelf_fact", deal, kw)
        self.assertEqual(self.rows(), [])

    def test_exactly_twenty_hours_is_allowed(self):
        self.on(share=1.0)
        c = CASES[0]
        self.assertIsNotNone(self.trial.answer(c["task"], c["text"], deal_of(c), now=c["ts"]))

    def test_invalid_or_non_regular_config_is_off(self):
        c = CASES[0]
        self.trial.root.mkdir(parents=True, exist_ok=True)
        (self.trial.root / "config.json").write_text("{not json")
        self.assertIsNone(self.trial.answer(c["task"], c["text"], deal_of(c), now=c["ts"]))
        (self.trial.root / "config.json").unlink()
        os.mkfifo(self.trial.root / "config.json")
        self.assertIsNone(self.trial.answer(c["task"], c["text"], deal_of(c), now=c["ts"]))
        self.assertEqual(self.rows(), [])

    def test_adopted_answers_every_eligible_turn(self):
        self.on(mode="adopted")
        for c in CASES:
            self.assertIsNotNone(self.trial.answer(c["task"], c["text"], deal_of(c), now=c["ts"]))
        self.assertEqual({r["arm"] for r in self.rows()}, {"adopted"})


class R1Protection(Base):
    def test_never_on_together_with_r1(self):
        self.on(share=1.0)
        self.r1_on()
        for c in CASES:
            deal = deal_of(c)
            self.assertIsNone(self.trial.answer(c["task"], c["text"], deal, now=c["ts"]))
            self.assertNotIn("shelf_fact", deal)
        self.assertEqual(self.rows(), [])

    def test_r1_ended_or_disabled_lets_s_shelf_answer(self):
        c = CASES[0]
        self.on(share=1.0)
        self.r1_on(stop_at=c["ts"] - 1)
        self.assertIsNotNone(self.trial.answer(c["task"], c["text"], deal_of(c), now=c["ts"]))
        self.r1_on(enabled=False)
        self.assertIsNotNone(self.trial.answer(c["task"], c["text"], deal_of(c), now=c["ts"] + 1))

    def test_r1_assignment_is_kept_on_the_row(self):
        self.on(share=1.0)
        c = CASES[0]
        deal = deal_of(c, shelf_jev=dict(study="r1", version=1, arm="B", mode="ab"))
        self.trial.answer(c["task"], c["text"], deal, now=c["ts"])
        self.assertEqual(self.rows()[0]["others"]["shelf_jev"], dict(study="r1", version=1, arm="B"))   # OTHER_FIELDS


class Journal(Base):
    def test_a_failed_write_keeps_the_original_path(self):
        self.on(share=1.0)
        c = CASES[0]
        deal = deal_of(c)
        with mock.patch.object(self.trial, "_write", side_effect=OSError("disk")):
            self.assertIsNone(self.trial.answer(c["task"], c["text"], deal, now=c["ts"]))
        self.assertNotIn("shelf_fact", deal)

    def test_outcome_rows_for_assigned_talks_only(self):
        self.on()
        c = CASES[0]
        deal = deal_of(c)
        self.trial.event(c["task"], deal, "canceled", None, now=c["ts"] + 5)
        self.assertEqual(self.rows(), [])
        self.trial.answer(c["task"], c["text"], deal, now=c["ts"])
        self.trial.event(c["task"], deal, "deal", c["ask"], now=c["ts"] + 5)
        last = self.rows()[-1]
        self.assertEqual((last["event"], last["task"], last["price"], last["arm"]),
                         ("deal", c["task"], c["ask"], deal["shelf_fact"]["arm"]))

    def test_event_never_raises(self):
        self.on()
        c = CASES[0]
        deal = deal_of(c)
        self.trial.answer(c["task"], c["text"], deal, now=c["ts"])
        with mock.patch.object(self.trial, "_write", side_effect=OSError("disk")):
            self.trial.event(c["task"], deal, "deal", 1)

    def test_answer_never_raises(self):
        self.on()
        c = CASES[0]
        with mock.patch.object(shelf_fact, "factual_answer", side_effect=RuntimeError("x")):
            self.assertIsNone(self.trial.answer(c["task"], c["text"], deal_of(c), now=c["ts"]))


class SellerWiring(unittest.TestCase):
    def test_seller_asks_s_shelf_only_when_the_old_shelf_trial_did_not_answer(self):
        src = (HERE / "seller.py").read_text()
        i = src.index("shelf_text = SHELF_REPLY.reply(text, deal)")
        self.assertIn("if shelf_text is None:\n            shelf_text = SHELF_FACT.answer(task.id, text, deal)",
                      src[i:i + 200])
        self.assertIn("SHELF_FACT = shelf_fact.Trial(r1=SHELF_JEV)", src)
        self.assertIn("SHELF_FACT.event(task_id, deal, result, price, observed_at)", src)


if __name__ == "__main__":
    unittest.main()
