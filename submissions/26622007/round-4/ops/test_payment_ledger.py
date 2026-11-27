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
        self.assertEqual([e["kind"] for e in new], ["supply", "sale", "supply", "sale", "transfer_out"])   # unreadable lots and sales wait too

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
        self.assertEqual(kinds, ["broken", "money_out", "money_out", "reconcile_mismatch"])   # the two lots are money out
        self.assertIn("order o-1", [a for a in rec["alerts"] if a["kind"] == "broken"][0]["text"])
        rec2 = pl.reconcile(self.d, "m", "nocha12", FakeChain(rec["ledger_credits"] * W), SHOP, 1.0)
        self.assertEqual(rec2["alerts"], [])
        self.assertEqual(len(self.lines("alerts.jsonl")), 4)

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
        rec = {"ok": False, "diff": 9000.4, "chain_credits": 1, "ledger_credits": 0, "note": "x", "payments_tail": "a" * 64}
        first = pl.alerts(self.d, [], [], rec, now=1795500000)
        rec["payments_tail"] = "b" * 64
        self.assertEqual(first, pl.alerts(self.d, [], [], rec, now=1795500000 + 600))

    def test_intent_with_an_iso_time_or_no_time_still_ages_into_an_alert(self):
        self.write_agent("sends.jsonl", [{"purpose": "refund", "order": "o-1", "amount": 1, "nonce": 1,
                                          "ts": "2026-11-24T20:00:00+09:00"}, {"purpose": "supply", "order": "q", "amount": 1}])
        now = pl.epoch("2026-11-24T21:00:00+09:00", 0)
        got = pl.alerts(self.d, [], [], {"ok": True}, now=now)
        self.assertEqual([a["kind"] for a in got], ["intent_without_tx"])   # the one with no time uses the file time (just now)


    def test_sale_whose_payment_is_not_readable_yet_waits_so_its_payer_is_known(self):
        self.rows = ROWS[:3] + [ROWS[6]]
        kit_log(self.d, [])
        new, _ = pl.sync(self.d, "m", "nocha12", FakeChain(0, mined=False), SHOP)
        self.assertNotIn("sale", [e["kind"] for e in new])
        c = FakeChain(0)
        c.sender[TX[3]] = CUST.lower()
        new, _ = pl.sync(self.d, "m", "nocha12", c, SHOP)
        self.assertEqual([(e["kind"], e["payer"]) for e in new], [("sale", CUST.lower())])
        pending = []
        pl.sync(self.d, "m", "nocha12", FakeChain(0, mined=False), SHOP, pending)   # nothing new waits now
        self.assertEqual(pending, [])

    def test_sale_that_stays_unreadable_is_an_alert_after_the_grace_time(self):
        self.rows = ROWS[:3] + [ROWS[6]]
        kit_log(self.d, [])
        pending = []
        pl.sync(self.d, "m", "nocha12", FakeChain(0, mined=False), SHOP, pending)
        got = pl.alerts(self.d, [], [], {"ok": True}, now=ROWS[6]["ts"] + pl.INTENT_GRACE + 1, pending=pending)
        self.assertEqual([a["id"] for a in got], ["unreadable-o-1"])

    def test_money_out_and_refund_over_the_sale_are_alerts(self):
        kit_log(self.d, [sent("supply_buy", TX[2])])
        self.rows = ROWS[:4]                     # paid, the market has not written the lot: a plain transfer out
        rec = pl.reconcile(self.d, "m", "nocha12", FakeChain(0), SHOP, 1e12)
        self.assertEqual([a["kind"] for a in rec["alerts"]], ["money_out"])
        self.rows = ROWS[:5]                     # the market writes the lot: reclassified, not money out again
        rec = pl.reconcile(self.d, "m", "nocha12", FakeChain(0), SHOP, 1e12)
        self.assertEqual(rec["alerts"], [])
        self.assertNotIn("이정화", json.dumps(self.lines("alerts.jsonl"), ensure_ascii=False))
        lines = [{"kind": "sale", "id": "sale-o-1", "amount": 10000}]
        new = [{"kind": "refund", "id": "refund-x", "order": "o-1", "amount": 13000, "tx_hash": "0xr", "delta": -13000}]
        got = pl.alerts(self.d, lines, new, {"ok": True})
        self.assertEqual([a["kind"] for a in got], ["refund_over_amount"])

    def test_refund_line_with_checkout_and_market_order_names_the_market_order(self):
        """11/28 9,290·2,632: one checkout, two market orders from one wallet. refund_send writes order = checkout id and
        market_order = the unpaid market order; the refund belongs to that market order (no unmatched/disagree alert)."""
        kit_log(self.d, [])
        self.write_agent("refunds.jsonl", [{"purpose": "refund", "order": "chk-a", "market_order": "o-3", "to": CUST,
                                            "amount": 12000, "nonce": 9, "ts": 1},
                                           {"purpose": "refund", "order": "chk-a", "market_order": "o-3", "tx_hash": REFUND}])
        c = FakeChain(0)
        c.sender[TX[3]] = c.sender[TX[5]] = CUST.lower()      # CUST paid o-1 and o-3
        rec = pl.reconcile(self.d, "m", "nocha12", c, SHOP, 1e12)
        r = [e for e in self.lines() if e["tx_hash"] == REFUND][0]
        self.assertEqual((r["kind"], r["order"], r["said_order"]), ("refund", "o-3", None))
        kinds = [a["kind"] for a in rec["alerts"]]
        self.assertNotIn("refund_unmatched", kinds)
        self.assertNotIn("refund_order_disagrees", kinds)
        self.assertEqual(pl.agent_sends(self.d)[1], [])   # the intent is closed by its tx line

    def test_second_refund_named_by_market_order_is_a_duplicate_alert(self):
        kit_log(self.d, [])
        self.write_agent("refunds.jsonl", [{"purpose": "refund", "order": "chk-a", "market_order": "o-3", "tx_hash": REFUND},
                                           {"purpose": "refund", "order": "chk-a", "market_order": "o-3", "tx_hash": REFUND2}])
        c = FakeChain(0)
        c.sender[TX[3]] = c.sender[TX[5]] = CUST.lower()
        rec = pl.reconcile(self.d, "m", "nocha12", c, SHOP, 1e12)
        self.assertIn("dup-o-3-2", [a["id"] for a in rec["alerts"]])

    def test_market_order_the_payer_did_not_pay_is_still_a_disagree_alert(self):
        kit_log(self.d, [])
        self.write_agent("refunds.jsonl", [{"purpose": "refund", "order": "chk-a", "market_order": "o-9", "tx_hash": REFUND}])
        c = FakeChain(0)
        c.sender[TX[3]] = CUST.lower()           # CUST paid o-1 only
        rec = pl.reconcile(self.d, "m", "nocha12", c, SHOP, 1e12)
        r = [e for e in self.lines() if e["tx_hash"] == REFUND][0]
        self.assertEqual((r["order"], r["said_order"]), ("o-1", "o-9"))
        self.assertIn("refund_order_disagrees", [a["kind"] for a in rec["alerts"]])

    def test_refund_line_with_only_market_order_names_it(self):
        kit_log(self.d, [])
        self.write_agent("refunds.jsonl", [{"purpose": "refund", "market_order": "o-3", "tx_hash": REFUND}])
        c = FakeChain(0)
        c.sender[TX[3]] = c.sender[TX[5]] = CUST.lower()
        rec = pl.reconcile(self.d, "m", "nocha12", c, SHOP, 1e12)
        r = [e for e in self.lines() if e["tx_hash"] == REFUND][0]
        self.assertEqual((r["order"], r["said_order"]), ("o-3", None))
        self.assertNotIn("refund_unmatched", [a["kind"] for a in rec["alerts"]])

    def test_market_order_not_paid_but_order_paid_is_a_disagree_alert(self):
        """Review M1: the stated market order is not dropped silently."""
        kit_log(self.d, [])
        self.write_agent("refunds.jsonl", [{"purpose": "refund", "order": "o-1", "market_order": "o-9", "tx_hash": REFUND}])
        c = FakeChain(0)
        c.sender[TX[3]] = c.sender[TX[5]] = CUST.lower()
        rec = pl.reconcile(self.d, "m", "nocha12", c, SHOP, 1e12)
        r = [e for e in self.lines() if e["tx_hash"] == REFUND][0]
        self.assertEqual((r["order"], r["said_order"]), ("o-1", "o-9"))
        self.assertIn("refund_order_disagrees", [a["kind"] for a in rec["alerts"]])

    def test_agent_order_that_the_payer_did_not_pay_is_an_alert(self):
        kit_log(self.d, [])
        self.write_agent("refunds.jsonl", [{"purpose": "refund", "order": "o-3", "tx_hash": REFUND}])
        c = FakeChain(0)
        c.sender[TX[3]] = CUST.lower()           # CUST paid o-1 only
        rec = pl.reconcile(self.d, "m", "nocha12", c, SHOP, 1e12)
        r = [e for e in self.lines() if e["tx_hash"] == REFUND][0]
        self.assertEqual((r["order"], r["said_order"]), ("o-1", "o-3"))
        self.assertIn("refund_order_disagrees", [a["kind"] for a in rec["alerts"]])


    def test_corrected_sale_is_read_with_its_corrected_payer_and_amount(self):
        """rca1126: a sale_correction line moves a sale to another customer's payment. A later transfer to the old
        payer is not that order's refund, and the refund limit is the corrected amount."""
        kit_log(self.d, [])
        c = FakeChain(0)
        c.sender[TX[3]] = CUST.lower()
        pl.sync(self.d, "m", "nocha12", c, SHOP)
        last = self.lines()[-1]
        fix = {"ts": 1795510000, "time": pl.iso(1795510000), "round": 4, "source": "rca1126 correction",
               "kind": "sale_correction", "id": "fix-o-1-rca1126", "order": "o-1", "counterparty": "x", "item": None,
               "qty": None, "unit_price": None, "amount": 9000, "fee": 300, "storage": 0, "forum_fees": 0,
               "delta_wei": -3960 * W, "gas_wei": 0, "corrected_tx": "0x" + "c" * 64, "payer": "0xother",
               "delta": -3960.0, "gas": 0.0, "balance_wei": last["balance_wei"] - 3960 * W,
               "balance_after": (last["balance_wei"] - 3960 * W) / W}
        pl.append(self.d / "logs" / "payments.jsonl", [fix])
        kit_log(self.d, [sent("refund", REFUND)])
        new, _ = pl.sync(self.d, "m", "nocha12", c, SHOP)
        out = [e for e in new if e["tx_hash"] == REFUND][0]
        self.assertEqual((out["kind"], out["order"]), ("transfer_out", None))   # CUST paid no sale once o-1 was corrected
        lines = [{"kind": "sale", "id": "sale-o-1", "amount": 13000}, {"kind": "sale_correction", "order": "o-1", "amount": 9000}]
        got = pl.alerts(self.d, lines, [{"kind": "refund", "id": "refund-x", "order": "o-1", "amount": 10000,
                                         "tx_hash": "0xr", "delta": -10000}], {"ok": True})
        self.assertEqual([a["kind"] for a in got], ["refund_over_amount"])


