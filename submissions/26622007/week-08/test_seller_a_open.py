import json
import os
import tempfile
import unittest

import profit_price
import seller_a_open as ao


def unit(**kw):
    return {"sku": "G24f5f979", "unit": "u1", "kind": "goods", "grade": "A", "cost": 1696000, "list_price": 2630000,
            **kw}


class SellerAOpenTest(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.TemporaryDirectory()
        self.addCleanup(self.md.cleanup)

    def on(self, **kw):
        with open(os.path.join(self.md.name, ao.SWITCH), "w") as f:
            json.dump({"enabled": True, "study": "a-open-t", "salt": "s1", **kw}, f)

    def salt_for(self, want, sku="G24f5f979", grade="A"):
        for i in range(200):
            if ao.arm({"salt": f"s{i}"}, sku, grade) == want:
                return f"s{i}"
        raise AssertionError

    def run_(self, u, q, keep=1, margin=None, env=None, pricing=None):
        return ao.decide(u, q, keep=keep, margin_assignment=margin, pricing=pricing, md=self.md.name, env=env or {})

    def q(self, u, median=1780000):
        return profit_price.quote(u, median=median, measured=0.03, pricing={"price": {"goods_break": 0.03, "high_median_up": {
            "min_list": 300000, "factor": 0.97}}})

    def test_off_is_todays_ask(self):
        u = unit()
        q = self.q(u)
        self.assertEqual(self.run_(u, q), (q["open"], None))
        self.on(enabled=False)
        self.assertEqual(self.run_(u, q), (q["open"], None))

    def test_env_off_and_bad_ratio_are_off(self):
        u = unit()
        q = self.q(u)
        self.on(salt=self.salt_for("B"))
        self.assertEqual(self.run_(u, q, env={"SELLER_A_OPEN": "0"}), (q["open"], None))
        for bad in (0.6, 0.9, True, "0.76", float("nan")):
            self.on(salt=self.salt_for("B"), ratio=bad)
            self.assertEqual(self.run_(u, q), (q["open"], None), bad)

    def test_arm_b_opens_at_the_list_share_arm_a_keeps_it(self):
        u = unit()
        q = self.q(u)
        self.assertLess(q["open"], 0.76 * u["list_price"])          # today's ask, the weakness
        self.on(salt=self.salt_for("B"))
        p, rec = self.run_(u, q)
        self.assertEqual(p, -(-76 * u["list_price"] // 100))
        self.assertIs(type(p), int)
        self.assertEqual((rec["arm"], rec["usual"], rec["open"]), ("B", q["open"], p))
        self.on(salt=self.salt_for("A"))
        p, rec = self.run_(u, q)
        self.assertEqual((p, rec["arm"], rec["open"]), (q["open"], "A", q["open"]))

    def test_never_lowers_and_never_over_list(self):
        u = unit()
        self.on(salt=self.salt_for("B"))
        q = self.q(u, median=2500000)                    # usual ask above 0.76 x list stays
        self.assertEqual(self.run_(u, q)[0], q["open"])
        self.on(salt=self.salt_for("B"), ratio=0.8)
        q = dict(self.q(u), floor=2700000, open=2700000)  # a floor above list stands (F wins)
        self.assertEqual(self.run_(u, q)[0], 2700000)
        q = dict(self.q(u), open=1000)
        self.assertEqual(self.run_(u, q)[0], -(-80 * u["list_price"] // 100))
        self.assertLess(self.run_(u, q)[0], u["list_price"])

    def test_ineligible_talks_untouched(self):
        self.on(salt=self.salt_for("B"))
        for u in (unit(grade="B"), unit(grade="C"), unit(kind="food"), unit(list_price=299000, cost=190000),
                  unit(list_price=None)):
            q = self.q(u) if u.get("list_price") else {"open": 100, "floor": 90, "why": ""}
            self.assertEqual(self.run_(u, q), (q["open"], None), u)
        u = unit()
        q = self.q(u)
        self.assertEqual(self.run_(u, q, keep=0.9), (q["open"], None))
        self.assertEqual(self.run_(u, q, margin={"arm": "A"}), (q["open"], None))
        for caps in ({"G24f5f979": {"A": 9000000}}, {"G24f5f979": {"*": 9000000}}):   # a cap that does not bind
            self.assertEqual(self.run_(u, q, pricing={"price": {"ask_caps": caps}}), (q["open"], None), caps)
        self.assertEqual(self.run_(u, q, pricing={"price": {"ask_caps": {"G24f5f979": {"B": 1}}}})[1]["arm"], "B")
        for why in ("ask cap 1800000 for grade A (was ...)", "scarce ask 1900000", "trial 0.9 x median 1"):
            self.assertEqual(self.run_(u, dict(q, why=why)), (q["open"], None), why)

    def test_arm_is_per_sku_grade(self):
        cfg = {"salt": "x"}
        self.assertEqual(ao.arm(cfg, "G1", "A"), ao.arm(cfg, "G1", "A"))
        arms = {ao.arm(cfg, f"G{i}", "A") for i in range(40)}
        self.assertEqual(arms, {"A", "B"})

    def test_never_raises(self):
        self.on(salt=self.salt_for("B"))
        self.assertEqual(self.run_(unit(), {"open": 5, "floor": None}), (5, None))
        self.assertEqual(ao.decide(None, {"open": 5}, keep=1, margin_assignment=None, md=self.md.name, env={})[0], 5)


if __name__ == "__main__":
    unittest.main()
