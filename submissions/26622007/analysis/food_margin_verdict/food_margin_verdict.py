#!/usr/bin/env python3
"""seller_food_margin A/B per food unit supplied in the window (W-20261109-2040-AX셀러전략, 11/09 23:50).

The treatment is assigned to a unit (sha256("food-margin|salt|unit"), first byte odd is B) and every food unit ends
sold, expired or still in stock, so the verdict is intention to treat per unit supplied, not per talk: a unit counts in
its arm whether or not any talk on it was treated (a markdown talk is never treated, keep < 1) and its outcome is read
from the public ledger only.

Units: our food units. Ours = a "supply" row of shop --me (default nocha12) or a units_in row (the shop's own record of
what it holds; it covers units supplied before the first ledger file read). Food = units_in kind "food" for the unit,
else the kind units_in gives the unit's SKU. supply_ts = the supply row's time (else units_in supplied_at).
Parts by supply_ts: before [.., start), main [start, cohort_end), late [cohort_end, stop), after [stop, ..);
cohort_end = min(stop, asof - COHORT_LAG) (4 h: 11/09 our food units sold 50/90/95/99% within .2/.9/1.5/3.3 h of supply).
The verdict reads main only.

Status per unit (ledger rows at or before asof only):
  sold       exactly one "settle" row of the unit and no "expire" row of ours naming it
  expired    an "expire" row of shop --me names it and no settle
  ambiguous  two or more settles, or a settle and an expire
  unknown    neither, and units_in's expires_at is more than EXPIRE_GRACE before asof (the expire row is missing;
             11/09 the one expire row of ours came 19.5 s after expires_at). Never read as a loss.
  pending    neither yet
cost = units_in's cost for the unit (None when its rows disagree; never the talk's, never the supply total / qty, which
differs by unit within one supply). A unit with no units_in row takes its cost from the supply row only where that is
exact for it or for its arm (derive_costs); otherwise None. net: sold = amount - cost - refunds of the order
(broken rows at or before min(settle + 6 h 1 min, asof); food did not break once in 127,234 market food sales 11/07-09,
so a sold unit is resolved at its settle; refund_open counts sold units still inside that 6 h 1 min); expired = -cost.
Excluded (out of numerator and denominator, counted per arm): ambiguous, unknown, and cost unknown whatever the status.

compare(main): B - A of mean net per unit supplied, two ways: pending units as 0 (cash by asof) and as -cost (the whole
unit lost: a B unit not sold yet may never sell). Units of one SKU x grade share buyers, so the 90% intervals come from
resampling SKU x grade clusters (2,000 draws, fixed seed, both arms of a drawn cluster together).
Interference between unit arms of one SKU has no fixed sign (seller_food_margin.py), so a keep also needs the shop:
our food net per hour (sales - cost - refunds of our food units settled in the span, minus the expire rows' own cost)
over [start, min(stop, asof)) not under the same clock hours a day before. Reported, not gated: per arm the sales made
while the SKU x grade had no unit of the other arm in stock (other_arm_in_stock False), sales whose talk was not treated
(markdown, ask cap, trial; joined settle -> result row of the order -> receipt task -> shop-memory talk), sales at or
under the usual floor (cost x 1.0025 + 1), and the recorded arm that disagrees with the salt's (should be 0).

Verdict: wait before start + MIN_HOURS (9 h, the registry's judgeable time) at asof; keep when every gate holds (main per
arm >= 30 units and >= 4 clusters, excluded < 5%, both p05 > 0, shop no_harm with unknown-cost sales < 5% of its sales);
revert when both p95 < 0; else hold.

  food_margin_verdict.py <seller_memory_r3.json> <units_in.jsonl> <ledger.jsonl>... --study ID --salt SALT
      --start EPOCH --stop EPOCH [--asof EPOCH] [--me SHOP] [--units]
Read only.
"""
import argparse
import collections
import hashlib
import json
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
KIND = re.compile(r'"kind":\s*"(supply|settle|result|broken|expire)"')
EXCLUDED = ("ambiguous", "unknown")


