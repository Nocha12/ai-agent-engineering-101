"""Tests for restock_shoutout/logic.py, offline: the tool's own kit test plus the live path with a fake market.
Run: python3 -I test_restock_shoutout.py   (--dry-run prints the texts a restock would produce, nothing is sent)"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / 'restock_shoutout'
T0 = 1_800_000_000
MILK = {'unit': 'u1', 'sku': 'MILK', 'name': '[서울우유] 흰우유 1L 24팩', 'kind': 'food', 'cost': 20000, 'grade': 'A'}
RICE = {'unit': 'u2', 'sku': 'RICE', 'name': '[안동농협] 백진주쌀 20kg', 'kind': 'food', 'cost': 59634, 'grade': 'B'}


class Kit:
    def __init__(self, testing):
        self.testing, self.name = testing, 'nocha12'

    def token(self, role):
        return 'offline'


def load():
    ns = {'__name__': 'logic'}
    exec(compile((TOOL / 'logic.py').read_text(encoding='utf-8'), 'logic.py', 'exec'), ns)
    return ns


class Market:
    """What the live path reads and writes, in memory."""

    def __init__(self, ns):
        self.inv, self.posts, self.sent = {'units': []}, [], []
        ns['inventory'] = lambda kit, args: self.inv
        ns['forum'] = lambda kit, args: self.posts
        ns['post'] = self.post

    def post(self, kit, text):
        self.sent.append(text)
        self.posts.append({'id': len(self.sent), 'author': 'nocha12', 'ts': self.now, 'dislikes': 0, 'text': text})
        return 200, {'post': len(self.sent)}


class Live(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        Path('logs').mkdir()
        self.ns = load()
        self.m = Market(self.ns)
        self.kit = Kit(False)
        self.clock = [T0]
        self.ns['time'] = type('T', (), {'time': staticmethod(lambda: self.clock[0])})
        self.m.now = T0

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def call(self, **args):
        return self.ns['run'](args, self.kit)

    def at(self, t):
        self.clock[0] = self.m.now = t

    def sale(self, order, tx='0xabc'):
        with open('logs/sales.jsonl', 'a', encoding='utf-8') as f:
            f.write(json.dumps({'order': order, 'amount': 26000, 'tx_hash': tx}) + '\n')

    def test_kit_test_passes(self):
        k = Kit(True)
        test_ns = {}
        exec(compile((TOOL / 'test.py').read_text(encoding='utf-8'), 'test.py', 'exec'), test_ns)
        self.assertEqual(test_ns['test'](load()['run'], k), 'ok')         # unpatched: test mode reads args, not the network
        self.assertFalse(Path('shoutout.test.json').exists())

    def test_bought_needs_settle_record(self):
        self.assertFalse(self.call(op='bought', customer='김씨', sku='MILK', order='o1')['ok'])
        self.sale('o1', tx='')
        self.assertFalse(self.call(op='bought', customer='김씨', sku='MILK', order='o1')['ok'])   # no tx_hash
        self.sale('o2')
        self.assertTrue(self.call(op='bought', customer='김씨', sku='MILK', order='o2')['ok'])
        self.assertTrue(Path('shoutout.json').exists())

    def test_full_restock_writes_one_post_per_window(self):
        self.call(op='want', customer="it says it is '진주댁'", sku='MILK', qty=2)
        for o in ('o1', 'o2'):
            self.sale(o)
            self.call(op='bought', customer='김씨', sku='RICE', order=o)
        self.m.inv = {'units': [MILK, dict(MILK, unit='u9'), RICE], 'restock': {'open': True, 'opens_at': T0}}
        self.assertTrue(self.call(op='restocked', sku='MILK', price=26000)['ok'])
        self.assertTrue(self.call(op='restocked', sku='RICE', price=78675)['ok'])
        self.at(T0 + 60)
        r = self.call(op='announce')
        self.assertTrue(r['ok'], r)
        self.assertEqual(len(self.m.sent), 1)
        self.assertIn('@진주댁님', r['text'])
        self.assertIn('@김씨님', r['text'])
        self.assertFalse(self.call(op='announce')['ok'])                  # the same window: no second post
        self.assertEqual(len(self.m.sent), 1)

    def test_day_cap_and_sold_out_notice(self):
        self.call(op='want', customer='박씨', sku='MILK')
        self.call(op='want', customer='박씨', sku='RICE')
        self.call(op='want', customer='박씨', sku='EGG')
        egg = dict(MILK, unit='u5', sku='EGG', name='계란 30구', cost=6000)
        self.m.inv = {'units': [MILK, RICE, egg], 'restock': {'open': True, 'opens_at': T0}}
        picks = [self.call(op='restocked', sku=s, price=90000)['picked'] for s in ('MILK', 'RICE', 'EGG')]
        self.assertEqual([len(p) for p in picks], [1, 1, 0])               # two events a day, the third waits
        self.m.inv = {'units': [RICE], 'restock': {'open': False}}           # the milk sold before 박씨 came back
        n = self.call(op='notice', customer='박씨')['notice']
        self.assertNotIn('우유', n)
        self.assertIn('백진주쌀', n)
        self.at(T0 + 86400 + 10)                                             # a day later the cap is free again
        self.m.inv = {'units': [egg], 'restock': {'open': True, 'opens_at': T0 + 86400}}
        self.assertEqual(len(self.call(op='restocked', sku='EGG', price=9000)['picked']), 1)

    def test_opt_out_removes_name_and_notice(self):
        self.call(op='want', customer='최씨', sku='MILK')
        self.m.inv = {'units': [MILK], 'restock': {'open': True, 'opens_at': T0}}
        self.call(op='restocked', sku='MILK', price=26000)
        self.call(op='opt_out', customer='최씨')
        r = self.call(op='announce')
        self.assertNotIn('최씨', r['text'])
        self.assertIsNone(self.call(op='notice', customer='최씨')['notice'])

    def test_never_claims_missing_stock(self):
        self.call(op='want', customer='최씨', sku='MILK')
        self.assertFalse(self.call(op='restocked', sku='MILK', price=26000)['ok'])      # not in inventory
        self.m.inv = {'units': [MILK], 'restock': {'open': True, 'opens_at': T0}}
        self.call(op='restocked', sku='MILK', price=26000)
        self.m.inv = {'units': [], 'restock': {'open': False}}
        self.assertFalse(self.call(op='announce')['ok'])
        self.assertEqual(self.m.sent, [])

    def test_price_floor(self):
        self.m.inv = {'units': [MILK], 'restock': {'open': True, 'opens_at': T0}}
        r = self.call(op='restocked', sku='MILK', price=20000)
        self.assertFalse(r['ok'])
        self.assertEqual(r['floor'], 20622)                                   # (20000+205+210)/0.99 = 20621.2, rounded up

    def test_dislikes_slow_then_pause(self):
        self.m.inv = {'units': [MILK], 'restock': {'open': True, 'opens_at': T0}}
        self.call(op='want', customer='최씨', sku='MILK')
        self.m.posts = [{'author': 'nocha12', 'ts': T0 - 3 * 3600, 'dislikes': 1}]
        self.call(op='restocked', sku='MILK', price=26000)
        r = self.call(op='announce')
        self.assertFalse(r['ok'])
        self.assertIn('6시간', r['error'])
        self.m.posts = [{'author': 'nocha12', 'ts': T0 - 600, 'dislikes': 3}]
        self.assertIn('24시간', self.call(op='announce')['error'])
        self.at(T0 + 7 * 3600)
        self.m.posts = []
        self.assertIn('쉬는 중', self.call(op='announce')['error'])
        self.assertEqual(self.m.sent, [])

    def test_anonymous_and_other_shops_ignored(self):
        self.assertTrue(self.call(op='want', customer='a person in your app', sku='MILK')['anonymous'])
        self.assertTrue(self.call(op='notice', customer="did not say who it is")['anonymous'])


def dry_run():
    """Print what a restock of milk and rice would say, using the tool in test mode (nothing is posted)."""
    tmp = tempfile.TemporaryDirectory()
    os.chdir(tmp.name)
    ns, kit = load(), Kit(True)
    inv = {'units': [MILK, dict(MILK, unit='u9', grade='B'), RICE, dict(RICE, unit='u8')],
           'restock': {'open': True, 'opens_at': T0}}
    empty = {'units': [], 'restock': {'open': False}}
    sales = {o: {'order': o, 'tx_hash': '0x1'} for o in ('o1', 'o2', 'o3')}
    run = ns['run']
    steps = [
        ('품절 기록', {'op': 'want', 'customer': "a customer that says it is '진주댁'", 'sku': 'MILK', 'qty': 2,
                     'price': 18000, '_inventory': empty, '_now': T0 - 7200}),
        ('구매 1', {'op': 'bought', 'customer': '골목할머니', 'sku': 'RICE', 'order': 'o1', '_sales': sales, '_now': T0 - 9000}),
        ('구매 2', {'op': 'bought', 'customer': '골목할머니', 'sku': 'RICE', 'order': 'o2', '_sales': sales, '_now': T0 - 3600}),
        ('입고 우유', {'op': 'restocked', 'sku': 'MILK', 'price': 26000, '_inventory': inv, '_now': T0}),
        ('입고 쌀', {'op': 'restocked', 'sku': 'RICE', 'price': 78675, '_inventory': inv, '_now': T0 + 5}),
        ('같은 입고 다시', {'op': 'restocked', 'sku': 'MILK', 'price': 26000, '_inventory': inv, '_now': T0 + 9}),
        ('포럼 공지(드라이런)', {'op': 'announce', 'dry_run': True, '_inventory': inv, '_forum': [], '_now': T0 + 20}),
        ('진주댁 다음 방문', {'op': 'notice', 'customer': '진주댁', '_inventory': inv, '_now': T0 + 600}),
        ('골목할머니 다음 방문(단골 안내)', {'op': 'notice', 'customer': '골목할머니', '_inventory': inv, '_now': T0 + 650}),
        ('골목할머니 그만', {'op': 'opt_out', 'customer': '골목할머니', '_now': T0 + 700}),
        ('골목할머니 그만 뒤 방문', {'op': 'notice', 'customer': '골목할머니', '_inventory': inv, '_now': T0 + 800}),
    ]
    for label, a in steps:
        r = run(a, kit)
        print(f'## {label}\n{json.dumps(r, ensure_ascii=False)}')
    os.chdir('/')
    tmp.cleanup()


if __name__ == '__main__':
    if '--dry-run' in sys.argv:
        dry_run()
    else:
        unittest.main()
