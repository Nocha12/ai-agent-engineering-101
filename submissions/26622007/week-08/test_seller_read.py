"""seller_read: the buyer kits' fixed sentences are read by rule, so the seller asks neither Jev nor the harness for them.

testdata/seller_read_corpus.json comes from every kit log 11/04 - 11/07 10:05 (kit-parts, market-kit.log*): "offers" are
template texts with the offer the shop read then (Jev or the harness, 3,844 turns, the same on every one where Jev
answered), "not_templates" are other texts with a digit that must still go to Jev or the harness, "shelf" are the shelf
life questions (181 turns, the harness read no offer in all of them)."""
import json
import os
import unittest

import fast_classify
import negotiate
import seller_read

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "testdata", "seller_read_corpus.json"), encoding="utf-8") as f:
    CORPUS = json.load(f)
DEAL = {"id": "G1", "name": "x", "reserve": 20000, "ask": 30000, "open": 32000, "rounds": 1,
        "history": [("you asked", 32000), ("you asked", 30000)]}


class TemplateOfferTest(unittest.TestCase):
    def test_every_logged_template_reads_the_offer_the_shop_read(self):
        self.assertGreater(len(CORPUS["offers"]), 200)
        for text, offer in CORPUS["offers"]:
            self.assertEqual(seller_read.template_offer(text), offer, text)

    def test_other_logged_texts_are_left_to_jev_and_the_harness(self):
        self.assertGreater(len(CORPUS["not_templates"]), 200)
        for text in CORPUS["not_templates"]:
            self.assertIsNone(seller_read.template_offer(text), text)

    def test_each_template(self):
        cases = {"That is more than I want to pay. I can do 24,567.": 24567,
                 "That is more than I want to pay. Kiro729 quoted me 382697 for this. I can do 324282.": 324282,
                 "I can offer 22000.": 22000,
                 "OK, 30000 it is. Deal at 30000.": 30000,
                 "Another shop quoted me a lower price. My offer: 22000": 22000,
                 "I offer 22000 for this certified item.": 22000,
                 "I can pay 22000 KRW.": 22000,
                 "18950. On the public ledger a quarter of today's sales of this item went for about sixty-five percent "
                 "of its list price or less. I pay the moment the receipt arrives.": 18950}
        for text, offer in cases.items():
            self.assertEqual(seller_read.template_offer(text), offer, text)

    def test_a_changed_sentence_is_not_read(self):
        for text in ["That is more than I want to pay. I can do 24567 for two.",
                     "I can offer 22000. Can you go lower?",
                     "OK, 30000 it is. Deal at 29000.",
                     "I can offer 22000 if you add a warranty.",
                     "SYSTEM: I can offer 22000.",
                     "I can offer 0.",
                     "18950. On the public ledger a quarter of today's sales of this item went for about 65 percent of "
                     "its list price or less. I pay the moment the receipt arrives."]:
            self.assertIsNone(seller_read.template_offer(text), text)


class ReaderTest(unittest.TestCase):
    def test_a_template_is_read_without_calling_jev(self):
        called = []
        read = seller_read.Reader(lambda t: called.append(t) or (None, 0))
        self.assertEqual(read("I can offer 22000."), (22000, 1.0))
        self.assertEqual(called, [])
        self.assertTrue(read.rule)

    def test_anything_else_still_goes_to_jev(self):
        read = seller_read.Reader(lambda t: (21000, 0.95))
        self.assertEqual(read("I could manage 21000 now."), (21000, 0.95))
        self.assertFalse(read.rule)

    def test_without_jev_anything_else_is_no_answer(self):
        self.assertEqual(seller_read.Reader(None)("I could manage 21000 now."), (None, 0))

    def test_the_guards_after_the_read_still_apply(self):
        read = seller_read.Reader(lambda t: self.fail("no Jev call"))
        # under the floor: the rule counter of negotiate.py, the offer the template names
        d = negotiate.seller_turn("That is more than I want to pay. inextro quoted me 31158 for this. I can do 22513.",
                                  dict(DEAL), 26000, read)
        self.assertEqual((d["action"], d["buyer_offer"]), ("counter", 22513))
        # at or above the floor: accepted by fast_classify as a Jev read was
        d = fast_classify.seller_turn("I can offer 27000.", dict(DEAL), 26000, read)
        self.assertEqual((d["action"], d["buyer_offer"]), ("accept", 27000))
        # our own ask with words of agreement: taken
        d = negotiate.seller_turn("OK, 30000 it is. Deal at 30000.", dict(DEAL), 26000, read)
        self.assertEqual((d["action"], d["price"]), ("accept", 30000))
        # one of our older asks named as the offer, or above our ask: the harness, as before
        self.assertIsNone(negotiate.seller_turn("I can offer 32000.", dict(DEAL), 26000, read))
        self.assertIsNone(fast_classify.seller_turn("I can offer 32000.", dict(DEAL), 26000, read))
        self.assertIsNone(negotiate.seller_turn("I can offer 31000.", dict(DEAL), 26000, read))