def arm(salt, unit):
    """seller_food_margin.arm with the salt given."""
    h = hashlib.sha256(f"food-margin|{salt}|{unit}".encode("utf-8", "surrogatepass")).digest()
    return "B" if h[0] % 2 else "A"


def talk_ts(n):
    """shop_memory writes time.strftime("%Y-%m-%dT%H:%M:%S") on the shop's clock, which is KST."""
    return datetime.strptime(n["time"], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=KST).timestamp()


def kst(t):
    return datetime.fromtimestamp(t, KST).strftime("%Y-%m-%dT%H:%M:%S")


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


def classify(memory, ledger_rows, units_rows, *, study, salt, start, stop, asof, ledger_end=None, me="nocha12",
             end_cell=None):
    """ledger_rows: an iterable of ledger dicts, consumed once. ledger_end: the ledger's last ts (None: end_cell[0] if
    given, else the largest ts read). asof is capped at ledger_end and cuts every input."""
    rows_by_kind = collections.defaultdict(list)
    seen_end = 0.0
    for r in ledger_rows:
        seen_end = max(seen_end, r.get("ts") or 0)
        if r.get("kind") in ("supply", "settle", "result", "broken", "expire"):
            rows_by_kind[r["kind"]].append(r)
    if ledger_end is None:
        ledger_end = max(seen_end, end_cell[0]) if end_cell else seen_end
    asof = min(asof, ledger_end)
    cut = {k: [r for r in rs if r["ts"] <= asof] for k, rs in rows_by_kind.items()}
    info, sku_kind = unit_info([u for u in units_rows if u.get("ts") is None or u["ts"] <= asof])

    supply_ts, supply_sku, supplies = {}, {}, []
    for r in cut.get("supply", ()):
        d = r["data"]
        if d.get("shop") == me:
            supplies.append(d)
            for u in d.get("units") or ():
                supply_ts.setdefault(u, r["ts"])
                supply_sku.setdefault(u, d.get("sku"))
    derived = derive_costs(supplies, info, salt)
    ours = set(supply_ts) | set(info)

    def is_food(u):
        k = (info.get(u) or {}).get("kind") or sku_kind.get((info.get(u) or {}).get("sku") or supply_sku.get(u))
        return k == "food"

    food = {u for u in ours if is_food(u)}
    settles, expires = collections.defaultdict(list), collections.defaultdict(list)
    for r in cut.get("settle", ()):
        if r["data"].get("unit") in food:
            settles[r["data"]["unit"]].append(r)
    for r in cut.get("expire", ()):
        if r["data"].get("shop") == me:
            for u in r["data"].get("units") or ():
                if u in food:
                    expires[u].append(r)
    broken = collections.defaultdict(list)
    for r in cut.get("broken", ()):
        if r["data"].get("order"):
            broken[r["data"]["order"]].append((r["ts"], r["data"].get("refunded") or 0))
    task_of = collections.defaultdict(set)
    for r in cut.get("result", ()):
        d = r["data"]
        if d.get("order") and (d.get("receipt") or {}).get("task"):
            task_of[d["order"]].add(d["receipt"]["task"])
    talks = collections.defaultdict(list)
    for n in memory:
        if n.get("task") and n.get("time") and talk_ts(n) <= asof:
            talks[n["task"]].append(n)

    cohort_end = min(stop, asof - COHORT_LAG)
    out_rows, mismatch = [], 0
    for u in sorted(food):
        i = info.get(u) or {}
        t = supply_ts.get(u, i.get("supplied_at"))
        if t is None:
            continue
        part = "before" if t < start else "main" if t < cohort_end else "late" if t < stop else "after"
        s, e = settles.get(u, []), expires.get(u, [])
        grade = i.get("grade") or (s[0]["data"].get("grade") if len(s) == 1 else None)
        r = {"unit": u, "arm": arm(salt, u), "sku": i.get("sku") or supply_sku.get(u), "grade": grade,
             "supply_ts": t, "part": part, "cost": i.get("cost"), "cost_source": "units_in" if u in info else None,
             "list": i.get("list_price"),
             "expires_at": i.get("expires_at"), "status": None, "order": None, "amount": None, "settle_ts": None,
             "end_ts": None, "refund": None, "refund_open": None, "net": None, "excluded_by": None,
             "sale_talk": None, "recorded_arm": None, "other_arm_in_stock": None}
        if u not in info and u in derived:
            r["cost"], r["cost_source"] = derived[u]
        if len(s) > 1 or (s and e):
            r["status"] = "ambiguous"
        elif s:
            d = s[0]["data"]
            r.update(status="sold", order=d.get("order"), amount=d.get("amount"), settle_ts=s[0]["ts"],
                     end_ts=s[0]["ts"])
            r["refund"] = sum(v for ts, v in broken.get(r["order"], ()) if ts <= min(r["settle_ts"] + MATURE, asof))
            r["refund_open"] = r["settle_ts"] + MATURE > asof
            ts_ = set()
            for task in task_of.get(r["order"], ()):
                ts_.update(task for n in talks.get(task, ()) if n.get("unit") == u)
            ns = [n for task in ts_ for n in talks[task]]
            recs = [n["food_margin"] for n in ns
                    if isinstance(n.get("food_margin"), dict) and n["food_margin"].get("study") == study]
            r["sale_talk"] = "treated" if recs else "untreated" if ns else "no_talk"
            if recs:
                r["recorded_arm"] = recs[-1].get("arm")
                mismatch += any(x.get("arm") != r["arm"] for x in recs)
        elif len(e) > 1:
            r["status"] = "ambiguous"
        elif e:
            r.update(status="expired", end_ts=e[0]["ts"])
        elif r["expires_at"] is not None and r["expires_at"] + EXPIRE_GRACE < asof:
            r["status"] = "unknown"
        else:
            r["status"] = "pending"
        if r["status"] in EXCLUDED:
            r["excluded_by"] = r["status"]
        elif r["cost"] is None:
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
    shop_ = shop(out_rows, cut.get("expire", ()), me, start, min(stop, asof))
    judgeable = asof >= start + MIN_HOURS * 3600
    g = gates(cmp_, shop_, judgeable=judgeable)
    warnings = []
    if food and not settles:
        warnings.append("no settle row of our food units read: wrong files or a changed format")
    if not any(supply_ts.get(u) for u in food):
        warnings.append("no supply row of shop --me read")
    return {"study": study, "salt": salt, "start": start, "stop": stop, "asof": asof, "ledger_end": ledger_end,
            "cohort_end": cohort_end, "judgeable_at": start + MIN_HOURS * 3600, "arm_mismatch": mismatch,
            "arms": arms, "compare_main": cmp_, "shop": shop_, "gates": g, "verdict": decide(g, cmp_),
            "warnings": warnings, "units": out_rows}


