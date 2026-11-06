"""The meta-harness: Terra tunes and repairs every hour, Astra explores and grades every twelve.

Terra adjusts: pricing.json's settings a bounded step at a time, the shop's memory (memory/agent/), broken tools, and the
numbers against the Players objective. Astra explores: new strategies, tools and data sources (ledger, forum, own sales)
as hypotheses with small experiments, and grades what Terra did. Strategy comes before dials: Astra compares two or three
directions and writes the chosen one in meta/strategy.md; Terra checks each hour whether it still leads to first place in
book (profit until Patch 3.3) and shifts it only after two cycles point the same way. Both answer to hard_limits.py: a change that moves a
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
import hashlib
import importlib
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
NOT_CODE = ("meta/evidence/", "meta/changes.jsonl", "meta/grades.jsonl", "meta/state.json", "meta/last-",
            "meta/experiments_log.jsonl")
LIVE = ("logs", "memory")         # what the kit and the shop keep writing: never staged by the harness (.gitignore too)
SELLER_FILES = ("seller.py", "shop_memory.py", "shop_stock.py", "harness.py", "catalog.py", "pricing.json", "acp_shop.py")
EVIDENCE = {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["ref", "excerpt", "meaning"],
                                       "properties": {k: {"type": "string"} for k in ("ref", "excerpt", "meaning")}}}
COMMON = {"changed": {"type": "boolean"}, "hypothesis": {"type": "string"}, "change_summary": {"type": "string"},
          "target_metric": {"type": "string"}, "expected": {"type": "string"}, "evidence": EVIDENCE,
          # "hard to tell" is not an answer: what is missing, the measurement taken for it this cycle, the line that judges it
          "evidence_gap": {"type": "object", "additionalProperties": False, "required": ["missing", "measurement", "judge_line"],
                           "properties": {"missing": {"type": "string"}, "measurement": {"type": "string"},
                                          "judge_line": {"type": "object", "additionalProperties": False,
                                                         "required": ["metric", "direction", "threshold", "min_sample", "expires", "undo"],
                                                         "properties": {"metric": {"type": "string"},
                                                                        "direction": {"type": "string", "enum": ["above", "below", "none"]},
                                                                        "threshold": {"type": "string"}, "min_sample": {"type": "string"},
                                                                        "expires": {"type": "string"}, "undo": {"type": "string"}}}}}}
JUDGE_FIELDS = ("metric", "threshold", "min_sample", "expires", "undo")   # a judge line: the number, which way, how many, by when, how to undo
STRATEGY = "meta/strategy.md"     # the strategy Terra follows: Astra sets it, Terra shifts it only on repeated evidence
EXPERIMENTS = "meta/experiments.json"        # the queue of evidenced experiments: Astra, Terra or a person adds to it
EXPERIMENT_LOG = "meta/experiments_log.jsonl"  # the harness's record of each start and judgment: which one runs
DOCS_ONLY = (STRATEGY, EXPERIMENTS)  # a change of these alone needs no tests and no watch: nothing running reads them
REVIEW_ONLY = ("scorecard.py", "patch_notes.py", "meta_harness.py")


def runtime_version():
    """Fingerprint modules retained by the supervisor, including the owner's limits."""
    digest = hashlib.sha256()
    for name in ("meta_harness.py", "scorecard.py", "patch_notes.py", "hard_limits.py"):
        with open(os.path.join(os.path.dirname(__file__), name), "rb") as f:
            digest.update(f.read())
    return digest.hexdigest()


LOADED_VERSION = runtime_version()


def refresh_runtime():
    """Re-exec only between recorded cycles; never kill a model or its validation."""
    if runtime_version() != LOADED_VERSION:
        log("runtime source changed; reloading at a completed cycle boundary")
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__), "run"])


def needs_trading_watch(paths):
    """An offline scoring repair cannot cause checkout/model failures in the kit."""
    return any(p not in REVIEW_ONLY and not p.startswith(("meta/", "test_")) for p in paths)


def strict(props):
    return {"type": "object", "additionalProperties": False, "required": sorted(props), "properties": props}


