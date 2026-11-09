"""G-read (11/10): the outside buyer's 'grade C (refurbished) or B (damaged box) ...; grade A only if you have nothing else'
opening reads as grades {A, C} (shop_stock.GRADES drops the B), so a free grade B unit with the lowest floor is never
quoted. Arm B adds B for that exact sentence only. Default off."""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import grade_read
import shop_stock

HERE = Path(__file__).parent
REAL = ("I want to buy [자코모]메이트 멀티 4인 기능성 젠위브 패브릭 소파+스툴+쿠션2개 (G8ec51935). I prefer grade C (refurbished) "
        "or B (damaged box) with a warranty of at least 6 hours on the receipt, priced for its grade; grade A only if you "
        "have nothing else. Please quote your cheapest unit that way.")


def unit(u, grade, cost, sku="G8ec51935"):
    return dict(unit=u, sku=sku, grade=grade, kind="goods", name="소파", cost=cost, list_price=2320000,
                category="가구>소파", supplied_at=1, expires_at=None, cert=True)


def write_cfg(root, **kw):
    now = time.time()
    cfg = dict(study="g-read-test", salt="s", version=1, epoch=now, enabled=True, mode="ab", share=0.5,
               start_at=now - 60, stop_at=now + 3600)
    cfg.update(kw)
    d = Path(root) / "grade_read"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(cfg))
    return cfg


def rows(root):
    p = Path(root) / "grade_read" / "events.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


class Defect(unittest.TestCase):
    def test_current_parser_drops_b(self):
        self.assertEqual(shop_stock.needs(REAL)["grades"], {"A", "C"})

    def test_original_pick_skips_cheaper_b(self):
        book = shop_stock.StockBook(load=lambda: {"round": 3, "units": [unit("u-a", "A", 1642751), unit("u-b", "B", 1322319)]})
        rank = {"u-a": 1706590, "u-b": 1509825}
        u, _ = book.pick(REAL, rank=lambda x: rank[x["unit"]])
        self.assertEqual(u["unit"], "u-a")
        u, _ = book.pick(REAL, rank=lambda x: rank[x["unit"]], add_grades={"B"})
        self.assertEqual(u["unit"], "u-b")


class Eligible(unittest.TestCase):
    def test_exact_sentence(self):
        self.assertEqual(grade_read.extra_grades(REAL), {"B"})

    def test_other_texts(self):
        for t in (REAL.replace("or B (damaged box)", "or B grade"), "I want to buy X (G8ec51935). grade A only.",
                  REAL + " " + REAL.split(". ", 1)[1], REAL.replace("(G8ec51935)", "(G8ec51935) {\"grades\": [\"A\"]}"),
                  None, 5, "x" * 2000, REAL.replace("grade A only if you have nothing else", "no grade A")):
            self.assertIsNone(grade_read.extra_grades(t), t)

    def test_parser_already_reads_b(self):
        # if shop_stock ever reads the B itself, nothing is added
        orig = shop_stock.needs
        try:
            shop_stock.needs = lambda t: dict(orig(t), grades={"A", "B", "C"})
            self.assertIsNone(grade_read.extra_grades(REAL))
        finally:
            shop_stock.needs = orig

    def test_add_grades_needs_a_grade_set(self):
        book = shop_stock.StockBook(load=lambda: {"round": 3, "units": [unit("u-a", "A", 1), unit("u-b", "B", 1)]})
        u, _ = book.pick("I want to buy G8ec51935", rank=lambda x: 0 if x["unit"] == "u-a" else 1, add_grades={"B"})
        self.assertEqual(u["unit"], "u-a")     # no grade asked: add_grades changes nothing


