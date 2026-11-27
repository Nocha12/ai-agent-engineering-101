"""Tests for ops/forum_menu.py and its hook in agent_kit_records.py, offline (no market, no forum, no wallet).
Run: python3 -I ops/test_forum_menu.py  (from the round-4 folder)"""
import json
import time
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import forum_menu as fm  # noqa: E402

LIVE = HERE.parent / 'tools' / 'restock_shoutout' / 'logic.py'   # what the hook loads in the shop
QB_PATH = LIVE if LIVE.exists() else HERE / 'restock_shoutout' / 'logic.py'
QB = fm.load_quote_block(QB_PATH)
NOW = 1_800_000_000.0


def unit(u, sku, name, grade, cost, exp=NOW + 86400):
    return {'unit': u, 'sku': sku, 'name': name, 'grade': grade, 'cost': cost, 'kind': 'food', 'expires_at': exp}


EGG, WATER, TEA, RICE = 'Gab7c8b3d', 'G00b8acf0', 'G0746760c', 'G3c4127d8'
INV = {'units': [
    unit('u1', EGG, '한계란 난각번호 1번 계란 무항생제 유정란 20구', 'B', 6361),
    unit('u2', EGG, '한계란 난각번호 1번 계란 무항생제 유정란 20구', 'C', 6361),
    unit('u3', WATER, '[본사공식] 몽베스트 무라벨 생수 2L', 'A', 2500),
    unit('u4', TEA, '동서 둥굴레차 50T 60g', 'B', 1800),
    unit('u5', RICE, '2026년 햅쌀 국산 백미 쌀 2kg', 'C', 3000),
    unit('u6', 'Gdead0001', '이상한 상품', 'A', 1000, exp=NOW - 5),
], 'restock': {'open': False, 'closes_at': NOW - 3600}}


def settles(rows):
    s = fm.Settles()
    s.add([{'seq': i + 1, 'ts': NOW - 60 * i, 'kind': 'settle', 'data': {'sku': k, 'grade': g, 'amount': a}}
           for i, (k, g, a) in enumerate(rows)], NOW)
    return s


INV_LONG = dict(INV, units=[dict(u, expires_at=NOW + 72 * 3600) if u['expires_at'] > NOW else u for u in INV['units']])
MARKET_SETTLES = [(EGG, 'B', 7975)] * 3 + [(EGG, 'C', 5258)] * 3 + [(WATER, 'A', 1000)] * 3 + [(TEA, 'B', 2813)] * 4


class Label(unittest.TestCase):
    def test_words(self):
        self.assertEqual(fm.label('한계란 난각번호 1번 계란 무항생제 유정란 20구'), '계란20구')
        self.assertEqual(fm.label('2026년 햅쌀 씻어나온쌀 5kg 상등급'), '쌀5kg')
        self.assertEqual(fm.label('한계란 무항생제 유정란 30구'), '계란30구')
        self.assertEqual(fm.label('[본사공식] 몽베스트 무라벨 생수 2L'), '생수2L')
        self.assertEqual(fm.label('[반찬천국] 취나물'), '취나물')
        self.assertEqual(fm.label('한상궁김치 총각김치 2kg/단단한 알타리'), '총각김치2kg')
        self.assertEqual(fm.label('[브랜드] 처음보는물건이름길다 1개'), '처음보는물건이름1개')

    def test_made_things_are_not_their_ingredient(self):
        self.assertEqual(fm.label('찹쌀 꿀호떡 1.05kg'), '꿀호떡1.05kg')
        self.assertEqual(fm.label('두부과자 55gx5팩'), '두부과자55g×5팩')
        self.assertEqual(fm.label('비비고 김치손만두 1.4kg'), '김치손만두1.4kg')
        self.assertEqual(fm.label('김치사발면'), '김치사발면')
        self.assertEqual(fm.label('2026년 유기농 방울토마토 500g'), '방울토마토500g')
        self.assertEqual(fm.label('정식수입 사토노유키 실큰두부 300g'), '실큰두부300g')

    def test_tofu_kinds_and_packs_00_10(self):   # 11/27 00:10: both were '두부300g' and four 두부 askers said no
        self.assertEqual(fm.label('[농협] 안동생명콩 국산콩 연두부 300g×5팩'), '연두부300g×5팩')
        self.assertEqual(fm.label('정식수입 사토노유키 실큰두부 300g 1팩 상온보관 멸균두부 연두부 순두부 단백질 식단관리'), '실큰두부300g×1팩')
        self.assertEqual(fm.label('순두부 350g'), '순두부350g')
        self.assertEqual(fm.label('국산콩 두부 300g'), '두부300g')
        self.assertEqual(fm.label('몽베스트 무라벨 생수 2Lx6병(1팩)'), '생수2L×6병,1팩')
        self.assertEqual(fm.label('하림 닭가슴살 100x10개'), '하림10개')            # review I1: no count after a bare number
        self.assertEqual(fm.label('한계란 무항생제 유정란 30구'), '계란30구')


