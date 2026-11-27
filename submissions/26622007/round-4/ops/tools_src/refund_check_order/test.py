import os


def test(run, kit):
    for f in ('logs/refund-plans.test.jsonl', 'logs/refunds.test.jsonl', 'logs/sends.test.jsonl'):
        if os.path.exists(f):
            os.remove(f)
    tx = '0x' + 'a' * 64
    settle = {'kind': 'settle', 'seq': 10, 'data': {'order': 't-o1', 'amount': 10000, 'shop_fee': 310, 'tx_hash': tx, 'warranty_hours': 24}}
    broken = {'kind': 'broken', 'seq': 20, 'data': {'order': 't-o1', 'hours_after_sale': 5.0, 'warranty_hours': 24, 'refunded': 0}}
    late = {'kind': 'broken', 'seq': 20, 'data': {'order': 't-o1', 'hours_after_sale': 30.0, 'warranty_hours': 24}}
    try:
        assert not run({}, kit)['ok']
        assert '고장' in run({'order': 't-o1', '_sealed': [settle]}, kit)['error']
        assert '보증' in run({'order': 't-o1', '_sealed': [settle, late]}, kit)['error']
        assert '403' in run({'order': 't-o1', '_sealed_status': 403}, kit)['error']
        r = run({'order': 't-o1', '_sealed': [settle, broken]}, kit)
        assert r['ok'] and r['plan']['amount'] == 9690 and r['plan']['gross'] == 10000, r   # net = 10000 - 310
        miss = {'event': 'unsettled', 'order': 't-o2', 'tx_hash': '0x' + 'b' * 64, 'digest_hex': 'ab' * 32, 'price': 5000}
        r = run({'order': 't-o2', '_checkouts': [miss]}, kit)
        assert r['ok'] and r['plan']['kind'] == 'unsettled' and r['plan']['amount'] is None, r
        assert not run({'order': 't-o2', '_checkouts': [miss], '_sales': [{'order': 'x', 'tx_hash': miss['tx_hash']}]}, kit)['ok']
        assert '없습니다' in run({'order': 't-o3'}, kit)['error']
        nofee = dict(settle, data={k: v for k, v in settle['data'].items() if k != 'shop_fee'})
        assert run({'order': 't-o1', '_sealed': [nofee, broken]}, kit)['plan']['amount'] == 9690   # 10000//100 + 210
        twice = [miss, dict(miss, order='t-o9')]
        assert '다른 주문' in run({'order': 't-o2', '_checkouts': twice}, kit)['error']
        # the unsettled path keeps its plan, also when the ledger has an owed line for the same payment
        r = run({'order': 't-o2', '_checkouts': [miss], '_payments': [{'kind': 'paid_unsettled', 'id': 'owed-' + miss['tx_hash'],
                 'tx_hash': miss['tx_hash'], 'amount': 4740.0, 'payer': '0x' + '1' * 40, 'checkout_order': 't-o2'}]}, kit)
        assert r['ok'] and r['plan']['kind'] == 'unsettled' and set(r['plan']) == {
            'kind', 'source', 'tx_hash', 'digest_hex', 'gross', 'shop_fee', 'amount', 'stage', 'order', 'ts', 'basis'}, r
        # balance-diff-rca-1127: paid with no settle and no unsettled record, only the ledger's owed-<tx> line
        otx, payer = '0x' + 'c' * 64, '0x' + '1' * 40
        issued = {'event': 'issued', 'order': 'ck-1', 'price': 13470, 'digest_hex': 'cd' * 32, 'unit': 'u-1'}
        owed = {'kind': 'paid_unsettled', 'id': 'owed-' + otx, 'tx_hash': otx, 'amount': 13126.0, 'payer': payer,
                'checkout_order': 'ck-1', 'order': 'o-m1', 'held_order': None}
        base = {'_checkouts': [issued], '_payments': [owed]}
        for oid in ('ck-1', 'o-m1'):                                         # our checkout id or the market order id
            r = run(dict(base, order=oid), kit)
            assert r['ok'] and r['plan']['kind'] == 'owed' and r['plan']['amount'] == 13126 and r['plan']['payer'] == payer \
                and r['plan']['tx_hash'] == otx and r['plan']['digest_hex'] == 'cd' * 32 and r['plan']['gross'] == 13470 \
                and r['plan']['shop_fee'] == 344, r                       # 13470 - (13470 // 100 + 210)
        assert '없습니다' in run({'order': 'ck-1', '_checkouts': [issued]}, kit)['error']             # no owed line
        assert '없습니다' in run({'order': 'ck-2', '_checkouts': [issued], '_payments': [owed]}, kit)['error']
        rl = {'kind': 'refund', 'id': 'refund-0x' + 'e' * 64, 'tx_hash': '0x' + 'e' * 64, 'order': 'o-m1', 'said_order': 'ck-1'}
        for line in (rl, dict(rl, said_order=None), dict(rl, order='ck-1', said_order=None)):
            assert '이미' in run(dict(base, order='ck-1', _payments=[owed, line]), kit)['error']   # the ledger paired a refund
        sold = {'kind': 'sale', 'id': 'sale-o-m1', 'tx_hash': otx, 'note': 'reclassifies owed-' + otx + ': settled later'}
        assert '판매' in run(dict(base, order='ck-1', _payments=[owed, sold]), kit)['error']
        assert '판매' in run(dict(base, order='ck-1', _sales=[{'order': 'o-x', 'tx_hash': otx}]), kit)['error']
        assert '판매' in run(dict(base, order='ck-1', _sales=[{'order': 'o-m1', 'tx_hash': '0x' + 'f' * 64}]), kit)['error']
        assert '판매' in run(dict(base, order='ck-1', _checkouts=[issued, {'event': 'settled', 'order': 'ck-1'}]), kit)['error']
        assert '금액' in run(dict(base, order='ck-1', _payments=[dict(owed, amount=13000.0)]), kit)['error']
        assert '금액' in run(dict(base, order='ck-1', _checkouts=[dict(issued, price=13000)]), kit)['error']
        assert '주소' in run(dict(base, order='ck-1', _payments=[dict(owed, payer='0x12')]), kit)['error']
        assert not run(dict(base, order='ck-1', _payments=[dict(owed, held_order='o-h')]), kit)['ok']   # maybe a sale's
        assert '결제서' in run({'order': 'ck-1', '_payments': [owed]}, kit)['error']                    # no checkout of ours
        assert not run(dict(base, order='ck-1', _payments=[owed, dict(owed, id='owed-0x' + 'd' * 64, tx_hash='0x' + 'd' * 64)]), kit)['ok']
        assert not run(dict(base, order='ck-1', _checkouts=[issued, dict(issued, order='ck-9')]), kit)['ok']   # one digest, two orders
        with open('logs/refunds.test.jsonl', 'w') as f:
            f.write('{"purpose":"refund","order":"other","paid_tx":"%s"}\n' % otx)
        assert '이미' in run(dict(base, order='ck-1'), kit)['error']                                   # refunds.jsonl, same payment
        os.remove('logs/refunds.test.jsonl')
        with open('logs/refunds.test.jsonl', 'w') as f:
            f.write('{"purpose":"refund","order":"t-o9","paid_tx":"%s","tx_hash":"0x1"}\n' % miss['tx_hash'])
        assert '이미' in run({'order': 't-o2', '_checkouts': [miss]}, kit)['error']                # same payment, other id
        with open('logs/refunds.test.jsonl', 'w') as f:
            f.write('{"purpose":"refund","order":"t-o1","tx_hash":"0x1"}\n')
        assert '이미' in run({'order': 't-o1', '_sealed': [settle, broken]}, kit)['error']
        return 'ok'
    finally:
        for f in ('logs/refund-plans.test.jsonl', 'logs/refunds.test.jsonl', 'logs/sends.test.jsonl'):
            if os.path.exists(f):
                os.remove(f)
