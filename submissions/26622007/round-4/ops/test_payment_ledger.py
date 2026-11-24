"""Tests for payment_ledger.py, offline: a fake market ledger and a fake chain. Run: python3 -I test_payment_ledger.py"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import payment_ledger as pl  # noqa: E402

SHOP = "0xAE9a93da9D38Bc3CF3a018768813ef362eea194e"
CUST = "0x0942914b2C527c23d6c59416833383cC83273FCD"
W = pl.CREDIT_WEI
TX = {n: ("0x" + (str(n) * 64))[:66] for n in range(1, 8)}


def row(seq, kind, data, ts=1795500000.0):
    return {"seq": seq, "ts": ts + seq, "kind": kind, "round": 4, "data": data, "prev": "", "hash": ""}


ROWS = [
    row(1, "join", {"name": "nocha12", "role": "shop", "url": "https://a.trycloudflare.com", "pubkey": SHOP}),
    row(2, "join", {"name": "이정화-8ae6", "role": "buyer", "url": None, "pubkey": CUST}),
    row(3, "grant", {"shop": "nocha12", "credits": 5000000, "why": "start", "tx_hash": TX[1]}),
    row(4, "grant", {"shop": "other", "credits": 5000000, "why": "start", "tx_hash": TX[7]}),
    row(5, "supply", {"shop": "nocha12", "sku": "G1", "qty": 2, "total": 20000, "units": ["u1", "u2"], "storage": 0,
                      "fees": 0, "tx_hash": TX[2]}),
    row(6, "join", {"name": "nocha12", "role": "shop", "url": "https://b.trycloudflare.com", "pubkey": SHOP}),   # tunnel restart
    row(7, "settle", {"order": "o-1", "buyer": "이정화-8ae6", "shop": "https://a.trycloudflare.com", "item": "G1", "sku": "G1",
                      "name": "의자", "amount": 13000, "shop_fee": 340, "buyer_fee": 340, "tx_hash": TX[3], "grade": "A",
                      "claim": "A", "warranty_hours": 24, "unit": "u1"}),
    row(8, "settle", {"order": "o-2", "buyer": "x", "shop": "https://someone-else.com", "amount": 9999, "shop_fee": 309,
                      "tx_hash": TX[6]}),
    row(9, "supply", {"shop": "nocha12", "sku": "G2", "qty": 1, "total": 10000, "units": ["u3"], "storage": 120,
                      "fees": 50, "tx_hash": TX[4]}),
    row(10, "settle", {"order": "o-3", "buyer": "이정화-8ae6", "shop": "https://b.trycloudflare.com/", "item": "G2",
                       "sku": "G2", "amount": 12000, "shop_fee": 330, "tx_hash": TX[5], "warranty_hours": 24, "unit": "u3"}),
    row(11, "broken", {"order": "o-1", "buyer": "이정화-8ae6", "shop": "https://b.trycloudflare.com", "unit": "u1",
                       "item": "G1", "hours_after_sale": 3.0, "warranty_hours": 24, "refunded": 0}),
]
REFUND = ("0x" + "a" * 64)
REFUND2 = ("0x" + "e" * 64)
GAS = {TX[2]: 2 * W // 10, TX[4]: W // 10, REFUND: W // 10, REFUND2: W // 10}   # supply and refund transfers burn shop gas


class FakeChain:
    def __init__(self, balance_wei, mined=True):
        self.balance, self.mined, self.calls, self.sender = balance_wei, mined, [], {}

    def balance_wei(self, address):
        return self.balance

    def transfer(self, tx):
        self.calls.append(tx)
        if not self.mined:
            return None
        if tx in (REFUND, REFUND2):
            return {"from": SHOP.lower(), "to": CUST.lower(), "value_wei": 13000 * W, "gas_wei": GAS[tx], "ok": True,
                    "ts": 1795510000}
        if tx == "0x" + "b" * 64:
            return {"from": "0xdead", "to": SHOP.lower(), "value_wei": 5 * W, "gas_wei": 0, "ok": True, "ts": 1795510000}
        if tx == TX[2]:   # the lot of 20,000 paid from the shop wallet
            return {"from": self.sender.get(tx, SHOP.lower()), "to": "0xmarket", "value_wei": 20000 * W, "gas_wei": GAS[tx], "ok": True,
                    "ts": 1795510000}
        return {"from": self.sender.get(tx, SHOP.lower()), "to": "0xmarket", "value_wei": 0, "gas_wei": GAS.get(tx, 0),
                "ok": True, "ts": 1795510000}


def kit_log(d, lines):
    (d / "logs").mkdir(exist_ok=True)
    (d / "logs" / "agent-kit.log").write_text(
        f"19:00:08 [maroo] shop wallet {SHOP}, buyer wallet 0x961678ab2305dD6B715738F91bf0b0915CD3Ab61. Fund\n"
        + "".join(x + "\n" for x in lines), encoding="utf-8")


def sent(tool, tx):
    return f"20:10:00 [maroo] {tool} sent 13 tOKRW from the shop wallet: https://explorer-testnet.maroo.io/tx/{tx}"


class Ledger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.rows = list(ROWS)
        p = mock.patch.object(pl, "market_rows", lambda market, after=0: list(self.rows))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def lines(self, name="payments.jsonl"):
        return pl.read_lines(self.d / "logs" / name)

    def test_every_money_event_of_this_shop_once_with_running_balance(self):
        kit_log(self.d, [sent("supply_buy", TX[2]), sent("refund", REFUND), sent("other", "0x" + "b" * 64)])
        new, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        kinds = [(e["kind"], e["id"]) for e in new]
        self.assertEqual(kinds, [("grant", f"grant-{TX[1]}"), ("supply", f"supply-{TX[2]}"), ("sale", "sale-o-1"),
                                 ("supply", f"supply-{TX[4]}"), ("sale", "sale-o-3"), ("broken", "broken-o-1"),
                                 ("transfer_out", f"out-{REFUND}")])
        sale = new[2]
        self.assertEqual((sale["counterparty"], sale["amount"], sale["fee"], sale["delta"], sale["tx_hash"]),
                         ("이정화-8ae6", 13000, 340, 12660, TX[3]))
        sup = new[3]
        self.assertEqual((sup["amount"], sup["storage"], sup["forum_fees"], sup["delta"], sup["gas"]),
                         (10000, 120, 50, -10170, 0.1))
        self.assertEqual(new[1]["unit_price"], 10000)
        self.assertEqual(new[6]["counterparty"], "이정화-8ae6")
        expect = (5000000 - 20000 + 12660 - 10170 + 11670 - 13000) * W - GAS[TX[2]] - GAS[TX[4]] - GAS[REFUND]
        self.assertEqual(bal, expect)
        self.assertEqual(new[6]["time"], pl.iso(1795510000))
        self.assertEqual(new[-1]["balance_wei"], expect)
        for e in new:   # every line carries the agreed fields
            for k in ("time", "id", "counterparty", "item", "qty", "unit_price", "amount", "fee", "storage", "tx_hash",
                      "balance_after", "prev", "hash"):
                self.assertIn(k, self.lines()[0] if k in ("prev", "hash") else e)

    def test_second_sync_appends_nothing_and_does_not_query_the_chain_again(self):
        kit_log(self.d, [sent("refund", REFUND)])
        pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        before = (self.d / "logs" / "payments.jsonl").read_bytes()
        c = FakeChain(0)
        new, _ = pl.sync(self.d, "m", "nocha12", c, SHOP)
        self.assertEqual(new, [])
        self.assertEqual(c.calls, [])
        self.assertEqual((self.d / "logs" / "payments.jsonl").read_bytes(), before)

    def test_new_market_rows_are_appended_after_the_old_lines(self):
        kit_log(self.d, [])
        pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        first = (self.d / "logs" / "payments.jsonl").read_bytes()
        self.rows.append(row(12, "settle", {"order": "o-4", "buyer": "y", "shop": "https://b.trycloudflare.com",
                                            "amount": 5000, "shop_fee": 260, "tx_hash": "0x" + "c" * 64}))
        new, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        self.assertEqual([e["id"] for e in new], ["sale-o-4"])
        self.assertTrue((self.d / "logs" / "payments.jsonl").read_bytes().startswith(first))
        self.assertEqual(bal, self.lines()[-2]["balance_wei"] + 4740 * W)
        self.assertEqual(pl.verify(self.d / "logs" / "payments.jsonl"), [])

    def test_unmined_kit_transfer_waits_for_a_later_sync(self):
        kit_log(self.d, [sent("refund", REFUND)])
        new, _ = pl.sync(self.d, "m", "nocha12", FakeChain(0, mined=False), SHOP)
        self.assertNotIn("transfer_out", [e["kind"] for e in new])
        new, _ = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        self.assertEqual([e["kind"] for e in new], ["supply", "supply", "transfer_out"])   # unreadable lots wait too

    def test_pending_grant_without_tx_is_not_counted(self):
        self.rows = [ROWS[0], row(3, "grant", {"shop": "nocha12", "credits": 5000000, "why": "start", "waiting": "x"})]
        kit_log(self.d, [])
        new, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        self.assertEqual((new, bal), ([], 0))

    def test_verify_finds_an_edited_or_removed_line(self):
        kit_log(self.d, [])
        pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        p = self.d / "logs" / "payments.jsonl"
        lines = p.read_text(encoding="utf-8").splitlines()
        p.write_text("\n".join([lines[0].replace("5000000", "6000000")] + lines[1:]) + "\n", encoding="utf-8")
        self.assertTrue(pl.verify(p))
        p.write_text("\n".join(lines[:1] + lines[2:]) + "\n", encoding="utf-8")
        self.assertTrue(pl.verify(p))
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertEqual(pl.verify(p), [])

    def test_reconcile_equal_and_different(self):
        kit_log(self.d, [])
        _, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        ok = pl.reconcile(self.d, "m", "nocha12", FakeChain(bal), SHOP, 1.0)
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["diff"], 0)
        self.assertEqual(ok["payments_tail"], self.lines()[-1]["hash"])
        extra = pl.reconcile(self.d, "m", "nocha12", FakeChain(bal + 9000 * W), SHOP, 1.0)
        self.assertFalse(extra["ok"])
        self.assertAlmostEqual(extra["diff"], 9000, places=3)
        self.assertIn("more than the ledger", extra["note"])
        self.assertEqual(len(self.lines("reconcile.jsonl")), 2)
        self.assertEqual(pl.verify(self.d / "logs" / "reconcile.jsonl"), [])

    def test_cli_exit_codes(self):
        kit_log(self.d, [])
        with mock.patch.object(pl, "Chain", lambda rpc: FakeChain(1)), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(pl.main(["reconcile", "--dir", str(self.d)]), 2)
            self.assertEqual(pl.main(["verify", "--dir", str(self.d)]), 0)
        self.assertIn('"ok": true', out.getvalue().splitlines()[-1])

    def test_lot_paid_before_the_market_records_it_is_not_counted_twice(self):
        self.rows = ROWS[:4]                     # the kit has paid the lot, the market has not written the supply row yet
        kit_log(self.d, [sent("supply_buy", TX[2])])
        new, bal1 = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        self.assertEqual(new[-1]["kind"], "transfer_out")
        self.assertEqual(bal1, (5000000 - 20000) * W - GAS[TX[2]])
        self.rows = ROWS[:5]
        new, bal2 = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        self.assertEqual([e["kind"] for e in new], ["supply"])
        self.assertEqual((new[0]["delta_wei"], new[0]["gas_wei"]), (0, 0))
        self.assertIn("reclassifies", new[0]["note"])
        self.assertEqual(bal2, bal1)

    def test_supply_gas_counts_only_when_the_shop_wallet_sent_it(self):
        self.rows = ROWS[:5]
        kit_log(self.d, [])
        c = FakeChain(0)
        c.sender[TX[2]] = "0xmarket"
        new, _ = pl.sync(self.d, "m", "nocha12", c, SHOP)
        self.assertEqual(new[-1]["gas_wei"], 0)

    def test_unreadable_supply_tx_is_written_by_a_later_sync(self):
        self.rows = ROWS[:5]
        kit_log(self.d, [])
        new, _ = pl.sync(self.d, "m", "nocha12", FakeChain(0, mined=False), SHOP)
        self.assertEqual([e["kind"] for e in new], ["grant"])
        new, _ = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        self.assertEqual([(e["kind"], e["gas_wei"]) for e in new], [("supply", GAS[TX[2]])])

    def test_torn_last_line_stops_sync_and_verify_reports_it(self):
        kit_log(self.d, [])
        pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        p = self.d / "logs" / "payments.jsonl"
        p.write_bytes(p.read_bytes()[:-20])
        with self.assertRaises(pl.TornTail):
            pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        self.assertTrue(pl.verify(p))
        with mock.patch.object(pl, "Chain", lambda rpc: FakeChain(0)), redirect_stdout(io.StringIO()):
            self.assertEqual(pl.main(["sync", "--dir", str(self.d)]), 2)

    def test_a_second_run_waits_out_the_lock(self):
        kit_log(self.d, [])
        with pl.Lock(self.d):
            with mock.patch.object(pl, "Chain", lambda rpc: FakeChain(0)), redirect_stdout(io.StringIO()) as out:
                self.assertEqual(pl.main(["sync", "--dir", str(self.d)]), 0)
            self.assertIn("skipped", out.getvalue())
            self.assertFalse((self.d / "logs" / "payments.jsonl").exists())

    def test_empty_shop_reconciles_to_zero(self):
        self.rows = [ROWS[0]]
        kit_log(self.d, [])
        rec = pl.reconcile(self.d, "m", "nocha12", FakeChain(0), SHOP, 1.0)
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["lines"], 0)


    def write_agent(self, name, recs):
        (self.d / "logs").mkdir(exist_ok=True)
        (self.d / "logs" / name).write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")

    def test_transfer_to_the_wallet_that_paid_our_sale_is_a_refund_of_that_order(self):
        kit_log(self.d, [sent("refund", REFUND)])
        c = FakeChain(0)
        c.sender[TX[3]] = CUST.lower()              # the customer paid order o-1 from this wallet
        new, _ = pl.sync(self.d, "m", "nocha12", c, SHOP)
        r = [e for e in new if e["tx_hash"] == REFUND][0]
        self.assertEqual((r["kind"], r["id"], r["order"], r["delta"]), ("refund", f"refund-{REFUND}", "o-1", -13000))
        self.assertEqual(pl.sync(self.d, "m", "nocha12", c, SHOP)[0], [])   # not written again

    def test_agent_send_record_finds_a_transfer_the_kit_never_logged_and_names_its_order(self):
        kit_log(self.d, [])                         # the receipt wait timed out: no "[maroo] ... sent" line
        self.write_agent("sends.jsonl", [{"purpose": "refund", "order": "o-1", "to": CUST, "amount": 13000, "nonce": 7, "ts": 1},
                                         {"purpose": "refund", "order": "o-1", "tx_hash": REFUND}])
        c = FakeChain(0)
        c.sender[TX[3]] = CUST.lower()
        new, _ = pl.sync(self.d, "m", "nocha12", c, SHOP)
        r = [e for e in new if e["tx_hash"] == REFUND][0]
        self.assertEqual((r["kind"], r["order"], r["source"]), ("refund", "o-1", "sends.jsonl:2"))
        self.assertEqual(pl.agent_sends(self.d)[1], [])  # the intent is closed by its tx line

    def test_second_refund_of_one_order_is_an_alert_once_per_count(self):
        kit_log(self.d, [sent("refund", REFUND), sent("refund", REFUND2)])
        c = FakeChain(0)
        c.sender[TX[3]] = CUST.lower()
        _, bal = pl.sync(self.d, "m", "nocha12", c, SHOP)
        c.balance = bal
        rec = pl.reconcile(self.d, "m", "nocha12", c, SHOP, 1.0)
        dup = [a for a in rec["alerts"] if a["kind"] == "duplicate_refund"]
        self.assertEqual([a["id"] for a in dup], ["dup-o-1-2"])
        self.assertIn(REFUND2, dup[0]["text"])
        self.assertEqual(pl.verify(self.d / "logs" / "alerts.jsonl"), [])

    def test_new_broken_row_and_mismatch_are_alerts_and_broken_only_once(self):
        kit_log(self.d, [])
        rec = pl.reconcile(self.d, "m", "nocha12", FakeChain(1), SHOP, 1.0)
        kinds = sorted(a["kind"] for a in rec["alerts"])
        self.assertEqual(kinds, ["broken", "reconcile_mismatch"])
        self.assertIn("order o-1", [a for a in rec["alerts"] if a["kind"] == "broken"][0]["text"])
        rec2 = pl.reconcile(self.d, "m", "nocha12", FakeChain(rec["ledger_credits"] * W), SHOP, 1.0)
        self.assertEqual(rec2["alerts"], [])
        self.assertEqual(len(self.lines("alerts.jsonl")), 2)

    def test_intent_without_tx_hash_is_an_alert_after_the_grace_time(self):
        kit_log(self.d, [])
        self.write_agent("sends.jsonl", [{"purpose": "supply", "order": "q-9", "amount": 5000, "nonce": 3, "ts": 1000}])
        rec = {"ok": True, "payments_tail": pl.GENESIS}
        self.assertEqual(pl.alerts(self.d, [], [], rec, now=1000 + 60), [])
        late = pl.alerts(self.d, [], [], rec, now=1000 + pl.INTENT_GRACE + 60)
        self.assertEqual([a["kind"] for a in late], ["intent_without_tx"])
        self.assertIn("nonce", late[0]["text"])


    def test_refund_said_by_the_agent_to_a_wallet_that_paid_nothing_is_an_alert_not_a_refund(self):
        kit_log(self.d, [])
        self.write_agent("refunds.jsonl", [{"purpose": "refund", "order": "o-1", "tx_hash": REFUND}])
        rec = pl.reconcile(self.d, "m", "nocha12", FakeChain(0), SHOP, 1.0)   # nobody paid o-1 from CUST here
        r = [e for e in self.lines() if e["tx_hash"] == REFUND][0]
        self.assertEqual(r["kind"], "transfer_out")
        self.assertIn("refund_to_unknown", [a["kind"] for a in rec["alerts"]])

    def test_payer_with_two_orders_and_no_hint_is_an_unmatched_refund_alert(self):
        kit_log(self.d, [sent("refund", REFUND)])
        c = FakeChain(0)
        c.sender[TX[3]] = c.sender[TX[5]] = CUST.lower()
        rec = pl.reconcile(self.d, "m", "nocha12", c, SHOP, 1.0)
        r = [e for e in self.lines() if e["tx_hash"] == REFUND][0]
        self.assertEqual((r["kind"], r["order"], r["orders_of_payer"]), ("refund", None, ["o-1", "o-3"]))
        self.assertIn("refund_unmatched", [a["kind"] for a in rec["alerts"]])

    def test_sealed_sale_waits_for_the_agent_record_then_counts_its_amount(self):
        self.rows = ROWS[:4] + [row(7, "settle", {"order": "o-9", "shop": "https://a.trycloudflare.com"}, ts=1795500000.0)]
        kit_log(self.d, [])
        c = FakeChain(0)
        new, bal = pl.sync(self.d, "m", "nocha12", c, SHOP)
        self.assertEqual([e["kind"] for e in new], ["grant"])
        pending = []
        pl.sync(self.d, "m", "nocha12", c, SHOP, pending)
        self.assertEqual([p["order"] for p in pending], ["o-9"])
        late = pl.alerts(self.d, [], [], {"ok": True}, now=1795500007 + pl.INTENT_GRACE + 1, pending=pending)
        self.assertEqual([a["id"] for a in late], ["sealed-o-9"])
        self.write_agent("sales.jsonl", [{"ts": 1, "order": "o-9", "amount": 8000, "shop_fee": 290, "tx_hash": TX[3],
                                          "warranty_hours": 24}])
        new, bal2 = pl.sync(self.d, "m", "nocha12", c, SHOP)
        self.assertEqual([(e["kind"], e["amount"], e["fee"], e["sealed"], e["tx_hash"]) for e in new],
                         [("sale", 8000, 290, True, TX[3])])
        self.assertEqual(bal2 - bal, 7710 * W)

    def test_mismatch_alert_keeps_its_id_while_the_gap_stays(self):
        rec = {"ok": False, "diff": 9000.0, "chain_credits": 1, "ledger_credits": 0, "note": "x", "payments_tail": "a" * 64}
        first = pl.alerts(self.d, [], [], rec, now=1795500000)
        rec["payments_tail"] = "b" * 64
        self.assertEqual(first, pl.alerts(self.d, [], [], rec, now=1795500000 + 600))

    def test_intent_with_an_iso_time_or_no_time_still_ages_into_an_alert(self):
        self.write_agent("sends.jsonl", [{"purpose": "refund", "order": "o-1", "amount": 1, "nonce": 1,
                                          "ts": "2026-11-24T20:00:00+09:00"}, {"purpose": "supply", "order": "q", "amount": 1}])
        now = pl.epoch("2026-11-24T21:00:00+09:00", 0)
        got = pl.alerts(self.d, [], [], {"ok": True}, now=now)
        self.assertEqual([a["kind"] for a in got], ["intent_without_tx"])   # the one with no time uses the file time (just now)


if __name__ == "__main__":
    unittest.main()