class CashOut(unittest.TestCase):
    """현금화 11/27: a food grade with 48 h or less left whose 24 h median is under the goal (3) floor gets a cash-out
    quote at the median (its own floor, under cost); other grades, water and long-dated stock keep the old rule."""
    QB = fm.load_quote_block(HERE / 'restock_shoutout' / 'logic.py')     # the source the deploy forges
    MILK = 'G0011e806'
    INV = {'units': [
        unit('m1', 'G0011e806', '서울우유 멸균우유 190ml 24팩', 'C', 12235, exp=NOW + 29 * 3600),
        unit('m2', 'G0011e806', '서울우유 멸균우유 190ml 24팩', 'B', 12235, exp=NOW + 60 * 3600),
        unit('w1', WATER, '[본사공식] 몽베스트 무라벨 생수 2L', 'C', 2500, exp=NOW + 10 * 3600),
        unit('t1', TEA, '동서 둥굴레차 50T 60g', 'B', 1800, exp=NOW + 30 * 3600),
    ]}
    S = [('G0011e806', 'C', 11787)] * 5 + [('G0011e806', 'B', 11000)] * 3 + [(WATER, 'C', 500)] * 3 + [(TEA, 'B', 2813)] * 4

    def test_plan(self):
        qb = self.QB
        plan = fm.requote_plan(qb, self.INV, {}, settles(self.S), NOW)
        self.assertEqual(plan[self.MILK]['C'], {'price': 11787, 'floor': 11787})   # 29 h left, median under 12,778
        self.assertEqual(plan[self.MILK]['B'], 12778)    # 60 h left: the old rule, max(floor, median)
        self.assertNotIn(WATER, plan)                     # water never
        self.assertEqual(plan[TEA]['B'], 2813)            # median over the floor: the old rule

    def test_apply_and_shown(self):
        qb = self.QB
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'quotes.json'
            p.write_text(json.dumps({f'{self.MILK}|C': {'price': 15900, 'ts': NOW - 10, 'by': 'forum_menu'}}))
            plan = fm.requote_plan(qb, self.INV, json.loads(p.read_text()), settles(self.S), NOW)
            out = fm.apply_plan(qb, p, self.INV, plan, NOW)
            self.assertEqual(out[self.MILK]['C'], 11787)
            q = json.loads(p.read_text())
            self.assertEqual(q[f'{self.MILK}|C'], {'price': 11787, 'ts': NOW, 'by': 'cashout', 'floor': 11787})
            floors = qb['stock'](self.INV, self.MILK, NOW)['floors']
            self.assertEqual(qb['quote_prices'](q, self.MILK, floors, NOW)['C'], 11787)
            self.assertEqual(qb['quote_floor'](q, self.MILK, 'C', NOW), 11787)
            again = fm.requote_plan(qb, self.INV, q, settles(self.S), NOW + 60)
            self.assertNotIn('C', again.get(self.MILK, {}))   # already a cash-out quote at the median: no rewrite
            self.assertIsNone(qb['quote_floor'](q, self.MILK, 'C', NOW + 24 * 3600 + 1))   # a day old: gone

    def test_menu_text_shows_cash_price(self):
        qb = self.QB
        q = {f'{self.MILK}|C': {'price': 11787, 'ts': NOW - 10, 'by': 'cashout', 'floor': 11787}}
        floors = qb['stock'](self.INV, self.MILK, NOW)['floors']
        self.assertEqual(qb['quote_prices'](q, self.MILK, floors, NOW)['C'], 11787)
        q2 = {f'{self.MILK}|C': {'price': 11787, 'ts': NOW - 10, 'by': 'forum_menu', 'floor': 11787}}
        self.assertEqual(qb['quote_prices'](q2, self.MILK, floors, NOW)['C'], 12778)   # floor key alone: ignored

    def test_stray_low_median_moves_nothing(self):
        plan = fm.requote_plan(self.QB, self.INV, {}, settles([(self.MILK, 'C', 8000)] * 5), NOW)   # 0.63 x 12,778
        self.assertEqual(plan[self.MILK]['C'], 12778)   # the old rule: max(floor, median)

    def test_taken_back_when_gone(self):                 # review I2: sold, longer-dated only, median up, off
        qb = self.QB
        q = {f'{self.MILK}|C': {'price': 11787, 'ts': NOW - 10, 'by': 'cashout', 'floor': 11787}}
        long_only = {'units': [dict(u, expires_at=NOW + 200 * 3600) if u['unit'] == 'm1' else u for u in self.INV['units']]}
        plan = fm.requote_plan(qb, long_only, q, settles(self.S), NOW)
        self.assertEqual(plan[self.MILK]['C'], {'price': 12778, 'revoke': True})
        up = [(self.MILK, 'C', 13500)] * 5 + [(self.MILK, 'B', 11000)] * 3
        self.assertEqual(fm.requote_plan(qb, self.INV, q, settles(up), NOW)[self.MILK]['C'], {'price': 13500, 'revoke': True})
        self.assertEqual(fm.requote_plan(qb, self.INV, q, settles(self.S), NOW, on=False)[self.MILK]['C'],
                         {'price': 12778, 'revoke': True})
        self.assertEqual(fm.requote_plan(qb, self.INV, q, settles([]), NOW)[self.MILK]['C'], {'price': 12778, 'revoke': True})
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'quotes.json'
            p.write_text(json.dumps(q))
            out = fm.apply_plan(qb, p, long_only, plan, NOW)
            self.assertEqual(out[self.MILK]['C'], 12778)
            got = json.loads(p.read_text())[f'{self.MILK}|C']
            self.assertEqual((got['price'], got['by']), (12778, 'forum_menu'))
            self.assertIsNone(qb['quote_floor'](json.loads(p.read_text()), self.MILK, 'C', NOW))

    def test_follows_median_both_ways(self):
        qb = self.QB
        q = {f'{self.MILK}|C': {'price': 11000, 'ts': NOW - 10, 'by': 'cashout', 'floor': 11000}}
        plan = fm.requote_plan(qb, self.INV, q, settles(self.S), NOW)     # median 11,787 now: raised to it
        self.assertEqual(plan[self.MILK]['C'], {'price': 11787, 'floor': 11787})
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'quotes.json'
            p.write_text(json.dumps(q))
            self.assertEqual(fm.apply_plan(qb, p, self.INV, plan, NOW)[self.MILK]['C'], 11787)
            self.assertEqual(qb['quote_floor'](json.loads(p.read_text()), self.MILK, 'C', NOW), 11787)

    def test_not_under_lower_grade(self):                # review M1: a B cash-out never under the C quote
        qb = self.QB
        inv = {'units': [unit('m1', self.MILK, '서울우유 멸균우유 190ml 24팩', 'C', 12235, exp=NOW + 29 * 3600),
                         unit('m2', self.MILK, '서울우유 멸균우유 190ml 24팩', 'B', 12235, exp=NOW + 30 * 3600)]}
        q = {f'{self.MILK}|C': {'price': 12000, 'ts': NOW - 10, 'by': 'cashout', 'floor': 12000}}
        s = [(self.MILK, 'C', 12000)] * 5 + [(self.MILK, 'B', 11000)] * 5
        plan = fm.requote_plan(qb, inv, q, settles(s), NOW)
        self.assertEqual(plan[self.MILK]['B'], {'price': 12000, 'floor': 12000})

    def test_clearance_not_over_cash_price(self):         # review r2 I-N1
        qb = self.QB
        inv = {'units': [dict(unit('m7', self.MILK, '서울우유 멸균우유 190ml 24팩', 'C', 12235, exp=NOW + 20 * 3600),
                              category='식품>계란/유제품>우유/두유')]}
        q = {f'{self.MILK}|C': {'price': 11787, 'ts': NOW - 10, 'by': 'cashout', 'floor': 11787}}
        shown = {g: p for g, p in qb['quote_prices'](q, self.MILK, qb['stock'](inv, self.MILK, NOW)['floors'], NOW).items() if p}
        plain = fm.clear_item(inv, self.MILK, shown, NOW)
        got = fm.clear_item(inv, self.MILK, shown, NOW, qb, q)
        self.assertIsNotNone(got)
        self.assertLessEqual(got[2]['price'], 11787)
        self.assertGreater(plain[2]['price'], 11787)         # without the guard the curve gives the cost floor

    def test_off_file(self):
        with tempfile.TemporaryDirectory() as d:
            off = Path(d) / 'cashout.off'
            self.assertTrue(fm.cash_on(self.QB, str(off)))
            off.write_text('')
            self.assertFalse(fm.cash_on(self.QB, str(off)))

    def test_old_block_has_no_cash_out(self):
        qb = dict(self.QB)
        qb.pop('CASHOUT_MIN')                               # a stale forged block: nothing goes under the floor
        plan = fm.requote_plan(qb, self.INV, {}, settles(self.S), NOW)
        self.assertEqual(plan[self.MILK]['C'], 12778)
        qb = dict(self.QB)
        qb.pop('unit_cash')                                 # partly stale (review M5): no cash-out, no KeyError
        self.assertEqual(fm.requote_plan(qb, self.INV, {}, settles(self.S), NOW)[self.MILK]['C'], 12778)


