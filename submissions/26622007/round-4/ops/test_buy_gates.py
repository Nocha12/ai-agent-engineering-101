"""Buy gates outside the owner's minimum list (대표님 11/25 02:25 list, 11/26 09:3x "병신같은 가드 넣지 말라고",
11/26 09:35 "라운드 종료시간은 고려하지마"); audit review/W-20261124-1650-AXRound4준비/purchase-gate-audit-1127.md D-1..D-5.
Allowed: class kit limits, food only, expected sale > cost + fees, 3 items a window / 4 each, same-quote re-confirm.
Market facts (supply window closed, 429, balance) are not gates.
Run from ops/: ~/ax-round4-venv/bin/python -m unittest -q test_buy_gates"""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def goal():
    return json.loads((HERE.parent / 'agent.json').read_text(encoding='utf-8'))['goal']


class GoalHasNoStockOrEndGate(unittest.TestCase):
    """D-2, D-3: goal sentences that act as a total-stock cap or an end-time rule."""

    def test_no_stock_cap_sentences(self):
        g = goal()
        # 11/27 17:30 (#13855): the per-SKU refill (cover - held of that SKU; cover = 6 h of our 24 h sales since 19:1x) is back as a judgment; a total-stock cap stays out
        for s in ('재고만 쌓아 두거나', '유통기한 안에 팔 수 있는 양만 산다', '유통기한 안에 팔 수 있는 양까지', '재고 총개수가',
                  '총재고'):
            self.assertNotIn(s, g)

    def test_no_end_time_rules(self):
        g = goal()
        for s in ('종료 24시간', '24시간 전부터', '6시간 전부터', '마지막 6시간', '종료 시각을 알게 되면',
                  '종료 시각은 /rules', '종료 시각이 공지되지 않았으면', "notes의 'R4 종료 시각' 줄"):
            self.assertNotIn(s, g)

    def test_food_is_not_six_words(self):
        self.assertNotIn('식품 필수품(생수, 우유, 쌀, 계란, 면, 과일)만 둔다', goal())

    def test_owner_minimum_list_kept(self):
        g = goal()
        for s in ('품목당 최대 4개', '한 창 최대 3품목', '후보는 식품뿐', '실제로 팔리는 값으로 팔아도 남지 않으면 사지 않는다',
                  '손님이 찾은 품목은 다른 규격 재고가 있어도 그것만으로 매입을 멈추지 않는다', '있는 품목을 바로 산다'):
            self.assertIn(s, g)

    def test_buy_follows_demand(self):
        """11/27 17:30 대표님 #13855 '제대로 해 파산하겠다': 16:06~17:30 매입 203,438 vs 판매 15,516. The goal told the shop
        to ignore sales speed and stock and to fill 3 items a window; it bought for one ask 2~4 lots and market best
        sellers it already held. Now: one unit per customer who asked, refill = cover (our next 6 h of 24 h sales) - held per SKU, the
        3 x 4 is a ceiling, a window without demand is skipped. No amount cap, no total-stock cap, no end time."""
        g = goal()
        self.assertNotIn('판매 속도와 지금 가진 재고는 참고만 하고 매입을 멈추거나 줄이는 이유로 쓰지 않는다', g)
        # review I2: no push to spend every sale on buying again
        self.assertNotIn('판 돈은 바로 다시 매입에 돌려 회전시킨다', g)
        self.assertIn('공급 창이 열리면 근거(손님 문의·우리 판매) 있는 품목을 바로 산다', g)
        for s in ('채울 목표가 아니다', '근거 있는 품목이 없으면 그 창은 사지 않고', '1명 1개, 2명 2개, 3명 이상 3~4개',
                  'food_candidates refill의 qty', '1개만 시험으로', '일반 두부를 찾은 손님에게 멸균 실큰두부'):
            self.assertIn(s, g)
        self.assertNotIn('객단가 1만 이상(우유 묶음, 쌀, 계란 30구 이상 등)을 우선한다', g)
        for s in ('50만', '500,000', '현금 하한', '일일 상한'):
            self.assertNotIn(s, g)