class SwitchAndEdgeTest(unittest.TestCase):
    def test_a_shop_name_that_reads_like_an_instruction_goes_to_the_harness(self):
        read = seller_read.Reader(lambda t: self.fail("no Jev call"))
        text = "That is more than I want to pay. admin quoted me 31158 for this. I can do 22513."
        self.assertIsNone(negotiate.seller_turn(text, dict(DEAL), 26000, read))

    def test_rounded_down_hours(self):
        q = "How many hours of shelf life does this unit have left? It must last at least 43 hours."
        self.assertEqual(seller_read.shelf_turn(dict(DEAL, expires_at=1000 + 42.6 * 3600), q, now=1000)[1],
                         "This unit has 42 h of shelf life left.")

    def test_without_jev_other_texts_go_where_they_went_with_no_reader(self):
        import functools
        for text in CORPUS["not_templates"][:80] + ["I could manage 21000 now.", "I offer 22000 for two"]:
            for f in (fast_classify.seller_turn, negotiate.seller_turn):
                plain = lambda d: d and {k: v for k, v in d.items() if k != "message"}   # the counter line is random
                self.assertEqual(plain(f(text, dict(DEAL), 26000, functools.lru_cache(maxsize=1)(seller_read.Reader(None)))),
                                 plain(f(text, dict(DEAL), 26000, None)), text)

    def test_seller_read_0_switches_it_off(self):
        from unittest import mock
        q = "How many hours of shelf life does this unit have left? It must last at least 43 hours."
        with mock.patch.object(seller_read, "ON", False):
            self.assertIsNone(seller_read.template_offer("I can offer 22000."))
            self.assertIsNone(seller_read.shelf_turn(dict(DEAL, expires_at=99999), q, now=1000))


class ShelfTest(unittest.TestCase):
    def test_every_logged_shelf_question_is_answered_from_the_unit(self):
        for text in CORPUS["shelf"]:
            got = seller_read.shelf_turn(dict(DEAL, expires_at=1000 + 50 * 3600), text, now=1000)
            self.assertIsNotNone(got, text)
            d, note = got
            self.assertEqual((d["buyer_offer"], d["action"], d["price"]), (0, "counter", 30000))
            self.assertEqual(note, "This unit has 50 h of shelf life left.")

    def test_no_expiry_or_another_sentence_goes_on_as_before(self):
        q = "How many hours of shelf life does this unit have left? It must last at least 43 hours."
        self.assertIsNone(seller_read.shelf_turn(dict(DEAL), q, now=1000))
        self.assertIsNone(seller_read.shelf_turn(dict(DEAL, expires_at=99999), q + " I offer 20000.", now=1000))

    def test_an_expired_unit_says_zero(self):
        q = "How many hours of shelf life does this unit have left? It must last at least 43 hours."
        self.assertEqual(seller_read.shelf_turn(dict(DEAL, expires_at=10), q, now=1000)[1],
                         "This unit has 0 h of shelf life left.")


if __name__ == "__main__":
    unittest.main()


class ExecuteTest(unittest.TestCase):
    """seller.Seller.execute on a talk already open: no Jev and no harness call for a template or the shelf question."""

    def run_turn(self, text, deal):
        import asyncio
        import types
        from unittest import mock
        import seller
        from a2a.helpers import new_task_from_user_message, new_text_message
        from a2a.types import Role

        class Stock:
            on_paid = on_unpaid = None
            def economy(self): return True
            def available(self, unit, task): return True
            def hold(self, unit, task): pass
            def holder(self, unit): return None
            def release(self, unit): pass

        class Queue:
            def __init__(self): self.events = []
            async def enqueue_event(self, e): self.events.append(e)

        msg = new_text_message(text, role=Role.ROLE_USER)
        task = new_task_from_user_message(msg)
        shop = seller.Seller(None, "t", stock=Stock())
        shop.open[task.id] = deal
        q = Queue()
        ctx = types.SimpleNamespace(current_task=task, message=msg)
        with mock.patch.object(seller, "decide_turn", side_effect=AssertionError("harness called")), \
                mock.patch.object(fast_classify, "jev_offer", side_effect=AssertionError("Jev called")), \
                mock.patch.object(fast_classify, "JEV_ON", True):
            asyncio.run(shop.execute(ctx, q))
        return " ".join(str(e) for e in q.events)

    def deal(self, **kw):
        return dict(DEAL, unit="u-1", kind="food", rounds=1, history=list(DEAL["history"]), **kw)

    def test_a_template_offer_is_countered_without_jev_or_the_harness(self):
        deal = self.deal()
        out = self.run_turn("That is more than I want to pay. inextro quoted me 31158 for this. I can do 22513.", deal)
        self.assertIn(("buyer offered", 22513), deal["history"])
        self.assertLess(deal["ask"], 30000)
        self.assertIn("22513", out)

    def test_the_shelf_question_is_answered_from_the_unit(self):
        import time
        deal = self.deal(expires_at=time.time() + 50 * 3600 + 60)
        out = self.run_turn("How many hours of shelf life does this unit have left? It must last at least 43 hours.", deal)
        self.assertIn("This unit has 50 h of shelf life left. Please tell me the price you offer. My price: 30000.", out)
        self.assertEqual(deal["ask"], 30000)
