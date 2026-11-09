#!/usr/bin/env python3
"""seller_floor_margin A/B per arm on paid, matured sales only (W-20261109-2040-AX셀러전략, v4 22:05).

v1 (kept as floor_margin_verdict.v1.py) counted every shop-memory "deal" as a sale, counted a task's agreed and deal
rows as two talks, mapped the settle by unit (the unit's last settle wins) and fell back to the talk's own cost when
units_in had no row (v1-defect-demo.txt). v2 fixed the join; its independent review (C1 I4 M6) found the arm tables
mixed parts and per-talk net used mismatched numerator and denominator. v3:

Join on the market's own keys:
  memory talk (task, unit, price) -> public ledger "result" rows whose receipt.task is the task -> their order
  -> "settle" rows of that order (unit and amount must equal the talk's) -> "broken" rows of that order.
shop_memory writes several rows per task ("agreed", then "deal" or "unpaid"; 11/09: agreed+deal 22,912 tasks,
agreed+unpaid 6,602). A task is one talk: its first row's time is talk_ts and places it in a part, its last row is
memory_result, and payment is read from the ledger only. The market writes a result row for unpaid orders too
(checks.settled false, 923 on 11/09, none with a settle), so that row is taken as explicit non-payment.

Status per talk:
  paid       exactly one order with exactly one settle, same unit and amount
  mismatch   that settle has another unit or amount
  ambiguous  two or more orders for the task, or two or more settles for the order
  unpaid     a result row of the market says checks.settled false (its own word; shop memory "unpaid" is not)
  unknown    neither a settle nor that refusal, however long ago the talk was
v3.1 also called a talk unpaid once the ledger ran 3 h past it with no row; a missing or late row then entered the
denominator as a zero (parent probe 21:55). On 11/09 12:00-21:58, 393 of 404 memory-unpaid talks had the refusal row.
  no_deal    no row of the task reached a price (rejected or canceled only)
cost is units_in's (the unit's real cost); None when the unit has no row or rows that disagree (never the talk's cost).
A refund counts when its broken row is at most 6 h 1 min after the settle and not after asof.
asof is a hard cut for every input: ledger rows (result, settle, broken), memory rows and units_in rows with a later
time are not read, so a verdict at asof is the one that could have been made then (v3.1 read a settle 1 s after asof).

Parts by talk_ts against --window FROM TO and --stop (both required on the command line): before [.., FROM),
main [FROM, TO), tail [TO, stop), after [stop, ..). Every table is per part; the verdict reads main only.
Per-talk economics use the mature cohort: talks with talk_ts <= asof - 6 h 1 min - SETTLE_LAG (a settle comes within
the talk's payment hold), asof capped at the ledger's last row. Inside it, unknown, ambiguous, mismatch and
cost-unknown paid talks leave both numerator and denominator and are counted in "excluded";
net_per_talk = sum(net of paid) / (cohort talks - excluded). all_mature: every main talk is in the mature cohort and
every main paid sale is mature.

Arms are randomized by SKU x grade, so talks are not independent: compare() resamples SKU x grade clusters within each
arm (2,000 draws, fixed seed) and gives the 90% interval of B - A for net_per_talk, and for the same with refunds
replaced by the pooled refund rate x price (refunds are price-sized noise: one broken bed moves a 30-sale arm by
10,000+ per sale). It also gives net / list per talk, and net_unknown0: the same B - A with every unknown cohort
talk put back as a zero (a missing payment is most likely unpaid; leaving it out flatters the arm that has more).

  floor_margin_verdict.py <seller_memory_r3.json> <units_in.jsonl> <ledger.jsonl>... --study ID
      --window FROM TO --stop EPOCH [--asof EPOCH] [--rows]
Read only.
"""
import argparse
import collections
import json
import random
import re
import sys
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
MATURE = 6 * 3600 + 60
SETTLE_LAG = 15 * 60
KIND = re.compile(r'"kind":\s*"(settle|result|broken)"')
TS = re.compile(r'"ts":\s*([0-9.]+)')
EXCLUDED = ("unknown", "ambiguous", "mismatch")


