"""Seller turns where Jev reads the buyer and rules answer (NEGOTIATE_ENGINE=jevrule; 대표님 11/07 01:59).

On 2,772 seller-harness turns (11/06 17:18 - 11/07 02:00, market-kit.log) the code after the harness already fixed the
action: an offer at or above the round's floor is accepted, one under it is countered, and the counter is held inside
[floor, ask]. The harness only chose where in that range to counter and wrote one line. 91% of the messages were a bare
number. Its counters sat at floor + share x (ask - floor), the share falling by round (median 0.62, 0.53, 0.46, then
0.25); that rule gives its price within 1% on 84% and within 3% on 95% of 1,755 counters. Replayed over all 2,772 turns
this engine took 2,745 (2,507 by rule, 238 read by Jev at confidence 0.9 or more, all 238 the offer the harness read),
the same action on all but 3, the price within 3% on 93% (mean +0.3%), at about 0.07 s a turn against 3.7 s.
How long the shop took did not change whether buyers stayed (left after a counter: 12% under 3 s, 10% at 4-6 s).

Everything doubtful still goes to the harness: no number read, Jev under 0.9, a number that is not in the message, one
of our older asks, above our ask (FAIR in seller.py), or a message that talks like an instruction. seller.py runs the
same price guards after this as after the harness.

Switch: NEGOTIATE_ENGINE=jevrule|llm in the environment, else pricing.json "negotiate": {"engine": ...}, read at every
turn so it goes off without a restart.
"""
import os
import random
import re

import fast_classify
import shop_memory

# share of (ask - floor) kept by the counter in round 1, 2, 3; the last repeats (medians of the harness, see above)
COUNTER_SHARE = [0.62, 0.53, 0.46, 0.25]
SUSPECT = re.compile(r"\b(?:system|admin|assistant|instruction|ignore|override|prompt|already agreed|you agreed|"
                     r"the shop agreed|market rule|developer)\b", re.I)
LINES = ["Thanks for your offer of {offer}. I can come down a little.",
         "I appreciate the offer of {offer}. Here is a better price.",
         "Thank you for {offer}. I can meet you partway."]
FINAL = ["Thanks for your offer of {offer}. This is already the lowest price we can sell it for.",
         "I appreciate the offer of {offer}, but we are already at our floor price for this item.",
         "Thank you for {offer}. Our price is already as low as it goes."]


def engine_on():
    env = (os.environ.get("NEGOTIATE_ENGINE") or "").strip().lower()
    if env:
        return env == "jevrule"
    cfg = shop_memory.load_pricing().get("negotiate")
    return isinstance(cfg, dict) and str(cfg.get("engine", "")).lower() == "jevrule"


def counter_price(deal, floor):
    """The counter for this round: inside [max(floor, reserve), ask]."""
    least = max(floor, deal["reserve"])
    share = COUNTER_SHARE[min(max(deal["rounds"], 1), len(COUNTER_SHARE)) - 1]
    return int(min(max(round(floor + share * (deal["ask"] - floor)), least), deal["ask"]))


def read_offer(text, deal, jev):
    """The offer in the message, or None when it is not certain."""
    offer = fast_classify.bare_offer(text)
    if offer is not None:
        return offer
    if jev is None or SUSPECT.search(text or ""):
        return None
    try:
        offer, conf = jev(text)
    except Exception:  # noqa: BLE001 - any failure goes to the harness
        return None
    if offer is None or not conf >= fast_classify.JEV_MIN_CONFIDENCE or offer not in fast_classify.written(text):
        return None
    older = {p for who, p in deal.get("history") or [] if who == "you asked"} - {deal["ask"]}
    return None if offer in older else offer


def seller_turn(text, deal, floor, jev=None):
    """The turn as seller.py takes it from the harness ({buyer_offer, action, price, message, why}), or None."""
    if SUSPECT.search(text or ""):
        return None
    offer = read_offer(text, deal, jev)
    if not offer or offer > deal["ask"]:
        return None
    least = max(floor, deal["reserve"])
    if offer >= least:
        return {"buyer_offer": offer, "action": "accept", "price": offer, "message": "",
                "why": f"jevrule offer {offer} >= floor {least}"}
    price = counter_price(deal, floor)
    lines = FINAL if price <= deal["reserve"] else LINES
    return {"buyer_offer": offer, "action": "counter", "price": price,
            "message": random.choice(lines).format(offer=offer),
            "why": f"jevrule counter {price} to {offer} (round {deal['rounds']}, floor {least})"}
