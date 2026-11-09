#!/usr/bin/env python3
"""seller_food_margin A/B per food unit supplied in the window (W-20261109-2040-AX셀러전략, v4 11/10 00:40).

The treatment is assigned to a unit (sha256("food-margin|salt|unit"), first byte odd is B) and every food unit ends
sold, expired or still in stock, so the verdict is intention to treat per unit supplied, not per talk: a unit counts in
its arm whether or not any talk on it was treated (a markdown talk is never treated, keep < 1) and its outcome is read
from the public ledger only.

Units: our food units. Ours = a "supply" row of shop --me (default nocha12) or a units_in row (the shop's own record of
what it holds). Food = units_in kind "food" for the unit, else the kind units_in gives the unit's SKU.
supply_ts = the supply row's time (else units_in supplied_at). Parts by supply_ts: before [.., start),
main [start, cohort_end), late [cohort_end, stop), after [stop, ..); cohort_end = min(stop, asof - COHORT_LAG)
(4 h: 11/09 our food units sold 50/90/95/99% within .2/.9/1.5/3.3 h of supply). The verdict reads main only.

asof is a hard cut on every input and is capped at the end of each: the ledger's last row, units_in's last row and the
memory's last talk (a sale after the memory's end would read as having no talk, a supply after units_in's end as cost
unknown). Ledger coverage: the files' [first, last] row times, joined where the next file starts within 10 min of the
last one's end. A unit supplied outside every coverage span is "uncovered" (its sale may be in a file not read). A unit that is in
neither units_in nor a supply row read is not seen at all, so its sales are not in the shop spans either.

Status per unit (rows at or before asof only):
  sold       exactly one "settle" row of the unit and no "expire" row of ours naming it
  expired    an "expire" row of shop --me names it and no settle
  ambiguous  two or more settles, or a settle and an expire
  unknown    neither, and units_in's expires_at is more than EXPIRE_GRACE before asof (the expire row is missing;
             11/09 the one expire row of ours came 19.5 s after expires_at). Never read as a loss.
  uncovered  supplied outside the ledger coverage
  pending    neither yet
cost = units_in's cost for the unit (None when its rows disagree or it has none); never the talk's, never the supply
total / qty, which differs by unit within one supply (316 of 330 multi-unit food supplies mix grades). One exact
exception: when exactly one unit of a supply has no units_in row and every other unit has one, its cost is the supply
total minus theirs ("supply_residual"; supply totals equal the sum of their units' costs in all 1,776 fully known
supplies 11/08-09). Units with no units_in row are the ones sold seconds after supply (11/09 148 of 2,058, all sold
before the next units_in tick), so leaving them out is outcome dependent: compare() also gives a bound with them in.
net: sold = amount - cost - refunds of the order (broken rows at or before min(settle + 6 h 1 min, asof); food did not
break once in 127,234 market food sales 11/07-09, so a sold unit is resolved at its settle; refund_open counts sold
units still inside that 6 h 1 min); expired = -cost.
Excluded (out of numerator and denominator, counted per arm): ambiguous, unknown, uncovered, cost unknown.

compare(main): B - A of net per unit supplied, stratified: within each SKU x grade with units of both arms, mean B -
mean A, weighted by the cluster's units (units of one SKU x grade share buyers; 4 SKUs carry 98% of our food deals, and
costs run 8k-30k, so a pooled mean would mix arm imbalance across SKUs into the effect). Ways: pending as 0 (cash
by asof), pending as -cost (a B unit not sold yet may never sell), and talk_cost: pending as 0 with each cost-unknown unit
put back at the cost its shop-memory talks name (the exclusion is outcome dependent: the units with no units_in row are
the ones sold fastest; talk cost equalled units_in cost in 83,022 of 83,022 talks 11/08-10 and no unit had two, and it
is used here only, never as the unit's cost). Reported, not gated: "bound", pending as 0 with each excluded unit at its
arm's unfavourable end (bound_net: a sold one at its price minus the highest (B) or lowest (A) known cost of its SKU x
grade); in a placebo run with units supplied 11/09 13:36-18:55 (before the treatment) its interval was -972..-53 while
the others held 0 (-161..221), so as a gate it would refuse any effect under ~500 per unit.
Each way gives a cluster bootstrap 90% interval (2,000 draws, fixed seed) and a t interval over the cluster differences with G - 1 degrees of freedom; lo = the lower of the two p05, hi = the higher of the two p95.
Interference between unit arms of one SKU has no fixed sign (seller_food_margin.py), so a keep also needs the shop:
our food net per hour (known net of food sales settled in the span scaled by sales / known-cost sales, minus the expire
rows' own cost) over [start, min(stop, asof)) not under the same clock hours a day before, and not under it either with
the unknown-cost sales at their talk cost when every one has one; both spans must be inside the ledger coverage, else
no_harm is None. net_per_hour_low / _high (unknown-cost sales at the span's lowest / highest known net) are reported
only: on 11/09 they spread 30-40% of the level. The span before the window (264k/h) against its day before (357k/h)
with nothing switched on shows how noisy this gate is: it guards against harm, it does not find an effect. Reported, not gated: per arm the sales
made while the SKU x grade had no covered unit of the other arm in stock (other_arm_in_stock False), sales whose talk
was not treated (markdown, ask cap, trial; settle -> result row of the order -> receipt task -> shop-memory talk),
sales at or under the usual floor (cost x 1.0025 + 1), and recorded arms that disagree with the salt's (should be 0).

Verdict: wait before start + MIN_HOURS (9 h, the registry's judgeable time) at asof; keep when every gate holds: main
per arm >= 30 units, >= 4 clusters with >= 5 units of each arm, unresolved (excluded and not put back by talk_cost) < 5%
per arm, lo > 0 for pending0, pending_cost and talk_cost, shop no_harm true; revert when the units, clusters and
unresolved gates hold and hi < 0 for pending 0 and pending -cost; else
hold.

  food_margin_verdict.py <seller_memory_r3.json> <units_in.jsonl> <ledger.jsonl>... --study ID --salt SALT
      --start EPOCH --stop EPOCH [--asof EPOCH] [--me SHOP] [--units]
Pass the ledger files from the day before start through asof (11-08, 11-09, 11-10 for the 11/09 22:55 window).
Read only.
"""
import argparse
import collections
import hashlib
import json
import math
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
MATURE = 6 * 3600 + 60
COHORT_LAG = 4 * 3600
EXPIRE_GRACE = 3600
MIN_HOURS = 9
DAY = 86400.0
FILE_GAP = 600
MIN_CLUSTER_UNITS = 5
KIND = re.compile(r'"kind":\s*"(supply|settle|result|broken|expire)"')
EXCLUDED = ("ambiguous", "unknown", "uncovered")
# one-sided 95% t quantiles by degrees of freedom
T95 = [None, 6.314, 2.920, 2.353, 2.132, 2.015, 1.943, 1.895, 1.860, 1.833, 1.812, 1.796, 1.782, 1.771, 1.761,
       1.753, 1.746, 1.740, 1.734, 1.729, 1.725, 1.721, 1.717, 1.714, 1.711, 1.708, 1.706, 1.703, 1.701, 1.699, 1.697]


