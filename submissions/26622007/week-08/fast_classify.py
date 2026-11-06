"""Turns whose outcome the code already fixes, read without a harness call.

Week 4 names Jev (TypeSafe AI): classify a message's act and price with a cheap judgment instead of a full LLM call. Jev
needs a paid API key, so these are plain rules, used only where the code after the harness would end the same whatever
the harness said; every other turn still goes to the harness, and the price guards run after both.

Seller (seller.py): any offer at or above the round's floor is accepted by the code, so a bare number there is an accept
(204 of 473 seller-harness turns, 23:29-00:35 on 11/07, answered accept at a median 3.6 s each). A message without a
digit has no offer the code would take, so it is asked again. Buyer (buyer.py): a shop asking my last offer or less is
taken at its ask (4 of 273 buyer-harness turns; the harness offered the ask in all 4).
"""
import re

BARE_OFFER = re.compile(r"\s*([1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]*)\s*(?:\.(?![0-9])[^0-9]*)?")


def bare_offer(text):
    """The offer of a message that is a number alone, or a number and a full stop then words without any digit
    ("24000. I pay the moment the receipt arrives."); else None."""
    m = BARE_OFFER.fullmatch(text or "")
    return int(m[1].replace(",", "")) if m else None


def seller_turn(text, deal, floor):
    """The seller's turn decided without the harness, or None. floor: shop_memory.concession_floor for this round."""
    if not re.search(r"[0-9]", text or ""):
        return {"buyer_offer": 0, "action": "counter", "price": deal["ask"], "message": "", "why": "no digit, no offer"}
    offer = bare_offer(text)
    if offer is not None and offer >= max(floor, deal["reserve"]):
        return {"buyer_offer": offer, "action": "accept", "price": offer, "message": "",
                "why": f"bare offer {offer} >= floor {max(floor, deal['reserve'])}"}
    return None


def buyer_turn(ask, history):
    """The buyer's turn decided without the harness, or None: the shop's ask is at or under my last offer, so I offer
    the ask. Once per ask: a shop that asks it again after I offered it goes to the harness."""
    mine = [p for who, p in history if who == "you offered"]
    if not mine or not isinstance(ask, int) or isinstance(ask, bool) or ask <= 0 or ask > mine[-1]:
        return None
    if history[-2:] == [("shop asked", ask), ("you offered", ask)]:
        return None
    return {"shop_price": ask, "action": "offer", "price": ask}
