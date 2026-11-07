"""The shop choice by rule (BUYER_CHOOSE_ENGINE=rule, 메인 11/07 09:30): no agent call; a shop the prefetch has not asked
goes first, then the one that sold me this item cheapest, then the most reviewed."""
import os
import unittest
from unittest import mock

import market_buyer as mb


def shop(name, reviews=0):
    return {"name": name, "url": f"http://{name}", "reviews": [{"stars": 5, "review": "ok"}] * reviews}


def deal(name, price, item="김치"):
    return {"shop": name, "item": item, "result": "deal", "price": price, "signed": True, "problems": []}


class ChooseRule(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"BUYER_CHOOSE_ENGINE": "rule"})
        p.start()
        self.addCleanup(p.stop)
        a = mock.patch.object(mb, "ask", side_effect=AssertionError("no agent call"))
        a.start()
        self.addCleanup(a.stop)

    def test_a_shop_not_yet_asked_goes_first_then_the_cheapest_before(self):
        shops = [shop("a", 9), shop("b", 2), shop("c", 1)]
        memory = {"trades": [deal("a", 9000), deal("b", 9500), deal("c", 9900)]}
        s, why = mb.choose_racing(shops, memory, "김치", 12000, {"enough": lambda: False, "asked": {"a"}})
        self.assertEqual(s["name"], "b")
        self.assertIn(" rule: ", why)
        self.assertEqual(mb.choose_path(why), "rule")
        s, _ = mb.choose_racing(shops, memory, "김치", 12000, {"enough": lambda: False, "asked": set()})
        self.assertEqual(s["name"], "a")

    def test_no_history_the_most_reviewed_and_the_filters_still_apply(self):
        shops = [shop("a", 5), shop("b", 7), shop("cheat", 30)]
        memory = {"trades": [], "carried_bans": {"cheat": "receipt did not match"}}
        s, _ = mb.choose_racing(shops, memory, "김치", 12000, {"enough": lambda: False, "asked": set()})
        self.assertEqual(s["name"], "b")

    def test_switch_off_keeps_the_agent(self):
        with mock.patch.dict(os.environ, {"BUYER_CHOOSE_ENGINE": "agent"}):
            self.assertFalse(mb.choose_by_rule())
        with mock.patch.dict(os.environ, {"BUYER_CHOOSE_ENGINE": ""}), \
                mock.patch.object(mb, "load_json", return_value={"buyer_choose": {"engine": "rule"}}):
            self.assertTrue(mb.choose_by_rule())
        with mock.patch.dict(os.environ, {"BUYER_CHOOSE_ENGINE": ""}), mock.patch.object(mb, "load_json", return_value={}):
            self.assertFalse(mb.choose_by_rule())


if __name__ == "__main__":
    unittest.main()