def talk_ts(n):
    """shop_memory writes time.strftime("%Y-%m-%dT%H:%M:%S") on the shop's clock, which is KST."""
    return datetime.strptime(n["time"], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=KST).timestamp()


def part_of(t, window, stop):
    if not window:
        return "main"
    lo, hi = window
    if t < lo:
        return "before"
    if t < hi:
        return "main"
    if stop is None or t < stop:
        return "tail"
    return "after"


def group_talks(memory, study):
    talks, dup, seen = {}, 0, set()
    for n in memory:
        fm = n.get("floor_margin")
        if not isinstance(fm, dict) or fm.get("study") != study or not n.get("task"):
            continue
        key = (n["task"], n.get("time"), n.get("result"))
        if key in seen:
            dup += 1
            continue
        seen.add(key)
        talks.setdefault(n["task"], []).append(n)
    return talks, dup


def unit_costs(units_rows):
    """{unit: (cost or None, grade)}; rows of one unit that disagree on cost leave its cost unknown."""
    cost, bad = {}, set()
    for u in units_rows:
        k = u.get("unit")
        if k is None:
            continue
        if k in cost and cost[k][0] != u.get("cost"):
            bad.add(k)
        cost.setdefault(k, (u.get("cost"), u.get("grade")))
    for k in bad:
        cost[k] = (None, cost[k][1])
    return cost


def classify(memory, ledger_rows, units_rows, *, study, asof, ledger_end=None, window=None, stop=None, end_cell=None):
    """ledger_rows: an iterable of ledger dicts, consumed once. ledger_end: the ledger's last ts; None takes end_cell[0]
    after the rows are consumed (ledger_iter's cell, which sees every kind), else the largest ts among the rows."""
    talks, dup = group_talks(memory, study)
    results = collections.defaultdict(list)
    settles, broken = collections.defaultdict(list), collections.defaultdict(list)
    seen_end, kinds = 0.0, collections.Counter()
    for r in ledger_rows:
        k, d = r.get("kind"), r.get("data") or {}
        seen_end = max(seen_end, r.get("ts") or 0)
        kinds[k] += 1
        if k == "result":
            t = (d.get("receipt") or {}).get("task")
            if t in talks and d.get("order"):
                results[t].append((r["ts"], d["order"], (d.get("checks") or {}).get("settled") is False))
        elif k == "settle" and d.get("order"):
            settles[d["order"]].append((r["ts"], d.get("unit"), d.get("amount")))
        elif k == "broken" and d.get("order"):
            broken[d["order"]].append((r["ts"], d.get("refunded") or 0))
    if ledger_end is None:
        ledger_end = max(seen_end, end_cell[0]) if end_cell else seen_end
    asof = min(asof, ledger_end)
    # the cut: nothing written after asof is read
    orders = {t: {o for ts, o, _ in rs if ts <= asof} for t, rs in results.items()}
    unsettled = {o for rs in results.values() for ts, o, no in rs if no and ts <= asof}
    settles = {o: [x for x in s if x[0] <= asof] for o, s in settles.items()}
    talks = {t: [n for n in ns if talk_ts(n) <= asof] for t, ns in talks.items()}
    talks = {t: ns for t, ns in talks.items() if ns}
    cost = unit_costs([u for u in units_rows if u.get("ts") is None or u["ts"] <= asof])
    cohort_before = asof - MATURE - SETTLE_LAG
    rows = []
    for task, ns in talks.items():
        n, fm, t = ns[-1], ns[-1]["floor_margin"], talk_ts(ns[0])
        cu = cost.get(n.get("unit"), (None, None))
        r = {"task": task, "arm": fm.get("arm"), "unit": n.get("unit"), "sku": n.get("sku"), "grade": cu[1], "talk_ts": t,
             "part": part_of(t, window, stop), "price": n.get("price"), "list": fm.get("list"), "order": None,
             "memory_result": n.get("result"), "amount": None, "settle_ts": None, "cost": None, "refund": None,
             "mature": False, "in_cohort": t <= cohort_before, "net": None}
        os_ = orders.get(task, set())
        if not any(x.get("result") in ("agreed", "deal", "unpaid") for x in ns) and not os_:
            r["status"] = "no_deal"
        elif len(os_) > 1:
            r["status"] = "ambiguous"
        else:
            order = next(iter(os_), None)
            r["order"] = order
            s = settles.get(order, []) if order else []
            if len(s) > 1:
                r["status"] = "ambiguous"
            elif len(s) == 1:
                ts, unit, amount = s[0]
                r.update(settle_ts=ts, amount=amount)
                r["status"] = "paid" if unit == n.get("unit") and amount == n.get("price") else "mismatch"
            elif order in unsettled:
                r["status"] = "unpaid"
            else:
                r["status"] = "unknown"
        if r["status"] == "paid":
            r["cost"] = cu[0]
            r["mature"] = r["settle_ts"] <= asof - MATURE
            r["refund"] = sum(v for ts, v in broken.get(r["order"], ())
                              if ts <= min(r["settle_ts"] + MATURE, asof))
            if r["mature"] and r["cost"] is not None:
                r["net"] = r["amount"] - r["cost"] - r["refund"]
        rows.append(r)
    parts = {}
    for p in ("before", "main", "tail", "after"):
        sub = [r for r in rows if r["part"] == p]
        if sub:
            parts[p] = {str(a): arm_table([r for r in sub if r["arm"] == a]) for a in sorted({r["arm"] for r in sub},
                                                                                              key=str)}
    main = [r for r in rows if r["part"] == "main"]
    main_paid = [r["settle_ts"] for r in main if r["status"] == "paid"]
    warnings = []
    if talks and not kinds.get("settle"):
        warnings.append("no settle rows read from the ledger: wrong files or a changed format")
    return {"study": study, "asof": asof, "ledger_end": ledger_end, "window": window, "stop": stop,
            "cohort_talks_before": cohort_before, "duplicate_rows": dup,
            "full_mature_at": max(main_paid) + MATURE if main_paid else None,
            "all_mature": bool(main) and all(r["in_cohort"] and (r["status"] != "paid" or r["mature"]) for r in main),
            "compare_main": compare(main),
            "warnings": warnings, "parts": parts, "rows": rows}


