"""food_margin_verdict on the real field shapes (fixture/real_*: our food units of 11/09, their public ledger supply,
settle, result and expire rows, units_in rows and shop-memory talks, copied as they are; fixture/units.json names
them). Every other case is one of those rows with one field changed. Run: python3 -m unittest test_food_margin_verdict
(in this folder)."""
import copy
import json
import math
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
DAY = 86400.0


def real(full=False):
    """full: also the units_in rows of the other units of the fixture's supplies (fixture/real_units_in_rest.jsonl;
    one unit of the 20-unit G200e39a3 supply, u-78f6fa8bfd36, has none in the real file)."""
    with open(os.path.join(FIX, "real_memory_rows.json"), encoding="utf-8") as f:
        mem = json.load(f)
    with open(os.path.join(FIX, "real_ledger_rows.jsonl"), encoding="utf-8") as f:
        led = [json.loads(x) for x in f]
    with open(os.path.join(FIX, "real_units_in.jsonl"), encoding="utf-8") as f:
        units = [json.loads(x) for x in f]
    if full:
        with open(os.path.join(FIX, "real_units_in_rest.jsonl"), encoding="utf-8") as f:
            units += [json.loads(x) for x in f]
    return mem, led, units


def settle_of(led, unit):
    return next(r for r in led if r["kind"] == "settle" and r["data"]["unit"] == unit)


def go(mem, led, units, **kw):
    kw.setdefault("asof", ASOF)
    kw.setdefault("ledger_end", kw["asof"])
    kw.setdefault("units_in_end", kw["asof"])
    kw.setdefault("memory_end", kw["asof"])
    kw.setdefault("coverage", [(START - 2 * DAY, kw["asof"])])
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

    def test_the_real_one_missing_unit_of_a_supply_takes_the_exact_residual(self):
        mem, led, units = real(full=True)
        sup = next(r for r in led if r["kind"] == "supply" and B_SOLD in r["data"]["units"])
        known = sum(u["cost"] for u in units if u["unit"] in sup["data"]["units"])
        r = unit_row(go(mem, led, units), "u-78f6fa8bfd36")
        self.assertEqual((r["cost"], r["cost_source"]), (sup["data"]["total"] - known, "supply_residual"))
        self.assertEqual(sup["data"]["qty"], 20)

    def test_one_missing_sold_unit_gets_its_residual_and_its_net(self):
        mem, led, units = real(full=True)
        units[:] = [u for u in units if u["unit"] != B_SOLD]
        units.append({"ts": START + 60, "unit": "u-78f6fa8bfd36", "sku": "G200e39a3", "kind": "food", "grade": "A",
                      "cost": 22000, "list_price": 37900, "supplied_at": 1794232827, "expires_at": None})
        sup = next(r for r in led if r["kind"] == "supply" and B_SOLD in r["data"]["units"])
        others = sum(u["cost"] for u in units if u["unit"] in sup["data"]["units"])
        sup["data"]["total"] = others + 30211
        r = unit_row(go(mem, led, units), B_SOLD)
        self.assertEqual((r["cost"], r["cost_source"], r["net"]), (30211, "supply_residual", 31722 - 30211))
        self.assertEqual(r["grade"], "A")                                       # from its settle row

    def test_two_missing_units_of_one_arm_never_get_the_supply_average(self):
        mem, led, units = real(full=True)
        sup = next(r for r in led if r["kind"] == "supply" and B_SOLD in r["data"]["units"])
        other_b = next(u for u in sup["data"]["units"] if u not in (B_SOLD, "u-78f6fa8bfd36")
                       and v.arm(SALT, u) == "B")
        units.append({"ts": START + 60, "unit": "u-78f6fa8bfd36", "sku": "G200e39a3", "kind": "food", "grade": "A",
                      "cost": 22000, "list_price": 37900, "supplied_at": 1794232827, "expires_at": None})
        units[:] = [u for u in units if u["unit"] not in (B_SOLD, other_b)]
        out = go(mem, led, units)
        for u in (B_SOLD, other_b):
            self.assertEqual((unit_row(out, u)["cost"], unit_row(out, u)["excluded_by"]), (None, "cost_unknown"))

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

    def test_units_in_row_without_a_time_is_not_read(self):
        mem, led, units = real()
        for u in units:
            if u["unit"] == B_SOLD:
                u.pop("ts")
        self.assertIsNone(unit_row(go(mem, led, units), B_SOLD)["cost"])

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



    def test_pending_unit_of_the_other_arm_supplied_before_the_ledger_is_not_stock(self):
        mem, led, units = real()
        for u in units:
            if u["unit"] == A_SOLD:
                u["grade"] = "C"
        old = ids("A", 1, prefix="u-old")[0]
        units.append({"ts": START - 3 * DAY, "unit": old, "sku": "G200e39a3", "kind": "food", "grade": "A",
                      "cost": 30000, "list_price": 37900, "supplied_at": START - 3 * DAY, "expires_at": None})
        out = go(mem, led, units, coverage=[(START - 2 * DAY, ASOF)])
        self.assertEqual(unit_row(out, old)["status"], "uncovered")
        self.assertIs(unit_row(out, B_SOLD)["other_arm_in_stock"], False)


