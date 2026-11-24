import os


def test(run, kit):
    """The whole path in test mode: sold-out request, purchases, restock, one forum text, notices, caps, opt-out."""
    for f in ('shoutout.test.json', 'shoutout.test.json.tmp'):
        if os.path.exists(f):
            os.remove(f)
    t0 = 1_800_000_000
    milk = {'unit': 'u1', 'sku': 'MILK', 'name': '[서울우유] 흰우유 1L 24팩', 'kind': 'food', 'cost': 20000, 'grade': 'A'}
    rice = {'unit': 'u2', 'sku': 'RICE', 'name': '백진주쌀 20kg', 'kind': 'food', 'cost': 59000, 'grade': 'B'}
    empty = {'units': [], 'restock': {'open': False}}
    both = {'units': [milk, dict(milk, unit='u3', grade='B'), rice], 'restock': {'open': True, 'opens_at': t0}}
    sales = {'o1': {'order': 'o1', 'tx_hash': '0x1'}, 'o2': {'order': 'o2', 'tx_hash': '0x2'}, 'o3': {'order': 'o3', 'tx_hash': '0x3'}}
    jin = "a customer that says it is '진주댁'"
    try:
        assert run({'op': 'want', 'customer': 'a person in your app', 'sku': 'MILK', '_inventory': empty}, kit)['anonymous']
        assert run({'op': 'want', 'customer': jin, 'sku': 'MILK', 'qty': 2, 'price': 15000, '_inventory': empty, '_now': t0}, kit)['ok']
        r = run({'op': 'want', 'customer': '박씨', 'sku': 'RICE', '_inventory': both, '_now': t0}, kit)
        assert r['in_stock'] == 1 and not r['ok']                     # in stock: no sold-out record
        assert not run({'op': 'bought', 'customer': '김씨', 'sku': 'MILK', 'order': 'nope', '_sales': sales}, kit)['ok']
        for o in ('o1', 'o2', 'o2'):
            r = run({'op': 'bought', 'customer': '김씨', 'sku': 'MILK', 'order': o, '_sales': sales, '_now': t0}, kit)
        assert r['count'] == 2 and r['duplicate']                    # the same order counts once
        run({'op': 'bought', 'customer': '최씨', 'sku': 'MILK', 'order': 'o3', '_sales': sales, '_now': t0}, kit)
        assert not run({'op': 'restocked', 'sku': 'EGG', 'price': 9000, '_inventory': both, '_now': t0}, kit)['ok']
        assert run({'op': 'restocked', 'sku': 'MILK', 'price': 100, '_inventory': both, '_now': t0}, kit)['floor'] > 100
        r = run({'op': 'restocked', 'sku': 'MILK', 'price': 26000, '_inventory': both, '_now': t0 + 10}, kit)
        got = {p['customer']: p['why'] for p in r['picked']}
        assert got == {'진주댁': 'waited', '김씨': 'regular'}, got      # 최씨 bought once: not a regular
        r = run({'op': 'restocked', 'sku': 'MILK', 'price': 26000, '_inventory': both, '_now': t0 + 20}, kit)
        assert r['picked'] == [] and r['skipped'].get('김씨') == 'already_this_restock'
        run({'op': 'restocked', 'sku': 'RICE', 'price': 78000, '_inventory': both, '_now': t0 + 30}, kit)
        a = run({'op': 'announce', '_inventory': both, '_forum': [], '_now': t0 + 40}, kit)
        assert a['ok'] and '@진주댁님' in a['text'] and '김씨' not in a['text'] and '백진주쌀' in a['text'], a  # regulars: notice only
        n = run({'op': 'notice', 'customer': jin, '_inventory': both, '_now': t0 + 50}, kit)['notice']
        assert '2개' in n and 'A·B등급' in n and '26,000' in n and '그만' in n, n
        assert run({'op': 'notice', 'customer': jin, '_inventory': both, '_now': t0 + 60}, kit)['notice'] is None
        assert run({'op': 'notice', 'customer': '김씨', '_inventory': empty, '_now': t0 + 60}, kit)['notice'] is None  # sold out: say nothing
        assert run({'op': 'opt_out', 'customer': '김씨', '_now': t0 + 70}, kit)['opt_out']
        later = dict(both, restock={'open': True, 'opens_at': t0 + 7200})
        r = run({'op': 'restocked', 'sku': 'MILK', 'price': 26000, '_inventory': later, '_now': t0 + 7200}, kit)
        assert r['skipped'].get('김씨') == 'opt_out'
        bad = [{'author': kit.name, 'ts': t0 + 7000, 'dislikes': 3}]
        assert not run({'op': 'announce', '_inventory': later, '_forum': bad, '_now': t0 + 7210}, kit)['ok']
        return 'ok'
    finally:
        if os.path.exists('shoutout.test.json'):
            os.remove('shoutout.test.json')