def t95(df):
    if df < len(T95):
        return T95[df]
    return 1.684 if df <= 40 else 1.671 if df <= 60 else 1.658 if df <= 120 else 1.645


def arm(salt, unit):
    """seller_food_margin.arm with the salt given."""
    h = hashlib.sha256(f"food-margin|{salt}|{unit}".encode("utf-8", "surrogatepass")).digest()
    return "B" if h[0] % 2 else "A"


def talk_ts(n):
    """shop_memory writes time.strftime("%Y-%m-%dT%H:%M:%S") on the shop's clock, which is KST."""
    return datetime.strptime(n["time"], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=KST).timestamp()


def kst(t):
    return datetime.fromtimestamp(t, KST).strftime("%Y-%m-%dT%H:%M:%S")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def unit_info(units_rows):
    """{unit: row} with cost None for units whose rows disagree on cost; {sku: kind}."""
    info, bad, sku_kind = {}, set(), {}
    for u in units_rows:
        k = u.get("unit")
        if k is None:
            continue
        if u.get("sku") and u.get("kind"):
            sku_kind.setdefault(u["sku"], u["kind"])
        if k in info and info[k].get("cost") != u.get("cost"):
            bad.add(k)
        info.setdefault(k, dict(u))
    for k in bad:
        info[k]["cost"] = None
    return info, sku_kind


def covered(spans):
    """[(first, last)] of the files read -> the joined spans."""
    out = []
    for a, b in sorted(s for s in spans if s[0] is not None):
        if out and a <= out[-1][1] + FILE_GAP:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def inside(cov, a, b):
    return any(x <= a and b <= y for x, y in cov)


