"""Independent source-OFF fresh-slot experiment. No price or history inference.

Only generic bounded immutable IO primitives are shared with stock evidence;
its B1 activation, availability and decision rules are never used here.
"""
import hashlib
import json
import os
import re
import time
import uuid

from buyer_stock_evidence import _io, _once, _hash, _bytes, _order_id, _number

ENABLED = True
START = 1794075900  # 2026-11-08 03:25 KST
END = 1794077700    # 2026-11-08 03:55 KST
STUDY = "fresh-uuid-slot-20261108-0325"
ROOT = "buyer_fresh_rotation"


def active(now):
    return (ENABLED is True and _number(now) and _number(START) and _number(END)
            and 0 < START <= now < END <= START + 1800
            and isinstance(STUDY, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", STUDY) is not None)


def competing_window(now):
    # Read live module scalars, not the reserve-time snapshot. Keep the direct
    # decide seam fail-closed too, including a window opening during bounded IO.
    import buyer_stock_evidence
    import buyer_goods_first_one
    import buyer_goods_quote_probe
    import buyer_firm_trial
    import buyer_first95_trial
    import buyer_cap95_trial
    return any(m.ENABLED and m.START <= now < m.END for m in (
        buyer_stock_evidence, buyer_goods_first_one, buyer_goods_quote_probe,
        buyer_firm_trial, buyer_first95_trial, buyer_cap95_trial))


def reserve(md, order, *, now=None, conflict=False):
    live = now is None
    now = time.time() if now is None else now
    oid, started = _order_id(order.get("order")), order.get("_buyer_started_at")
    if (conflict or not active(now) or not oid or order.get("kind") != "goods"
            or not _number(started) or not START <= started <= now
            or order.get("task") or order.get("resume") or order.get("state") in ("REJECTED", "CANCELED", "COMPLETED")):
        return None
    study, start, end = STUDY, START, END
    allocation = str(uuid.uuid5(uuid.NAMESPACE_URL, study + ":" + oid))
    row = {"schema": 1, "event": "assignment", "order": oid, "study": study,
           "arm": "AB"[int.from_bytes(hashlib.sha256((study + ":" + allocation).encode()).digest(), "big") % 2],
           "allocation_id": allocation, "start": start, "end": end, "at": now, "started_at": started,
           "kind": "goods"}
    directory = os.path.join(md, ROOT, _hash([study, oid]))
    prior = _io(lambda: _once(os.path.join(directory, "assignment.json"), row))
    if (not prior or any(prior.get(k) != row[k] for k in row if k not in ("at", "started_at"))
            or not active(time.time() if live else now) or (study, start, end) != (STUDY, START, END)):
        return None
    return {**prior, "directory": directory}


def plan(baseline, fits, rivals, *, k, order_id):
    if not _order_id(order_id) or len(baseline) > 256:
        return None
    names = [s["name"] for s in baseline]
    if len(set(names)) != len(names):
        return None
    partitions = [[fits[n] > 0, n in rivals] for n in names]
    treatment = list(names)
    # The first fresh slot in final baseline order; only its own fit x rival
    # partition can supply the replacement. Other fresh partitions stay intact.
    indices = [i for i, p in enumerate(partitions) if not p[0]]
    if indices:
        first = indices[0]
        same = [i for i in indices if partitions[i] == partitions[first]]
        selected = same[int.from_bytes(hashlib.sha256(order_id.encode()).digest(), "big") % len(same)]
        treatment[first], treatment[selected] = treatment[selected], treatment[first]
    batch = max(0, k - 1)
    result = {"baseline": names, "treatment": treatment, "partitions": partitions, "k": k,
              "first_baseline": names[:batch], "first_treatment": treatment[:batch],
              "eligible": set(names[:batch]) != set(treatment[:batch]),
              "inputs": baseline, "fits": dict(fits), "rivals": sorted(rivals),
              "baseline_hash": _hash(names), "treatment_hash": _hash(treatment)}
    try:
        result = json.loads(_bytes(result))  # freeze before quote workers mutate shops
    except (ValueError, TypeError):
        return None
    return result


def decide(ctx, p, *, now=None):
    if not ctx or not p:
        return None
    def valid():
        at = time.time() if now is None else now
        return (active(at) and not competing_window(at)
                and (ctx["study"], ctx["start"], ctx["end"]) == (STUDY, START, END))
    if not valid():
        return p["baseline"]
    row = {"schema": 1, "event": "decision", "order": ctx["order"], "study": ctx["study"],
           "arm": ctx["arm"], "plan": p, "at": time.time() if now is None else now,
           "proposed_reorder": ctx["arm"] == "B" and p["eligible"], "actual_attempt": None}
    prior = _io(lambda: _once(os.path.join(ctx["directory"], "decision.json"), row))
    return (p["treatment"] if prior and prior.get("plan") == p and prior.get("arm") == ctx["arm"]
            and ctx["arm"] == "B" and p["eligible"] and valid() else p["baseline"])


def event(ctx, event, **fields):
    if not ctx:
        return False
    uid = str(uuid.uuid4())
    row = {"schema": 1, "event": event, "order": ctx["order"], "study": ctx["study"], "arm": ctx["arm"],
           "at": time.time(), **fields}
    return bool(_io(lambda: _once(os.path.join(ctx["directory"], event + "-" + uid + ".json"), row), False))