def derive_costs(supplies, info, salt):
    """{unit: (cost, source)} for our units with no units_in row (11/09: 148 of 2,058 units supplied after 23:33 the
    day before, every one sold seconds after supply, before the shop's next units_in tick). The supply row's total is
    the sum of its units' real costs (units_in sums to the supply totals exactly), so total - the known costs is the
    missing units' cost together: exact for one missing unit ("supply_residual"); for several missing units of one arm
    each gets the residual / their number ("supply_group": the arm's sum is exact, the split between them is not, and
    they are one SKU). Missing units of both arms, a known unit with disagreeing rows, or a residual <= 0: unknown."""
    out = {}
    for d in supplies:
        us = d.get("units") or []
        if not isinstance(d.get("total"), (int, float)) or not us:
            continue
        miss = [u for u in us if u not in info]
        if not miss or any(info[u].get("cost") is None for u in us if u in info):
            continue
        rest = d["total"] - sum(info[u]["cost"] for u in us if u in info)
        if rest <= 0:
            continue
        if len(miss) == 1:
            out[miss[0]] = (rest, "supply_residual")
        elif len({arm(salt, u) for u in miss}) == 1:
            for u in miss:
                out[u] = (rest / len(miss), "supply_group")
    return out


def exposure(rows):
    """other_arm_in_stock for each sold unit of known SKU x grade: was a unit of the other arm of the same SKU x grade
    (known grade) supplied before its settle and not yet sold or expired then."""
    by = collections.defaultdict(list)
    for r in rows:
        if r["grade"] is not None and r["sku"] is not None:
            by[(r["sku"], r["grade"])].append(r)
    for rs in by.values():
        for r in rs:
            if r["status"] != "sold" or r["settle_ts"] is None:
                continue
            t = r["settle_ts"]
            r["other_arm_in_stock"] = any(
                o["arm"] != r["arm"] and o["supply_ts"] <= t and (o["end_ts"] is None or o["end_ts"] > t)
                and o["status"] in ("sold", "expired", "pending") for o in rs)


