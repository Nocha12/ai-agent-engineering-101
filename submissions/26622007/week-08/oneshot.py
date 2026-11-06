"""The talk priced by code: one offer under the shop's first ask, then its counter taken, with no harness call.

Of the 447 talks logged 11/06 21:3x - 11/07 01:13, a shop answered my offer in a median 1.0 s (mean 3.8 s), while the
harness took 3.9 s a turn over 3.8 turns an order: the harness, not the shop, set the 22-28 s a talk took of the ~49 s an
order took. Replayed on those talks (review/W-20261107-0115-AX바이어한방제안), 0.9 of the first ask and then the counter
kept 0.364 of list price a deal against the harness's 0.379, at 5.5 s a talk instead of 29.5 s: 80 deals an hour
instead of 47. With 263 h left that is the deals percentile 0.56 -> 0.91 for at most ~0.02 of the saved percentile.

Settings (pricing.json "buyer", read at every order): oneshot (true/false), oneshot_steps (shares of the first ask, rising,
up to 5). BUYER_ONESHOT=1 / 0 in the environment wins over pricing.json.
"""
import os

STEPS = (0.9,)
MAX_STEPS = 5


def enabled(cfg, env=None):
    env = os.environ if env is None else env
    v = env.get("BUYER_ONESHOT")
    if v in ("0", "1"):
        return v == "1"
    return (cfg or {}).get("oneshot") is True


def steps(cfg):
    s = (cfg or {}).get("oneshot_steps")
    if not isinstance(s, list) or not 1 <= len(s) <= MAX_STEPS:
        return STEPS
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and 0.5 <= x <= 1.0 for x in s):
        return STEPS
    if any(b < a for a, b in zip(s, s[1:])):
        return STEPS
    return tuple(float(x) for x in s)


def code_turn(ask, history, limit, steps=STEPS):
    """The harness's answer ({"shop_price", "action", "price"}) for this turn, or None when no ask was read (the harness
    reads the text then). history holds the turns before this ask. The offer never goes over limit or the ask."""
    if not isinstance(ask, int) or isinstance(ask, bool) or ask <= 0:
        return None
    offers = [p for who, p in history if who == "you offered"]
    asks = [p for who, p in history if who == "shop asked"]
    first, last, k = (asks[0] if asks else ask), (offers[-1] if offers else 0), len(offers)
    walk = {"shop_price": ask, "action": "walk_away", "price": 0}
    offer = lambda p: {"shop_price": ask, "action": "offer", "price": int(p)}
    if history[-2:] == [("shop asked", ask), ("you offered", ask)] or k >= len(steps) + 2:
        return walk               # the shop did not take its own ask, or the steps and both last offers are spent
    if ask <= last:
        return offer(ask)
    if k < len(steps):
        p = min(int(steps[k] * first), ask, limit)
        if p > last:
            return offer(p)
    if ask <= limit:
        return offer(ask)         # the counter is within what I pay: taken
    return offer(limit) if last < limit else walk     # one raise to my limit, then the next order
