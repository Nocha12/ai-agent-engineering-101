"""The Luna agent loop and the meta-harness. The fake codex is a script that edits a throwaway git repository.

    python -m unittest test_meta -v        (from submissions/26622007/week-08)
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import datetime
import textwrap
import time
import unittest
from unittest import mock

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
import agent_loop            # noqa: E402
import agent_tools           # noqa: E402
import forum                 # noqa: E402
import harness               # noqa: E402
import market_model as mm    # noqa: E402
import meta_harness as mh    # noqa: E402
import restock               # noqa: E402

CATALOG = {"F1": {"name": "포기김치", "kind": "food", "category": "식품>김치", "list_price": 20000, "shelf_hours": 720}}
FINAL = restock.FINAL


class ScorecardRefreshTest(unittest.TestCase):
    def test_next_cycle_discards_stale_buyer_formula(self):
        original = mh.scorecard.OBJECTIVE
        try:
            mh.scorecard.OBJECTIVE = {"buyers": [(('surplus',), 0.5)]}
            refreshed = mh.refresh_scorecard()
            self.assertEqual(refreshed.OBJECTIVE["buyers"][0], (("saved",), 0.5))
        finally:
            mh.scorecard.OBJECTIVE = original


def step(action="finish", tool="", args="", evidence=(), final=None):
    return {"reason": "r", "evidence": list(evidence), "action": action, "tool": tool, "args_json": args, "final": final}


def script(*steps):
    seen = []

    def ask(prompt, schema, system, tag=""):
        seen.append(prompt)
        return steps[min(len(seen) - 1, len(steps) - 1)]
    return ask, seen


class AgentLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = self.tmp.name
        with open(os.path.join(self.md, "catalog.json"), "w", encoding="utf-8") as f:
            json.dump(CATALOG, f, ensure_ascii=False)
        self.final = {"why": "w", "buy": [{"sku": "F1", "qty": 2}]}

    def decide(self, *steps, **kw):
        ask, seen = script(*steps)
        return agent_loop.decide("goal", FINAL, self.md, "restock", ask_fn=ask, **kw), seen

    def test_calls_the_tool_it_chose_and_records_what_it_cited(self):
        out, seen = self.decide(step("call_tool", "catalog_search", '{"query": "김치"}'),
                                step(evidence=["c0"], final=self.final))
        self.assertEqual(out, self.final)
        self.assertIn("F1", seen[1])
        with open(os.path.join(self.md, "decisions.jsonl"), encoding="utf-8") as f:
            d = json.loads(f.readline())
        self.assertEqual((d["evidence"], d["calls"][0]["tool"]), (["c0"], "catalog_search"))
        with open(os.path.join(self.md, "tool_calls.jsonl"), encoding="utf-8") as f:
            self.assertTrue(json.loads(f.readline())["ok"])

    def test_a_decision_without_evidence_is_sent_back(self):
        out, seen = self.decide(step(final=self.final), step(evidence=["c9"], final=self.final),
                                step("call_tool", "catalog_search", "{}"), step(evidence=["c0"], final=self.final))
        self.assertEqual(out, self.final)
        self.assertIn("refused", seen[1])
        self.assertIn("refused", seen[2])

    def test_no_evidenced_decision_means_none(self):
        self.assertIsNone(self.decide(step(final=self.final), max_steps=3)[0])
        self.assertFalse(os.path.exists(os.path.join(self.md, "decisions.jsonl")))

    def test_a_given_proposal_can_be_cited(self):
        ask, _ = script(step(evidence=["c0"], final=self.final))
        self.assertEqual(agent_loop.decide("g", FINAL, self.md, "restock", {"c0": "{}"}, ask), self.final)

    def test_bad_tool_calls_are_told_not_fatal(self):
        out, seen = self.decide(step("call_tool", "", "{}"), step("call_tool", "catalog_search", "not json"),
                                step("call_tool", "catalog_search", "{}"), step(evidence=["c0"], final=self.final))
        self.assertEqual(out, self.final)
        self.assertIn("no tool named", seen[1])
        self.assertIn("not JSON", seen[2])

    def test_a_new_tool_is_found_without_a_restart(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(agent_tools, "TOOLS", t):
            self.assertEqual(agent_tools.discover(), {})
            os.makedirs(os.path.join(t, "fresh"))
            with open(os.path.join(t, "fresh", "manifest.json"), "w") as f:
                json.dump({"purpose": "p"}, f)
            with open(os.path.join(t, "fresh", "tool.py"), "w") as f:
                f.write('import json,sys;print(json.dumps({"got": json.load(sys.stdin)}))')
            self.assertEqual(list(agent_tools.discover()), ["fresh"])
            self.assertEqual(json.loads(agent_tools.run("fresh", {"a": 1}, self.md)), {"got": {"a": 1}})

    def test_a_crashing_tool_answers_with_the_error(self):
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "bad"))
            with open(os.path.join(t, "bad", "tool.py"), "w") as f:
                f.write("raise ValueError('boom')")
            self.assertIn("boom", agent_tools.run("bad", {}, self.md, folder=t))

    def test_seed_tools_run(self):
        with open(os.path.join(self.md, "inventory.json"), "w") as f:
            json.dump({"balance": 5, "units": [{"unit": "u", "category": "c", "cost": 3, "expires_at": 1}]}, f)
        for name in agent_tools.discover(os.path.join(HERE, "tools")):
            out = json.loads(agent_tools.run(name, {}, self.md, folder=os.path.join(HERE, "tools")))
            self.assertFalse(isinstance(out, dict) and "error" in out, (name, out))

    def test_every_step_reaches_the_wire_with_a_strict_schema(self):
        bodies = []

        def api(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(step(evidence=["c0"], final=self.final))}}]})
        real = httpx.Client
        with mock.patch.object(harness.httpx, "Client", lambda **k: real(transport=httpx.MockTransport(api), **k)), \
                mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "x"}):
            out = agent_loop.decide("g", FINAL, self.md, "restock", {"c0": "{}"}, harness._ask_openrouter)
        self.assertEqual(out, self.final)
        rf = bodies[0]["response_format"]
        self.assertEqual((rf["type"], rf["json_schema"]["strict"]), ("json_schema", True))

    def test_schema_is_what_strict_mode_asks_for(self):
        def check(s):
            if s.get("type") == "object":
                self.assertFalse(s["additionalProperties"])
                self.assertEqual(sorted(s["required"]), sorted(s["properties"]))
                for p in s["properties"].values():
                    check(p)
            for a in s.get("anyOf", []):
                check(a)
            if s.get("items"):
                check(s["items"])
        check(agent_loop.step_schema(["t"], FINAL))
        check(mh.schema_for("terra"))
        check(mh.schema_for("astra"))


class RestockAgentTest(unittest.TestCase):
    CFG = {**restock.DEFAULTS, "round_ends_at": None, "max_lines": 2}

    def turn(self, d, final, cat=None):
        inv = {"balance": 5000000, "units": []}
        calls = []

        def decide(goal, schema, md, agent, given):
            calls.append(given)
            return final
        out = restock.agent_turn(d, inv, cat or {f"S{i}": CATALOG["F1"] for i in range(5)}, [], self.CFG, None, decide)
        return out, calls

    def test_the_queue_goes_out_in_lots_and_the_next_decision_waits(self):
        lines = [{"sku": f"S{i}", "qty": 1} for i in range(3)]
        with tempfile.TemporaryDirectory() as d:
            out, calls = self.turn(d, {"buy": lines, "why": ""})
            self.assertEqual((out, len(calls)), (lines[:2], 1))
            self.assertIn("c0", calls[0])
            out, calls = self.turn(d, {"buy": [], "why": ""})
            self.assertEqual((out, calls), (lines[2:], []))

    def test_no_decision_leaves_it_to_the_formula(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(self.turn(d, None)[0])

    def test_unknown_skus_and_bad_lots_do_not_go_out(self):
        got = restock.clean_lines([{"sku": "F1", "qty": 2}, {"sku": "F1", "qty": 3}, {"sku": "X", "qty": 1}, {"sku": "F2", "qty": 21}],
                                  {"F1": {}, "F2": {}}, 10)
        self.assertEqual(got, [{"sku": "F1", "qty": 2}])

    def test_my_receipts_identify_my_shop_when_the_tunnel_url_changes(self):
        rows = [{"k": "settle", "seq": 1, "ts": 1800000000, "buyer": "b", "order": "o", "sku": "F1", "amount": 1, "shop": "https://new.trycloudflare.com"},
                {"k": "result", "seq": 2, "order": "o", "shop": "https://new.trycloudflare.com", "seller": "Nocha Furniture"}]
        m = mm.estimate(rows, CATALOG, (), mm.my_shops(), 1800000000, {"min_settles": 1})
        self.assertEqual(m["skus"]["F1"]["my_share"], 1.0)
        self.assertEqual(m["skus"]["F1"]["sellers"], 0)


FAKE = """\
#!/usr/bin/env python3
import json, os, sys
a = sys.argv
out = a[a.index("-o") + 1]
os.chdir(a[a.index("-C") + 1])
mode = os.environ["FAKE_MODE"]
if mode == "refused":
    print("The 'gpt-6.1-terra' model is not supported", file=sys.stderr)
    sys.exit(1)
