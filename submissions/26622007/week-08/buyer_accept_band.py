"""Plan A, the near-miss accept band (owner 11/08, telegram 7918 "바로 해보자 이거 중요해";
review/W-20261108-1645-AX성사율원인/report.md section 5A).

Of 1,925 orders that ended with no deal 11/07 16:43 - 11/08 16:43, about 89% ended on price: the shop's last ask was
1-10% over my limit (list x (1 - 0.30 / (1 - b)), about 0.68-0.70 of list) while the buyers ahead of us pay 0.70-0.76 of
list for goods. Goods converted 21%, food 81%.

The band: once my oneshot steps are spent, a shop counter over my limit but at or under 105% of it (and never over the
order's budget) is taken, as a counter at or under the limit always was. My own offers stay as they were (the steps, then
one raise to the limit); only what I take moves. The receipt check (market_buyer.over_limit) pays up to the same ceiling.
Everything else holds: hard_limits, the goods 6 h warranty (warranty_short, the short-warranty shop skip), the budget.

Switch: .market/buyer_accept_band.json {"enabled": true}; missing, unreadable or anything else is off, and off is the
old behaviour exactly. BUYER_ACCEPT_BAND=0 in the environment is off too. Read at every order (a buyer run is one
order), so no restart either way.
"""
import json
import os

SWITCH = "buyer_accept_band.json"
PERCENT = 105


def percent(md, env=None):
    """PERCENT when the switch is on, else None."""
    env = os.environ if env is None else env
    if str(env.get("BUYER_ACCEPT_BAND") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        with open(os.path.join(md, SWITCH), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return PERCENT if isinstance(d, dict) and d.get("enabled") is True else None


def ceiling(limit, budget, pct):
    """The most I take: pct% of the limit, rounded down, never over the order's budget nor under the limit."""
    if pct is None or not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        return limit
    top = limit * pct // 100
    if isinstance(budget, (int, float)) and not isinstance(budget, bool):
        top = min(top, int(budget))
    return max(limit, top)


def turn(baseline, history, limit, top, steps):
    """(the turn, a note or None). Takes the shop's ask when it is over my limit and at or under top, my steps are
    spent, and the turn would otherwise walk away or offer under it. A shop that already turned down its own ask
    (my last offer was that ask) is left to walk, as before; a harness turn (None) is left alone."""
    if not isinstance(baseline, dict) or not isinstance(top, int) or not isinstance(limit, int) or top <= limit:
        return baseline, None
    ask = baseline.get("shop_price")
    if type(ask) is not int or not limit < ask <= top:
        return baseline, None
    offers = [p for who, p in history if who == "you offered"]
    if len(offers) < len(steps or ()) or not offers or offers[-1] >= ask:
        return baseline, None
    if baseline.get("action") == "offer" and isinstance(baseline.get("price"), int) and baseline["price"] >= ask:
        return baseline, None
    if baseline.get("action") not in ("offer", "walk_away"):
        return baseline, None
    return {**baseline, "action": "offer", "price": ask}, {"taken": ask, "limit": limit, "ceiling": top}