def classify(memory, ledger_rows, units_rows, *, study, salt, start, stop, asof, ledger_end=None, me="nocha12",
             coverage=None, file_spans=None, units_in_end=None, memory_end=None):
    """ledger_rows: an iterable of ledger dicts, consumed once. ledger_end, units_in_end, memory_end: each input's
    last time (None: the largest read). coverage: [(first, last)] of the ledger read; None: file_spans (ledger_iter's list, read after the rows are
    consumed) if given, else one span from the first to the last row read."""
    malformed_units = sum(1 for u in units_rows if not isinstance(u, dict))
    units_rows = [u for u in units_rows if isinstance(u, dict) and _num(u.get("ts"))]
    maybe_ours = {u.get("unit") for u in units_rows}
    sup_rows, settle_rows, expire_rows, result_rows, broken_rows = [], [], [], [], []
    first, last, malformed = None, 0.0, 0
    for r in ledger_rows:
        ts = r.get("ts") if isinstance(r, dict) else None
        if not _num(ts) or not isinstance(r.get("data") or {}, dict):
            malformed += 1
            continue
        first, last = ts if first is None else min(first, ts), max(last, ts)
        k, d = r.get("kind"), r.get("data") or {}
        if k == "supply" and d.get("shop") == me:
            sup_rows.append(r)
            maybe_ours.update(d.get("units") or ())
        elif k == "settle" and d.get("unit") in maybe_ours:
            settle_rows.append(r)
        elif k == "expire" and d.get("shop") == me:
            expire_rows.append(r)
        elif k == "result" and (d.get("receipt") or {}).get("unit") in maybe_ours and d.get("order"):
            result_rows.append((ts, d["order"], d["receipt"].get("task")))
        elif k == "broken" and d.get("order"):
            broken_rows.append((ts, d["order"], d.get("refunded") or 0))
    if ledger_end is None:
        ledger_end = last
    if coverage is None:
        coverage = list(file_spans) if file_spans else [(first, last)]
    cov = covered(coverage)
    talk_times = []
    for n in memory:
        try:
            talk_times.append(talk_ts(n))
        except (KeyError, TypeError, ValueError):
            talk_times.append(None)
    ends = {"ledger": ledger_end,
            "units_in": units_in_end if units_in_end is not None else max((u["ts"] for u in units_rows), default=None),
            "memory": memory_end if memory_end is not None else max((t for t in talk_times if t is not None),
                                                                     default=None)}
    requested_asof = asof
    asof = min([asof] + [e for e in ends.values() if e is not None])
    cov = [[a, min(b, asof)] for a, b in cov if a <= asof]
    cov_start = cov[0][0] if cov else None

    info, sku_kind = unit_info([u for u in units_rows if u["ts"] <= asof])
    supply_ts, supply_sku, supplies = {}, {}, []
    for r in sup_rows:
        if r["ts"] > asof:
            continue
        d = r["data"]
        supplies.append(d)
        for u in d.get("units") or ():
            supply_ts.setdefault(u, r["ts"])
            supply_sku.setdefault(u, d.get("sku"))
    derived = residual_costs(supplies, info)
    ours = set(supply_ts) | set(info)

    def is_food(u):
        i = info.get(u) or {}
        return (i.get("kind") or sku_kind.get(i.get("sku") or supply_sku.get(u))) == "food"

    food = {u for u in ours if is_food(u)}
    settles, expires = collections.defaultdict(list), collections.defaultdict(list)
    for r in settle_rows:
        if r["ts"] <= asof and r["data"].get("unit") in food:
            settles[r["data"]["unit"]].append(r)
    for r in expire_rows:
        if r["ts"] <= asof:
            for u in r["data"].get("units") or ():
                if u in food:
                    expires[u].append(r)
    broken = collections.defaultdict(list)
    for ts, o, v in broken_rows:
        if ts <= asof:
            broken[o].append((ts, v))
    task_of = collections.defaultdict(set)
    for ts, o, task in result_rows:
        if ts <= asof and task:
            task_of[o].add(task)
    talks, talk_cost = collections.defaultdict(list), {}
    for n, t in zip(memory, talk_times):
        if t is None:
            malformed += 1
        elif n.get("task") and t <= asof:
            talks[n["task"]].append(n)
            if n.get("unit") in food and _num(n.get("cost")):
                talk_cost.setdefault(n["unit"], set()).add(n["cost"])

    cohort_end = min(stop, asof - COHORT_LAG)
    out_rows, mismatch = [], 0
    for u in sorted(food):
        i = info.get(u) or {}
        t = supply_ts.get(u, i.get("supplied_at"))
        if not _num(t):
            continue
        part = "before" if t < start else "main" if t < cohort_end else "late" if t < stop else "after"
        s, e = settles.get(u, []), expires.get(u, [])
        grade = i.get("grade") or (s[0]["data"].get("grade") if len(s) == 1 else None)
        r = {"unit": u, "arm": arm(salt, u), "sku": i.get("sku") or supply_sku.get(u), "grade": grade,
             "supply_ts": t, "part": part, "cost": i.get("cost"), "cost_source": "units_in" if u in info else None,
             "list": i.get("list_price"), "expires_at": i.get("expires_at"), "status": None, "order": None,
             "amount": None, "settle_ts": None, "end_ts": None, "refund": None, "refund_open": None, "net": None,
             "excluded_by": None, "sale_talk": None, "recorded_arm": None, "other_arm_in_stock": None,
             "talk_cost": next(iter(talk_cost[u])) if len(talk_cost.get(u, ())) == 1 else None}
        if u not in info and u in derived:
            r["cost"], r["cost_source"] = derived[u], "supply_residual"
        if s:
            r["settle_ts"] = s[0]["ts"]
        if not any(a <= t <= b for a, b in cov):
            r["status"] = "uncovered"
        elif len(s) > 1 or (s and e) or len(e) > 1:
            r["status"] = "ambiguous"
        elif s:
            d = s[0]["data"]
            if _num(d.get("amount")):
                r.update(status="sold", order=d.get("order"), amount=d["amount"], end_ts=s[0]["ts"])
            else:
                r["status"] = "ambiguous"
                malformed += 1
        elif e:
            r.update(status="expired", end_ts=e[0]["ts"])
        elif _num(r["expires_at"]) and r["expires_at"] + EXPIRE_GRACE < asof:
            r["status"] = "unknown"
        else:
            r["status"] = "pending"
        if r["status"] == "sold":
            r["refund"] = sum(v for ts, v in broken.get(r["order"], ()) if ts <= min(r["settle_ts"] + MATURE, asof))
            r["refund_open"] = r["settle_ts"] + MATURE > asof
            tasks = {task for task in task_of.get(r["order"], ()) if any(n.get("unit") == u for n in talks.get(task, ()))}
            ns = [n for task in tasks for n in talks[task]]
            recs = [n["food_margin"] for n in ns
                    if isinstance(n.get("food_margin"), dict) and n["food_margin"].get("study") == study]
            r["sale_talk"] = "treated" if recs else "untreated" if ns else "no_talk"
            if recs:
                r["recorded_arm"] = recs[-1].get("arm")
                mismatch += any(x.get("arm") != r["arm"] for x in recs)
        if r["status"] in EXCLUDED:
            r["excluded_by"] = r["status"]
        elif not _num(r["cost"]):
            r["excluded_by"] = "cost_unknown"
        elif r["status"] == "sold":
            r["net"] = r["amount"] - r["cost"] - r["refund"]
        elif r["status"] == "expired":
            r["net"] = -r["cost"]
        out_rows.append(r)

    exposure(out_rows)
    arms = {}
    for p in ("before", "main", "late", "after"):
        sub = [r for r in out_rows if r["part"] == p]
        if sub:
            arms[p] = {a: arm_table([r for r in sub if r["arm"] == a]) for a in ("A", "B")}
    main = [r for r in out_rows if r["part"] == "main"]
    cmp_ = compare(main)
    shop_ = shop(out_rows, expire_rows, start, min(stop, asof), cov, asof)
    judgeable = asof >= start + MIN_HOURS * 3600
    g = gates(cmp_, shop_, judgeable=judgeable)
    warnings = []
    if food and not settles:
        warnings.append("no settle row of our food units read: wrong files or a changed format")
    if not supplies:
        warnings.append("no supply row of shop --me read")
    main_from = min([r["supply_ts"] for r in main] + [start])
    if not inside(cov, min(main_from, start - DAY), asof):
        warnings.append("the ledger read does not cover the day before start through asof without a gap")
    if malformed or malformed_units:
        warnings.append(f"{malformed + malformed_units} malformed rows skipped")
    for k, e in ends.items():
        if e is not None and e < requested_asof - 300 and e == asof:
            warnings.append(f"asof cut {round((requested_asof - e) / 60)} min back to the {k} input's end")
    return {"study": study, "salt": salt, "start": start, "stop": stop, "asof": asof, "input_ends": ends,
            "coverage": cov, "cohort_end": cohort_end, "judgeable_at": start + MIN_HOURS * 3600,
            "arm_mismatch": mismatch, "arms": arms, "compare_main": cmp_, "shop": shop_, "gates": g,
            "verdict": decide(g, cmp_), "warnings": warnings, "units": out_rows}


