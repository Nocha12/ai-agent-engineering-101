"""Offline tests for best_sellers (python3 -B -m unittest test_best_sellers, from ops/)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import best_sellers as bs

OPS = Path(__file__).resolve().parent
H = 3600.0
NOW = 1_800_000_000.0


def settle(seq, ts, sku, amount, shop='https://s1', buyer='b1', tx='0xaa'):
    return dict(seq=seq, ts=ts, kind='settle', data=dict(shop=shop, sku=sku, item=sku, amount=amount, buyer=buyer,
                                                         name='n-' + sku, shop_fee=amount // 100 + 210,
                                                         tx_hash=tx + format(seq, '062x')))


CATALOG = [dict(sku='water', name='생수', kind='food', list_price=5000, shelf_hours=720),
           dict(sku='milk', name='우유', kind='food', list_price=20000, shelf_hours=240),
           dict(sku='sofa', name='소파', kind='goods', list_price=500000, shelf_hours=None)]


def ledger():
    rows = [dict(seq=1, ts=NOW - 50 * H, kind='join', data=dict(name='s1', role='shop', url='https://s1')),
            dict(seq=2, ts=NOW - 50 * H, kind='join', data=dict(name='s2', role='shop', url='https://s2'))]
    # water: 4 recent sales at two shops, one buyer twice
    rows += [settle(3, NOW - 0.5 * H, 'water', 3000, buyer='b1'), settle(4, NOW - 1.0 * H, 'water', 3200, buyer='b1'),
             settle(5, NOW - 1.5 * H, 'water', 2800, shop='https://s2', buyer='b2'),
             settle(6, NOW - 3.0 * H, 'water', 3100, buyer='b3')]
    # milk: one recent sale, two old ones outside the window
    rows += [settle(7, NOW - 1.0 * H, 'milk', 12000), settle(8, NOW - 30 * H, 'milk', 11000),
             settle(9, NOW - 31 * H, 'milk', 11500)]
    # a settle without a tx hash is not counted as money that moved
    bad = settle(10, NOW - 0.2 * H, 'sofa', 300000)
    bad['data'].pop('tx_hash')
    rows.append(bad)
    return rows


class Build(unittest.TestCase):
    def test_only_paid_settles_inside_the_kept_window(self):
        data = bs.build_data(ledger(), CATALOG, now=NOW, keep_hours=24)
        skus = sorted({r[1] for r in data['sales']})
        self.assertEqual(skus, ['milk', 'water'])               # sofa had no tx, old milk is outside 24 h
        self.assertEqual(len(data['sales']), 5)
        self.assertEqual(data['last_seq'], 10)
        self.assertEqual(data['items']['water'][:3], ['생수', 'food', 5000])
        self.assertNotIn('b1', json.dumps(data))                # buyers are kept as short ids only

    def test_tx_check_marks_mismatched_amounts(self):
        rows = ledger()
        txs = {rows[2]['data']['tx_hash']: {'credits': 3000 - 240}, rows[3]['data']['tx_hash']: {'credits': 1}}
        data = bs.build_data(rows, CATALOG, now=NOW, keep_hours=24, txs=txs)
        flags = [r[5] for r in data['sales']]                    # rows in ledger order
        self.assertEqual(flags[0], 1)                            # 3000 - (30 + 210) matches the chain
        self.assertEqual(flags[1], -1)                           # 1 credit on chain: mismatch
        self.assertEqual(flags[2], 0)                            # not checked yet
        out = bs.rank({}, data, now=NOW)
        self.assertEqual(out['items'][0]['tx'], 3)               # a mismatched sale is not evidence
        self.assertEqual(out['items'][0]['tx_on_chain'], 1)


class Rank(unittest.TestCase):
    def setUp(self):
        self.data = bs.build_data(ledger(), CATALOG, now=NOW, keep_hours=24)

    def test_water_tops_the_list_with_velocity_prices_and_evidence(self):
        out = bs.rank({}, self.data, now=NOW)
        top = out['items'][0]
        self.assertEqual(top['sku'], 'water')
        self.assertEqual(top['tx'], 4)
        self.assertEqual(top['shops'], 2)
        self.assertEqual(top['repeat_buyers'], 1)
        self.assertEqual(top['price'], [2800, 3050, 3200])      # min, median, max of real paid prices
        self.assertAlmostEqual(top['price_vs_list'], 0.61)
        # 3 sales in the last 2 h, 4 in 6 h: 0.6 * 1.5 + 0.4 * 0.667
        self.assertAlmostEqual(top['per_hour'], 1.17, places=2)

    def test_buy_qty_shares_demand_and_subtracts_own_stock(self):
        out = bs.rank({'horizon_hours': 4, 'own_stock': {'water': 1}}, self.data, now=NOW)
        top = out['items'][0]
        # 1.17/h * 4 h shared with 2 selling shops + us = 1.56 -> 2, minus 1 in stock
        self.assertEqual(top['buy_qty'], 1)
        self.assertEqual(top['price_ceiling_args'], {'sku': 'water', 'sell': 3050, 'qty': 1})

    def test_one_recent_sale_is_too_thin_to_recommend_buying(self):
        milk = next(i for i in bs.rank({'horizon_hours': 48}, self.data, now=NOW)['items'] if i['sku'] == 'milk')
        self.assertEqual(milk['tx_6h'], 1)
        self.assertEqual(milk['buy_qty'], 0)

    def test_min_tx_and_kind_filters_and_bad_input(self):
        self.assertEqual([i['sku'] for i in bs.rank({'min_tx': 2}, self.data, now=NOW)['items']], ['water'])
        self.assertEqual(bs.rank({'kind': 'goods'}, self.data, now=NOW)['items'], [])
        with self.assertRaises(ValueError):
            bs.rank({'top': 'x'}, self.data, now=NOW)
        with self.assertRaises(ValueError):
            bs.rank({'wallet': 1}, self.data, now=NOW)

    def test_stale_data_is_reported(self):
        out = bs.rank({}, self.data, now=NOW + 5 * H)
        self.assertEqual(out['data_age_min'], 300)
        self.assertTrue(out['stale'])

    def test_live_rows_are_merged_by_seq_once(self):
        new = [settle(11, NOW - 0.1 * H, 'milk', 12500, buyer='b9'), settle(10, NOW, 'milk', 1)]
        merged = bs.merge_live(self.data, new, now=NOW)
        self.assertEqual(merged['last_seq'], 11)
        self.assertEqual(sum(1 for r in merged['sales'] if r[1] == 'milk'), 2)


class Forge(unittest.TestCase):
    def test_forge_move_is_read_only_and_its_test_passes_in_a_sandbox_like_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'data.json'
            out.write_text(json.dumps(bs.build_data(ledger(), CATALOG, now=NOW)), encoding='utf-8')
            move = json.loads(subprocess.run([sys.executable, '-B', str(OPS / 'best_sellers_forge.py'), '--data', str(out)],
                                             capture_output=True, text=True, check=True).stdout)
        self.assertEqual(move['action'], 'forge_tool')
        args = move['args']
        self.assertEqual(args['name'], 'best_sellers')
        self.assertEqual(set(args['permission']), {'network', 'timeout'})
        self.assertEqual(args['permission']['network'], ['market.ourolabs.ai'])
        for word in ('pay', 'sign', 'token', 'open(', 'write'):
            self.assertNotIn(word + '(' if word != 'open(' else word, args['logic'])
        ns = {}
        exec(compile(args['logic'], 'logic.py', 'exec'), ns)
        exec(compile(args['test'], 'test.py', 'exec'), ns)

        class Kit:
            testing = True
            market = 'https://market.ourolabs.ai'
        ns['test'](ns['run'], Kit())
        result = ns['run']({'top': 1}, Kit())
        self.assertEqual(result['items'][0]['sku'], 'water')
        self.assertLess(len(json.dumps(result, ensure_ascii=False)), 1500)


if __name__ == '__main__':
    unittest.main()
