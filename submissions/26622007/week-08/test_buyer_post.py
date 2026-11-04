"""buyer_post.py: one code-written buyer post per settled deal, paced, and never in the way of the review hook."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import buyer_post

NOW = 1800000000.0
HERE = os.path.dirname(os.path.abspath(__file__))


def deal(ledger, sku="G1", price=7000, value=10000, ago=60, **kw):
    return {"time": NOW - ago, "order": f"o-{ledger}", "price": price, "shop": "https://s.example", "sku": sku,
            "name": "생수 500mL", "grade": "B", "claim": "B", "claim_honest": True, "expires_at": NOW + 50 * 3600,
            "warranty_hours": None, "value": value, "ledger": ledger, "kind": "food", **kw}


class BuyerPost(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.md = os.path.join(self.tmp.name, ".market")
        os.makedirs(self.md)
        self.settled = os.path.join(self.tmp.name, "settled.jsonl")
        self.memory = os.path.join(self.tmp.name, "mem.json")
        with open(self.memory, "w") as f:
            json.dump({"trades": [{"order": "o-11", "shop": "yms979"}]}, f)

    def tearDown(self):
        self.tmp.cleanup()

    def write_settled(self, rows):
        with open(self.settled, "w") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def outbox(self):
        path = os.path.join(self.md, "outbox.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(x) for x in f]

    def post(self, now=NOW):
        return buyer_post.maybe_post(self.md, self.settled, self.memory, now=now)

    def test_posts_the_newest_deal_with_its_ledger_ref(self):
        self.write_settled([deal(10), deal(11)])
        line = self.post()
        self.assertEqual(line["refs"], [11])
        self.assertEqual(line["as"], "buyer")
        self.assertIn("7,000원, 정가 10,000원 대비 30% 저렴", line["post"])
        self.assertIn("판매: yms979", line["post"])
        self.assertIn("남은 유통기한 50시간", line["post"])
        self.assertEqual(self.outbox(), [line])

    def test_paced_and_each_deal_once(self):
        self.write_settled([deal(10), deal(11)])
        self.post()
        os.remove(os.path.join(self.md, "outbox.jsonl"))     # the kit sent it
        self.assertIsNone(self.post(now=NOW + 60))           # within GAP_MINUTES
        second = self.post(now=NOW + buyer_post.GAP_MINUTES * 60 + 1)
        self.assertEqual(second["refs"], [10])
        os.remove(os.path.join(self.md, "outbox.jsonl"))
        self.assertIsNone(self.post(now=NOW + 2 * buyer_post.GAP_MINUTES * 60 + 2))   # nothing new left

    def test_waits_while_a_buyer_post_is_queued(self):
        self.write_settled([deal(10)])
        with open(os.path.join(self.md, "outbox.jsonl"), "w") as f:
            f.write(json.dumps({"post": "luna", "refs": [1], "as": "buyer"}) + "\n")
        self.assertIsNone(self.post())
        with open(os.path.join(self.md, "outbox.jsonl"), "w") as f:      # a queued vote or shop post does not block
            f.write(json.dumps({"vote": 3, "value": 1, "as": "buyer"}) + "\n")
            f.write(json.dumps({"post": "shop", "refs": [1], "as": "shop"}) + "\n")
        self.assertEqual(self.post()["refs"], [10])

    def test_skips_my_shop_skus_overpaid_and_stale_deals(self):
        with open(os.path.join(self.md, "inventory.json"), "w") as f:
            json.dump({"units": [{"sku": "MINE"}]}, f)
        self.write_settled([deal(10, sku="MINE"), deal(11, price=12000), deal(12, ago=4 * 3600), deal(13, value=0)])
        self.assertIsNone(self.post())
        self.assertEqual(self.outbox(), [])

    def test_goods_show_the_warranty(self):
        self.write_settled([deal(20, kind="goods", warranty_hours=6.0, expires_at=None, price=900000, value=1800000)])
        line = self.post()
        self.assertIn("보증 6h", line["post"])
        self.assertIn("900,000원, 정가 1,800,000원 대비 50% 저렴", line["post"])

    def test_a_bad_line_or_field_skips_only_that_deal(self):
        self.write_settled([deal(10)])
        with open(self.settled, "a") as f:
            f.write('{"half written\n')
            f.write(json.dumps(deal(11, kind="goods", expires_at=None, warranty_hours="six")) + "\n")
        self.assertEqual(self.post()["refs"], [10])

    def test_restock_list_skus_and_a_post_being_sent_block(self):
        pricing = os.path.join(self.tmp.name, "pricing.json")
        with open(pricing, "w") as f:
            json.dump({"restock": {"priority_skus": ["P1"], "sku_target_units": {"T1": 2}}}, f)
        self.assertEqual(buyer_post.my_shop_skus(self.md, pricing), {"P1", "T1"})
        self.write_settled([deal(10)])
        with open(os.path.join(self.md, "outbox.taking"), "w") as f:
            f.write(json.dumps({"post": "x", "refs": [1], "as": "buyer"}) + "\n")
        self.assertIsNone(self.post())

    def test_bad_files_never_raise(self):
        with open(self.settled, "w") as f:
            f.write("not json\n")
        self.assertIsNone(self.post())
        self.assertIsNone(buyer_post.maybe_post("/nonexistent/dir", self.settled, self.memory, now=NOW))

    def test_review_hook_prints_its_result_last_and_queues_the_post(self):
        deal_path = os.path.join(self.md, "deal.json")
        with open(deal_path, "w") as f:
            json.dump({"order": {"order": "o-11", "kind": "food", "use_within_hours": 5},
                       "receipt": {"price": 7000},
                       "settled": {k: v for k, v in deal(11).items() if k not in ("time", "order", "price", "kind")}}, f)
        env = dict(os.environ, BUYER_SETTLED=self.settled, BUYER_MEMORY=self.memory)
        r = subprocess.run([sys.executable, os.path.join(HERE, "review.py"), "--deal", deal_path], capture_output=True,
                           text=True, env=env, cwd=self.tmp.name, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["stars"], 5)
        self.assertEqual(self.outbox()[0]["refs"], [11])


if __name__ == "__main__":
    unittest.main()