def arm_table(rows):
    """One arm in one part. Status counts and money over every talk; per-talk economics over the mature cohort."""
    c = collections.Counter(talks=len(rows))
    for r in rows:
        c[r["status"]] += 1
        if r["status"] == "paid":
            c["revenue"] += r["amount"]
            if r["list"]:
                c["revenue_listed"] += r["amount"]
                c["list_paid"] += r["list"]
            c["immature"] += not r["mature"]
            c["cost_unknown"] += r["cost"] is None
    co = [r for r in rows if r["in_cohort"]]
    ex = [r for r in co if excluded_why(r)]
    exid = {id(r) for r in ex}
    used = [r for r in co if id(r) not in exid]
    paid = [r for r in used if r["status"] == "paid"]
    out = dict(c)
    out.update({
        "paid_rate": round(c["paid"] / c["talks"], 4) if c["talks"] else None,
        "price_over_list": round(c["revenue_listed"] / c["list_paid"], 4) if c["list_paid"] else None,
        "cohort": {"talks": len(used), "excluded": len(ex), "clusters": len({(r["sku"], r["grade"]) for r in used}),
                   "excluded_by": dict(collections.Counter(excluded_why(r) for r in ex)),
                   "paid": len(paid), "revenue": sum(r["amount"] for r in paid),
                   "refund": sum(r["refund"] for r in paid), "net": sum(r["net"] for r in paid),
                   "net_per_talk": round(sum(r["net"] for r in paid) / len(used)) if used else None,
                   "net_share": round(sum(r["net"] for r in paid) / sum(r["amount"] for r in paid), 4)
                   if paid and sum(r["amount"] for r in paid) else None}})
    return out


def excluded_why(r):
    """Why a cohort talk leaves the per-talk numbers, or None."""
    if r["status"] in EXCLUDED:
        return r["status"]
    if r["status"] == "paid" and r["cost"] is None:
        return "cost_unknown"
    if r["status"] == "paid" and not r["mature"]:
        return "immature"
    return None


def _ratio(cl, f):
    t = sum(x["talks"] for x in cl)
    return sum(x[f] for x in cl) / t if t else None


