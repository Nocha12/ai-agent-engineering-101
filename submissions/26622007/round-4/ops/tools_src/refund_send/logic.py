"""refund_send - refund stage 3 of 3 (pays): one transfer for one order, exactly the checked amount, then a record.

args {order}. Needs the order's stage-1 and stage-2 lines in logs/refund-plans.jsonl from the last 30 minutes, on the
same payment transaction. Refuses when any refund line (sent or only intended) already names the order: an intent
without a tx_hash means a send whose outcome is unknown, so a person checks the chain before anything else is sent.
Before paying it reads the payment transaction again and pays its sender only. The cap is the order's own amount:
the stage-2 amount, never more than what reached our wallet (permission.pay_limit is a fixed ceiling on top).
Writes an intent line (with the nonce) before kit.pay and the tx_hash line after, in logs/sends.jsonl and
logs/refunds.jsonl.
Review r3 (48b2d94a): the last checks, the intent line, the transfer and the tx line run under an exclusive lock on
logs/refunds.jsonl, so two calls at once cannot both pay one payment: the second reads the first one's intent and
refuses. private_checkout settle holds the same lock shared from its refund check to its record, so a refund never
passes its last check while a settle of that payment is at the market. Under that lock, just
before paying, it reads the refund lines and (for an unsettled or owed plan) sales.jsonl and checkouts.jsonl's
'settled' events again, matching every order id of the plan (the order asked, our checkout order, the market order)
and the payment tx without case.
Review r5 (W-20261124-2007): C1 a settle whose answer was lost leaves no local record, so for an unsettled or owed
plan it also reads the market's sealed ledger again under the lock for every order id of the plan and pays only
when each answers 404/403 or 200 without a settle row (anything else refuses, nothing sent); I1 the lock opens
logs/refunds.jsonl without creating it, so settle (which cannot create it) always locks the same file: no file =
refuse, a person creates the empty file once.
"""
import fcntl
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

MARKET = 'https://market.ourolabs.ai'
RPC = 'https://rpc-testnet.maroo.io'
PLANS = 'logs/refund-plans.jsonl'
FRESH = 1800
PAY_CAP = 200000            # = permission.pay_limit in ops/install_tools.py; above it the kit refuses the transfer
WEI = 10 ** 15
LOCK_WAIT = 60              # seconds to wait for another refund_send or a settle at the market (private_checkout holds
                            # the lock shared through its market calls, POST up to 40 s), then refuse (nothing sent)


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


def _write(kit, line):
    for path in ('logs/sends.jsonl', 'logs/refunds.jsonl'):
        with open(_p(kit, path), 'a', encoding='utf-8') as f:
            f.write(json.dumps(line, separators=(',', ':')) + '\n')


def _norm(v):
    return str(v or '').strip().lower()


def _keys(order, p1):
    return {str(k) for k in (order, p1.get('checkout_order'), p1.get('market_order')) if k not in (None, '')}


def refund_seen(kit, keys, tx):
    """A refund line (sent or only intended) naming any order id of the plan, or the same payment tx in any case."""
    for r in _lines(_p(kit, 'logs/refunds.jsonl')) + _lines(_p(kit, 'logs/sends.jsonl')):
        if r.get('purpose') == 'refund' and (str(r.get('order')) in keys or (tx and _norm(r.get('paid_tx')) == tx)):
            return r
    return None


def seen_error(r):
    if r.get('tx_hash'):
        return {'ok': False, 'error': '이 주문은 이미 환불했습니다(주문당 1회)', 'tx_hash': r['tx_hash']}
    return {'ok': False, 'error': '이 주문의 환불 송금 결과가 불확실합니다. 다시 보내지 말고 체인에서 nonce와 영수증을 먼저 확인해야 합니다',
            'intent': r}


def settled_since(kit, args, keys, tx):
    """A sale (sales.jsonl) or a 'settled' checkout event under any order id of the plan or on the payment tx."""
    sales = _strict(_p(kit, 'logs/sales.jsonl'))
    log = list(args.get('_checkouts') or []) if kit.testing else _strict('logs/checkouts.jsonl')
    if sales is None or log is None:                # review r7 M2: unreadable = refuse
        return True
    sales += list(args.get('_sales') or []) if kit.testing else []

    def hit(r):
        return str(r.get('order')) in keys or bool(tx and _norm(r.get('tx_hash')) == tx)
    return any(hit(s) for s in sales) or any(r.get('event') == 'settled' and hit(r) for r in log)


