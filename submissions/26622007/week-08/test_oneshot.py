"""oneshot: the talk priced by code (one offer under the shop's first ask, then its counter taken), with no harness call."""
import unittest
from unittest import mock

import buyer
import oneshot


def task(state, ask=None, receipt=None, text=None):
    t = {"id": "t-1", "status": {"state": state, "message": {"parts": [{"text": text if text is not None else f"asking {ask}"}],
                                                          **({"metadata": {"ask": ask}} if ask is not None else {})}}}
    if receipt:
        t["artifacts"] = [{"parts": [{"data": receipt}]}]
    return t


def fresh():
    return {"history": [], "agreed": None, "holds": 0}


class CodeTurn(unittest.TestCase):
    def turn(self, ask, history, limit=10000, steps=(0.9,)):
        return oneshot.code_turn(ask, history, limit, steps)

    def test_first_offer_is_the_step_share_of_the_first_ask(self):
        self.assertEqual(self.turn(10000, []), {"shop_price": 10000, "action": "offer", "price": 9000})

    def test_first_offer_never_over_my_limit(self):
        self.assertEqual(self.turn(20000, [], limit=12000)["price"], 12000)

    def test_counter_within_my_limit_is_taken(self):
        h = [("shop asked", 10000), ("you offered", 9000)]
        self.assertEqual(self.turn(9600, h), {"shop_price": 9600, "action": "offer", "price": 9600})

    def test_counter_over_my_limit_gets_my_limit_once(self):
        h = [("shop asked", 12000), ("you offered", 9000)]
        self.assertEqual(self.turn(11000, h, limit=10000)["price"], 10000)
        h += [("shop asked", 11000), ("you offered", 10000)]
        self.assertEqual(self.turn(10500, h, limit=10000)["action"], "walk_away")

    def test_same_ask_after_i_offered_it_walks(self):
        """The shop did not take its own ask back: no loop of the same number."""
        h = [("shop asked", 10000), ("you offered", 9000), ("shop asked", 9600), ("you offered", 9600)]
        self.assertEqual(self.turn(9600, h)["action"], "walk_away")

    def test_ask_at_or_under_my_last_offer_is_taken(self):
        h = [("shop asked", 10000), ("you offered", 9000)]
        self.assertEqual(self.turn(8800, h)["price"], 8800)

    def test_ladder_of_steps_rises_from_the_first_ask(self):
        steps = (0.84, 0.9)
        self.assertEqual(self.turn(10000, [], steps=steps)["price"], 8400)
        h = [("shop asked", 10000), ("you offered", 8400)]
        self.assertEqual(self.turn(9800, h, steps=steps)["price"], 9000)
        h += [("shop asked", 9800), ("you offered", 9000)]
        self.assertEqual(self.turn(9700, h, steps=steps)["price"], 9700)

    def test_no_ask_read_leaves_the_turn_to_the_harness(self):
        self.assertIsNone(self.turn(None, []))
        self.assertIsNone(self.turn(0, []))

    def test_offers_end_after_the_steps_and_two_more(self):
        h = [("shop asked", 10000), ("you offered", 9000), ("shop asked", 9800), ("you offered", 9800),
             ("shop asked", 9900), ("you offered", 9900)]
        self.assertEqual(self.turn(9700, h)["action"], "walk_away")


class Settings(unittest.TestCase):
    def test_env_wins_over_pricing(self):
        self.assertTrue(oneshot.enabled({"oneshot": False}, {"BUYER_ONESHOT": "1"}))
        self.assertFalse(oneshot.enabled({"oneshot": True}, {"BUYER_ONESHOT": "0"}))
        self.assertFalse(oneshot.enabled({"oneshot": True}, {"BUYER_ONESHOT": "false"}))
        self.assertFalse(oneshot.enabled({"oneshot": True}, {"BUYER_ONESHOT": " OFF "}))
        self.assertTrue(oneshot.enabled({}, {"BUYER_ONESHOT": "true"}))
        self.assertTrue(oneshot.enabled({"oneshot": True}, {"BUYER_ONESHOT": ""}))

    def test_meta_harness_may_not_move_the_switch(self):
        import hard_limits
        old = {"buyer": {"oneshot": True, "quote_shops": 10}}
        self.assertTrue(any("FIXED" in p for p in hard_limits.check_policy_change(old, {"buyer": {"oneshot": False, "quote_shops": 10}})))
        self.assertTrue(any("removed" in p for p in hard_limits.check_policy_change(old, {"buyer": {"quote_shops": 10}})))
        self.assertEqual(hard_limits.check_policy_change(old, old), [])

    def test_pricing_flag_and_default_off(self):
        self.assertTrue(oneshot.enabled({"oneshot": True}, {}))
        self.assertFalse(oneshot.enabled({}, {}))
        self.assertFalse(oneshot.enabled({"oneshot": "yes"}, {}))

    def test_steps_kept_only_when_sane(self):
        self.assertEqual(oneshot.steps({"oneshot_steps": [0.85, 0.92]}), (0.85, 0.92))
        for bad in ([1.2], [0.3], [0.9, 0.8], "0.9", [True], [], [0.9] * 6):
            self.assertEqual(oneshot.steps({"oneshot_steps": bad}), oneshot.STEPS, bad)
        self.assertEqual(oneshot.steps({}), oneshot.STEPS)


