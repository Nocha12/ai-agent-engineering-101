"""floor_margin_verdict v2 on the real field shapes (fixture/real_*: 11/09 21:24 and 21:26 deals, their public ledger
settle/result rows and units_in rows, copied as they are). Every other case is one of those rows with one field changed.
Run: python3 -I -m unittest test_floor_margin_verdict  (in this folder)."""
import copy
import json
import os
import tempfile
import unittest

import floor_margin_verdict as v

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixture")
T1, T2 = "5ec1fb85-dd18-43be-bf78-c9b4490c9c83", "3ee61a2f-a1c8-4adf-8c0d-948db450c823"
S1_TS, S2_TS = 1794227041.701, 1794227203.624          # settle ts of the two real deals (21:24:01, 21:26:43 KST)
H6 = 6 * 3600 + 60


def real():
    with open(os.path.join(FIX, "real_memory_rows.json"), encoding="utf-8") as f:
        mem = json.load(f)
    with open(os.path.join(FIX, "real_ledger_rows.jsonl"), encoding="utf-8") as f:
        led = [json.loads(x) for x in f]
    with open(os.path.join(FIX, "real_units_in.jsonl"), encoding="utf-8") as f:
        units = [json.loads(x) for x in f]
    for n in mem:       # the real rows are a-open talks; the same row as a floor-margin talk
        a = n.pop("a_open")
        n["floor_margin"] = {"study": "fm-t", "arm": a["arm"], "share": 0.04, "usual_reserve": a["floor"],
                             "usual_ask": a["usual"], "reserve": n["reserve"], "ask": n["open"], "list": a["list"],
                             "cost": n["cost"], "stop_at": 1794293246.83632}
    return mem, led, units


def by_task(mem, task):
    return next(n for n in mem if n["task"] == task and n["result"] == "deal")


def row(led, kind, order=None):
    return next(r for r in led if r["kind"] == kind and (order is None or r["data"]["order"] == order))


