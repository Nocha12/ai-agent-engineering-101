"""Round 3 prices from the GPT Pro profit review (review/W-20261103-2120/gpt-pro-answer1.md §2-b, §2-c):

- floor F = ceil((C + H + max(1,000, 0.03 L)) / (1 - b)): the unit's cost C, H = 0.0025 C for six hours of storage, a
  minimum profit, and b the chance a goods unit breaks under the 6 h warranty (food 0; goods 0.15, or the ledger's
  rate when it is higher). F is the reserve: no normal sale goes under it.
- first ask P0 = max(F, min(L, 1.05 M)), M the median the ledger shows for this SKU (or the kind's share of list price
  when the SKU has too few deals); a price this shop was already paid for the SKU stays a candidate. For SKUs in
  price.scarce_asks (units sold out within minutes of each supply window) that fixed ask is a candidate as well.
  price.ask_caps then caps P0 per SKU and grade for slow stock, never below F.
- goods whose break chance is above warranty.max_break (pricing.json, 0.5 from 11/06) are not sold at all: no sale without
  the warranty.
- each unit is also checked at b = max(0.25, its own b) (stress) and the log says when the first ask would lose money there.
pricing.json "price" holds the numbers.
"""
import hashlib
import math
import time

DEFAULTS = {"min_profit": 1000, "min_profit_share": 0.03, "storage_share": 0.0025, "goods_break": 0.15,
            "stress_break": 0.25, "median_up": 1.05, "kind_ratio": {"food": 0.708, "goods": 0.755},
            "scarce_asks": {}}


def cfg_of(pricing):
    return {**DEFAULTS, **((pricing or {}).get("price") or {})}


def break_for(kind, measured, cfg, grade=None):
    """b for the floor: 0 for food; for goods the prior, or the ledger's rate when it is higher. price.grade_break (the
    market's rate per grade) is a floor under it too: without break stats, or with a grade too thin to measure, a C unit
    (34% break) would otherwise be priced at the 8% prior. A unit with no rate and no known grade keeps 0.15."""
    if kind != "goods":
        return 0.0
    by_grade = cfg.get("grade_break") if isinstance(cfg.get("grade_break"), dict) else {}
    g = by_grade.get(grade)
    g = g if isinstance(g, (int, float)) and not isinstance(g, bool) and 0 <= g < 1 else None
    if g is None:
        g = DEFAULTS["goods_break"] if measured is None else 0.0
    return max(cfg["goods_break"], measured or 0.0, g)


def break_trial_arm(u, cfg, now=None):
    """price.grade_break_trial's b for this goods unit, or None (control, or the trial is off). AX 11/09 15:20
    (review/W-20261109-1520-AXPro주기/report.md): 11/08 06:00-11/09 09:00 our grade B furniture broke 72 of 377 (19.1%)
    against 45.1 expected at the market's SKU x grade rates (11.8% for the grade), so the floor priced them at b 12.2% and
    B furniture lost 9.0M over 02:35-15:15 (sales 95.1M, refunds 24.5M). The trial half of the units (a stable hash of
    salt|unit under share) takes b at least the trial's grade rate; the other half keeps the usual b. Fail-closed: off
    outside [start_at, stop_at) or when a field is missing or odd, so an expired trial stops by itself."""
    t = cfg.get("grade_break_trial")
    if not isinstance(t, dict) or u.get("kind") != "goods" or not isinstance(t.get("salt"), str) or not t["salt"]:
        return None
    share, start, stop = t.get("share"), t.get("start_at"), t.get("stop_at")
    if not (_num(share) and 0 < share <= 1 and _num(start) and _num(stop) and start < stop):
        return None
    now = time.time() if now is None else now
    if not start <= now < stop:
        return None
    rates = t.get("grade_break")
    g = rates.get(u.get("grade")) if isinstance(rates, dict) else None
    if not _num(g) or not 0 <= g < 1 or not isinstance(u.get("unit"), str) or not u["unit"]:
        return None
    h = hashlib.sha1(f"{t['salt']}|{u['unit']}".encode()).digest()
    return g if int.from_bytes(h[:4], "big") / 2 ** 32 < share else None


def unit_break(u, measured, cfg, now=None):
    """(b, trial b or None): break_for, raised to price.grade_break_trial's rate for a trial-half unit."""
    b = break_for(u.get("kind"), measured, cfg, u.get("grade"))
    tb = break_trial_arm(u, cfg, now)
    return (max(b, tb) if tb is not None else b), tb


def floor(cost, list_price, kind, b, cfg=None):
    cfg = cfg or DEFAULTS
    c = math.ceil(cost)
    m = max(cfg["min_profit"], cfg["min_profit_share"] * (list_price or 0))
    return math.ceil((c + cfg["storage_share"] * c + m) / (1 - (b if kind == "goods" else 0.0)))


