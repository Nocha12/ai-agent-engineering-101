#!/usr/bin/env python3
"""Conversation test for the regulars rule (goal (8)): two made-up customers talk to the agent, offline from the market.

It runs the kit's own mind (agent_kit.Mind, Toolbox, Model) on a copy of agent.json in a temporary directory: no
wallets, no market tokens, no endpoint, an empty directory card, and the purchase hold in its notes. The agent forges
its own tools there (forge.json hashes as the kit makes them) and nothing is written to the real round-4 directory.

    OPENROUTER_API_KEY=... <kit venv python> -I ops/dialog_test.py --dir submissions/26622007/round-4 --kit scripts/round4

Scenario: customer A first visit (a chair, a tired day after a child's sports day) -> customer B first visit (rice) ->
customer A again. Checks: every message gets a reply; customers.json holds A and B apart; B's reply carries nothing
from A's visit; A's second reply refers back to the first visit; nothing about customers went into notes.md.
Prints the transcript and a JSON verdict; exit 0 when every check passes.
"""
import argparse
import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

A, B = "김민서-t3a1", "박도윤-t9b2"
A_CONTEXT = ("의자", "운동회")
SCRIPT = [
    (A, "안녕하세요. 식탁 의자 하나 보러 왔어요. 오늘 아이 운동회 다녀와서 좀 피곤하네요. 예산은 5만원쯤이요."),
    (B, "처음 와 봐요. 쌀 10kg 있나요? 가격이 제일 중요해요."),
    (A, "또 왔어요. 이번엔 주방 수납함 같은 거 있나요?"),
]
HOLD = "[운영자 지시 11/24 19:43] 매입 일시 중지: 해제 줄이 있을 때까지 도매 매입·재입고·결제 도구 제작과 사용을 하지 않는다.\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="the round-4 directory (only agent.json is read)")
    ap.add_argument("--kit", required=True, help="scripts/round4")
    ap.add_argument("--keep", action="store_true", help="keep the temporary directory (it holds the made-up customers.json)")
    a = ap.parse_args()
    sys.path.insert(0, str(Path(a.kit).resolve()))
    import agent_kit as ak                                  # noqa: E402  the kit itself, unchanged
    import market_kit as mk                                 # noqa: E402

    tmp = Path(tempfile.mkdtemp(prefix="r4-dialog-"))
    shutil.copy(Path(a.dir) / "agent.json", tmp / "agent.json")
    (tmp / "logs").mkdir()
    (tmp / "notes.md").write_text(HOLD, encoding="utf-8")
    mk.LOG_FILE = tmp / "logs" / "agent-kit.log"
    agent = json.loads((tmp / "agent.json").read_text(encoding="utf-8"))
    ctx = {"market": "https://market.invalid", "name": "nocha12", "tokens": {}, "addresses": {"shop": "0x0"},
           "signer": ak.Signer({}, tmp)}
    toolbox = ak.Toolbox(tmp, ctx)
    mind = ak.Mind(tmp, agent, ak.Model(agent), toolbox, ctx, ["shop"])
    mind.directory = {}
    transcript, hist = [], {}
    for who, text in SCRIPT:
        frm = f"an agent that says it is {who!r} (unverified: anyone can claim a name), over A2A"
        h = hist.setdefault(who, [])
        ev = mind.deliver(ak.Event("a2a", frm, text, None, f"task-{who}-{len(h)}", []))
        t = threading.Thread(target=mind.run_once, args=([ev],), daemon=True)
        t0 = time.time()
        t.start()
        ev.done.wait(900)
        secs = round(time.time() - t0, 1)                   # the market's customer waits ak.A2A_WAIT (80 s) for this
        t.join(900)
        reply = (ev.answer or {}).get("reply")
        transcript.append({"customer": who, "said": text, "reply": reply, "seconds": secs})
        print(f"\n[{who}] {text}\n[nocha12 {secs}s] {reply}", flush=True)
    for _ in range(2):                                      # a quiet wake: the agent may forge now, nobody waiting
        mind.run_once([])
        if "customer_memory" in toolbox.tools:
            break
        time.sleep(1)
    mem_path = tmp / "customers.json"
    mem = json.loads(mem_path.read_text(encoding="utf-8")) if mem_path.exists() else None
    flat = json.dumps(mem, ensure_ascii=False) if mem is not None else ""

    def record(name):
        if isinstance(mem, dict):
            for k in (name, f"customers.{name}"):
                if k in mem:
                    return mem[k]
            for v in mem.values():
                if isinstance(v, dict) and name in v:
                    return v[name]
        return None
    ra, rb = record(A), record(B)
    rb_text = json.dumps(rb, ensure_ascii=False) if rb is not None else ""

    def visits(rec):
        return next((v for k, v in (rec or {}).items() if "방문" in k and "횟수" in k and isinstance(v, int)), None)
    tool = toolbox.tools.get("customer_memory") or {}
    perm = tool.get("perm") or {}
    b_reply, a2_reply = transcript[1]["reply"] or "", transcript[2]["reply"] or ""
    notes = (tmp / "notes.md").read_text(encoding="utf-8")
    checks = {
        "every_message_answered": all(x["reply"] for x in transcript),
        "memory_tool_forged": bool(tool),
        "memory_tool_permission_write_only": bool(tool) and set(perm.get("write") or []) <= {"customers.json", "customers.test.json"}
        and not any(perm.get(k) for k in ("network", "identity", "wallet", "handles", "pay_limit")),
        "memory_has_both_apart": ra is not None and rb is not None,
        "b_record_has_nothing_of_a": rb is not None and not any(w in rb_text for w in A_CONTEXT),
        "b_reply_has_nothing_of_a": not any(w in b_reply for w in A_CONTEXT),
        "a_second_visit_recalls_first": any(w in a2_reply for w in A_CONTEXT + ("지난번", "저번")),
        "no_customer_info_in_notes": not any(w in notes for w in (A, B) + A_CONTEXT),
        "test_file_cleaned": not (tmp / "customers.test.json").exists(),
        "visits_counted": visits(ra) == 1 and visits(rb) == 1,   # A's two visits are minutes apart: one visit
        "b_sensitivity_not_raised_by_words": rb is not None and str(next((v for k, v in rb.items() if "민감" in k), "mid")).strip() in ("mid", ""),
        "replies_within_a2a_wait": all(x["seconds"] <= ak.A2A_WAIT for x in transcript),
    }
    print("\n" + json.dumps({"ok": all(checks.values()), "checks": checks, "memory": mem, "dir": str(tmp)},
                            ensure_ascii=False, indent=1))
    if not a.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
