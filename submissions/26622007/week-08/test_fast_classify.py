"""Turns whose outcome the code already fixes go without a harness call (fast_classify; 11/07 00:30, Jev in the
lecture: classify with a cheap judgment instead of a full LLM call). Jev itself needs a paid key, so these are rules.

Seller: of 473 seller-harness turns from 23:29 to 00:35, 204 answered accept; the code takes any offer at or above the
round's floor whatever the harness says, so a bare number at or above the floor is an accept without the 3.6 s call.
Buyer: a shop that comes down to or below my last offer is taken at its ask (4 of 273 turns, the harness did the same)."""
import unittest
from unittest import mock

import buyer
import fast_classify


def task(text, meta=None, state="TASK_STATE_INPUT_REQUIRED"):
    msg = {"parts": [{"text": text}]}
    if meta is not None:
        msg["metadata"] = {"ask": meta}
    return {"id": "t1", "status": {"state": state, "message": msg}}


DEAL = {"ask": 30000, "reserve": 20000, "open": 32000, "rounds": 2}


class BareOfferTest(unittest.TestCase):
    def test_a_bare_number_is_the_offer(self):
        self.assertEqual(fast_classify.bare_offer("24000"), 24000)
        self.assertEqual(fast_classify.bare_offer(" 24,000 "), 24000)

    def test_a_number_then_words_without_digits_is_the_offer(self):
        self.assertEqual(fast_classify.bare_offer("24000. Other shops have it in stock too, so I would like to settle "
                                                  "quickly. I pay the moment the receipt arrives."), 24000)

    def test_anything_less_clear_goes_to_the_harness(self):
        for text in ["24000 is too much", "I can pay 24000", "24000. It must last at least 85 hours.", "24000?",
                     "24000 or 23000", "", "about 66% of list: 24000", "24000.5", "-24000", "0",
                     "30000. Too expensive, can you go lower?", "24000. No.", "24000. Two units please",
                     "24000. twenty thousand", "24000. I can't go higher", "24000. ２ units", "２４０００"]:
            self.assertIsNone(fast_classify.bare_offer(text), text)


class SellerTurnTest(unittest.TestCase):
    def test_an_offer_at_or_above_the_floor_is_accepted(self):
        d = fast_classify.seller_turn("26000", DEAL, 26000)
        self.assertEqual((d["action"], d["buyer_offer"], d["price"]), ("accept", 26000, 26000))

    def test_an_offer_above_our_ask_is_still_an_accept_so_the_fair_check_runs(self):
        self.assertEqual(fast_classify.seller_turn("31000", DEAL, 26000)["buyer_offer"], 31000)

    def test_an_offer_under_the_floor_is_the_harness_s_counter(self):
        self.assertIsNone(fast_classify.seller_turn("25999", DEAL, 26000))

    def test_never_under_the_reserve_even_with_a_wrong_floor(self):
        self.assertIsNone(fast_classify.seller_turn("19000", DEAL, 18000))

    def test_a_message_without_any_digit_has_no_offer(self):
        d = fast_classify.seller_turn("Can you do better?", DEAL, 26000)
        self.assertEqual((d["buyer_offer"], d["action"]), (0, "counter"))

    def test_a_message_with_unicode_digits_goes_to_the_harness(self):
        self.assertIsNone(fast_classify.seller_turn("２６０００", DEAL, 26000))

    def test_a_message_with_other_numbers_goes_to_the_harness(self):
        self.assertIsNone(fast_classify.seller_turn("26000. It must last at least 85 hours.", DEAL, 26000))


class BuyerTurnTest(unittest.TestCase):
    def test_an_ask_at_or_under_my_last_offer_is_taken(self):
        h = [("shop asked", 30000), ("you offered", 25000)]
        self.assertEqual(fast_classify.buyer_turn(25000, h), {"shop_price": 25000, "action": "offer", "price": 25000})
        self.assertEqual(fast_classify.buyer_turn(24000, h)["price"], 24000)

    def test_an_ask_above_my_last_offer_is_the_harness_s(self):
        self.assertIsNone(fast_classify.buyer_turn(25001, [("shop asked", 30000), ("you offered", 25000)]))

    def test_no_offer_yet_or_no_ask(self):
        self.assertIsNone(fast_classify.buyer_turn(25000, [("shop asked", 30000)]))
        self.assertIsNone(fast_classify.buyer_turn(None, [("you offered", 25000)]))
        self.assertIsNone(fast_classify.buyer_turn(0, [("you offered", 25000)]))

    def test_once_per_ask_so_a_shop_that_will_not_take_it_goes_to_the_harness(self):
        h = [("shop asked", 30000), ("you offered", 25000), ("shop asked", 25000), ("you offered", 25000)]
        self.assertIsNone(fast_classify.buyer_turn(25000, h))


class BuyerTalkTest(unittest.TestCase):
    def test_the_talk_takes_the_ask_without_the_harness_and_keeps_the_guards(self):
        sent, calls = [], []
        done = {"id": "t1", "status": {"state": "TASK_STATE_COMPLETED"},
                "artifacts": [{"parts": [{"data": {"price": 24500}}]}]}
        replies = [task("We came down for you, asking 24500.", None), done]

        def send(url, text, task_id=None):
            sent.append(text)
            return replies.pop(0)

        def decide(item, budget, history, text, meta, turn, note=""):
            calls.append(text)
            return {"shop_price": 30000, "action": "offer", "price": 25000}
        out = {"state": "", "task": None, "receipt": None, "history": [], "agreed": None, "holds": 0}
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", decide), \
                mock.patch.object(buyer, "log"), mock.patch.object(buyer, "rpc", lambda *a: None):
            buyer.talk("u", task("asking 30000", 30000), "lamp", 26000, "", out)
        self.assertEqual(sent, ["25000", "24500"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(out["state"], "TASK_STATE_COMPLETED")


if __name__ == "__main__":
    unittest.main()
