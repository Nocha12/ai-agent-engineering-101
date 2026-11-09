"""food_margin_verdict on the real field shapes (fixture/real_*: our food units of 11/09, their public ledger supply,
settle, result and expire rows, units_in rows and shop-memory talks, copied as they are; fixture/units.json names
them). Every other case is one of those rows with one field changed. Run: python3 -m unittest test_food_margin_verdict
(in this folder)."""
import copy
import json
import os
import subprocess
import sys
import unittest

import food_margin_verdict as v

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixture")
SALT, STUDY = "food-margin-2040-f1", "food-margin-2040"
START, STOP = 1794232549.604962, 1794275749.604962          # the real switch: 22:55:49 - 10:55:49 KST
with open(os.path.join(FIX, "units.json"), encoding="utf-8") as _f:
    _U = json.load(_f)
B_SOLD, A_SOLD = _U["('B', 'sold', True, True, True)"], _U["('A', 'sold', True, True, True)"]
B_STOCK, A_STOCK = _U["('B', 'stock')"], _U["('A', 'stock')"]
OLD_SALE, EXPIRED = _U["untreated"], _U["expired"]
ASOF = START + 9 * 3600 + 5                                 # 07:55:54


def real():
    with open(os.path.join(FIX, "real_memory_rows.json"), encoding="utf-8") as f:
        mem = json.load(f)
    with open(os.path.join(FIX, "real_ledger_rows.jsonl"), encoding="utf-8") as f:
        led = [json.loads(x) for x in f]
    with open(os.path.join(FIX, "real_units_in.jsonl"), encoding="utf-8") as f:
        units = [json.loads(x) for x in f]
    return mem, led, units


def settle_of(led, unit):
    return next(r for r in led if r["kind"] == "settle" and r["data"]["unit"] == unit)


def go(mem, led, units, **kw):
    kw.setdefault("asof", ASOF)
    kw.setdefault("ledger_end", kw["asof"])
    return v.classify(mem, led, units, study=STUDY, salt=SALT, start=START, stop=STOP, **kw)


def unit_row(out, unit):
    return next(r for r in out["units"] if r["unit"] == unit)


def clone(mem, led, units, unit, new, *, shift=0.0, amount=None, sold=True, supply_at=None):
    """A copy of a real sold unit under another id, its rows moved by shift seconds (a new single-unit supply row)."""
    s = copy.deepcopy(settle_of(led, unit))
    res = copy.deepcopy(next(r for r in led if r["kind"] == "result" and r["data"]["order"] == s["data"]["order"]))
    sup = copy.deepcopy(next(r for r in led if r["kind"] == "supply" and unit in r["data"]["units"]))
    u = copy.deepcopy(next(x for x in units if x["unit"] == unit))
    task = res["data"]["receipt"]["task"]
    talk = [copy.deepcopy(n) for n in mem if n["task"] == task]
    order, ntask = "o-" + new[2:], "t-" + new[2:]
    sup["ts"] = supply_at if supply_at is not None else sup["ts"] + shift
    sup["data"].update(qty=1, total=u["cost"], units=[new])
    u.update(unit=new, ts=u["ts"] + shift)
    s["ts"] += shift
    s["data"].update(unit=new, order=order, **({"amount": amount} if amount is not None else {}))
    res["ts"] += shift
    res["data"]["order"] = order
    res["data"]["receipt"].update(unit=new, task=ntask, **({"price": amount} if amount is not None else {}))
    for n in talk:
        n.update(task=ntask, unit=new, **({"price": amount} if amount is not None and n.get("price") else {}))
        n["time"] = v.kst(v.talk_ts(n) + shift)
        if n.get("food_margin"):
            n["food_margin"] = dict(n["food_margin"], unit=new, arm=v.arm(SALT, new))
    led += [sup] + ([s, res] if sold else [])
    units.append(u)
    mem += talk
    return new


def ids(arm, n, prefix="u-t"):
    """n unit ids whose arm under SALT is arm (the arm is the salt's, never chosen by the test)."""
    out, i = [], 0
    while len(out) < n:
        k = f"{prefix}{i:08x}"
        if v.arm(SALT, k) == arm:
            out.append(k)
        i += 1
    return out