class Requote(unittest.TestCase):
    def test_plan_follows_median_above_floor_and_skips_water(self):
        q = {f'{EGG}|B': {'price': 9675, 'ts': NOW - 10, 'by': 'x'}, f'{EGG}|C': {'price': 9675, 'ts': NOW - 10, 'by': 'x'},
             f'{WATER}|A': {'price': 4830, 'ts': NOW - 10, 'by': 'x'}}
        plan = fm.requote_plan(QB, INV_LONG, q, settles(MARKET_SETTLES), NOW)   # 72 h left: no cash-out (11/27)
        floor = QB['stock'](INV, EGG, NOW)['floors']
        self.assertEqual(plan[EGG]['B'], 7975)
        self.assertEqual(plan[EGG]['C'], max(floor['C'], 5258))
        self.assertGreaterEqual(plan[EGG]['C'], floor['C'])
        if 'CASHOUT_MIN' in QB:                          # 24 h left, median 5,258 >= 0.7 x floor 6,845: cash-out
            self.assertEqual(fm.requote_plan(QB, INV, q, settles(MARKET_SETTLES), NOW)[EGG]['C'],
                             {'price': 5258, 'floor': 5258})
        self.assertNotIn(WATER, plan)                    # fixed price
        self.assertEqual(plan[TEA]['B'], max(QB['stock'](INV, TEA, NOW)['floors']['B'], 2813))
        self.assertNotIn(RICE, plan)                     # no settles: no median, nothing changes

    def test_cheaper_quote_is_kept(self):
        q = {f'{TEA}|B': {'price': 2500, 'ts': NOW - 10, 'by': 'x'}}
        floor = QB['stock'](INV, TEA, NOW)['floors']['B']
        plan = fm.requote_plan(QB, INV, q, settles(MARKET_SETTLES), NOW)
        if 2500 >= floor:
            self.assertNotIn(TEA, plan)

    def test_too_few_settles(self):
        plan = fm.requote_plan(QB, INV, {}, settles([(TEA, 'B', 2813)] * 2), NOW)
        self.assertEqual(plan, {})

    def test_apply_writes_shared_block(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'quotes.json'
            p.write_text(json.dumps({f'{EGG}|B': {'price': 9675, 'ts': NOW - 10, 'by': 'x'}}))
            out = fm.apply_plan(QB, p, INV, {EGG: {'B': 7975}}, NOW)
            self.assertEqual(out[EGG]['B'], 7975)
            q = json.loads(p.read_text())
            self.assertEqual(q[f'{EGG}|B']['price'], 7975)
            self.assertEqual(q[f'{EGG}|B']['by'], 'forum_menu')
            self.assertEqual(QB['quote_prices'](q, EGG, QB['stock'](INV, EGG, NOW)['floors'], NOW)['B'], 7975)


class Text(unittest.TestCase):
    QUOTES = {f'{EGG}|B': {'price': 7975, 'ts': NOW, 'by': 'x'}, f'{EGG}|C': {'price': 7000, 'ts': NOW, 'by': 'x'},
              f'{TEA}|B': {'price': 2900, 'ts': NOW, 'by': 'x'}, f'{WATER}|A': {'price': 4830, 'ts': NOW, 'by': 'x'},
              f'{RICE}|C': {'price': 4700, 'ts': NOW, 'by': 'x'}}

    def test_fits_the_view_and_orders_by_market_sales(self):
        text, skus = fm.build_text(QB, INV, self.QUOTES, settles(MARKET_SETTLES), NOW, 0)
        self.assertLessEqual(len(text), 160)
        self.assertTrue(text.endswith(fm.TAIL))
        # 11/27 12:40: A·B lines with 24 h+ left lead (tea B, water A, in the old order: the tea's 4 settles, then
        # the water's 3); the egg (6 settles, but its cheapest shown grade is C) follows them
        self.assertEqual(skus[:3], [TEA, WATER, EGG])
        self.assertIn('계란20구 C7,000', text)               # cheapest shown grade of the egg

    def test_display_rule_failures_written_and_still_shown(self):
        # 대표님 11/27 01:26: the display rules run before each post. Two eggs that would read alike get their own names
        # (review I5: the SKU tail at the latest); a rule break is written down (failed) and the item still goes up —
        # the check never hides stock (review I4).
        two = {'units': INV['units'] + [unit('u7', 'GEGG2', '한계란 난각번호 1번 계란 무항생제 유정란 20구', 'C', 6361)]}
        q = dict(self.QUOTES, **{'GEGG2|C': {'price': 7100, 'ts': NOW, 'by': 'x'}})
        failed = {}
        text, skus = fm.build_text(QB, two, q, settles(MARKET_SETTLES), NOW, 0, failed=failed)
        self.assertIn(EGG, skus)
        self.assertIn('GEGG2', skus)
        shown = [x.rsplit(' ', 1)[0] for x in text.split('] ', 1)[1].split(' — ')[0].split(' · ')]
        self.assertEqual(len(shown), len(set(shown)), shown)
        self.assertEqual(failed, {})
        real = fm.display_check
        fm.display_check = lambda names, shown: {EGG: ['용량·묶음 빠짐 20구']}
        try:
            failed = {}
            text, skus = fm.build_text(QB, INV, self.QUOTES, settles(MARKET_SETTLES), NOW, 0, failed=failed)
        finally:
            fm.display_check = real
        self.assertEqual(failed[EGG]['why'], ['용량·묶음 빠짐 20구'])
        self.assertIn(EGG, skus)                           # written down, still shown
        self.assertIn('계란20구 C7,000', text)

    def test_names_alike_up_to_a_comma_both_posted(self):
        # 동원135g×2, 150g / 동원135g×2, 100g: one line per shown name, never per its first word (r3 I2)
        inv = {'units': INV['units'] + [unit('t1', 'GTUNA1', '동원 살코기 135gX2+동원 마일드150g', 'B', 3000),
                                        unit('t2', 'GTUNA2', '동원 살코기 135gX2+동원 고추100g', 'B', 3000)]}
        q = dict(self.QUOTES, **{'GTUNA1|B': {'price': 4000, 'ts': NOW, 'by': 'x'},
                                 'GTUNA2|B': {'price': 4100, 'ts': NOW, 'by': 'x'}})
        text, skus = fm.build_text(QB, inv, q, settles(MARKET_SETTLES), NOW, 0)
        self.assertIn('GTUNA1', skus)
        self.assertIn('GTUNA2', skus)

    def test_display_check_reads_only_live_stock(self):
        # an expired unit of another SKU with the same name does not rename or hide the live egg (review I4)
        old = unit('u8', 'GEGG3', '한계란 난각번호 1번 계란 무항생제 유정란 20구', 'C', 6361, NOW - 60)
        inv = {'units': INV['units'] + [old]}
        failed = {}
        text, skus = fm.build_text(QB, inv, self.QUOTES, settles(MARKET_SETTLES), NOW, 0, failed=failed)
        self.assertEqual(skus[:3], [TEA, WATER, EGG])      # 11/27 12:40: A·B 24 h+ lead, then the egg
        self.assertIn('계란20구 C7,000', text)
        self.assertNotIn('GEGG3', skus)
        self.assertEqual(failed, {})

    def test_unpriced_and_expired_stock_left_out(self):
        text, skus = fm.build_text(QB, INV, {}, settles(MARKET_SETTLES), NOW, 0)
        self.assertIsNone(text)
        self.assertEqual(skus, [])
        text, skus = fm.build_text(QB, INV, self.QUOTES, settles([]), NOW, 0)
        self.assertNotIn('Gdead0001', skus)

    def test_rotation(self):
        many = {'units': [unit(f'v{i}', f'G{i:08x}', f'품목{i}번 상품', 'B', 1000) for i in range(30)]}
        q = {f'G{i:08x}|B': {'price': 2000 + i, 'ts': NOW, 'by': 'x'} for i in range(30)}
        a = fm.build_text(QB, many, q, settles([]), NOW, 0)[1]
        b = fm.build_text(QB, many, q, settles([]), NOW, 1)[1]
        self.assertEqual(a[:2], b[:2])
        self.assertNotEqual(a[2:], b[2:])


class Window(unittest.TestCase):
    def test_open_flag_only(self):
        self.assertTrue(fm.window_open({'restock': {'open': True}}))
        self.assertFalse(fm.window_open({'restock': {'open': False, 'opens_at': NOW + 600, 'closes_at': NOW + 780}}))
        self.assertFalse(fm.window_open({}))

    def test_announce_due(self):
        s = {'last_window': 'w', 'windows': {'w': {'ts': NOW - 60, 'skus': {EGG: {}}, 'posted': None}}}
        self.assertTrue(fm.announce_due(s, NOW))
        s['windows']['w']['posted'] = 'pending'
        self.assertTrue(fm.announce_due(s, NOW))
        s['windows']['w']['posted'] = 9287
        self.assertFalse(fm.announce_due(s, NOW))
        s['windows']['w'].update(posted=None, ts=NOW - fm.ANNOUNCE_WAIT - 1)
        self.assertFalse(fm.announce_due(s, NOW))
        s['windows']['w']['ts'] = NOW - 60
        s['paused_until'] = NOW + 100
        self.assertFalse(fm.announce_due(s, NOW))
        self.assertFalse(fm.announce_due(None, NOW))

class FakeMenu(fm.Menu):
    def __init__(self, d, forum_ts=0.0, inv=None, post_code=200, ledger=None, dislikes=0):
        market = types.SimpleNamespace(token={'shop': 't'}, joined={'shop': ('nocha12', None, None)},
                                       base='https://market.example')
        self.now = NOW
        super().__init__(d, market, QB_PATH, clock=lambda: self.now, sleep=lambda s: None)
        self.forum_ts, self.inv, self.post_code, self.posts = forum_ts, inv or INV, post_code, []
        self.dislikes, self.afters, self.body = dislikes, [], None
        self.ledger = ledger if ledger is not None else [
            {'seq': i + 1, 'ts': NOW - 60, 'kind': 'settle', 'data': {'sku': k, 'grade': g, 'amount': a}}
            for i, (k, g, a) in enumerate(MARKET_SETTLES)]

    def _http(self, method, path, body=None):
        if path == '/api/inventory':
            return 200, self.inv
        if path.startswith('/api/forum?'):
            if self.forum_ts is None:
                return 500, None
            return 200, [{'author': 'nocha12', 'ts': self.forum_ts, 'dislikes': self.dislikes, 'text': '[노차네 구멍가게(nocha12) 지금 재고] 계란20구 B7,975'},
                         {'author': 'yms979', 'ts': self.now, 'dislikes': 9}]
        if path.startswith('/api/ledger?after='):
            after = int(path.split('=')[1])
            self.afters.append(after)
            if after < 0:
                return 200, self.ledger[after:]
            return 200, [r for r in self.ledger if r['seq'] > after][:500]
        if method == 'POST' and path == '/api/forum':
            self.posts.append(body['text'])
            return self.post_code, (self.body if self.body is not None else ({'id': 7} if self.post_code == 200 else None))
        raise AssertionError(path)


def shop(d):
    (Path(d) / 'logs').mkdir()
    (Path(d) / 'ops').mkdir()
    (Path(d) / 'logs' / 'quotes.json').write_text(json.dumps(
        {f'{EGG}|B': {'price': 9675, 'ts': NOW - 10, 'by': 'x'}, f'{WATER}|A': {'price': 4830, 'ts': NOW - 10, 'by': 'x'}}))


class Once(unittest.TestCase):
    def test_two_alike_items_both_posted(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            two = {'units': INV['units'] + [unit('u7', 'GEGG2', '한계란 난각번호 1번 계란 무항생제 유정란 20구', 'C', 6361)],
                   'restock': INV['restock']}
            m = FakeMenu(d, inv=two)
            r = m.once()
            self.assertTrue(r['ok'], r)
            self.assertIn('계란20구·', m.posts[0])            # still posted, under a name of its own (the other has no price yet)
            rows = [json.loads(x) for x in (Path(d) / 'logs' / 'forum-menu.jsonl').read_text().splitlines()]
            self.assertEqual([x for x in rows if x.get('display_fail')], [])

    def test_posts_requotes_then_waits(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            m = FakeMenu(d)
            r = m.once()
            self.assertTrue(r['ok'], r)
            self.assertEqual(len(m.posts), 1)
            floor_c = QB['stock'](INV, EGG, NOW)['floors']['C']
            want_c = 5258 if 'CASHOUT_MIN' in QB else max(floor_c, 5258)   # 24 h left: the cash-out price (11/27)
            self.assertIn(f'계란20구 C{want_c:,}', m.posts[0])   # the cheapest shown grade
            q = json.loads((Path(d) / 'logs' / 'quotes.json').read_text())
            self.assertEqual(q[f'{EGG}|B']['price'], 7975)
            self.assertEqual(q[f'{WATER}|A']['price'], 4830)       # water untouched
            m.now += 100
            self.assertEqual(m.once(), {'skip': 'gap'})
            m.now += fm.POST_GAP
            m.forum_ts = m.now - 10                                   # restock_shoutout just posted
            self.assertEqual(m.once(), {'skip': 'gap'})
            self.assertEqual(len(m.posts), 1)
            rows = [json.loads(x) for x in (Path(d) / 'logs' / 'forum-menu.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows), 1)

    def test_off_switch_window_and_unreadable_forum(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            (Path(d) / 'ops' / 'forum_menu.off').write_text('')
            self.assertEqual(FakeMenu(d).once(), {'skip': 'off'})
            (Path(d) / 'ops' / 'forum_menu.off').unlink()
            self.assertEqual(FakeMenu(d, inv=dict(INV, restock={'open': True})).once(), {'skip': 'supply window'})
            (Path(d) / 'logs' / 'forum-menu-state.json').unlink()   # the window seen above is remembered
            m = FakeMenu(d, forum_ts=None)
            self.assertEqual(m.once(), {'skip': 'forum unreadable'})
            self.assertEqual(m.posts, [])

    def test_lost_answer_waits_a_full_gap(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            m = FakeMenu(d, post_code=0)
            r = m.once()
            self.assertFalse(r['ok'])
            m.now += 60
            self.assertEqual(m.once(), {'skip': 'gap'})

    def test_refused_post_retries_next_tick(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            m = FakeMenu(d, post_code=400)
            self.assertFalse(m.once()['ok'])
            m.post_code = 200
            m.now += 30
            self.assertEqual(m.once(), {'skip': 'gap'})            # a refusal waits a minute, not a tick
            m.now += 31
            self.assertTrue(m.once()['ok'])

    def test_no_token_no_call(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            m = fm.Menu(d, types.SimpleNamespace(token={}, joined={}), QB_PATH, clock=lambda: NOW)
            self.assertEqual(m._http('GET', '/api/inventory'), (0, None))


class Guards(unittest.TestCase):
    def test_quiet_after_a_window_with_the_real_closed_shape(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            m = FakeMenu(d, inv=dict(INV, restock={'open': True, 'opens_at': NOW - 60, 'closes_at': NOW + 120}))
            self.assertEqual(m.once(), {'skip': 'supply window'})
            m.inv = dict(INV, restock={'open': False, 'opens_at': NOW + 1200, 'closes_at': NOW + 1380})
            m.now += fm.AFTER_WINDOW - 30                    # q8: AFTER_WINDOW is 90 s now
            self.assertEqual(m.once(), {'skip': 'supply window'})
            m.now += 31
            self.assertEqual(m.once(), {'skip': 'restock post may come'})   # no restock entry for it yet
            m.now = NOW + 120 + fm.AFTER_WINDOW_LONG
            self.assertTrue(m.once()['ok'])

    def test_votes_do_not_skip_an_eligible_menu(self):
        for votes in (1, 3):
            with self.subTest(votes=votes), tempfile.TemporaryDirectory() as d:
                shop(d)
                m = FakeMenu(d, forum_ts=NOW - 600, dislikes=votes)
                self.assertTrue(m.once().get('ok'))

    def test_old_dislike_deadline_is_not_inherited(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            (Path(d) / 'logs' / 'forum-menu-state.json').write_text(json.dumps(
                {'not_before': NOW + 86400}))
            self.assertTrue(FakeMenu(d).once().get('ok'))

    def test_legacy_shoutout_pause_does_not_stop_stock_menu(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            (Path(d) / 'shoutout.json').write_text(json.dumps({'paused_until': NOW + 600}))
            m = FakeMenu(d)
            self.assertTrue(m.once().get('ok'))

    def test_waitlisted_restock_post_keeps_existing_priority(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            (Path(d) / 'shoutout.json').write_text(json.dumps(
                {'last_window': 'w', 'paused_until': NOW + 3600,             # announce_due off: this branch alone
                 'windows': {'w': {'ts': NOW - 120, 'skus': {EGG: {}}, 'posted': None}}}))
            (Path(d) / 'waitlist.json').write_text(json.dumps({'entries': [{'sku': EGG}]}))
            m = FakeMenu(d, forum_ts=NOW - 3600, dislikes=1)
            self.assertEqual(m.once(), {'skip': 'restock post for waitlisted customers goes first'})
            m.now = NOW - 120 + fm.ANNOUNCE_WAIT + 1           # q8 (review I2): a post that never came stops holding
            self.assertTrue(m.once().get('ok'))

    def test_market_429_retry_deadline_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            m = FakeMenu(d, post_code=429)
            m.body = {'wait': 1000}
            self.assertEqual(m.once()['status'], 429)
            other = FakeMenu(d)
            other.now += 350
            self.assertEqual(other.once(), {'skip': 'gap'})
            other.now += 650
            self.assertTrue(other.once().get('ok'))

    def test_429_counts_from_response_after_slow_backfill(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            m = FakeMenu(d, post_code=429)
            m.body = {'wait': 60}
            refresh = m.refresh_settles
            def delayed(now):
                refresh(now)
                m.now += 90
            m.refresh_settles = delayed
            self.assertEqual(m.once()['status'], 429)
            other = FakeMenu(d)
            other.now += 91
            self.assertEqual(other.once(), {'skip': 'gap'})
            other.now += 59
            self.assertTrue(other.once().get('ok'))

    def test_restock_post_first(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            (Path(d) / 'shoutout.json').write_text(json.dumps(
                {'last_window': 'w', 'windows': {'w': {'ts': NOW - 30, 'skus': {EGG: {}}, 'posted': None}}}))
            m = FakeMenu(d)
            self.assertEqual(m.once(), {'skip': 'restock post to come'})
            self.assertEqual(m.posts, [])

    def test_a_quote_held_up_by_a_lower_grade_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            p = Path(d) / 'logs' / 'quotes.json'
            q = {f'{EGG}|C': {'price': 9675, 'ts': NOW - 10, 'by': 'x'}, f'{EGG}|B': {'price': 9675, 'ts': NOW - 10, 'by': 'x'}}
            p.write_text(json.dumps(q))
            plan = fm.requote_plan(QB, INV, q, settles([(EGG, 'B', 7975)] * 3), NOW)
            self.assertEqual(plan, {})

    def test_backfill_then_new_rows_only(self):
        rows = [{'seq': i, 'ts': NOW - 10, 'kind': 'settle', 'data': {'sku': TEA, 'grade': 'B', 'amount': 2813}}
                for i in range(1, 9001)]
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            m = FakeMenu(d, ledger=rows)
            m.refresh_settles(NOW)
            self.assertEqual(m.afters[0], -1)
            self.assertEqual(m.afters[1], 9000 - fm.BACKFILL)
            self.assertEqual(m.settles.last, 9000)
            self.assertEqual(len(m.settles.rows), fm.BACKFILL)
            rows.append({'seq': 9001, 'ts': NOW, 'kind': 'settle', 'data': {'sku': TEA, 'grade': 'B', 'amount': 2900}})
            m.afters.clear()
            m.refresh_settles(NOW)
            self.assertEqual(m.afters, [9000])
            self.assertEqual(m.settles.last, 9001)

    def test_skip_logged_once_per_reason(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            (Path(d) / 'ops' / 'forum_menu.off').write_text('')
            m = FakeMenu(d)
            m.once(); m.now += 30; m.once()
            rows = (Path(d) / 'logs' / 'forum-menu.jsonl').read_text().splitlines()
            self.assertEqual(len(rows), 1)
            m.now += fm.SKIP_LOG
            m.once()
            self.assertEqual(len((Path(d) / 'logs' / 'forum-menu.jsonl').read_text().splitlines()), 2)

    def test_429_waits_what_the_market_says(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            m = FakeMenu(d, post_code=429)
            m.body = {'error': 'slow down', 'wait': 200}
            self.assertFalse(m.once()['ok'])
            m.post_code, m.body = 200, None
            m.now += 100
            self.assertEqual(m.once(), {'skip': 'gap'})
            m.now += 101
            self.assertTrue(m.once()['ok'])

    def test_uses_the_kit_market_base(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            self.assertEqual(FakeMenu(d).base(), 'https://market.example')

    def test_own_temp_file(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            p = Path(d) / 'logs' / 'quotes.json'
            fm.apply_plan(QB, p, INV, {EGG: {'B': 7975}}, NOW)
            self.assertFalse((Path(d) / 'logs' / 'quotes.json.tmp').exists())
            self.assertEqual(sorted(x.name for x in (Path(d) / 'logs').iterdir()), ['quotes.json'])
            self.assertEqual(json.loads(p.read_text())[f'{WATER}|A']['price'], 4830)   # other quotes kept


class Review2(unittest.TestCase):
    def test_slot_kept_for_a_window_about_to_open(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            m = FakeMenu(d, inv=dict(INV, restock={'open': False, 'opens_at': NOW + 200, 'closes_at': NOW + 380}))
            self.assertEqual(m.once(), {'skip': 'supply window soon'})
            m.inv = dict(INV, restock={'open': False, 'opens_at': NOW + fm.POST_GAP + 61, 'closes_at': NOW + 900})
            self.assertTrue(m.once()['ok'])

    def test_failed_backfill_is_tried_again_and_after_is_a_number(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            m = FakeMenu(d, ledger=[])
            m.refresh_settles(NOW)
            m.refresh_settles(NOW)
            self.assertTrue(all(isinstance(a, int) for a in m.afters), m.afters)
            m2 = FakeMenu(d)
            real = m2._http
            m2._http = lambda meth, path, body=None: (500, None) if path.startswith('/api/ledger?after=') and not path.endswith('-1') else real(meth, path, body)
            m2.refresh_settles(NOW)
            self.assertFalse(m2.backfilled)
            m2._http = real
            m2.refresh_settles(NOW)
            self.assertTrue(m2.backfilled)
            self.assertEqual(m2.settles.count(EGG), 6)

    def test_future_rows_kept(self):
        s = fm.Settles()
        s.add([{'seq': 1, 'ts': NOW + 5, 'kind': 'settle', 'data': {'sku': TEA, 'grade': 'B', 'amount': 2813}}], NOW)
        self.assertEqual(len(s.rows), 1)

    def test_loop_error_goes_through_skip(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            m = FakeMenu(d)
            class Stop:
                done = False
                def wait(self, s):
                    return self.done
                def is_set(self):
                    return self.done
            stop = Stop()
            def boom():
                stop.done = True
                raise RuntimeError('x')
            m.once = boom
            m.loop(stop)
            rows = [json.loads(x) for x in (Path(d) / 'logs' / 'forum-menu.jsonl').read_text().splitlines()]
            self.assertTrue(rows and rows[-1]['skip'].startswith('error RuntimeError'), rows)


class Review3(unittest.TestCase):
    def test_water_by_sku_or_any_water_word(self):
        self.assertTrue(fm.fixed('아무 이름', WATER))
        self.assertTrue(fm.fixed('제주 삼다수 2L', 'Gx'))
        self.assertTrue(fm.fixed('동원샘물 2L x 6병', 'Gy'))
        self.assertFalse(fm.fixed('동서 둥굴레차 50T 60g', TEA))

    def test_no_quote_gets_market_price_not_below_floor(self):
        plan = fm.requote_plan(QB, INV, {}, settles([(TEA, 'B', 1)] * 3), NOW)
        self.assertEqual(plan[TEA]['B'], QB['stock'](INV, TEA, NOW)['floors']['B'])

    def test_a_quote_written_meanwhile_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            p = Path(d) / 'logs' / 'quotes.json'
            q = json.loads(p.read_text())
            q[f'{EGG}|B'] = {'price': 7000, 'ts': NOW, 'by': 'private_checkout'}   # a tool lowered it already
            p.write_text(json.dumps(q))
            self.assertEqual(fm.apply_plan(QB, p, INV, {EGG: {'B': 7975}}, NOW), {})
            self.assertEqual(json.loads(p.read_text())[f'{EGG}|B']['price'], 7000)

    def test_file_changed_during_the_write_is_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            p = Path(d) / 'logs' / 'quotes.json'
            real = QB['quotes_load']
            def load_then_touch(path):
                r = real(path)
                q = json.loads(p.read_text()); q['X|A'] = {'price': 1, 'ts': NOW, 'by': 'check_item_inventory'}
                p.write_text(json.dumps(q))
                import os; os.utime(p, ns=(1, 1))
                return r
            qb = dict(QB, quotes_load=load_then_touch)
            self.assertEqual(fm.apply_plan(qb, p, INV, {EGG: {'B': 7975}}, NOW), {})
            self.assertIn('X|A', json.loads(p.read_text()))

    def test_odd_rows_do_not_stop_the_menu(self):
        s = fm.Settles()
        s.add([{'seq': 1, 'ts': 'yesterday', 'kind': 'settle', 'data': {'sku': TEA, 'grade': 'B', 'amount': 1}},
               {'seq': 2, 'ts': NOW, 'kind': 'settle', 'data': {'sku': TEA, 'grade': 'B', 'amount': 2813}}, 'junk'], NOW)
        self.assertEqual(len(s.rows), 1)

    def test_no_line_fits_no_post(self):
        inv = {'units': [unit('u1', EGG, '가' * 200, 'B', 6361)], 'restock': {}}
        self.assertEqual(fm.build_text(QB, inv, {}, fm.Settles(), NOW, 0), (None, []))

    def test_slot_held_until_the_restock_entry_appears(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            m = FakeMenu(d, inv=dict(INV, restock={'open': True, 'opens_at': NOW - 60, 'closes_at': NOW + 60}))
            m.once()
            m.inv = dict(INV, restock={'open': False, 'opens_at': NOW + 1200, 'closes_at': NOW + 1380})
            m.now += fm.AFTER_WINDOW + 10
            self.assertEqual(m.once(), {'skip': 'restock post may come'})
            (Path(d) / 'shoutout.json').write_text(json.dumps(
                {'last_window': 'w', 'windows': {'w': {'ts': NOW - 30, 'skus': {EGG: {}}, 'posted': 'p9'}}}))
            self.assertTrue(m.once()['ok'])
            m2 = FakeMenu(d, inv=dict(INV, restock={'open': True}))
            (Path(d) / 'shoutout.json').unlink()
            m2.once()
            m2.inv = dict(INV, restock={'open': False, 'opens_at': NOW + 1200})
            m2.now += fm.AFTER_WINDOW_LONG + 1
            m2.forum_ts = NOW - 7200
            self.assertTrue(m2.once()['ok'])                    # no entry at all: wait 10 min, then post


class Review4(unittest.TestCase):
    def test_restock_entry_appearing_during_the_backfill_wins(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            m = FakeMenu(d)
            real = m.refresh_settles
            def slow(now):
                real(now)
                (Path(d) / 'shoutout.json').write_text(json.dumps(
                    {'last_window': 'w', 'windows': {'w': {'ts': NOW, 'skus': {EGG: {}}, 'posted': None}}}))
            m.refresh_settles = slow
            self.assertEqual(m.once(), {'skip': 'restock post to come'})
            self.assertEqual(m.posts, [])

    def test_last_open_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            m = FakeMenu(d, inv=dict(INV, restock={'open': True}))
            m.once()
            m2 = FakeMenu(d, inv=dict(INV, restock={'open': False, 'opens_at': NOW + 1200}))
            m2.now += 60
            self.assertEqual(m2.once(), {'skip': 'supply window'})

    def test_millisecond_times_and_dict_forum(self):
        self.assertEqual(fm._ts({'ts': NOW * 1000}), NOW)
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            m = FakeMenu(d)
            real = m._http
            m._http = lambda meth, path, body=None: ((200, {'posts': real(meth, path, body)[1]})
                                                    if path.startswith('/api/forum?') else real(meth, path, body))
            self.assertTrue(m.once()['ok'])


class Hook(unittest.TestCase):
    def test_starts_once_for_the_shop_only(self):
        import agent_kit_records as rec
        calls = []

        class Market:
            def join(self, role, name=None):
                return {'role': role}

        ak = types.SimpleNamespace(mk=types.SimpleNamespace(Market=Market))
        real = fm.start
        fm.start = lambda shop_dir, market, qb: calls.append((shop_dir, market, qb))
        try:
            rec._menu_hook(ak, Path('/x'))
            m = Market()
            self.assertEqual(m.join('buyer'), {'role': 'buyer'})
            self.assertEqual(calls, [])
            self.assertEqual(m.join('shop', 'nocha12'), {'role': 'shop'})
            m.join('shop', 'nocha12')                    # a join again after a market reset: no second thread
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][2], Path('/x/tools/restock_shoutout/logic.py'))
        finally:
            fm.start = real

    def test_start_failure_keeps_join(self):
        import agent_kit_records as rec

        class Market:
            def join(self, role):
                return 'joined'

        ak = types.SimpleNamespace(mk=types.SimpleNamespace(Market=Market))
        real = fm.start
        fm.start = lambda *a: (_ for _ in ()).throw(RuntimeError('boom'))
        try:
            rec._menu_hook(ak, Path('/x'))
            self.assertEqual(Market().join('shop'), 'joined')
        finally:
            fm.start = real

    def test_checkout_first_in_format(self):
        import ast
        src = (HERE / 'agent_kit_records.py').read_text(encoding='utf-8')
        fmt = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.AugAssign)
                   and getattr(n.target, 'attr', None) == 'FORMAT')
        text = ast.literal_eval(fmt.value)               # the exact text added to ak.FORMAT
        self.assertIn('Checkout first', text)
        self.assertIn('create the checkout in this same reply at the shown price', text)
        self.assertIn('hold back only when the customer said not to issue yet', text)

    def test_thread_stops(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            m = FakeMenu(d)
            stop = threading.Event()
            stop.set()
            m.loop(stop)                                  # returns at once when stopped
            self.assertEqual(m.posts, [])



class Q8Exposure(unittest.TestCase):
    """q8 (11/26 19:3x): take every market slot outside the window's own restock post; expiring stock first."""

    def test_expiring_and_big_stock_first(self):
        inv = {'units': [unit('e1', EGG, '한계란 난각번호 1번 계란 무항생제 유정란 20구', 'C', 6361, exp=NOW + 400 * 3600),
                         unit('t1', TEA, '동서 둥굴레차 50T 60g', 'B', 1800, exp=NOW + 300 * 3600),
                         unit('t2', TEA, '동서 둥굴레차 50T 60g', 'B', 1800, exp=NOW + 300 * 3600),
                         unit('t3', TEA, '동서 둥굴레차 50T 60g', 'B', 1800, exp=NOW + 300 * 3600),
                         unit('r1', RICE, '2026년 햅쌀 국산 백미 쌀 2kg', 'C', 3000, exp=NOW + 5 * 3600)]}
        q = {f'{EGG}|C': {'price': 7000, 'ts': NOW, 'by': 'x'}, f'{TEA}|B': {'price': 2900, 'ts': NOW, 'by': 'x'},
             f'{RICE}|C': {'price': 4700, 'ts': NOW, 'by': 'x'}}
        text, skus = fm.build_text(QB, inv, q, settles(MARKET_SETTLES), NOW, 0)
        # 11/27 06:40: the first two lines with 10 h+ left lead (3 units, then the best seller); 5 h rice follows
        self.assertEqual(skus[:3], [TEA, EGG, RICE])
        self.assertLessEqual(len(text), fm.VIEW)

    def test_soonest_reads_only_good_dated_units_of_that_grade(self):
        inv = {'units': [unit('a', EGG, 'x', 'B', 1, exp=NOW + 7200), unit('b', EGG, 'x', 'C', 1, exp=NOW + 3600),
                         unit('c', EGG, 'x', 'B', 1, exp=NOW - 5), dict(unit('d', EGG, 'x', 'B', 1), expires_at=None)]}
        self.assertEqual(fm.soonest(inv, EGG, 'B', NOW), 2.0)
        self.assertIsNone(fm.soonest(inv, TEA, 'B', NOW))

    def test_restock_post_naming_nobody_gets_only_the_grace(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            (Path(d) / 'shoutout.json').write_text(json.dumps(
                {'last_window': 'w', 'windows': {'w': {'ts': NOW - fm.ANNOUNCE_GRACE + 5, 'skus': {EGG: {}}, 'posted': None}}}))
            m = FakeMenu(d, forum_ts=NOW - 3600)
            self.assertEqual(m.once(), {'skip': 'restock post to come'})
            m.now += 6
            self.assertTrue(m.once().get('ok'))

    def test_restock_post_naming_customers_keeps_the_long_wait(self):
        for why in ('waitlist', 'waited', 'regular'):                # review C1/I1: the window's own names, any kind
            with self.subTest(why=why), tempfile.TemporaryDirectory() as d:
                shop(d)
                (Path(d) / 'shoutout.json').write_text(json.dumps(
                    {'last_window': 'w', 'windows': {'w': {'ts': NOW - fm.ANNOUNCE_GRACE - 100,
                     'skus': {EGG: {'name': '계란', 'names': {'김콩-1a2b': why}}}, 'posted': None}}}))
                (Path(d) / 'waitlist.json').write_text(json.dumps({'entries': [{'sku': '콩나물'}]}))   # name-keyed
                m = FakeMenu(d, forum_ts=NOW - 3600)
                self.assertEqual(m.once(), {'skip': 'restock post to come'})
                m.now = NOW - fm.ANNOUNCE_GRACE - 100 + fm.ANNOUNCE_WAIT + 1
                self.assertTrue(m.once().get('ok'))

    def test_names_customers_reads_only_a_real_names_dict(self):
        mk = lambda skus: {'last_window': 'w', 'windows': {'w': {'ts': NOW, 'skus': skus}}}
        self.assertTrue(fm.names_customers(mk({EGG: {'names': {'a': 'waited'}}})))
        self.assertFalse(fm.names_customers(mk({EGG: {'names': {}}})))
        self.assertFalse(fm.names_customers(mk({EGG: {}})))
        self.assertFalse(fm.names_customers(mk([EGG])))
        self.assertFalse(fm.names_customers(None))

    def test_soon_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            m = FakeMenu(d, inv=dict(INV, restock={'open': False, 'opens_at': NOW + fm.SOON - 1, 'closes_at': NOW + 900}))
            self.assertEqual(m.once(), {'skip': 'supply window soon'})
            m.inv = dict(INV, restock={'open': False, 'opens_at': NOW + fm.SOON + 1, 'closes_at': NOW + 900})
            self.assertTrue(m.once().get('ok'))

    def test_a_window_without_restock_frees_the_slot_in_three_minutes(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            m = FakeMenu(d, inv=dict(INV, restock={'open': True, 'opens_at': NOW - 60, 'closes_at': NOW + 120}),
                         forum_ts=NOW - 7200)
            self.assertEqual(m.once(), {'skip': 'supply window'})
            m.inv = dict(INV, restock={'open': False, 'opens_at': NOW + 1200, 'closes_at': NOW + 1380})
            m.now += fm.AFTER_WINDOW_LONG + 1
            self.assertTrue(m.once().get('ok'))
            self.assertLessEqual(fm.AFTER_WINDOW_LONG, 180)

class Clearance(unittest.TestCase):
    """떨이 11/26 19:05: units on clearance lead the post at their clearance price; water never."""

    def test_clearance_first_with_its_price(self):
        cat = {'category': '식품>계란/유제품>계란', 'list_price': 12900}
        inv = {'units': [dict(unit('e1', EGG, '한계란 난각번호 1번 계란 무항생제 유정란 20구', 'C', 5000, exp=NOW + 2 * 3600), **cat),
                         dict(unit('e2', EGG, '한계란 난각번호 1번 계란 무항생제 유정란 20구', 'B', 6000, exp=NOW + 47 * 3600), **cat),
                         dict(unit('w1', WATER, '[본사공식] 몽베스트 무라벨 생수 2L', 'C', 2500, exp=NOW + 3600), **cat),
                         unit('t1', TEA, '동서 둥굴레차 50T 60g', 'B', 1800, exp=NOW + 300 * 3600)]}
        q = {f'{EGG}|B': {'price': 7975, 'ts': NOW, 'by': 'x'}, f'{TEA}|B': {'price': 2900, 'ts': NOW, 'by': 'x'},
             f'{WATER}|C': {'price': 4830, 'ts': NOW, 'by': 'x'}}
        text, skus = fm.build_text(QB, inv, q, settles(MARKET_SETTLES), NOW, 0)
        # 메인 11/27 03:5x: 2 h (egg) and 1 h (water) are under SHORT: both after tea, in the old order among themselves
        self.assertEqual(skus, [TEA, EGG, WATER])
        until = time.strftime('%H:%M', time.localtime(NOW + 2 * 3600))
        self.assertTrue(text.startswith('[노차네 구멍가게(nocha12) 지금 재고]'), text)   # 11/27 12:40: tea B leads
        self.assertIn(f' · 계란20구 C4,190(~{until})', text)
        self.assertIn('생수', text)                        # water is listed at its own price, not on clearance
        self.assertNotIn('생수2L C4,', text.split('·')[0])
        self.assertLessEqual(len(text), fm.VIEW)

    def test_three_hours_or_more_first_short_last_in_the_same_post(self):
        """메인 11/27 03:5x: lines with SHORT (3) hours or more lead; clearance under 3 h follows, prices as before."""
        cat = {'category': '식품>계란/유제품>계란', 'list_price': 12900}
        tofu = {'category': '식품>야채/과일>신선야채(부패주의)', 'list_price': 3000}
        inv = {'units': [dict(unit('e1', EGG, '한계란 난각번호 1번 계란 무항생제 유정란 20구', 'C', 5000, exp=NOW + 1 * 3600), **cat),
                         dict(unit('d1', 'G5c96ee78', '정식수입 사토노유키 실큰두부 300g', 'C', 1200, exp=NOW + 5 * 3600), **tofu),
                         unit('t1', TEA, '동서 둥굴레차 50T 60g', 'B', 1800, exp=NOW + 300 * 3600)]}
        q = {f'{TEA}|B': {'price': 2900, 'ts': NOW, 'by': 'x'}}
        text, skus = fm.build_text(QB, inv, q, settles(MARKET_SETTLES), NOW, 0)
        self.assertEqual(skus, [TEA, 'G5c96ee78', EGG])    # 11/27 06:40: 300 h tea leads, then 5 h clearance, 1 h egg last
        for u in inv['units'][:2]:                         # each line keeps its unit's own clearance price
            cl = fm.clearance(u, None, NOW)
            self.assertIsNotNone(cl)
            self.assertIn(f"C{cl['price']:,}(~", text)
        self.assertTrue(text.startswith('[노차네 구멍가게(nocha12) 지금 재고]'), text)   # 11/27 12:40: tea B leads
        self.assertLessEqual(len(text), fm.VIEW)

    def test_only_short_lines_still_posted(self):
        cat = {'category': '식품>계란/유제품>계란', 'list_price': 12900}
        inv = {'units': [dict(unit('e1', EGG, '한계란 난각번호 1번 계란 무항생제 유정란 20구', 'C', 5000, exp=NOW + 3600), **cat)]}
        text, skus = fm.build_text(QB, inv, {}, settles(MARKET_SETTLES), NOW, 0)
        self.assertEqual(skus, [EGG])                      # nothing hidden: the short line is the post
        self.assertTrue(text.startswith('[노차네 구멍가게(nocha12) 떨이·기한 임박] 계란20구 C'), text)

    def test_a_sku_with_a_longer_unit_on_clearance_shows_that_unit(self):
        cat = {'category': '식품>계란/유제품>계란', 'list_price': 12900}
        e1 = dict(unit('e1', EGG, '한계란 난각번호 1번 계란 무항생제 유정란 20구', 'C', 5000, exp=NOW + 1 * 3600), **cat)
        e2 = dict(unit('e2', EGG, '한계란 난각번호 1번 계란 무항생제 유정란 20구', 'C', 5000, exp=NOW + 6 * 3600), **cat)
        left, g, cl = fm.clear_item({'units': [e1, e2]}, EGG, {}, NOW)
        self.assertEqual((left, g), (6.0, 'C'))
        self.assertEqual(cl['price'], fm.clearance(e2, None, NOW)['price'])
        left, _, _ = fm.clear_item({'units': [e1]}, EGG, {}, NOW)   # only a short one: that one
        self.assertEqual(left, 1.0)
        a = dict(e2, unit='e3', expires_at=NOW + 4 * 3600)             # two long ones: the sooner of them
        self.assertEqual(fm.clear_item({'units': [e2, a, e1]}, EGG, {}, NOW)[0], 4.0)

    def test_short_line_not_on_clearance_also_goes_last(self):
        inv = {'units': [unit('r1', RICE, '2026년 햅쌀 국산 백미 쌀 2kg', 'C', 3000, exp=NOW + 2 * 3600),
                         unit('t1', TEA, '동서 둥굴레차 50T 60g', 'B', 1800, exp=NOW + 300 * 3600)]}
        q = {f'{RICE}|C': {'price': 4700, 'ts': NOW, 'by': 'x'}, f'{TEA}|B': {'price': 2900, 'ts': NOW, 'by': 'x'}}
        real = fm.clear_item
        fm.clear_item = lambda *a: None                     # as if no clearance applied (rice has no catalog shelf here)
        try:
            text, skus = fm.build_text(QB, inv, q, settles(MARKET_SETTLES), NOW, 0)
        finally:
            fm.clear_item = real
        self.assertEqual(skus, [TEA, RICE])
        self.assertIn('C4,700', text)

    def test_long_lines_lead_clearance_follows(self):
        """11/27 06:40: at most LEAD_LONG lines with LONG (10) hours or more lead; 3-10 h clearance stays right after."""
        tofu = {'category': '식품>야채/과일>신선야채(부패주의)', 'list_price': 3000}
        inv = {'units': [dict(unit('d1', 'G5c96ee78', '정식수입 사토노유키 실큰두부 300g', 'C', 1200, exp=NOW + 4 * 3600), **tofu),
                         unit('r1', RICE, '2026년 햅쌀 국산 백미 쌀 2kg', 'B', 3000, exp=NOW + 30 * 3600),
                         unit('t1', TEA, '동서 둥굴레차 50T 60g', 'B', 1800, exp=NOW + 300 * 3600),
                         unit('k1', EGG, '한계란 난각번호 1번 계란 무항생제 유정란 20구', 'B', 5000, exp=NOW + 9 * 3600)]}
        q = {f'{RICE}|B': {'price': 4700, 'ts': NOW, 'by': 'x'}, f'{TEA}|B': {'price': 2900, 'ts': NOW, 'by': 'x'},
             f'{EGG}|B': {'price': 7975, 'ts': NOW, 'by': 'x'}}
        real = fm.clear_item
        fm.clear_item = lambda inv_, sku, shown, now, *r: real(inv_, sku, shown, now, *r) if sku == 'G5c96ee78' else None
        try:
            text, skus = fm.build_text(QB, inv, q, settles(MARKET_SETTLES), NOW, 0)
        finally:
            fm.clear_item = real
        self.assertEqual(skus[:2], [RICE, TEA])           # 30 h and 300 h first (in the old order between them)
        self.assertEqual(skus[2], 'G5c96ee78')             # the 4 h clearance line is still in the post, right after
        self.assertIn(EGG, skus)                           # the 9 h line is not long: it keeps its old place
        self.assertTrue(text.startswith('[노차네 구멍가게(nocha12) 지금 재고]'), text)   # 11/27 12:40: rice B leads
        cl = fm.clearance(inv['units'][0], None, NOW)
        self.assertIn(f"C{cl['price']:,}(~", text)         # clearance price unchanged
        self.assertLessEqual(len(text), fm.VIEW)

    def test_no_long_line_keeps_the_old_order(self):
        cat = {'category': '식품>계란/유제품>계란', 'list_price': 12900}
        inv = {'units': [dict(unit('e1', EGG, '한계란 난각번호 1번 계란 무항생제 유정란 20구', 'C', 5000, exp=NOW + 5 * 3600), **cat),
                         unit('r1', RICE, '2026년 햅쌀 국산 백미 쌀 2kg', 'B', 3000, exp=NOW + 8 * 3600)]}
        q = {f'{RICE}|B': {'price': 4700, 'ts': NOW, 'by': 'x'}}
        text, skus = fm.build_text(QB, inv, q, settles(MARKET_SETTLES), NOW, 0)
        self.assertEqual(skus, [EGG, RICE])                # nothing 10 h+: clearance first as before

    def test_no_clearance_keeps_the_old_header(self):
        text, _ = fm.build_text(QB, INV, Text.QUOTES, settles(MARKET_SETTLES), NOW, 0)
        self.assertTrue(text.startswith('[노차네 구멍가게(nocha12) 지금 재고]'), text)


class Lead(unittest.TestCase):
    """11/27 12:40 check: 20/20 posts were led by 10-24 h clearance lines and read '떨이·기한 임박'. The lead is first
    filled with A·B lines not on clearance with LEAD_AB (24) hours or more left; clearance lines stay right after."""
    EGGCAT = {'category': '식품>계란/유제품>계란', 'list_price': 9900}
    MACK, CITR, YOG = 'GMACK0001', 'GCITR0001', 'GYOGR0001'

    def clr(self, u, sku, name, grade, hours):        # shelf 96 h -> 48: 10-24 h left is on clearance (r <= 0.5)
        return dict(unit(u, sku, name, grade, 4500, exp=NOW + hours * 3600), **self.EGGCAT)

    def clear_units(self):
        return [self.clr('m1', self.MACK, '고등어2미,800g', 'C', 20),
                self.clr('c1', self.CITR, '감귤4.5kg', 'C', 15),
                self.clr('y1', self.YOG, '그릭요거트450g×2개', 'B', 22)]   # a B line on clearance is not an A·B lead

    def test_ab_lines_lead_clearance_follows_header_now(self):
        inv = {'units': self.clear_units() + [
            unit('r1', RICE, '2026년 햅쌀 국산 백미 쌀 2kg', 'B', 3000, exp=NOW + 30 * 3600),
            unit('t1', TEA, '동서 둥굴레차 50T 60g', 'B', 1800, exp=NOW + 300 * 3600),
            unit('e1', EGG, '한계란 난각번호 1번 계란 무항생제 유정란 20구', 'C', 5000, exp=NOW + 300 * 3600)]}
        q = {f'{RICE}|B': {'price': 4700, 'ts': NOW, 'by': 'x'}, f'{TEA}|B': {'price': 2900, 'ts': NOW, 'by': 'x'},
             f'{EGG}|C': {'price': 7000, 'ts': NOW, 'by': 'x'}}
        for u in self.clear_units():
            self.assertIsNotNone(fm.clearance(u, None, NOW))
        text, skus = fm.build_text(QB, inv, q, settles(MARKET_SETTLES), NOW, 0)
        self.assertEqual(skus[:2], [RICE, TEA])            # A·B 24 h+, old order between them (30 h rice is urgent)
        self.assertEqual(skus[2], self.CITR)               # clearance right after, soonest first, as before
        self.assertTrue(text.startswith('[노차네 구멍가게(nocha12) 지금 재고] 쌀2kg B4,700 · 둥굴레차60g B2,900 · '), text)
        for u in self.clear_units():                       # clearance lines that fit keep their unit's clearance price
            if u['sku'] in skus:
                self.assertIn(f"{u['grade']}{fm.clearance(u, None, NOW)['price']:,}(~", text)
        self.assertLessEqual(len(text), fm.VIEW)

    def test_clearance_only_unchanged(self):
        inv = {'units': self.clear_units()}
        text, skus = fm.build_text(QB, inv, {}, settles(MARKET_SETTLES), NOW, 0)
        self.assertEqual(skus, [self.CITR, self.MACK, self.YOG])   # soonest first, as before (no A·B line to lead)
        until = time.strftime('%H:%M', time.localtime(NOW + 15 * 3600))
        price = fm.clearance(inv['units'][1], None, NOW)['price']
        self.assertTrue(text.startswith(f'[노차네 구멍가게(nocha12) 떨이·기한 임박] 감귤4.5kg C{price:,}(~{until}) · '), text)

    def test_one_ab_line_then_the_long_rule(self):
        inv = {'units': self.clear_units() + [unit('t1', TEA, '동서 둥굴레차 50T 60g', 'B', 1800, exp=NOW + 300 * 3600)]}
        q = {f'{TEA}|B': {'price': 2900, 'ts': NOW, 'by': 'x'}}
        text, skus = fm.build_text(QB, inv, q, settles(MARKET_SETTLES), NOW, 0)
        self.assertEqual(skus[:3], [TEA, self.CITR, self.MACK])   # tea, then the 06:40 rule's first long line
        self.assertTrue(text.startswith('[노차네 구멍가게(nocha12) 지금 재고] 둥굴레차60g B2,900 · 감귤4.5kg C'), text)

    def test_c_or_under_24_h_does_not_take_the_ab_lead(self):
        inv = {'units': self.clear_units() + [
            unit('r1', RICE, '2026년 햅쌀 국산 백미 쌀 2kg', 'B', 3000, exp=NOW + 20 * 3600),
            unit('e1', EGG, '한계란 난각번호 1번 계란 무항생제 유정란 20구', 'C', 5000, exp=NOW + 300 * 3600)]}
        q = {f'{RICE}|B': {'price': 4700, 'ts': NOW, 'by': 'x'}, f'{EGG}|C': {'price': 7000, 'ts': NOW, 'by': 'x'}}
        text, skus = fm.build_text(QB, inv, q, settles(MARKET_SETTLES), NOW, 0)
        # no A·B 24 h+ line: the 06:40 rule as before (clearance 15 h and 20 h first), header as before
        self.assertEqual(skus[:2], [self.CITR, self.MACK])
        self.assertTrue(text.startswith('[노차네 구멍가게(nocha12) 떨이·기한 임박]'), text)

    def test_undated_ab_line_leads(self):
        inv = {'units': self.clear_units() + [dict(unit('t1', TEA, '동서 둥굴레차 50T 60g', 'A', 1800), expires_at=None)]}
        q = {f'{TEA}|A': {'price': 3100, 'ts': NOW, 'by': 'x'}}
        text, skus = fm.build_text(QB, inv, q, settles(MARKET_SETTLES), NOW, 0)
        self.assertEqual(skus[0], TEA)                     # no date reads as long, as in the 06:40 rule
        self.assertTrue(text.startswith('[노차네 구멍가게(nocha12) 지금 재고]'), text)


class ForumUnits(unittest.TestCase):
    """11/27 05fcd1d5: the post read '고등어 C6,845(~22:51)' (the 10.5 h unit), check_item_inventory listed both C units and
    the checkout went out on the 2.5 h one. Each post that goes up now keeps its lines (clearance lines with their unit
    and expiry) in logs/forum-menu-units.json for check_item_inventory to mark."""
    CAT = {'category': '식품>해산물>신선생선', 'list_price': 11900}   # shelf 24 h
    MACK = 'GMACK0001'

    def inv(self):
        name = '국산 고등어 2미 800g'
        return {'units': [dict(unit('u-34f1', self.MACK, name, 'C', 3000, exp=NOW + 2.5 * 3600), **self.CAT),
                          dict(unit('u-0222', self.MACK, name, 'C', 3000, exp=NOW + 10.5 * 3600), **self.CAT),
                          unit('t1', TEA, '동서 둥굴레차 50T 60g', 'B', 1800, exp=NOW + 300 * 3600)],
                'restock': INV['restock']}

    Q = {f'{TEA}|B': {'price': 2900, 'ts': NOW, 'by': 'x'}}

    def test_lines_name_the_posted_clearance_unit(self):
        posted = []
        text, skus = fm.build_text(QB, self.inv(), self.Q, settles(MARKET_SETTLES), NOW, 0, posted=posted)
        self.assertEqual((text, skus), fm.build_text(QB, self.inv(), self.Q, settles(MARKET_SETTLES), NOW, 0))  # unchanged
        self.assertEqual([p['sku'] for p in posted], skus)
        by = {p['sku']: p for p in posted}
        long_unit = self.inv()['units'][1]
        cl = fm.clearance(long_unit, None, NOW)
        self.assertEqual(by[self.MACK], {'sku': self.MACK, 'grade': 'C', 'price': cl['price'], 'unit': 'u-0222',
                                         'until': NOW + 10.5 * 3600})   # the one the text shows, not the 2.5 h one
        self.assertIn(f"C{cl['price']:,}(~{time.strftime('%H:%M', time.localtime(NOW + 10.5 * 3600))})", text)
        self.assertEqual(by[TEA], {'sku': TEA, 'grade': 'B', 'price': 2900, 'unit': None, 'until': None})

    def test_posted_menu_is_kept_and_old_posts_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            f = Path(d) / 'logs' / 'forum-menu-units.json'
            f.write_text(json.dumps({'posts': [{'ts': NOW - fm.UNITS_KEEP - 1, 'post': 1, 'lines': []},
                                               {'ts': NOW - 600, 'post': 2, 'lines': [{'unit': 'u-old'}]}, 'x']}))
            m = FakeMenu(d, inv=self.inv())
            r = m.once()
            self.assertTrue(r['ok'], r)
            posts = json.loads(f.read_text())['posts']
            self.assertEqual([p['post'] for p in posts], [2, 7])            # the expired one and the odd row are gone
            self.assertEqual(posts[-1]['ts'], m.now)
            self.assertEqual([ln['sku'] for ln in posts[-1]['lines']], r['skus'])
            self.assertIn({'sku': self.MACK, 'grade': 'C', 'price': fm.clearance(self.inv()['units'][1], None, NOW)['price'],
                           'unit': 'u-0222', 'until': NOW + 10.5 * 3600}, posts[-1]['lines'])
            self.assertEqual(list(Path(d, 'logs').glob('*.tmp')), [])

    def test_kept_posts_are_capped(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            f = Path(d) / 'logs' / 'forum-menu-units.json'
            f.write_text(json.dumps({'posts': [{'ts': NOW - 60 - i, 'post': 100 + i, 'lines': []} for i in range(60)]}))
            self.assertTrue(FakeMenu(d, inv=self.inv()).once()['ok'])
            posts = json.loads(f.read_text())['posts']
            self.assertEqual(len(posts), fm.UNITS_MAX)
            self.assertEqual(posts[-1]['post'], 7)

    def test_refused_post_or_bad_file_never_breaks_the_menu(self):
        with tempfile.TemporaryDirectory() as d:
            shop(d)
            f = Path(d) / 'logs' / 'forum-menu-units.json'
            r = FakeMenu(d, inv=self.inv(), post_code=500).once()
            self.assertFalse(r['ok'])
            self.assertFalse(f.exists())                                     # nothing went up: nothing kept
            f.write_text('{not json')
            r = FakeMenu(d, inv=self.inv()).once()
            self.assertTrue(r['ok'], r)
            self.assertEqual([p['post'] for p in json.loads(f.read_text())['posts']], [7])
            f.unlink()
            f.mkdir()                                                        # unwritable: the post still goes on
            r = FakeMenu(d, inv=self.inv()).once()
            self.assertTrue(r['ok'], r)
            self.assertEqual(list(Path(d, 'logs').glob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