class Coverage(unittest.TestCase):
    def test_files_join_within_ten_minutes_and_a_gap_breaks_them(self):
        self.assertEqual(v.covered([(10, 100), (500, 900)]), [[10, 900]])
        self.assertEqual(v.covered([(10, 100), (800, 900)]), [[10, 100], [800, 900]])

    def test_a_day_before_not_in_the_ledger_read_gives_no_shop_verdict(self):
        mem, led, units = real()
        out = go(mem, led, units, coverage=[(START - 600, ASOF)])
        self.assertIsNone(out["shop"]["day_before"]["net_per_hour"])
        self.assertIsNone(out["shop"]["no_harm"])
        self.assertFalse(out["gates"]["shop_no_harm"])
        self.assertTrue(any("does not cover" in w for w in out["warnings"]))

    def test_asof_is_capped_at_the_memory_and_units_in_ends(self):
        mem, led, units = real()
        out = go(mem, led, units, memory_end=START + 1000, units_in_end=START + 2000)
        self.assertEqual(out["asof"], START + 1000)
        out = go(mem, led, units, memory_end=None, units_in_end=None)
        self.assertEqual(out["asof"], min(out["input_ends"]["memory"], out["input_ends"]["units_in"]))

    def test_malformed_rows_are_skipped_and_counted(self):
        mem, led, units = real()
        mem.append(dict(mem[0], time="not a time"))
        settle_of(led, B_SOLD)["data"]["amount"] = None
        out = go(mem, led, units)
        self.assertEqual(unit_row(out, B_SOLD)["status"], "ambiguous")
        self.assertTrue(any("2 malformed" in w for w in out["warnings"]))


def synth(spec, *, cost=1000):
    """spec: [(units per arm, net A, net B, noise)] one per SKU x grade cluster. Noise is spread evenly
    (-noise .. +noise) over the units so every cluster has a real within-arm variance."""
    rows = []
    for c, (n, na, nb, noise) in enumerate(spec):
        for a, base in (("A", na), ("B", nb)):
            for i, u in enumerate(ids(a, n, prefix=f"u-c{c}{a}")):
                jit = (2 * i / max(1, n - 1) - 1) * noise
                rows.append({"unit": u, "arm": a, "sku": f"G{c}", "grade": "A", "part": "main", "status": "sold",
                             "cost": cost, "net": base + jit, "excluded_by": None})
    return rows


SIX = [(10, 50, 90, 40)] * 6


