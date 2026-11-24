import os

FILES = ('logs/checkouts.test.jsonl', 'logs/sales.test.jsonl', 'logs/refunds.test.jsonl')


def test(run, kit):
    for f in FILES:
        if os.path.exists(f):
            os.remove(f)
    os.makedirs('logs', exist_ok=True)
    shop = kit.address('shop')
    unit = {'unit': 'u-t1', 'sku': 'G1', 'name': '쌀 20kg', 'kind': 'food', 'cost': 59634, 'grade': 'B', 'expires_at': None,
            'cert': {'unit': 'u-t1', 'grade': 'B'}}
    inv = {'wallet': shop, 'units': [unit]}
    a = {'op': 'create', 'unit': 'u-t1', 'price': 78675, 'task': 't', 'warranty_hours': 0, 'claim': 'B', 'order': 'o-t1',
         '_inventory': inv, '_registered': shop}
    try:
        assert '재고' in run(dict(a, unit='u-gone'), kit)['error']
        assert '지갑' in run(dict(a, _inventory=dict(inv, wallet='0x' + '9' * 40)), kit)['error']
        assert '지갑' in run(dict(a, _registered='0x' + '9' * 40), kit)['error']
        assert '등급' in run(dict(a, claim='A'), kit)['error']
        assert '등급' in run(dict(a, _inventory=dict(inv, units=[dict(unit, cert={'grade': 'A'})])), kit)['error']
        assert run(dict(a, price=60000), kit)['floor'] == 60656                 # ceil((59634 + 205 + 210) / 0.99)
        assert not run(dict(a, warranty_hours=73), kit)['ok']
        r = run(a, kit)
        assert r['ok'] and r['payload']['private'] is True and r['payload']['claim'] == 'B', r
        assert '열려' in run(dict(a, order='o-t2'), kit)['error']               # one open checkout per unit
        mand = {'mandate': {'tx_hash': '0x' + 'a' * 64}}
        s = {'op': 'settle', 'order': 'o-t1', 'checkout_jwt': r['checkout_jwt'], 'checkout_mandate': {}, 'payment_mandate': mand,
             '_inventory': {'wallet': shop, 'units': []}}
        assert '우리가 낸' in run(dict(s, checkout_jwt='x.y.z'), kit)['error']
        assert '다른 주문' in run(dict(s, order='o-other'), kit)['error']
        with open('logs/refunds.test.jsonl', 'w') as f:
            f.write('{"purpose":"refund","order":"o-x","paid_tx":"0x%s","tx_hash":"0x1"}\n' % ('a' * 64))
        assert '환불' in run(s, kit)['error']
        os.remove('logs/refunds.test.jsonl')
        g = run(s, kit)
        assert g.get('refund') is True, g                                       # paid, unit gone: refund path
        with open(FILES[0]) as f:
            assert '"unsettled"' in f.read()
        return 'ok'
    finally:
        for f in FILES:
            if os.path.exists(f):
                os.remove(f)
