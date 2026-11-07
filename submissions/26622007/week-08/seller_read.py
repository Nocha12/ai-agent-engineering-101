"""The buyer kits' fixed sentences, read by rule on the seller's turn instead of a Jev call (대표님 11/07 09:15: fewer
LLM calls, better trades).

11/07 09:00-10:05: of 2,007 seller turns, 179 were read by Jev and 111 went to the seller harness; Jev made 278 calls an
hour, the most of any LLM tag. Nearly all were a handful of sentences the buyer kits write the same way every time
("That is more than I want to pay. <shop> quoted me N for this. I can do N.", "I can offer N.", "OK, N it is. Deal at
N.", "N. On the public ledger a quarter of today's sales ...", the shelf life question). Over every kit log from 11/04 to
11/07 10:05 the offer these templates name is the offer Jev or the harness read on 3,844 of 3,844 turns where Jev
answered (4 more were Jev timeouts); testdata/seller_read_corpus.json keeps a sample of them.

A sentence is read only when it matches a template whole: any word added or changed goes to Jev and the harness as
before. The read goes through the same checks as Jev's (fast_classify.seller_turn, negotiate.read_offer: in the message,
not one of our older asks, our own ask only with words of agreement, never above our ask), then seller.py's price guards.

The shelf life question names no offer (the harness read none on all 181): the shop answers it from the unit's expiry,
as its opening line already did, and asks for a price."""
import math
import os
import re
import time

N = r"([1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]*)"
OFFER_TEMPLATES = [
    re.compile(r"That is more than I want to pay\.(?: [\w.-]{1,40} quoted me " + N + r" for this\.)? I can do " + N + r"\."),
    re.compile(r"I can offer " + N + r"\."),
    re.compile(r"I offer " + N + r" for this certified item\."),
    re.compile(r"I can pay " + N + r" KRW\."),
    re.compile(r"Another shop quoted me a lower price\. My offer: " + N + r"\.?"),
    re.compile(N + r"\. On the public ledger a quarter of today's sales of this item went for about [a-z]+(?:-[a-z]+)? "
               r"percent of its list price or less\. I pay the moment the receipt arrives\."),
]
# "OK, N it is. Deal at N.": both numbers must be the same
AGREED = re.compile(r"OK, " + N + r" it is\. Deal at " + N + r"\.")
# SELLER_READ=0: no rule read and no shelf answer, every turn goes to Jev and the harness as before (no code revert)
ON = os.environ.get("SELLER_READ", "1") != "0"
SHELF = re.compile(r"How many hours of shelf life does this unit have left\? It must last at least [0-9]+(?:\.[0-9]+)? hours\.")


def _int(s):
    return int(s.replace(",", ""))


def template_offer(text):
    """The offer a buyer kit's fixed sentence names (the last number in it), or None when the text is not one whole."""
    if not ON:
        return None
    t = (text or "").strip()
    m = AGREED.fullmatch(t)
    if m:
        return _int(m[1]) if _int(m[1]) == _int(m[2]) else None
    for p in OFFER_TEMPLATES:
        m = p.fullmatch(t)
        if m:
            return _int([g for g in m.groups() if g][-1])
    return None


class Reader:
    """jev(text) -> (offer or None, confidence) for fast_classify and negotiate: a template is read by rule (confidence
    1.0, rule = True) without a call; anything else goes to jev, or is (None, 0) without one."""

    def __init__(self, jev):
        self.jev, self.rule = jev, False

    def __call__(self, text):
        offer = template_offer(text)
        if offer is not None:
            self.rule = True
            return offer, 1.0
        return self.jev(text) if self.jev is not None else (None, 0)


def shelf_turn(deal, text, now=None):
    """(the turn without an offer, the sentence with the unit's shelf life left) for the shelf life question on a unit
    with an expiry, else None. Whole hours rounded down: a buyer asking for 43 h is not told 43 for 42.6 (review 11/07, Low)."""
    if not ON or not deal.get("expires_at") or not SHELF.fullmatch((text or "").strip()):
        return None
    hours = max(0, (deal["expires_at"] - (time.time() if now is None else now)) / 3600)
    return ({"buyer_offer": 0, "action": "counter", "price": deal["ask"], "message": "",
             "why": "the shelf life question, answered from the unit"},
            f"This unit has {math.floor(hours)} h of shelf life left.")