class Join(unittest.TestCase):
    def test_real_sold_units_join_supply_settle_cost_and_talk(self):
        mem, led, units = real()
        out = go(mem, led, units)
        b, a = unit_row(out, B_SOLD), unit_row(out, A_SOLD)
        self.assertEqual((b["arm"], a["arm"]), ("B", "A"))
        self.assertEqual((b["status"], b["amount"], b["cost"], b["net"]), ("sold", 31722, 30211, 31722 - 30211))
        self.assertEqual((a["status"], a["amount"], a["cost"], a["net"]), ("sold", 30683, 30605, 30683 - 30605))
        self.assertEqual((b["part"], b["sku"], b["grade"]), ("main", "G200e39a3", "A"))
        self.assertEqual((b["sale_talk"], b["recorded_arm"]), ("treated", "B"))
        self.assertEqual(a["sale_talk"], "treated")

    def test_arm_is_recomputed_from_salt_and_unit(self):
        mem, led, units = real()
        out = go(mem, led, units)
        for r in out["units"]:
            self.assertEqual(r["arm"], v.arm(SALT, r["unit"]))
        self.assertEqual(out["arm_mismatch"], 0)
        for n in mem:
            if n.get("food_margin"):
                n["food_margin"]["arm"] = "A" if n["food_margin"]["arm"] == "B" else "B"
        self.assertGreater(go(mem, led, units)["arm_mismatch"], 0)

    def test_unsold_unit_in_stock_is_pending(self):
        mem, led, units = real()
        out = go(mem, led, units)
        for u in (B_STOCK, A_STOCK):
            self.assertEqual((unit_row(out, u)["status"], unit_row(out, u)["net"]), ("pending", None))

    def test_units_supplied_before_start_are_not_main(self):
        mem, led, units = real()
        out = go(mem, led, units)
        self.assertEqual(unit_row(out, OLD_SALE)["part"], "before")
        self.assertNotIn(OLD_SALE, [r["unit"] for r in out["units"] if r["part"] == "main"])

    def test_real_expire_row_is_a_loss_of_the_units_cost(self):
        mem, led, units = real()
        out = go(mem, led, units, asof=1794197780.535 + 1, ledger_end=1794197780.535 + 1)
        r = unit_row(out, EXPIRED)
        self.assertEqual((r["status"], r["cost"], r["net"]), ("expired", 8113, -8113))

    def test_goods_units_are_not_counted(self):
        mem, led, units = real()
        for u in units:
            if u["unit"] == B_SOLD:
                u["kind"] = "goods"
        self.assertNotIn(B_SOLD, [r["unit"] for r in go(mem, led, units)["units"]])

    def test_unit_without_units_in_row_is_food_by_its_sku_but_cost_stays_unknown(self):
        mem, led, units = real()
        units[:] = [u for u in units if u["unit"] != B_SOLD]
        r = unit_row(go(mem, led, units), B_SOLD)
        self.assertEqual((r["status"], r["cost"], r["net"]), ("sold", None, None))
        self.assertEqual(r["excluded_by"], "cost_unknown")

    def test_one_missing_unit_of_a_supply_takes_the_exact_residual(self):
        mem, led, units = real()
        sup = next(r for r in led if r["kind"] == "supply" and B_SOLD in r["data"]["units"])
        sup["data"].update(units=[A_SOLD, B_SOLD], qty=2, total=30605 + 30211)
        units[:] = [u for u in units if u["unit"] != B_SOLD]
        r = unit_row(go(mem, led, units), B_SOLD)
        self.assertEqual((r["cost"], r["cost_source"], r["net"]), (30211, "supply_residual", 31722 - 30211))
        self.assertEqual(r["grade"], "A")                                       # from its settle row

    def test_missing_units_of_one_arm_share_the_residual_and_of_both_arms_stay_unknown(self):
        mem, led, units = real()
        sup = next(r for r in led if r["kind"] == "supply" and B_SOLD in r["data"]["units"])
        b2 = ids("B", 1, prefix="u-g")[0]
        sup["data"].update(units=[A_SOLD, B_SOLD, b2], qty=3, total=30605 + 50000)
        units[:] = [u for u in units if u["unit"] != B_SOLD]
        out = go(mem, led, units)
        self.assertEqual((unit_row(out, B_SOLD)["cost"], unit_row(out, B_SOLD)["cost_source"]), (25000, "supply_group"))
        self.assertEqual(unit_row(out, b2)["cost"], 25000)
        a2 = ids("A", 1, prefix="u-g")[0]
        sup["data"].update(units=[A_SOLD, B_SOLD, a2])
        out = go(mem, led, units)
        self.assertEqual((unit_row(out, B_SOLD)["cost"], unit_row(out, B_SOLD)["excluded_by"]), (None, "cost_unknown"))

    def test_no_residual_when_a_known_unit_of_the_supply_has_disagreeing_rows(self):
        mem, led, units = real()
        sup = next(r for r in led if r["kind"] == "supply" and B_SOLD in r["data"]["units"])
        sup["data"].update(units=[A_SOLD, B_SOLD], qty=2, total=30605 + 30211)
        units[:] = [u for u in units if u["unit"] != B_SOLD]
        units.append(dict(next(u for u in units if u["unit"] == A_SOLD), cost=1))
        self.assertIsNone(unit_row(go(mem, led, units), B_SOLD)["cost"])

    def test_units_in_rows_that_disagree_leave_cost_unknown(self):
        mem, led, units = real()
        u = copy.deepcopy(next(x for x in units if x["unit"] == B_SOLD))
        u["cost"] += 1
        units.append(u)
        self.assertIsNone(unit_row(go(mem, led, units), B_SOLD)["cost"])

    def test_cost_never_comes_from_the_talk_or_the_supply_average(self):
        mem, led, units = real()
        units[:] = [u for u in units if u["unit"] != A_SOLD]
        r = unit_row(go(mem, led, units), A_SOLD)
        self.assertIsNone(r["cost"])
        self.assertIsNone(r["net"])


