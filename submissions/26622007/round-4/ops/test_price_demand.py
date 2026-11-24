import unittest

import price_demand
from price_demand_cache import merge_observations


class PriceDemand(unittest.TestCase):
    def test_replay_clock_is_the_observation_not_the_wall_clock(self):
        observed = price_demand.market_as_of()
        for when in (observed - 1, observed + 24 * 3600, float('nan'), float('inf'), True):
            with self.subTest(when=when), self.assertRaises(ValueError):
                price_demand.signals('G00b8acf0', when)
        readings = price_demand.signals('G00b8acf0', observed)
        self.assertIsInstance(readings, tuple)
        self.assertEqual(len(readings), 5)
        self.assertEqual(readings, price_demand.signals('G00b8acf0', observed))

    def test_paid_windows_and_competing_shops_are_independent(self):
        now = 100000
        ages = [0, 7200, 7201, 21600, 21601, -1]
        sales = [[now - age, 'egg', 1000, 0, 1, 1, seq] for seq, age in enumerate(ages, 1)]
        sales.append([now, 'egg', 1000, 0, 1, -1, 7])
        paid = dict(as_of=now + 600, keep_hours=24, last_seq=10, sales=sales)
        ledger = [dict(round=4, seq=10, ts=now - 60)]
        shops = [{'name': name, 'stock': {'egg': units}} for name, units in
                 [('nocha12', 2), ('first', 3), ('second', 5), ('empty', 0)]]
        snap = dict(ts=now, shops=dict(round=4, shops=shops))
        data = merge_observations(paid, ledger, snap)
        self.assertEqual(data['as_of'], now)
        rate, *counts = price_demand.signals_from(data, 'egg', now)
        self.assertAlmostEqual(rate, 13 / 15)
        self.assertEqual(counts, [4, 8, 2, 2])
        self.assertEqual(price_demand.signals_from(data, 'absent', now), (0, 0, 0, 0, 0))
        with self.assertRaises(ValueError):
            price_demand.signals_from(data, 'egg', now + 5400)
        for bad in [dict(data, sku_stock=None), dict(data, sku_stock={'egg': [1, 2, 0]}),
                    dict(data, stock_as_of=None), dict(data, keep_hours=2)]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                price_demand.signals_from(bad, 'egg', now)
        with self.assertRaises(ValueError):
            merge_observations(paid, ledger, dict(snap, shops=dict(round=4, shops=shops[1:])))
        with self.assertRaises(ValueError):
            merge_observations(dict(paid, last_seq=9), ledger, snap)
