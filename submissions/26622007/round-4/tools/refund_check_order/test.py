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
