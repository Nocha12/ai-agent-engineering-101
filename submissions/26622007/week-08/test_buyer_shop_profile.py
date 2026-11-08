import json, os, tempfile, time, unittest

import buyer_shop_profile as sp


def write(md, name, body):
    with open(os.path.join(md, name), 'w', encoding='utf-8') as f:
        f.write(body if isinstance(body, str) else json.dumps(body))


class SwitchTest(unittest.TestCase):
    def test_off_gives_back_the_plan_a_percent_exactly(self):
        with tempfile.TemporaryDirectory() as md:
            for body in (None, '{"enabled": false}', 'not json', '[]', '{"enabled": 1}'):
                if body is not None:
                    write(md, sp.SWITCH, body)
                for base in (None, 105):
                    self.assertEqual(sp.percent(md, 'mjforge', base, list_price=1000, chance=0, limit=680, env={}),
                                     base, body)


NOW = 1_800_000_000.0


def talk(shop, asks, limit=1000, deal=None, order=None, ts=NOW - 60, kit=False):
    r = {'ts': ts, 'order': order or f'o-{shop}-{len(asks)}-{asks[-1]}-{deal}-{ts}', 'shop': shop, 'limit': limit,
         'asks': asks, 'state': 'TASK_STATE_COMPLETED' if deal else 'TASK_STATE_CANCELED', 'price': deal,
         'passed_to_kit': kit}
    return r


def holder(shop, fails, deals=10, n=None):
    """a shop that never lowers its ask: each failed talk at that share of my limit"""
    rows = [talk(shop, [int(1000 * x), int(1000 * x)], order=f'{shop}-f{i}') for i, x in enumerate(fails)]
    rows += [talk(shop, [1000, 1000], deal=1000, order=f'{shop}-d{i}', kit=True) for i in range(deals)]
    return rows


class ProfileTest(unittest.TestCase):
    def test_a_holding_shop_gets_the_80th_percentile_of_its_near_misses(self):
        rows = holder('H', [1.02] * 5 + [1.06] * 5 + [1.08] * 5 + [1.2] * 5)
        self.assertEqual(sp.profile_from_rows(rows, {}, now=NOW), {'H': 108})

    def test_a_shop_that_comes_down_keeps_plan_a(self):
        rows = [talk('C', [1100, 1000, 1080 - i % 3], order=f'c{i}') for i in range(20)]
        rows += [talk('C', [1000, 950], deal=950, order=f'cd{i}') for i in range(10)]
        self.assertEqual(sp.profile_from_rows(rows, {}, now=NOW), {})

    def test_too_few_talks_old_talks_and_far_misses_keep_plan_a(self):
        self.assertEqual(sp.profile_from_rows(holder('S', [1.08] * 19), {}, now=NOW), {})          # 29 talks
        old = [dict(r, ts=NOW - 25 * 3600) for r in holder('O', [1.08] * 30)]
        self.assertEqual(sp.profile_from_rows(old, {}, now=NOW), {})
        self.assertEqual(sp.profile_from_rows(holder('F', [1.08] * 5 + [1.3] * 20), {}, now=NOW), {})  # 5/25 near

    def test_never_over_110(self):
        self.assertEqual(sp.profile_from_rows(holder('M', [1.099] * 20 + [1.3] * 5), {}, now=NOW), {'M': 109})

    def test_a_shop_whose_deals_the_kit_could_not_pay_is_not_raised(self):
        rows = holder('P', [1.08] * 20)
        paid = {r['order']: i % 2 == 0 for i, r in enumerate(x for x in rows if x['price'])}
        self.assertEqual(sp.profile_from_rows(rows, paid, now=NOW), {})
        self.assertEqual(sp.profile_from_rows(rows, {k: True for k in paid}, now=NOW), {'P': 108})


def market_dir(md, rows, payments=()):
    os.makedirs(os.path.join(md, 'measure'), exist_ok=True)
    with open(os.path.join(md, 'measure', 'orders.jsonl'), 'w') as f:
        f.write('\n'.join(json.dumps(r) for r in rows) + '\n')
    with open(os.path.join(md, 'payments.jsonl'), 'w') as f:
        f.write(''.join(json.dumps(p) + '\n' for p in payments))
    write(md, sp.SWITCH, {'enabled': True})


class PercentTest(unittest.TestCase):
    def test_on_a_profiled_shop_takes_its_percent_from_the_log(self):
        with tempfile.TemporaryDirectory() as md:
            market_dir(md, holder('H', [1.02] * 5 + [1.06] * 5 + [1.08] * 5 + [1.2] * 5))
            self.assertEqual(sp.percent(md, 'H', 105, list_price=1000, chance=0, limit=680, env={}, now=NOW), 108)
            self.assertEqual(sp.percent(md, 'other', 105, list_price=1000, chance=0, limit=680, env={}, now=NOW), 105)
            self.assertIsNone(sp.percent(md, 'other', None, list_price=1000, chance=0, limit=680, env={}, now=NOW))
            self.assertEqual(sp.percent(md, 'H', None, list_price=1000, chance=0, limit=680, env={}, now=NOW), 108)
            self.assertEqual(sp.percent(md, 'H', 105, 1000, 0, 680, env={'BUYER_SHOP_PROFILE': 'off'}, now=NOW), 105)

    def test_a_deal_keeps_a_quarter_of_the_list_price_in_expectation(self):
        with tempfile.TemporaryDirectory() as md:
            market_dir(md, holder('H', [1.08] * 20))
            # list 1000, b 0: at most 750; limit 700 -> 107% (749), not 108% (756)
            self.assertEqual(sp.percent(md, 'H', 105, list_price=1000, chance=0, limit=700, env={}, now=NOW), 107)
            # b 0.2: at most 1000 (1 - 0.25 / 0.8) = 687; limit 680 -> 101, so plan A's 105 stays (never under it)
            self.assertEqual(sp.percent(md, 'H', 105, list_price=1000, chance=0.2, limit=680, env={}, now=NOW), 105)

    def test_no_log_or_a_broken_log_keeps_plan_a(self):
        with tempfile.TemporaryDirectory() as md:
            write(md, sp.SWITCH, {'enabled': True})
            self.assertEqual(sp.percent(md, 'H', 105, 1000, 0, 680, env={}, now=NOW), 105)
            os.makedirs(os.path.join(md, 'measure'))
            write(md, 'measure/orders.jsonl', 'garbage\n{"shop": 1}\n')
            write(md, sp.CACHE, 'garbage')
            self.assertEqual(sp.percent(md, 'H', 105, 1000, 0, 680, env={}, now=NOW + 7200), 105)


if __name__ == '__main__':
    unittest.main()