class Cut(unittest.TestCase):
    def test_settle_after_asof_is_not_read(self):
        mem, led, units = real()
        t = settle_of(led, B_SOLD)["ts"]
        out = go(mem, led, units, asof=t - 1, ledger_end=t - 1)
        self.assertEqual(unit_row(out, B_SOLD)["status"], "pending")

    def test_units_in_row_after_asof_is_not_read(self):
        mem, led, units = real()
        t = settle_of(led, B_SOLD)["ts"]
        for u in units:
            if u["unit"] == B_SOLD:
                u["ts"] = t + 10
        r = unit_row(go(mem, led, units, asof=t + 5, ledger_end=t + 5), B_SOLD)
        self.assertEqual((r["status"], r["cost"]), ("sold", None))

    def test_memory_talk_after_asof_is_not_read(self):
        mem, led, units = real()
        t = settle_of(led, B_SOLD)["ts"]
        task = next(r for r in led if r["kind"] == "result" and r["data"]["receipt"]["unit"] == B_SOLD
                    and r["data"]["order"] == settle_of(led, B_SOLD)["data"]["order"])["data"]["receipt"]["task"]
        for n in mem:
            if n["task"] == task:
                n["time"] = v.kst(t + 60)
        r = unit_row(go(mem, led, units, asof=t + 30, ledger_end=t + 30), B_SOLD)
        self.assertEqual(r["sale_talk"], "no_talk")

    def test_expire_after_asof_is_not_read(self):
        mem, led, units = real()
        out = go(mem, led, units, asof=1794197780.535 - 1, ledger_end=1794197780.535 - 1)
        self.assertEqual(unit_row(out, EXPIRED)["status"], "pending")

    def test_asof_is_capped_at_the_ledger_end(self):
        mem, led, units = real()
        out = go(mem, led, units, asof=ASOF, ledger_end=START + 3600)
        self.assertEqual(out["asof"], START + 3600)

    def test_supply_after_asof_is_not_a_unit(self):
        mem, led, units = real()
        t = next(r for r in led if r["kind"] == "supply" and B_SOLD in r["data"]["units"])["ts"]
        out = go(mem, led, units, asof=t - 1, ledger_end=t - 1)
        self.assertNotIn(B_SOLD, [r["unit"] for r in out["units"]])

    def test_status_never_goes_back_as_asof_moves_on(self):
        mem, led, units = real()
        rank = {"pending": 0, "sold": 1, "expired": 1}
        seen = {}
        for asof in (START + 600, START + 1300, START + 1400, ASOF):
            for r in go(copy.deepcopy(mem), copy.deepcopy(led), copy.deepcopy(units), asof=asof,
                        ledger_end=asof)["units"]:
                if r["unit"] in seen and seen[r["unit"]] != "pending":
                    self.assertEqual(r["status"], seen[r["unit"]])
                self.assertIn(r["status"], rank)
                seen[r["unit"]] = r["status"]