S = {"type": "string"}
SCHEMAS = {
    "terra": {"previous_verdict": {"type": "string", "enum": ["none", "keep", "fix", "revert"]}, "previous_reason": S,
              "grade_summary": S, "weakest": {"type": "array", "items": S},
              "strategy_check": strict({"current": S, "gap_cause": {"type": "string", "enum": ["dials", "strategy", "bug", "unclear"]},
                                        "keep_or_shift": {"type": "string", "enum": ["keep", "watch", "shift"]}, "reason": S}),
              "action_kind": {"type": "string", "enum": ["none", "settings", "tool_new", "tool_fix", "prompt", "code", "memory",
                                                         "strategy_shift"]},
              "experiment": strict({"id": S, "step": {"type": "string", "enum": ["none", "start", "continue", "done", "drop"]},
                                    "result": S})},
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


def stage():
    """This folder's changes without logs/ and memory/. Astra's commit eba7dd67 carried them: 64 MB of kit logs, a Round 2
    archive and the shop's memory dumps, which the kit kept writing, so the undo of that commit was refused."""
    # not `add -A -- . :!logs :!memory`: with both in .gitignore git exits 1 on those pathspecs ("paths are ignored"),
    # which failed every Terra cycle from 11/04 11:07 (f0a8917) and left its change staged, uncommitted
    git("add", "-A", "--", ".")
    git("reset", "-q", "--", *LIVE, check=False)      # tracked evidence there stays as committed; no HEAD yet: nothing to undo
    paths = git("diff", "--cached", "--name-only", "--relative").splitlines()
    live = [p for p in paths if p.split("/", 1)[0] in LIVE]
    if live:
        raise RuntimeError(f"stage: {live[:5]} under {LIVE} still staged")
    return paths


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
    if now.get("tracebacks_open"):     # a block half written at the read: let it finish before counting it
        time.sleep(2)
        now = kit_log_counts_since(log_id)
    # a traceback block still open at the end counts as new; one open at the start is not taken off when it turns out noise
    now = dict(now, tracebacks=now["tracebacks"] + now.get("tracebacks_open", 0))
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
    owner = [f"{p} decides which buyers are refused, an owner's decision (11/05)" for p in code
             if p.split("/")[-1] == "acp_gate.py"]
    if (pricing_at_head().get("seller") or {}) != (new.get("seller") or {}):
        owner.append("pricing.json seller (refuse_buyers, refuse_one_star_at, refuse_exempt) is the owner's setting (11/05)")
    return owner + hard_limits.check_policy_change(pricing_at_head(), new, step) + \
        hard_limits.guard_problems(code, git("diff", "--cached", "--no-color", "--", *guarded) if guarded else "")


def push():
    if os.environ.get("META_PUSH") == "1":
        log(git("push", "origin", f"HEAD:{os.environ.get('META_PUSH_BRANCH', 'week-08')}", check=False) or "pushed")


SCORE_SWITCH = datetime.datetime(2026, 11, 5, 3, 45, tzinfo=datetime.timezone(datetime.timedelta(hours=9))).timestamp()
BASELINE_HOURS = (6, 12)


def since_last(card, kind="terra", now=None):
    """What changed since the previous scorecard of the same kind (Terra hourly, Astra twelve-hourly: one window each),
    and against the packs 6 and 12 hours back: the log counters (failures included) and the objective. Terra grades the
    last hour from this, then the change it made in it. A window across Patch 3.3's switch (board 11/05 03:42-03:47, shop
    ALL from cash to book) is marked: its shop ALL moved with the formula, not only with trading."""
    now = time.time() if now is None else now
    folder = os.path.join(ROOT, "meta", "evidence")
    # only the harness's own packs: the models save their test runs and audits here too (astra-1104-090509-review.json)
    packs = sorted((p for p in os.listdir(folder) if re.fullmatch(rf"{kind}-\d+(-\d+)?\.json", p)),
                   key=lambda p: os.path.getmtime(os.path.join(folder, p))) if os.path.isdir(folder) else []
    if not packs:
        return {"first": True}

    def load(p):
        try:
            with open(os.path.join(folder, p), encoding="utf-8") as f:
                x = json.load(f)
            return x if isinstance(x, dict) else None
        except (OSError, ValueError):
            return None

    prev = load(packs[-1])
    if prev is None:
        return {"first": True}
    a, b = prev.get("log_counts") or {}, card.get("log_counts") or {}
    obj = lambda c: ((c.get("board") or {}).get("objective") or {})
    rotated = b.get("lines", 0) < a.get("lines", 0)        # the kit moved its log to .log.1: counts start again
    # the part written to the old file after the last pack is in market-kit.log.1 past its line a["lines"]; without it
    # the 22:26 Terra pack saw 46 shop settles of 144 (the hour before the 21:58 move was lost)
    old = scorecard.log_counts(ROOT, "market-kit.log.1", start=a.get("lines", 0)) if rotated else {}
    delta = {k: (b[k] + old.get(k, 0) if rotated else b[k] - a.get(k, 0)) for k in b if isinstance(b[k], int) and k != "tracebacks_open"}
    out = {"previous": packs[-1], "log_rotated": rotated, "log_counts": {k: v for k, v in delta.items() if v},
           "players": [obj(prev).get("players"), obj(card).get("players")], "shop": [prev.get("shop"), card.get("shop")],
           "across_score_switch": os.path.getmtime(os.path.join(folder, packs[-1])) < SCORE_SWITCH <= now, "baselines": {}}
    every = sorted((os.path.getmtime(os.path.join(folder, p)), p) for p in os.listdir(folder)
                   if re.fullmatch(r"(terra|astra)-\d+(-\d+)?\.json", p))
    for h in BASELINE_HOURS:      # the newest pack at least h hours old, any kind: the 6 and 12 hour trend
        old = [p for t, p in every if t <= now - h * 3600]
        base = load(old[-1]) if old else None
        if base is None:
            out["baselines"][f"{h}h"] = None
            continue
        t = os.path.getmtime(os.path.join(folder, old[-1]))
        out["baselines"][f"{h}h"] = {"pack": old[-1], "hours": round((now - t) / 3600, 1),
                                     "players": [obj(base).get("players"), obj(card).get("players")],
                                     "shop_all": [(obj(base).get("shops") or {}).get("all"), (obj(card).get("shops") or {}).get("all")],
                                     "buyer_all": [(obj(base).get("buyers") or {}).get("all"), (obj(card).get("buyers") or {}).get("all")],
                                     "across_score_switch": t < SCORE_SWITCH <= now}
    return out


def alert_problems(card, answer, code):
    """A cycle that changes nothing while the scorecard raises an alert (llm cap, surge above the worst cost, forum falling,
    drift, rules) must answer each one as "alert <name>: ..." in grade_summary or evidence_gap: no_change cannot pass one
    over, and a plain word like "forum" in a summary does not count as an answer."""
    if code or not answer:
        return []
    said = json.dumps({k: answer.get(k) for k in ("grade_summary", "evidence_gap")}, ensure_ascii=False)
    return [f"alert left open with no change: {a}" for a in card.get("alerts") or []
            if f"alert {a.split(':')[0]}:" not in said]


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
    line = gap.get("judge_line")
    whole = isinstance(line, dict) and line.get("direction") in ("above", "below") and \
        all(isinstance(line.get(k), str) and line[k].strip() for k in JUDGE_FIELDS)
    if (gap.get("missing") or "").strip() and not ((gap.get("measurement") or "").strip() and whole):
        return ["evidence is missing but no measurement or whole judge line (metric, direction, threshold, min_sample, "
                "expires, undo) was set (meta/terra.md 'When evidence is short')"]
    return []


def read_queue(text=None):
    """The queue's entries (dicts with a string id), from meta/experiments.json or the given text; anything else is []."""
    try:
        if text is None:
            with open(os.path.join(ROOT, EXPERIMENTS), encoding="utf-8") as f:
                text = f.read()
        queue = json.loads(text).get("queue")
    except (OSError, ValueError, AttributeError):
        return []
    return [e for e in queue if isinstance(e, dict) and isinstance(e.get("id"), str)] if isinstance(queue, list) else []


def hours_of(e):
    h = e.get("hours")
    return h if isinstance(h, (int, float)) and not isinstance(h, bool) and 0 < h <= 168 else 12


def experiments(now=None):
    """The queue as the pack shows it: the one running (when it started, its expiry, whether that passed), the next one
    queued and how many wait. A queue entry: id, change, evidence, metric, direction, threshold, min_sample, hours, undo.
    What runs comes from meta/experiments_log.jsonl alone (its start line keeps the entry as it was), so an entry edited
    or removed from the queue while it runs is still the one judged; an id the log already holds never starts again."""
    now = time.time() if now is None else now
    queue = read_queue()
    steps, began = {}, {}
    for r in scorecard.jsonl(os.path.join(ROOT, EXPERIMENT_LOG)):
        if isinstance(r.get("id"), str) and isinstance(r.get("ts"), (int, float)):
            steps[r["id"]] = r.get("step")
            if r.get("step") == "start":
                began[r["id"]] = r
    run = next((i for i, st in steps.items() if st in ("start", "continue") and i in began), None)
    waiting = [e for e in queue if e["id"] not in steps]
    out = {"running": None, "next": waiting[0] if waiting else None, "waiting": len(waiting),
           "closed": {i: st for i, st in steps.items() if st in ("done", "drop")},
           "skipped": [e["id"] for e in queue if steps.get(e["id"]) in ("done", "drop")]}
    if run:
        b = began[run]
        e = b.get("entry") if isinstance(b.get("entry"), dict) else next((q for q in queue if q["id"] == run), {"id": run})
        hours, ends = hours_of(e), b["ts"] + hours_of(e) * 3600
        out["running"] = {**e, "id": run, "hours": hours, "started": time.strftime("%m-%d %H:%M", time.localtime(b["ts"])),
                          "expires": time.strftime("%m-%d %H:%M", time.localtime(ends)), "expired": now > ends}
    return out


def experiment_problems(kind, answer, view, code=()):
    """One evidenced experiment at a time, from the queue in order (meta/terra.md 'Experiments'): start only the next
    one, only when none runs and only with its change in this cycle's code; judge the running one once its hours pass;
    with none running either start the next or say why not in result. A cycle that breaks this is not accepted."""
    if kind != "terra" or not answer:
        return []
    x = answer.get("experiment") or {}
    step, eid, said = x.get("step") or "none", x.get("id") or "", (x.get("result") or "").strip()
    run, nxt = view.get("running"), view.get("next")
    if step == "start":
        if run:
            return [f"experiment {eid}: {run['id']} still runs; judge it (done or drop) before another starts"]
        if not nxt or eid != nxt["id"]:
            return [f"experiment {eid}: only the next one in the queue ({nxt['id'] if nxt else 'none'}) can start"]
        if not [c for c in code if c not in DOCS_ONLY]:
            return [f"experiment {eid}: a start makes its change in this cycle (no code or settings changed)"]
        return []
    if step in ("continue", "done", "drop"):
        if not run or eid != run["id"]:
            return [f"experiment {eid}: not the running one ({run['id'] if run else 'none'})"]
        if step == "continue" and run.get("expired"):
            return [f"experiment {eid} passed its {run['hours']} h at {run['expires']}: judge it (done or drop)"]
        if step != "continue" and not said:
            return [f"experiment {eid} {step}: result must give the metric's number against its threshold and the sample"]
        return []
    if run and run.get("expired"):
        return [f"experiment {run['id']} passed its {run['hours']} h at {run['expires']}: judge it (done or drop)"]
    if not run and nxt and not said:
        return [f"experiment {nxt['id']} is next in the queue: start it or say in result why not now (the number that blocks it)"]
    return []


def queue_problems(kind, paths):
    """Terra only adds at the end of the queue (Astra orders it), and the log is the harness's: a model's write to it is
    put back and refused."""
    out = []
    if EXPERIMENT_LOG in paths:
        out.append(f"{EXPERIMENT_LOG} is written by the harness only")
        if git("ls-tree", "HEAD", "--", EXPERIMENT_LOG, check=False):        # in HEAD: back to it
            git("checkout", "HEAD", "--", EXPERIMENT_LOG, check=False)
        else:
            git("rm", "-q", "--cached", "--", EXPERIMENT_LOG, check=False)
            if os.path.exists(os.path.join(ROOT, EXPERIMENT_LOG)):
                os.remove(os.path.join(ROOT, EXPERIMENT_LOG))
    if kind == "terra" and EXPERIMENTS in paths:
        try:
            old = read_queue(git("show", f"HEAD:./{EXPERIMENTS}"))
        except RuntimeError:
            old = []
        if read_queue()[:len(old)] != old:
            out.append(f"Terra changed or removed entries of {EXPERIMENTS}; it may only add at the end")
    return out


def note_experiment(cid, answer):
    """After an accepted cycle: its experiment step goes into the log the next pack reads; a start keeps its entry."""
    x = (answer or {}).get("experiment") or {}
    if x.get("step") in ("start", "continue", "done", "drop") and x.get("id"):
        rec = {"ts": time.time(), "cycle": cid, "id": x["id"], "step": x["step"], "result": x.get("result") or ""}
        if x["step"] == "start":
            rec["entry"] = next((e for e in read_queue() if e["id"] == x["id"]), None)
        with open(os.path.join(ROOT, EXPERIMENT_LOG), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


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


def refresh_scorecard():
    """Pick up scorecard changes made while the long-running loop was alive."""
    importlib.invalidate_caches()
    importlib.reload(hard_limits)
    importlib.reload(scorecard.patch_notes)
    return importlib.reload(scorecard)


def cycle(kind):
    cid = f"{kind}-{time.strftime('%m%d-%H%M%S')}"
    os.makedirs(os.path.join(ROOT, "meta", "evidence"), exist_ok=True)
    pack = os.path.join(ROOT, "meta", "evidence", f"{cid}.json")
    # The loop can survive code updates for many hours. Load the current score
    # formula before each pack rather than retaining the version from startup.
    card = refresh_scorecard().build(ROOT, record=True)
    card["since_last_cycle"] = since_last(card, kind)
    card["experiments"] = view = experiments()
    lc = card.get("log_counts") or {}
    SAMPLE["settled"] = lc.get("settled_as_shop", 0) + lc.get("paid_as_buyer", 0)
    with open(pack, "w", encoding="utf-8") as f:
        json.dump(card, f, ensure_ascii=False, indent=1, default=str)
    log(f"{cid}: asking {MODELS[kind]}")
    answer, err = call_model(kind, cid, pack)
    # The owner can change limits during a long model turn. Validate against the
    # current protected module, never against limits retained from this morning.
    importlib.invalidate_caches()
    importlib.reload(hard_limits)
    rec = {"cycle": cid, "model": MODELS[kind], "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "answer": answer}
    paths = stage()
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
        problems += alert_problems(card, answer, code)
        problems += experiment_problems(kind, answer, view, code)
    problems += limit_problems(code)
    problems += queue_problems(kind, paths)
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
        if not problems:
            note_experiment(cid, answer)
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
        note_experiment(cid, answer)
        rec.update(status="running")
        log(f"{cid}: strategy document only, nothing to watch")
        record(rec)
        return rec
    restart_seller(code)
    bad = canary() if needs_trading_watch(code) else []
    if bad:
        log(f"{cid}: failed the watch: {'; '.join(bad)}")
        failed = try_undo(cid, sha, f"{cid} 10분 점검 실패: {'; '.join(bad)}")
        if failed:
            rec.update(status="canary_failed_kept", problems=bad + [failed])
        else:
            restart_seller(code)
            rec.update(status="canary_failed", problems=bad)
    else:
        note_experiment(cid, answer)    # only a change that stays: one the watch undid took its experiment step with it
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
        refresh_runtime()
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
            refresh_runtime()
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