if mode == "edit-then-die":
    open("changed.py", "w").write("x = 1\\n")
    sys.exit(2)
check = {"current": "lean", "gap_cause": "unclear", "keep_or_shift": "keep", "reason": "r"}
action = "code"
if mode in ("watch", "shift", "shift-no-doc", "strategy-doc"):
    check["keep_or_shift"] = {"watch": "watch", "strategy-doc": "keep"}.get(mode, "shift")
    action = "strategy_shift" if mode.startswith("shift") else "none"
    if mode == "shift":
        open("meta/strategy.md", "a").write("- shifted\\n")
    if mode == "strategy-doc":
        open("meta/strategy.md", "w").write("prompt\\n- shifted\\n")
elif mode not in ("nothing", "nothing-false", "unsure", "unsure-planned"):
    open("changed.py", "w").write({"broken": "raise SystemExit(1)\\n", "secret": "k = 'sk-or-v1-" + "a" * 30 + "'\\n",
                                   "env-secret": "k = 'value-from-env-file-1234'\\n"}.get(mode, "x = 1\\n"))
if mode == "broken":
    open("test_x.py", "w").write("import unittest\\nclass T(unittest.TestCase):\\n    def test(self):\\n        self.fail('no')\\n")
good = [{"ref": "logs/market-kit.log:2", "excerpt": "[acp] settled", "meaning": "m"}]
bad = [{"ref": "logs/market-kit.log:2", "excerpt": "never said this", "meaning": "m"}]
json.dump({"changed": True, "hypothesis": "h", "change_summary": "변경 요약", "target_metric": "profit", "expected": "up",
           "evidence": bad if mode in ("false-citation", "nothing-false") else [] if mode == "no-citation" else good,
           "previous_verdict": "none", "previous_reason": "", "strategy_check": check, "action_kind": action,
           "strategy": {"options": [], "chosen": "lean", "experiment": "e"},
           "evidence_gap": {"missing": "breaks before or after the widening" if mode.startswith("unsure") else "",
                            "measurement": "ledger seq of each break vs the 02:30 commit" if mode == "unsure-planned" else "",
                            "judge_line": {"metric": "break rate of the widened SKUs", "direction": "below",
                                           "threshold": "15%", "min_sample": "20 sales", "expires": "11-05 18:00",
                                           "undo": "narrow back"} if mode == "unsure-planned" else
                                          {"metric": "", "direction": "none", "threshold": "", "min_sample": "",
                                           "expires": "", "undo": ""}}}, open(out, "w"))