def residual_costs(supplies, info):
    """{unit: cost} for the one unit of a supply with no units_in row, when every other unit of it has a cost."""
    out = {}
    for d in supplies:
        us = d.get("units") or []
        if not _num(d.get("total")) or not us:
            continue
        miss = [u for u in us if u not in info]
        if len(miss) != 1 or any(not _num(info[u].get("cost")) for u in us if u in info):
            continue
        rest = d["total"] - sum(info[u]["cost"] for u in us if u in info)
        if rest > 0:
            out[miss[0]] = rest
    return out


def exposure(rows):
    """other_arm_in_stock for each sold unit of known SKU x grade: was a covered unit of the other arm of the same SKU x
    grade supplied before its settle and not yet sold or expired then. None for uncovered or unknown-grade units."""
    by = collections.defaultdict(list)
    for r in rows:
        if r["grade"] is not None and r["sku"] is not None and r["status"] in ("sold", "expired", "pending"):
            by[(r["sku"], r["grade"])].append(r)
    for rs in by.values():
        for r in rs:
            if r["status"] != "sold":
                continue
            t = r["settle_ts"]
            r["other_arm_in_stock"] = any(o["arm"] != r["arm"] and o["supply_ts"] <= t
                                          and (o["end_ts"] is None or o["end_ts"] > t) for o in rs)