class Compare(unittest.TestCase):
    def test_b_above_a_in_every_cluster_gives_lo_above_zero_every_way(self):
        c = v.compare(synth(SIX))
        for k in ("net_pending0_b_minus_a", "net_pending_cost_b_minus_a", "net_talk_cost_b_minus_a",
                  "net_bound_b_minus_a"):
            self.assertGreater(c[k]["lo"], 0)
            self.assertAlmostEqual(c[k]["point"], 40)
        self.assertEqual(c["clusters_both_arms"], 6)

    def test_one_big_cluster_carrying_the_effect_is_not_enough(self):
        c = v.compare(synth([(40, 0, 400, 50)] + [(10, 60, 20, 40)] * 4))
        iv = c["net_pending0_b_minus_a"]
        self.assertGreater(iv["point"], 0)
        self.assertLess(iv["lo"], 0)

    def test_no_effect_gives_an_interval_round_zero(self):
        iv = v.compare(synth([(10, 50, 50 + d, 40) for d in (-30, 20, -10, 30, -20, 10)]))["net_pending0_b_minus_a"]
        self.assertLess(iv["lo"], 0)
        self.assertGreater(iv["hi"], 0)

    def test_stratified_estimate_is_not_moved_by_arm_imbalance_across_skus(self):
        rows = synth([(10, 100, 100, 0), (10, 5000, 5000, 0)])
        drop = [r for r in rows if r["sku"] == "G0" and r["arm"] == "A"][:8]
        rows = [r for r in rows if r not in drop]
        self.assertEqual(v.compare(rows)["net_pending0_b_minus_a"]["point"], 0)

    def test_pending_b_units_valued_at_their_cost_can_turn_it_round(self):
        rows = synth(SIX)
        for r in [r for r in rows if r["arm"] == "B"][::2]:
            r.update(status="pending", net=None)
        c = v.compare(rows)
        self.assertLess(c["net_pending_cost_b_minus_a"]["point"], 0)
        self.assertGreater(c["net_pending0_b_minus_a"]["point"], c["net_pending_cost_b_minus_a"]["point"])

    def test_excluded_b_units_at_the_arms_worst_lower_the_bound(self):
        rows = synth(SIX)
        for r in [r for r in rows if r["arm"] == "B"][::2]:
            r.update(net=None, cost=None, excluded_by="cost_unknown")
        c = v.compare(rows)
        self.assertLess(c["net_bound_b_minus_a"]["point"], c["net_pending0_b_minus_a"]["point"])
        self.assertEqual(c["excluded"]["B"], 30)

    def test_cost_unknown_units_come_back_at_their_talk_cost_only_in_that_way(self):
        rows = synth(SIX)
        bs = [r for r in rows if r["arm"] == "B"][::2]
        for r in bs:
            r.update(status="sold", amount=1000 + r["net"] + 5000, refund=0, net=None, cost=None,
                     excluded_by="cost_unknown", talk_cost=1000)
        c = v.compare(rows)
        self.assertEqual(c["unresolved"]["B"], 0)
        self.assertEqual(c["excluded"]["B"], 30)
        self.assertAlmostEqual(c["net_talk_cost_b_minus_a"]["point"], 40 + 5000 / 2)
        self.assertLess(c["net_pending0_b_minus_a"]["point"], 100)                 # those units left out
        for r in bs[:3]:
            r["talk_cost"] = None
        self.assertEqual(v.compare(rows)["unresolved"]["B"], 3)

    def test_real_talk_cost_is_read_up_to_asof_and_two_costs_are_none(self):
        mem, led, units = real()
        units[:] = [u for u in units if u["unit"] != B_SOLD]
        r = unit_row(go(mem, led, units), B_SOLD)
        self.assertEqual((r["cost"], r["talk_cost"]), (None, 30211))
        self.assertEqual(v.talk_net(r), 31722 - 30211)
        t = min(v.talk_ts(n) for n in mem if n["unit"] == B_SOLD)
        self.assertIsNone(unit_row(go(mem, led, units, asof=t - 1), B_SOLD)["talk_cost"])     # talk after asof
        mem.append(dict(next(n for n in mem if n["unit"] == B_SOLD), cost=1))
        self.assertIsNone(unit_row(go(mem, led, units), B_SOLD)["talk_cost"])

    def test_clusters_count_only_with_five_units_of_each_arm(self):
        self.assertEqual(v.compare(synth([(4, 50, 90, 10)] * 6))["clusters_both_arms"], 0)

    def test_t_interval_numbers(self):
        spec = [(5, 0, d, 0) for d in (10, 20, 30, 40)]
        iv = v.compare(synth(spec))["net_pending0_b_minus_a"]
        se = math.sqrt(sum((10 * (d - 25)) ** 2 for d in (10, 20, 30, 40)) * 4 / 3) / 40
        self.assertEqual((iv["point"], iv["clusters"]), (25, 4))
        self.assertAlmostEqual(iv["t_p05"], round(25 - 2.353 * se, 2))
        self.assertEqual(v.t95(45), 1.671)

    def test_clusters_under_five_units_an_arm_are_left_out_of_the_interval(self):
        iv = v.compare(synth(SIX + [(3, 0, 100000, 0)]))["net_pending0_b_minus_a"]
        self.assertEqual(iv["clusters"], 6)
        self.assertAlmostEqual(iv["point"], 40)

    def test_bound_puts_a_sold_unit_at_its_price_minus_its_clusters_cost_range(self):
        rows = synth(SIX)
        for r in rows:
            r["cost"] = 1000 + (r["net"] % 7)
        b = [r for r in rows if r["arm"] == "B"][0]
        b.update(amount=1500, refund=0, net=None, cost=None, excluded_by="cost_unknown")
        top = max(r["cost"] for r in rows if r["sku"] == b["sku"] and r["net"] is not None)
        self.assertEqual(v.bound_net(b, [r["cost"] for r in rows if r["sku"] == b["sku"] and r["net"] is not None],
                                     -9999), 1500 - top)

    def test_about_six_percent_cost_unknown_with_talk_cost_still_passes_the_unresolved_gate(self):
        rows = synth(SIX)
        for r in [r for r in rows if r["arm"] == "B"][::16] + [r for r in rows if r["arm"] == "A"][::16]:
            r.update(status="sold", amount=1000 + r["net"], refund=0, net=None, cost=None,
                     excluded_by="cost_unknown", talk_cost=1000)
        c = v.compare(rows)
        self.assertGreaterEqual(c["excluded"]["B"] / c["units"]["B"], .06)
        g = v.gates(c, SHOP_OK, judgeable=True)
        self.assertTrue(g["unresolved_under_5pct"])
        self.assertEqual(v.decide(g, c), "keep")

    def test_one_empty_arm_gives_no_interval(self):
        rows = [r for r in synth(SIX) if r["arm"] == "A"]
        self.assertNotIn("net_pending0_b_minus_a", v.compare(rows))


