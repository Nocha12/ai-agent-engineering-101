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
        for s in ('재고만 쌓아 두거나', '유통기한 안에 팔 수 있는 양만 산다', '지금 가진 그 품목 재고를 뺀다',
                  '유통기한 안에 팔 수 있는 양까지'):
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
                  '이미 가진 재고가 있어도 그것만으로 매입을 멈추지 않는다', '공급 창이 열리면 바로 산다'):
            self.assertIn(s, g)

    def test_stock_and_speed_are_reference_only(self):
        self.assertIn('판매 속도와 지금 가진 재고는 참고만 하고 매입을 멈추거나 줄이는 이유로 쓰지 않는다', goal())


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


if __name__ == '__main__':
    unittest.main()