class Status(unittest.TestCase):
    def test_two_settles_for_a_unit_are_ambiguous(self):
        mem, led, units = real()
        s = copy.deepcopy(settle_of(led, B_SOLD))
        s["ts"] += 5
        s["data"]["order"] = "o-second"
        led.append(s)
        r = unit_row(go(mem, led, units), B_SOLD)
        self.assertEqual((r["status"], r["net"], r["excluded_by"]), ("ambiguous", None, "ambiguous"))

    def test_sold_and_expired_is_ambiguous(self):
        mem, led, units = real()
        e = copy.deepcopy(next(r for r in led if r["kind"] == "expire"))
        e["ts"] = settle_of(led, B_SOLD)["ts"] + 5
        e["data"]["units"] = [B_SOLD]
        led.append(e)
        self.assertEqual(unit_row(go(mem, led, units), B_SOLD)["status"], "ambiguous")

    def test_expire_of_another_shop_is_not_ours(self):
        mem, led, units = real()
        e = copy.deepcopy(next(r for r in led if r["kind"] == "expire"))
        e["ts"] = START + 100
        e["data"].update(shop="someone-else", units=[B_STOCK])
        led.append(e)
        self.assertEqual(unit_row(go(mem, led, units), B_STOCK)["status"], "pending")

    def test_past_its_date_without_a_row_is_unknown_not_a_loss(self):
        mem, led, units = real()
        for u in units:
            if u["unit"] == B_STOCK:
                u["expires_at"] = START + 3600
        r = unit_row(go(mem, led, units), B_STOCK)
        self.assertEqual((r["status"], r["net"], r["excluded_by"]), ("unknown", None, "unknown"))

    def test_refund_of_the_order_is_subtracted_until_asof(self):
        mem, led, units = real()
        s = settle_of(led, B_SOLD)
        led.append({"seq": 1, "ts": s["ts"] + 3600, "kind": "broken", "round": 3,
                    "data": {"order": s["data"]["order"], "unit": B_SOLD, "refunded": 31722}})
        self.assertEqual(unit_row(go(mem, led, units), B_SOLD)["net"], -30211)
        later = go(mem, led, units, asof=s["ts"] + 1800, ledger_end=s["ts"] + 1800)
        self.assertEqual(unit_row(later, B_SOLD)["net"], 31722 - 30211)

    def test_sale_whose_talk_has_no_record_is_untreated(self):
        mem, led, units = real()
        new = clone(mem, led, units, OLD_SALE, "u-moved00001", shift=START + 600 - settle_of(led, OLD_SALE)["ts"],
                    supply_at=START + 100)
        r = unit_row(go(mem, led, units), new)
        self.assertEqual((r["part"], r["status"], r["sale_talk"]), ("main", "sold", "untreated"))


class Exposure(unittest.TestCase):
    def test_b_sale_with_an_a_unit_of_the_sku_grade_in_stock_is_not_counted(self):
        mem, led, units = real()
        out = go(mem, led, units)
        self.assertIs(unit_row(out, B_SOLD)["other_arm_in_stock"], True)      # A_SOLD sold 90 s later
        self.assertEqual(out["arms"]["main"]["B"]["sold_without_other_arm_stock"], 0)

    def test_sale_with_no_unit_of_the_other_arm_in_stock_is_counted(self):
        mem, led, units = real()
        for u in units:
            if u["unit"] == A_SOLD:
                u["grade"] = "C"            # the only other A-arm unit of G200e39a3 x A with a known grade
        out = go(mem, led, units)
        self.assertIs(unit_row(out, B_SOLD)["other_arm_in_stock"], False)
        self.assertEqual(out["arms"]["main"]["B"]["sold_without_other_arm_stock"], 1)

    def test_unit_of_unknown_grade_is_not_stock_of_any_cluster(self):
        mem, led, units = real()
        out = go(mem, led, units)
        none = [r for r in out["units"] if r["grade"] is None]
        self.assertTrue(none)
        self.assertTrue(all(r["other_arm_in_stock"] is None for r in none))


def synth(n_a, n_b, net_a, net_b, *, clusters=6, pending_b=0, cost=1000):
    rows = []
    for arm, n, net in (("A", n_a, net_a), ("B", n_b, net_b)):
        for i, u in enumerate(ids(arm, n, prefix=f"u-{arm}{n}")):
            pend = arm == "B" and i < pending_b
            rows.append({"unit": u, "arm": arm, "sku": f"G{i % clusters}", "grade": "A", "part": "main",
                         "status": "pending" if pend else "sold", "cost": cost, "list": 2000,
                         "net": None if pend else net, "excluded_by": None})
    return rows


