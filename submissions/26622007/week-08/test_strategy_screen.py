import tempfile
import unittest
from pathlib import Path
import agent_tools
from strategy_screen import screen


class ScreenTest(unittest.TestCase):
    def test_affordable_evidence_requires_identified_buyers_and_finite_prices(self):
        sale = dict(k='settle', seq=1, ts=100, sku='G', buyer='b', amount=95000)
        rows = [sale, dict(sale, seq=2, buyer='c', amount=140000),
                dict(sale, seq=3, buyer=None), dict(sale, seq=4, buyer=''),
                dict(sale, seq=5, amount=float('nan')), dict(sale, seq=6, amount=float('inf'))]
        out = screen(rows, {'G': dict(list_price=100000, kind='goods')}, {}, {}, 100)['candidates'][0]
        self.assertEqual((out['paid'], out['buyers'], out['missing_buyer']), (4, 2, 2))
        self.assertEqual((out['within_list_clears_floor15'], out['within_list_floor15_buyers']), (1, 1))

    def test_paid_only_dedup_time_surge_and_stress(self):
        sale = dict(k='settle', seq=1, ts=100, sku='G', buyer='b', amount=90000)
        rows = [sale, sale, dict(sale, seq=2, k='result'), dict(sale, seq=3, ts=10001),
                dict(sale, seq=4, ts=1), dict(sale, seq=5, buyer='c', amount=140000)]
        cat = {'G': dict(list_price=100000, kind='goods')}
        inv = dict(surge=[dict(sku='G', factor=1.5)], units=[])
        # At 7300 a 2h window includes ts=100 but excludes ts=1.
        out = screen(rows, cat, inv, {}, 7300)['candidates'][0]
        self.assertEqual((out['paid'], out['buyers']), (2, 2))
        self.assertEqual(out['worst_unit_cost'], 112500)
        self.assertEqual((out['clears_floor15'], out['clears_floor25']), (1, 0))
        self.assertEqual(out['stock_units'], 0)

    def test_empty_and_sandbox(self):
        self.assertEqual(screen([], {}, {}, {}, 100)['candidates'], [])
        with tempfile.TemporaryDirectory() as root:
            md = Path(root)/'.market'
            md.mkdir()
            out = agent_tools.execute('market_opportunity', {'sku': 'G'}, str(md))
            self.assertNotIn('error', out)
            self.assertEqual(out['candidates'], [])
