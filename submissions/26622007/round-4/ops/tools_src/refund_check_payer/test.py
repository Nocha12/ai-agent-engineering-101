import json
import os
import time

PLANS = 'logs/refund-plans.test.jsonl'


def test(run, kit):
    shop, payer, tx = kit.address('shop'), '0x' + '1' * 40, '0x' + 'a' * 64
    if os.path.exists(PLANS):
        os.remove(PLANS)
    os.makedirs('logs', exist_ok=True)
    try:
        assert '1단계' in run({'order': 't-o1'}, kit)['error']
        with open(PLANS, 'w') as f:
            f.write(json.dumps({'stage': 1, 'order': 't-o1', 'ts': int(time.time()), 'kind': 'settled', 'basis': 'net',
                                'tx_hash': tx, 'gross': 10000, 'shop_fee': 310, 'amount': 9690}) + '\n')
            f.write(json.dumps({'stage': 1, 'order': 't-o2', 'ts': int(time.time()), 'kind': 'unsettled', 'basis': 'net',
                                'tx_hash': tx, 'digest_hex': 'ab' * 32, 'amount': None}) + '\n')
        ok = {'status': '0x1'}
        paid = {'from': payer, 'to': shop, 'value': hex(9690 * 10 ** 15), 'input': '0x' + 'ab' * 32}
        r = run({'order': 't-o1', '_rpc': {'eth_getTransactionByHash': paid, 'eth_getTransactionReceipt': ok}}, kit)
        assert r['ok'] and r['plan']['to'] == payer and r['plan']['amount'] == 9690, r
        bad = dict(paid, value=hex(10000 * 10 ** 15))
        assert '실수령액' in run({'order': 't-o1', '_rpc': {'eth_getTransactionByHash': bad, 'eth_getTransactionReceipt': ok}}, kit)['error']
        other = dict(paid, to='0x' + '2' * 40)
        assert '가게 지갑' in run({'order': 't-o1', '_rpc': {'eth_getTransactionByHash': other, 'eth_getTransactionReceipt': ok}}, kit)['error']
        failed = {'status': '0x0'}
        assert '실패' in run({'order': 't-o1', '_rpc': {'eth_getTransactionByHash': paid, 'eth_getTransactionReceipt': failed}}, kit)['error']
        r = run({'order': 't-o2', '_rpc': {'eth_getTransactionByHash': paid, 'eth_getTransactionReceipt': ok}}, kit)
        assert r['ok'] and r['plan']['amount'] == 9690, r                  # unsettled: what reached us
        wrong = dict(paid, input='0x' + 'cd' * 32)
        assert '결제서' in run({'order': 't-o2', '_rpc': {'eth_getTransactionByHash': wrong, 'eth_getTransactionReceipt': ok}}, kit)['error']
        # owed (balance-diff-rca-1127): the chain sender and value must be the ledger's owed line
        with open(PLANS, 'a') as f:
            f.write(json.dumps({'stage': 1, 'order': 't-o3', 'ts': int(time.time()), 'kind': 'owed', 'basis': 'net',
                                'tx_hash': tx, 'digest_hex': 'ab' * 32, 'amount': 9690, 'payer': payer}) + '\n')
        r = run({'order': 't-o3', '_rpc': {'eth_getTransactionByHash': paid, 'eth_getTransactionReceipt': ok}}, kit)
        assert r['ok'] and r['plan']['amount'] == 9690 and r['plan']['to'] == payer and r['plan']['kind'] == 'owed', r
        other_payer = dict(paid, **{'from': '0x' + '3' * 40})
        assert '주소' in run({'order': 't-o3', '_rpc': {'eth_getTransactionByHash': other_payer, 'eth_getTransactionReceipt': ok}}, kit)['error']
        less = dict(paid, value=hex(9000 * 10 ** 15))
        assert '금액' in run({'order': 't-o3', '_rpc': {'eth_getTransactionByHash': less, 'eth_getTransactionReceipt': ok}}, kit)['error']
        assert '결제서' in run({'order': 't-o3', '_rpc': {'eth_getTransactionByHash': wrong, 'eth_getTransactionReceipt': ok}}, kit)['error']
        r = run({'order': 't-o2', '_rpc': {'eth_getTransactionByHash': other_payer, 'eth_getTransactionReceipt': ok}}, kit)
        assert r['ok'], r                                                  # unsettled: no ledger payer to compare
        return 'ok'
    finally:
        if os.path.exists(PLANS):
            os.remove(PLANS)
