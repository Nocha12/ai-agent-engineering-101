"""hard_limits.py: the lines the meta-harness cannot move. Protected like the module itself: the meta-harness runs
these tests before any change stands, so a change that unhooks a guard elsewhere fails here."""
import inspect
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import agent_tools
import hard_limits as hl
import meta_harness
import restock

HERE = os.path.dirname(os.path.abspath(__file__))


class HardLimitsTest(unittest.TestCase):
    def pricing(self):
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_the_shipped_settings_are_inside_the_bounds(self):
        self.assertEqual(hl.check_policy_change({}, self.pricing()), [])

    def test_never_spends_into_the_bankruptcy_zone(self):
        self.assertEqual(hl.spendable(900000), 0)
        self.assertEqual(hl.spendable(1500000), 500000)
        self.assertGreater(hl.STOP_BUYING_BELOW, hl.BANKRUPT_LINE)

    def test_one_bounded_step_per_cycle(self):
        old = self.pricing()
        old["restock"]["max_stock_cost"] = 2000000     # the shipped value may sit at the upper bound; the step rule needs room
        new = json.loads(json.dumps(old))
        new["restock"]["max_stock_cost"] = old["restock"]["max_stock_cost"] * 2
        self.assertEqual(hl.check_policy_change(old, new), [])
        new["restock"]["max_stock_cost"] = old["restock"]["max_stock_cost"] * 2.1
        self.assertIn("more than 100%", hl.check_policy_change(old, new)[0])
        new = json.loads(json.dumps(old))
        new["restock"]["keep_cash"] = 900000
        self.assertTrue(any("outside" in p for p in hl.check_policy_change(old, new)))
        new = json.loads(json.dumps(old))
        new["restock"]["target_per_category"] = 2           # small integers may move by one
        self.assertEqual(hl.check_policy_change(old, new), [])
        del new["restock"]["max_break"]
        self.assertTrue(any("removed" in p for p in hl.check_policy_change(old, new)))
        new = json.loads(json.dumps(old))
        new["markdown"]["below_cost"] = ["food_expiry", "everything"]
        self.assertTrue(hl.check_policy_change(old, new))

    def test_a_real_sample_allows_a_bigger_step_but_not_more(self):
        old, new = {"restock": {"max_stock_cost": 1000000}}, {"restock": {"max_stock_cost": 3000000}}
        self.assertTrue(hl.check_policy_change(old, new))
        self.assertEqual(hl.check_policy_change(old, new, hl.MAX_STEP_STRONG), [])
        self.assertEqual(hl.check_policy_change(old, {"restock": {"max_stock_cost": 5000000}}, hl.MAX_STEP_STRONG), [])
        self.assertTrue(hl.check_policy_change(old, {"restock": {"max_stock_cost": 5100000}}, hl.MAX_STEP_STRONG))
        self.assertTrue(hl.check_policy_change(old, {"restock": {"max_stock_cost": 5100000}}, 9.0))     # capped at strong
        # 15M reaches 75M (about the top ten's 78M average) in one strong cycle and the 150M bound in two
        self.assertEqual(hl.check_policy_change({"restock": {"max_stock_cost": 15000000}},
                                                {"restock": {"max_stock_cost": 75000000}}, hl.MAX_STEP_STRONG), [])
        self.assertEqual(hl.check_policy_change({"restock": {"max_stock_cost": 75000000}},
                                                {"restock": {"max_stock_cost": 150000000}}, hl.MAX_STEP_STRONG), [])

    def test_a_fall_is_half_at_most_whatever_the_sample(self):
        old = {"restock": {"max_break": 0.2, "cover_hours": 6}}
        self.assertEqual(hl.check_policy_change(old, {"restock": {"max_break": 0.1, "cover_hours": 3}}, hl.MAX_STEP_STRONG), [])
        self.assertIn("more than 50%", hl.check_policy_change(old, {"restock": {"max_break": 0.05, "cover_hours": 6}},
                                                              hl.MAX_STEP_STRONG)[0])

    def test_the_meta_harness_keeps_the_stock_cap_over_its_floor(self):
        old = {"restock": {"max_stock_cost": 15000000}}
        self.assertEqual(hl.check_policy_change(old, {"restock": {"max_stock_cost": 10000000}}, hl.MAX_STEP_STRONG), [])
        self.assertTrue(any("META_FLOOR" in p for p in
                            hl.check_policy_change(old, {"restock": {"max_stock_cost": 9000000}}, hl.MAX_STEP_STRONG)))
        # under the floor already (a person's choice): the usual steps apply either way, the floor only stops a crossing
        self.assertEqual(hl.check_policy_change({"restock": {"max_stock_cost": 2000000}},
                                                {"restock": {"max_stock_cost": 3000000}}), [])
        self.assertEqual(hl.check_policy_change({"restock": {"max_stock_cost": 2000000}},
                                                {"restock": {"max_stock_cost": 1500000}}), [])
        self.assertEqual(hl.clamp_cfg("restock", {"max_stock_cost": 2000000})["max_stock_cost"], 2000000)   # a person's file stands

    def test_settings_without_bounds_still_at_most_triple(self):
        old = {"restock": {"food_max_stock_cost": 650000, "sku_target_units": {"G1": 3}}}
        self.assertEqual(hl.check_policy_change(old, {"restock": {"food_max_stock_cost": 1950000, "sku_target_units": {"G1": 9}}},
                                                hl.MAX_STEP_STRONG), [])
        self.assertEqual(len(hl.check_policy_change(old, {"restock": {"food_max_stock_cost": 2000000, "sku_target_units": {"G1": 10}}},
                                                    hl.MAX_STEP_STRONG)), 2)
        self.assertEqual(len(hl.check_policy_change(old, {"restock": {"food_max_stock_cost": 1300001, "sku_target_units": {"G1": 7}}})),
                         2)                                                         # a weak sample: double at most
        self.assertTrue(hl.check_policy_change({"x": {"y": 0.1}}, {"x": {"y": -0.1}}, hl.MAX_STEP_STRONG))   # a sign flip is a fall

    def test_explore_price_caps_reach_the_dearest_catalog_items(self):
        self.assertEqual((hl.BOUNDS["restock.goods_max_price"], hl.BOUNDS["restock.food_max_price"]),
                         ((5000, 10000000), (5000, 1000000)))

    def test_protected_files_and_guard_lines(self):
        self.assertTrue(hl.guard_problems(["hard_limits.py"], ""))
        self.assertTrue(hl.guard_problems(["restock.py"], "-    spare = min(x, hard_limits.spendable(balance))\n"))
        self.assertEqual(hl.guard_problems(["restock.py"], "+    x = 1\n-    y = 2\n"), [])
        self.assertTrue(hl.guard_problems(["restock.py"], "+hard_limits.STOP_BUYING_BELOW = 0\n"))
        self.assertTrue(hl.guard_problems(["restock.py"], "+    setattr(hard_limits, 'BOUNDS', {})\n"))
        self.assertEqual(hl.guard_problems(["restock.py"], "+    if hard_limits.spendable(b) == 0:\n"), [])

    def test_the_sandbox_shuts_out_network_keys_and_writes(self):
        self.assertIn("(deny network*)", hl.SANDBOX)
        self.assertIn(".env", hl.SANDBOX)
        self.assertIn("(deny file-write*)", hl.SANDBOX)