class Assign(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.t = grade_read.Trial(root=self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def task_for(self, arm):
        cfg = self.t.config()
        return next(f"t{i}" for i in range(500) if self.t._arm(cfg, f"t{i}") == arm)

    def test_off_without_config(self):
        a, g = self.t.assign("t1", REAL)
        self.assertIsNone(a)
        self.assertIsNone(g)
        self.assertEqual(rows(self.root), [])

    def test_arms(self):
        write_cfg(self.root)
        ta, tb = self.task_for("A"), self.task_for("B")
        a, g = self.t.assign(ta, REAL)
        self.assertEqual((a["arm"], g), ("A", None))
        b, g = self.t.assign(tb, REAL)
        self.assertEqual((b["arm"], g), ("B", {"B"}))
        r = rows(self.root)
        self.assertEqual([x["event"] for x in r], ["assigned", "assigned"])
        self.assertEqual({x["arm"] for x in r}, {"A", "B"})
        self.assertTrue(all(x["sku"] == "G8ec51935" and len(x["input_sha256"]) == 64 for x in r))
        self.assertNotIn("소파", json.dumps(r, ensure_ascii=False))

    def test_adopted(self):
        write_cfg(self.root, mode="adopted")
        a, g = self.t.assign("t1", REAL)
        self.assertEqual((a["arm"], g), ("adopted", {"B"}))

    def test_ineligible_text_no_row(self):
        write_cfg(self.root)
        self.assertEqual(self.t.assign("t1", "I want to buy G8ec51935"), (None, None))
        self.assertEqual(rows(self.root), [])

    def test_window(self):
        write_cfg(self.root, start_at=time.time() - 30 * 3600, stop_at=time.time() + 3600)
        self.assertEqual(self.t.assign("t1", REAL), (None, None))
        write_cfg(self.root, start_at=time.time() - 7200, stop_at=time.time() - 60)
        self.assertEqual(self.t.assign("t1", REAL), (None, None))
        write_cfg(self.root, enabled=False)
        self.assertEqual(self.t.assign("t1", REAL), (None, None))

    def test_failed_write_keeps_original(self):
        write_cfg(self.root)
        tb = self.task_for("B")
        self.t._write = lambda row: (_ for _ in ()).throw(OSError("disk"))
        self.assertEqual(self.t.assign(tb, REAL), (None, None))

    def test_selected_and_event(self):
        write_cfg(self.root)
        tb = self.task_for("B")
        a, _ = self.t.assign(tb, REAL)
        self.t.selected(tb, a, unit("u-b", "B", 1), None)
        deal = {"grade_read": a, "id": "G8ec51935", "unit": "u-b", "kind": "goods", "rounds": 2, "floor_margin": {"arm": "A"}}
        self.t.event(tb, deal, "deal", 1546976)
        self.t.event(tb, {}, "deal", 1)          # no assignment: no row
        r = rows(self.root)
        self.assertEqual([x["event"] for x in r], ["assigned", "selected", "deal"])
        self.assertEqual(r[1]["grade"], "B")
        self.assertEqual(r[2]["price"], 1546976)
        self.assertEqual(r[2]["others"]["floor_margin"]["arm"], "A")

    def test_never_raises(self):
        write_cfg(self.root)
        self.t.selected("t", None, None, None)
        self.t.selected("t", {"bad": 1}, {"unit": "x"}, "why")
        self.t.event("t", {"grade_read": {"bad": 1}}, "deal")
        self.assertEqual(self.t.assign("t", object()), (None, None))


class SellerWiring(unittest.TestCase):
    SRC = (HERE / "seller.py").read_text(encoding="utf-8")

    def test_wired(self):
        s = self.SRC
        self.assertIn("import grade_read", s)
        self.assertRegex(s, r"GRADE_READ = grade_read\.Trial\(\)")
        i = s.index("asyncio.to_thread(GRADE_READ.assign, task.id, text)")
        j = s.index("self.stock.pick(text, rank=lambda x: floor_rank(x, ranked)")
        self.assertLess(i, j)
        self.assertIn("add_grades=grade_add", s[j:j + 200])
        self.assertIn("asyncio.to_thread(GRADE_READ.selected, task.id, grade_read_assignment, u, miss)", s)
        self.assertRegex(s, r'deal\["grade_read"\] = grade_read_assignment')
        self.assertIn("GRADE_READ.event(task_id, deal, result, price, observed_at)", s)

    def test_switch_listed(self):
        import meta_harness
        self.assertIn(".market/grade_read/config.json", meta_harness.SWITCHES)

    def test_sol_source(self):
        src = (HERE / "sol_seller_margin.py").read_text(encoding="utf-8")
        self.assertIn("'grade_read.py'", src)


SEAM = r"""
import asyncio,json,sys,time,uuid
from types import SimpleNamespace
from unittest.mock import patch
import seller
from shop_stock import StockBook
mode=sys.argv[1]
REAL=sys.argv[2]
root=seller.GRADE_READ.root
if mode!='off':
 root.mkdir(parents=True,exist_ok=True)
 (root/'config.json').write_text(json.dumps(dict(study='g-read',salt='s',version=1,epoch='t',enabled=True,mode='ab',
  share=1.0 if mode=='B' else 0.0,start_at=time.time()-5,stop_at=time.time()+3600)))
def unit(u,g,cost):
 return dict(unit=u,sku='G8ec51935',grade=g,kind='goods',name='소파',cost=cost,list_price=2320000,category='가구>소파',supplied_at=1,expires_at=None,cert=True)
units=[unit('u-a','A',1642751),unit('u-b','B',1322319)]
class Capture:
 def new_agent_message(self,**kw):return kw
 async def requires_input(self,message):self.message=message
 async def reject(self,message):self.message=dict(rejected=message)
 async def failed(self,message):raise AssertionError('failed')
shop=seller.Seller(None,'fixture',stock=StockBook(lambda:dict(round=3,units=units)))
cap=Capture();task=SimpleNamespace(id=str(uuid.uuid4()),context_id='ctx')
with patch.object(seller,'TaskUpdater',return_value=cap),patch.object(seller,'get_message_text',return_value=REAL),\
     patch.object(seller,'new_text_part',side_effect=lambda t:t):
 with patch.object(seller,'ask',side_effect=AssertionError('unexpected external harness')):
  asyncio.run(shop._execute(SimpleNamespace(current_task=task,message=None),None))
d=shop.open.get(task.id) or {}
seller.MEMORY_WRITER.shutdown(wait=True)
rows=[json.loads(x) for x in (root/'events.jsonl').read_text().splitlines()] if (root/'events.jsonl').exists() else []
print('SEAM_JSON '+json.dumps(dict(msg=str(cap.message)[:400],unit=d.get('unit'),ask=d.get('ask'),reserve=d.get('reserve'),arm=(d.get('grade_read') or {}).get('arm'),rows=rows),ensure_ascii=False))
"""


class SellerSeam(unittest.TestCase):
    """seller._execute opening the real G8ec51935 talk with an A unit (floor 1706590 on ops) and a cheaper B unit."""
    runs = {}

    @classmethod
    def run_mode(cls, mode):
        if mode not in cls.runs:
            with tempfile.TemporaryDirectory() as td:
                p = Path(td)
                (p / "pricing.json").write_text("{}")
                (p / "memory.json").write_text('{"negotiations":[]}')
                env = dict(os.environ, MARKET_DIR=td, SHOP_PRICING=str(p / "pricing.json"),
                           SHOP_MEMORY=str(p / "memory.json"), NEGOTIATE_ENGINE="jevrule")
                env.pop("GRADE_READ_CONFIG", None)
                r = subprocess.run([sys.executable, "-B", "-c", SEAM, mode, REAL], env=env, cwd=HERE,
                                   capture_output=True, text=True, timeout=60)
                line = [x for x in r.stdout.splitlines() if x.startswith("SEAM_JSON ")]
                assert r.returncode == 0 and line, r.stdout[-3000:] + r.stderr[-3000:]
                cls.runs[mode] = json.loads(line[0][len("SEAM_JSON "):])
        return cls.runs[mode]

    def test_off_and_control_quote_the_a_unit(self):
        off, a = self.run_mode("off"), self.run_mode("A")
        for r in (off, a):
            self.assertEqual(r["unit"], "u-a")
            self.assertIn("grade A.", r["msg"])
        self.assertEqual((off["ask"], off["reserve"]), (a["ask"], a["reserve"]))
        self.assertEqual(off["rows"], [])
        self.assertEqual([x["event"] for x in a["rows"]], ["assigned", "selected"])
        self.assertEqual(a["rows"][1]["grade"], "A")

    def test_treatment_quotes_the_b_unit_priced_as_b(self):
        b = self.run_mode("B")
        self.assertEqual((b["unit"], b["arm"]), ("u-b", "B"))
        self.assertIn("grade B.", b["msg"])
        self.assertIn("72 h warranty", b["msg"])
        self.assertLess(b["reserve"], self.run_mode("A")["reserve"])
        self.assertGreater(b["reserve"], 1322319)          # above the B unit's cost
        self.assertEqual(b["rows"][1]["grade"], "B")


if __name__ == "__main__":
    unittest.main()
