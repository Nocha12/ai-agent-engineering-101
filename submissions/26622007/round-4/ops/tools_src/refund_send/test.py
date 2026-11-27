import json
import os
import time

FILES = ('logs/refund-plans.test.jsonl', 'logs/refunds.test.jsonl', 'logs/sends.test.jsonl')


def test(run, kit):
    shop, payer, tx = kit.address('shop'), '0x' + '1' * 40, '0x' + 'a' * 64
    for f in FILES:
        if os.path.exists(f):
            os.remove(f)
    os.makedirs('logs', exist_ok=True)
    now = int(time.time())
    paid = {'from': payer, 'to': shop, 'value': hex(9690 * 10 ** 15)}
    try:
        assert '1·2단계' in run({'order': 't-o1'}, kit)['error']
        with open(FILES[0], 'w') as f:
            f.write(json.dumps({'stage': 1, 'order': 't-o1', 'ts': now, 'kind': 'settled', 'basis': 'net', 'tx_hash': tx, 'amount': 9690}) + '\n')
            f.write(json.dumps({'stage': 2, 'order': 't-o1', 'ts': now, 'kind': 'settled', 'tx_hash': tx, 'to': payer,
                                'amount': 9690, 'value_credits': 9690}) + '\n')
        r = run({'order': 't-o1', '_rpc': {'eth_getTransactionByHash': paid}}, kit)
        assert r.get('would_send') == {'to': payer, 'amount': 9690}, r     # test mode never pays
        moved = dict(paid, **{'from': '0x' + '3' * 40})
        assert '다릅니다' in run({'order': 't-o1', '_rpc': {'eth_getTransactionByHash': moved}}, kit)['error']
        with open(FILES[0], 'a') as f:                                   # stage 2 naming more than stage 1's net amount
            f.write(json.dumps({'stage': 2, 'order': 't-o1', 'ts': now, 'kind': 'settled', 'tx_hash': tx, 'to': payer,
                                'amount': 10000, 'value_credits': 10000}) + '\n')
        assert '상한' in run({'order': 't-o1', '_rpc': {'eth_getTransactionByHash': paid}}, kit)['error']
        big = {'from': payer, 'to': shop, 'value': hex(250000 * 10 ** 15)}
        with open(FILES[0], 'a') as f:                                   # unsettled, more than one call may send
            f.write(json.dumps({'stage': 1, 'order': 't-o5', 'ts': now, 'kind': 'unsettled', 'tx_hash': tx, 'amount': None}) + '\n')
            f.write(json.dumps({'stage': 2, 'order': 't-o5', 'ts': now, 'kind': 'unsettled', 'tx_hash': tx, 'to': payer,
                                'amount': 250000, 'value_credits': 250000}) + '\n')
        assert '송금 한도' in run({'order': 't-o5', '_rpc': {'eth_getTransactionByHash': big}}, kit)['error']
        assert not os.path.exists(FILES[2])                              # refused before any intent line
        # owed (balance-diff-rca-1127, review r1 I2): stage 3 reads the ledger again before it pays
        otx = '0x' + 'c' * 64
        owed = {'kind': 'paid_unsettled', 'id': 'owed-' + otx, 'tx_hash': otx, 'amount': 9690.0, 'payer': payer,
                'checkout_order': 't-o6', 'order': 'o-m6', 'counterparty': '손님', 'ts': now - 100}
        with open(FILES[0], 'a') as f:
            f.write(json.dumps({'stage': 1, 'order': 't-o6', 'ts': now, 'kind': 'owed', 'tx_hash': otx, 'amount': 9690,
                                'payer': payer, 'checkout_order': 't-o6', 'market_order': 'o-m6'}) + '\n')
            f.write(json.dumps({'stage': 2, 'order': 't-o6', 'ts': now, 'kind': 'owed', 'tx_hash': otx, 'to': payer,
                                'amount': 9690, 'value_credits': 9690}) + '\n')
        a = {'order': 't-o6', '_rpc': {'eth_getTransactionByHash': paid}}
        r = run(dict(a, _payments=[owed]), kit)
        assert r.get('would_send') == {'to': payer, 'amount': 9690}, r
        assert '장부' in run(dict(a, _payments=[]), kit)['error']                                   # the owed line is gone
        sold = {'kind': 'sale', 'id': 'sale-o-m6', 'tx_hash': otx}
        assert '장부' in run(dict(a, _payments=[owed, sold]), kit)['error']                         # settled later
        for line in ({'kind': 'refund', 'order': 'o-m6', 'ts': now}, {'kind': 'refund', 'order': None, 'orders_of_payer': ['t-o6']},
                     {'kind': 'transfer_out', 'counterparty': '손님', 'ts': now}):
            assert '장부' in run(dict(a, _payments=[owed, line]), kit)['error'], line                # paid back by hand
        assert not os.path.exists(FILES[2])                              # refused before any intent line
        with open(FILES[2], 'w') as f:
            f.write(json.dumps({'purpose': 'refund', 'order': 't-o1', 'nonce': 3}) + '\n')
        assert '불확실' in run({'order': 't-o1', '_rpc': {'eth_getTransactionByHash': paid}}, kit)['error']
        with open(FILES[1], 'w') as f:
            f.write(json.dumps({'purpose': 'refund', 'order': 't-o1', 'tx_hash': '0x9'}) + '\n')
        assert '이미' in run({'order': 't-o1', '_rpc': {'eth_getTransactionByHash': paid}}, kit)['error']
        return 'ok'
    finally:
        for f in FILES:
            if os.path.exists(f):
                os.remove(f)