def opening(f, list_price, median=None, kind=None, paid=(), cfg=None, scarce=None):
    """(first ask, why). median: the SKU's median deal price, or None to use the kind's share of list price.
    scarce: a fixed ask set in pricing.json for a SKU that sells out within minutes of each window (quote()); it is a
    number, not a factor, because our own sales feed the median and a factor would climb from sale to sale."""
    cfg = cfg or DEFAULTS
    up = cfg["median_up"]
    high = cfg.get("high_median_up")        # {"min_list", "factor"}: dear goods open at the market's price, not over it
    if isinstance(high, dict) and kind == "goods" and isinstance(high.get("factor"), (int, float)) \
            and not isinstance(high.get("factor"), bool) and 0.5 <= high["factor"] <= cfg["median_up"] \
            and isinstance(high.get("min_list"), (int, float)) and (list_price or 0) >= high["min_list"]:
        up = high["factor"]
    if median:
        target, why = up * median, f"{up:g} x median {round(median)}"
    else:
        ratio = (cfg.get("kind_ratio") or {}).get(kind) or DEFAULTS["kind_ratio"].get(kind, 0.75)
        target, why = up * ratio * list_price, f"{up:g} x {ratio:.1%} of list (no SKU median)"
    if paid and (median is None or cfg.get("paid_over_median", True)):
        # price.paid_over_median false: with a ledger median our own best price is no longer a floor for the first ask.
        # It held 353 of 433 first asks on 30만+ goods at 0.934 of list (11/06 11:02-17:18) while the top five sold the
        # same SKUs at 0.70-0.79; G8ec51935 took 2 deals from 174 quotes (GPT Pro 11/06 16:45 §3.5-3)
        best = max(paid)
        if best > target:
            target, why = best, f"our own paid price {best}"
    if scarce and scarce > target:
        target, why = scarce, f"scarce ask {scarce}"
    p = min(list_price or target, target)
    return max(f, round(p)), why


def ask_cap(cfg, u):
    """price.ask_caps {sku: {grade or "*": price}}: the highest first ask for a slow unit, so the median, our own best paid
    price or a scarce ask cannot keep it above what buyers offer (0600: G6b9db161 at its scarce ask 392,986 sold 0 of 2
    in 38 min; the slow goods were not bought at 1.05 x median). None when the SKU/grade has no valid cap."""
    caps = cfg.get("ask_caps")
    row = caps.get(u.get("sku")) if isinstance(caps, dict) else None
    if not isinstance(row, dict):
        return None
    cap = row.get(u.get("grade"), row.get("*"))
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not math.isfinite(cap) or cap <= 0:
        return None         # a bad setting never stops a sale
    return round(cap)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def trial_arm(sku, grade, trial):
    """Whether this SKU x grade is in price.high_price_trial's lower-price half (GPT Pro 11/06 23:40, 메인 23:40: cuts
    only on 30만+ goods, tried per SKU x grade). A stable hash of salt|sku|grade under share; False on a bad setting."""
    if not isinstance(trial, dict) or not _num(trial.get("share")) or not 0 < trial["share"] <= 1:
        return False
    h = hashlib.sha1(f"{trial.get('salt', '')}|{sku}|{grade}".encode()).digest()
    return int.from_bytes(h[:4], "big") / 2 ** 32 < trial["share"]


def quote(u, median=None, measured=None, paid=(), pricing=None):
    """Everything a unit's price rests on: {floor, open, b, stress_floor, refuse, why}."""
    cfg = cfg_of(pricing)
    kind, cost, lp = u.get("kind"), u["cost"], u.get("list_price") or 0
    b, trial_b = unit_break(u, measured, cfg)
    max_break = ((pricing or {}).get("warranty") or {}).get("max_break", cfg["stress_break"])
    f = floor(cost, lp, kind, b, cfg)
    scarce = (cfg.get("scarce_asks") or {}).get(u.get("sku")) if isinstance(cfg.get("scarce_asks"), dict) else None
    if isinstance(scarce, bool) or not isinstance(scarce, (int, float)) or scarce <= 0:
        scarce = None       # a bad setting never stops a sale; the list price still caps a good one
    p0, why = opening(f, lp, median, kind, paid, cfg, scarce)
    trial, high = cfg.get("high_price_trial"), cfg.get("high_median_up")
    if kind == "goods" and median and scarce is None and isinstance(high, dict) and _num(high.get("min_list")) \
            and lp >= high["min_list"] and isinstance(trial, dict) and _num(trial.get("factor")) \
            and 0.8 <= trial["factor"] <= cfg["median_up"] and trial_arm(u.get("sku"), u.get("grade"), trial):
        t = max(f, math.ceil(trial["factor"] * median))
        if t < p0:      # the trial half opens lower, never under the floor F; the other half keeps the usual ask
            p0, why = t, f"trial {trial['factor']:g} x median {round(median)} (was {p0}, {why})"
    cap = ask_cap(cfg, u)
    if cap is not None and cap < p0:     # never under this unit's floor F
        p0, why = max(f, cap), f"ask cap {cap} for grade {u.get('grade')} (was {p0}, {why})"
    stress = floor(cost, lp, kind, max(cfg["stress_break"], b), cfg) if kind == "goods" else f
    return {"floor": f, "open": p0, "b": b, "stress_floor": stress, "stress_b": max(cfg["stress_break"], b), "why": why,
            "refuse": kind == "goods" and b > max_break, "stress_loss": kind == "goods" and p0 < stress,
            "b_trial": trial_b}


def line(u, q):
    """One log line about a unit's price."""
    out = (f"{u['sku']} unit {u['unit']} cost {math.ceil(u['cost'])} list {u.get('list_price')}: floor {q['floor']}"
           f" (b {q['b']:.0%}), first ask {q['open']} ({q['why']})")
    if u.get("kind") == "goods":
        out += f", floor at b {q.get('stress_b', 0.25):.0%} {q['stress_floor']}" + (" - the first ask loses money there" if q["stress_loss"] else "")
    if q.get("b_trial") is not None:
        out += f", grade_break_trial b {q['b_trial']:.0%}"
    if q["refuse"]:
        out += ", not sold: break chance above the warranty limit"
    return out
