import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import buyer_short_warranty as sw
import buyer_warranty_said as ws
import market_buyer

# measure/orders.jsonl, 11/09: o-7432c695ba11 and o-234498e79c5f (S .3096, history {seochanit, thisjonghoon, yeonsong2,
# yms979: 0}); the quote texts as recorded (300 characters), limits 192,767 and 127,121 from the 0 h history
THISJONGHOON = ("시몬스 뷰티레스트 시트러스. 슈퍼싱글 매트리스 (G85a1a856): grade A, market certificate attached, 72 h warranty. "
                "List price 921600, asking 57")
THISJONGHOON_2 = ("[에이스침대]BMA 1139-E CA등급/SS(슈퍼싱글사이즈) (G8095ddd6): grade A, market certificate attached, 72 h "
                  "warranty. List price 851400, ")
YMS979 = ("Thanks for asking! 시몬스 뷰티레스트 시트러스. 슈퍼싱글 매트리스 (G85a1a856), grade B, 72 h warranty, market "
          "certificate included. Asking 71")
SEOCHANIT = "G85a1a856: asking 708417. Send your integer offer. "
HIST = {"seochanit": 0, "thisjonghoon": 0, "yeonsong2": 0, "yms979": 0}
T0 = 1794220000.0


def quote(text):
    return {"warranty_h": market_buyer.warranty_hours_said(text), "warranty_said_ok": ws.stated_ok(text), "text": text}


def order_sw(arm):
    return {"S": 0.3096, "buffer": 1000000, "hist": dict(HIST),
            **({"said": {"study": "bw1", "v": ws.VERSION, "arm": arm}} if arm else {})}


class HoursForTest(unittest.TestCase):
    def test_arm_b_prices_a_quote_naming_72h_as_a_6h_deal(self):
        # before B-W1: warranty_hours_said(72 h) is None, so the 0 h history alone was left (limit 192,767)
        for text in (THISJONGHOON, THISJONGHOON_2, YMS979):
            q = quote(text)
            self.assertIsNone(q["warranty_h"])
            self.assertTrue(q["warranty_said_ok"])
            self.assertIsNone(sw.hours_for(order_sw("B"), "thisjonghoon", q))
            self.assertEqual(sw.hours_for(order_sw("A"), "thisjonghoon", q), 0)
            self.assertEqual(sw.hours_for(order_sw(None), "thisjonghoon", q), 0)

    def test_a_quote_that_names_no_warranty_keeps_the_history(self):
        q = quote(SEOCHANIT)
        self.assertFalse(q["warranty_said_ok"])
        self.assertEqual(sw.hours_for(order_sw("B"), "seochanit", q), 0)

    def test_a_short_or_mixed_warranty_is_never_set_aside(self):
        for text in ("grade C, no warranty. asking 5000", "warranty: 0 h", "three-hour full-refund warranty",
                     "grade C 3 h warranty, grade A 72 h warranty", "72 h warranty on A; this unit is sold as-is",
                     "Warranty: 2 hours. asking 100"):
            q = quote(text)
            self.assertFalse(q["warranty_said_ok"], text)
            self.assertEqual(sw.hours_for(order_sw("B"), "thisjonghoon", q), 0, text)     # the least with the 0 h history

    def test_stated_ok_readings(self):
        for text in ("72 h warranty", "warranty: 24 h", "six-hour full-refund warranty", "6 h warranty", "warranty of 3 days"):
            self.assertTrue(ws.stated_ok(text), text)
        for text in ("", None, "asking 5000", "5 h warranty", "warranty 0 hours"):
            self.assertFalse(ws.stated_ok(text), text)

    def test_short_cap_uses_the_6h_path_in_arm_b(self):
        o = {"list_price": 921600, "sku": "G85a1a856"}
        q = quote(THISJONGHOON)
        self.assertIsNone(market_buyer.short_cap(o, 0.03, {"_sw": order_sw("B")}, "thisjonghoon", q))
        self.assertIsNotNone(market_buyer.short_cap(o, 0.03, {"_sw": order_sw("A")}, "thisjonghoon", q))

    def test_the_receipt_check_is_untouched(self):
        order = {"kind": "goods", "_sw": order_sw("B")}
        self.assertIsNotNone(market_buyer.warranty_short({"warranty_hours": 0, "price": 500000}, order))
        self.assertIsNone(market_buyer.warranty_short({"warranty_hours": 72, "price": 500000}, order))

    def test_record_names_the_eligible_shops_in_both_arms(self):
        quotes = {"thisjonghoon": quote(THISJONGHOON), "yms979": quote(YMS979), "seochanit": quote(SEOCHANIT),
                  "mjforge": quote(THISJONGHOON)}          # mjforge: no 0 h history, nothing to set aside
        for arm in ("A", "B"):
            r = ws.record(order_sw(arm), quotes)
            self.assertEqual(r["arm"], arm)
            self.assertEqual(r["eligible"], ["thisjonghoon", "yms979"])
        self.assertIsNone(ws.record(order_sw(None), quotes))
        self.assertIsNone(ws.record(None, quotes))


class SwitchTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.md = self.td.name

    def tearDown(self):
        self.td.cleanup()

    def write(self, d):
        Path(self.md, ws.SWITCH).write_text(json.dumps(d))

    def good(self, **kw):
        return {"enabled": True, "study": "bw1-test", "salt": "bw1-salt", "share": 0.5,
                "start_at": T0, "stop_at": T0 + 9 * 3600, **kw}

    def test_on_only_inside_the_window(self):
        self.write(self.good())
        self.assertIsNone(ws.config(self.md, {}, T0 - 1))
        self.assertIsNotNone(ws.config(self.md, {}, T0))
        self.assertIsNotNone(ws.config(self.md, {}, T0 + 9 * 3600 - 0.001))
        self.assertIsNone(ws.config(self.md, {}, T0 + 9 * 3600))
        self.assertIsNone(ws.config(self.md, {}, T0 + 9 * 3600 + 1))

    def test_bad_or_missing_fields_are_off(self):
        self.assertIsNone(ws.config(self.md, {}, T0))                 # no file
        for bad in ({"enabled": False}, {"start_at": None}, {"stop_at": None}, {"stop_at": T0 + 13 * 3600},
                    {"stop_at": T0}, {"start_at": float("nan")}, {"share": 0}, {"share": 1.5}, {"share": True},
                    {"salt": ""}, {"study": None}, {"enabled": "true"}):
            self.write(self.good(**bad))
            self.assertIsNone(ws.config(self.md, {}, T0 + 60), bad)
        Path(self.md, ws.SWITCH).write_text("{not json")
        self.assertIsNone(ws.config(self.md, {}, T0 + 60))

    def test_environment_off(self):
        self.write(self.good())
        self.assertIsNone(ws.config(self.md, {"BUYER_WARRANTY_SAID": "0"}, T0 + 60))
        self.assertIsNotNone(ws.config(self.md, {"BUYER_WARRANTY_SAID": ""}, T0 + 60))

    def test_state_arms_split_by_its_own_salt(self):
        self.write(self.good())
        arms = [ws.state(self.md, f"o-{i:012x}", {}, T0 + 60)["arm"] for i in range(2000)]
        self.assertTrue(0.45 < arms.count("B") / len(arms) < 0.55)
        s = ws.state(self.md, "o-7432c695ba11", {}, T0 + 60)
        self.assertEqual((s["study"], s["v"], s["start_at"], s["stop_at"]), ("bw1-test", ws.VERSION, T0, T0 + 9 * 3600))
        self.assertEqual(s["arm"], ws.arm("bw1-salt", "o-7432c695ba11", 0.5))
        self.assertIsNone(ws.state(self.md, "", {}, T0 + 60))
        self.assertIsNone(ws.state(self.md, "o-1", {}, T0 + 10 * 3600))

    def test_switch_on_writes_a_finite_window_0600(self):
        d = ws.switch_on(self.md, study="bw1-test", salt="s", start_at=T0, hours=9)
        self.assertEqual(d["stop_at"], T0 + 9 * 3600)
        p = Path(self.md, ws.SWITCH)
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        self.assertIsNotNone(ws.config(self.md, {}, T0 + 1))
        before = p.read_text()
        for bad in ({"hours": 13}, {"hours": 0}, {"hours": None}, {"start_at": None}, {"salt": ""}):
            kw = {"study": "bw1-test", "salt": "s", "start_at": T0, "hours": 9, **bad}
            with self.assertRaises(ValueError):
                ws.switch_on(self.md, **kw)
            self.assertEqual(p.read_text(), before)
        self.assertEqual([n for n in os.listdir(self.md) if ".tmp-" in n], [])


class WiringTest(unittest.TestCase):
    def test_market_buyer_reads_the_switch_once_and_records_both_arms(self):
        src = Path(market_buyer.__file__).read_text(encoding="utf-8")
        self.assertEqual(src.count("buyer_warranty_said.state(md, order.get(\"order\"))"), 1)
        self.assertEqual(src.count("q[\"warranty_said_ok\"] = buyer_warranty_said.stated_ok(text)"), 1)
        self.assertEqual(src.count("buyer_warranty_said.record(order.get(\"_sw\"), quotes)"), 1)


if __name__ == "__main__":
    unittest.main()
