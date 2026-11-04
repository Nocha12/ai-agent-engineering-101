"""The meta-harness: Terra tunes and repairs every hour, Astra explores and grades every twelve.

Terra adjusts: pricing.json's settings a bounded step at a time, the shop's memory (memory/agent/), broken tools, and the
numbers against the Players objective. Astra explores: new strategies, tools and data sources (ledger, forum, own sales)
as hypotheses with small experiments, and grades what Terra did. Strategy comes before dials: Astra compares two or three
directions and writes the chosen one in meta/strategy.md; Terra checks each hour whether it still leads to first place in
profit and shifts it only after two cycles point the same way. Both answer to hard_limits.py: a change that moves a
setting out of its bounds or by more than one step, touches a protected file or drops a hard_limits line is undone.

    python meta_harness.py terra | astra     one cycle
    python meta_harness.py run               the loop (run_meta.sh keeps it alive)

A cycle: scorecard -> the model reads the logs and changes this folder -> citations checked, tests run -> commit ->
ten minutes watched -> an hour of trading -> the next cycle judges it. A failed change is committed and then reverted, so
the attempt stays in history. Models come from META_TERRA_MODEL and META_ASTRA_MODEL; if one is refused, the cycle
says so and the shop goes on. The harness commits only under this folder and checks for secrets before each commit,
because the repository's rules forbid both; pushes happen only with META_PUSH=1.
"""
import datetime
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

import httpx

import hard_limits
import scorecard

ROOT = os.path.abspath(os.environ.get("META_DIR") or os.path.dirname(os.path.abspath(__file__)))
MODELS = {"terra": os.environ.get("META_TERRA_MODEL", "gpt-6.1-terra"),
          "astra": os.environ.get("META_ASTRA_MODEL", "gpt-6-astra")}
CODEX_TIMEOUT = int(os.environ.get("META_CODEX_TIMEOUT", 2700))
CANARY_SECONDS = int(os.environ.get("META_CANARY_SECONDS", 600))
RUN_SECONDS = int(os.environ.get("META_RUN_SECONDS", 3600))
ASTRA_EVERY = int(os.environ.get("META_ASTRA_EVERY", 12 * 3600))
KIT_PATTERN = os.environ.get("META_KIT_PATTERN", "market_kit.py join")
CHATGPT_CODEX = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
ECON_ROUND = 3
STARTED = time.time()             # Astra's clock when no cycle is recorded yet
NOT_CODE = ("meta/evidence/", "meta/changes.jsonl", "meta/grades.jsonl", "meta/state.json", "meta/last-")
LIVE = (":!logs", ":!memory")
SELLER_FILES = ("seller.py", "shop_memory.py", "shop_stock.py", "harness.py", "catalog.py", "pricing.json", "acp_shop.py")
EVIDENCE = {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["ref", "excerpt", "meaning"],
                                       "properties": {k: {"type": "string"} for k in ("ref", "excerpt", "meaning")}}}
COMMON = {"changed": {"type": "boolean"}, "hypothesis": {"type": "string"}, "change_summary": {"type": "string"},
          "target_metric": {"type": "string"}, "expected": {"type": "string"}, "evidence": EVIDENCE,
          # "hard to tell" is not an answer: what is missing, the measurement taken for it this cycle, the line that judges it
          "evidence_gap": {"type": "object", "additionalProperties": False, "required": ["missing", "measurement", "judge_line"],
                           "properties": {k: {"type": "string"} for k in ("missing", "measurement", "judge_line")}}}
STRATEGY = "meta/strategy.md"     # the strategy Terra follows: Astra sets it, Terra shifts it only on repeated evidence
DOCS_ONLY = (STRATEGY,)           # a change of these alone needs no tests and no watch: nothing running reads them


def strict(props):
    return {"type": "object", "additionalProperties": False, "required": sorted(props), "properties": props}


