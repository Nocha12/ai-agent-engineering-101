"""acp_gate: buyers in pricing.json seller.refuse_buyers are answered "out of stock" at ACP complete; others settle.

    python -m unittest test_acp_gate -v        (from submissions/26622007/week-08)

The ledger is a fake /api/ledger; the kit's AcpShop and acp_shop.AcpCheckout run with a fake market.
"""
import asyncio
import base64
import json
import os
import sys
import tempfile
import unittest
import unittest.mock

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.applications import Starlette
from starlette.testclient import TestClient

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "scripts"))
import acp_gate                    # noqa: E402
import acp_shop                    # noqa: E402
import market_kit as kit           # noqa: E402

BAD = "totato2203-r3"


class FakeLedger:
    """/api/ledger?after=N over a list of rows, PAGE rows a call; counts calls."""

    def __init__(self, rows):
        self.rows, self.calls = rows, 0

    def __call__(self, path, timeout=10):
        self.calls += 1
        after = int(path.split("after=")[1])
        if after == -1:
            return self.rows[-1:]
        return [r for r in self.rows if r["seq"] > after][:acp_gate.PAGE]


def order_row(seq, order, buyer):
    return {"seq": seq, "kind": "order", "data": {"order": order, "buyer": buyer, "expires": 0}}


def ledger(*orders):
    rows = [order_row(i + 1, o, b) for i, (o, b) in enumerate(orders)]
    rows += [{"seq": len(rows) + 1 + i, "kind": "quote", "data": {}} for i in range(3)]
    return FakeLedger(rows)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pricing = os.path.join(self.tmp.name, "pricing.json")
        self.md = os.path.join(self.tmp.name, ".market")
        self.set_refused([BAD])
        self.ledger = ledger(("o-bad", BAD), ("o-good", "buyer-01"))
        self.logs = []
        self.gate = acp_gate.Gate(acp_gate.OrderBuyers(self.ledger), self.pricing, self.md, self.logs.append)
        self.gate.orders._read()                                   # what the background thread does first

    def tearDown(self):
        self.tmp.cleanup()

    def set_refused(self, names, key="seller"):
        with open(self.pricing, "w", encoding="utf-8") as f:
            json.dump({"tiers": [], key: {"refuse_buyers": names}} if names is not None else {"tiers": []}, f)

    def refused_lines(self):
        p = os.path.join(self.md, "measure", "refused.jsonl")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(line) for line in f]


class GateTest(Base):
    def test_refuses_the_listed_buyer_and_records_it(self):
        s = {"id": "cs_1", "task": "t-1", "unit": "u-1", "line_items": [{"id": "G1", "quantity": 1}],
             "totals": [{"type": "total", "amount": 77000}]}
        self.assertEqual(self.gate.refusal("o-bad", s), acp_gate.OUT_OF_STOCK)
        [rec] = self.refused_lines()
        self.assertEqual((rec["buyer"], rec["order"], rec["item"], rec["price"], rec["task"]),
                         (BAD, "o-bad", "G1", 77000, "t-1"))
        self.assertIn("ts", rec)
        self.assertTrue(any("REFUSED buyer totato2203-r3 order o-bad" in line for line in self.logs))

    def test_other_buyers_pass(self):
        self.assertIsNone(self.gate.refusal("o-good", {}))
        self.assertIsNone(self.gate.refusal("o-unknown", {}))      # not in the ledger: settle as before
        self.assertEqual(self.refused_lines(), [])

    def test_off_when_the_list_is_empty_or_missing(self):
        self.ledger.calls = 0
        for names in ([], None):
            self.set_refused(names)
            self.assertIsNone(self.gate.refusal("o-bad", {}))
        self.assertEqual(self.ledger.calls, 0)                      # off: the ledger is not even read
        self.assertEqual(acp_gate.refused_buyers(os.path.join(self.tmp.name, "nope.json")), [])

    def test_ledger_failure_never_blocks_a_sale(self):
        def down(path, timeout=10):
            raise OSError("market down")
        ob = acp_gate.OrderBuyers(down)
        ob.seq = 5                                                 # warm before, the market down now
        gate = acp_gate.Gate(ob, self.pricing, self.md, self.logs.append)
        self.assertIsNone(gate.refusal("o-bad", {}))
        self.assertTrue(any("ledger lookup for o-bad failed" in line for line in self.logs))

    def test_reads_forward_and_pages(self):
        rows = [order_row(i + 1, f"o-{i}", "buyer-01") for i in range(1200)] + [order_row(1201, "o-late", BAD)]
        led = FakeLedger(rows)
        ob = acp_gate.OrderBuyers(led)
        self.assertIsNone(ob.buyer("o-late"))                      # cold: a payment never does the first read
        self.assertEqual(led.calls, 0)
        ob._read()                                                 # the background's first pass
        self.assertEqual(ob.buyer("o-late"), BAD)
        self.assertEqual(ob.buyer("o-5"), "buyer-01")              # cached: no new read
        calls = led.calls
        led.rows.append(order_row(1202, "o-new", BAD))
        self.assertEqual(ob.buyer("o-new"), BAD)
        self.assertEqual(led.calls, calls + 1)                     # a payment reads one page from the last row seen
        self.assertIsNone(ob.buyer("o-never"))
        self.assertEqual(led.calls, calls + 2)

    def test_a_payment_waits_at_most_wait_for_a_running_read(self):
        ob = acp_gate.OrderBuyers(FakeLedger([order_row(1, "o-1", BAD)]))
        ob._read()
        ob.lock.acquire()                                          # a slow background page
        try:
            with unittest.mock.patch.object(acp_gate, "WAIT", 0.05):
                self.assertIsNone(ob.buyer("o-2"))                 # gave up waiting: settle as usual
        finally:
            ob.lock.release()

    def test_cold_start_reads_back_a_bounded_window(self):
        rows = [order_row(i + 1, f"o-{i}", "buyer-01") for i in range(acp_gate.BACK + 600)]
        ob = acp_gate.OrderBuyers(FakeLedger(rows))
        ob._read()
        self.assertIsNone(ob.buyer("o-0"))                         # older than BACK rows: not read
        self.assertEqual(ob.buyer(f"o-{acp_gate.BACK + 599}"), "buyer-01")


