#!/usr/bin/env python3
"""drain_gate: when a stage deploy may stop the kit (11/27, replaces the ~/.ax-v6-51422de9/activity.py quiet window).

Run in the round-4 dir (reads logs/ only, writes nothing, standard library only):
    python3 -I drain_gate.py quiet [--dir .]   one check of the phase 1 rule; exit 0 quiet, 1 not
    python3 -I drain_gate.py drain [--dir .]   one check of the phase 2 rule; exit 0 drained, 1 not
    python3 -I drain_gate.py wait  [--dir .]   phase 1 up to AXD_GATE_QUIET_MAX s, then phase 2 up to
                                               AXD_GATE_DRAIN_MAX s; exit 0 when either passes, 3 at the drain cap

phase 1 (quiet) = activity.py: no inbound message unanswered in the last 90 s and no checkout issued in the last 300 s.
phase 2 (drain), used once phase 1 found no window (customers keep coming, 11/27 14:54 exit 24):
  - no inbound message without a later answer in the last AXD_GATE_LOOKBACK s (600; a kit run is at most 8 min, so
    a turn longer than 90 s still counts),
  - no turn in flight: the newest logs/react.jsonl step is older than AXD_GATE_REACT_BUSY s (45) or is a sleep
    (the kit logs no turn start/end; this step log is the only in-flight marker it writes),
  - no wallet send in flight: no sends.jsonl/refunds.jsonl intent (nonce/amount, no tx hash) in the lookback.
  Checkouts issued but not paid do not block: the buyer pays on chain and settles with the market without the kit,
  and the ledger cron (payment_ledger.py reconcile, outside the kit) records that sale or writes paid_unsettled.
A log that cannot be read counts as busy (fail closed). Other env: AXD_GATE_SLEEP (20), AXD_GATE_DRAIN_SLEEP (5).
"""
import json
import os
import re
import sys
import time
from pathlib import Path

HEX_TX = re.compile(r"^0x[0-9a-fA-F]{64}$")
TAIL = 4 << 20                       # bytes read from the end of a log: far more than 10 minutes of rows


def rows(path, required=True):
    """The JSON objects of a log (bad lines skipped); only the last TAIL bytes. Missing: [] or OSError if required."""
    if not path.exists():
        if required:
            raise OSError(f"{path.name} missing")
        return []
    with open(path, "rb") as f:
        size = f.seek(0, 2)
        f.seek(max(0, size - TAIL))
        data = f.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    if size > TAIL:
        lines = lines[1:]            # the first one is cut
    out = []
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def unanswered(msgs, now, span):
    """Inbound messages of the last span s with no answer after them (by id, else task; as activity.py)."""
    pending, answered = {}, {}
    for x in msgs:
        t = num(x.get("ts"))
        if t is None or t < now - span:
            continue
        ident = x.get("id") or x.get("task")
        if x.get("dir") == "in":
            pending[ident] = t
        elif x.get("dir") == "out":
            answered[ident] = t
    return sorted(i for i, t in pending.items() if answered.get(i, 0) < t)


def quiet(d, now):
    """[] when the phase 1 rule holds, else the reasons."""
    logs = Path(d) / "logs"
    try:
        msgs, cos = rows(logs / "messages.jsonl"), rows(logs / "checkouts.jsonl")
    except OSError as e:
        return [f"unreadable: {e}"]
    why = []
    n = unanswered(msgs, now, 90)
    if n:
        why.append(f"unanswered-90s {len(n)}")
    k = sum(1 for r in cos if r.get("event") == "issued" and (num(r.get("ts")) or 0) > now - 300)
    if k:
        why.append(f"new-issues-300s {k}")
    return why


def open_intents(logs, now, span):
    """Wallet sends the kit started (intent line) and has not finished (no tx hash line), in the last span s."""
    found = {}
    for name in ("sends.jsonl", "refunds.jsonl"):
        p = logs / name
        mtime = p.stat().st_mtime if p.exists() else 0
        for r in rows(p, required=False):
            tx = str(r.get("tx_hash") or r.get("tx") or "")
            order = r.get("order") or r.get("order_id")
            purpose = str(r.get("purpose") or r.get("kind") or ("refund" if name == "refunds.jsonl" else "")).lower()
            key = (str(r.get("intent") or r.get("id") or order), purpose)
            if HEX_TX.match(tx):
                found.pop(key, None)
            elif any(k in r for k in ("nonce", "amount", "amount_wei", "wei")):
                t = num(r.get("ts")) or mtime
                found[key] = t
    return sorted(k for k, t in found.items() if t >= now - span)


def drained(d, now, lookback=600, react_busy=45):
    """[] when the phase 2 rule holds, else the reasons."""
    logs = Path(d) / "logs"
    why = []
    try:
        n = unanswered(rows(logs / "messages.jsonl"), now, lookback)
        steps = [r for r in rows(logs / "react.jsonl", required=False) if num(r.get("ts")) is not None]
        sends = open_intents(logs, now, lookback)
    except OSError as e:
        return [f"unreadable: {e}"]
    if n:
        why.append(f"unanswered-{lookback:g}s {len(n)}")
    if steps:
        last = max(steps, key=lambda r: r["ts"])
        if now - last["ts"] < react_busy and last.get("action") != "sleep":
            why.append(f"turn in flight (last step {last.get('action')} {now - last['ts']:.0f}s ago)")
    if sends:
        why.append(f"wallet send in flight {len(sends)}")
    return why


def stamp(now):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))


def main(argv=None, env=None, clock=time.time, sleep=time.sleep):
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    if not argv or argv[0] not in ("quiet", "drain", "wait"):
        print(__doc__.strip().splitlines()[0])
        return 2
    cmd, d = argv[0], "."
    if "--dir" in argv[1:]:
        d = argv[argv.index("--dir") + 1]
    try:
        cfg = {k: float(env.get(k, v)) for k, v in (("AXD_GATE_QUIET_MAX", 900), ("AXD_GATE_SLEEP", 20),
               ("AXD_GATE_DRAIN_MAX", 900), ("AXD_GATE_DRAIN_SLEEP", 5), ("AXD_GATE_LOOKBACK", 600),
               ("AXD_GATE_REACT_BUSY", 45))}
    except ValueError:
        print("drain_gate: a AXD_GATE_* value is not a number")
        return 2
    lookback, busy = cfg["AXD_GATE_LOOKBACK"], cfg["AXD_GATE_REACT_BUSY"]

    def drain_now():
        return drained(d, clock(), lookback, busy)

    if cmd != "wait":
        now = clock()
        why = quiet(d, now) if cmd == "quiet" else drained(d, now, lookback, busy)
        print(stamp(now), cmd, "ok" if not why else "busy: " + "; ".join(why))
        return 0 if not why else 1
    for phase, check, cap, pause in ((1, lambda: quiet(d, clock()), cfg["AXD_GATE_QUIET_MAX"], cfg["AXD_GATE_SLEEP"]),
                                     (2, drain_now, cfg["AXD_GATE_DRAIN_MAX"], cfg["AXD_GATE_DRAIN_SLEEP"])):
        start = clock()
        while True:
            why = check()
            if not why:
                print(stamp(clock()), f"phase {phase} ok ({'quiet window' if phase == 1 else 'drained'})")
                return 0
            if clock() - start >= cap:
                print(stamp(clock()), f"phase {phase} gave up after {clock() - start:.0f}s: " + "; ".join(why))
                break
            sleep(pause)
    print(stamp(clock()), "drain cap reached: no restart")
    return 3


if __name__ == "__main__":
    sys.exit(main())
