"""price.grade_break_trial (profit_price.break_trial_arm / unit_break): a unit-hash half of grade B goods priced at a
higher break chance, off outside its window or on any odd field."""
import hashlib
import unittest

import profit_price

NOW = 1_794_210_000.0
TRIAL = {"share": 0.5, "salt": "gbt-test", "grade_break": {"B": 0.19}, "start_at": NOW - 60, "stop_at": NOW + 60}


def unit(uid, grade="B", kind="goods", cost=600_000, list_price=1_000_000):
    return {"unit": uid, "sku": "Gtest", "grade": grade, "kind": kind, "cost": cost, "list_price": list_price}


def in_half(uid, salt="gbt-test", share=0.5):
    h = hashlib.sha1(f"{salt}|{uid}".encode()).digest()
    return int.from_bytes(h[:4], "big") / 2 ** 32 < share


UIDS = [f"u-{i:04d}" for i in range(400)]
TREAT = [u for u in UIDS if in_half(u)]
CTRL = [u for u in UIDS if not in_half(u)]


def cfg(**trial):
    return {**profit_price.DEFAULTS, "grade_break": {"A": 0.03, "B": 0.122, "C": 0.344}, "goods_break": 0.035,
            "min_profit": 1, "min_profit_share": 0.0, **({"grade_break_trial": trial} if trial else {})}


class GradeBreakTrial(unittest.TestCase):
    def test_hash_split_is_about_half(self):
        self.assertTrue(150 < len(TREAT) < 250)

    def test_treated_b_unit_takes_trial_rate(self):
        b, tb = profit_price.unit_break(unit(TREAT[0]), None, cfg(**TRIAL), now=NOW)
        self.assertEqual((b, tb), (0.19, 0.19))

    def test_control_b_unit_keeps_usual_rate(self):
        b, tb = profit_price.unit_break(unit(CTRL[0]), None, cfg(**TRIAL), now=NOW)
        self.assertEqual((b, tb), (0.122, None))

    def test_never_lowers_a_higher_measured_rate(self):
        b, tb = profit_price.unit_break(unit(TREAT[0]), 0.30, cfg(**TRIAL), now=NOW)
        self.assertEqual((b, tb), (0.30, 0.19))

    def test_other_grades_and_food_untouched(self):
        for u, want in ((unit(TREAT[0], "A"), 0.035), (unit(TREAT[0], "C"), 0.344), (unit(TREAT[0], kind="food"), 0.0)):
            b, tb = profit_price.unit_break(u, None, cfg(**TRIAL), now=NOW)
            self.assertEqual((b, tb), (want, None), u)

    def test_window_is_half_open_and_fail_closed(self):
        u = unit(TREAT[0])
        for now in (TRIAL["start_at"] - 1, TRIAL["stop_at"], TRIAL["stop_at"] + 1):
            self.assertIsNone(profit_price.break_trial_arm(u, cfg(**TRIAL), now=now))
        self.assertEqual(profit_price.break_trial_arm(u, cfg(**TRIAL), now=TRIAL["start_at"]), 0.19)
        for bad in ({"stop_at": None}, {"start_at": float("nan")}, {"stop_at": float("inf")}, {"share": 0},
                    {"share": 1.5}, {"share": True}, {"salt": ""}, {"grade_break": {"B": 1.0}},
                    {"grade_break": {"B": "0.19"}}, {"grade_break": None}, {"start_at": NOW + 120}):
            self.assertIsNone(profit_price.break_trial_arm(u, cfg(**{**TRIAL, **bad}), now=NOW), bad)
        self.assertIsNone(profit_price.break_trial_arm(u, cfg(), now=NOW))
        self.assertIsNone(profit_price.break_trial_arm({**u, "unit": None}, cfg(**TRIAL), now=NOW))

    def test_quote_floor_rises_only_for_treated(self):
        p = {"price": {k: v for k, v in cfg(**TRIAL).items() if k != "kind_ratio"}}
        import unittest.mock as m
        with m.patch.object(profit_price.time, "time", return_value=NOW):
            qt = profit_price.quote(unit(TREAT[0]), median=700_000, pricing=p)
            qc = profit_price.quote(unit(CTRL[0]), median=700_000, pricing=p)
        self.assertEqual(qc["floor"], profit_price.floor(600_000, 1_000_000, "goods", 0.122, profit_price.cfg_of(p)))
        self.assertEqual(qt["floor"], profit_price.floor(600_000, 1_000_000, "goods", 0.19, profit_price.cfg_of(p)))
        self.assertGreater(qt["floor"], qc["floor"])
        self.assertGreaterEqual(qt["open"], qt["floor"])
        self.assertEqual((qt["b_trial"], qc["b_trial"]), (0.19, None))
        self.assertIn("grade_break_trial b 19%", profit_price.line(unit(TREAT[0]), qt))
        self.assertNotIn("grade_break_trial", profit_price.line(unit(CTRL[0]), qc))

    def test_off_quote_is_unchanged(self):
        p_off = {"price": {k: v for k, v in cfg().items() if k != "kind_ratio"}}
        q = profit_price.quote(unit(TREAT[0]), median=700_000, pricing=p_off)
        self.assertEqual(q["b"], 0.122)
        self.assertIsNone(q["b_trial"])


if __name__ == "__main__":
    unittest.main()