class Compare(unittest.TestCase):
    def test_b_above_a_gives_an_interval_above_zero_both_ways(self):
        c = v.compare(synth(60, 60, 50, 80))
        self.assertGreater(c["net_pending0_b_minus_a"]["p05"], 0)
        self.assertGreater(c["net_pending_cost_b_minus_a"]["p05"], 0)
        self.assertEqual(c["clusters"], {"A": 6, "B": 6})

    def test_pending_b_units_valued_at_their_cost_can_turn_it_round(self):
        c = v.compare(synth(60, 60, 50, 80, pending_b=10))
        self.assertGreater(c["net_pending0_b_minus_a"]["point"], 0)
        self.assertLess(c["net_pending_cost_b_minus_a"]["point"], 0)

    def test_one_empty_arm_gives_no_interval(self):
        self.assertNotIn("net_pending0_b_minus_a", v.compare(synth(30, 0, 50, 80)))

    def test_excluded_units_leave_numerator_and_denominator(self):
        rows = synth(40, 40, 50, 80)
        rows.append(dict(rows[-1], unit="u-x", status="sold", cost=None, net=None, excluded_by="cost_unknown"))
        c = v.compare(rows)
        self.assertEqual(c["excluded"]["B"], 1)
        self.assertAlmostEqual(c["net_pending0_b_minus_a"]["point"], 30)


class ShopAndGates(unittest.TestCase):
    def test_shop_food_net_per_hour_window_against_the_same_hours_a_day_before(self):
        mem, led, units = real()
        day = 86400.0
        before = clone(mem, led, units, A_SOLD, "u-yesterday1", shift=-day, amount=40000)
        out = go(mem, led, units)
        s = out["shop"]
        self.assertEqual(s["window"]["sales"], 2)
        self.assertEqual(s["window"]["net"], (31722 - 30211) + (30683 - 30605))
        self.assertEqual(s["day_before"]["sales"], 1)
        self.assertEqual(s["day_before"]["net"], 40000 - 30605)
        self.assertFalse(s["no_harm"])
        self.assertTrue(before)

    def test_expire_in_the_window_counts_against_the_shop(self):
        mem, led, units = real()
        e = copy.deepcopy(next(r for r in led if r["kind"] == "expire"))
        e["ts"] = START + 100
        led.append(e)
        self.assertEqual(go(mem, led, units)["shop"]["window"]["expired_cost"], 8113)

    def test_no_verdict_before_nine_hours(self):
        mem, led, units = real()
        out = go(mem, led, units, asof=START + 9 * 3600 - 60, ledger_end=START + 9 * 3600 - 60)
        self.assertEqual(out["verdict"], "wait")
        self.assertFalse(out["gates"]["judgeable"])

    def test_small_real_sample_is_hold_not_keep(self):
        mem, led, units = real()
        out = go(mem, led, units)
        self.assertEqual(out["verdict"], "hold")
        self.assertFalse(out["gates"]["units_per_arm"])

    def test_keep_needs_every_gate(self):
        c = v.compare(synth(60, 60, 50, 80))
        shop = {"no_harm": True, "unknown_share": 0.0}
        g = v.gates(c, shop, judgeable=True)
        self.assertEqual(v.decide(g, c), "keep")
        self.assertEqual(v.decide(v.gates(c, dict(shop, no_harm=False), judgeable=True), c), "hold")
        self.assertEqual(v.decide(v.gates(c, shop, judgeable=False), c), "wait")
        worse = v.compare(synth(60, 60, 80, 20))
        self.assertEqual(v.decide(v.gates(worse, shop, judgeable=True), worse), "revert")


class Cli(unittest.TestCase):
    def test_cli_on_the_real_fixture(self):
        p = subprocess.run([sys.executable, "-I", os.path.join(HERE, "food_margin_verdict.py"),
                            os.path.join(FIX, "real_memory_rows.json"), os.path.join(FIX, "real_units_in.jsonl"),
                            os.path.join(FIX, "real_ledger_rows.jsonl"), "--study", STUDY, "--salt", SALT,
                            "--start", str(START), "--stop", str(STOP), "--asof", str(ASOF)],
                           capture_output=True, text=True, check=True)
        out = json.loads(p.stdout)
        last = max(r["ts"] for r in real()[1])
        self.assertEqual((out["asof"], out["verdict"]), (last, "wait"))     # capped at the ledger's last row
        self.assertNotIn("main", out["arms"])      # supplied after asof - 4 h: late, not main
        self.assertEqual(out["arms"]["late"]["B"]["sold"], 1)
        self.assertNotIn("units", out)


if __name__ == "__main__":
    unittest.main()
