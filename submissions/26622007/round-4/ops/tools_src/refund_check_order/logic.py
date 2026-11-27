"""refund_check_order - refund stage 1 of 3 (read only): is this order ours, broken in warranty, and not refunded yet?

args {order}. Reads the market's ledger (sealed entry for our sealed sales, the public rows otherwise) and our own
logs; writes one stage-1 line to logs/refund-plans.jsonl. Two cases:
  settled   our settle + a broken row for the same order, hours_after_sale <= warranty_hours
  unsettled the customer paid us on chain but /api/settle refused (our private_checkout logged it in logs/checkouts.jsonl)
  owed      the customer paid our checkout on chain and never called /api/settle (no unsettled record): the ledger's
            inflow scan wrote it as paid_unsettled (owed-<tx>) in logs/payments.jsonl (balance-diff-rca-1127)
The amount is the shop's net receipt (REFUND_BASIS): what reached our wallet, settle amount minus the shop's sale fee.
Stage 2 (refund_check_payer) reads the payer from the chain; stage 3 (refund_send) pays once.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = 'https://market.ourolabs.ai'
REFUND_BASIS = 'net'        # 'net' = settle amount - shop fee (13주차 리허설 선례), 'gross' = settle amount
                            # (ax-price-ceiling: price_ceiling.py REFUND_BASIS models the same refund; change both)
PAGES = 6                   # public rows only: at most 6 pages of 500 after the settle row, 1.1 s apart
PLANS = 'logs/refund-plans.jsonl'


def _p(kit, path):
    """Test runs (forge) use logs/*.test.jsonl so a test never touches the real records."""
    return path.replace('.jsonl', '.test.jsonl') if kit.testing else path


def _get(kit, path, auth=False):
    req = urllib.request.Request(BASE + path, headers={'authorization': 'Bearer ' + kit.token('shop')} if auth else {})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read().decode() or 'null')
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None


def _lines(path):
    try:
        with open(path, encoding='utf-8') as f:
            return [x for x in (json.loads(l) for l in f if l.strip().startswith('{')) if isinstance(x, dict)]
    except (OSError, ValueError):
        return []


def _num(v):
    return v if type(v) in (int, float) else None


def refund_amount(amount, shop_fee):
    return amount - shop_fee if REFUND_BASIS == 'net' else amount


def already(kit, order, paid_tx=None):
    """A refund line (sent or only intended) for this order, or for the same payment under another order id."""
    for r in _lines(_p(kit, 'logs/refunds.jsonl')) + _lines(_p(kit, 'logs/sends.jsonl')):
        if r.get('purpose') == 'refund' and (str(r.get('order')) == order or (paid_tx and r.get('paid_tx') == paid_tx)):
            return r
    return None


def sealed_rows(kit, args, order):
    if kit.testing:
        return args.get('_sealed_status', 200 if args.get('_sealed') else 404), args.get('_sealed')
    return _get(kit, '/api/ledger/sealed?order=' + urllib.parse.quote(order), auth=True)


def public_rows(kit, args, after):
    if kit.testing:
        return [r for r in args.get('_ledger') or [] if r.get('seq', 0) > after][:500]
    st, rows = _get(kit, f'/api/ledger?after={int(after)}')
    if st != 200 or not isinstance(rows, list):
        raise RuntimeError(f'ledger unreadable ({st})')
    return rows


def from_public(kit, args, order):
    """Our sale record names the settle row (ledger seq); the broken row comes after it."""
    sale = next((s for s in reversed(_lines('logs/sales.jsonl') if not kit.testing else args.get('_sales') or [])
                 if str(s.get('order')) == order), None)
    if not sale or type(sale.get('ledger')) is not int:
        return None, None
    settle, broken, after = None, None, sale['ledger'] - 1
    for i in range(PAGES):
        rows = public_rows(kit, args, after)
        for r in rows:
            d = r.get('data') or {}
            # the row names the shop by its tunnel URL, which changes on every restart, so match on our own order id
            # (it came from our sales.jsonl above)
            if str(d.get('order')) != order:
                continue
            if r.get('kind') == 'settle' and settle is None:
                settle = dict(d, seq=r.get('seq'))
            elif r.get('kind') == 'broken':
                broken = dict(d, seq=r.get('seq'))
        if broken or len(rows) < 500:
            break
        after = rows[-1].get('seq', after + 500)
        if not kit.testing:
            time.sleep(1.1)
    return settle, broken


def owed_plan(kit, args, order, log):
    """balance-diff-rca-1127 (11/27 13,126): the buyer's kit paid the shop share, its market-fee transfer failed, and it
    never called /api/settle, so there is no settle and no unsettled record; only payment_ledger's owed-<tx> line.
    order is our checkout id or the market order id on that line. Refuses unless the line is open (no later sale or
    grant line for the payment, no refund paired with it, not held for a sealed sale) and it agrees with the checkout
    we issued (amount = price - shop fee, a payer address). Stage 2 reads the chain for the digest, sender and value."""
    pay = _lines('logs/payments.jsonl') if not kit.testing else args.get('_payments') or []
    owed = [e for e in pay if e.get('kind') == 'paid_unsettled' and str(e.get('id') or '').startswith('owed-')
            and order in (str(e.get('checkout_order')), str(e.get('order')))]
    if not owed:
        return None, '이 주문의 정산 기록도, 결제 후 정산 실패 기록도, 장부의 미정산 입금(owed) 줄도 없습니다'
    if len(owed) != 1:
        return None, '이 주문 번호에 장부 미정산 입금 줄이 여럿입니다. 사람이 확인해야 합니다'
    e = owed[0]
    tx = str(e.get('tx_hash') or '').lower()
    if not re.fullmatch(r'0x[0-9a-f]{64}', tx) or str(e.get('id')).lower() != 'owed-' + tx:
        return None, '장부 미정산 입금 줄의 결제 tx가 올바르지 않습니다'
    if e.get('held_order'):
        return None, ('같은 손님의 봉인 판매(' + str(e['held_order']) + ')가 아직 기록되지 않아 그 판매의 결제일 수 있습니다. '
                      '환불하지 않고 사람이 확인합니다')
    keys = {str(k) for k in (e.get('checkout_order'), e.get('order')) if k}
    for x in pay:
        if x is e:
            continue
        if str(x.get('tx_hash') or '').lower() == tx or str(x.get('corrected_tx') or '').lower() == tx:
            return None, '이 결제는 장부에서 판매·지급으로 바뀌었습니다. 환불 대상이 아닙니다'
        if x.get('kind') == 'refund' and (str(x.get('order')) in keys or str(x.get('said_order')) in keys):
            return None, '이 결제는 이미 장부에 환불 기록이 있습니다(결제당 1회)'
    sales = _lines('logs/sales.jsonl') if not kit.testing else args.get('_sales') or []
    if any(str(s.get('tx_hash') or '').lower() == tx or str(s.get('order')) in keys for s in sales) or \
            any(r.get('event') == 'settled' and str(r.get('order')) in keys for r in log):
        return None, '이 결제는 정산된 판매에 쓰였습니다. 미정산 입금 환불 대상이 아닙니다'
    for k in keys:
        if already(kit, k, tx):
            return None, '이 결제는 이미 환불 기록이 있습니다(결제당 1회)'
    ck = str(e.get('checkout_order') or '')
    issued = [r for r in log if r.get('event') == 'issued' and ck and str(r.get('order')) == ck]
    c = issued[-1] if issued else None
    if not c or not re.fullmatch(r'[0-9a-f]{64}', str(c.get('digest_hex') or '').lower()):
        return None, '장부 미정산 입금 줄의 결제서(우리가 낸 결제서 해시)를 찾지 못했습니다'
    digest = str(c['digest_hex']).lower()
    if any(r.get('event') == 'issued' and str(r.get('digest_hex') or '').lower() == digest and str(r.get('order')) != ck
           for r in log) or any(r.get('tx_hash') and str(r['tx_hash']).lower() == tx and str(r.get('order')) not in keys
                                for r in log if r.get('event') in ('unsettled', 'settled', 'issued')):
        return None, '이 결제서나 결제가 다른 주문 번호에도 기록되어 있습니다. 사람이 확인해야 합니다'
    price, fee, amount = _num(c.get('price')), _num(c.get('shop_fee')), _num(e.get('amount'))
    if price is not None and fee is None:
        fee = int(price) // 100 + 210              # the market's shop fee, as for a settle without shop_fee
    if price is None or amount is None or amount <= 0 or amount != int(amount) or int(amount) != price - fee:
        return None, '장부 미정산 입금 금액이 결제서 금액(가격 - 판매 수수료)과 다릅니다'
    payer = str(e.get('payer') or '').lower()
    if not re.fullmatch(r'0x[0-9a-f]{40}', payer):
        return None, '장부 미정산 입금 줄에 보낸 사람 주소가 없거나 올바르지 않습니다'
    return {'kind': 'owed', 'source': 'payments', 'tx_hash': tx, 'digest_hex': digest, 'gross': price, 'shop_fee': fee,
            'amount': int(amount), 'payer': payer, 'checkout_order': ck, 'market_order': e.get('order')}, None


def run(args, kit):
    order = str(args.get('order') or '').strip()
    if not order:
        return {'ok': False, 'error': 'order is required'}
    done = already(kit, order)
    if done:
        return {'ok': False, 'error': '이 주문은 이미 환불 기록이 있습니다(주문당 1회)', 'record': done}
    plan = None
    st, rows = sealed_rows(kit, args, order)
    if st == 200 and isinstance(rows, list):
        settle = next((r.get('data') for r in rows if r.get('kind') == 'settle' and isinstance(r.get('data'), dict)), None)
        broken = next((r.get('data') for r in rows if r.get('kind') == 'broken' and isinstance(r.get('data'), dict)), None)
        source = 'sealed'
    elif st in (404, 200):
        settle, broken = from_public(kit, args, order)
        source = 'public'
    elif st == 403:
        return {'ok': False, 'error': '이 주문은 우리 가게 판매가 아닙니다(봉인 장부 403)'}
    else:
        return {'ok': False, 'error': f'봉인 장부를 읽지 못했습니다({st}); 나중에 다시 확인합니다'}

    if settle:
        amount, fee, wh = _num(settle.get('amount')), _num(settle.get('shop_fee')), _num(settle.get('warranty_hours'))
        if fee is None and amount is not None:
            fee = int(amount) // 100 + 210          # the market's shop fee; stage 2 checks the net against the chain
        if amount is None or fee is None or not settle.get('tx_hash'):
            return {'ok': False, 'error': '판매 기록에 금액·수수료·결제 tx가 없습니다(온체인 결제가 아닌 판매는 이 도구로 환불하지 않습니다)'}
        if not broken:
            return {'ok': False, 'error': '장부에 이 주문의 고장 기록이 없습니다'}
        hours = _num(broken.get('hours_after_sale'))
        if not wh or hours is None or hours > wh:
            return {'ok': False, 'error': '보증 시간 밖의 고장이거나 보증이 없습니다', 'hours_after_sale': hours, 'warranty_hours': wh}
        plan = {'kind': 'settled', 'source': source, 'tx_hash': settle['tx_hash'], 'gross': amount, 'shop_fee': fee,
                'amount': refund_amount(amount, fee), 'warranty_hours': wh, 'hours_after_sale': hours}
    else:
        log = _lines('logs/checkouts.jsonl') if not kit.testing else args.get('_checkouts') or []
        miss = next((r for r in reversed(log) if r.get('event') == 'unsettled' and str(r.get('order')) == order), None)
        if not miss:
            plan, err = owed_plan(kit, args, order, log)
            if err:
                return {'ok': False, 'error': err}
        else:
            if not miss.get('tx_hash') or not miss.get('digest_hex'):
                return {'ok': False, 'error': '정산 실패 기록에 결제 tx나 결제서 해시가 없습니다'}
            sales = _lines('logs/sales.jsonl') if not kit.testing else args.get('_sales') or []
            if any(s.get('tx_hash') == miss['tx_hash'] or str(s.get('order')) == order for s in sales):
                return {'ok': False, 'error': '이 결제는 정산된 판매에 쓰였습니다. 정산 실패 환불 대상이 아닙니다'}
            if any(r.get('tx_hash') == miss['tx_hash'] and str(r.get('order')) != order for r in log
                   if r.get('event') in ('unsettled', 'settled', 'issued')):
                return {'ok': False, 'error': '이 결제는 다른 주문 번호에도 기록되어 있습니다. 사람이 확인해야 합니다'}
            plan = {'kind': 'unsettled', 'source': 'checkouts', 'tx_hash': miss['tx_hash'], 'digest_hex': miss['digest_hex'],
                    'gross': _num(miss.get('price')), 'shop_fee': _num(miss.get('shop_fee')), 'amount': None}
    done = already(kit, order, plan['tx_hash'])
    if done:
        return {'ok': False, 'error': '이 결제는 이미 환불 기록이 있습니다(결제당 1회)', 'record': done}
    plan.update({'stage': 1, 'order': order, 'ts': int(time.time()), 'basis': REFUND_BASIS})
    with open(_p(kit, PLANS), 'a', encoding='utf-8') as f:
        f.write(json.dumps(plan, ensure_ascii=False, separators=(',', ':')) + '\n')
    return {'ok': True, 'plan': plan, 'next': 'refund_check_payer {order}'}