def arm_table(rows):
    c = collections.Counter(units=len(rows))
    for r in rows:
        c[r["status"]] += 1
        c["cost_source_" + str(r["cost_source"])] += 1
        if r["excluded_by"]:
            c["excluded"] += 1
            c["excluded_" + r["excluded_by"]] += 1
        if r["status"] == "sold":
            c["sales"] += r["amount"]
            c["refund"] += r["refund"] or 0
            c["refund_open"] += bool(r["refund_open"])
            c["sale_talk_" + r["sale_talk"]] += 1
            c["sold_without_other_arm_stock"] += r["other_arm_in_stock"] is False
            if _num(r["cost"]) and r["amount"] <= r["cost"] * 1.0025 + 1:
                c["sold_at_or_under_floor"] += 1
        if r["net"] is not None:
            c["net"] += r["net"]
    out = dict(c)
    out["clusters"] = len({(r["sku"], r["grade"]) for r in rows})
    n = len(rows) - c["excluded"]
    out["net_per_unit"] = round(c["net"] / n, 2) if n else None
    out["sold_share"] = round(sum(1 for r in rows if r["status"] == "sold" and not r["excluded_by"]) / n, 4) if n else None
    return out


def talk_net(r):
    """A cost-unknown unit's net at the cost its shop-memory talks name (None without one)."""
    c = r.get("talk_cost")
    if r["excluded_by"] != "cost_unknown" or not _num(c):
        return None
    if r["status"] == "sold":
        return r["amount"] - c - (r["refund"] or 0)
    return -c if r["status"] == "expired" else 0.0


def bound_net(r, cluster_costs, arm_extreme):
    """An excluded unit at its arm's unfavourable end: a sold unit at its price minus the highest (B) or lowest (A)
    known cost of its SKU x grade in main; anything else at the arm's lowest (B) or highest (A) known net."""
    if r["status"] == "sold" and _num(r.get("amount")) and cluster_costs:
        c = max(cluster_costs) if r["arm"] == "B" else min(cluster_costs)
        return r["amount"] - c - (r.get("refund") or 0)
    return arm_extreme