class SealedRecovery(unittest.TestCase):
    def test_buyer_settled_sealed_checkout_gets_a_sale_line(self):
        import tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory() as tmp:
            d = P(tmp); (d / "logs").mkdir()
            (d / "logs" / "checkouts.jsonl").write_text(json.dumps({"event": "issued", "order": "o-ours", "unit": "u1", "sku": "EGG",
                "price": 9675, "grade": "B", "warranty_hours": 0, "digest_hex": "ab" * 32}) + "\n")
            class C:
                def payments_to(self, address, t0, t1):
                    assert t0 <= 1000 - 30 and t1 >= 1006
                    return [("0x" + "11" * 32, "0xbuyer", 9369 * pl.CREDIT_WEI, "cd" * 32, True),   # someone else's data
                            ("0x" + "22" * 32, "0xbuyer", 9369 * pl.CREDIT_WEI, "ab" * 32, True)]
            rows = [{"kind": "join", "ts": 1, "data": {"name": "b-1", "pubkey": "0xBuyer"}},
                    {"kind": "order", "ts": 1000, "data": {"order": "o-m", "buyer": "b-1"}},
                    {"kind": "order", "ts": 1000, "data": {"order": "o-n", "buyer": "b-1"}}]
            got = pl.recover_sealed(d, rows, C(), "0xme", [{"order": "o-m", "ts": 1006}])
            self.assertEqual(got, ["o-m"])
            s = pl.agent_sales(d)["o-m"]
            self.assertEqual((s["amount"], s["shop_fee"], s["tx_hash"], s["grade"]), (9675, 306, "0x" + "22" * 32, "B"))
            self.assertEqual(pl.recover_sealed(d, rows, C(), "0xme", [{"order": "o-m", "ts": 1006}]), [])   # once
            (d / "logs" / ".sealed-tried.json").unlink()
            with open(d / "logs" / "checkouts.jsonl", "a") as f:
                f.write(json.dumps({"event": "unsettled", "tx_hash": "0x" + "33" * 32}) + "\n")
            class C2:
                def payments_to(self, address, t0, t1):
                    return [("0x" + "33" * 32, "0xbuyer", 9369 * pl.CREDIT_WEI, "ab" * 32, True)]   # the buyer's own payment, owed back
            self.assertEqual(pl.recover_sealed(d, rows, C2(), "0xme", [{"order": "o-n", "ts": 1006}]), [])   # owed back, not a sale

    def test_back_to_back_sealed_orders_each_get_their_own_buyers_payment(self):
        """11/26 20:05: o-f456 (paid 3163 at 1114), o-7878 (2780 at 1132), o-25a6 (6750 at 1159), payments listed newest
        first. Matching on the digest alone gave o-f456 the 2780 payment, o-7878 the 6750 one, and left o-25a6 with
        nothing (wallet 2922 over the ledger)."""
        import tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory() as tmp:
            d = P(tmp); (d / "logs").mkdir()
            ck = [("5137", 3163, "a1"), ("63e6", 2780, "b2"), ("710d", 6750, "c3")]
            (d / "logs" / "checkouts.jsonl").write_text("".join(json.dumps({"event": "issued", "order": o, "unit": "u-" + o,
                "sku": "S" + o, "price": pr, "grade": "B", "warranty_hours": 0, "digest_hex": h * 32}) + "\n" for o, pr, h in ck))
            net = lambda pr: (pr - (int(pr * 0.01) + 210)) * pl.CREDIT_WEI
            pays = [("0xc3", "0x9ee0", net(6750), "c3" * 32, True, 1159),
                    ("0xb2", "0xda39", net(2780), "b2" * 32, True, 1132),
                    ("0xa1", "0x2d0e", net(3163), "a1" * 32, True, 1114)]
            class C:
                def payments_to(self, address, t0, t1):
                    return [x for x in pays if t0 <= x[5] <= t1]
            rows = [{"kind": "join", "ts": 1, "data": {"name": n, "pubkey": k}} for n, k in
                    (("bae", "0x2D0E"), ("yoon", "0xda39"), ("eom", "0x9ee0"))]
            rows += [{"kind": "order", "ts": 1114.08, "data": {"order": "o-f456", "buyer": "bae"}},
                     {"kind": "order", "ts": 1132.2, "data": {"order": "o-7878", "buyer": "yoon"}},
                     {"kind": "order", "ts": 1159.3, "data": {"order": "o-25a6", "buyer": "eom"}}]
            pending = [{"order": "o-f456", "ts": 1118.7}, {"order": "o-7878", "ts": 1136.8}, {"order": "o-25a6", "ts": 1163.9}]
            self.assertEqual(pl.recover_sealed(d, rows, C(), "0xme", pending), ["o-f456", "o-7878", "o-25a6"])
            s = pl.agent_sales(d)
            self.assertEqual({o: (s[o]["amount"], s[o]["tx_hash"], s[o]["checkout_order"]) for o in s},
                             {"o-f456": (3163, "0xa1", "5137"), "o-7878": (2780, "0xb2", "63e6"), "o-25a6": (6750, "0xc3", "710d")})

    def test_same_buyer_twice_in_the_window_takes_the_payment_nearest_its_order(self):
        import tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory() as tmp:
            d = P(tmp); (d / "logs").mkdir()
            (d / "logs" / "checkouts.jsonl").write_text("".join(json.dumps({"event": "issued", "order": o, "price": 1000,
                "digest_hex": h * 32}) + "\n" for o, h in (("k1", "a1"), ("k2", "b2"))))
            w = (1000 - 220) * pl.CREDIT_WEI
            class C:
                def payments_to(self, address, t0, t1):
                    return [x for x in [("0xlate", "0xb", w, "b2" * 32, True, 1046), ("0xearly", "0xb", w, "a1" * 32, True, 1000)]
                            if t0 <= x[5] <= t1]
            rows = [{"kind": "join", "ts": 1, "data": {"name": "b", "pubkey": "0xb"}},
                    {"kind": "order", "ts": 1000, "data": {"order": "o-1", "buyer": "b"}},
                    {"kind": "order", "ts": 1046, "data": {"order": "o-2", "buyer": "b"}}]
            pl.recover_sealed(d, rows, C(), "0xme", [{"order": "o-1", "ts": 1004}, {"order": "o-2", "ts": 1050}])
            s = pl.agent_sales(d)
            self.assertEqual((s["o-1"]["tx_hash"], s["o-2"]["tx_hash"]), ("0xearly", "0xlate"))

    def test_payment_from_another_wallet_or_unknown_buyer_is_not_recorded(self):
        import tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory() as tmp:
            d = P(tmp); (d / "logs").mkdir()
            (d / "logs" / "checkouts.jsonl").write_text(json.dumps({"event": "issued", "order": "k", "price": 1000,
                                                                     "digest_hex": "a1" * 32}) + "\n")
            class C:
                def payments_to(self, address, t0, t1):
                    return [("0xt", "0xother", (1000 - 220) * pl.CREDIT_WEI, "a1" * 32, True)]   # old 5-tuple shape
            rows = [{"kind": "join", "ts": 1, "data": {"name": "b", "pubkey": "0xb"}},
                    {"kind": "order", "ts": 1000, "data": {"order": "o-1", "buyer": "b"}},
                    {"kind": "order", "ts": 1000, "data": {"order": "o-2", "buyer": "nobody"}}]
            self.assertEqual(pl.recover_sealed(d, rows, C(), "0xme", [{"order": "o-1", "ts": 1004}, {"order": "o-2", "ts": 1004}]), [])
            self.assertEqual(pl.agent_sales(d), {})


    def test_order_settled_at_another_shop_does_not_block_ours(self):
        import tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory() as tmp:
            d = P(tmp); (d / "logs").mkdir()
            (d / "logs" / "checkouts.jsonl").write_text(json.dumps({"event": "issued", "order": "k", "price": 1000,
                                                                     "digest_hex": "a1" * 32}) + "\n")
            class C:
                def payments_to(self, address, t0, t1):
                    return [("0xt", "0xb", (1000 - 220) * pl.CREDIT_WEI, "a1" * 32, True, 1003)]
            rows = [{"kind": "join", "ts": 1, "data": {"name": "b", "pubkey": "0xb"}},
                    {"kind": "order", "ts": 1000, "data": {"order": "o-1", "buyer": "b"}},
                    {"kind": "order", "ts": 1003, "data": {"order": "o-x", "buyer": "b"}},
                    {"kind": "settle", "ts": 1005, "data": {"order": "o-x", "shop": "https://other"}},
                    {"kind": "settle", "ts": 1006, "data": {"order": "o-1", "shop": "https://ours"}}]
            self.assertEqual(pl.recover_sealed(d, rows, C(), "0xme", [{"order": "o-1", "ts": 1006}]), ["o-1"])

    def test_unknown_buyer_key_does_not_use_up_the_tries(self):
        """Review 11/26 I2: with no join key the order was counted as tried; after six rounds a late join row could
        no longer recover it."""
        import tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory() as tmp:
            d = P(tmp); (d / "logs").mkdir()
            (d / "logs" / "checkouts.jsonl").write_text(json.dumps({"event": "issued", "order": "k", "price": 1000,
                                                                     "digest_hex": "a1" * 32}) + "\n")
            class C:
                def payments_to(self, address, t0, t1):
                    return [("0xt", "0xb", (1000 - 220) * pl.CREDIT_WEI, "a1" * 32, True, 1001)]
            order = [{"kind": "order", "ts": 1000, "data": {"order": "o-1", "buyer": "b"}}]
            for _ in range(8):
                self.assertEqual(pl.recover_sealed(d, order, C(), "0xme", [{"order": "o-1", "ts": 1004}]), [])
            tried = json.loads((d / "logs" / ".sealed-tried.json").read_text())
            self.assertNotIn("o-1", tried)
            rows = [{"kind": "join", "ts": 1, "data": {"name": "b", "pubkey": "0xb"}}] + order
            self.assertEqual(pl.recover_sealed(d, rows, C(), "0xme", [{"order": "o-1", "ts": 1004}]), ["o-1"])


