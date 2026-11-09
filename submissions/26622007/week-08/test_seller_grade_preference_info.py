"""The grade-information request (seller_grade_preference.INFO) is not a grade policy; review/W-20261109-1338-AXPro주기."""
import json, tempfile, time, unittest
from pathlib import Path

import seller_concession_trial
import seller_grade_preference as g
from shop_stock import StockBook

# market-kit.log 11/09 10:31:35, task a9701d3d (the template every grade-C-preferring buyer sends)
REAL = ("I want to buy 시몬스 뷰티레스트 허브. 라지킹 매트리스 (G3e0d6c67). Quote the total price. Require a market "
        "certificate. Confirm an available unreserved unit before agreeing; select another eligible unit if the first "
        "has sold. Prefer discounted grade C/refurbished stock when available. State price, grade and warranty hours. "
        "Do not add optional warranty fees without agreement.")
REVERSED = ("I want to buy box (G3e0d6c67). State price, grade and warranty hours. Prefer discounted grade "
            "C/refurbished stock when available. Quote the lowest total.")


def unit(grade="A", cert=True):
    return dict(unit="uA", sku="G3e0d6c67", name="mattress", category="goods", kind="goods", grade=grade, cost=100,
                cert=cert, supplied_at=10)


class InfoTest(unittest.TestCase):
    def test_real_template_is_an_optional_preference(self):
        need = g.preference_needs(REAL)
        self.assertEqual(need["grades"], {"A", "B", "C"})
        self.assertEqual(need["skus"], {"G3e0d6c67"})
        self.assertTrue(need["cert"])
        self.assertEqual(g.preference_needs(REVERSED)["grades"], {"A", "B", "C"})

    def test_pick_recovers_a_grade_a_unit_only_with_the_preference_arm(self):
        stock = StockBook(load=lambda: dict(units=[unit()]))
        self.assertIsNone(stock.pick(REAL)[0])                       # arm A: today's refusal
        got, miss = stock.pick(REAL, allow_grade_preference=True)
        self.assertEqual((got["unit"], miss), ("uA", None))
        stock = StockBook(load=lambda: dict(units=[unit(cert=False)]))  # the certificate need stays
        self.assertIsNone(stock.pick(REAL, allow_grade_preference=True)[0])

    def test_info_does_not_hide_a_real_grade_policy(self):
        for extra in (" Only grade C.", " Grade C only.", " Not grade A.",
                      " State price, grade and warranty hours.",            # twice: left strict
                      " State price, grade C and warranty hours."):
            self.assertIsNone(g.preference_needs(REAL + extra), extra)
        self.assertIsNone(g.preference_needs(REAL.replace("(G3e0d6c67)", "[G3e0d6c67]")))
        self.assertIsNone(g.preference_needs(REAL.replace("Prefer discounted grade C/refurbished stock when available. ", "")))


class WindowTest(unittest.TestCase):
    def cfg(self, root, span):
        root.mkdir(parents=True, exist_ok=True)
        (root / "seller_concession_trial.json").write_text(json.dumps(
            {"enabled": True, "id": "gp-1338", "start": 1000.0, "end": 1000.0 + span, "seed": 7}))

    def test_grade_preference_window_is_12h_and_the_concession_trial_keeps_30_min(self):
        with tempfile.TemporaryDirectory() as d:
            gp = g.GradePreferenceTrial(d)
            self.cfg(gp.root, 9 * 3600)
            self.assertEqual(gp.config()["id"], "gp-1338")
            self.cfg(gp.root, 12 * 3600 + 1)
            self.assertIsNone(gp.config())
            ct = seller_concession_trial.Trial(Path(d) / "c")
            self.cfg(ct.root, 1801)
            self.assertIsNone(ct.config())
            self.cfg(ct.root, 1800)
            self.assertEqual(ct.config()["id"], "gp-1338")

    def test_9h_window_enrolls_the_real_template(self):
        with tempfile.TemporaryDirectory() as d:
            gp = g.GradePreferenceTrial(d)
            now = time.time()
            gp.root.mkdir(parents=True)
            (gp.root / "seller_concession_trial.json").write_text(json.dumps(
                {"enabled": True, "id": "gp-1338", "start": now - 60, "end": now + 9 * 3600, "seed": 7}))
            arms = {gp.enroll(f"00000000-0000-4000-8000-{i:012d}", REAL, now=now)["arm"] for i in range(8)}
            self.assertEqual(arms, {"A", "B"})


if __name__ == "__main__":
    unittest.main()
