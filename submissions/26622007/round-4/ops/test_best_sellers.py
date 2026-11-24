"""Offline tests for best_sellers (python3 -B -m unittest test_best_sellers, from ops/)."""
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
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
        self.data.update(stock_as_of=NOW, ledger_as_of=NOW, sku_stock={'water': [3, 2, 0]}, stock_unconfirmed=[])

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
        self.assertEqual(top['price_ceiling_args'], {'sku': 'water', 'sell': 3050, 'qty': 1, 'hold_hours': 4})

    def test_one_recent_sale_is_too_thin_to_recommend_buying(self):
        milk = next(i for i in bs.rank({'horizon_hours': 48}, self.data, now=NOW)['items'] if i['sku'] == 'milk')
        self.assertEqual(milk['tx_6h'], 1)
        self.assertEqual(milk['buy_qty'], 0)

    def test_buy_qty_never_exceeds_the_cap_even_with_odd_own_stock(self):
        out = bs.rank({'horizon_hours': 48, 'own_stock': {'water': -3}}, self.data, now=NOW)
        self.assertLessEqual(out['items'][0]['buy_qty'], bs.QTY_CAP)

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
        self.assertTrue(all(item['buy_qty'] == 0 for item in out['items']))

    def test_ledger_refresh_does_not_refresh_inventory(self):
        later = NOW + 2 * H
        data = dict(self.data, as_of=later, ledger_as_of=later)
        self.assertTrue(bs.rank({}, data, later)['stale'])

    def test_peer_and_price_replay_the_same_actual_cache_and_stock(self):
        import price_demand
        data = json.loads(bs.DATA_PATH.read_text(encoding='utf-8'))
        now = price_demand.market_as_of()
        for item in bs.rank({'top': 20}, data, now)['items']:
            if item['sku'] in data['stock_unconfirmed']:
                self.assertEqual(item['buy_qty'], 0)
                continue
            rate, count, _, competitors, own = price_demand.signals(item['sku'], now)
            self.assertEqual((item['per_hour'], item['tx_6h']), (round(rate, 2), count))
            expected = min(4, max(0, int(rate * 3 / (competitors + 1) + 0.5) - own)) if count >= 2 else 0
            self.assertEqual(item['buy_qty'], expected)

    def test_live_rows_are_merged_by_seq_once(self):
        new = [settle(11, NOW - 0.1 * H, 'milk', 12500, buyer='b9'), settle(10, NOW, 'milk', 1)]
        merged = bs.merge_live(self.data, new, now=NOW)
        self.assertEqual(merged['last_seq'], 11)
        self.assertEqual(sum(1 for r in merged['sales'] if r[1] == 'milk'), 2)

    def test_live_rows_from_a_restarted_tunnel_count_as_the_same_shop(self):
        new = [dict(seq=11, ts=NOW - 0.2 * H, kind='join', data=dict(name='s2', role='shop', url='https://s2-new/')),
               settle(12, NOW - 0.1 * H, 'water', 3000, shop='https://s2-new', buyer='b7')]
        out = bs.rank({}, bs.merge_live(self.data, new, now=NOW), now=NOW)
        self.assertEqual(out['items'][0]['shops'], 2)


MARKET, SHOP1, SHOP2, CUST = '0x' + 'm' * 40, '0x' + '1' * 40, '0x' + '2' * 40, '0x' + 'c' * 40


