"""refund_check_payer - refund stage 2 of 3 (read only): who paid this order, and how much reached our wallet?

args {order}. Takes the order's stage-1 line from logs/refund-plans.jsonl (30 minutes at most), reads the payment
transaction on the Maroo chain and writes one stage-2 line: the refund goes to the transaction's sender, never to an
address someone names. Checks: the transaction succeeded, it paid our shop wallet, and
  settled   the value is exactly the stage-1 net amount (the buyer pays the shop price - shop fee on chain)
  unsettled its data is the checkout hash we issued for this order; the refund is the value that reached us
"""
import json
import time
import urllib.request

RPC = 'https://rpc-testnet.maroo.io'
PLANS = 'logs/refund-plans.jsonl'
FRESH = 1800
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
    # rpc-testnet.maroo.io answers urllib's default user-agent with 403 (11/27); the same value as private_checkout
    req = urllib.request.Request(RPC, data=body, headers={'content-type': 'application/json', 'user-agent': 'ax-shop/1.0'})
    with urllib.request.urlopen(req, timeout=10) as r:
        out = json.loads(r.read().decode())
    if out.get('error'):
        raise RuntimeError(f"chain: {out['error']}")
    return out.get('result')


def run(args, kit):
    order = str(args.get('order') or '').strip()
    now = time.time()
    p1 = next((p for p in reversed(_lines(_p(kit, PLANS))) if p.get('stage') == 1 and p.get('order') == order), None)
    if not p1 or now - p1.get('ts', 0) > FRESH:
        return {'ok': False, 'error': '30분 안의 1단계(refund_check_order) 결과가 없습니다. 1단계부터 다시 합니다'}
    tx_hash = p1['tx_hash']
    tx = rpc(kit, args, 'eth_getTransactionByHash', [tx_hash])
    receipt = rpc(kit, args, 'eth_getTransactionReceipt', [tx_hash])
    if not isinstance(tx, dict) or not isinstance(receipt, dict):
        return {'ok': False, 'error': '결제 트랜잭션을 체인에서 찾지 못했습니다'}
    if int(str(receipt.get('status', '0x0')), 16) != 1:
        return {'ok': False, 'error': '결제 트랜잭션이 실패한 거래입니다'}
    shop = kit.address('shop').lower()
    if str(tx.get('to') or '').lower() != shop:
        return {'ok': False, 'error': '이 결제는 우리 가게 지갑으로 온 돈이 아닙니다'}
    value = int(str(tx.get('value', '0x0')), 16)
    if value % WEI:
        return {'ok': False, 'error': '결제 금액이 크레딧 단위가 아닙니다'}
    payer = str(tx.get('from') or '')
    if len(payer) != 42 or not payer.startswith('0x') or payer.lower() == shop:
        return {'ok': False, 'error': '결제 보낸 주소를 확인할 수 없습니다'}
    if p1['kind'] == 'settled':
        net = p1['gross'] - p1['shop_fee']          # the buyer pays the shop price - shop fee on chain, whatever the basis
        if value != net * WEI:
            return {'ok': False, 'error': '체인 결제액이 판매 기록의 실수령액과 다릅니다', 'chain': value // WEI, 'ledger_net': net}
        amount = p1['amount']
    else:
        data = str(tx.get('input') or tx.get('data') or '').lower()
        if data != '0x' + str(p1['digest_hex']).lower():
            return {'ok': False, 'error': '이 결제는 우리가 이 주문에 낸 결제서를 가리키지 않습니다'}
        amount = value // WEI
    if amount <= 0:
        return {'ok': False, 'error': '환불할 금액이 없습니다'}
    plan = {'stage': 2, 'order': order, 'ts': int(now), 'tx_hash': tx_hash, 'to': payer, 'amount': amount,
            'kind': p1['kind'], 'value_credits': value // WEI}
    with open(_p(kit, PLANS), 'a', encoding='utf-8') as f:
        f.write(json.dumps(plan, separators=(',', ':')) + '\n')
    return {'ok': True, 'plan': plan, 'next': 'refund_send {order}'}