S = {"type": "string"}
SCHEMAS = {
    "terra": {"previous_verdict": {"type": "string", "enum": ["none", "keep", "fix", "revert"]}, "previous_reason": S,
              "grade_summary": S, "weakest": {"type": "array", "items": S},
              "strategy_check": strict({"current": S, "gap_cause": {"type": "string", "enum": ["dials", "strategy", "bug", "unclear"]},
                                        "keep_or_shift": {"type": "string", "enum": ["keep", "watch", "shift"]}, "reason": S}),
              "action_kind": {"type": "string", "enum": ["none", "settings", "tool_new", "tool_fix", "prompt", "code", "memory",
                                                         "strategy_shift"]}},
    "astra": {"summary": S,
              "strategy": strict({"options": {"type": "array", "items": strict({"direction": S, "expected_profit": S, "risk": S, "test": S})},
                                  "chosen": S, "experiment": S}),
              "grades": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["change", "grade", "reason"],
                                                    "properties": {"change": {"type": "string"}, "reason": {"type": "string"},
                                                                   "grade": {"type": "string", "enum": ["worked", "no_effect", "harmful", "unclear"]}}}},
              "keep_or_drop": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["item", "decision", "reason"],
                                                          "properties": {"item": {"type": "string"}, "reason": {"type": "string"},
                                                                         "decision": {"type": "string", "enum": ["keep", "drop"]}}}}},
}


def schema_for(kind):
    return strict({**COMMON, **SCHEMAS[kind]})


def log(msg):
    line = f"{time.strftime('%m-%d %H:%M:%S')} [meta] {msg}"
    print(line, flush=True)
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    with open(os.path.join(ROOT, "logs", "meta.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def git(*args, check=True):
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and p.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()[:400]}")
    return p.stdout.strip()


def verify_evidence(items, root=None):
    """Problems with the citations: each must be a real line of a real file here holding the quoted text."""
    root, problems = root or ROOT, []
    if not items:
        return ["no evidence cited"]
    for e in items:
        m = re.fullmatch(r"(.+):(\d+)", e.get("ref") or "")
        if not m:
            problems.append(f"{e.get('ref')!r} is not path:line")
            continue
        path = os.path.realpath(os.path.join(root, m.group(1)))
        if not path.startswith(os.path.realpath(root) + os.sep) or not os.path.isfile(path):
            problems.append(f"{m.group(1)} is not a file here")
            continue
        n, excerpt = int(m.group(2)), (e.get("excerpt") or "").strip()
        with open(path, encoding="utf-8", errors="replace") as f:
            line = next((x for i, x in enumerate(f, 1) if i == n), None)
        if line is None:
            problems.append(f"{e['ref']}: no such line")
        elif len(excerpt) < 4 or excerpt not in line:
            problems.append(f"{e['ref']}: the line does not contain {excerpt[:60]!r}")
    return problems


def secret_values():
    """Values that must never enter a commit: the secrets file next to this folder."""
    out = []
    for p in (os.path.join(ROOT, "..", ".env"), os.path.join(ROOT, ".env")):
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    m = re.match(r"\s*(?:export\s+)?[A-Z_]*(?:KEY|TOKEN|OWNER|SECRET)[A-Z_]*=[\"']?([^\"'\s]{12,})", line)
                    if m:
                        out.append(m.group(1))
        except OSError:
            pass
    return out


def secrets_in_stage():
    diff = git("diff", "--cached", "--no-color")
    found = [n for n, pat in (("private key", r"BEGIN [A-Z ]*PRIVATE KEY"), ("OpenRouter key", r"sk-or-v1-[0-9a-zA-Z]{20,}"))
             if re.search(r"^\+.*" + pat, diff, re.M)]
    found += ["a value from .env" for v in secret_values() if v in diff]
    return found


def stage(with_logs=False):
    git("add", "-A", "--", ".", *(() if with_logs else LIVE))
    return git("diff", "--cached", "--name-only", "--relative").splitlines()


LIVE_ENV = ("SHOP_MARKET_NAME", "SHOP_MEMORY", "AGENT_MEMORY_DIR")    # these point shop_memory/agent_memory at live files


def test_env():
    """The environment for the tests and for Terra/Astra: without the market name, so a test writes its scratch memory
    instead of memory/seller_memory_r3.json (11/04 22:06, 03:59 and 06:00 runs added 72 test talks there and failed 5
    tests at any HEAD), and with this venv first on PATH (the system python3 has no httpx)."""
    env = {k: v for k, v in os.environ.items() if k not in LIVE_ENV}
    env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
    return env


def run_tests():
    p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-p", "test_*.py"], cwd=ROOT, capture_output=True,
                       text=True, timeout=900, env=test_env())
    failed = [ln for ln in p.stderr.splitlines() if ln.startswith(("FAIL: ", "ERROR: "))]
    return p.returncode == 0, "\n".join(failed[:20] + [p.stderr[-1200:]])