class InflowGap(unittest.TestCase):
    """balance-diff-rca-1127: money in with no ledger line."""
    setUp, lines = Ledger.setUp, Ledger.lines
    # --- 11/27 11:19 김상용-d1c2: paid our checkout (13,470 - 344 = 13,126) but the market fee transfer never went, so the
    # buyer's kit never called /api/settle; the wallet held 13,126 more than the ledger with no tx named (balance-diff-rca-1127)

    def _inflow_setup(self, pays, head=2000, orders=None, settles=()):
        d = self.d
        (d / "logs").mkdir(exist_ok=True)
        kit_log(d, [])
        (d / "logs" / "checkouts.jsonl").write_text(json.dumps({"event": "issued", "order": "7c30", "unit": "u-k", "sku": "KIM",
            "price": 13470, "grade": "B", "warranty_hours": 0, "digest_hex": "6c" * 32, "ts": 1795509000}) + "\n")
        self.rows = list(ROWS) + [row(20, "join", {"name": "kim-d1c2", "role": "buyer", "url": None, "pubkey": "0xKIM"})]
        for o, ts, exp in (orders if orders is not None else [("o-kim", 1795509500, 1795510100)]):
            self.rows.append({"seq": 21, "ts": ts, "kind": "order", "round": 4,
                              "data": {"order": o, "buyer": "kim-d1c2", "expires": exp}, "prev": "", "hash": ""})
        for o in settles:
            self.rows.append({"seq": 22, "ts": 1795509560, "kind": "settle", "round": 4,
                              "data": {"order": o, "shop": "https://b.trycloudflare.com", "sealed": "x"}, "prev": "", "hash": ""})
        test = self

        class C(FakeChain):
            scans = []

            def inflows(self, address, start, max_blocks):
                C.scans.append((start, max_blocks))
                got = [p for p in pays if start <= p[6] < start + max_blocks]
                return [p[:6] for p in got], min(head + 1, start + max_blocks), head

            def block_at(self, ts):
                return 1000
        return C

    def test_customer_payment_the_market_never_settled_is_written_as_owed_and_the_balance_matches(self):
        pay = ("0x" + "f2" * 32, "0xkim", 13126 * W, "6c" * 32, True, 1795509501, 1500)
        C = self._inflow_setup([pay])
        _, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        rec = pl.reconcile(self.d, "m", "nocha12", C(bal + 13126 * W), SHOP, 1.0, now=1795512000)
        self.assertTrue(rec["ok"], rec)
        self.assertEqual(rec["diff"], 0)
        e = self.lines()[-1]
        self.assertEqual((e["kind"], e["order"], e["counterparty"], e["amount"], e["checkout_order"], e["tx_hash"]),
                         ("paid_unsettled", "o-kim", "kim-d1c2", 13126, "7c30", "0x" + "f2" * 32))
        kinds = {a["kind"]: a for a in rec["alerts"]}
        self.assertIn("paid_unsettled", kinds)
        self.assertIn("o-kim", kinds["paid_unsettled"]["text"])
        self.assertIn("0x" + "f2" * 32, kinds["paid_unsettled"]["text"])
        self.assertNotIn("reconcile_mismatch", kinds)
        self.assertEqual(pl.verify(self.d / "logs" / "payments.jsonl"), [])
        again = pl.reconcile(self.d, "m", "nocha12", C(bal + 13126 * W), SHOP, 1.0, now=1795512600)
        self.assertTrue(again["ok"])
        self.assertEqual(again["new_lines"], 0)
        self.assertEqual([a for a in again["alerts"] if a["kind"] == "paid_unsettled"], [])

    def test_no_scan_while_the_balance_matches(self):
        C = self._inflow_setup([])
        _, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        C.scans = []
        self.assertTrue(pl.reconcile(self.d, "m", "nocha12", C(bal), SHOP, 1.0, now=1795512000)["ok"])
        self.assertEqual(C.scans, [])

    def test_payment_whose_order_is_still_open_or_settled_waits_and_is_not_written(self):
        pay = ("0x" + "f2" * 32, "0xkim", 13126 * W, "6c" * 32, True, 1795509501, 1500)
        C = self._inflow_setup([pay], orders=[("o-kim", 1795509500, 1795510100)])
        _, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        rec = pl.reconcile(self.d, "m", "nocha12", C(bal + 13126 * W), SHOP, 1.0, now=1795509700)   # order still open
        self.assertFalse(rec["ok"])
        self.assertNotIn("paid_unsettled", [e["kind"] for e in self.lines()])
        self.assertIn("0x" + "f2" * 32, rec["note"])                       # the gap names the waiting payment
        rec = pl.reconcile(self.d, "m", "nocha12", C(bal + 13126 * W), SHOP, 1.0, now=1795512000)   # kept, then written
        self.assertTrue(rec["ok"], rec)

    def test_payment_of_a_buyer_with_an_unrecorded_sealed_sale_waits(self):
        pay = ("0x" + "f2" * 32, "0xkim", 13126 * W, "6c" * 32, True, 1795509501, 1500)
        C = self._inflow_setup([pay], settles=["o-kim"])
        _, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        rec = pl.reconcile(self.d, "m", "nocha12", C(bal + 13126 * W), SHOP, 1.0, now=1795512000)
        self.assertNotIn("paid_unsettled", [e["kind"] for e in self.lines()])
        self.assertFalse(rec["ok"])

    def test_unrecorded_sealed_sale_wait_ends_after_sealed_wait_and_the_payment_is_owed(self):
        # review2 I-A: the wait on an unrecorded sealed sale is bounded; after SEALED_WAIT the payment is written as owed
        pay = ("0x" + "f2" * 32, "0xkim", 13126 * W, "6c" * 32, True, 1795509530, 1500)
        C = self._inflow_setup([pay], orders=[("o-kim", 1795509500, 1795510100), ("o-other", 1795509520, 1795510120)],
                               settles=["o-other"])
        _, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        rec = pl.reconcile(self.d, "m", "nocha12", C(bal + 13126 * W), SHOP, 1.0, now=1795509530 + 1800)
        self.assertFalse(rec["ok"])                                         # inside the wait: still waiting
        rec = pl.reconcile(self.d, "m", "nocha12", C(bal + 13126 * W), SHOP, 1.0, now=1795509530 + pl.SEALED_WAIT + 60)
        self.assertTrue(rec["ok"], rec)
        e = self.lines()[-1]
        self.assertEqual((e["kind"], e["tx_hash"]), ("paid_unsettled", "0x" + "f2" * 32))
        self.assertIn("o-other", e["note"])                                 # names the sealed sale it waited on
        self.assertEqual(e["held_order"], "o-other")
        alert = [x for x in rec["alerts"] if x["kind"] == "paid_unsettled"][0]
        self.assertIn("o-other", alert["text"])                             # review3 I-C: may be that sale's payment
        self.assertIn("do not refund", alert["text"])
        self.assertNotIn("the unit is still ours", alert["text"])

    def test_unrecorded_sealed_sale_long_before_the_payment_does_not_hold_it(self):
        # review2 I-A: only a sealed sale settled at or after the payment can be the one the payment paid
        pay = ("0x" + "f2" * 32, "0xkim", 13126 * W, "6c" * 32, True, 1795509501, 1500)
        C = self._inflow_setup([pay], orders=[("o-old", 1795000000, 1795000600), ("o-kim", 1795509500, 1795510100)])
        self.rows.append({"seq": 23, "ts": 1795000100, "kind": "settle", "round": 4,
                          "data": {"order": "o-old", "shop": "https://b.trycloudflare.com", "sealed": "x"}, "prev": "", "hash": ""})
        _, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        rec = pl.reconcile(self.d, "m", "nocha12", C(bal + 13126 * W), SHOP, 1.0, now=1795512000)
        self.assertIn("owed-0x" + "f2" * 32, [e["id"] for e in self.lines()])

    def test_payment_20s_after_an_unrecorded_sealed_settle_waits(self):
        # review3: recover_sealed pairs a payment up to 30 s after the settle, so the hold covers 30 s too
        pay = ("0x" + "f2" * 32, "0xkim", 13126 * W, "6c" * 32, True, 1795509580, 1500)      # settle at 1795509560
        C = self._inflow_setup([pay], settles=["o-kim"])
        _, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        pl.reconcile(self.d, "m", "nocha12", C(bal + 13126 * W), SHOP, 1.0, now=1795512000)
        self.assertNotIn("paid_unsettled", [e["kind"] for e in self.lines()])

    def test_receipt_unreadable_is_read_again_on_the_next_run(self):
        # review2 I-B: an inflow saved with ok=None is re-read (receipt_ok) and classified once the node has the receipt
        pay = ("0x" + "f2" * 32, "0xkim", 13126 * W, "6c" * 32, True, 1795509501, 1500)
        Base = self._inflow_setup([])
        readable = [False]

        class C(Base):
            def inflows(self, address, start, max_blocks):
                got = [p for p in [pay] if start <= p[6] < start + max_blocks]
                return [p[:4] + (None,) + p[5:6] for p in got], min(2001, start + max_blocks), 2000

            def receipt_ok(self, tx):
                return True if readable[0] else None
        _, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        rec = pl.reconcile(self.d, "m", "nocha12", C(bal + 13126 * W), SHOP, 1.0, now=1795512000)
        self.assertFalse(rec["ok"])
        readable[0] = True
        rec = pl.reconcile(self.d, "m", "nocha12", C(bal + 13126 * W), SHOP, 1.0, now=1795512600)
        self.assertTrue(rec["ok"], rec)
        self.assertEqual(self.lines()[-1]["kind"], "paid_unsettled")

    def test_chain_receipt_ok_reads_the_status(self):
        c = pl.Chain("http://x")
        answers = {"0xa": {"status": "0x1"}, "0xb": {"status": "0x0"}, "0xc": None}
        c.call = lambda m, p: answers[p[0]]
        self.assertEqual([c.receipt_ok(t) for t in ("0xa", "0xb", "0xc")], [True, False, None])

    def test_direct_transfer_with_no_checkout_is_transfer_in_after_the_grace_time(self):
        pay = ("0x" + "d1" * 32, "0xstranger", 500 * W, "", True, 1795509501, 1500)
        C = self._inflow_setup([pay])
        _, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        self.assertFalse(pl.reconcile(self.d, "m", "nocha12", C(bal + 500 * W), SHOP, 1.0, now=1795509600)["ok"])
        rec = pl.reconcile(self.d, "m", "nocha12", C(bal + 500 * W), SHOP, 1.0, now=1795512000)
        self.assertTrue(rec["ok"], rec)
        self.assertEqual(self.lines()[-1]["kind"], "transfer_in")
        self.assertIn("money_in_unknown", [a["kind"] for a in rec["alerts"]])

    def test_scan_resumes_from_its_cursor_in_bounded_steps(self):
        pay = ("0x" + "f2" * 32, "0xkim", 13126 * W, "6c" * 32, True, 1795509501, 1950)
        C = self._inflow_setup([pay], head=2000)
        _, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        C.scans = []
        r1 = pl.reconcile(self.d, "m", "nocha12", C(bal + 13126 * W), SHOP, 1.0, now=1795512000, scan_blocks=600)
        self.assertFalse(r1["ok"])
        r2 = pl.reconcile(self.d, "m", "nocha12", C(bal + 13126 * W), SHOP, 1.0, now=1795512000, scan_blocks=600)
        self.assertTrue(r2["ok"], r2)
        self.assertEqual(C.scans, [(1000, 600), (1600, 600)])

    def test_a_later_sale_with_the_same_payment_is_not_counted_twice(self):
        pay = ("0x" + "f2" * 32, "0xkim", 13126 * W, "6c" * 32, True, 1795509501, 1500)
        C = self._inflow_setup([pay])
        _, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        pl.reconcile(self.d, "m", "nocha12", C(bal + 13126 * W), SHOP, 1.0, now=1795512000)
        self.rows.append({"seq": 30, "ts": 1795512100, "kind": "settle", "round": 4, "prev": "", "hash": "",
                          "data": {"order": "o-kim", "shop": "https://b.trycloudflare.com", "amount": 13470, "shop_fee": 344,
                                   "tx_hash": "0x" + "f2" * 32}})
        new, bal2 = pl.sync(self.d, "m", "nocha12", C(0), SHOP)
        self.assertEqual(bal2, bal + 13126 * W)
        self.assertIn("reclassifies", new[-1].get("note") or "")

    def test_refund_of_an_owed_payment_is_a_refund_of_that_order(self):
        pay = ("0x" + "f2" * 32, "0xkim", 13126 * W, "6c" * 32, True, 1795509501, 1500)
        C = self._inflow_setup([pay])
        _, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        pl.reconcile(self.d, "m", "nocha12", C(bal + 13126 * W), SHOP, 1.0, now=1795512000)
        back = "0x" + "bb" * 32

        class R(C):
            def transfer(self, tx):
                if tx == back:
                    return {"from": SHOP.lower(), "to": "0xkim", "value_wei": 13126 * W, "gas_wei": 0, "ok": True, "ts": 1795513000}
                return super().transfer(tx)
        kit_log(self.d, [sent("refund_send", back)])
        new, _ = pl.sync(self.d, "m", "nocha12", R(0), SHOP)
        self.assertEqual((new[-1]["kind"], new[-1]["order"]), ("refund", "o-kim"))


    def test_refund_hint_with_only_the_checkout_id_names_its_owed_market_order(self):
        """Review I1: the shop's refund_send writes order = checkout id and no market_order."""
        pay = ("0x" + "f2" * 32, "0xkim", 13126 * W, "6c" * 32, True, 1795509501, 1500)
        C = self._inflow_setup([pay])
        _, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        pl.reconcile(self.d, "m", "nocha12", C(bal + 13126 * W), SHOP, 1.0, now=1795512000)
        back = "0x" + "bb" * 32

        class R(C):
            def transfer(self, tx):
                if tx == back:
                    return {"from": SHOP.lower(), "to": "0xkim", "value_wei": 13126 * W, "gas_wei": 0, "ok": True, "ts": 1795513000}
                return super().transfer(tx)
        Ledger.write_agent(self, "refunds.jsonl", [{"purpose": "refund", "order": "7c30", "tx_hash": back}])
        new, _ = pl.sync(self.d, "m", "nocha12", R(0), SHOP)
        self.assertEqual((new[-1]["kind"], new[-1]["order"], new[-1]["said_order"]), ("refund", "o-kim", None))
        alerts = pl.alerts(self.d, pl.read_lines(self.d / "logs" / "payments.jsonl"), new,
                           {"ok": True, "payments_tail": pl.GENESIS}, now=1795513100)
        kinds = [a["kind"] for a in alerts]
        self.assertNotIn("refund_order_disagrees", kinds)
        self.assertNotIn("refund_over_amount", kinds)

    def _sale_record(self, order, tx):
        with open(self.d / "logs" / "sales.jsonl", "a") as f:
            f.write(json.dumps({"order": order, "amount": 13470, "shop_fee": 344, "tx_hash": tx}) + "\n")

    def test_review_c1_overlapping_orders_the_other_settled_with_its_own_payment(self):
        """Review C1: A (paid here, never settled) and B (settled at our shop with another payment) overlap."""
        pay = ("0x" + "f2" * 32, "0xkim", 13126 * W, "6c" * 32, True, 1795509501, 1500)
        C = self._inflow_setup([pay], orders=[("o-a", 1795509500, 1795510100), ("o-b", 1795509501, 1795510101)],
                               settles=["o-b"])
        self._sale_record("o-b", "0x" + "99" * 32)
        _, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        rec = pl.reconcile(self.d, "m", "nocha12", C(bal + 13126 * W), SHOP, 1.0, now=1795512000)
        self.assertTrue(rec["ok"], rec)
        self.assertEqual(self.lines()[-1]["id"], "owed-0x" + "f2" * 32)

    def test_review_c1_second_payment_for_one_checkout_is_owed_and_the_settled_one_is_not(self):
        p1 = ("0x" + "f1" * 32, "0xkim", 13126 * W, "6c" * 32, True, 1795509501, 1500)
        p2 = ("0x" + "f3" * 32, "0xkim", 13126 * W, "6c" * 32, True, 1795509530, 1530)
        C = self._inflow_setup([p1, p2], settles=["o-kim"])
        self._sale_record("o-kim", "0x" + "f3" * 32)
        _, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        rec = pl.reconcile(self.d, "m", "nocha12", C(bal + 13126 * W), SHOP, 1.0, now=1795512000)
        self.assertTrue(rec["ok"], rec)
        owed = [e for e in self.lines() if e["kind"] == "paid_unsettled"]
        self.assertEqual([e["tx_hash"] for e in owed], ["0x" + "f1" * 32])

    def test_a_settle_row_that_names_the_payment_counts_it(self):
        pay = ("0x" + "f2" * 32, "0xkim", 13126 * W, "6c" * 32, True, 1795509501, 1500)
        C = self._inflow_setup([pay])
        self.rows.append({"seq": 23, "ts": 1795509560, "kind": "settle", "round": 4, "prev": "", "hash": "",
                          "data": {"order": "o-kim", "shop": "https://b.trycloudflare.com", "amount": 13470,
                                   "shop_fee": 344, "tx_hash": "0x" + "f2" * 32}})

        class Hidden(C):                             # the sale's transfer is not readable yet: the sale waits
            def transfer(self, tx):
                return None if tx == "0x" + "f2" * 32 else super().transfer(tx)
        _, bal = pl.sync(self.d, "m", "nocha12", Hidden(0), SHOP)
        pl.reconcile(self.d, "m", "nocha12", Hidden(bal + 13126 * W), SHOP, 1.0, now=1795512000)
        self.assertNotIn("paid_unsettled", [e["kind"] for e in self.lines()])

    def test_owed_payment_settled_later_is_an_alert(self):
        pay = ("0x" + "f2" * 32, "0xkim", 13126 * W, "6c" * 32, True, 1795509501, 1500)
        C = self._inflow_setup([pay])
        _, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        pl.reconcile(self.d, "m", "nocha12", C(bal + 13126 * W), SHOP, 1.0, now=1795512000)
        self.rows.append({"seq": 30, "ts": 1795512100, "kind": "settle", "round": 4, "prev": "", "hash": "",
                          "data": {"order": "o-kim", "shop": "https://b.trycloudflare.com", "amount": 13470, "shop_fee": 344,
                                   "tx_hash": "0x" + "f2" * 32}})
        rec = pl.reconcile(self.d, "m", "nocha12", C(bal + 13126 * W), SHOP, 1.0, now=1795512200)
        self.assertTrue(rec["ok"], rec)
        self.assertIn("owed_settled_later", [a["kind"] for a in rec["alerts"]])

    def test_grant_written_first_as_money_in_is_not_counted_twice(self):
        C = self._inflow_setup([("0x" + "6a" * 32, "0xmarket", 1000 * W, "", True, 1795509501, 1500)])
        _, bal = pl.sync(self.d, "m", "nocha12", FakeChain(0), SHOP)
        self.assertTrue(pl.reconcile(self.d, "m", "nocha12", C(bal + 1000 * W), SHOP, 1.0, now=1795512000)["ok"])
        self.rows.append(row(40, "grant", {"shop": "nocha12", "credits": 1000, "why": "late", "tx_hash": "0x" + "6a" * 32}))
        new, bal2 = pl.sync(self.d, "m", "nocha12", C(0), SHOP)
        self.assertEqual(bal2, bal + 1000 * W)


