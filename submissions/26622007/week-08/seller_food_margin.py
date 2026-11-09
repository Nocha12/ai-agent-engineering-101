"""Food: a reserve with a margin over cost, not cost itself (W-20261109-2040-AX셀러전략).

Food never breaks, so its floor is cost x 1.0025 + 1 (profit_price.floor with b 0): a deal at the reserve keeps 0.25%.
11/09 10-22 h, 215 of our 463 grade A food deals closed at the reserve (B 222 of 850). On the same SKU x grade the
public ledger shows other shops selling higher (11/08 23:40-11/09 22:13, deal-weighted median price / list): A .739
(ours) vs .791, B .595 vs .658, C .481 vs .557, other shops higher on every one of the 4 SKUs per grade. Food is about a
third of our deals and no running study prices it (food_opening is disabled since 11/08; buyer_food_* are the buyer's).

Arm B raises the reserve to ceil(cost x (1 + share)) (0.05 by default, 0.02-0.10), never over list unless today's
reserve already is, never under today's; the ask rises to the new reserve when it is under it. Arm A keeps today's price.
Arms by unit, sha256("food-margin|salt|unit"), first byte odd is B: food is 4 SKUs per grade, too few for SKU arms, and
each unit ends sold, marked down or expired, so the verdict is per unit supplied in the window (net of real cost, with
markdown sales and expiry losses in it). Both arms are recorded (deal["food_margin"] -> shop_memory).

Eligible: food with a list price and a cost, no markdown (keep 1: near its date or the round's end the usual clearance
price holds, so a unit arm B did not sell is still cleared), not an ask-cap / scarce-ask / trial talk, not in the margin
study.

Switch: .market/seller_food_margin.json {"enabled": true, "study": "<id>", "salt": "<salt>", "share": 0.05,
"start_at": <epoch>, "stop_at": <epoch>}; on only in [start_at, stop_at), a window of at most MAX_WINDOW_HOURS.
Missing, unreadable, a bad value or time, or anything else is off, and off is today's price exactly.
SELLER_FOOD_MARGIN=0 in the environment is off too. Write it with switch_on(). Read at every quote: no restart either
way, and the treatment stops by itself at stop_at. Never raises. A new salt or share needs a new study id.
"""
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

import profit_price

SWITCH = "seller_food_margin.json"
SHARE, SHARE_LOW, SHARE_HIGH = 0.05, 0.02, 0.10
MAX_WINDOW_HOURS = 20


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _window(start, stop):
    return _num(start) and _num(stop) and 0 < start < stop <= start + MAX_WINDOW_HOURS * 3600


def _share_ok(v):
    return _num(v) and SHARE_LOW <= v <= SHARE_HIGH


def config(md=None, env=None, now=None):
    """{"study", "salt", "share", "start_at", "stop_at"} with the switch on at now, else None."""
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    if str(env.get("SELLER_FOOD_MARGIN") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    md = Path(md) if md is not None else Path(env.get("MARKET_DIR") or Path(__file__).parent / ".market")
    try:
        d = json.loads((md / SWITCH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("enabled") is not True:
        return None
    study, salt, share = d.get("study"), d.get("salt"), d.get("share", SHARE)
    if not (isinstance(study, str) and study and isinstance(salt, str) and salt) or not _share_ok(share):
        return None
    start, stop = d.get("start_at"), d.get("stop_at")
    if not _window(start, stop) or not start <= now < stop:
        return None
    return {"study": study, "salt": salt, "share": float(share), "start_at": start, "stop_at": stop}


def switch_on(md, *, study, salt, start_at, hours, share=SHARE):
    """Write the switch for [start_at, start_at + hours) atomically (0600); ValueError leaves the old file as it was."""
    stop_at = start_at + hours * 3600 if _num(hours) and _num(start_at) else None
    d = {"enabled": True, "study": study, "salt": salt, "share": share, "start_at": start_at, "stop_at": stop_at}
    if (not (isinstance(study, str) and study and isinstance(salt, str) and salt) or not _share_ok(share)
            or not _window(start_at, stop_at)):
        raise ValueError("seller_food_margin: bad switch")
    path = Path(md) / SWITCH
    tmp = path.with_name(f"{SWITCH}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(d, f, sort_keys=True, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return d


def arm(cfg, unit):
    """"A" or "B" for a unit under the study's salt; None without a study or a unit id."""
    if not cfg or not isinstance(unit, str) or not unit:
        return None
    h = hashlib.sha256(f"food-margin|{cfg['salt']}|{unit}".encode("utf-8", "surrogatepass")).digest()
    return "B" if h[0] % 2 else "A"


def decide(u, q, *, reserve, ask, keep, margin_assignment, pricing=None, md=None, env=None, now=None):
    """(reserve, ask, record|None). u: the unit; q: profit_price.quote()'s result; reserve and ask: the talk's so far.
    Today's reserve and ask unless arm B raises them."""
    try:
        cfg = config(md, env, now)
        if (not cfg or not isinstance(u, dict) or u.get("kind") != "food" or not _num(u.get("list_price"))
                or u["list_price"] <= 0 or not _num(u.get("cost")) or u["cost"] <= 0
                or not _num(reserve) or not _num(ask) or not _num(keep) or keep < 1 or margin_assignment is not None
                or str(q.get("why") or "").startswith(("ask cap", "scarce ask", "trial "))
                or profit_price.ask_cap(profit_price.cfg_of(pricing), u) is not None):
            return reserve, ask, None
        a = arm(cfg, u.get("unit"))
        if a is None:
            return reserve, ask, None
        lp = u["list_price"]
        new_reserve, new_ask = reserve, ask
        if a == "B":
            new_reserve = int(max(reserve, min(math.ceil(u["cost"] * (1 + cfg["share"])), lp)))
            new_ask = int(max(ask, new_reserve))
        return new_reserve, new_ask, {"study": cfg["study"], "arm": a, "share": cfg["share"], "usual_reserve": reserve,
                                      "usual_ask": ask, "reserve": new_reserve, "ask": new_ask, "list": lp,
                                      "cost": u["cost"], "sku": u.get("sku"), "grade": u.get("grade"),
                                      "unit": u.get("unit"), "stop_at": cfg["stop_at"]}
    except Exception as e:  # noqa: BLE001 - a broken setting never stops a sale
        print(f"SELLER_FOOD_MARGIN skipped: {type(e).__name__}", file=sys.stderr)
        return reserve, ask, None