def receipt(key, item="G1", price=77000, task="t-1", shop="Nocha Furniture"):
    r = {"shop": shop, "item": item, "price": price, "task": task}
    r["sig_seller"] = base64.b64encode(key.sign(acp_shop.canon({k: r[k] for k in acp_shop.RECEIPT_KEYS}))).decode()
    return r


def pay_body(order, amount=77000):
    return {"payment_data": {"order": order, "checkout_mandate": {"mandate": {}, "sig": "c"},
                             "payment_mandate": {"mandate": {"amount": amount}, "sig": "p"}}}


class KitMarket:
    """The kit's Market for AcpShop: Round 2 inventory, and /api/settle answered and counted."""

    def __init__(self):
        self.settles = []

    def call(self, method, path, role="buyer", **kw):
        if path == "/api/settle":
            self.settles.append(kw.get("json"))
            return httpx.Response(200, json={"ledger": 9, "mandate_receipt": {"ledger": 9}})
        return httpx.Response(200, json={"round": 2, "units": []})


class KitAcpShopTest(Base):
    """The production path: the tunneled shop's ACP is the kit's AcpShop with the gate load_gate gives it."""

    def setUp(self):
        super().setUp()
        self.key = Ed25519PrivateKey.generate()
        self.market = KitMarket()
        self.shop = kit.AcpShop(self.key, self.market)
        self.shop.gate = lambda order, s: self.gate.refusal(order, s)

    def run_path(self, path, body):
        class Req:
            method = "POST"
        return asyncio.run(self.shop.handle(Req(), path, json.dumps(body).encode(), "https://x.example"))

    def open_session(self, task):
        r = self.run_path("acp/checkout_sessions", {"line_items": [{"id": "G1", "quantity": 1}],
                                                     "receipt": receipt(self.key, task=task)})
        self.assertEqual(r.status_code, 201)
        return json.loads(r.body)["id"]

    def test_refused_buyer_gets_out_of_stock_and_nothing_settles(self):
        sid = self.open_session("t-bad")
        r = self.run_path(f"acp/checkout_sessions/{sid}/complete", pay_body("o-bad"))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(json.loads(r.body), {"error": acp_gate.OUT_OF_STOCK})
        self.assertEqual(self.market.settles, [])
        self.assertEqual(self.shop.sessions[sid]["status"], "canceled")
        self.assertEqual(len(self.refused_lines()), 1)

    def test_other_buyer_settles(self):
        sid = self.open_session("t-good")
        r = self.run_path(f"acp/checkout_sessions/{sid}/complete", pay_body("o-good"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([s["order"] for s in self.market.settles], ["o-good"])
        self.assertEqual(self.refused_lines(), [])

    def test_a_broken_gate_never_blocks_a_sale(self):
        def broken(order, s):
            raise RuntimeError("boom")
        self.shop.gate = broken
        sid = self.open_session("t-x")
        self.assertEqual(self.run_path(f"acp/checkout_sessions/{sid}/complete", pay_body("o-bad")).status_code, 200)

    def test_load_gate_finds_this_folder(self):
        with unittest.mock.patch.object(acp_gate.OrderBuyers, "warm"):    # no real ledger thread in tests
            g = kit.load_gate(HERE)
        self.assertTrue(callable(g))
        self.assertIsNone(kit.load_gate(self.tmp.name))           # a folder without acp_gate.py: no gate


class AcpCheckoutTest(Base):
    """The Render path: acp_shop.AcpCheckout with the gate seller.build passes."""

    def setUp(self):
        super().setUp()
        self.key = Ed25519PrivateKey.generate()
        self.settles = []

        def market(request):
            self.settles.append(json.loads(request.content))
            return httpx.Response(200, json={"ledger": 9, "mandate_receipt": {"ledger": 9}})

        self.acp = acp_shop.AcpCheckout("https://x.example", lambda: self.key, self.logs.append,
                                        transport=httpx.MockTransport(market), gate=self.gate)
        self.client = TestClient(Starlette(routes=self.acp.routes()))

    def session(self, task):
        rec = receipt(self.key, task=task)
        return self.client.post("/acp/checkout_sessions", json={"line_items": [{"id": "G1", "quantity": 1}],
                                                                "receipt": rec}).json()["id"]

    def test_refused_and_passed(self):
        bad = self.session("t-bad")
        r = self.client.post(f"/acp/checkout_sessions/{bad}/complete", json=pay_body("o-bad"))
        self.assertEqual((r.status_code, r.json()), (409, {"error": acp_gate.OUT_OF_STOCK}))
        self.assertEqual(self.client.get(f"/acp/checkout_sessions/{bad}").json()["status"], "canceled")
        good = self.session("t-good")
        self.assertEqual(self.client.post(f"/acp/checkout_sessions/{good}/complete",
                                          json=pay_body("o-good")).status_code, 200)
        self.assertEqual([s["order"] for s in self.settles], ["o-good"])


class ConfigTest(unittest.TestCase):
    def test_shipped_pricing_refuses_only_totato(self):
        self.assertEqual(acp_gate.refused_buyers(os.path.join(HERE, "pricing.json")), [BAD])


if __name__ == "__main__":
    unittest.main()