class ChainInflows(unittest.TestCase):
    """Chain.inflows against a fake RPC node (review I2/I4)."""

    def node(self, blocks, receipts, head):
        c = pl.Chain("x")
        c.call = lambda m, p: hex(head)

        def batch(calls):
            if calls[0][0] == "eth_getBlockByNumber":
                return [blocks.get(int(p[0], 16)) for _, p in calls]
            return [receipts.get(p[0]) for _, p in calls]
        c.batch = batch
        return c

    def blk(self, n, txs):
        return {"timestamp": hex(1000 + n), "transactions": txs}

    def test_reads_transfers_to_us_and_stops_at_a_block_the_node_does_not_have(self):
        me = SHOP.lower()
        t = lambda h, to: {"hash": h, "from": "0xKIM", "to": to, "value": hex(5 * W), "input": "0x" + "6c" * 32}
        blocks = {10: self.blk(10, [t("0xA1", me), t("0xA2", "0xother")]), 11: self.blk(11, []), 13: self.blk(13, [t("0xA3", me)])}
        c = self.node(blocks, {"0xA1": {"status": "0x1"}}, head=20)
        got, nxt, head = c.inflows(SHOP, 10, 10, step=5)
        self.assertEqual(nxt, 12)                    # block 12 missing: read it again next time
        self.assertEqual(got, [("0xa1", "0xkim", 5 * W, "6c" * 32, True, 1010)])

    def test_unreadable_receipt_is_none_not_failed(self):
        me = SHOP.lower()
        blocks = {1: self.blk(1, [{"hash": "0xB1", "from": "0xk", "to": me, "value": hex(W), "input": "0x"}])}
        got, nxt, _ = self.node(blocks, {}, head=1).inflows(SHOP, 1, 10)
        self.assertEqual((got[0][4], nxt), (None, 2))


if __name__ == "__main__":
    unittest.main()
