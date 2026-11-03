"""Round 3 prices from the GPT Pro profit review (review/W-20261103-2120/gpt-pro-answer1.md §2-b, §2-c):

- floor F = ceil((C + H + max(1,000, 0.03 L)) / (1 - b)): the unit's cost C, H = 0.0025 C for six hours of storage, a
  minimum profit, and b the chance a goods unit breaks under the 6 h warranty (food 0; goods 0.15, or the ledger's
  rate when it is higher). F is the reserve: no normal sale goes under it.
- first ask P0 = max(F, min(L, 1.05 M)), M the median the ledger shows for this SKU (or the kind's share of list price
  when the SKU has too few deals); a price this shop was already paid for the SKU stays a candidate. For SKUs in
  price.scarce_asks (units sold out within minutes of each supply window) that fixed ask is a candidate as well.
  price.ask_caps then caps P0 per SKU and grade for slow stock, never below F.
- goods whose break chance is above warranty.max_break (0.25) are not sold at all: no sale without the warranty.
- each unit is also checked at b = 0.25 (stress) and the log says when the first ask would lose money there.
pricing.json "price" holds the numbers.
"""
import math

DEFAULTS = {"min_profit": 1000, "min_profit_share": 0.03, "storage_share": 0.0025, "goods_break": 0.15,
            "stress_break": 0.25, "median_up": 1.05, "kind_ratio": {"food": 0.708, "goods": 0.755},
            "scarce_asks": {}}


def cfg_of(pricing):
    return {**DEFAULTS, **((pricing or {}).get("price") or {})}


def break_for(kind, measured, cfg):
    """b for the floor: 0 for food; for goods the prior, or the ledger's rate when it is higher."""
    if kind != "goods":
        return 0.0
    return max(cfg["goods_break"], measured or 0.0)


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
    if median:
        target, why = cfg["median_up"] * median, f"1.05 x median {round(median)}"
    else:
        ratio = (cfg.get("kind_ratio") or {}).get(kind) or DEFAULTS["kind_ratio"].get(kind, 0.75)
        target, why = cfg["median_up"] * ratio * list_price, f"1.05 x {ratio:.1%} of list (no SKU median)"
    if paid:
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


def quote(u, median=None, measured=None, paid=(), pricing=None):
    """Everything a unit's price rests on: {floor, open, b, stress_floor, refuse, why}."""
    cfg = cfg_of(pricing)
    kind, cost, lp = u.get("kind"), u["cost"], u.get("list_price") or 0
    b = break_for(kind, measured, cfg)
    max_break = ((pricing or {}).get("warranty") or {}).get("max_break", cfg["stress_break"])
    f = floor(cost, lp, kind, b, cfg)
    scarce = (cfg.get("scarce_asks") or {}).get(u.get("sku")) if isinstance(cfg.get("scarce_asks"), dict) else None
    if isinstance(scarce, bool) or not isinstance(scarce, (int, float)) or scarce <= 0:
        scarce = None       # a bad setting never stops a sale; the list price still caps a good one
    p0, why = opening(f, lp, median, kind, paid, cfg, scarce)
    cap = ask_cap(cfg, u)
    if cap is not None and cap < p0:     # never under this unit's floor F
        p0, why = max(f, cap), f"ask cap {cap} for grade {u.get('grade')} (was {p0}, {why})"
    stress = floor(cost, lp, kind, cfg["stress_break"], cfg) if kind == "goods" else f
    return {"floor": f, "open": p0, "b": b, "stress_floor": stress, "why": why,
            "refuse": kind == "goods" and b > max_break, "stress_loss": kind == "goods" and p0 < stress}


def line(u, q):
    """One log line about a unit's price."""
    out = (f"{u['sku']} unit {u['unit']} cost {math.ceil(u['cost'])} list {u.get('list_price')}: floor {q['floor']}"
           f" (b {q['b']:.0%}), first ask {q['open']} ({q['why']})")
    if u.get("kind") == "goods":
        out += f", floor at b 25% {q['stress_floor']}" + (" - the first ask loses money there" if q["stress_loss"] else "")
    if q["refuse"]:
        out += ", not sold: break chance above the warranty limit"
    return out
