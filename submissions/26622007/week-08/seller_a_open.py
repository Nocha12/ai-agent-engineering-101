"""Grade A dear goods: open at a share of list price when the usual first ask is under it
(review/W-20261109-1033-AX취약점루프/weakness-01-seller.md §S3).

Grade A carries the shop's revenue and almost none of its profit. 11/09 11:50 cut, 72 h, units sold 6 h before (the
warranty window closed), real unit cost from units_in.jsonl: grade A 3,043 units, 2,303M revenue, 2,149M cost, 47M
refunds, 106.8M net (4.6%); B 8.9%, C 26%. An A unit of 30만+ furniture sells at .69 of list on a .65 cost: the first
ask is 0.97 x the SKU median (price.high_median_up), the median is our own past sales, and buyers take it at .997 of the
ask. yms979 sold the same A furniture at .722-.728 of list, seochanit at .768-.791 (48 h), so the ask, not the buyer,
holds the price down.

Arm B opens at max(usual first ask, ratio x list), never over list, ratio 0.74 by default (0.70-0.80). The floor F,
the concessions and every later turn stay as they are, so a buyer who would not pay it bargains down as today. Arm A
keeps the usual ask. Arms by SKU x grade, sha256(salt|sku|grade), first byte odd is B: our own deals feed the median, so
a per-talk split would lift arm A's ask too.

Eligible: goods, grade A, list price at or above min_list (300,000), no markdown (keep 1), no ask cap, no scarce ask,
no high-price trial cut (quote()'s why), and not in the margin study (sol_seller_margin assignment), so the two never
price the same talk. Everything else is untouched and unrecorded.

Switch: .market/seller_a_open.json {"enabled": true, "study": "<id>", "salt": "<salt>", "ratio": 0.74}; missing,
unreadable or anything else is off, and off is today's ask exactly. SELLER_A_OPEN=0 in the environment is off too.
Read at every quote, so no restart either way. Never raises.
"""
import hashlib
import json
import math
import os
from pathlib import Path

SWITCH = "seller_a_open.json"
RATIO, RATIO_LOW, RATIO_HIGH = 0.74, 0.70, 0.80
MIN_LIST = 300000


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def config(md=None, env=None):
    """{"study", "salt", "ratio"} with the switch on, else None."""
    env = os.environ if env is None else env
    if str(env.get("SELLER_A_OPEN") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    md = Path(md) if md is not None else Path(env.get("MARKET_DIR") or Path(__file__).parent / ".market")
    try:
        d = json.loads((md / SWITCH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("enabled") is not True:
        return None
    study, salt, ratio = d.get("study"), d.get("salt"), d.get("ratio", RATIO)
    if not (isinstance(study, str) and study and isinstance(salt, str) and salt):
        return None
    if not _num(ratio) or not RATIO_LOW <= ratio <= RATIO_HIGH:
        return None
    return {"study": study, "salt": salt, "ratio": float(ratio)}


def arm(cfg, sku, grade):
    """"A" or "B" for a SKU x grade under the study's salt; None without a study."""
    if not cfg or not isinstance(sku, str) or not sku:
        return None
    h = hashlib.sha256(f"{cfg['salt']}|{sku}|{grade}".encode("utf-8", "surrogatepass")).digest()
    return "B" if h[0] % 2 else "A"


def decide(u, q, *, keep, margin_assignment, md=None, env=None):
    """(first ask, record|None). u: the unit; q: profit_price.quote()'s result. Today's q["open"] unless arm B moves it."""
    try:
        p0 = q["open"]
        cfg = config(md, env)
        lp = u.get("list_price")
        why = str(q.get("why") or "")
        if (not cfg or u.get("kind") != "goods" or u.get("grade") != "A" or not _num(lp) or lp < MIN_LIST
                or not _num(p0) or not _num(q.get("floor")) or keep < 1 or margin_assignment is not None
                or why.startswith(("ask cap", "scarce ask", "trial "))):
            return p0, None
        a = arm(cfg, u.get("sku"), u.get("grade"))
        if a is None:
            return p0, None
        target = max(p0, q["floor"], min(round(lp), math.ceil(cfg["ratio"] * lp)))     # never under today's ask or F
        new = target if a == "B" else p0
        return new, {"study": cfg["study"], "arm": a, "ratio": cfg["ratio"], "usual": p0, "open": new,
                     "list": lp, "floor": q["floor"]}
    except Exception:       # noqa: BLE001 - a broken setting never stops a sale
        return q.get("open") if isinstance(q, dict) else None, None