SHOP_OK = {"no_harm": True}


class ShopAndGates(unittest.TestCase):
    def test_shop_food_net_per_hour_window_against_the_same_hours_a_day_before(self):
        mem, led, units = real()
        clone(mem, led, units, A_SOLD, "u-yesterday1", shift=-DAY, amount=40000)
        s = go(mem, led, units)["shop"]
        self.assertEqual((s["window"]["sales"], s["window"]["net_scaled"]), (2, (31722 - 30211) + (30683 - 30605)))
        self.assertEqual((s["day_before"]["sales"], s["day_before"]["net_scaled"]), (1, 40000 - 30605))
        self.assertIs(s["no_harm"], False)

    def test_unknown_cost_sales_are_scaled_and_bounded(self):
        mem, led, units = real()
        units[:] = [u for u in units if u["unit"] != B_SOLD]
        s = go(mem, led, units)["shop"]
        self.assertEqual((s["window"]["unknown_cost_sales"], s["window"]["net_scaled"]), (1, 2 * (30683 - 30605)))
        self.assertEqual(s["window"]["net_per_hour_low"], s["window"]["net_per_hour_high"])   # one known net: 78

    def test_no_harm_needs_the_scaled_point_and_the_talk_cost_comparison(self):
        def sale(i, t, net, talk=None):
            return {"unit": f"u-s{i}", "settle_ts": t, "net": net, "status": "sold", "amount": 1000 + (talk or 0),
                    "refund": 0, "excluded_by": None if net is not None else "cost_unknown", "talk_cost": 1000}
        lo, hi = START, START + 3600
        cov = [[START - 2 * DAY, hi]]
        day = [sale(0, lo - DAY + 10, 100), sale(1, lo - DAY + 20, 100)]
        win = [sale(2, lo + 10, 100), sale(3, lo + 20, 300), sale(4, lo + 30, None, talk=200)]
        s = v.shop(day + win, [], lo, hi, cov, hi)
        self.assertEqual((s["window"]["net_per_hour"], s["day_before"]["net_per_hour"]), (600, 200))
        self.assertEqual(s["window"]["net_per_hour_talk_cost"], 600)
        self.assertEqual((s["window"]["net_per_hour_low"], s["window"]["net_per_hour_high"]), (500, 700))
        self.assertIs(s["no_harm"], True)
        day.append(sale(5, lo - DAY + 30, 800))                 # day before: scaled 1000
        self.assertIs(v.shop(day + win, [], lo, hi, cov, hi)["no_harm"], False)
        day[-1]["net"] = 300                                    # day before 500: the point passes
        win[2].update(amount=1000 - 400)                        # the unknown sale at its talk cost: -400
        s = v.shop(day + win, [], lo, hi, cov, hi)
        self.assertGreaterEqual(s["window"]["net_per_hour"], s["day_before"]["net_per_hour"])
        self.assertEqual(s["window"]["net_per_hour_talk_cost"], 0)
        self.assertIs(s["no_harm"], False)                      # the talk-cost comparison decides
        win[2]["talk_cost"] = None
        self.assertIs(v.shop(day + win, [], lo, hi, cov, hi)["no_harm"], True)   # no talk cost: the point alone

    def test_an_ambiguous_sale_counts_as_an_unknown_cost_sale(self):
        mem, led, units = real()
        e = copy.deepcopy(next(r for r in led if r["kind"] == "expire"))
        e["ts"] = settle_of(led, B_SOLD)["ts"] + 5
        e["data"]["units"] = [B_SOLD]
        led.append(e)
        self.assertEqual(go(mem, led, units)["shop"]["window"]["unknown_cost_sales"], 1)

    def test_expire_in_the_window_counts_against_the_shop(self):
        mem, led, units = real()
        e = copy.deepcopy(next(r for r in led if r["kind"] == "expire"))
        e["ts"] = START + 100
        led.append(e)
        self.assertEqual(go(mem, led, units)["shop"]["window"]["expired_cost"], 8113)

    def test_no_verdict_before_nine_hours(self):
        mem, led, units = real()
        out = go(mem, led, units, asof=START + 9 * 3600 - 60)
        self.assertEqual(out["verdict"], "wait")
        self.assertFalse(out["gates"]["judgeable"])

    def test_small_real_sample_is_hold_not_keep(self):
        out = go(*real())
        self.assertEqual(out["verdict"], "hold")
        self.assertFalse(out["gates"]["units_per_arm"])

    def test_keep_needs_every_gate(self):
        good = v.compare(synth(SIX))
        self.assertEqual(v.decide(v.gates(good, SHOP_OK, judgeable=True), good), "keep")
        for shop in ({"no_harm": False}, {"no_harm": None}):
            self.assertEqual(v.decide(v.gates(good, shop, judgeable=True), good), "hold")
        self.assertEqual(v.decide(v.gates(good, SHOP_OK, judgeable=False), good), "wait")
        few = v.compare(synth([(10, 50, 90, 40)] * 3))
        self.assertFalse(v.gates(few, SHOP_OK, judgeable=True)["clusters_both_arms"])
        small = v.compare(synth([(4, 50, 90, 10)] * 6))
        self.assertFalse(v.gates(small, SHOP_OK, judgeable=True)["units_per_arm"])
        rows = synth(SIX)
        for r in [r for r in rows if r["arm"] == "A"][:3]:
            r.update(net=None, excluded_by="unknown")
        ex = v.compare(rows)
        g = v.gates(ex, SHOP_OK, judgeable=True)
        self.assertFalse(g["unresolved_under_5pct"])
        self.assertEqual(v.decide(g, ex), "hold")

    def test_revert_needs_hi_below_zero_and_the_sample_gates(self):
        worse = v.compare(synth([(10, 90, 20, 30)] * 6))
        self.assertEqual(v.decide(v.gates(worse, SHOP_OK, judgeable=True), worse), "revert")
        rows = synth([(10, 90, 20, 30)] * 6)
        for r in [r for r in rows if r["arm"] == "B"][:6]:
            r.update(net=None, excluded_by="cost_unknown")
        ex = v.compare(rows)
        self.assertEqual(v.decide(v.gates(ex, SHOP_OK, judgeable=True), ex), "hold")


