"""restock.urgent_listed: a window serves ~14 lines, so listed SKUs with nothing held or on its way go before refills."""
import time
import unittest

import restock

CATALOG = {"F1": {"kind": "food", "list_price": 20000}, "F2": {"kind": "food", "list_price": 20000},
           "P1": {"kind": "goods", "list_price": 400000}, "H1": {"kind": "goods", "list_price": 3000000},
           "H2": {"kind": "goods", "list_price": 1500000}, "X1": {"kind": "goods", "list_price": 100000}}
CFG = {"high_price_proven_skus": ["P1"], "priority_skus": ["F1", "F2", "H1", "H2"]}


def inv(*held, expired=()):
    now = time.time()
    return {"units": [{"sku": s} for s in held] + [{"sku": s, "expires_at": now - 1} for s in expired]}


class UrgentListedTest(unittest.TestCase):
    def test_food_then_empty_goods_then_refills_each_in_list_order(self):
        got = restock.urgent_listed(CFG, inv("P1", "F1", "H1"), CATALOG)
        self.assertEqual(got, ["F2", "H2", "F1", "P1", "H1"])     # sold-out food, empty goods, then refills

    def test_lines_on_their_way_count_as_held_and_expired_units_do_not(self):
        got = restock.urgent_listed(CFG, inv("P1", expired=("H1",)), CATALOG, [{"sku": "H2", "qty": 1}])
        self.assertEqual(got, ["F1", "F2", "H1", "P1", "H2"])
        expired = {"units": [{"sku": "P1", "grade": "expired"}, {"sku": "F1"}]}
        self.assertEqual(restock.urgent_listed(CFG, expired, CATALOG)[:3], ["F2", "P1", "H1"])

    def test_plan_sends_the_empty_skus_before_the_refills(self):
        model = {"active": True, "skus": {}}
        cfg = dict(restock.DEFAULTS, **CFG, max_lines=2, explore_lines=0, cash_buffer=0, keep_cash=0)
        ranked = [(1.0, "P1", 2), (1.0, "H1", 1), (1.0, "H2", 1)]
        orig = restock.wanted, restock.fill_listed
        restock.wanted = lambda *a, **k: list(ranked)
        restock.fill_listed = lambda r, *a, **k: r
        try:
            out = restock.plan({"balance": 10 ** 9, "units": [{"sku": "P1", "category": "c", "cost": 1}]}, CATALOG, [], cfg, model)
        finally:
            restock.wanted, restock.fill_listed = orig
        self.assertEqual([b["sku"] for b in out["buy"]][:2], ["H1", "H2"])

    def test_proven_first_takes_the_order_it_is_given(self):
        agent, formula = [{"sku": "X1", "qty": 1}], [{"sku": "P1", "qty": 2}, {"sku": "H2", "qty": 1}]
        self.assertEqual(restock.proven_first(agent, formula, CFG, order=["H2", "P1"]),
                         [{"sku": "H2", "qty": 1}, {"sku": "P1", "qty": 2}, {"sku": "X1", "qty": 1}])
        self.assertEqual(restock.proven_first(agent, formula, CFG)[:2], [{"sku": "P1", "qty": 2}, {"sku": "H2", "qty": 1}])

    def test_batch_keeps_the_order_and_queues_only_unlisted_agent_lines(self):
        keep = lambda lines: lines
        agent = [{"sku": "X1", "qty": 1}, {"sku": "P1", "qty": 1}]
        formula = [{"sku": "P1", "qty": 2}, {"sku": "H2", "qty": 1}]
        send, back = restock.batch(agent, [], formula, keep, dict(CFG, max_lines=2), ["H2", "P1"])
        self.assertEqual((send, back), ([{"sku": "H2", "qty": 1}, {"sku": "P1", "qty": 2}], [{"sku": "X1", "qty": 1}]))

    def test_a_malformed_setting_lists_nothing(self):
        self.assertEqual(restock.urgent_listed({"priority_skus": "F1"}, inv(), CATALOG), [])


if __name__ == "__main__":
    unittest.main()
