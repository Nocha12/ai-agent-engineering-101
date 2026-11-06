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

BARE_OFFER = re.compile(r"\s*([1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]*)\s*(?:\.(?!\d)(\D*))?")
# words after the number that may make it something other than an offer ("30000. Too expensive, can you go lower?"),
# a count ("24000. Two units") or a number in words: those turns go to the harness (independent review 11/07, Medium)
NOT_PLAIN = re.compile(r"\?|n't|\b(?:no|not|never|lower|less|cheaper|discount|expensive|too (?:much|high|pricey)|can you|"
                       r"could you|would you|units?|pieces?|pcs|half|instead|rather|but|reject|refuse|decline|counter|"
                       r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|sixty|"
                       r"seventy|eighty|ninety|hundred|thousand|million)\b", re.I)


def bare_offer(text):
    """The offer of a message that is a number alone, or a number and a full stop then plain words without any digit
    ("24000. I pay the moment the receipt arrives."); else None."""
    m = BARE_OFFER.fullmatch(text or "")
    if not m or NOT_PLAIN.search(m[2] or ""):
        return None
    return int(m[1].replace(",", ""))


def seller_turn(text, deal, floor):
    """The seller's turn decided without the harness, or None. floor: shop_memory.concession_floor for this round."""
    if not re.search(r"\d", text or ""):
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