class Malformed(unittest.TestCase):
    def test_non_object_ledger_rows_and_bad_units_in_lines_are_counted(self):
        mem, led, units = real()
        led += [None, {"ts": START, "kind": "settle", "data": "x"}]
        units.append(None)
        out = go(mem, led, units)
        self.assertTrue(any("3 malformed" in w for w in out["warnings"]))

    def test_a_unit_supplied_in_a_gap_between_files_is_uncovered(self):
        mem, led, units = real()
        t = next(r for r in led if r["kind"] == "supply" and B_SOLD in r["data"]["units"])["ts"]
        out = go(mem, led, units, coverage=[(START - 2 * DAY, t - 1000), (t + 1000, ASOF)])
        self.assertEqual(unit_row(out, B_SOLD)["status"], "uncovered")

    def test_asof_cut_back_by_an_input_end_is_warned(self):
        mem, led, units = real()
        out = go(mem, led, units, memory_end=ASOF - 3600)
        self.assertTrue(any("memory input's end" in w for w in out["warnings"]))


class Cli(unittest.TestCase):
    def test_cli_on_the_real_fixture(self):
        p = subprocess.run([sys.executable, "-I", os.path.join(HERE, "food_margin_verdict.py"),
                            os.path.join(FIX, "real_memory_rows.json"), os.path.join(FIX, "real_units_in.jsonl"),
                            os.path.join(FIX, "real_ledger_rows.jsonl"), "--study", STUDY, "--salt", SALT,
                            "--start", str(START), "--stop", str(STOP), "--asof", str(ASOF)],
                           capture_output=True, text=True, check=True)
        out = json.loads(p.stdout)
        led = real()[1]
        self.assertEqual(out["coverage"], [[min(r["ts"] for r in led), out["asof"]]])
        self.assertEqual(out["verdict"], "wait")
        self.assertLessEqual(out["asof"], max(r["ts"] for r in led))
        self.assertTrue(any("does not cover" in w for w in out["warnings"]))    # one fixture file, no day before
        self.assertNotIn("units", out)


if __name__ == "__main__":
    unittest.main()
