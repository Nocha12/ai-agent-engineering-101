"""Money safety of a restart that does not wait for a quiet window (drain_gate phase 2 lets issued, unpaid checkouts
through). Offline: the ledger test harness (ops/test_payment_ledger.py fakes) and mocked market rows, no chain.
Scenario: we issue a checkout -> the kit is stopped -> the buyer pays and settles with the market while it is down ->
the kit restarts (joins again with a new tunnel URL) -> the ledger cron, which does not need the kit, records the sale.
Run: python3 -I ops/deploy/test_drain_money.py   (AXD_PL_OPS=<ops dir> tests another payment_ledger.py)"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
OPS = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(OPS))
import drain_gate as g  # noqa: E402
import test_payment_ledger as tpl  # noqa: E402  (FakeChain, row, kit_log, SHOP)

PL_OPS = Path(os.environ.get("AXD_PL_OPS") or OPS)
spec = importlib.util.spec_from_file_location("payment_ledger_under_test", PL_OPS / "payment_ledger.py")
pl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pl)
spec = importlib.util.spec_from_file_location("private_checkout_logic", OPS.parent / "tools" / "private_checkout" / "logic.py")
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)

W = pl.CREDIT_WEI
BUYER = "0x" + "be" * 20
DIGEST = "c0" * 32
PAY_TX = "0x" + "d1" * 32
T_ISSUE = 1795700000
PRICE = 9675
FEE = int(PRICE * 0.01) + 210                               # what the buyer's transfer leaves out (recover_sealed)
OLD_URL, NEW_URL = "https://old.trycloudflare.com", "https://new.trycloudflare.com"
ISSUED = {"event": "issued", "ts": T_ISSUE, "order": "t-9-2", "unit": "u-1", "sku": "EGG", "price": PRICE, "grade": "B",
          "warranty_hours": 0, "digest_hex": DIGEST, "holder": "t-9"}       # the row private_checkout create writes


class Chain(tpl.FakeChain):
    def __init__(self, balance, pays):
        super().__init__(balance)
        self.pays = pays

    def payments_to(self, address, t0, t1):
        return [p[:6] for p in self.pays if t0 <= p[5] <= t1]

    def transfer(self, tx):
        if tx == PAY_TX:
            return {"from": BUYER, "to": tpl.SHOP.lower(), "value_wei": (PRICE - FEE) * W, "gas_wei": 0, "ok": True,
                    "ts": T_ISSUE + 120}
        return super().transfer(tx)

    def inflows(self, address, start, max_blocks):
        return [p[:6] for p in self.pays], start + max_blocks, start + max_blocks

    def block_at(self, ts):
        return 1000


class RestartDuringOpenCheckout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        tpl.kit_log(self.d, [])
        logs = self.d / "logs"
        (logs / "checkouts.jsonl").write_text(json.dumps(ISSUED) + "\n")
        (logs / "react.jsonl").write_text("")
        (logs / "messages.jsonl").write_text(
            json.dumps({"dir": "in", "id": "m9", "task": "t-9", "ts": T_ISSUE - 20}) + "\n"
            + json.dumps({"dir": "out", "id": "m9", "task": "t-9", "ts": T_ISSUE + 2}) + "\n")   # checkout delivered
        self.rows = [tpl.row(1, "join", {"name": "nocha12", "role": "shop", "url": OLD_URL, "pubkey": tpl.SHOP}),
                     tpl.row(2, "join", {"name": "buyer-9", "role": "buyer", "url": None, "pubkey": BUYER})]
        p = mock.patch.object(pl, "market_rows", lambda market, after=0: list(self.rows))
        p.start()
        self.addCleanup(p.stop)

    def order(self, expires):
        self.rows.append({"seq": 3, "ts": T_ISSUE + 100, "kind": "order", "round": 4,
                          "data": {"order": "o-9", "buyer": "buyer-9", "expires": expires}, "prev": "", "hash": ""})

    def restart(self):                                     # the new kit joins with its new tunnel URL
        self.rows.append(tpl.row(5, "join", {"name": "nocha12", "role": "shop", "url": NEW_URL, "pubkey": tpl.SHOP}))

    def test_gate_stops_the_kit_with_this_checkout_open(self):
        now = T_ISSUE + 60                                 # issued 60 s ago, unpaid
        self.assertTrue(g.quiet(self.d, now))              # the old rule waits
        self.assertEqual(g.drained(self.d, now), [])       # phase 2 lets the stop go ahead

    def test_buy_settled_during_downtime_is_recorded_as_a_sale_after_restart(self):
        self.order(T_ISSUE + 400)
        pay = (PAY_TX, BUYER, (PRICE - FEE) * W, DIGEST, True, T_ISSUE + 120)
        # kit down: the buyer settles our sealed checkout with the market itself (the public row: order + shop only)
        self.rows.append({"seq": 4, "ts": T_ISSUE + 125, "kind": "settle", "round": 4,
                          "data": {"order": "o-9", "shop": OLD_URL}, "prev": "", "hash": ""})
        self.restart()
        self.assertEqual(pl.agent_sales(self.d), {})       # nobody recorded it: the kit was down
        new, bal = pl.sync(self.d, "m", "nocha12", Chain(0, [pay]), tpl.SHOP)
        sale = pl.agent_sales(self.d)["o-9"]
        self.assertEqual((sale["amount"], sale["shop_fee"], sale["tx_hash"], sale["checkout_order"]),
                         (PRICE, FEE, PAY_TX, "t-9-2"))
        line = [e for e in new if e["kind"] == "sale"]
        self.assertEqual([(e["id"], e["amount"], e["delta"]) for e in line], [("sale-o-9", PRICE, PRICE - FEE)])
        self.assertEqual(bal, (PRICE - FEE) * W)           # the ledger holds exactly what the wallet got
        self.assertEqual(pl.sync(self.d, "m", "nocha12", Chain(0, [pay]), tpl.SHOP)[0], [])   # once

    @unittest.skipUnless(hasattr(pl, "scan_inflows"), "this payment_ledger.py has no inflow scan (paid_unsettled)")
    def test_paid_during_downtime_but_never_settled_is_owed_and_alerted_not_lost(self):
        self.order(T_ISSUE + 400)
        pay = (PAY_TX, BUYER, (PRICE - FEE) * W, DIGEST, True, T_ISSUE + 120)
        self.restart()                                      # no settle row: the order expires
        rec = pl.reconcile(self.d, "m", "nocha12", Chain((PRICE - FEE) * W, [pay]), tpl.SHOP, 1.0, now=T_ISSUE + 3000)
        self.assertTrue(rec["ok"], rec)
        owed = [e for e in pl.read_lines(self.d / "logs" / "payments.jsonl") if e["kind"] == "paid_unsettled"]
        self.assertEqual([(e["tx_hash"], e["order"], e["checkout_order"]) for e in owed], [(PAY_TX, "o-9", "t-9-2")])
        self.assertIn("paid_unsettled", [a["kind"] for a in rec["alerts"]])
        self.assertFalse((self.d / "logs" / "sends.jsonl").exists())   # nothing sent by itself: an operator decides

    def test_the_unit_stays_held_for_other_inquiries_after_the_restart(self):
        rows = [json.loads(x) for x in (self.d / "logs" / "checkouts.jsonl").read_text().splitlines()]
        held = pc.open_hold(None, "u-1", "t-other-1", T_ISSUE + 200, holder="t-other", rows=rows)
        self.assertEqual((held or {}).get("digest_hex"), DIGEST)


if __name__ == "__main__":
    unittest.main()