def restart_seller(paths):
    """The hooks start fresh at each use; only a long-running seller needs a restart. The kit's watchdog starts it again."""
    if any(os.path.basename(p) in SELLER_FILES for p in paths) and KIT_PATTERN:
        subprocess.run(["pkill", "-f", "seller.py --port"], check=False)
        log("seller restarted for the change")


def kit_alive():
    return not KIT_PATTERN or subprocess.run(["pgrep", "-f", KIT_PATTERN], capture_output=True).returncode == 0


def kit_log_id(name="market-kit.log"):
    try:
        return os.stat(os.path.join(ROOT, "logs", name)).st_ino
    except OSError:
        return None


def kit_log_counts():
    """The kit log's counts and the file they came from, read again if the kit moved the log during the read."""
    while True:
        log_id = kit_log_id()
        counts = scorecard.log_counts(ROOT)
        if kit_log_id() == log_id:
            return log_id, counts


def kit_log_counts_since(log_id):
    """The kit log's counts now, plus the part the kit moved to market-kit.log.1 at 20 MB when that is the file that was
    market-kit.log (log_id). 11/04 09:27 it moved during Astra's watch: the count fell from ~171,000 lines to 872 and the
    watch undid a sound change for "the kit's log did not grow". Two moves inside one watch lose the first part."""
    while True:
        moved = log_id is not None and kit_log_id("market-kit.log.1") == log_id
        now_id, now = kit_log_counts()
        if moved:
            old = scorecard.log_counts(ROOT, "market-kit.log.1")
            return {k: now[k] + old.get(k, 0) for k in now}
        if log_id is None or now_id == log_id or kit_log_id("market-kit.log.1") != log_id:
            return now                # not moved, or moved too long ago to find: read as is


def canary(seconds=None):
    """Watches the log for the first minutes of a change. Returns the reasons to undo it; none means carry on."""
    seconds = CANARY_SECONDS if seconds is None else seconds
    log_id, before = kit_log_counts()
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(min(30, max(0.01, end - time.time())))
        if not kit_alive():
            return ["the kit is not running"]
    now = kit_log_counts_since(log_id)
    grew = lambda k: now[k] - before[k]
    bad = [f"{k} +{grew(k)}" for k, limit in (("tracebacks", 3), ("settle_refused", 5), ("checkout_refused", 10),
                                              ("supply_refused", 10), ("harness_failed", 10)) if grew(k) >= limit]
    if KIT_PATTERN and seconds >= 60 and grew("lines") <= 0:
        bad.append("the kit's log did not grow")
    return bad


def version_of(path):
    """(0, 165, 0) from `codex-cli 0.165.0-alpha.1.2`, or None if it does not run."""
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return tuple(map(int, m.groups())) if m else None


def find_codex():
    """The newest Codex here: an old npm CLI on PATH refuses gpt-6.1-terra (0.153.2 did, 0.165 takes it).
    META_CODEX overrides."""
    if os.environ.get("META_CODEX"):
        return os.environ["META_CODEX"]
    found = [(version_of(p), p) for p in (shutil.which("codex"), CHATGPT_CODEX) if p and os.path.exists(p)]
    found = [f for f in found if f[0]]
    return max(found)[1] if found else None


