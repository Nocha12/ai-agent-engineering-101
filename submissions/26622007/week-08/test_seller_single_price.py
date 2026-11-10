"""Public outgoing wording seam; all state isolated in TemporaryDirectory."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import negotiate
import seller_single_price as sp

class SinglePriceTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.r=sp.SinglePrice(self.root)
        self.cfg=sp.trial_config("single-price-test",100,1900,"seed")
        self.r.root.mkdir(parents=True)
        self.save(self.cfg)
    def save(self,c):
        (self.r.root/"config.json").write_text(json.dumps(c))
    def task(self,arm):
        for i in range(100):
            t=str(i)
            if sp.task_arm(t,self.cfg)==arm:return t
        self.fail("no arm")
    def rows(self):
        return [json.loads(l) for l in (self.r.root/"trace.jsonl").read_text().splitlines()]
    def test_exact_echo_only_price_and_final_stance_preserved(self):
        before="Thanks for your offer of 9000. This is already the lowest price we can sell it for. This is our final price. My price: 10000."
        after="Thanks for your offer. This is already the lowest price we can sell it for. This is our final price. My price: 10000."
        self.assertEqual(self.r.render(self.task("B"),before,10000,now=100),after)
        self.assertEqual(self.r.render(self.task("A"),before,10000,now=101),before)
    def test_actual_generated_lines_have_same_decision_price_and_history(self):
        for source in (negotiate.LINES,negotiate.HOLD,negotiate.FINAL):
            for line in source:
                with self.subTest(line=line):
                    deal=dict(reserve=10000,ask=15000,rounds=1,history=[("you asked",15000)])
                    original=copy.deepcopy(deal)
                    floor=16000 if source is negotiate.HOLD else 10000
                    with mock.patch.object(negotiate,"counter_price",return_value=15000 if source is negotiate.HOLD else (10000 if source is negotiate.FINAL else 13000)),mock.patch.object(negotiate.random,"choice",return_value=line):
                        d=negotiate.seller_turn("9000",deal,floor)
                    self.assertEqual(deal,original)
                    p=d["price"];suffix=" This is our final price." if p<=deal["reserve"] else ""
                    text=d["message"]+suffix+f" My price: {p}."
                    b=self.r.render(self.task("B"),text,p,now=110)
                    self.assertNotIn("9000",b)
                    self.assertTrue(b.endswith(f"My price: {p}."))
                    self.assertEqual(suffix in b, suffix in text)
                    self.assertEqual(d["action"],"counter")
                    self.assertEqual(d["buyer_offer"],9000)
    def test_nonallowed_facts_fair_questions_and_rejects_are_unchanged(self):
        examples=[
            "Thanks for your offer of 9000. I can come down a little. Warranty: 6 hours. My price: 10000.",
            "Thanks for your offer of 9000. I can come down a little. Certificate attached. My price: 10000.",
            "Thanks for your offer of 9000. I can come down a little. My price: 10001.",
            "Thank you for your offer of 9000. We are so sorry we could not meet your budget this time! No deal.",
            "Please tell me the price you offer. My price: 10000.",
            "You offered more than our price. Our price is only 10000; send it to close the deal. My price: 10000.",
            "Thanks for your offer of 9000. I can come down a little. Delivery tomorrow? My price: 10000."]
        for s in examples:self.assertEqual(self.r.render(self.task("B"),s,10000,now=120),s)
        self.assertTrue(all(r["effective_arm"]=="not-exposed" for r in self.rows()))
    def test_render_clock_boundaries_and_late_b_control(self):
        t=self.task("B");s="I appreciate the offer of 9000, but this price stays. My price: 10000."
        for now in (99,1900,2000):
            self.assertEqual(self.r.render(t,s,10000,now=now),s)
        self.assertNotEqual(self.r.render(t,s,10000,now=100),s)
        self.assertNotEqual(self.r.render(t,s,10000,now=1899.9),s)
        self.assertEqual(self.r.render(t,s,10000,now=1900),s)
        self.assertEqual(self.rows()[-1]["itt_arm"],"B")
        self.assertEqual(self.rows()[-1]["effective_arm"],"A")
        (self.r.root/"disabled").touch()
        self.assertEqual(self.r.render(t,s,10000,now=101),s)
    def test_invalid_missing_future_and_config_changes_fail_closed(self):
        s="Thank you for 9000. I can meet you partway. My price: 10000.";t=self.task("B")
        self.assertNotEqual(self.r.render(t,s,10000,now=100),s)
        for c in ({},dict(self.cfg,enabled=False),dict(self.cfg,start=200,end=2000),dict(self.cfg,salt="changed"),dict(self.cfg,end=1901),dict(self.cfg,start=float("nan")),dict(self.cfg,start=True)):
            self.save(c)
            self.assertEqual(self.r.render(t,s,10000,now=101),s)
        (self.r.root/"config.json").unlink()
        self.assertEqual(self.r.render(t,s,10000,now=101),s)
        self.save(self.cfg)
        for now in (None,float("nan"),float("inf"),True):
            if now is None:continue
            self.assertEqual(self.r.render(t,s,10000,now=now),s)
        self.assertEqual(self.r.render(t,s,10000,now=99),s)
    def test_all_itt_immutable_no_legacy_cap_and_hash_only_trace(self):
        s="Opening quote with warranty facts. My price: 10000."
        for i in range(250):
            self.assertEqual(self.r.render(f"task-{i}",s,10000,now=110),s)
        rows=self.rows()
        self.assertEqual(len(rows),250)
        self.assertTrue(all(r["itt_arm"] in ("A","B") and not r["eligible"] for r in rows))
        b="Thank you for 9000. I can meet you partway. My price: 10000."
        self.r.render("task-249",b,10000,now=120)
        self.assertEqual(self.rows()[-1]["itt_arm"],rows[-1]["itt_arm"])
        raw=(self.r.root/"trace.jsonl").read_text()
        self.assertNotIn("task-249",raw);self.assertNotIn("warranty",raw);self.assertNotIn("Thank you",raw)
    def test_sink_and_db_failures_never_change_response_or_propagate(self):
        t=self.task("B");s="Thank you for 9000. I can meet you partway. My price: 10000."
        with mock.patch.object(self.r,"record",side_effect=OSError("sink")):
            self.assertEqual(self.r.render(t,s,10000,now=100),s)
        with mock.patch.object(sp.sqlite3,"connect",side_effect=OSError("db")):
            self.assertEqual(self.r.render(t,s,10000,now=100),s)
    def test_epoch_reuse_new_tasks_and_db_cleanup_fail_closed(self):
        s="Thank you for 9000. I can meet you partway. My price: 10000."
        self.assertNotEqual(self.r.render(self.task("B"),s,10000,now=100),s)
        self.save(dict(self.cfg,start=200,end=2000))
        self.assertEqual(self.r.render("new-task-after-epoch-edit",s,10000,now=300),s)
        self.save(self.cfg)
        real=sp.sqlite3.connect
        class BadClose:
            def __init__(self,*a,**k):self.db=real(*a,**k)
            def __enter__(self):return self
            def __exit__(self,*args):return self.db.__exit__(*args)
            def __getattr__(self,k):return getattr(self.db,k)
            def close(self):
                self.db.close()
                raise OSError("cleanup")
        with mock.patch.object(sp.sqlite3,"connect",side_effect=BadClose):
            self.assertEqual(self.r.render(self.task("B"),s,10000,now=110),s)
    def test_production_clock_rechecked_after_enrollment_crosses_end(self):
        s="Thank you for 9000. I can meet you partway. My price: 10000."
        with mock.patch.object(sp.time,"time",side_effect=[1899,1900]):
            self.assertEqual(self.r.render(self.task("B"),s,10000),s)
        self.assertEqual(self.rows()[-1]["effective_arm"],"A")
    def test_config_builder_requires_fresh_finite_1800_window(self):
        for args in (("bad/id",100,1900,"s"),("ok",100,1901,"s"),("ok",float("nan"),1900,"s"),("ok",100,1900,"")):
            with self.assertRaises(ValueError):sp.trial_config(*args)