class BestSellersDoesNotStopBuying(unittest.TestCase):
    """D-1: best_sellers buy_qty was 0 whenever data was stale and its note said 0이면 사지 않음."""

    def setUp(self):
        import best_sellers as bs
        from test_best_sellers import ledger, CATALOG, NOW, H
        self.bs, self.NOW, self.H = bs, NOW, H
        self.data = bs.build_data(ledger(), CATALOG, now=NOW, keep_hours=24)
        self.data.update(stock_as_of=NOW, ledger_as_of=NOW, sku_stock={'water': [3, 2, 0]}, stock_unconfirmed=[])

    def test_note_and_description_do_not_say_zero_means_no_buy(self):
        out = self.bs.rank({}, self.data, now=self.NOW)
        self.assertNotIn('사지 않', out['note'])
        self.assertIn('참고', out['note'])
        self.assertIn('매입을 막는 이유가 아니', out['note'])
        forge_src = (HERE / 'best_sellers_forge.py').read_text(encoding='utf-8')
        self.assertNotIn('0이면 사지', forge_src)
        self.assertNotIn('0이면 사지', (HERE / 'best_sellers.py').read_text(encoding='utf-8'))

    def test_stale_data_still_gives_a_reference_quantity(self):
        stale = dict(self.data, stock_as_of=self.NOW - 30 * self.H)      # old stock snapshot: stale
        out = self.bs.rank({}, stale, now=self.NOW)
        self.assertTrue(out['stale'])
        water = out['items'][0]
        self.assertEqual(water['sku'], 'water')
        self.assertGreater(water['buy_qty'], 0)                      # not forced to 0
        self.assertLessEqual(water['buy_qty'], 4)
        self.assertEqual(water['buy_qty_basis'], 'no_stock_data')

    def test_unconfirmed_stock_still_gives_a_reference_quantity(self):
        data = dict(self.data, stock_unconfirmed=['water'])
        water = self.bs.rank({}, data, now=self.NOW)['items'][0]
        self.assertGreater(water['buy_qty'], 0)
        self.assertEqual(water['buy_qty_basis'], 'no_stock_data')

    def test_fresh_data_quantity_unchanged(self):
        water = self.bs.rank({'horizon_hours': 4, 'own_stock': {'water': 1}}, self.data, now=self.NOW)['items'][0]
        self.assertEqual(water['buy_qty'], 1)                         # same as test_best_sellers
        self.assertEqual(water['buy_qty_basis'], 'stock')

    def test_real_cache_forges_under_the_size_limit(self):
        import runpy
        move = runpy.run_path(str(HERE / 'best_sellers_forge.py'))['forge_move'](str(HERE / 'best_sellers_data.json'))
        logic = move['args']['logic']
        self.assertLess(len(logic), 40_000)
        self.assertNotIn('0이면 사지', logic + move['args']['description'])
        compile(logic, 'logic.py', 'exec')


class BuyFoodLotHasNoOwnPayCap(unittest.TestCase):
    """D-5: 1회 50만 was our own permission.pay_limit (class kit: DEFAULT_PAY_LIMIT 200,000 "unless permission.pay_limit
    says otherwise"; Round 4 has no hard_limits file). It goes; the kit still needs a number, so it is the 5,000,000
    starting balance and the balance check (a market fact) is what bounds a lot. The 1 h expiry refusal stays."""

    def logic(self):
        return (HERE / 'tools_src/buy_food_lot/logic.py').read_text(encoding='utf-8')

    def test_no_500k_refusal(self):
        self.assertNotIn('500000 * 10 ** 15', self.logic())
        self.assertNotIn('500,000 tool limit', self.logic())

    def test_permission_pay_limit_is_the_starting_balance(self):
        import install_tools
        self.assertEqual(install_tools.TOOLS['buy_food_lot'][1]['pay_limit'], 5000000)
        self.assertIn('PAY_LIMIT = 5000000', self.logic())

    def test_one_hour_expiry_refusal_kept(self):
        self.assertIn('HOLD_HOURS = 1 ', self.logic())
        self.assertIn('a unit expires within', self.logic())


class FoodCandidatesAreAllFood(unittest.TestCase):
    """D-4: candidates were only foods whose name/category had one of six words and list price > 3,000."""

    def test_no_word_or_price_filter(self):
        src = (HERE / 'tools_src/food_candidates/logic.py').read_text(encoding='utf-8')
        self.assertNotIn('if not any(w in text for w in wanted): continue', src)
        self.assertNotIn('p <= 3000', src)