def chain_pay(ts, to, price, frm=CUST, tag='a'):
    """A buyer's settle transfer and its market fee, as slim chain rows (credits, 32-byte input)."""
    inp = '0x' + (tag * 64)[:64]
    return [dict(ts=ts, hash='0x' + tag + 's' + str(ts), frm=frm, to=to, credits=price - (price // 100 + 210), input=inp),
            dict(ts=ts, hash='0x' + tag + 'f' + str(ts), frm=frm, to=MARKET, credits=2 * (price // 100), input=inp)]


def book():
    return {'chain': {'market_address': MARKET}, 'shops': {SHOP1: {'name': 's1', 'url': 'https://s1'},
                                                          SHOP2: {'name': 's2', 'url': 'https://s2'}}}


class Chain(unittest.TestCase):
    def test_price_is_recovered_from_what_the_shop_received(self):
        for price in (300, 999, 2999, 3000, 11302, 499999):
            self.assertEqual(bs.price_from_transfers(price - (price // 100 + 210), 2 * (price // 100)), price)
        self.assertIsNone(bs.price_from_transfers(2760, 61))            # an odd or inconsistent fee is not a settle

    def test_a_settle_needs_its_market_fee_with_the_same_input(self):
        rows = chain_pay(NOW - 60, SHOP1, 3000) + [dict(ts=NOW - 30, hash='0xlone', frm=CUST, to=SHOP1, credits=500,
                                                         input='0x' + 'b' * 64)]
        got = bs.chain_settles(rows, MARKET, {SHOP1: 's1'})
        self.assertEqual(got, [[NOW - 60, 's1', 3000, CUST, '0xas' + str(NOW - 60)]])

    def test_a_retried_payment_with_one_fee_is_one_sale(self):
        first, fee = chain_pay(NOW - 60, SHOP1, 3000)
        retry = dict(first, ts=NOW - 50, hash='0xretry')
        fee = dict(fee, ts=NOW - 45)
        got = bs.chain_settles([first, retry, fee], MARKET, {SHOP1: 's1'})
        self.assertEqual([g[4] for g in got], ['0xretry'])

    def test_chain_sales_older_than_the_ledger_newest_row_are_refused_payments(self):
        data = bs.build_data(ledger(), CATALOG, now=NOW, chain={'book': book(), 'upto_block': 9,
                                                              'settles': [[NOW - 5 * H, SHOP1, 3000, CUST, '0xold']]})
        self.assertEqual(data['chain_only'], 0)

    def test_chain_buyers_share_the_ledger_buyer_id(self):
        b = book()
        b['customers'] = {CUST: {'name': 'b1'}}
        data = bs.build_data(ledger(), CATALOG, now=NOW, chain={'book': b, 'upto_block': 9,
                                                              'settles': [[NOW - 60, SHOP1, 3000, CUST, '0xn']]})
        self.assertEqual(data['sales'][-1][4], bs._bid('b1'))

    def test_a_chain_only_sale_seen_later_in_the_ledger_is_swapped_not_added(self):
        data = bs.build_data(ledger(), CATALOG, now=NOW, chain={'book': book(), 'upto_block': 9,
                                                              'settles': [[NOW - 60, SHOP1, 3000, CUST, '0xlate']]})
        n = len(data['sales'])
        late = settle(11, NOW - 60, 'water', 3000, buyer='b9', tx='0x')
        late['data']['tx_hash'] = '0xlate'
        merged = bs.merge_live(data, [late], NOW)
        self.assertEqual(len(merged['sales']), n)
        self.assertEqual(merged['sales'][-1][5], 0)

    def test_the_tool_chain_tail_skips_what_the_ledger_already_had(self):
        data = bs.build_data(ledger(), CATALOG, now=NOW, chain={'book': book(), 'upto_block': 9, 'settles': []})
        old = [NOW - 0.5 * H, 's1', 3000, CUST, '0xinledger']            # before the ledger's newest row
        merged, _ = bs.merge_chain(data, [old])
        self.assertEqual(len(merged['sales']), len(data['sales']))

    def test_supply_and_grant_transfers_are_not_sales(self):
        rows = [dict(ts=NOW, hash='0xsup', frm=SHOP1, to=MARKET, credits=22203, input='0x' + 'd' * 64),
                dict(ts=NOW, hash='0xgr', frm=MARKET, to=SHOP1, credits=5000000, input='0x')]
        self.assertEqual(bs.chain_settles(rows, MARKET, {SHOP1: 's1'}), [])

    def test_build_adds_chain_only_sales_with_the_sku_guessed_by_price(self):
        data = bs.build_data(ledger(), CATALOG, now=NOW, keep_hours=24, chain={
            'book': book(), 'upto_block': 900,
            'settles': [[NOW - 600, SHOP1, 3050, CUST, '0xnew1'],      # water's paid range 2800..3200: water
                        [NOW - 500, SHOP2, 99999, CUST, '0xnew2']]})   # fits no SKU: counted, not labelled
        self.assertEqual(data['source'], 'ledger+chain')
        self.assertEqual(data['chain_only'], 2)
        self.assertEqual(data['unlabelled'], 1)
        self.assertEqual(data['sales'][-1][1:3], ['water', 3050])
        self.assertEqual(data['sales'][-1][5], 2)                      # 2 = chain-only, SKU guessed
        self.assertEqual(data['chain']['next'], 901)
        self.assertEqual(data['chain']['shops'][SHOP1], 's1')

    def test_a_ledger_sale_seen_again_on_chain_is_not_counted_twice(self):
        rows = ledger()
        dup = rows[2]['data']['tx_hash']
        data = bs.build_data(rows, CATALOG, now=NOW, chain={'book': book(), 'upto_block': 1,
                                                          'settles': [[NOW - 0.5 * H, SHOP1, 3000, CUST, dup]]})
        self.assertEqual(data['chain_only'], 0)
        self.assertEqual(data['source'], 'ledger')

    def test_with_no_ledger_the_build_is_chain_only(self):
        data = bs.build_data([], CATALOG, now=NOW, chain={'book': book(), 'upto_block': 5, 'bands': {'water': [2800, 3200]},
                                                        'settles': [[NOW - 60, SHOP1, 3000, CUST, '0x1']]})
        self.assertEqual(data['source'], 'chain')
        self.assertEqual([r[1] for r in data['sales']], ['water'])
        out = bs.rank({}, data, NOW)
        self.assertEqual(out['items'][0]['tx_sku_guessed'], 1)

    def test_cli_build_reads_the_chain_rebuild_next_to_the_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / 'chain').mkdir()
            (d / 'ledger.json').write_text(json.dumps(ledger()), encoding='utf-8')
            (d / 'catalog.json').write_text(json.dumps(CATALOG), encoding='utf-8')
            (d / 'chain' / 'rebuild.json').write_text(json.dumps({'book': book(), 'upto_block': 7, 'settles': [
                [NOW + 10, SHOP1, 3000, CUST, '0xfresh']]}), encoding='utf-8')
            out = subprocess.run([sys.executable, '-B', str(OPS / 'best_sellers.py'), '--build', '--data-dir', tmp,
                                  '--output', str(d / 'o.json')], capture_output=True, text=True, check=True).stdout
            self.assertIn('chain_only=1', out)
            self.assertEqual(json.loads((d / 'o.json').read_text())['chain']['next'], 8)


class FakeKit:
    testing = False
    market = 'https://market.ourolabs.ai'


def forged(data):
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'data.json'
        out.write_text(json.dumps(data), encoding='utf-8')
        move = json.loads(subprocess.run([sys.executable, '-B', str(OPS / 'best_sellers_forge.py'), '--data', str(out)],
                                         capture_output=True, text=True, check=True).stdout)
    ns = {'__file__': bs.__file__}
    exec(compile(move['args']['logic'], 'logic.py', 'exec'), ns)
    return move, ns


class SharedCacheBuild(unittest.TestCase):
    """best_sellers.py --build writes the shared cache (paid sales + chain tail + public stock) price_demand reads."""

    def build(self, chain_sale_ts):
        sold = dict(seq=3, ts=NOW - 1 * H, round=4, kind='settle',
                    data=dict(shop='https://s1', sku='water', unit='u1', amount=3000, buyer='b1', tx_hash='0x' + 'a' * 64))
        rows = [dict(seq=1, ts=NOW - 50 * H, round=4, kind='join', data=dict(name='s1', role='shop', url='https://s1')),
                dict(seq=2, ts=NOW - 2 * H, round=4, kind='supply', data=dict(shop='s1', sku='water', qty=2,
                                                                              units=['u1', 'u2'])), sold]
        snap = dict(ts=NOW - 0.5 * H, shops=dict(round=4, shops=[dict(name='nocha12', stock={}),
                                                                 dict(name='s1', stock={'water': 1})]))
        rebuild = {'book': book(), 'upto_block': 1, 'settles': [[chain_sale_ts, SHOP1, 3000, CUST, '0xc6']]}
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / 'snapshots').mkdir()
            (d / 'chain').mkdir()
            (d / 'ledger.json').write_text(json.dumps(rows), encoding='utf-8')
            (d / 'catalog.json').write_text(json.dumps(CATALOG), encoding='utf-8')
            (d / 'snapshots' / '1.json').write_text(json.dumps(snap), encoding='utf-8')
            (d / 'chain' / 'rebuild.json').write_text(json.dumps(rebuild), encoding='utf-8')
            subprocess.run([sys.executable, '-B', str(OPS / 'best_sellers.py'), '--build', '--data-dir', tmp,
                            '--output', str(d / 'out.json')], capture_output=True, text=True, check=True)
            return json.loads((d / 'out.json').read_text(encoding='utf-8'))

    def test_build_carries_public_stock_and_the_chain_tail(self):
        data = self.build(NOW - 0.7 * H)                       # chain sale before the snapshot: stock already shows it
        self.assertEqual((data['as_of'], data['stock_as_of'], data['ledger_as_of']), (NOW - 0.5 * H, NOW - 0.5 * H, NOW - H))
        self.assertEqual(data['sku_stock'], {'water': [1, 1, 0]})
        self.assertEqual(data['stock_unconfirmed'], [])
        self.assertEqual(data['source'], 'ledger+chain')
        self.assertEqual(sum(1 for r in data['sales'] if r[5] == bs.CHAIN_ONLY), 1)

    def test_a_chain_sale_after_the_stock_snapshot_leaves_that_sku_unconfirmed(self):
        self.assertEqual(self.build(NOW - 0.2 * H)['stock_unconfirmed'], ['water'])


class Live(unittest.TestCase):
    def setUp(self):
        self.move, self.ns = forged(bs.build_data(ledger(), CATALOG, now=NOW, chain={'book': book(), 'upto_block': 1,
                                                                             'settles': []}))
        self.real_chain = self.ns['_chain']
        self.ns['_chain'] = lambda *a: ([], 0)                 # never the network in unit tests

    def test_a_truncated_tail_is_flagged_and_dated_by_its_newest_row(self):
        rows = [settle(11, NOW + 60, 'water', 3000, buyer='b8')]
        self.ns['_live'] = lambda kit, data, deadline: (rows, True)
        out = self.ns['run']({}, FakeKit())
        self.assertTrue(out['live_truncated'])
        self.assertTrue(out['stale'])
        self.assertTrue(all(item['buy_qty'] == 0 for item in out['items']))
        self.assertEqual(out['as_of'], int(NOW + 60))
        self.assertEqual(out['items'][0]['tx'], 5)

    def test_a_failed_live_read_falls_back_to_built_data(self):
        def boom(*a):
            raise OSError('down')
        self.ns['_live'] = boom
        with patch('time.time', return_value=NOW):
            out = self.ns['run']({}, FakeKit())
        self.assertIn('live_error', out)
        self.assertEqual(out['items'][0]['sku'], 'water')
        self.assertEqual(out['source'], 'chain')

    def test_an_empty_ledger_answer_switches_to_the_chain_and_says_so(self):
        self.ns['_live'] = lambda *a: (_ for _ in ()).throw(RuntimeError('ledger answered nothing'))
        self.ns['_chain'] = lambda *a: ([[NOW - 30, 's1', 3000, CUST, '0xc1']], 0)
        with patch('time.time', return_value=NOW):              # the clock at the build (rows after now are skipped)
            out = self.ns['run']({}, FakeKit())
        self.assertEqual(out['source'], 'chain')
        self.assertTrue(out['ledger_down'])
        self.assertEqual(out['items'][0]['tx'], 5)

    def test_when_both_paths_fail_the_built_data_answers(self):
        def boom(*a):
            raise OSError('down')
        self.ns['_live'] = boom
        self.ns['_chain'] = boom
        out = self.ns['run']({}, FakeKit())
        self.assertEqual(out['source'], 'built')
        self.assertIn('chain_error', out)

    def test_a_chain_gap_keeps_the_answer_dated_by_the_build(self):
        self.ns['_live'] = lambda *a: (_ for _ in ()).throw(OSError('down'))
        self.ns['_chain'] = lambda *a: ([[NOW - 30, 's1', 3000, CUST, '0xc9']], 5000)
        with patch('time.time', return_value=NOW):
            out = self.ns['run']({}, FakeKit())
        self.assertEqual(out['chain_gap_blocks'], 5000)
        self.assertEqual(out['as_of'], int(NOW))                # dated by the build: blocks in between were not read
        self.assertEqual(out['items'][0]['tx'], 5)              # but the tail's sale is counted

    def test_a_reset_ledger_is_caught_by_the_last_row_hash(self):
        class R:
            def __init__(self, rows):
                self.rows = rows

            def raise_for_status(self):
                pass

            def json(self):
                return self.rows

        class FakeHttpx:
            Timeout = staticmethod(lambda t, connect=None: t)
            rows = []

            @staticmethod
            def get(url, params=None, timeout=None):
                return R(FakeHttpx.rows)
        sys.modules['httpx'] = FakeHttpx
        try:
            data = dict(self.ns['DATA'], last_seq=10, last_hash='h10')
            FakeHttpx.rows = [dict(seq=10, hash='other'), dict(seq=11, hash='h11')]
            with self.assertRaises(RuntimeError):
                self.ns['_live'](FakeKit(), data, __import__('time').time() + 20)
            FakeHttpx.rows = []
            with self.assertRaises(RuntimeError):
                self.ns['_live'](FakeKit(), data, __import__('time').time() + 20)
            FakeHttpx.rows = [dict(seq=10, hash='h10'), dict(seq=11, hash='h11')]
            rows, cut = self.ns['_live'](FakeKit(), data, __import__('time').time() + 20)
            self.assertEqual(([r['seq'] for r in rows], cut), ([11], False))
            rows, cut = self.ns['_live'](FakeKit(), data, __import__('time').time() + 1)   # no time left
            self.assertEqual((rows, cut), ([], True))
        finally:
            del sys.modules['httpx']

    def test_the_chain_reader_stays_inside_its_time_budget_and_reports_the_gap(self):
        calls = []

        class R:
            def __init__(self, out):
                self.out = out

            def raise_for_status(self):
                pass

            def json(self):
                return self.out

        class FakeHttpx:
            Timeout = staticmethod(lambda t, connect=None: t)

            @staticmethod
            def post(url, json=None, timeout=None):
                calls.append(len(json))
                if json[0]['method'] == 'eth_blockNumber':
                    return R([{'id': 0, 'result': hex(1000)}])
                return R([{'id': c['id'], 'result': {'timestamp': hex(5), 'transactions': []}} for c in json])
        sys.modules['httpx'] = FakeHttpx
        try:
            data = dict(self.ns['DATA'], chain=dict(self.ns['DATA']['chain'], next=10))
            settles, gap = self.real_chain(FakeKit(), data, __import__('time').time() + 20)
            self.assertEqual(settles, [])
            self.assertEqual(gap, 1000 - 75 + 1 - 10)          # blocks 10..925 were not read
            self.assertEqual(calls, [1, 25, 25, 25])
        finally:
            del sys.modules['httpx']

    def test_live_must_be_a_bool(self):
        with self.assertRaises(ValueError):
            self.ns['run']({'live': 'false'}, FakeKit())

    def test_expiry_and_seizure_disable_live_quantity_until_inventory_refresh(self):
        for kind in ('expire', 'bankrupt'):
            self.ns['DATA'].update(stock_as_of=NOW, ledger_as_of=NOW, sku_stock={}, stock_unconfirmed=[])
            self.ns['_live'] = lambda *a: ([dict(seq=11, kind=kind, ts=NOW)], False)
            with self.subTest(kind=kind), patch('time.time', return_value=NOW):
                out = self.ns['run']({}, FakeKit())
                self.assertTrue(out['stale'])
                self.assertTrue(all(item['buy_qty'] == 0 for item in out['items']))


    def test_chain_fallback_sales_hold_quantity_until_inventory_refresh(self):
        self.ns['DATA'].update(stock_as_of=NOW, ledger_as_of=NOW, sku_stock={'water': [3, 2, 0]}, stock_unconfirmed=[])
        self.ns['_live'] = lambda *a: (_ for _ in ()).throw(OSError('down'))
        for settles, gap, held in (([], 0, False), ([[NOW - 30, 's1', 3000, CUST, '0xc5']], 0, True), ([], 7, True)):
            self.ns['_chain'] = lambda *a: (settles, gap)
            with self.subTest(settles=len(settles), gap=gap), patch('time.time', return_value=NOW):
                out = self.ns['run']({}, FakeKit())
                self.assertEqual(out['source'], 'chain')
                self.assertEqual(out['stale'], held)
                if held:
                    self.assertTrue(all(item['buy_qty'] == 0 for item in out['items']))


class Forge(unittest.TestCase):
    def test_a_big_sales_history_is_trimmed_to_keep_the_tool_readable(self):
        rows = ledger() + [settle(100 + i, NOW - (i % 20) * 60, 'water', 3000, buyer='x%d' % i) for i in range(3000)]
        move, ns = forged(bs.build_data(rows, CATALOG, now=NOW))
        self.assertLess(len(move['args']['logic']), 40_000)
        self.assertIn('trimmed', ns['DATA'])
        self.assertNotIn('buyers', ns['DATA'].get('chain', {}))

    def test_a_catalog_too_big_even_with_few_sales_fails_instead_of_looping(self):
        data = bs.build_data(ledger(), CATALOG, now=NOW)
        data['url_names'] = {'https://s%d.example' % i: 'n' * 40 for i in range(1000)}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'data.json'
            out.write_text(json.dumps(data), encoding='utf-8')
            run = subprocess.run([sys.executable, '-B', str(OPS / 'best_sellers_forge.py'), '--data', str(out)],
                                 capture_output=True, text=True, timeout=60)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('even with no sales', run.stderr)

    def test_the_shared_stock_part_is_not_embedded(self):
        data = bs.build_data(ledger(), CATALOG, now=NOW)
        data.update(sku_stock={'water': [1, 1]}, stock_as_of=NOW, stock_unconfirmed=[])
        move, ns = forged(data)
        for k in ('sku_stock', 'stock_as_of', 'stock_unconfirmed'):
            self.assertNotIn(k, ns['DATA'])

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
        self.assertEqual(args['permission']['network'], ['market.ourolabs.ai', 'rpc-testnet.maroo.io'])
        for word in ('pay(', 'sign(', 'sign_jwt(', 'token(', 'open(', 'write(', '.put(', '.delete(',
                     'kit.pay', 'kit.sign', 'kit.token', 'kit.address', 'eth_send', 'personal_'):
            self.assertNotIn(word, args['logic'])
        # the one POST is the read-only JSON-RPC call to the chain
        self.assertEqual(args['logic'].count('.post('), 1)
        self.assertIn("httpx.post(RPC,", args['logic'])
        ns = {'__file__': bs.__file__}
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