def compare(rows, draws=2000, seed=20261109):
    """B - A on the mature cohort of these rows, clusters SKU x grade resampled within each arm."""
    used = [r for r in rows if r["in_cohort"] and not excluded_why(r)]
    paid = [r for r in used if r["status"] == "paid"]
    rev = sum(r["amount"] for r in paid)
    pooled = sum(r["refund"] for r in paid) / rev if rev else 0.0
    cl = {}
    for r in used:
        x = cl.setdefault((r["arm"], r["sku"], r["grade"]), collections.Counter())
        x["talks"] += 1
        x["list"] += r["list"] or 0
        if r["status"] == "paid":
            x["net"] += r["net"]
            x["net_pooled"] += r["amount"] - r["cost"] - pooled * r["amount"]
    cl0 = {k: collections.Counter(talks=x["talks"], net=x["net"]) for k, x in cl.items()}
    unknown = collections.Counter()
    for r in rows:
        if r["in_cohort"] and excluded_why(r) == "unknown":
            cl0.setdefault((r["arm"], r["sku"], r["grade"]), collections.Counter())["talks"] += 1
            unknown[r["arm"]] += 1
    arms = {a: [v for (aa, _, _), v in cl.items() if aa == a] for a in ("A", "B")}
    arms0 = {a: [v for (aa, _, _), v in cl0.items() if aa == a] for a in ("A", "B")}
    out = {"pooled_refund_rate": round(pooled, 4), "unknown": {a: unknown[a] for a in ("A", "B")},
           "clusters": {a: len(v) for a, v in arms.items()}, "talks": {a: sum(x["talks"] for x in v) for a, v in arms.items()}}
    if not arms["A"] or not arms["B"]:
        return out
    rnd = random.Random(seed)
    for f, by in (("net", arms), ("net_pooled", arms), ("net_unknown0", arms0)):
        g = "net" if f == "net_unknown0" else f
        point = _ratio(by["B"], g) - _ratio(by["A"], g)
        ds = sorted(_ratio([rnd.choice(by["B"]) for _ in by["B"]], g)
                    - _ratio([rnd.choice(by["A"]) for _ in by["A"]], g) for _ in range(draws))
        out[f"{f}_per_talk_b_minus_a"] = {"point": round(point), "p05": round(ds[int(0.05 * draws)]),
                                          "p95": round(ds[int(0.95 * draws) - 1])}
    la, lb = sum(x["list"] for x in arms["A"]), sum(x["list"] for x in arms["B"])
    if la and lb:
        out["net_over_list_b_minus_a"] = round(sum(x["net"] for x in arms["B"]) / lb - sum(x["net"] for x in arms["A"]) / la, 5)
    return out


def ledger_iter(paths, end):
    """Streams the three kinds the join needs; any other row only moves end[0] (its ts read without json.loads)."""
    for p in paths:
        with open(p, encoding="utf-8") as f:      # a day of ledger is 200-250 MB
            for line in f:
                if not KIND.search(line, 0, 200):
                    m = TS.search(line, 0, 80)
                    if m:
                        end[0] = max(end[0], float(m.group(1)))
                    continue
                r = json.loads(line)
                end[0] = max(end[0], r["ts"])
                yield r


def run(argv=None):
    """(result, parsed args)."""
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("memory")
    ap.add_argument("units_in")
    ap.add_argument("ledger", nargs="+")
    ap.add_argument("--study", required=True)
    ap.add_argument("--window", nargs=2, type=float, required=True)
    ap.add_argument("--stop", type=float, required=True)
    ap.add_argument("--asof", type=float, default=9e12)
    ap.add_argument("--rows", action="store_true")
    a = ap.parse_args(argv)
    with open(a.memory, encoding="utf-8") as f:
        memory = json.load(f)["negotiations"]
    with open(a.units_in, encoding="utf-8") as f:
        units = [json.loads(x) for x in f if x.strip()]
    end = [0.0]
    out = classify(memory, ledger_iter(a.ledger, end), units, study=a.study, asof=a.asof, window=tuple(a.window),
                   stop=a.stop, end_cell=end)
    return out, a


def main():
    out, a = run()
    if not a.rows:
        out = {k: v for k, v in out.items() if k != "rows"}
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
