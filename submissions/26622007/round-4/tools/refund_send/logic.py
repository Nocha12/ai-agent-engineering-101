"""refund_send - refund stage 3 of 3 (pays): one transfer for one order, exactly the checked amount, then a record.

args {order}. Needs the order's stage-1 and stage-2 lines in logs/refund-plans.jsonl from the last 30 minutes, on the
same payment transaction. Refuses when any refund line (sent or only intended) already names the order: an intent
without a tx_hash means a send whose outcome is unknown, so a person checks the chain before anything else is sent.
Before paying it reads the payment transaction again and pays its sender only. The cap is the order's own amount:
the stage-2 amount, never more than what reached our wallet (permission.pay_limit is a fixed ceiling on top).
Writes an intent line (with the nonce) before kit.pay and the tx_hash line after, in logs/sends.jsonl and
logs/refunds.jsonl.
"""
import json
import time
import urllib.request

RPC = 'https://rpc-testnet.maroo.io'
PLANS = 'logs/refund-plans.jsonl'
FRESH = 1800
PAY_CAP = 200000            # = permission.pay_limit in ops/install_tools.py; above it the kit refuses the transfer
WEI = 10 ** 15


def _p(kit, path):
    """Test runs (forge) use logs/*.test.jsonl so a test never touches the real records."""
    return path.replace('.jsonl', '.test.jsonl') if kit.testing else path


def _lines(path):
    try:
        with open(path, encoding='utf-8') as f:
            return [x for x in (json.loads(l) for l in f if l.strip().startswith('{')) if isinstance(x, dict)]
    except (OSError, ValueError):
        return []


def rpc(kit, args, method, params):
    if kit.testing:
        return (args.get('_rpc') or {}).get(method)
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
    req = urllib.request.Request(RPC, data=body, headers={'content-type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as r:
        out = json.loads(r.read().decode())
    if out.get('error'):
        raise RuntimeError(f"chain: {out['error']}")
    return out.get('result')


def _write(kit, line):
    for path in ('logs/sends.jsonl', 'logs/refunds.jsonl'):
        with open(_p(kit, path), 'a', encoding='utf-8') as f:
            f.write(json.dumps(line, separators=(',', ':')) + '\n')


def run(args, kit):
    order = str(args.get('order') or '').strip()
    now = time.time()
    plans = [p for p in _lines(_p(kit, PLANS)) if p.get('order') == order and now - p.get('ts', 0) <= FRESH]
    p1 = next((p for p in reversed(plans) if p.get('stage') == 1), None)
    p2 = next((p for p in reversed(plans) if p.get('stage') == 2), None)
    if not p1 or not p2 or p2.get('ts', 0) < p1.get('ts', 0) or p1.get('tx_hash') != p2.get('tx_hash'):
        return {'ok': False, 'error': '30분 안의 1·2단계 확인이 없거나 서로 다릅니다. 1단계부터 다시 합니다'}
    for r in _lines(_p(kit, 'logs/refunds.jsonl')) + _lines(_p(kit, 'logs/sends.jsonl')):
        if r.get('purpose') == 'refund' and (str(r.get('order')) == order or r.get('paid_tx') == p1.get('tx_hash')):
            if r.get('tx_hash'):
                return {'ok': False, 'error': '이 주문은 이미 환불했습니다(주문당 1회)', 'tx_hash': r['tx_hash']}
            return {'ok': False, 'error': '이 주문의 환불 송금 결과가 불확실합니다. 다시 보내지 말고 체인에서 nonce와 영수증을 먼저 확인해야 합니다',
                    'intent': r}
    amount, to = p2.get('amount'), str(p2.get('to') or '')
    if type(amount) is not int or amount <= 0:
        return {'ok': False, 'error': '환불 금액이 올바르지 않습니다'}
    # the cap is this order's own amount: stage 1's (REFUND_BASIS there) for a settled sale, what reached us otherwise
    cap = p1.get('amount') if p1['kind'] == 'settled' else p2.get('value_credits', 0)
    if amount != cap:
        return {'ok': False, 'error': '환불 금액이 이 주문의 상한(1단계 금액·받은 금액)과 다릅니다', 'cap': cap}
    if amount > PAY_CAP:
        return {'ok': False, 'error': f'환불 금액이 도구 한 번의 송금 한도({PAY_CAP})를 넘습니다. 사람이 처리합니다', 'amount': amount}
    tx = rpc(kit, args, 'eth_getTransactionByHash', [p2['tx_hash']])
    if not isinstance(tx, dict) or str(tx.get('from') or '').lower() != to.lower() \
            or str(tx.get('to') or '').lower() != kit.address('shop').lower():
        return {'ok': False, 'error': '결제 트랜잭션을 다시 읽어 보니 2단계 결과와 다릅니다'}
    if kit.testing:
        return {'ok': False, 'error': 'testing mode: no refund sent', 'would_send': {'to': to, 'amount': amount}}
    nonce = int(rpc(kit, args, 'eth_getTransactionCount', [kit.address('shop'), 'pending']), 16)
    base = {'purpose': 'refund', 'order': order, 'paid_tx': p2['tx_hash'], 'to': to, 'amount': amount, 'kind': p1['kind'],
            'basis': p1.get('basis')}
    _write(kit, dict(base, ts=int(time.time()), nonce=nonce))
    sent = kit.pay(to, amount * WEI, '0x', role='shop')
    txid = sent if isinstance(sent, str) else str(sent)
    _write(kit, dict(base, ts=int(time.time()), tx_hash=txid))
    return {'ok': True, 'order': order, 'to': to, 'amount': amount, 'tx_hash': txid}