class GuardsStayHookedTest(unittest.TestCase):
    CATALOG = {"H1": {"kind": "goods", "category": "가전>컵", "list_price": 20000}}

    def test_whatever_restock_decides_it_prints_nothing_past_the_line(self):
        with tempfile.TemporaryDirectory() as d:
            for name, data in (("inventory.json", {"round": 3, "balance": 1100000, "units": []}), ("catalog.json", self.CATALOG)):
                with open(os.path.join(d, name), "w") as f:
                    json.dump(data, f)
            printed = []
            env = {k: v for k, v in os.environ.items() if k != "HARNESS"}
            with mock.patch.object(restock, "read_model", lambda *a: None), \
                    mock.patch.object(restock.opening, "lines", lambda *a, **k: (None, "test")), \
                    mock.patch.object(restock, "plan", lambda *a, **k: {"buy": [{"sku": "H1", "qty": 20}]}), \
                    mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "argv", ["x", "--state", d]), \
                    mock.patch("builtins.print", lambda *a, **k: printed.append(a[0] if a else "")):
                restock.main()
        out = json.loads(printed[-1])
        self.assertEqual(out["buy"], [{"sku": "H1", "qty": 6}])          # (1,100,000 - 1,000,000) // 15,000

    def test_the_stock_cap_tops_out_at_150m(self):
        self.assertEqual(hl.BOUNDS["restock.max_stock_cost"], (100000, 150000000))
        self.assertEqual(hl.clamp_cfg("restock", {"max_stock_cost": 150000000})["max_stock_cost"], 150000000)
        self.assertEqual(hl.clamp_cfg("restock", {"max_stock_cost": 300000000})["max_stock_cost"], 150000000)
        self.assertTrue(any("outside" in p for p in hl.check_policy_change({"restock": {"max_stock_cost": 140000000}},
                                                                           {"restock": {"max_stock_cost": 150000001}})))
        self.assertEqual(hl.META_FLOOR["restock.max_stock_cost"], 10000000)     # the meta-harness still keeps 10M+

    def test_the_widened_bounds(self):
        b = hl.BOUNDS
        self.assertEqual((b["restock.max_units_per_sku"], b["restock.target_per_category"], b["restock.food_explore_qty"],
                          b["restock.max_sku_share"], b["opening.explore_budget_worst"], b["restock.keep_cash"]),
                         ((1, 100), (0, 10), (0, 20), (0.01, 0.4), (0, 10000000), (1000000, 20000000)))
        self.assertEqual((hl.MAX_STEP, hl.MAX_STEP_STRONG, hl.MAX_STEP_UNBOUNDED), (1.0, 4.0, 2.0))

    def test_the_meta_harness_keeps_cash_for_refunds_and_fees(self):
        old = {"restock": {"keep_cash": 4500000}}
        self.assertEqual(hl.check_policy_change(old, {"restock": {"keep_cash": 3000000}}, hl.MAX_STEP_STRONG), [])
        self.assertTrue(any("META_FLOOR" in p for p in
                            hl.check_policy_change(old, {"restock": {"keep_cash": 2900000}}, hl.MAX_STEP_STRONG)))
        self.assertIn("more than 50%", hl.check_policy_change({"restock": {"keep_cash": 8000000}},
                                                              {"restock": {"keep_cash": 3900000}}, hl.MAX_STEP_STRONG)[0])

    def test_unlisted_dear_goods_stay_out_whatever_the_meta_writes(self):
        old = {"restock": {"high_price_listed_only": True}}
        self.assertTrue(any("FIXED" in p for p in hl.check_policy_change(old, {"restock": {"high_price_listed_only": False}})))
        self.assertTrue(any("removed" in p for p in hl.check_policy_change(old, {"restock": {}})))
        self.assertTrue(any("FIXED" in p for p in hl.check_policy_change(old, {"restock": {"high_price_listed_only": 1}})))
        self.assertEqual(hl.check_policy_change(old, old), [])
        # nor by lifting the line above every price: high_price_goods is bounded and moves a step at a time
        self.assertTrue(any("outside" in p for p in hl.check_policy_change({"restock": {"high_price_goods": 800000}},
                                                                           {"restock": {"high_price_goods": 1600000}},
                                                                           hl.MAX_STEP_STRONG)))
        self.assertEqual(hl.clamp_cfg("restock", {"high_price_goods": 5000000})["high_price_goods"], 1000000)
        self.assertEqual(hl.clamp_cfg("restock", {"high_price_goods": None})["high_price_goods"], 100000)
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            self.assertIs(json.load(f)["restock"]["high_price_listed_only"], True)

    def test_the_lines_that_stay(self):
        self.assertEqual(hl.BOUNDS["restock.keep_cash"][0], hl.STOP_BUYING_BELOW)   # the least that keeps supplies going
        self.assertEqual((hl.BANKRUPT_LINE, hl.STOP_BUYING_BELOW), (500000, 1000000))
        self.assertEqual((hl.BUYER_GOODS_WARRANTY_HOURS, hl.SELLER_GOODS_WARRANTY_HOURS), (6, 6))
        self.assertEqual(hl.PROTECTED, ("hard_limits.py", "test_hard_limits.py", "run_market.sh", "run_meta.sh", "ensure_running.sh"))
        self.assertEqual(hl.MAX_STEP_DOWN, 0.5)
        self.assertEqual(hl.BOUNDS["restock.max_lines"], (1, 10))

    def test_settings_that_are_not_numbers_fall_back_to_the_cautious_start(self):
        cfg = hl.clamp_cfg("restock", {"food_max_qty": None, "keep_cash": "0", "max_stock_cost": 10 ** 9})
        self.assertEqual((cfg["food_max_qty"], cfg["keep_cash"], cfg["max_stock_cost"]), (2, 3000000, 150000000))
        self.assertEqual(hl.clamp_cfg("markdown", {})["below_cost"], ["food_expiry"])
        self.assertTrue(hl.check_policy_change({"restock": {"max_break": 0.1}}, {"restock": {"max_break": None}}))
        self.assertTrue(hl.check_policy_change({"markdown": {"below_cost": ["food_expiry"]}}, {"markdown": {}}))

    def test_the_buyer_has_no_cap_per_deal_but_keeps_its_budget_and_list_price(self):
        import market_buyer
        cfg = hl.clamp_cfg("buyer", {"max_price_share": 1.0, "max_spend_per_deal": 10000, "quote_shops": 3})
        self.assertNotIn("max_spend_per_deal", cfg)          # whoever writes one into pricing.json
        self.assertNotIn("buyer.max_spend_per_deal", hl.BOUNDS)
        self.assertNotIn("max_spend_per_deal", hl.SAFE["buyer"])
        self.assertEqual(hl.clamp_cfg("buyer", {"max_price_share": 5})["max_price_share"], 1.0)   # never past list
        offer = {"list_price": 5 * 10 ** 9}           # far over any cap a later change could slip into the code
        self.assertEqual(market_buyer.value_limit(offer, 9 * 10 ** 9, None, cfg), 5 * 10 ** 9)
        self.assertEqual(market_buyer.value_limit(offer, 2500000, None, cfg), 2500000)           # the order's budget
        self.assertEqual(market_buyer.value_limit(offer, 9 * 10 ** 9, None, market_buyer.buyer_cfg()), 5 * 10 ** 9)
        with mock.patch.object(market_buyer, "__file__", os.path.join(tempfile.mkdtemp(), "market_buyer.py")):
            self.assertEqual(market_buyer.value_limit(offer, 9 * 10 ** 9, None, market_buyer.buyer_cfg()), 5 * 10 ** 9)
        self.assertEqual(market_buyer.accept_policy({"kind": "goods"}), {"warranty_hours": hl.BUYER_GOODS_WARRANTY_HOURS})
        self.assertFalse(hl.check_policy_change({"buyer": {"max_spend_per_deal": 800000}}, {"buyer": {}}))

    def test_the_meta_harness_still_checks_and_tests(self):
        src = inspect.getsource(meta_harness.cycle)
        self.assertIn("problems += limit_problems(code)", src)
        self.assertIn("run_tests()", src)
        lim = inspect.getsource(meta_harness.limit_problems)
        self.assertIn("hard_limits.check_policy_change", lim)
        self.assertIn("hard_limits.guard_problems", lim)
        self.assertIn('"discover"', inspect.getsource(meta_harness.run_tests))

    def test_goods_warranty_rules_stay_fixed(self):
        import market_buyer
        import seller
        self.assertGreaterEqual(hl.BUYER_GOODS_WARRANTY_HOURS, 6)
        self.assertGreaterEqual(hl.SELLER_GOODS_WARRANTY_HOURS, 6)
        self.assertEqual(market_buyer.accept_policy({"kind": "goods"}), {"warranty_hours": hl.BUYER_GOODS_WARRANTY_HOURS})
        self.assertIsNotNone(market_buyer.warranty_short({"warranty_hours": 5}, {"kind": "goods"}))
        self.assertIn("hard_limits.SELLER_GOODS_WARRANTY_HOURS", inspect.getsource(seller.Seller.warranty_terms))
        self.assertIn("problems or short", inspect.getsource(market_buyer.main))

    def test_no_tool_runs_without_the_sandbox(self):
        with mock.patch.object(shutil, "which", lambda name: None):
            self.assertIn("error", agent_tools.execute("my_memory", {"query": "x"}, tempfile.gettempdir()))

    @unittest.skipUnless(shutil.which("sandbox-exec"), "macOS sandbox")
    def test_a_tool_cannot_reach_the_keys_the_outbox_or_other_programs(self):
        tmp = tempfile.mkdtemp()
        try:
            tools, md = os.path.join(tmp, "tools"), os.path.join(tmp, "md")
            os.makedirs(md)
            probes = {"outbox": "import os,json;open(os.path.join(os.environ['MARKET_DIR'],'outbox.jsonl'),'a').write('x');print(1)",
                      "ps": "import subprocess,os;print(subprocess.run(['ps','eww',str(os.getppid())],capture_output=True).stdout)",
                      "history": "import os;print(len(open(os.path.expanduser('~/.zsh_history'),'rb').read()))",
                      "net": "import socket;socket.create_connection(('1.1.1.1',443),3);print(1)"}
            for name, code in probes.items():
                os.makedirs(os.path.join(tools, name))
                with open(os.path.join(tools, name, "tool.py"), "w") as f:
                    f.write(code)
            for name in probes:
                out = agent_tools.execute(name, {}, md, tools)
                self.assertIn("error", out if isinstance(out, dict) else {}, name)
            self.assertFalse(os.path.exists(os.path.join(md, "outbox.jsonl")))
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