def _values(rows, way):
    """{cluster: {"A": [..], "B": [..]}} of unit values for one way of counting."""
    known = {a: [r["net"] for r in rows if r["arm"] == a and r["net"] is not None] for a in ("A", "B")}
    fill = {"B": min(known["B"], default=None), "A": max(known["A"], default=None)}
    costs = collections.defaultdict(list)
    for r in rows:
        if r["net"] is not None:
            costs[(r["sku"], r["grade"])].append(r["cost"])
    cl = collections.defaultdict(lambda: {"A": [], "B": []})
    for r in rows:
        if r["excluded_by"]:
            val = (bound_net(r, costs.get((r["sku"], r["grade"])), fill[r["arm"]]) if way == "bound"
                   else talk_net(r) if way == "talk_cost" else None)
            if val is None:
                continue
        elif r["status"] == "pending":
            val = -r["cost"] if way == "pending_cost" else 0.0
        else:
            val = r["net"]
        cl[(r["sku"], r["grade"])][r["arm"]].append(val)
    return cl


def _strat(cl, keys):
    num = den = 0.0
    for k in keys:
        a, b = cl[k]["A"], cl[k]["B"]
        if a and b:
            w = len(a) + len(b)
            num += w * (sum(b) / len(b) - sum(a) / len(a))
            den += w
    return num / den if den else None


def _interval(cl, draws=2000, seed=20261109):
    keys = sorted((k for k in cl if min(len(cl[k]["A"]), len(cl[k]["B"])) >= MIN_CLUSTER_UNITS), key=str)
    point = _strat(cl, keys)
    if point is None:
        return None
    rnd, ds = random.Random(seed), []
    for _ in range(draws):
        d = _strat(cl, [rnd.choice(keys) for _ in keys])
        if d is not None:
            ds.append(d)
    ds.sort()
    q = lambda p: ds[min(len(ds) - 1, int(p * len(ds)))]
    g = len(keys)
    out = {"point": round(point, 2), "p05": round(q(.05), 2), "p95": round(q(.95), 2), "clusters": g}
    if g >= 2:
        w = [len(cl[k]["A"]) + len(cl[k]["B"]) for k in keys]
        d = [sum(cl[k]["B"]) / len(cl[k]["B"]) - sum(cl[k]["A"]) / len(cl[k]["A"]) for k in keys]
        sw = sum(w)
        se = math.sqrt(sum((wi * (di - point)) ** 2 for wi, di in zip(w, d)) * g / (g - 1)) / sw
        t = t95(g - 1)
        out.update(t_p05=round(point - t * se, 2), t_p95=round(point + t * se, 2))
        out["lo"], out["hi"] = min(out["p05"], out["t_p05"]), max(out["p95"], out["t_p95"])
    else:
        out["lo"], out["hi"] = None, None
    return out


def compare(rows):
    rows = [r for r in rows if r["arm"] in ("A", "B")]
    per = collections.defaultdict(collections.Counter)
    for r in rows:
        per[(r["sku"], r["grade"])][r["arm"]] += 1
    out = {"units": {a: sum(1 for r in rows if r["arm"] == a) for a in ("A", "B")},
           "clusters_both_arms": sum(1 for c in per.values() if min(c["A"], c["B"]) >= MIN_CLUSTER_UNITS),
           "excluded": {a: sum(1 for r in rows if r["arm"] == a and r["excluded_by"]) for a in ("A", "B")},
           "unresolved": {a: sum(1 for r in rows if r["arm"] == a and r["excluded_by"] and talk_net(r) is None)
                          for a in ("A", "B")},
           "pending": {a: sum(1 for r in rows if r["arm"] == a and r["status"] == "pending") for a in ("A", "B")}}
    for way in ("pending0", "pending_cost", "talk_cost", "bound"):
        iv = _interval(_values(rows, way))
        if iv:
            out[f"net_{way}_b_minus_a"] = iv
    return out


