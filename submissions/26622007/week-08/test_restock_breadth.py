"""Finite breadth study: switch, assignment, listing, and existing guard limits."""
import json
import os
import tempfile
import unittest

import meta_harness
import restock
import restock_breadth as breadth

NOW = 1794290000.0
ON = {"study": "breadth-test", "salt": "s1", "skus": ["G1", "G2"], "units": 2,
      "treat": 0.5, "start_at": NOW - 60, "stop_at": NOW + 3600}


class SwitchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = self.tmp.name

    def write(self, data):
        with open(os.path.join(self.md, breadth.SWITCH), "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_validity_boundaries_and_stop(self):
        self.write({"enabled": True, **ON})
        self.assertEqual(breadth.config(self.md, env={}, now=ON["start_at"])["units"], 2)
        self.assertIsNone(breadth.config(self.md, env={}, now=ON["stop_at"]))
        self.assertIsNone(breadth.config(self.md, env={}, now=ON["start_at"] - 1))
        for value in ("0", "false", "no", "off"):
            self.assertIsNone(breadth.config(self.md, env={"RESTOCK_BREADTH": value}, now=NOW))
        bads = ({"enabled": False}, {"study": ""}, {"salt": 1}, {"skus": []},
                {"skus": ["G1", "G1"]}, {"skus": [""]}, {"skus": [1]},
                {"skus": ["G"] * 41}, {"units": 0}, {"units": 4}, {"units": True},
                {"units": 2.0}, {"units": "2"}, {"treat": 0.29}, {"treat": 1.01},
                {"start_at": "x"}, {"stop_at": float("inf")},
                {"stop_at": ON["start_at"] + 12 * 3600 + 1})
        for bad in bads:
            with self.subTest(bad=bad):
                self.write({"enabled": True, **ON, **bad})
                self.assertIsNone(breadth.config(self.md, env={}, now=NOW))
        for missing in ("enabled", "study", "salt", "skus", "start_at", "stop_at"):
            with self.subTest(missing=missing):
                self.write({k: v for k, v in {"enabled": True, **ON}.items() if k != missing})
                self.assertIsNone(breadth.config(self.md, env={}, now=NOW))
        for omitted in ("units", "treat"):
            self.write({k: v for k, v in {"enabled": True, **ON}.items() if k != omitted})
            self.assertIsNotNone(breadth.config(self.md, env={}, now=NOW))

    def test_switch_on_round_trip_and_bad_write_preserves_old(self):
        d = breadth.switch_on(self.md, study="x", salt="s", skus=["G1"], start_at=NOW, hours=12)
        self.assertEqual(breadth.config(self.md, env={}, now=NOW + 1)["skus"], ["G1"])
        self.assertEqual(d["stop_at"], NOW + 12 * 3600)
        path = os.path.join(self.md, breadth.SWITCH)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with open(path, "rb") as f:
            old = f.read()
        for bad in ({"hours": 12 + 1 / 3600}, {"units": True}, {"skus": ["G1", "G1"]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                breadth.switch_on(self.md, **{"study": "x", "salt": "s", "skus": ["G1"],
                                               "start_at": NOW, "hours": 12, **bad})
            with open(path, "rb") as f:
                self.assertEqual(f.read(), old)

    def test_arms_and_harness(self):
        arms = [breadth.arm(ON, f"G{i:08x}") for i in range(400)]
        self.assertEqual(arms, [breadth.arm(ON, f"G{i:08x}") for i in range(400)])
        self.assertTrue(150 < arms.count("B") < 250)
        self.assertNotEqual(arms, [breadth.arm({**ON, "salt": "another"}, f"G{i:08x}") for i in range(400)])
        self.assertIsNone(breadth.arm(None, "G1"))
        self.assertIn(".market/" + breadth.SWITCH, meta_harness.SWITCHES)


class ListingTest(unittest.TestCase):
    def test_appends_only_new_b_in_switch_order_without_mutation(self):
        candidates = [f"G{i}" for i in range(100)]
        bs = [s for s in candidates if breadth.arm(ON, s) == "B"][:6]
        a = next(s for s in candidates if breadth.arm(ON, s) == "A")
        on = {**ON, "skus": [bs[0], a, bs[1], bs[2], bs[3], bs[4], bs[5]]}
        priority, targets = ["old"], {"old": 4, bs[4]: 3}
        cfg = {"breadth": on, "priority_skus": priority, "sku_target_units": targets,
               "skip_skus": [bs[1]], "high_price_proven_skus": [bs[2]],
               "other": "untouched"}
        got_p, got_t, added = breadth.listing(cfg, NOW)
        self.assertEqual(got_p, ["old", bs[0], bs[3], bs[5]])
        self.assertEqual(added, {bs[0]: 2, bs[3]: 2, bs[5]: 2})
        self.assertEqual(got_t, {**targets, **added})
        self.assertEqual(priority, ["old"])
        self.assertEqual(targets, {"old": 4, bs[4]: 3})
        self.assertIsNot(got_p, priority)
        self.assertIsNot(got_t, targets)
        self.assertNotIn(a, got_p)

    def test_off_expired_and_malformed_preserve_identity(self):
        p, t = ["old"], {"old": 2}
        for cfg, now in (({"priority_skus": p, "sku_target_units": t}, NOW),
                         ({"breadth": ON, "priority_skus": p, "sku_target_units": t}, ON["stop_at"]),
                         ({"breadth": ON, "priority_skus": tuple(p), "sku_target_units": t}, NOW),
                         ({"breadth": ON, "priority_skus": p, "sku_target_units": list(t)}, NOW)):
            with self.subTest(cfg=cfg):
                got_p, got_t, added = breadth.listing(cfg, now)
                self.assertIs(got_p, cfg["priority_skus"])
                self.assertIs(got_t, cfg["sku_target_units"])
                self.assertEqual(added, {})

    def test_guard_keeps_listed_cap_and_cash(self):
        b = next(f"G{i}" for i in range(100) if breadth.arm(ON, f"G{i}") == "B")
        a = next(f"G{i}" for i in range(100) if breadth.arm(ON, f"G{i}") == "A")
        cfg = {**restock.DEFAULTS, "breadth": {**ON, "skus": [a, b]},
               "priority_skus": [], "sku_target_units": {}, "keep_cash": 0,
               "cash_buffer": 0, "max_stock_cost": float("inf"),
               "high_price_goods": 100000, "high_price_max_units": 20,
               "high_price_listed_only": True}
        cfg["priority_skus"], cfg["sku_target_units"], _ = breadth.listing(cfg, NOW)
        catalog = {s: {"kind": "goods", "list_price": 300000} for s in (a, b)}
        inv = {"balance": 10000000, "units": [{"sku": b, "kind": "goods", "cost": 1}]}
        self.assertEqual(restock.guard([{"sku": b, "qty": 5}], inv, catalog, cfg), [{"sku": b, "qty": 1}])
        self.assertEqual(restock.guard([{"sku": a, "qty": 1}], inv, catalog, cfg), [])
        poor = {**inv, "balance": 1500000}
        line = [{"sku": b, "qty": 1}]
        self.assertEqual(restock.guard(line, poor, catalog, cfg), line)
        self.assertEqual(restock.guard(line, poor, catalog, {**cfg, "cash_buffer": 1400000}), [])


if __name__ == "__main__":
    unittest.main()