"""


class HarnessCycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.outer = self.tmp.name
        self.root = os.path.join(self.outer, "repo")
        os.makedirs(os.path.join(self.root, "logs"))
        os.makedirs(os.path.join(self.root, "meta"))
        with open(os.path.join(self.root, "logs", "market-kit.log"), "w") as f:
            f.write("10:00:00 [kit] market_kit 4\n10:00:01 [acp] settled x at 5\n")
        for name in ("terra", "astra", "strategy"):
            with open(os.path.join(self.root, "meta", f"{name}.md"), "w") as f:
                f.write("prompt\n")
        with open(os.path.join(self.root, ".gitignore"), "w") as f:
            f.write("__pycache__/\n.market/\n")
        with open(os.path.join(self.root, "test_ok.py"), "w") as f:
            f.write("import unittest\nclass T(unittest.TestCase):\n    def test(self):\n        pass\n")
        with open(os.path.join(self.outer, ".env"), "w") as f:
            f.write("KITLOG_TOKEN=value-from-env-file-1234\n")
        fake = os.path.join(self.outer, "fake-codex")
        with open(fake, "w") as f:
            f.write(FAKE)
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.git("add", "-A")
        self.git("commit", "-qm", "start")
        # cycle() reloads scorecard (79df539), which put the real board() back over the fake one below: the tests read the
        # live board, and its board_drift alert 11/06 18:2x-19:30 failed every no-code cycle. Keep the loaded module.
        patches = [mock.patch.object(mh, "ROOT", self.root), mock.patch.object(mh, "KIT_PATTERN", ""),
                   mock.patch.object(mh, "refresh_scorecard", lambda: mh.scorecard),
                   mock.patch.object(mh, "CANARY_SECONDS", 0), mock.patch.dict(os.environ, {"META_CODEX": fake}),
                   mock.patch.object(mh.scorecard, "board", lambda *a, **k: {"round": 3}),
                   mock.patch.object(mh.scorecard.patch_notes, "check", lambda *a, **k: {"error": "offline in tests"})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def git(self, *a):
        return subprocess.run(["git", *a], cwd=self.root, capture_output=True, text=True, check=True).stdout.strip()

    def go(self, mode, kind="terra"):
        with mock.patch.dict(os.environ, {"FAKE_MODE": mode}), mock.patch("builtins.print"):
            return mh.cycle(kind)

    def exists(self):
        return os.path.exists(os.path.join(self.root, "changed.py"))

    def test_a_cited_change_is_committed_and_recorded(self):
        rec = self.go("good")
        self.assertEqual(rec["status"], "running")
        self.assertTrue(self.exists())
        self.assertIn("feat(week-08)", self.git("log", "--format=%s"))
        self.assertIn("변경 요약", self.git("log", "--format=%s"))
        with open(os.path.join(self.root, "meta", "changes.jsonl"), encoding="utf-8") as f:
            self.assertEqual(json.loads(f.readline())["answer"]["evidence"][0]["excerpt"], "[acp] settled")

    def test_tests_run_without_the_market_name_and_with_this_python(self):
        # under SHOP_MARKET_NAME the shop's tests wrote into the live seller_memory_r3.json and 5 failed at any HEAD
        with open(os.path.join(self.root, "test_env.py"), "w") as f:
            f.write("import os, sys, unittest\nimport httpx\nclass T(unittest.TestCase):\n    def test(self):\n"
                    "        self.assertNotIn('SHOP_MARKET_NAME', os.environ)\n"
                    f"        self.assertEqual(sys.executable, {sys.executable!r})\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "env test")
        with mock.patch.dict(os.environ, {"SHOP_MARKET_NAME": "nocha12"}):
            rec = self.go("good")
            self.assertEqual(os.environ["SHOP_MARKET_NAME"], "nocha12")     # the harness's own environment is kept
        self.assertEqual(rec["status"], "running", rec.get("problems"))
        self.assertEqual(mh.test_env()["PATH"].split(os.pathsep)[0], os.path.dirname(sys.executable))

    def test_hard_to_tell_without_a_measurement_is_not_accepted(self):
        rec = self.go("unsure")
        self.assertEqual(rec["status"], "no_change_unsupported")
        self.assertIn("no measurement", rec["problems"][0])
        rec = self.go("unsure-planned")
        self.assertEqual(rec["status"], "no_change")
        with open(os.path.join(self.root, "meta", "grades.jsonl"), encoding="utf-8") as f:
            self.assertEqual(json.loads(f.read().splitlines()[-1])["evidence_gap"]["judge_line"]["threshold"], "15%")
        half = {"missing": "m", "measurement": "x", "judge_line": {"metric": "a", "direction": "above", "threshold": "1",
                                                                     "min_sample": "", "expires": "e", "undo": "u"}}
        self.assertTrue(mh.gap_problems({"evidence_gap": half}, []))        # no min_sample: not a whole judge line
        self.assertTrue(mh.gap_problems({"evidence_gap": {**half, "judge_line": "a string"}}, []))

    def test_the_schedule_survives_a_restart(self):
        grades = os.path.join(self.root, "meta", "grades.jsonl")
        t = lambda s: datetime.datetime.fromisoformat(s + "+09:00").timestamp()
        with open(grades, "w") as f:
            for c, at in (("terra-a", "2026-11-03T21:05:09"), ("terra-b", "2026-11-04T07:29:46")):
                f.write(json.dumps({"cycle": c, "time": at + "+0900", "status": "no_change"}) + "\n")
        with mock.patch.object(mh, "RUN_SECONDS", 3600), mock.patch.object(mh, "ASTRA_EVERY", 12 * 3600), \
                mock.patch.object(mh, "CANARY_SECONDS", 600):
            self.assertAlmostEqual(mh.due("terra", t("2026-11-04T07:50:00")), 2986, delta=1)   # not at once
            self.assertAlmostEqual(mh.due("astra", t("2026-11-04T07:50:00")), 4509, delta=1)   # 12 h after the first
            with open(grades, "a") as f:
                f.write(json.dumps({"cycle": "astra-c", "time": "2026-11-04T09:05:00+0900", "status": "running"}) + "\n")
            self.assertAlmostEqual(mh.due("astra", t("2026-11-04T10:00:00")), 11 * 3600 + 300, delta=1)

    def test_a_failed_test_is_named_in_the_problem(self):
        rec = self.go("broken")
        self.assertEqual(rec["status"], "rejected")
        self.assertIn("FAIL: test (test_x.T.test)", rec["problems"][0])

    def test_a_false_citation_undoes_the_change_and_keeps_the_attempt(self):
        rec = self.go("false-citation")
        self.assertEqual(rec["status"], "rejected")
        self.assertFalse(self.exists())
        subjects = self.git("log", "--format=%s")
        self.assertIn("wip(week-08)", subjects)
        self.assertIn("revert(week-08)", subjects)

    def test_no_citation_at_all_is_a_failure_too(self):
        self.assertEqual(self.go("no-citation")["status"], "rejected")
        self.assertFalse(self.exists())

    def test_failing_tests_undo_the_change(self):
        rec = self.go("broken")
        self.assertEqual(rec["status"], "rejected")
        self.assertIn("tests failed", rec["problems"][0])
        self.assertFalse(self.exists())

    def test_a_failed_watch_undoes_the_change(self):
        with mock.patch.object(mh, "canary", lambda: ["tracebacks +5"]):
            rec = self.go("good")
        self.assertEqual(rec["status"], "canary_failed")
        self.assertFalse(self.exists())

    def test_a_failed_watch_undoes_astras_change_while_the_kit_writes_its_log(self):
        log = os.path.join(self.root, "logs", "market-kit.log")
        open(log, "a").write("10:01:00 [acp] settled y at 6\n")        # Astra's commit carries the log

        def watch():
            open(log, "a").write("10:05:00 [acp] settled z at 7\n")    # and the kit goes on writing it
            return ["tracebacks +5"]
        with mock.patch.object(mh, "canary", watch):
            rec = self.go("good", kind="astra")
        self.assertEqual(rec["status"], "canary_failed")
        self.assertFalse(self.exists())
        self.assertIn("settled z", open(log).read())
        self.assertIn("revert(week-08)", self.git("log", "--format=%s"))
        self.assertIn('"canary_failed"', open(os.path.join(self.root, "meta", "grades.jsonl")).read())

    def change_that_deletes(self):
        open(os.path.join(self.root, "gone.py"), "w").write("y = 1\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "gone.py")
        os.remove(os.path.join(self.root, "gone.py"))
        open(os.path.join(self.root, "changed.py"), "w").write("x = 1\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "change")
        return self.git("rev-parse", "HEAD")

    def test_undo_brings_back_a_deleted_file_and_drops_an_added_one(self):
        sha = self.change_that_deletes()
        with mock.patch("builtins.print"):
            mh.undo(sha, "why")
        self.assertTrue(os.path.exists(os.path.join(self.root, "gone.py")))
        self.assertFalse(self.exists())
        self.assertEqual(self.git("status", "--porcelain", "--", ".", ":!logs"), "")

    def test_an_undo_that_fails_leaves_the_change_whole_and_is_recorded(self):
        sha = self.change_that_deletes()
        real = mh.git

        def git(*a, **k):
            if a[0] == "commit":
                raise RuntimeError("git commit: refused")
            return real(*a, **k)
        with mock.patch.object(mh, "git", git), mock.patch("builtins.print"):
            self.assertIn("undo failed", mh.try_undo("terra-x", sha, "why"))
        self.assertFalse(os.path.exists(os.path.join(self.root, "gone.py")))
        self.assertTrue(self.exists())
        self.assertEqual(self.git("status", "--porcelain", "--", ".", ":!logs"), "")
        self.assertEqual(self.git("rev-parse", "HEAD"), sha)

    def test_the_watch_counts_across_the_kits_log_rotation(self):
        log = os.path.join(self.root, "logs", "market-kit.log")
        open(log, "a").write("".join(f"10:00:{i:02d} [acp] settled x at 5\n" for i in range(10)))
        clock = [0.0]

        def sleep(s):
            clock[0] += s
            if not os.path.exists(log + ".1"):                       # the kit moves its log at 20 MB
                os.replace(log, log + ".1")
                open(log, "w").write("10:09:00 [acp] settled y at 6\n")
        fake_time = mock.Mock(time=lambda: clock[0], sleep=sleep)
        with mock.patch.object(mh, "time", fake_time), mock.patch.object(mh, "KIT_PATTERN", "kit"), \
                mock.patch.object(mh, "kit_alive", lambda: True):
            self.assertEqual(mh.canary(60), [])
            self.assertEqual(mh.canary(60), ["the kit's log did not grow"])   # no move, no new line: still caught

    def test_a_noise_block_open_at_the_start_does_not_hide_new_tracebacks(self):
        # GPT Pro 11/06 04:44 7-1: 2 tracebacks of an open detach block at the start, reread as noise at the end, took 2
        # off 3 real new ones and the watch passed under its +3 limit
        log = os.path.join(self.root, "logs", "market-kit.log")
        open(log, "a").write("10:00:00 [seller] Failed to detach context\n10:00:00 [seller] Traceback (most recent call last):\n"
                             "10:00:00 [seller]   File \"/v/opentelemetry/trace/__init__.py\", line 602, in use_span\n"
                             "10:00:00 [seller] GeneratorExit\n10:00:00 [seller] Traceback (most recent call last):\n")
        clock = [0.0]

        def sleep(s):
            clock[0] += s
            if clock[0] > s:
                return
            with open(log, "a") as f:
                f.write("10:00:00 [seller] ValueError: <Token> was created in a different Context\n")
                f.write("".join(f"10:01:0{i} [restock] Traceback (most recent call last):\n10:01:0{i} [restock] KeyError: 'x'\n"
                                for i in range(3)))
        fake_time = mock.Mock(time=lambda: clock[0], sleep=sleep)
        with mock.patch.object(mh, "time", fake_time), mock.patch.object(mh, "KIT_PATTERN", "kit"), \
                mock.patch.object(mh, "kit_alive", lambda: True):
            self.assertEqual(mh.canary(60), ["tracebacks +3"])

    def test_secrets_never_enter_history_even_as_an_attempt(self):
        for mode in ("secret", "env-secret"):
            self.assertEqual(self.go(mode)["status"], "discarded_secret")
            self.assertFalse(self.exists())
        self.assertNotIn("sk-or-v1", self.git("log", "-p", "--all"))
        self.assertNotIn("value-from-env-file", self.git("log", "-p", "--all"))

    def test_a_refused_model_is_said_plainly_and_nothing_breaks(self):
        rec = self.go("refused")
        self.assertEqual(rec["status"], "model_error")
        self.assertIn("not supported", rec["reason"])

    def test_a_model_that_dies_mid_edit_leaves_no_change_live(self):
        self.assertEqual(self.go("edit-then-die")["status"], "rejected")
        self.assertFalse(self.exists())

    def test_no_change_is_allowed_but_its_citations_count_too(self):
        self.assertEqual(self.go("nothing")["status"], "no_change")
        self.assertEqual(self.go("nothing-false")["status"], "no_change_unsupported")

    def test_the_kit_log_is_not_committed_by_terra(self):
        with open(os.path.join(self.root, "logs", "market-kit.log"), "a") as f:
            f.write("10:00:02 more\n")
        self.go("good")
        self.assertNotEqual(self.git("status", "--short", "logs/market-kit.log"), "")

    def test_the_last_hour_is_read_from_the_harness_pack_only(self):
        # the models save their own audits in meta/evidence/ too; one of those is not the previous scorecard
        folder = os.path.join(self.root, "meta", "evidence")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "terra-1104-095319.json"), "w") as f:
            json.dump({"log_counts": {"lines": 10, "sold": 4}}, f)
        with open(os.path.join(folder, "terra-1104-095319-audit.json"), "w") as f:
            json.dump([{"not": "a pack"}], f)
        later = time.time() + 5
        os.utime(os.path.join(folder, "terra-1104-095319-audit.json"), (later, later))
        got = mh.since_last({"log_counts": {"lines": 15, "sold": 6}})
        self.assertEqual((got["previous"], got["log_counts"]), ("terra-1104-095319.json", {"lines": 5, "sold": 2}))

    def test_astra_commits_no_logs_or_memory(self):
        # eba7dd67: Astra's commit carried 64 MB of kit logs, a Round 2 archive and the shop's memory dumps
        os.makedirs(os.path.join(self.root, "memory"))
        for p in ("logs/market-kit.log", "logs/astra-tests.log", "memory/seller_memory_r3.json"):
            with open(os.path.join(self.root, p), "a") as f:
                f.write("10:00:02 more\n")
        rec = self.go("good", kind="astra")
        self.assertEqual(rec["status"], "running", rec.get("problems"))
        committed = self.git("log", "--name-only", "--format=", "HEAD~2..HEAD").split()
        self.assertIn("changed.py", committed)
        self.assertEqual([p for p in committed if p.startswith(("logs/", "memory/"))], [])

    def grades(self):
        with open(os.path.join(self.root, "meta", "grades.jsonl"), encoding="utf-8") as f:
            return [json.loads(x) for x in f]

    def strategy(self):
        with open(os.path.join(self.root, "meta", "strategy.md"), encoding="utf-8") as f:
            return f.read()

    def test_the_strategy_check_is_kept_in_grades_and_changes(self):
        self.go("good")
        self.assertEqual(self.grades()[-1]["strategy_check"]["keep_or_shift"], "keep")
        with open(os.path.join(self.root, "meta", "changes.jsonl"), encoding="utf-8") as f:
            self.assertEqual(json.loads(f.readline())["answer"]["strategy_check"]["current"], "lean")

    def test_a_shift_on_one_hour_alone_is_undone(self):
        rec = self.go("shift")
        self.assertEqual(rec["status"], "rejected")
        self.assertIn("previous accepted Terra cycle", " ".join(rec["problems"]))
        self.assertNotIn("shifted", self.strategy())

    def test_a_shift_after_a_watch_is_applied_without_tests_or_watch(self):
        with open(os.path.join(self.root, "test_bad.py"), "w") as f:
            f.write("import unittest\nclass T(unittest.TestCase):\n    def test(self):\n        self.fail('old')\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "a failing test from elsewhere")
        self.assertEqual(self.go("watch")["status"], "no_change")
        with mock.patch.object(mh, "canary", lambda: ["tracebacks +5"]):
            rec = self.go("shift")
        self.assertEqual(rec["status"], "running", rec.get("problems"))
        self.assertIn("shifted", self.strategy())
        self.assertEqual(self.grades()[-1]["action"], "strategy_shift")

    def test_a_shift_must_write_the_strategy_down(self):
        self.go("watch")
        rec = self.go("shift-no-doc")
        self.assertIn(f"a strategy shift must rewrite {mh.STRATEGY}", rec["problems"])

    def test_a_refused_shift_is_no_evidence_for_the_next(self):
        self.assertEqual(self.go("shift")["status"], "rejected")
        self.assertEqual(self.go("shift")["status"], "rejected")
        self.assertNotIn("shifted", self.strategy())

    def test_a_shift_stands_before_the_next(self):
        self.go("watch")
        self.assertEqual(self.go("shift")["status"], "running")
        self.go("watch")
        rec = self.go("shift")
        self.assertEqual(rec["status"], "rejected")
        self.assertIn("stands", " ".join(rec["problems"]))

    def test_terra_does_not_rewrite_the_strategy_without_a_shift(self):
        rec = self.go("strategy-doc")
        self.assertEqual(rec["status"], "rejected")
        self.assertNotIn("shifted", self.strategy())

    def test_astra_sets_the_strategy_without_tests_or_watch(self):
        with open(os.path.join(self.root, "test_bad.py"), "w") as f:
            f.write("import unittest\nclass T(unittest.TestCase):\n    def test(self):\n        self.fail('old')\n")
        with open(os.path.join(self.root, "meta", "strategy.md"), "w") as f:
            f.write("prompt\nkeep inside hard_limits\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "a failing test from elsewhere")
        with mock.patch.object(mh, "canary", lambda: ["tracebacks +5"]):
            rec = self.go("strategy-doc", kind="astra")
        self.assertEqual((rec["status"], rec["files"]), ("running", [mh.STRATEGY]), rec.get("problems"))
        self.assertIn("shifted", self.strategy())

    def test_astra_records_its_chosen_strategy(self):
        with mock.patch.dict(os.environ, {"FAKE_MODE": "nothing"}), mock.patch("builtins.print"):
            mh.cycle("astra")
        self.assertEqual(self.grades()[-1]["strategy"]["chosen"], "lean")

    def test_only_this_folder_is_staged(self):
        sub = os.path.join(self.outer, "mono")
        os.makedirs(os.path.join(sub, "inner", "logs"))
        with open(os.path.join(sub, "other.txt"), "w") as f:
            f.write("a")
        subprocess.run(["git", "init", "-q"], cwd=sub, check=True)
        with open(os.path.join(sub, "inner", "x.py"), "w") as f:
            f.write("a")
        with mock.patch.object(mh, "ROOT", os.path.join(sub, "inner")):
            self.assertEqual(mh.stage(), ["x.py"])

    def test_staging_works_with_logs_and_memory_ignored_and_keeps_tracked_evidence_out(self):
        # 11/04 11:07-16:12 every Terra cycle failed: `git add -A -- . :!logs :!memory` exits 1 once both are in .gitignore
        sub = os.path.join(self.outer, "ign")
        inner = os.path.join(sub, "inner")
        for d in ("logs", "memory"):
            os.makedirs(os.path.join(inner, d))
        subprocess.run(["git", "init", "-q"], cwd=sub, check=True)
        for name, text in ((".gitignore", "/logs/\n/memory/\n"), ("logs/evidence.txt", "a"), ("memory/r2.json", "{}")):
            with open(os.path.join(inner, name), "w") as f:
                f.write(text)
        subprocess.run(["git", "add", "-f", ".gitignore", "logs/evidence.txt", "memory/r2.json"], cwd=inner, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"], cwd=inner, check=True)
        for name in ("logs/evidence.txt", "logs/kit.log", "memory/r2.json", "pricing.json"):
            with open(os.path.join(inner, name), "a") as f:
                f.write("more")
        with mock.patch.object(mh, "ROOT", inner):
            self.assertEqual(mh.stage(), ["pricing.json"])


class ForumTest(unittest.TestCase):
    POSTS = [{"id": "p1", "author": "other", "text": "a"}, {"id": "p2", "author": "nocha12", "text": "mine"}, {"id": 3, "author": "x", "text": "b"}]
    CFG = forum.DEFAULTS

    def clean(self, final, voted=()):
        base = {"post": "", "refs": [], "thread": "", "votes": []}
        return forum.clean({**base, **final}, self.POSTS, {10, 11}, set(voted), self.CFG, "nocha12")

    def test_a_post_needs_real_deals_to_cite(self):
        self.assertEqual(self.clean({"post": "x sells", "refs": [10, 99]})[0], [{"post": "x sells", "refs": [10], "as": "buyer"}])
        with mock.patch("builtins.print"):
            self.assertEqual(self.clean({"post": "x sells", "refs": [99]})[0], [])
            self.assertEqual(self.clean({"post": "x sells"})[0], [])
        self.assertEqual(self.clean({"post": "", "refs": [10]})[0], [])

    def test_a_reply_goes_to_a_post_that_exists(self):
        self.assertEqual(self.clean({"post": "y", "refs": [10], "thread": "p1"})[0][0]["thread"], "p1")
        self.assertNotIn("thread", self.clean({"post": "y", "refs": [10], "thread": "nope"})[0][0])

    def test_votes_go_once_to_real_posts_of_others(self):
        votes = [{"post": "p1", "value": 1}, {"post": "p1", "value": -1}, {"post": "p2", "value": 1}, {"post": "gone", "value": 1},
                 {"post": "3", "value": -1}]
        lines, new = self.clean({"votes": votes})
        # patch 3.4: dislikes past 3 an hour cost the shop cash, so a -1 is never sent
        self.assertEqual(lines, [{"vote": "p1", "value": 1, "as": "buyer"}])
        self.assertEqual(new, ["p1"])
        self.assertEqual(self.clean({"votes": votes}, voted=["p1", "3"])[0], [])

    def test_one_like_per_author(self):
        posts = [{"id": "q1", "author": "x"}, {"id": "q2", "author": "x"}, {"id": "q3", "author": "y"}]
        final = {"post": "", "refs": [], "thread": "", "votes": [{"post": p["id"], "value": 1} for p in posts]}
        self.assertEqual(forum.clean(final, posts, set(), set(), self.CFG, "nocha12")[1], ["q1", "q3"])

    def test_no_dislike_is_sent_or_offered(self):
        lines, new = self.clean({"votes": [{"post": "p1", "value": -1}, {"post": "3", "value": -1}]})
        self.assertEqual((lines, new), ([], []))
        enum = forum.FINAL["properties"]["votes"]["items"]["properties"]["value"]["enum"]
        self.assertEqual(enum, [1])
        self.assertIn("50 settled deals", forum.GOAL)
        self.assertNotIn("-1 on false", forum.GOAL)

    def test_the_vote_limit_and_length_limit_hold(self):
        posts = [{"id": str(i), "author": f"o{i}"} for i in range(30)]
        final = {"post": "z" * 2000, "refs": [10], "thread": "", "votes": [{"post": str(i), "value": 1} for i in range(30)]}
        lines, new = forum.clean(final, posts, {10}, set(), self.CFG, "nocha12")
        self.assertEqual(len(new), self.CFG["max_votes"])
        self.assertEqual(len(lines[0]["post"]), self.CFG["max_chars"])

    def run_it(self, d, final):
        calls = []

        def decide(goal, schema, md, agent, given=None):
            calls.append(goal)
            return final
        with mock.patch("builtins.print"):
            return forum.maybe_run(d, decide), calls

    def test_keeps_its_pace_writes_the_outbox_and_remembers(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "forum.json"), "w") as f:
                json.dump(self.POSTS, f)
            with open(os.path.join(d, mm.ROWS), "w") as f:
                f.write(json.dumps({"k": "settle", "seq": 10}) + "\n")
            lines, calls = self.run_it(d, {"post": "true", "refs": [10], "thread": "", "votes": [{"post": "p1", "value": 1}]})
            self.assertEqual(len(lines), 2)
            with open(os.path.join(d, "outbox.jsonl")) as f:
                self.assertEqual([json.loads(x)["as"] for x in f], ["buyer", "buyer"])
            self.assertEqual(self.run_it(d, {"post": "again", "refs": [10], "thread": "", "votes": []}), (None, []))
            with open(os.path.join(d, forum.STATE)) as f:
                state = json.load(f)
            self.assertEqual((state["voted"], state["posts"]), (["p1"], ["true"]))
            state["at"] = 0
            with open(os.path.join(d, forum.STATE), "w") as f:
                json.dump(state, f)
            _, calls = self.run_it(d, {"post": "", "refs": [], "thread": "", "votes": []})
            self.assertIn("true", calls[0])
            self.assertIn("p1", calls[0])

    def test_a_failure_stays_inside_and_waits_for_the_next_turn(self):
        def boom(*a):
            raise harness.HarnessError("rate limited")
        with tempfile.TemporaryDirectory() as d, mock.patch("builtins.print"):
            self.assertIsNone(forum.maybe_run(d, boom))
            self.assertIsNone(forum.maybe_run(d, lambda *a: self.fail("asked again at once")))


class FindCodexTest(unittest.TestCase):
    def fake(self, d, name, version):
        p = os.path.join(d, name)
        with open(p, "w") as f:
            f.write(f"#!/bin/sh\necho 'codex-cli {version}'\n")
        os.chmod(p, 0o755)
        return p

    def test_the_newest_wins_over_what_is_on_path(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ):
            os.environ.pop("META_CODEX", None)
            old, new = self.fake(d, "old", "0.153.2"), self.fake(d, "new", "0.165.0-alpha.1.2")
            with mock.patch.object(mh.shutil, "which", lambda n: old), mock.patch.object(mh, "CHATGPT_CODEX", new):
                self.assertEqual(mh.find_codex(), new)
            with mock.patch.object(mh.shutil, "which", lambda n: new), mock.patch.object(mh, "CHATGPT_CODEX", os.path.join(d, "gone")):
                self.assertEqual(mh.find_codex(), new)
            os.environ["META_CODEX"] = old
            self.assertEqual(mh.find_codex(), old)


class EvidenceTest(unittest.TestCase):
    def test_citations(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.log"), "w") as f:
                f.write("first line\nsecond line here\n")
            ok = {"ref": "a.log:2", "excerpt": "second line"}
            self.assertEqual(mh.verify_evidence([ok], d), [])
            for bad in ({"ref": "a.log:1", "excerpt": "second line"}, {"ref": "a.log:9", "excerpt": "second line"},
                        {"ref": "nope.log:1", "excerpt": "first"}, {"ref": "a.log", "excerpt": "first"},
                        {"ref": "../a.log:1", "excerpt": "first"}, {"ref": "a.log:1", "excerpt": "a"}):
                self.assertTrue(mh.verify_evidence([bad], d), bad)
            self.assertTrue(mh.verify_evidence([], d))


class RivalsTest(unittest.TestCase):
    def test_the_shops_ahead_in_profit_and_my_rank(self):
        rows = [{"name": "a", "profit": 10, "deals": 3, "card": {"description": "x" * 500}, "owner": {"login": "a"}},
                {"name": mh.scorecard.ME, "profit": 5}, {"name": "b", "profit": 20, "card": "odd"}, {"name": "c"}]
        r = mh.scorecard.rivals(rows, top=2)
        self.assertEqual((r["my_rank"], r["of"]), (3, 3))
        self.assertEqual([x["name"] for x in r["top"]], ["b", "a"])
        self.assertEqual((len(r["top"][1]["card"]), r["top"][0]["card"]), (240, ""))
        self.assertNotIn("owner", r["top"][1])


class ObjectiveTest(unittest.TestCase):
    def test_the_board_formula(self):
        # the board's own: rows it scores (all set, a shop with supplies and no deal too), mean rank on ties, stars_rank,
        # posts 0.1 of the buyer (review/W-20261104-2030: every row of 268 Round 3 snapshots within 0.0005)
        me = mh.scorecard.ME
        b = {"shops": [{"name": me, "deals": 4, "profit": 10, "revenue": 100, "stars": 5, "stars_rank": 4.5, "all": 0.7},
                       {"name": "a", "deals": 9, "profit": 20, "revenue": 50, "stars": 4, "stars_rank": 4.5, "all": 0.5},
                       {"name": "b", "deals": 0, "profit": 0, "revenue": 0, "stars": None, "stars_rank": None, "all": 0.1},
                       {"name": "c", "deals": 0, "profit": 1000, "revenue": 1000, "all": None}],
             "buyers": [{"name": me, "deals": 2, "saved": 0.1, "surplus": 100, "forum": 0.6, "posts": 3, "all": 0.38},
                        {"name": "x", "deals": 1, "saved": 0.2, "surplus": 1, "forum": 0.6, "posts": 0, "all": 0.65}],
             "players": [{"player": "Nocha12", "total": 1.08}]}
        o = mh.scorecard.objective(b)
        self.assertEqual((o["shops"]["all"], o["shops"]["of"], o["shops"]["board_all"]), (0.7, 3, 0.7))
        self.assertEqual(o["shops"]["parts"]["stars_rank"]["percentile"], 0.75)
        self.assertEqual((o["buyers"]["all"], o["players"]), (0.38, 1.08))
        self.assertEqual(o["weakest"][:2], ("buyers", "saved"))


class AlertProblemsTest(unittest.TestCase):
    def test_no_change_must_name_every_open_alert(self):
        card = {"alerts": ["llm_cap: 4 calls refused", "surge: 2 unit(s)"]}
        answer = {"grade_summary": "alert llm_cap: refusals are the seller at its own cap; measured next hour; surge seen"}
        self.assertEqual(mh.alert_problems(card, answer, []), ["alert left open with no change: surge: 2 unit(s)"])
        self.assertEqual(mh.alert_problems(card, answer, ["restock.py"]), [])
        self.assertEqual(mh.alert_problems({"alerts": []}, answer, []), [])


class ExperimentQueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, "meta"))
        p = mock.patch.object(mh, "ROOT", self.tmp.name)
        p.start()
        self.addCleanup(p.stop)
        with open(os.path.join(self.tmp.name, mh.EXPERIMENTS), "w") as f:
            json.dump({"queue": [{"id": "a", "hours": 2}, {"id": "b"}, "junk"]}, f)

    def answer(self, step, eid="", result=""):
        return {"experiment": {"step": step, "id": eid, "result": result}}

    def test_one_at_a_time_in_order_and_judged_when_due(self):
        v = mh.experiments()
        self.assertEqual((v["running"], v["next"]["id"], v["waiting"]), (None, "a", 2))
        p = lambda step, eid="", result="", view=None: mh.experiment_problems("terra", self.answer(step, eid, result), view or mh.experiments())
        self.assertTrue(p("start", "b"))                    # not the next one
        self.assertTrue(p("none"))                          # idle with a next one and no reason
        self.assertEqual(p("none", result="cash 3.9M of 4M: no room for it until 12:40"), [])
        self.assertTrue(p("start", "a"))                    # a start with no change in the cycle
        self.assertTrue(mh.experiment_problems("terra", self.answer("start", "a"), mh.experiments(), [mh.EXPERIMENTS]))
        self.assertEqual(mh.experiment_problems("terra", self.answer("start", "a"), mh.experiments(), ["pricing.json"]), [])
        mh.note_experiment("terra-1", self.answer("start", "a"))
        with open(os.path.join(self.tmp.name, mh.EXPERIMENTS), "w") as f:     # removing or stretching it changes nothing
            json.dump({"queue": [{"id": "b"}]}, f)
        v = mh.experiments()
        self.assertEqual((v["running"]["id"], v["running"]["hours"], v["running"]["expired"], v["next"]["id"]),
                         ("a", 2, False, "b"))
        self.assertTrue(p("start", "b"))                    # a still runs
        self.assertEqual(p("continue", "a"), [])
        self.assertEqual(p("none"), [])
        later = mh.experiments(now=time.time() + 3 * 3600)
        self.assertTrue(p("none", view=later))              # past its 2 h: must be judged
        self.assertTrue(p("continue", "a", view=later))
        self.assertTrue(p("drop", "a", view=later))         # a judgment needs its number
        self.assertEqual(p("drop", "a", "forum 0.51 < 0.55 on 22 posts", view=later), [])
        mh.note_experiment("terra-2", self.answer("drop", "a", "forum 0.51 < 0.55 on 22 posts"))
        v = mh.experiments()
        self.assertEqual((v["running"], v["next"]["id"], v["closed"]), (None, "b", {"a": "drop"}))
        self.assertEqual(mh.experiment_problems("astra", self.answer("start", "zzz"), v), [])

    def test_no_queue_file_asks_nothing(self):
        os.remove(os.path.join(self.tmp.name, mh.EXPERIMENTS))
        self.assertEqual(mh.experiment_problems("terra", self.answer("none"), mh.experiments()), [])
        with open(os.path.join(self.tmp.name, mh.EXPERIMENTS), "w") as f:
            json.dump({"queue": 5}, f)
        self.assertIsNone(mh.experiments()["next"])
        self.assertEqual(mh.hours_of({"hours": 1e300}), 12)

    def test_terra_only_appends_and_never_writes_the_log(self):
        g = lambda *a: subprocess.run(["git", *a], cwd=self.tmp.name, check=True, capture_output=True)
        g("init", "-q")
        g("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "q")
        with open(os.path.join(self.tmp.name, mh.EXPERIMENTS), "w") as f:
            json.dump({"queue": [{"id": "a", "hours": 2}, {"id": "b"}, {"id": "c"}]}, f)
        self.assertEqual(mh.queue_problems("terra", [mh.EXPERIMENTS]), [])
        with open(os.path.join(self.tmp.name, mh.EXPERIMENTS), "w") as f:
            json.dump({"queue": [{"id": "b"}, {"id": "a", "hours": 2}]}, f)
        self.assertTrue(mh.queue_problems("terra", [mh.EXPERIMENTS]))
        self.assertEqual(mh.queue_problems("astra", [mh.EXPERIMENTS]), [])
        with open(os.path.join(self.tmp.name, mh.EXPERIMENT_LOG), "w") as f:
            f.write(json.dumps({"ts": 1, "id": "b", "step": "done"}) + "\n")
        g("add", "-A")
        self.assertTrue(mh.queue_problems("terra", [mh.EXPERIMENT_LOG]))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, mh.EXPERIMENT_LOG)))


if __name__ == "__main__":
    unittest.main()