class StaleSalesDataDoesNotStopAllowedBuys(unittest.TestCase):
    """감독 11/27 16:00 ax-purchase-guard-recurrence: with old or missing sales data, a buy the owner's list allows
    (customer-asked food) must still be offered, and buy_food_lot's break-even ceiling must stay. Old data may only
    leave refill empty; it never removes an asked item or changes its qty."""
    H = 3600

    def food_candidates(self, sales_path):
        from runpy import run_path
        g = run_path(str(HERE / 'tools_src/food_candidates/logic.py'))['run'].__globals__   # run_path returns a copy
        now = g['_clock']()
        cat = [{'sku': 'G-ramen', 'kind': 'food', 'name': '농심 신라면 120g 5개입', 'category': '식품>면>라면',
                'list_price': 4500, 'shelf_hours': 400},
               {'sku': 'G-egg', 'kind': 'food', 'name': '무항생제 계란 20구', 'category': '식품>계란',
                'list_price': 12000, 'shelf_hours': 168}]
        inv = {'balance': 2900000, 'restock': {'open': True}, 'units': [{'unit': 'u1', 'sku': 'G-egg'}]}
        lost = [{'ts': now - 600, 'customer': 'a', 'item': '라면', 'reason': '재고없음', 'grade_stock': '재고 0'},
                {'ts': now - 300, 'customer': 'b', 'item': '라면', 'reason': '재고없음', 'grade_stock': '재고 0'}]
        g['fetch'] = lambda base, token: (cat, {'surge': []}, inv)
        g['read_lost'] = lambda path=None: lost
        g['market_sold'] = lambda path=None: {}                        # market sales cache missing too
        real = g['_own_sold_real']
        g['own_sold'] = lambda path=None, now=None: real(sales_path, now)

        class K:
            market, testing = 'https://market.example', True
            def token(self, role): return 'offline'
        return g['run']({}, K()), now

    def check_asked_still_offered(self, r, refill=()):
        asked = r['customer_asked_out_of_stock']
        self.assertIsInstance(asked, list, asked)
        ramen = [e for e in asked if 'G-ramen' in [o.get('sku') for o in e.get('buy_options') or []]]
        self.assertEqual(len(ramen), 1, asked)
        self.assertEqual(ramen[0].get('customers'), 2, ramen[0])                    # 2 customers -> 2, as with fresh data
        self.assertEqual([x['sku'] for x in r['refill']], list(refill))          # only fresh rows count; old ones are no stop
        self.assertIn('표시는 매입을 막지 않는다', r['note'])
        self.assertTrue({'G-ramen', 'G-egg'} <= {x['sku'] for x in r['candidates']}, r['candidates'])
        for s in ('stale', '판매 자료', '오래', '갱신 전'):
            self.assertNotIn(s, r['note'])

    def test_old_sales_file_keeps_asked_buys(self):
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix='.jsonl')
        try:
            now = __import__('time').time()
            with os.fdopen(fd, 'w', encoding='utf-8') as f:                   # last sale 30 h ago: older than the window
                for i in range(5):
                    f.write(json.dumps({'ts': now - 30 * self.H - i, 'sku': 'G-egg', 'unit': 'old%d' % i}) + '\n')
            r, _ = self.food_candidates(path)
            self.check_asked_still_offered(r)
            with open(path, 'a', encoding='utf-8') as f:            # control: the file is the one read; a fresh sale counts
                for i in range(4):
                    f.write(json.dumps({'ts': now - self.H - i, 'sku': 'G-egg', 'unit': 'new%d' % i}) + '\n')
            r, _ = self.food_candidates(path)
            self.check_asked_still_offered(r, refill=['G-egg'])
            self.assertEqual(r['refill'][0]['sold_24h'], 4, r['refill'])
        finally:
            os.unlink(path)

    def test_missing_sales_file_keeps_asked_buys(self):
        r, _ = self.food_candidates(str(HERE / 'no-such-sales.jsonl'))
        self.check_asked_still_offered(r)

    def test_buy_food_lot_ceiling_ignores_stale_demand(self):
        """buy_food_lot asks price_ceiling with unsold=0, so a missing or expired demand cache (price_demand) is never
        read: the break-even ceiling stays and no '갱신 전 거래 금지' refusal comes back."""
        import os, time
        from runpy import run_path
        cwd = os.getcwd()
        os.chdir(HERE.parent)                                    # buy_food_lot reads ops/price_ceiling.py from the round dir
        try:
            g = run_path(str(HERE / 'tools_src/buy_food_lot/logic.py'))['break_even'].__globals__
            real_run_path = g['run_path']
            calls, loaded = [], []

            def stale_demand():
                calls.append(1)
                raise OSError('demand cache expired')

            def run_path_stale(path, *a, **k):
                mod = real_run_path(path, *a, **k)
                if path.endswith('price_ceiling.py'):
                    loaded.append(path)
                    mod['quote'].__globals__['_demand_module'] = stale_demand
                return mod
            g['run_path'] = run_path_stale
            pc = real_run_path(str(HERE / 'price_ceiling.py'))
            sku, item = max(((s, v) for s, v in pc['DATA']['items'].items()
                             if v[1] == 'food' and v[5] and v[3] and v[3] > 48), key=lambda sv: (sv[1][5], sv[0]))
            now = time.time()
            goods = int(item[5] * 0.3) * 2                       # 2 units at 30% of the median paid price
            quote = {'goods': goods, 'storage': 0, 'forum_fees': 0,
                     'units': [{'expires_at': now + 40 * self.H}, {'expires_at': now + 40 * self.H}]}
            ceiling, source = g['break_even'](sku, 2, quote, item[2], now)
            self.assertEqual(source, 'price_ceiling')
            self.assertGreater(ceiling, goods)
            self.assertTrue(loaded)                              # break_even went through the patched loader
            self.assertEqual(calls, [])                          # the demand cache was never consulted
        finally:
            os.chdir(cwd)

    def test_goal_says_stale_is_not_a_stop(self):
        g = goal()
        self.assertIn('best_sellers의 stale·live_error·buy_qty는 매입을 멈추는 이유로 쓰지 않는다', g)
        self.assertIn('재고 총개수와 라운드 종료 시각은 판단 조건이 아니다', g)


if __name__ == '__main__':
    unittest.main()
