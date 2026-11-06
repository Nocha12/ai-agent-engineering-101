"""The talk priced by code: one offer under the shop's first ask, then its counter taken, with no harness call.

Of the 447 talks logged 11/06 21:3x - 11/07 01:13, a shop answered my offer in a median 1.0 s (mean 3.8 s), while the
harness took 3.9 s a turn over 3.8 turns an order: the harness, not the shop, set the 22-28 s a talk took of the ~49 s an
order took. Replayed on those talks (review/W-20261107-0115-AX바이어한방제안), 0.9 of the first ask and then the counter
kept 0.364 of list price a deal against the harness's 0.379, at 5.5 s a talk instead of 29.5 s: 80 deals an hour
instead of 47. With 263 h left that is the deals percentile 0.56 -> 0.91 for at most ~0.02 of the saved percentile.

Settings (pricing.json "buyer", read at every order): oneshot (true/false), oneshot_steps (shares of the first ask, rising,
up to 5). BUYER_ONESHOT=1 / 0 (true / false, on / off) in the environment wins over pricing.json; the meta-harness may
not move buyer.oneshot (hard_limits.FIXED).

An ask in the message metadata is priced by code. Only 80 of 1,419 asks logged came that way; an ask written in text is
priced by code only on a later turn and only when the whole text is one of the shop templates in TEMPLATE ("Counteroffer:
13049.", "11300 is too low. Counteroffer price: 13049."); any other text goes to the harness. Four independent reviews
(11/07) broke every reading of free text by word lists ("Was asking 20000, now just 12000", "asking 19000 minus 2000",
"Others ask 19000, I accept your offer"): free text cannot be read safely without the harness, a fixed template can.
The first turn's text (the quote) goes to the harness too: one call instead of 3.8.
"""
import os
import re

STEPS = (0.9,)
MAX_STEPS = 5
# a later turn's whole text, as shops in the log write it (logs/market-kit.log 11/06 21:3x - 11/07 01:13); nothing else
TEMPLATE = re.compile(
    r"\s*(?:(?P<mine>[0-9]{1,12}) is too low\.\s*Counteroffer price:|(?:[0-9]{1,2} offers? left\.\s*)?Counteroffer:"
    r"|Our lowest price for this unit:)\s*(?P<p>[0-9]{1,12})\.?"
    r"(?:\s*SKU G[0-9a-f]{8}, grade [ABC]; market certificate at checkout\."
    r"(?:\s*Includes a [0-9]{1,3}-hour warranty: market-enforced full refund if the goods break during warranty; "
    r"the quote includes the warranty surcharge\.)?)?"
    r"(?:\s*Reply ok to take it\.)?\s*")


def enabled(cfg, env=None):
    env = os.environ if env is None else env
    v = str(env.get("BUYER_ONESHOT") or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
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


def text_price(text, history, quoted=None):
    """The shop's ask from its text on a later turn, or None for the harness: the text must be one TEMPLATE whole, the
    number it names my own last offer where it names one, and the ask not over the shop's last ask nor under my last
    offer. quoted is kept for the callers; the first turn's text always goes to the harness."""
    mine = [p for who, p in history if who == "you offered"]
    asks = [p for who, p in history if who == "shop asked"]
    if not asks or not mine:
        return None
    m = TEMPLATE.fullmatch(text or "")
    if not m or (m.group("mine") and int(m.group("mine")) != mine[-1]):
        return None
    p = int(m.group("p"))
    if p <= 0 or p > asks[-1] or p < mine[-1]:
        return None
    return p


def turn(meta, text, history, limit, steps=STEPS, quoted=None):
    """code_turn on the metadata ask, else on text_price; None leaves the turn to the harness."""
    return code_turn(meta if meta is not None else text_price(text, history, quoted), history, limit, steps)


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