def shop(rows, expire_rows, lo, hi, cov, asof):
    """Our food net per hour in [lo, hi) and in the same clock hours a day before, by settle and expire time."""
    food_units = {r["unit"] for r in rows}

    def span(a, b):
        ok = inside(cov, a, b) and b > a
        sold = [r for r in rows if r["settle_ts"] is not None and a <= r["settle_ts"] < b]
        known = [r for r in sold if r["net"] is not None]
        exp = sum((r["data"].get("cost") or 0) for r in expire_rows
                  if a <= r["ts"] < b and r["ts"] <= asof and set(r["data"].get("units") or ()) & food_units)
        n_unk = len(sold) - len(known)
        nets = [r["net"] for r in known]
        net = sum(nets) * (len(sold) / len(known) if known else 0) - exp
        talk = [talk_net(r) for r in sold if r["net"] is None]
        h = (b - a) / 3600
        per = lambda x: round(x / h, 2) if ok and h > 0 and (known or not n_unk) else None
        return {"from": a, "to": b, "covered": ok, "sales": len(sold), "unknown_cost_sales": n_unk,
                "unknown_share": round(n_unk / len(sold), 4) if sold else 0.0, "expired_cost": exp,
                "net_scaled": round(net, 2), "net_per_hour": per(net),
                "net_per_hour_talk_cost": per(sum(nets) + sum(talk) - exp) if None not in talk else None,
                "net_per_hour_low": per(sum(nets) + n_unk * min(nets, default=0) - exp),
                "net_per_hour_high": per(sum(nets) + n_unk * max(nets, default=0) - exp)}
    w, d = span(lo, hi), span(lo - DAY, hi - DAY)
    p = span(lo - (hi - lo), lo)
    usable = all(x["net_per_hour"] is not None for x in (w, d))
    tc = all(x["net_per_hour_talk_cost"] is not None for x in (w, d))
    return {"window": w, "day_before": d, "span_before": p,
            "no_harm": (w["net_per_hour"] >= d["net_per_hour"]
                        and (not tc or w["net_per_hour_talk_cost"] >= d["net_per_hour_talk_cost"])) if usable else None}


def gates(cmp_, shop_, *, judgeable):
    u, ex = cmp_.get("units", {}), cmp_.get("unresolved", {})
    lo = lambda k: bool(cmp_.get(k)) and cmp_[k].get("lo") is not None and cmp_[k]["lo"] > 0
    return {"judgeable": bool(judgeable),
            "units_per_arm": all(u.get(a, 0) >= 30 for a in ("A", "B")),
            "clusters_both_arms": cmp_.get("clusters_both_arms", 0) >= 4,
            "unresolved_under_5pct": all(u.get(a, 0) and ex.get(a, 0) < .05 * u[a] for a in ("A", "B")),
            "pending0_lo_above_0": lo("net_pending0_b_minus_a"),
            "pending_cost_lo_above_0": lo("net_pending_cost_b_minus_a"),
            "talk_cost_lo_above_0": lo("net_talk_cost_b_minus_a"),
            "shop_no_harm": shop_.get("no_harm") is True}


def decide(g, cmp_):
    if not g["judgeable"]:
        return "wait"
    if all(g.values()):
        return "keep"
    hi = lambda k: bool(cmp_.get(k)) and cmp_[k].get("hi") is not None and cmp_[k]["hi"] < 0
    if (g["units_per_arm"] and g["clusters_both_arms"] and g["unresolved_under_5pct"]
            and hi("net_pending0_b_minus_a") and hi("net_pending_cost_b_minus_a")):
        return "revert"
    return "hold"


def ledger_iter(paths, spans):
    """Rows of the kinds read; appends (first ts, last ts) of each file to spans."""
    ts = re.compile(r'"ts":\s*([0-9.]+)')
    for p in paths:
        first = last = None
        with open(p, encoding="utf-8") as f:
            for line in f:
                m = ts.search(line, 0, 80)
                if m:
                    t = float(m.group(1))
                    first = t if first is None else min(first, t)
                    last = t if last is None else max(last, t)
                if not KIND.search(line, 0, 120):
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    yield None                          # counted as malformed by classify
        spans.append((first, last))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("memory")
    ap.add_argument("units_in")
    ap.add_argument("ledger", nargs="+")
    ap.add_argument("--study", required=True)
    ap.add_argument("--salt", required=True)
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--stop", type=float, required=True)
    ap.add_argument("--asof", type=float, default=None)
    ap.add_argument("--me", default="nocha12")
    ap.add_argument("--units", action="store_true", help="print every unit row too")
    a = ap.parse_args(argv)
    with open(a.memory, encoding="utf-8") as f:
        mem = json.load(f)
    mem = mem.get("negotiations", []) if isinstance(mem, dict) else mem
    units = []
    with open(a.units_in, encoding="utf-8") as f:
        for x in f:
            try:
                units.append(json.loads(x))
            except ValueError:
                units.append(None)                      # counted as malformed by classify
    spans = []
    rows = ledger_iter(a.ledger, spans)
    out = classify(mem, rows, units, study=a.study, salt=a.salt, start=a.start, stop=a.stop,
                   asof=a.asof if a.asof is not None else time.time(), me=a.me, file_spans=spans)
    if not a.units:
        out.pop("units")
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1, default=str)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
