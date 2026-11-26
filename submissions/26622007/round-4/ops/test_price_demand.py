import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import price_demand
from price_demand_cache import merge_observations


def supply(seq, ts, shop, *units, sku='egg'):
    return dict(seq=seq, ts=ts, round=4, kind='supply',
                data=dict(shop=shop, sku=sku, qty=len(units), units=list(units)))


def settle(seq, ts, unit, sku='egg'):
    return dict(seq=seq, ts=ts, round=4, kind='settle',
                data=dict(shop='https://unrelated.example/shop', sku=sku, unit=unit))


class PriceDemand(unittest.TestCase):
    def test_corrupt_public_chain_leaves_the_published_cache_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'snapshots').mkdir()
            row = dict(supply(1, 100, 'nocha12', 'unit'), prev='0' * 64, hash='0' * 64)
            (directory / 'ledger.json').write_text(json.dumps([row]))
            (directory / 'best_sellers_data.json').write_text(json.dumps(dict(last_seq=1, as_of=100, keep_hours=24, sales=[])))
            snap = dict(ts=100, shops=dict(round=4, shops=[dict(name='nocha12', stock={'egg': 1})]))
            (directory / 'snapshots' / '100.json').write_text(json.dumps(snap))
            output = directory / 'published.json'
            output.write_text('previous cache')
            result = subprocess.run([sys.executable, '-I', str(Path(__file__).with_name('price_demand_cache.py')),
                                     '--data-dir', tmp, '--output', str(output)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_text(), 'previous cache')

    def test_expiry_and_seizure_remove_only_that_shops_units(self):
        snap = dict(ts=100, shops=dict(round=4, shops=[dict(name='nocha12', stock={'egg': 1})]))
        ledger = [supply(1, 100, 'nocha12', 'own'), supply(2, 101, 'rival', 'old', 'seized'),
                  dict(seq=3, ts=102, round=4, kind='expire', data=dict(shop='rival', units=['old'], skus=['egg'])),
                  dict(seq=4, ts=103, round=4, kind='bankrupt', data=dict(shop='rival', seized_units=1)),
                  supply(5, 104, 'rival', 'fresh')]
        paid = dict(last_seq=5, as_of=104, keep_hours=24)
        self.assertEqual(merge_observations(paid, ledger, snap)['sku_stock'], {'egg': [1, 1, 1]})

    def test_sealed_sale_names_no_unit_and_leaves_the_stock_count_running(self):
        # Patch 4.1 sealed settle: {order, shop, sealed}, no unit or SKU. It stopped every stock merge from
        # 11/25 03:28 (best_sellers was built without public stock). The unit it took is unknown, so nothing is
        # removed for it; the next public shop snapshot corrects the count.
        snap = dict(ts=100, shops=dict(round=4, shops=[dict(name='nocha12', stock={'egg': 2})]))
        ledger = [supply(1, 99, 'nocha12', 'a', 'b'),
                  dict(seq=2, ts=101, round=4, kind='settle', data=dict(order='o-1', shop='https://x.example', sealed='ab' * 20)),
                  settle(3, 102, 'a')]
        paid = dict(last_seq=3, as_of=102, keep_hours=24)
        self.assertEqual(merge_observations(paid, ledger, snap)['sku_stock'], {'egg': [0, 0, 1]})
        ledger[1]['data'].pop('sealed')                                   # an open settle without its unit is still refused
        with self.assertRaises(ValueError):
            merge_observations(paid, ledger, snap)

    def test_a_shop_that_sold_sealed_can_still_go_bankrupt(self):
        # re-review 41b2dfbd I-4: the sealed sale took a unit we still count, so the seizure is one short of our count
        snap = dict(ts=100, shops=dict(round=4, shops=[dict(name='nocha12', stock={'egg': 1})]))
        ledger = [supply(1, 99, 'nocha12', 'own'), supply(2, 101, 'rival', 'a', 'b'),
                  dict(seq=3, ts=102, round=4, kind='settle', data=dict(order='o-1', shop='https://x.example', sealed='ab' * 20)),
                  dict(seq=4, ts=103, round=4, kind='bankrupt', data=dict(shop='rival', seized_units=1))]
        paid = dict(last_seq=4, as_of=103, keep_hours=24)
        self.assertEqual(merge_observations(paid, ledger, snap)['sku_stock'], {'egg': [0, 0, 1]})
        ledger[3]['data']['seized_units'] = 3                                # more than was ever supplied: still refused
        with self.assertRaises(ValueError):
            merge_observations(paid, ledger, snap)
        ledger[3]['data']['seized_units'] = -1                               # never a negative count
        with self.assertRaises(ValueError):
            merge_observations(paid, ledger, snap)
        ledger[3]['data']['seized_units'] = 0                                # two short with one sealed sale: refused
        with self.assertRaises(ValueError):
            merge_observations(paid, ledger, snap)

    def test_public_clocks_agree_through_ninety_minutes_inclusive(self):
        for snapshot_time, ledger_time in [(100, 5500), (5500, 100)]:
            ledger = [supply(1, ledger_time, 'nocha12', 'one')]
            shops = dict(round=4, shops=[dict(name='nocha12', stock={'egg': 1})])
            paid = dict(last_seq=1, as_of=5500, keep_hours=24, sales=[])
            self.assertEqual(merge_observations(paid, ledger, dict(ts=snapshot_time, shops=shops))['as_of'], 5500)
            past_boundary = snapshot_time + (0.01 if snapshot_time > ledger_time else -0.01)
            with self.assertRaises(ValueError):
                merge_observations(paid, ledger, dict(ts=past_boundary, shops=shops))

    def test_inconsistent_history_cannot_publish_stock(self):
        first = supply(1, 100, 'rival', 'unit')
        second = settle(2, 101, 'unit')
        cases = ([first, supply(2, 100, 'nocha12', 'unit')], [first, second, settle(3, 101, 'unit')],
                 [first, settle(2, 101, 'missing')], [first, settle(2, 101, 'unit', 'other')],
                 [first, dict(second, seq=3)], [dict(first, round=3), second], [first, dict(second, ts=99)],
                 [dict(first, ts=float('nan'))], [dict(first, data=dict(first['data'], qty=2))],
                 [dict(first, data=dict(first['data'], qty=True))], [dict(first, data={})],
                 [supply(1, 100, '   ', 'unit')], [supply(1, 100, 'rival', '')])
        snap = dict(ts=100, shops=dict(round=4, shops=[dict(name='nocha12', stock={})]))
        for ledger in cases:
            with self.subTest(ledger=ledger), self.assertRaises(ValueError):
                merge_observations(dict(last_seq=ledger[-1]['seq'], as_of=102, keep_hours=24), ledger, snap)

    def test_snapshot_caps_only_stock_at_or_before_snapshot(self):
        snap = dict(ts=100, shops=dict(round=4, shops=[dict(name='nocha12', stock={}),
                                                       dict(name='rival', stock={'egg': 1})]))
        ledger = [supply(1, 100, 'rival', 'old', 'expired'), supply(2, 100, 'nocha12', 'edge'),
                  supply(3, 100.1, 'nocha12', 'fresh'), settle(4, 101, 'old'),
                  supply(5, 102, 'new shop', 'new', 'gone', sku='tea'), settle(6, 102, 'gone', 'tea')]
        paid = dict(last_seq=6, as_of=102, keep_hours=24, sales=[])
        data = merge_observations(paid, ledger, snap)
        self.assertEqual(data['sku_stock'], {'egg': [0, 0, 1], 'tea': [1, 1, 0]})
        self.assertEqual((data['stock_as_of'], data['ledger_as_of'], data['as_of']), (100, 102, 102))

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
        ledger = [supply(1, now - 80, 'nocha12', 'a', 'b', 'c'),
                  supply(2, now - 80, 'first', 'd', 'e', 'f', 'expired'),
                  supply(3, now - 80, 'second', 'g', 'h', 'i', 'j', 'k'),
                  settle(4, now - 60, 'b')]
        paid = dict(as_of=now + 600, keep_hours=24, last_seq=4, sales=sales)
        shops = [{'name': name, 'stock': {'egg': units}} for name, units in
                 [('nocha12', 2), ('first', 3), ('second', 5), ('empty', 0)]]
        snap = dict(ts=now, shops=dict(round=4, shops=shops))
        data = merge_observations(paid, ledger, snap)
        self.assertEqual(data['as_of'], now)
        rate, *counts = price_demand.signals_from(data, 'egg', now)
        self.assertAlmostEqual(rate, 13 / 15)
        self.assertEqual(counts, [4, 8, 2, 2])
        self.assertEqual(price_demand.signals_from(data, 'absent', now), (0, 0, 0, 0, 0))
        shops[-1]['stock']['egg'] = 2
        skewed = merge_observations(paid, ledger, snap)
        self.assertEqual(skewed['sku_stock']['egg'], [10, 3, 2])
        with self.assertRaisesRegex(ValueError, 'ahead of ledger'):
            price_demand.signals_from(skewed, 'egg', now)
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