def arm_table(rows):
    c = collections.Counter(units=len(rows))
    for r in rows:
        c[r["status"]] += 1
        if r["excluded_by"]:
            c["excluded"] += 1
            c["excluded_" + r["excluded_by"]] += 1
        if r["status"] == "sold":
            c["sales"] += r["amount"] or 0
            c["refund"] += r["refund"] or 0
            c["refund_open"] += bool(r["refund_open"])
            c["sale_talk_" + r["sale_talk"]] += 1
            c["sold_without_other_arm_stock"] += r["other_arm_in_stock"] is False
            if r["cost"] is not None and r["amount"] <= r["cost"] * 1.0025 + 1:
                c["sold_at_or_under_floor"] += 1
        if r["net"] is not None:
            c["net"] += r["net"]
        c["cost_source_" + str(r["cost_source"])] += 1
    out = dict(c)
    out["clusters"] = len({(r["sku"], r["grade"]) for r in rows})
    n = len(rows) - c["excluded"]
    out["net_per_unit"] = round(c["net"] / n, 2) if n else None
    out["sold_share"] = round(c["sold"] / n, 4) if n else None
    return out


def _unit_values(rows, pending_cost):
    """(cluster, arm, value) for every main unit that is not excluded."""
    out = []
    for r in rows:
        if r["excluded_by"]:
            continue
        if r["status"] == "pending":
            val = -r["cost"] if pending_cost else 0.0
        else:
            val = r["net"]
        out.append(((r["sku"], r["grade"]), r["arm"], val))
    return out


def _diff(vals):
    s, n = collections.Counter(), collections.Counter()
    for _, a, x in vals:
        s[a] += x
        n[a] += 1
    if not n["A"] or not n["B"]:
        return None
    return s["B"] / n["B"] - s["A"] / n["A"]


def _interval(vals, draws=2000, seed=20261109):
    point = _diff(vals)
    if point is None:
        return None
    cl = collections.defaultdict(list)
    for v in vals:
        cl[v[0]].append(v)
    keys = sorted(cl, key=str)
    rnd, ds = random.Random(seed), []
    for _ in range(draws):
        d = _diff([v for k in (rnd.choice(keys) for _ in keys) for v in cl[k]])
        if d is not None:
            ds.append(d)
    ds.sort()
    q = lambda p: round(ds[min(len(ds) - 1, int(p * len(ds)))], 2)
    return {"point": round(point, 2), "p05": q(.05), "p50": q(.5), "p95": q(.95), "draws": len(ds)}


def compare(rows):
    rows = [r for r in rows if r["arm"] in ("A", "B")]
    out = {"units": dict(collections.Counter(r["arm"] for r in rows)),
           "clusters": {a: len({(r["sku"], r["grade"]) for r in rows if r["arm"] == a}) for a in ("A", "B")},
           "excluded": {a: sum(1 for r in rows if r["arm"] == a and r["excluded_by"]) for a in ("A", "B")},
           "pending": {a: sum(1 for r in rows if r["arm"] == a and r["status"] == "pending") for a in ("A", "B")}}
    for name, pc in (("net_pending0", False), ("net_pending_cost", True)):
        iv = _interval(_unit_values(rows, pc))
        if iv:
            out[name + "_b_minus_a"] = iv
    return out