def sealed_settle_now(kit, args, keys):
    """review r5 C1: True when the market's sealed ledger has a settle row under any order id of the plan, None when
    any lookup is unreadable (refuse), False when none has one. 403 (not our sale) on every id = None (review r6 M2)."""
    known = False
    for k in sorted(keys):
        if kit.testing:
            st, rows = (args.get('_sealed_now') or {}).get(k, (404, None))
        else:
            req = urllib.request.Request(MARKET + '/api/ledger/sealed?order=' + urllib.parse.quote(k),
                                         headers={'authorization': 'Bearer ' + kit.token('shop')})
            try:
                with urllib.request.urlopen(req, timeout=8) as r:
                    st, rows = r.status, json.loads(r.read().decode() or 'null')
            except urllib.error.HTTPError as e:
                st, rows = e.code, None
            except (urllib.error.URLError, OSError, ValueError):
                st, rows = 0, None
        if st == 403:
            continue
        if st == 404:
            known = True
            continue
        if st != 200 or not isinstance(rows, (list, type(None))):
            return None
        if any(isinstance(r, dict) and r.get('kind') == 'settle' for r in rows or []):
            return True
        known = True
    return False if known else None


def _strict(path):
    """review r7 M2: [] when the file is absent, None when it exists but cannot be read (the caller refuses); a
    half-written or bad line is skipped alone, never the whole file."""
    try:
        with open(path, encoding='utf-8') as f:
            raw = f.readlines()
    except FileNotFoundError:
        return []
    except OSError:
        return None
    out = []
    for l in raw:
        try:
            x = json.loads(l)
        except ValueError:
            continue
        if isinstance(x, dict):
            out.append(x)
    return out


def settle_open(kit, args, keys, tx):
    """review r6/r7 I1: a private_checkout 'settle_sent' row on any order id of the plan or the payment tx with no later
    answer from the market for the same order AND the same tx (settled, or a POST 400 unsettled row with post=True);
    a unit_gone or other refused row never closes it. True also when checkouts.jsonl cannot be read."""
    log = list(args.get('_checkouts') or []) if kit.testing else _strict('logs/checkouts.jsonl')
    if log is None:
        return True
    pending = set()
    for r in log:
        o, t = str(r.get('order')), _norm(r.get('tx_hash'))
        if r.get('event') == 'settle_sent' and (o in keys or (tx and t == tx)):
            pending.add((o, t))
        elif r.get('event') == 'settled' or (r.get('event') == 'unsettled' and r.get('post') is True):
            pending.discard((o, t))
    return bool(pending)


def _lock(kit, args):
    """Exclusive flock on refunds.jsonl (opened for append, never created: settle locks the same file read-only and
    cannot create it, review r5 I1); None if another call holds it, 'missing' when the file does not exist."""
    wait = args.get('_lock_wait', LOCK_WAIT) if kit.testing else LOCK_WAIT
    try:
        f = os.fdopen(os.open(_p(kit, 'logs/refunds.jsonl'), os.O_WRONLY | os.O_APPEND), 'a', encoding='utf-8')
    except FileNotFoundError:
        return 'missing'
    except OSError:
        return None
    end = time.time() + wait
    while True:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except BlockingIOError:
            if time.time() >= end:
                f.close()
                return None
            time.sleep(0.1)
        except OSError:                         # no flock on this file system: never pay without the lock
            f.close()
            return None


def recheck(kit, args, order, p1):
    """The refusals that must hold at the moment of paying; None when the refund may go."""
    tx, keys = _norm(p1.get('tx_hash')), _keys(order, p1)
    seen = refund_seen(kit, keys, tx)
    if seen:
        return seen_error(seen)
    if p1.get('kind') != 'settled' and settled_since(kit, args, keys, tx):
        return {'ok': False, 'error': '1·2단계 뒤에 이 주문이 정산됐습니다(sales.jsonl·checkouts.jsonl). 미정산 환불은 하지 않습니다. 1단계부터 다시 합니다'}
    if p1.get('kind') != 'settled' and settle_open(kit, args, keys, tx):
        return {'ok': False, 'error': '이 주문의 정산 요청 결과가 아직 확정되지 않았습니다(settle 응답 없음). 환불하지 않습니다. 같은 인자로 settle을 다시 불러 결과를 확정한 뒤 1단계부터 다시 합니다'}
    if p1.get('kind') == 'owed' and owed_changed(kit, args, p1):
        return {'ok': False, 'error': '1·2단계 뒤에 장부가 바뀌었습니다(미정산 입금 줄이 없거나 판매·환불·송금 기록이 생김). 환불하지 않고 1단계부터 다시 합니다'}
    return None


