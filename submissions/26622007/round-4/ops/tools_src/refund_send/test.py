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
        # review r5 I1: the lock never creates refunds.jsonl (settle locks the same file and cannot create it)
        r = run({'order': 't-o1', '_rpc': {'eth_getTransactionByHash': paid}}, kit)
        assert 'refunds.jsonl이 없습니다' in r.get('error', '') and not os.path.exists(FILES[1]), r
        open(FILES[1], 'w').close()
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
        # review r5 C1: a settle whose answer was lost: the sealed ledger under any order id of the plan is read again
        for k in ('t-o6', 'o-m6'):
            r = run(dict(a, _payments=[owed], _sealed_now={k: (200, [{'kind': 'settle', 'data': {}}])}), kit)
            assert '봉인 장부에 이 주문의 정산' in r.get('error', ''), (k, r)
        for bad in ((0, None), (500, None), (200, {'x': 1})):
            r = run(dict(a, _payments=[owed], _sealed_now={'o-m6': bad}), kit)
            assert '다시 읽지 못했습니다' in r.get('error', ''), (bad, r)
        r = run(dict(a, _payments=[owed], _sealed_now={'t-o6': (403, None), 'o-m6': (200, [{'kind': 'broken'}])}), kit)
        assert r.get('would_send') == {'to': payer, 'amount': 9690}, r
        assert not os.path.exists(FILES[2])
        r = run(dict(a, _payments=[owed], _sealed_now={k: (403, None) for k in ('t-o6', 'o-m6')}), kit)
        assert '다시 읽지 못했습니다' in r.get('error', ''), r                                # review r6 M2: 403 on every id
        # review r6 I1: a settle whose answer was lost (settle_sent, no outcome row) on an order id or the tx in other case
        for sent in ({'event': 'settle_sent', 'order': 't-o6', 'tx_hash': None},
                     {'event': 'settle_sent', 'order': 'x', 'tx_hash': otx.upper()}):
            r = run(dict(a, _payments=[owed], _checkouts=[sent]), kit)
            assert '확정되지 않았습니다' in r.get('error', ''), (sent, r)
            for out in ('unsettled', 'refused'):                                            # the outcome came later
                r = run(dict(a, _payments=[owed], _checkouts=[sent, {'event': out, 'order': sent['order'], 'tx_hash': sent['tx_hash']}]), kit)
                assert r.get('would_send') == {'to': payer, 'amount': 9690}, (out, r)
        later = [{'event': 'unsettled', 'order': 't-o6'}, {'event': 'settle_sent', 'order': 't-o6'}]  # a new try after it
        assert '확정되지 않았습니다' in run(dict(a, _payments=[owed], _checkouts=later), kit).get('error', '')
        assert '장부' in run(dict(a, _payments=[]), kit)['error']                                   # the owed line is gone
        sold = {'kind': 'sale', 'id': 'sale-o-m6', 'tx_hash': otx}
        assert '장부' in run(dict(a, _payments=[owed, sold]), kit)['error']                         # settled later
        for line in ({'kind': 'refund', 'order': 'o-m6', 'ts': now}, {'kind': 'refund', 'order': None, 'orders_of_payer': ['t-o6']},
                     {'kind': 'transfer_out', 'counterparty': '손님', 'ts': now}):
            assert '장부' in run(dict(a, _payments=[owed, line]), kit)['error'], line                # paid back by hand
        assert not os.path.exists(FILES[2])                              # refused before any intent line
        # review r3 (48b2d94a) C1: one refund per payment even for two calls at once. Another call holds the lock:
        import fcntl
        with open(FILES[1], 'a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            r = run(dict(a, _payments=[owed], _lock_wait=0.3), kit)
        assert '진행 중' in r.get('error', ''), r
        # another call wrote its intent while this one read the chain: the refund line is read again under the lock
        g, rpc0 = run.__globals__, run.__globals__['rpc']

        def racing(kit_, args_, method, params):
            if method == 'eth_getTransactionByHash':
                with open(FILES[2], 'a') as f:
                    f.write(json.dumps({'purpose': 'refund', 'order': 'o-m6', 'paid_tx': otx.upper(), 'nonce': 7}) + '\n')
            return rpc0(kit_, args_, method, params)
        g['rpc'] = racing
        try:
            r = run(dict(a, _payments=[owed]), kit)
        finally:
            g['rpc'] = rpc0
        assert '불확실' in r.get('error', ''), r
        os.remove(FILES[2])
        # the first call holds the lock while it pays; the second waits, gets the lock, reads that intent and refuses
        import threading
        got = threading.Event()

        def first():
            with open(FILES[1], 'a') as h:
                fcntl.flock(h, fcntl.LOCK_EX)
                got.set()
                time.sleep(0.4)
                with open(FILES[2], 'a') as f:
                    f.write(json.dumps({'purpose': 'refund', 'order': 't-o6', 'paid_tx': otx, 'nonce': 8}) + '\n')
        th = threading.Thread(target=first)
        th.start()
        got.wait(2)
        r = run(dict(a, _payments=[owed], _lock_wait=5), kit)
        th.join()
        assert '불확실' in r.get('error', ''), r
        os.remove(FILES[2])
        # asked by the market order id: a refund line under the checkout order is the same order
        with open(FILES[0], 'a') as f:
            f.write(json.dumps({'stage': 1, 'order': 'o-m8', 'ts': now, 'kind': 'owed', 'tx_hash': '0x' + 'e' * 64, 'amount': 9690,
                                'payer': payer, 'checkout_order': 't-o8', 'market_order': 'o-m8'}) + '\n')
            f.write(json.dumps({'stage': 2, 'order': 'o-m8', 'ts': now, 'kind': 'owed', 'tx_hash': '0x' + 'e' * 64, 'to': payer,
                                'amount': 9690, 'value_credits': 9690}) + '\n')
        with open(FILES[1], 'w') as f:
            f.write(json.dumps({'purpose': 'refund', 'order': 't-o8', 'tx_hash': '0x9'}) + '\n')
        assert '이미' in run({'order': 'o-m8', '_rpc': {'eth_getTransactionByHash': paid}}, kit).get('error', '')
        open(FILES[1], 'w').close()
        # review r3 I: a refund line under the other order id (market order) or the tx in other case is the same payment
        for line in ({'purpose': 'refund', 'order': 'o-m6', 'tx_hash': '0x9'},
                     {'purpose': 'refund', 'order': 'x', 'paid_tx': '0x' + 'C' * 64, 'tx_hash': '0x9'}):
            with open(FILES[1], 'w') as f:
                f.write(json.dumps(line) + '\n')
            assert '이미' in run(dict(a, _payments=[owed]), kit).get('error', ''), line
        open(FILES[1], 'w').close()
        # review r3 C2: settled after stage 1, under the checkout order, the market order or the tx in other case
        for kw in ({'_sales': [{'order': 'o-m6', 'tx_hash': '0x9'}]}, {'_sales': [{'order': 'x', 'tx_hash': '0x' + 'C' * 64}]},
                   {'_checkouts': [{'event': 'settled', 'order': 't-o6'}]}, {'_checkouts': [{'event': 'settled', 'order': 'o-m6'}]},
                   {'_checkouts': [{'event': 'settled', 'order': 'x', 'tx_hash': '0x' + 'C' * 64}]}):
            assert '정산' in run(dict(a, _payments=[owed], **kw), kit).get('error', ''), kw
        # ... and a sale written while this call read the chain (just before paying)
        late = dict(a, _payments=[owed], _sales=[])

        def selling(kit_, args_, method, params):
            if method == 'eth_getTransactionByHash':
                late['_sales'].append({'order': 'o-m6', 'tx_hash': otx})
            return rpc0(kit_, args_, method, params)
        g['rpc'] = selling
        try:
            r = run(late, kit)
        finally:
            g['rpc'] = rpc0
        assert '정산' in r.get('error', ''), r
        assert run(dict(a, _payments=[owed], _checkouts=[{'event': 'settled', 'order': 'other'}]), kit).get('would_send'), 'unrelated'
        # unsettled: a settled event on the same payment (other case) refuses; none sends
        with open(FILES[0], 'a') as f:
            f.write(json.dumps({'stage': 1, 'order': 't-o7', 'ts': now, 'kind': 'unsettled', 'tx_hash': tx, 'amount': None}) + '\n')
            f.write(json.dumps({'stage': 2, 'order': 't-o7', 'ts': now, 'kind': 'unsettled', 'tx_hash': tx, 'to': payer,
                                'amount': 9690, 'value_credits': 9690}) + '\n')
        u = {'order': 't-o7', '_rpc': {'eth_getTransactionByHash': paid}}
        assert run(u, kit).get('would_send') == {'to': payer, 'amount': 9690}
        assert '정산' in run(dict(u, _checkouts=[{'event': 'settled', 'order': 'y', 'tx_hash': tx.upper()}]), kit).get('error', '')
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