def call_model(kind, cycle, pack):
    codex = find_codex()
    if not codex:
        return None, "no codex found"
    os.makedirs(os.path.join(ROOT, "meta"), exist_ok=True)
    schema, out = os.path.join(ROOT, "meta", f"last-{kind}-schema.json"), os.path.join(ROOT, "meta", f"last-{kind}-answer.json")
    with open(schema, "w", encoding="utf-8") as f:
        json.dump(schema_for(kind), f)
    if os.path.exists(out):
        os.remove(out)
    with open(os.path.join(ROOT, "meta", f"{kind}.md"), encoding="utf-8") as f:
        prompt = f.read()
    prompt += f"\n\nCycle {cycle}. Evidence pack: {os.path.relpath(pack, ROOT)}. Past changes: meta/changes.jsonl.\n"
    prompt += f"Tests: `{sys.executable} -m unittest discover -p 'test_*.py'` in this folder, the same command and " \
              "environment the harness uses (the system python3 lacks httpx).\n"
    access = os.environ.get("META_CODEX_ACCESS", "--dangerously-bypass-approvals-and-sandbox").split()   # the Mac mini is its own
    cmd = [codex, "exec", "-m", MODELS[kind], *access, "--skip-git-repo-check", "--ephemeral",
           "-C", ROOT, "--output-schema", schema, "-o", out, "-"]
    try:
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=CODEX_TIMEOUT, env=test_env())
    except subprocess.TimeoutExpired:
        return None, f"no answer within {CODEX_TIMEOUT} s"
    with open(os.path.join(ROOT, "logs", "meta-codex.log"), "a", encoding="utf-8") as f:
        f.write(f"\n=== {cycle} {MODELS[kind]} exit {p.returncode}\n{p.stdout[-20000:]}\n{p.stderr[-4000:]}\n")
    if p.returncode:
        return None, f"codex exit {p.returncode}: {(p.stderr or p.stdout).strip()[-300:]}"
    try:
        with open(out, encoding="utf-8") as f:
            return json.load(f), None
    except (OSError, ValueError) as e:
        return None, f"no structured answer: {type(e).__name__}"


def commit(message):
    git("commit", "-q", "-m", f"{message}\n\nMeta-Harness: {os.path.basename(__file__)}")
    return git("rev-parse", "HEAD")


def undo(sha, why):
    """Puts back what `sha` changed except logs/ and memory/. Astra's commit carries those and the kit keeps writing them,
    so `git revert` refused (11/04 09:29, "local changes would be overwritten") and the cycle went unrecorded."""
    paths = [p for p in git("diff-tree", "--no-commit-id", "--name-only", "-r", "--relative", sha).splitlines()
             if p and not p.startswith(("logs/", "memory/"))]
    try:
        for p in paths:
            if git("ls-tree", "--name-only", f"{sha}^", "--", p):
                git("checkout", f"{sha}^", "--", p)
            else:
                git("rm", "-q", "-f", "--ignore-unmatch", "--", p)
        if git("diff", "--cached", "--name-only"):
            git("commit", "-q", "-m", f"revert(week-08): {sha[:8]} 되돌림 - {why}"[:300])
    except RuntimeError:              # half an undo is worse than none: every path back as the commit left it
        git("reset", "-q", "--", *paths, check=False)
        for p in paths:
            if git("ls-tree", "--name-only", "HEAD", "--", p):
                git("checkout", "HEAD", "--", p, check=False)
            elif os.path.exists(os.path.join(ROOT, p)):
                os.remove(os.path.join(ROOT, p))
        raise
    log(f"undid {sha[:8]}: {why}")


def try_undo(cid, sha, why):
    """undo(), or why it could not (the change then stays as committed). The cycle is recorded either way: an Astra
    cycle missing from meta/grades.jsonl runs again on the loop's next start."""
    try:
        undo(sha, why)
        return None
    except RuntimeError as e:
        log(f"{cid}: could not undo {sha[:8]}, it stays: {e}")
        return f"undo failed: {e}"[:400]