def owed_changed(kit, args, p1):
    """balance-diff-rca-1127, review r1 I2: for an owed plan, read logs/payments.jsonl again just before paying. Refuse
    unless the owed-<tx> line is still the only line on that payment and no refund or transfer_out names its orders
    or went to the same payer after the payment (a hand refund, or a sale the ledger wrote since stage 1)."""
    pay = _lines('logs/payments.jsonl') if not kit.testing else args.get('_payments') or []
    tx = str(p1.get('tx_hash') or '').lower()
    on_tx = [e for e in pay if tx and tx in (str(e.get('tx_hash') or '').lower(), str(e.get('corrected_tx') or '').lower())]
    if len(on_tx) != 1 or on_tx[0].get('kind') != 'paid_unsettled' or on_tx[0].get('held_order'):
        return True
    e = on_tx[0]
    keys = {str(k) for k in (e.get('checkout_order'), e.get('order'), p1.get('checkout_order'), p1.get('market_order')) if k}
    who = {str(e.get(k) or '').lower() for k in ('payer', 'counterparty')} - {''}
    t0 = e.get('ts') if type(e.get('ts')) in (int, float) else 0
    for x in pay:
        if x.get('kind') not in ('refund', 'transfer_out'):
            continue
        named = x.get('orders_of_payer') if isinstance(x.get('orders_of_payer'), list) else []
        if str(x.get('order')) in keys or str(x.get('said_order')) in keys or keys & {str(o) for o in named}:
            return True
        ts = x.get('ts')
        if str(x.get('counterparty') or '').lower() in who and (type(ts) not in (int, float) or ts >= t0):
            return True
    return False


def run(args, kit):
    order = str(args.get('order') or '').strip()
    now = time.time()
    plans = [p for p in _lines(_p(kit, PLANS)) if p.get('order') == order and now - p.get('ts', 0) <= FRESH]
    p1 = next((p for p in reversed(plans) if p.get('stage') == 1), None)
    p2 = next((p for p in reversed(plans) if p.get('stage') == 2), None)
    if not p1 or not p2 or p2.get('ts', 0) < p1.get('ts', 0) or p1.get('tx_hash') != p2.get('tx_hash'):
        return {'ok': False, 'error': '30분 안의 1·2단계 확인이 없거나 서로 다릅니다. 1단계부터 다시 합니다'}
    early = recheck(kit, args, order, p1)
    if early:
        return early
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
    lock = _lock(kit, args)
    if lock == 'missing':
        return {'ok': False, 'error': 'logs/refunds.jsonl이 없습니다. 보내지 않았습니다. 사람이 빈 파일을 한 번 만든 뒤 다시 호출합니다'}
    if lock is None:
        return {'ok': False, 'error': '다른 환불 송금이 진행 중입니다. 보내지 않았습니다. 잠시 뒤 같은 주문으로 다시 호출합니다'}
    try:
        late = recheck(kit, args, order, p1)        # read again under the lock, just before paying
        if late:
            return late
        if p1.get('kind') != 'settled':
            sealed = sealed_settle_now(kit, args, _keys(order, p1))
            if sealed is None:
                return {'ok': False, 'error': '봉인 장부를 지금 다시 읽지 못했습니다. 보내지 않았습니다. 잠시 뒤 같은 주문으로 다시 호출합니다'}
            if sealed:
                return {'ok': False, 'error': '1·2단계 뒤에 시장 봉인 장부에 이 주문의 정산이 생겼습니다(응답을 잃은 settle). 미정산 환불은 하지 않습니다. settle을 다시 불러 판매를 기록합니다'}
        if kit.testing:
            return {'ok': False, 'error': 'testing mode: no refund sent', 'would_send': {'to': to, 'amount': amount}}
        nonce = int(rpc(kit, args, 'eth_getTransactionCount', [kit.address('shop'), 'pending']), 16)
        base = {'purpose': 'refund', 'order': order, 'paid_tx': p2['tx_hash'], 'to': to, 'amount': amount, 'kind': p1['kind'],
                'basis': p1.get('basis')}
        _write(kit, dict(base, ts=int(time.time()), nonce=nonce))
        sent = kit.pay(to, amount * WEI, '0x', role='shop')     # still under the lock: one refund transfer at a time
        txid = sent if isinstance(sent, str) else str(sent)
        _write(kit, dict(base, ts=int(time.time()), tx_hash=txid))
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
    return {'ok': True, 'order': order, 'to': to, 'amount': amount, 'tx_hash': txid}