class InTalk(unittest.TestCase):
    def test_talk_with_a_ladder_calls_no_harness(self):
        sent = []
        replies = iter([task("TASK_STATE_INPUT_REQUIRED", 9500),
                        task("TASK_STATE_COMPLETED", receipt={"price": 9500, "item": "G1"})])

        def send(url, text, task_id=None):
            sent.append(text)
            return next(replies)
        lad = lambda meta, text, history: oneshot.turn(meta, text, history, 10000, (0.9,))
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", side_effect=AssertionError("no harness")):
            out = buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", 10000), "x", 10000, "", fresh(), ladder=lad)
        self.assertEqual(sent, ["9000", "9500"])
        self.assertEqual(out["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(out["agreed"], 9500)

    def test_one_text_price_is_priced_by_code(self):
        sent = []
        lad = lambda meta, text, history: oneshot.turn(meta, text, history, 10000, (0.9,), quoted=8000)

        def send(url, text, task_id=None):
            sent.append(text)
            return task("TASK_STATE_COMPLETED", receipt={"price": int(text), "item": "G1"})
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", side_effect=AssertionError("no harness")):
            buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", text="Grade A, 6 h warranty, asking 8000."), "x", 10000, "",
                       fresh(), ladder=lad)
        self.assertEqual(sent, ["7200"])

    def test_two_text_prices_go_to_the_harness(self):
        """"Was asking 20000, now just 12000": text_ask reads 20000, and code would offer 18000 against 12000."""
        calls, sent = [], []

        def decide(*a, **k):
            calls.append(a)
            return {"shop_price": 12000, "action": "offer", "price": 11000}

        def send(url, text, task_id=None):
            sent.append(text)
            return task("TASK_STATE_COMPLETED", receipt={"price": int(text), "item": "G1"})
        lad = lambda meta, text, history: oneshot.turn(meta, text, history, 25000, (0.9,), quoted=20000)
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "decide", decide):
            buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", text="Was asking 20000, now just 12000."), "x", 25000, "", fresh(),
                       ladder=lad)
        self.assertEqual(len(calls), 1)
        self.assertEqual(sent, ["11000"])

    def test_no_price_in_text_goes_to_the_harness(self):
        calls = []

        def decide(*a, **k):
            calls.append(a)
            return {"shop_price": 0, "action": "walk_away", "price": 0}
        lad = lambda meta, text, history: oneshot.turn(meta, text, history, 10000, (0.9,))
        with mock.patch.object(buyer, "send", lambda *a, **k: None), mock.patch.object(buyer, "decide", decide), \
                mock.patch.object(buyer, "rpc", lambda *a: task("TASK_STATE_CANCELED")):
            buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", text="What do you need it for?"), "x", 10000, "", fresh(),
                       ladder=lad)
        self.assertEqual(len(calls), 1)

    def test_walk_away_from_the_ladder_cancels(self):
        cancelled = []
        lad = lambda meta, text, history: oneshot.turn(meta, text, history, 10000, (0.9,))
        replies = iter([task("TASK_STATE_INPUT_REQUIRED", 13000), task("TASK_STATE_INPUT_REQUIRED", 13000)])
        with mock.patch.object(buyer, "send", lambda *a, **k: next(replies)), \
                mock.patch.object(buyer, "decide", side_effect=AssertionError("no harness")), \
                mock.patch.object(buyer, "rpc", lambda url, m, p: cancelled.append(m) or task("TASK_STATE_CANCELED")):
            out = buyer.talk("u", task("TASK_STATE_INPUT_REQUIRED", 14000), "x", 10000, "", fresh(), ladder=lad)
        self.assertEqual(cancelled, ["CancelTask"])
        self.assertIsNone(out["agreed"])
        self.assertLessEqual(max(p for who, p in out["history"] if who == "you offered"), 10000)


class TextPrice(unittest.TestCase):
    def test_counter_beside_my_own_offer(self):
        h = [("shop asked", 14000), ("you offered", 11300)]
        self.assertEqual(oneshot.text_price("11300 is too low. Counteroffer price: 13049.", h), 13049)

    def test_two_prices_go_to_the_harness(self):
        h = [("shop asked", 25000), ("you offered", 20000)]
        self.assertIsNone(oneshot.text_price("Was asking 20000, now just 12000.", [], quoted=20000))
        self.assertIsNone(oneshot.text_price("I can't go back to my old asking 24000; best I can do is 23500.", h))

    def test_sku_size_and_hours_are_not_prices(self):
        text = "제주 삼다수 2L X 18병 (G3434758a), grade B, about 373.1 h shelf life left; warranty 0 h. Asking price: 12,766."
        self.assertEqual(oneshot.price_numbers(text) & {3434758, 373}, set())
        self.assertEqual(oneshot.text_price(text, [], quoted=12766), 12766)

    def test_first_turn_must_be_the_checked_quote(self):
        self.assertIsNone(oneshot.text_price("asking 13600", [], quoted=13200))
        self.assertIsNone(oneshot.text_price("asking 13600", []))

    def test_never_over_the_last_ask(self):
        h = [("shop asked", 10000), ("you offered", 9000)]
        self.assertIsNone(oneshot.text_price("Counteroffer: 10400.", h))
        self.assertEqual(oneshot.text_price("Counteroffer: 9800.", h), 9800)

    def test_turn_prefers_metadata(self):
        self.assertEqual(oneshot.turn(10000, "asking 99999", [], 10000)["price"], 9000)
        self.assertIsNone(oneshot.turn(None, "no numbers here", [], 10000, quoted=10000))


if __name__ == "__main__":
    unittest.main()