def shop(rows, expire_rows, me, lo, hi):
    """Our food net per hour in [lo, hi) and in the same clock hours a day before, by settle and expire time."""
    def span(a, b):
        sold = [r for r in rows if r["status"] in ("sold", "ambiguous") and r["settle_ts"] is not None
                and a <= r["settle_ts"] < b]
        known = [r for r in sold if r["net"] is not None]
        exp = sum((r["data"].get("cost") or 0) for r in expire_rows
                  if r["data"].get("shop") == me and a <= r["ts"] < b
                  and any(u in food_units for u in r["data"].get("units") or ()))
        net = sum(r["net"] for r in known) - exp
        h = (b - a) / 3600
        return {"from": a, "to": b, "sales": len(sold), "unknown_cost_sales": len(sold) - len(known),
                "expired_cost": exp, "net": net, "net_per_hour": round(net / h, 2) if h > 0 else None}
    food_units = {r["unit"] for r in rows}
    w, d = span(lo, hi), span(lo - DAY, hi - DAY)
    p = span(lo - (hi - lo), lo)
    unknown = (w["unknown_cost_sales"] + d["unknown_cost_sales"]) / max(1, w["sales"] + d["sales"])
    ok = w["net_per_hour"] is not None and d["net_per_hour"] is not None and w["net_per_hour"] >= d["net_per_hour"]
    return {"window": w, "day_before": d, "span_before": p, "unknown_share": round(unknown, 4), "no_harm": ok}


def gates(cmp_, shop_, *, judgeable):
    u, cl, ex = cmp_.get("units", {}), cmp_.get("clusters", {}), cmp_.get("excluded", {})
    p0, pc = cmp_.get("net_pending0_b_minus_a"), cmp_.get("net_pending_cost_b_minus_a")
    return {"judgeable": bool(judgeable),
            "units_per_arm": all(u.get(a, 0) >= 30 for a in ("A", "B")),
            "clusters_per_arm": all(cl.get(a, 0) >= 4 for a in ("A", "B")),
            "excluded_under_5pct": all(u.get(a, 0) and ex.get(a, 0) < .05 * u[a] for a in ("A", "B")),
            "pending0_p05_above_0": bool(p0) and p0["p05"] > 0,
            "pending_cost_p05_above_0": bool(pc) and pc["p05"] > 0,
            "shop_no_harm": bool(shop_.get("no_harm")) and shop_.get("unknown_share", 1) < .05}


def decide(g, cmp_):
    if not g["judgeable"]:
        return "wait"
    if all(g.values()):
        return "keep"
    p0, pc = cmp_.get("net_pending0_b_minus_a"), cmp_.get("net_pending_cost_b_minus_a")
    if g["units_per_arm"] and p0 and pc and p0["p95"] < 0 and pc["p95"] < 0:
        return "revert"
    return "hold"


def ledger_iter(paths, cell):
    """Rows of the kinds read; cell[0] becomes the last ts of any row (the ledger's end)."""
    ts = re.compile(r'"ts":\s*([0-9.]+)')
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for line in f:
                m = ts.search(line, 0, 80)
                if m:
                    cell[0] = max(cell[0], float(m.group(1)))
                if not KIND.search(line, 0, 120):
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


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
    with open(a.units_in, encoding="utf-8") as f:
        units = [json.loads(x) for x in f if x.strip()]
    cell = [0.0]
    out = classify(mem, ledger_iter(a.ledger, cell), units, study=a.study, salt=a.salt, start=a.start, stop=a.stop,
                   asof=a.asof if a.asof is not None else time.time(), me=a.me, end_cell=cell)
    if not a.units:
        out.pop("units")
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1, default=str)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