def record(rec):
    grade_log(rec)
    with open(os.path.join(ROOT, "meta", "changes.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    stage()
    if git("diff", "--cached", "--name-only") and not secrets_in_stage():
        commit(f"chore(week-08): 메타 하네스 기록 {rec['cycle']} - {rec['status']}"[:200])


def archive_logs():
    """Astra's commit carries the logs. The kit keeps one rotated file only, so each is saved under its own name first."""
    rot = os.path.join(ROOT, "logs", "market-kit.log.1")
    if os.path.exists(rot):
        keep = os.path.join(ROOT, "logs", time.strftime("market-kit-rotated-%m%d-%H%M.log", time.localtime(os.path.getmtime(rot))))
        if not os.path.exists(keep):
            shutil.copy2(rot, keep)


def pricing_at_head():
    try:
        return json.loads(git("show", "HEAD:./pricing.json"))
    except (RuntimeError, ValueError):
        return {}


def pricing_now():
    try:
        with open(os.path.join(ROOT, "pricing.json"), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        return {"_unreadable": str(e)}


SAMPLE = {"settled": 0}      # counted before the model runs, so nothing it writes into the log can widen its own step


def limit_problems(code):
    """What hard_limits says about the staged change: settings out of bounds or moved too far, protected files."""
    new = pricing_now()
    if "_unreadable" in new:
        return [f"pricing.json is not valid JSON: {new['_unreadable']}"]
    guarded = [p for p in code if p not in DOCS_ONLY]     # prose rewritten in place may drop a line naming the module
    strong = SAMPLE["settled"] >= hard_limits.STRONG_SAMPLE
    step = hard_limits.MAX_STEP_STRONG if strong else hard_limits.MAX_STEP
    return hard_limits.check_policy_change(pricing_at_head(), new, step) + \
        hard_limits.guard_problems(code, git("diff", "--cached", "--no-color", "--", *guarded) if guarded else "")


def push():
    if os.environ.get("META_PUSH") == "1":
        log(git("push", "origin", f"HEAD:{os.environ.get('META_PUSH_BRANCH', 'week-08')}", check=False) or "pushed")


def since_last(card):
    """What changed since the previous scorecard: the log counters (failures included) and the objective. Terra grades the
    last hour from this, then the change it made in it."""
    folder = os.path.join(ROOT, "meta", "evidence")
    packs = sorted((p for p in os.listdir(folder) if p.endswith(".json")), key=lambda p: os.path.getmtime(os.path.join(folder, p))) \
        if os.path.isdir(folder) else []
    if not packs:
        return {"first": True}
    try:
        with open(os.path.join(folder, packs[-1]), encoding="utf-8") as f:
            prev = json.load(f)
    except (OSError, ValueError):
        return {"first": True}
    a, b = prev.get("log_counts") or {}, card.get("log_counts") or {}
    obj = lambda c: ((c.get("board") or {}).get("objective") or {})
    rotated = b.get("lines", 0) < a.get("lines", 0)        # the kit moved its log to .log.1: counts start again
    delta = {k: (b[k] if rotated else b[k] - a.get(k, 0)) for k in b if isinstance(b[k], int)}
    return {"previous": packs[-1], "log_rotated": rotated, "log_counts": {k: v for k, v in delta.items() if v},
            "players": [obj(prev).get("players"), obj(card).get("players")], "shop": [prev.get("shop"), card.get("shop")]}


def grade_log(rec):
    """meta/grades.jsonl: each cycle's grading, apart from the changes, so a trend of grades can be read in one place."""
    a = rec.get("answer") or {}
    with open(os.path.join(ROOT, "meta", "grades.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"cycle": rec["cycle"], "time": rec["time"], "status": rec.get("status"),
                            "grade": a.get("grade_summary") or a.get("summary"), "weakest": a.get("weakest"),
                            "action": a.get("action_kind"), "verdict": a.get("previous_verdict"), "grades": a.get("grades"),
                            "keep_or_drop": a.get("keep_or_drop"), "strategy_check": a.get("strategy_check"),
                            "strategy": a.get("strategy"), "evidence_gap": a.get("evidence_gap")}, ensure_ascii=False) + "\n")


def gap_problems(answer, code):
    """A cycle that changes nothing because evidence is missing must have planned how to get it: the measurement and the
    line that will judge it next cycle. Overnight 11/03-04 Terra ended 8 cycles on "hard to tell" with neither."""
    if not answer or code:
        return []
    gap = answer.get("evidence_gap") or {}
    if (gap.get("missing") or "").strip() and not ((gap.get("measurement") or "").strip() and (gap.get("judge_line") or "").strip()):
        return ["evidence is missing but no measurement or judge line was set (meta/terra.md 'When evidence is short')"]
    return []


def last_cycle_time(kind):
    """When the last `kind` cycle began, from meta/grades.jsonl (any status), or None. The loop keeps its rhythm across
    restarts with it: Astra had never run because each restart of the loop restarted its twelve hours."""
    try:
        with open(os.path.join(ROOT, "meta", "grades.jsonl"), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    for line in (reversed(lines) if kind != "first" else lines):
        try:
            g = json.loads(line)
            if kind == "first" or str(g.get("cycle", "")).startswith(f"{kind}-"):
                return datetime.datetime.fromisoformat(g["time"]).timestamp()
        except (ValueError, KeyError, TypeError):
            continue
    return None


def due(kind, now=None):
    """Seconds until the next `kind` cycle (0 or less: now). Terra an hour of trading after its last (recorded once the
    model answered, before the ten-minute watch); Astra twelve hours after its last, or after the first cycle of the
    round (or this loop's start) when it has not run yet."""
    now = time.time() if now is None else now
    if kind == "terra":
        last = last_cycle_time("terra")
        return (last + CANARY_SECONDS + RUN_SECONDS - now) if last else 0
    last = last_cycle_time("astra") or last_cycle_time("first") or STARTED
    return last + ASTRA_EVERY - now


ACCEPTED = ("no_change", "running")     # cycles whose answer the harness took: a refused or failed one is no evidence
SHIFT_GAP = 3                          # Terra cycles a shift stands before the next one


def recent_strategy_checks(n=SHIFT_GAP):
    """The strategy checks of Terra's last n accepted cycles in meta/grades.jsonl, oldest first."""
    try:
        with open(os.path.join(ROOT, "meta", "grades.jsonl"), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            g = json.loads(line)
        except ValueError:
            continue
        if str(g.get("cycle", "")).startswith("terra-") and g.get("status") in ACCEPTED and isinstance(g.get("strategy_check"), dict):
            out.append(g["strategy_check"])
            if len(out) == n:
                break
    return out[::-1]


def strategy_problems(kind, answer, paths):
    """Terra shifts the strategy only when its previous accepted cycle said watch, no shift stood in its last three cycles,
    and the new strategy is written in meta/strategy.md; it touches that file only to shift. So one noisy hour cannot turn
    the shop around, and a shift is not undone by the next."""
    if kind != "terra" or not answer:
        return []
    check = answer.get("strategy_check") or {}
    shifting = check.get("keep_or_shift") == "shift" or answer.get("action_kind") == "strategy_shift"
    if not shifting:
        return [f"Terra rewrites {STRATEGY} only with a strategy shift"] if STRATEGY in paths else []
    out = []
    if check.get("keep_or_shift") != "shift":
        out.append("action_kind strategy_shift needs strategy_check.keep_or_shift = shift")
    recent = recent_strategy_checks()
    if not recent or recent[-1].get("keep_or_shift") != "watch":
        out.append("a strategy shift needs the previous accepted Terra cycle to have said watch (two cycles of evidence)")
    if any(c.get("keep_or_shift") == "shift" for c in recent):
        out.append(f"a strategy shift stands {SHIFT_GAP} Terra cycles before the next")
    if STRATEGY not in paths:
        out.append(f"a strategy shift must rewrite {STRATEGY}")
    return out


def cycle(kind):
    cid = f"{kind}-{time.strftime('%m%d-%H%M%S')}"
    os.makedirs(os.path.join(ROOT, "meta", "evidence"), exist_ok=True)
    pack = os.path.join(ROOT, "meta", "evidence", f"{cid}.json")
    card = scorecard.build(ROOT)
    card["since_last_cycle"] = since_last(card)
    lc = card.get("log_counts") or {}
    SAMPLE["settled"] = lc.get("settled_as_shop", 0) + lc.get("paid_as_buyer", 0)
    with open(pack, "w", encoding="utf-8") as f:
        json.dump(card, f, ensure_ascii=False, indent=1, default=str)
    log(f"{cid}: asking {MODELS[kind]}")
    answer, err = call_model(kind, cid, pack)
    rec = {"cycle": cid, "model": MODELS[kind], "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "answer": answer}
    if kind == "astra":
        archive_logs()
    paths = stage(with_logs=kind == "astra")
    code = [p for p in paths if not p.startswith(NOT_CODE) and not p.startswith(("logs/", "memory/"))]

    if secrets_in_stage():      # nothing goes into history, the attempt included
        git("reset", "-q")
        subprocess.run(["git", "checkout", "--", *code], cwd=ROOT, check=False)
        for p in code:
            if os.path.exists(os.path.join(ROOT, p)) and git("ls-files", "--", p, check=False) == "":
                os.remove(os.path.join(ROOT, p))
        rec.update(status="discarded_secret", reason="a secret was about to be committed")
        log(f"{cid}: DISCARDED, a secret was in the change")
        record(rec)
        return rec

    problems = [err] if err else []
    if answer is not None:
        problems += verify_evidence(answer.get("evidence"))
        problems += strategy_problems(kind, answer, code)
        problems += gap_problems(answer, code)
    problems += limit_problems(code)
    moved = hard_limits.policy_diff(pricing_at_head(), pricing_now())
    if moved:
        log(f"{cid}: settings moved: {'; '.join(moved)}")
    docs_only = bool(code) and all(p in DOCS_ONLY for p in code)
    if code and not problems and not docs_only:
        ok, output = run_tests()
        if not ok:
            problems.append(f"tests failed: {output[:2500]}")       # the FAIL/ERROR names first, then the tail
    if err and not code:
        rec.update(status="model_error", reason=err)
        log(f"{cid}: {err}")
        record(rec)
        return rec
    if not code:
        rec.update(status="no_change" if not problems else "no_change_unsupported", problems=problems)
        log(f"{cid}: no change ({len(problems)} citation problems)")
        record(rec)
        return rec

    title = (answer or {}).get("change_summary") or err or "변경"
    if problems:
        sha = commit(f"wip(week-08): [{cid}] 실패 - {title} / {problems[0]}"[:300])
        failed = try_undo(cid, sha, f"{cid} 검증 실패: {problems[0]}")
        rec.update(status="rejected_kept" if failed else "rejected", problems=problems + ([failed] if failed else []),
                   commit=sha)
        record(rec)
        return rec

    why = (answer or {}).get("hypothesis") or ""
    body = "\n".join(x for x in (f"이유: {why}" if why else "", ("설정: " + "; ".join(moved)) if moved else "",
                                 f"기대: {(answer or {}).get('target_metric', '')} {(answer or {}).get('expected', '')}".strip()) if x)
    sha = commit(f"feat(week-08): [{cid}] {title}"[:300] + (f"\n\n{body}" if body else ""))
    rec.update(commit=sha, files=code, settings=moved)
    log(f"{cid}: applied {sha[:8]}: {title}")
    if docs_only:
        rec.update(status="running")
        log(f"{cid}: strategy document only, nothing to watch")
        record(rec)
        return rec
    restart_seller(code)
    bad = canary()
    if bad:
        log(f"{cid}: failed the watch: {'; '.join(bad)}")
        failed = try_undo(cid, sha, f"{cid} 10분 점검 실패: {'; '.join(bad)}")
        if failed:
            rec.update(status="canary_failed_kept", problems=bad + [failed])
        else:
            restart_seller(code)
            rec.update(status="canary_failed", problems=bad)
    else:
        rec.update(status="running")
        log(f"{cid}: passed the watch")
    record(rec)
    return rec


def market_round():
    try:
        return httpx.get(f"{scorecard.MARKET}/api/stats", timeout=15).json()["round"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


def run():
    """Terra, then its hour of trading; Astra when twelve hours have passed. Nothing happens before Round 3: no data."""
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    tried = {}                        # a cycle that crashed before recording still waits its turn
    while True:
        if (market_round() or 0) < ECON_ROUND:
            log("Round 3 has not opened, waiting")
            time.sleep(300)
            continue
        every = {"terra": RUN_SECONDS, "astra": ASTRA_EVERY}
        wait = lambda k: max(due(k), tried.get(k, 0) + every[k] - time.time())
        for kind in ("terra", "astra"):
            if wait(kind) > 0:
                continue
            tried[kind] = time.time()
            try:
                cycle(kind)
            except Exception as e:   # noqa: BLE001 - the shop runs on regardless; the next cycle tries again
                log(f"{kind} cycle failed: {type(e).__name__}: {e}")
            push()
        nxt = {k: time.strftime("%m-%d %H:%M", time.localtime(time.time() + max(0, wait(k)))) for k in every}
        log(f"next terra {nxt['terra']}, astra {nxt['astra']}")
        time.sleep(min(RUN_SECONDS, max(60, min(wait("terra"), wait("astra")))))


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("terra", "astra", "run"):
        sys.exit(__doc__)
    if sys.argv[1] == "run":
        run()
    else:
        print(json.dumps(cycle(sys.argv[1]), ensure_ascii=False, indent=1))
