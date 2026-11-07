"""Limited deterministic 1-won opener probe, no live market/model calls."""
import json
import os
import tempfile
import time
import unittest
from unittest import mock
import buyer
import market_buyer as mb


class BalancedProbe(unittest.TestCase):
    def test_only_time_bounded_target_shops_are_enabled_and_off_switch_wins(self):
        self.assertIsNone(mb.opening_probe_arm("absent", "11im", "o1", {}, now=mb.ONE_WON_PROBE_START-1))
        self.assertIsNone(mb.opening_probe_arm("absent", "11im", "o1", {}, now=mb.ONE_WON_PROBE_END))
        self.assertIsNone(mb.opening_probe_arm("absent", "11im", "o1", {"BUYER_ONE_WON_PROBE": "0"}, now=mb.ONE_WON_PROBE_START))
        self.assertIsNone(mb.opening_probe_arm("absent", "other", "o1", {"BUYER_ONE_WON_PROBE": "1"}, now=mb.ONE_WON_PROBE_START))

    def test_within_shop_imbalance_is_corrected_and_order_retry_keeps_arm(self):
        now = time.time()
        rows = [{"ts": now-10, "order": "o-old", "shop": "11im", "one_won_probe": "A"}]
        with tempfile.TemporaryDirectory() as md:
            os.mkdir(os.path.join(md, "measure"))
            with open(os.path.join(md, "measure", "orders.jsonl"), "w") as f:
                f.writelines(json.dumps(r)+"\n" for r in rows)
            self.assertEqual(mb.opening_probe_arm(md, "11im", "o-next", {"BUYER_ONE_WON_PROBE":"1"}, now=mb.ONE_WON_PROBE_START+1), "B")
            self.assertEqual(mb.opening_probe_arm(md, "11im", "o-old", {"BUYER_ONE_WON_PROBE":"1"}, now=mb.ONE_WON_PROBE_START+1), "A")


class ProbeTalk(unittest.TestCase):
    def test_only_first_valid_food_code_offer_is_one_then_normal_counter(self):
        h=[]
        self.assertEqual(mb.opening_probe_turn(10000, "", h, 10000, (.9,), None, False, "B")["price"], 1)
        h=[("shop asked",10000),("you offered",1)]
        self.assertEqual(mb.opening_probe_turn(9500, "", h, 10000, (.9,), None, False, "B")["price"], 9500)
        self.assertEqual(mb.opening_probe_turn(10000, "", [], 10000, (.9,), None, False, "A")["price"],9000)
        self.assertEqual(mb.opening_probe_turn(10000, "", [], 10000, (.9,), None, True, "B")["price"],9000)
        self.assertIsNone(mb.opening_probe_turn(None, "unclear price", [], 10000, (.9,), None, False, "B"))

    def test_real_talk_sends_one_then_counter_without_harness(self):
        def task(ask=None,done=False):
            return {"id":"t1","status":{"state":"TASK_STATE_COMPLETED" if done else "TASK_STATE_INPUT_REQUIRED",
                    "message":{"metadata":{"ask":ask},"parts":[{"text":str(ask)}]}},
                    **({"artifacts":[{"parts":[{"data":{"price":9500,"item":"G1"}}]}]} if done else {})}
        sent=[]
        replies=iter([task(9500),task(done=True)])
        def send(url,text,task_id=None):
            sent.append(text);return next(replies)
        ladder=lambda meta,text,h:mb.opening_probe_turn(meta,text,h,10000,(.9,),None,False,"B")
        with mock.patch.object(buyer,"send",send),mock.patch.object(buyer,"decide",side_effect=AssertionError("no LLM")):
            out=buyer.talk("https://example.invalid",task(10000),"food",10000,"",
                          {"history":[],"agreed":None,"holds":0},ladder=ladder)
        self.assertEqual(sent,["1","9500"])
        self.assertEqual(out["agreed"],9500)
        self.assertEqual(out["turns"],{"code":2})