class Verdict(unittest.TestCase):
    def go(self, mem, led, units, **kw):
        kw.setdefault("asof", S2_TS + H6 + 1)
        kw.setdefault("ledger_end", kw["asof"])
        return v.classify(mem, led, units, study="fm-t", **kw)

    def test_real_rows_join_task_result_order_settle(self):
        mem, led, units = real()
        out = self.go(mem, led, units)
        by = {r["task"]: r for r in out["rows"]}
        self.assertEqual(by[T1]["status"], "paid")
        self.assertEqual((by[T1]["order"], by[T1]["amount"], by[T1]["settle_ts"]), ("o-a7a58438eaeb", 447467, S1_TS))
        self.assertEqual(by[T2]["order"], "o-628672331bb7")
        self.assertEqual(by[T1]["cost"], 405522)            # units_in u-0d2de1927458
        self.assertTrue(by[T1]["mature"])
        self.assertEqual(by[T1]["net"], 447467 - 405522)

    def test_deal_without_settle_is_not_revenue(self):
        mem, led, units = real()
        led = [r for r in led if not (r["kind"] == "settle" and r["data"]["order"] == "o-a7a58438eaeb")]
        res = row(led, "result", "o-a7a58438eaeb")
        res["data"]["checks"]["settled"] = False               # the market's own word that it was not paid
        out = self.go(mem, led, units)
        r = next(x for x in out["rows"] if x["task"] == T1)
        self.assertEqual(r["status"], "unpaid")
        self.assertEqual((by_task(mem, T1)["floor_margin"]["arm"], by_task(mem, T2)["floor_margin"]["arm"]), ("B", "A"))
        main = out["parts"]["main"]
        self.assertEqual((main["B"].get("paid", 0), main["B"]["unpaid"], main["B"].get("revenue", 0)), (0, 1, 0))
        self.assertEqual((main["A"]["paid"], main["A"]["revenue"]), (1, 600980))

    def test_no_ledger_row_yet_is_unknown_not_unpaid(self):
        mem, led, units = real()
        led = [r for r in led if r["data"].get("order") != "o-a7a58438eaeb"]
        r = next(x for x in self.go(mem, led, units, ledger_end=S1_TS + 60)["rows"] if x["task"] == T1)
        self.assertEqual(r["status"], "unknown")             # ledger ends 1 min after: may still come
        r = next(x for x in self.go(mem, led, units, ledger_end=S1_TS + 3 * 3600 + 60)["rows"] if x["task"] == T1)
        self.assertEqual(r["status"], "unknown")             # time alone never makes it unpaid

    def test_amount_or_unit_mismatch_is_not_paid(self):
        for field, val in (("amount", 447000), ("unit", "u-other")):
            mem, led, units = real()
            row(led, "settle", "o-a7a58438eaeb")["data"][field] = val
            r = next(x for x in self.go(mem, led, units)["rows"] if x["task"] == T1)
            self.assertEqual(r["status"], "mismatch", field)

    def test_resold_unit_takes_its_own_order_not_the_last_settle(self):
        mem, led, units = real()
        later = copy.deepcopy(row(led, "settle", "o-a7a58438eaeb"))
        later["ts"] += 7200
        later["seq"] += 100000
        later["data"]["order"] = "o-resale"
        later["data"]["amount"] = 999999
        led.append(later)
        r = next(x for x in self.go(mem, led, units, asof=S2_TS + 9 * 3600)["rows"] if x["task"] == T1)
        self.assertEqual((r["status"], r["amount"], r["settle_ts"]), ("paid", 447467, S1_TS))

    def test_two_settles_for_one_order_are_ambiguous(self):
        mem, led, units = real()
        dup = copy.deepcopy(row(led, "settle", "o-a7a58438eaeb"))
        dup["seq"] += 1
        led.append(dup)
        r = next(x for x in self.go(mem, led, units)["rows"] if x["task"] == T1)
        self.assertEqual(r["status"], "ambiguous")

    def test_real_agreed_then_deal_rows_are_one_talk(self):
        mem, led, units = real()
        self.assertEqual([n["result"] for n in mem if n["task"] == T1], ["agreed", "deal"])   # the real shape
        out = self.go(mem, led, units)
        self.assertEqual(sorted(x["task"] for x in out["rows"]), sorted([T1, T2]))
        self.assertEqual(out["duplicate_rows"], 0)

    def test_duplicate_memory_row_counts_once(self):
        mem, led, units = real()
        mem.append(copy.deepcopy(by_task(mem, T1)))
        out = self.go(mem, led, units)
        self.assertEqual(sum(1 for x in out["rows"] if x["task"] == T1), 1)
        self.assertEqual(out["duplicate_rows"], 1)

    def test_memory_unpaid_but_ledger_settled_is_paid_and_reverse(self):
        mem, led, units = real()
        by_task(mem, T1)["result"] = "unpaid"          # memory says unpaid, the market settled it
        r = next(x for x in self.go(mem, led, units)["rows"] if x["task"] == T1)
        self.assertEqual((r["status"], r["memory_result"]), ("paid", "unpaid"))
        mem, led, units = real()
        led = [x for x in led if not (x["kind"] == "settle" and x["data"]["order"] == "o-a7a58438eaeb")]
        r = next(x for x in self.go(mem, led, units)["rows"] if x["task"] == T1)
        self.assertEqual((r["status"], r["memory_result"]), ("unknown", "deal"))  # memory deal, settled true, no settle
        mem, led, units = real()
        by_task(mem, T1)["result"] = "unpaid"
        led = [x for x in led if x["data"].get("order") != "o-a7a58438eaeb"]
        r = next(x for x in self.go(mem, led, units)["rows"] if x["task"] == T1)
        self.assertEqual(r["status"], "unknown")                                   # shop memory is not the market

    def test_rejected_talk_is_no_deal(self):
        mem, led, units = real()
        mem = [n for n in mem if n["task"] != T1 or n["result"] == "deal"]
        by_task(mem, T1)["result"] = "rejected"
        led = [r for r in led if r["data"].get("order") != "o-a7a58438eaeb"]
        r = next(x for x in self.go(mem, led, units)["rows"] if x["task"] == T1)
        self.assertEqual(r["status"], "no_deal")

    def test_missing_cost_is_unknown_not_the_talk_cost(self):
        mem, led, units = real()
        units = [u for u in units if u["unit"] != "u-0d2de1927458"]
        out = self.go(mem, led, units)
        r = next(x for x in out["rows"] if x["task"] == T1)
        self.assertEqual((r["status"], r["cost"], r["net"]), ("paid", None, None))
        self.assertEqual(out["parts"]["main"]["B"]["cost_unknown"], 1)

    def test_refund_joins_by_order(self):
        mem, led, units = real()
        led.append({"seq": 4480000, "ts": S1_TS + 3600, "kind": "broken", "round": 3,
                    "data": {"order": "o-a7a58438eaeb", "buyer": "yms979", "shop": "x", "unit": "u-0d2de1927458",
                             "item": "G2607d1f6", "hours_after_sale": 1.0, "warranty_hours": 72.0, "refunded": 447467}})
        led.append({"seq": 4480001, "ts": S1_TS + 3700, "kind": "broken", "round": 3,
                    "data": {"order": "o-someone-else", "unit": "u-0d2de1927458", "refunded": 5}})
        r = next(x for x in self.go(mem, led, units)["rows"] if x["task"] == T1)
        self.assertEqual((r["refund"], r["net"]), (447467, -405522))

    def test_maturity_is_settle_plus_6h_1m(self):
        mem, led, units = real()
        out = self.go(mem, led, units, asof=S1_TS + H6)
        st = {x["task"]: x["mature"] for x in out["rows"]}
        self.assertEqual(st, {T1: True, T2: False})
        r2 = next(x for x in out["rows"] if x["task"] == T2)
        self.assertIsNone(r2["net"])                          # an immature deal has no net yet
        self.assertEqual(out["full_mature_at"], S2_TS + H6)
        self.assertFalse(out["all_mature"])
        out = self.go(mem, led, units, asof=S2_TS + H6 + v.SETTLE_LAG)
        self.assertTrue(out["all_mature"])                    # both talks in the cohort

    def test_ledger_end_caps_asof(self):
        mem, led, units = real()
        out = self.go(mem, led, units, asof=S2_TS + 99 * 3600, ledger_end=S2_TS + 60)
        self.assertEqual({x["task"]: x["mature"] for x in out["rows"]}, {T1: False, T2: False})

    def test_window_splits_main_and_tail(self):
        mem, led, units = real()
        out = self.go(mem, led, units, window=(S1_TS - 60, S1_TS + 60), stop=S2_TS + 60)
        self.assertEqual({x["task"]: x["part"] for x in out["rows"]}, {T1: "main", T2: "tail"})
        out = self.go(mem, led, units, window=(S2_TS - 60, S2_TS + 600), stop=S2_TS + 600)
        self.assertEqual({x["task"]: x["part"] for x in out["rows"]}, {T1: "before", T2: "main"})

    def test_real_unsettled_result_row_is_unpaid_at_once(self):
        # 11/09 21:35:08 real talk dfbb85a6 (memory: agreed only) and its market result row, checks.settled false
        with open(os.path.join(FIX, "real_memory_unsettled.json"), encoding="utf-8") as f:
            mem = json.load(f)
        with open(os.path.join(FIX, "real_result_unsettled.jsonl"), encoding="utf-8") as f:
            led = [json.loads(x) for x in f]
        self.assertEqual([n["result"] for n in mem], ["agreed"])
        for n in mem:
            n["floor_margin"] = {"study": "fm-t", "arm": "B", "list": 26900, "cost": 1}
        t = led[0]["ts"]
        out = v.classify(mem, led, [], study="fm-t", asof=t + 60, ledger_end=t + 60)
        r = out["rows"][0]
        self.assertEqual((r["status"], r["memory_result"], r["order"]), ("unpaid", "agreed", "o-0d9fff8aabfb"))
        out = v.classify(mem, [], [], study="fm-t", asof=t + 60, ledger_end=t + 60)
        self.assertEqual(out["rows"][0]["status"], "unknown")   # agreed only, nothing in the ledger yet

    def test_two_orders_for_one_task_are_ambiguous(self):
        mem, led, units = real()
        other = copy.deepcopy(row(led, "result", "o-a7a58438eaeb"))
        other["data"]["order"] = "o-second"
        led.append(other)
        r = next(x for x in self.go(mem, led, units)["rows"] if x["task"] == T1)
        self.assertEqual(r["status"], "ambiguous")
        same = copy.deepcopy(row(led, "result", "o-a7a58438eaeb"))      # the same order written twice is fine
        mem, led, units = real()
        led.append(same)
        r = next(x for x in self.go(mem, led, units)["rows"] if x["task"] == T1)
        self.assertEqual(r["status"], "paid")

    def test_refund_after_asof_or_after_6h_is_not_counted(self):
        mem, led, units = real()
        led.append({"seq": 1, "ts": S1_TS + H6 + 5, "kind": "broken", "data": {"order": "o-a7a58438eaeb", "refunded": 7}})
        led.append({"seq": 2, "ts": S1_TS + 3600, "kind": "broken", "data": {"order": "o-a7a58438eaeb", "refunded": 5}})
        r = next(x for x in self.go(mem, led, units, asof=S1_TS + 1800 + H6, ledger_end=S1_TS + 99 * 3600)["rows"]
                 if x["task"] == T1)
        self.assertEqual(r["refund"], 5)
        r = next(x for x in self.go(mem, led, units, asof=S1_TS + 1800, ledger_end=S1_TS + 99 * 3600)["rows"]
                 if x["task"] == T1)
        self.assertEqual(r["refund"], 0)                       # the 1 h refund is after asof

    def test_units_in_rows_that_disagree_make_cost_unknown(self):
        mem, led, units = real()
        units.append(dict(next(u for u in units if u["unit"] == "u-0d2de1927458"), cost=1))
        r = next(x for x in self.go(mem, led, units)["rows"] if x["task"] == T1)
        self.assertEqual((r["cost"], r["net"]), (None, None))

    def test_per_talk_net_uses_one_cohort_and_parts_never_mix(self):
        mem, led, units = real()
        for n in mem:
            n["floor_margin"]["arm"] = "B"                     # both talks in arm B
        asof = S2_TS + H6 + v.SETTLE_LAG
        out = self.go(mem, led, units, asof=asof, window=(S1_TS - 600, S1_TS + 60), stop=S2_TS + 600)
        main, tail = out["parts"]["main"]["B"], out["parts"]["tail"]["B"]
        self.assertEqual((main["talks"], tail["talks"]), (1, 1))
        self.assertEqual(main["cohort"]["net_per_talk"], 447467 - 405522)
        self.assertEqual(tail["cohort"]["net_per_talk"], 600980 - 578498)
        # an immature talk of the same arm in main leaves the cohort, numerator and denominator together
        late = copy.deepcopy([n for n in mem if n["task"] == T2])
        for n in late:
            n["task"] = "late"
            n["time"] = "2026-11-09T21:24:30"
        mem += late
        out = self.go(mem, led, units, asof=S1_TS + H6 + v.SETTLE_LAG, window=(S1_TS - 600, S1_TS + 600),
                      stop=S2_TS + 600)
        c = out["parts"]["main"]["B"]["cohort"]
        self.assertEqual((c["talks"], c["net_per_talk"]), (1, 447467 - 405522))
        self.assertFalse(out["all_mature"])
        # an unknown talk inside the cohort is excluded, not a zero in the denominator
        mem, led, units = real()
        for n in mem:
            n["floor_margin"]["arm"] = "B"
        led = [r for r in led if r["data"].get("order") != "o-628672331bb7"]
        out = self.go(mem, led, units, asof=asof, ledger_end=S2_TS + 60)
        c = out["parts"]["main"]["B"]["cohort"]
        self.assertEqual(c["excluded"], 0)          # asof capped at ledger_end: T2 not in the cohort yet
        out = self.go(mem, led, units, asof=asof, ledger_end=asof)
        c = out["parts"]["main"]["B"]["cohort"]
        self.assertEqual(next(r["status"] for r in out["rows"] if r["task"] == T2), "unknown")
        self.assertEqual((c["talks"], c["excluded"], c["excluded_by"], c["net_per_talk"]),
                         (1, 1, {"unknown": 1}, 447467 - 405522))         # out of numerator and denominator
        # an explicit refusal is a real zero
        mem, led, units = real()
        for n in mem:
            n["floor_margin"]["arm"] = "B"
        led = [r for r in led if not (r["kind"] == "settle" and r["data"]["order"] == "o-628672331bb7")]
        row(led, "result", "o-628672331bb7")["data"]["checks"]["settled"] = False
        c = self.go(mem, led, units, asof=asof, ledger_end=asof)["parts"]["main"]["B"]["cohort"]
        self.assertEqual((c["talks"], c["paid"], c["net_per_talk"]), (2, 1, (447467 - 405522) // 2))

    # 21:55 parent probe (audit-20261109-2155/floor-asof-probe.py): time alone turned a missing payment into unpaid,
    # and a fixed asof still read result and settle rows written after it.
    def test_missing_payment_stays_unknown_however_long(self):
        mem, led, units = real()
        led = [r for r in led if r["data"].get("order") != "o-a7a58438eaeb"]
        for end in (S1_TS + 60, S1_TS + 3 * 3600 + 60, S2_TS + H6 + 1, S1_TS + 86400):
            r = next(x for x in self.go(mem, led, units, asof=end, ledger_end=end)["rows"] if x["task"] == T1)
            self.assertEqual(r["status"], "unknown", end)
            if r["in_cohort"]:
                self.assertEqual(v.excluded_why(r), "unknown")

    def test_explicit_refusal_is_unpaid_from_its_own_row_time(self):
        mem, led, units = real()
        led = [r for r in led if not (r["kind"] == "settle" and r["data"]["order"] == "o-a7a58438eaeb")]
        res = row(led, "result", "o-a7a58438eaeb")
        res["data"]["checks"]["settled"] = False
        end = S2_TS + H6 + 1
        for asof, want in ((res["ts"] - 1, "unknown"), (res["ts"], "unpaid"), (end, "unpaid")):
            r = next(x for x in self.go(mem, led, units, asof=asof, ledger_end=end)["rows"] if x["task"] == T1)
            self.assertEqual(r["status"], want, asof)

    def test_late_payment_is_unknown_until_its_rows_then_paid(self):
        mem, led, units = real()
        for r in led:
            if r["data"].get("order") == "o-a7a58438eaeb":
                r["ts"] += 4 * 3600                              # settle and result 4 h after the talk
        st, end = row(led, "settle", "o-a7a58438eaeb")["ts"], S1_TS + 99 * 3600
        for asof, want in ((S1_TS + 3 * 3600 + 60, "unknown"), (st - 1, "unknown"), (st + 2, "paid")):
            r = next(x for x in self.go(mem, led, units, asof=asof, ledger_end=end)["rows"] if x["task"] == T1)
            self.assertEqual(r["status"], want, asof)

    def test_fixed_asof_reads_no_later_row_of_any_kind(self):
        mem, led, units = real()
        end = S2_TS + H6 + 1
        out = self.go(mem, led, units, asof=S1_TS - 1, ledger_end=end)     # 1 s before the real settle
        self.assertEqual(out["asof"], S1_TS - 1)
        by = {r["task"]: r for r in out["rows"]}
        self.assertEqual((by[T1]["status"], by[T1]["order"], by[T1]["settle_ts"]), ("unknown", None, None))
        self.assertEqual(by[T1]["memory_result"], "agreed")                 # the 21:24:06 deal row is after asof
        self.assertNotIn(T2, by)                                            # T2 talked at 21:26:42
        # a units_in row written after asof gives no cost
        mem, led, units = real()
        next(u for u in units if u["unit"] == "u-0d2de1927458")["ts"] = S1_TS + 7200
        r = next(x for x in self.go(mem, led, units, asof=S1_TS + 3600, ledger_end=end)["rows"] if x["task"] == T1)
        self.assertEqual((r["status"], r["cost"]), ("paid", None))
        # a second settle after asof does not make it ambiguous at asof
        mem, led, units = real()
        extra = copy.deepcopy(row(led, "settle", "o-a7a58438eaeb"))
        extra["ts"] = S1_TS + 7200
        led.append(extra)
        r = next(x for x in self.go(mem, led, units, asof=S1_TS + 3600, ledger_end=end)["rows"] if x["task"] == T1)
        self.assertEqual(r["status"], "paid")

    def test_status_never_goes_back_as_asof_moves_forward(self):
        mem, led, units = real()
        refuse = copy.deepcopy(row(led, "result", "o-628672331bb7"))       # a third talk the market refused
        refuse["ts"] = S2_TS + 900
        refuse["data"].update(order="o-refused")
        refuse["data"]["receipt"]["task"] = "refused"
        refuse["data"]["checks"]["settled"] = False
        led.append(refuse)
        extra = copy.deepcopy([n for n in mem if n["task"] == T2])
        for n in extra:
            n["task"] = "refused"
            n["time"] = n["time"].replace("21:26", "21:35")
            n["result"] = "agreed"
        mem += extra
        end = S2_TS + 99 * 3600
        marks = sorted({r["ts"] for r in led} | {v.talk_ts(n) for n in mem} | {end})
        grid = sorted({m + d for m in marks for d in (-0.5, 0, 0.5)})
        prev, decided = {}, -1
        for asof in grid:
            out = self.go(mem, led, units, asof=asof, ledger_end=end)
            for r in out["rows"]:
                p = prev.get(r["task"])
                if p not in (None, "unknown"):
                    self.assertEqual(r["status"], p, (r["task"], asof))
                if r["status"] == "paid":
                    self.assertLessEqual(r["settle_ts"], out["asof"])
                prev[r["task"]] = r["status"]
            n = sum(r["status"] != "unknown" for r in out["rows"])
            self.assertGreaterEqual(n, decided)
            decided = n
        self.assertEqual((prev[T1], prev[T2], prev["refused"]), ("paid", "paid", "unpaid"))

    def test_compare_gives_unknown_as_zero_sensitivity(self):
        # Opus 22:10 M1: unknown leaves the per-talk numbers, so also give B - A with each unknown talk as a zero
        rows = []
        for arm, sku, status, net in (("A", "s1", "paid", 100), ("A", "s2", "paid", 100), ("B", "s3", "paid", 300),
                                      ("B", "s4", "unknown", None), ("B", "s5", "paid", 300)):
            rows.append({"arm": arm, "sku": sku, "grade": "A", "status": status, "net": net, "amount": 1000,
                         "cost": 700, "refund": 0, "in_cohort": True, "mature": status == "paid", "list": 1500})
        out = v.compare(rows, draws=200)
        self.assertEqual(out["unknown"], {"A": 0, "B": 1})
        self.assertEqual(out["net_per_talk_b_minus_a"]["point"], 200)              # unknown left out
        self.assertEqual(out["net_unknown0_per_talk_b_minus_a"]["point"], 100)     # 600 / 3 - 100
        self.assertLessEqual(out["net_unknown0_per_talk_b_minus_a"]["p05"], out["net_per_talk_b_minus_a"]["p05"])

    def test_grade_from_units_in_and_cluster_compare(self):
        mem, led, units = real()
        out = self.go(mem, led, units, asof=S2_TS + H6 + v.SETTLE_LAG)
        self.assertEqual({r["task"]: r["grade"] for r in out["rows"]}, {T1: "A", T2: "A"})
        c = out["compare_main"]
        self.assertEqual((c["clusters"], c["talks"]), ({"A": 1, "B": 1}, {"A": 1, "B": 1}))
        # one cluster per arm: the interval is the point, B (T1) 41,945 net minus A (T2) 22,482
        self.assertEqual(c["net_per_talk_b_minus_a"], {"point": 19463, "p05": 19463, "p95": 19463})
        self.assertEqual(c["pooled_refund_rate"], 0.0)

    def test_compare_resamples_clusters_not_talks(self):
        def r(arm, sku, net, amount=100):
            return {"arm": arm, "sku": sku, "grade": "A", "in_cohort": True, "status": "paid", "mature": True,
                    "cost": amount - net, "net": net, "amount": amount, "refund": 0, "list": 200}
        # arm B: one big cluster of 9 talks at +10 and one of 1 talk at -90; arm A: 10 talks at 0 in 10 clusters
        rows = [r("B", "Gbig", 10) for _ in range(9)] + [r("B", "Gsmall", -90)] + [r("A", f"G{i}", 0) for i in range(10)]
        c = v.compare(rows)
        self.assertEqual(c["clusters"], {"A": 10, "B": 2})
        self.assertEqual(c["net_per_talk_b_minus_a"]["point"], 0)          # (90 - 90) / 10
        self.assertLess(c["net_per_talk_b_minus_a"]["p05"], -10)           # 2 clusters: wide, not a talk-level CI
        self.assertGreater(c["net_per_talk_b_minus_a"]["p95"], 5)
        self.assertEqual(c["net_over_list_b_minus_a"], 0.0)

    def test_immature_and_cost_unknown_are_told_apart(self):
        mem, led, units = real()
        units = [u for u in units if u["unit"] != "u-0d2de1927458"]
        late = copy.deepcopy(row(led, "settle", "o-628672331bb7"))
        led = [x for x in led if x is not row(led, "settle", "o-628672331bb7")]
        late["ts"] = S2_TS + 3 * 3600                    # T2 paid 3 h after its talk (not seen live; max 56 s)
        led.append(late)
        out = self.go(mem, led, units, asof=S2_TS + H6 + v.SETTLE_LAG + 60)
        ex = {**out["parts"]["main"]["A"]["cohort"]["excluded_by"], **out["parts"]["main"]["B"]["cohort"]["excluded_by"]}
        self.assertEqual(ex, {"cost_unknown": 1, "immature": 1})
        self.assertFalse(out["all_mature"])              # every talk is in the cohort, but a paid sale is not mature

    def test_no_settle_rows_read_warns(self):
        mem, led, units = real()
        led = [r for r in led if r["kind"] != "settle"]
        self.assertTrue(self.go(mem, led, units)["warnings"])

    def test_cli_needs_window_and_stop(self):
        with self.assertRaises(SystemExit):
            v.run(["m", "u", "l", "--study", "x"])

    def test_cli_on_files(self):
        mem, led, units = real()
        with tempfile.TemporaryDirectory() as d:
            pm, pl, pu = (os.path.join(d, n) for n in ("m.json", "l.jsonl", "u.jsonl"))
            for path, text in ((pm, json.dumps({"negotiations": mem})),
                               (pl, "".join(json.dumps(r) + "\n" for r in led)),
                               (pu, "".join(json.dumps(u) + "\n" for u in units))):
                with open(path, "w") as f:
                    f.write(text)
            # two day files, the second compact JSON (no spaces) with a later non-join row
            pl2 = os.path.join(d, "l2.jsonl")
            with open(pl2, "w") as f:
                f.write(json.dumps({"seq": 9, "ts": S2_TS + 7200, "kind": "supply", "data": {}},
                                   separators=(",", ":")) + "\n")
            out, _ = v.run([pm, pu, pl, pl2, "--study", "fm-t", "--asof", str(S2_TS + H6 + 1),
                         "--window", "0", "9e12", "--stop", "9e12"])
        self.assertEqual(sorted(x["status"] for x in out["rows"]), ["paid", "paid"])
        self.assertEqual(out["ledger_end"], S2_TS + 7200)      # the last row of any kind, compact JSON read too


if __name__ == "__main__":
    unittest.main()
