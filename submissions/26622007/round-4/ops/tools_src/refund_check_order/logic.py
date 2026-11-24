"""refund_check_order - refund stage 1 of 3 (read only): is this order ours, broken in warranty, and not refunded yet?

args {order}. Reads the market's ledger (sealed entry for our sealed sales, the public rows otherwise) and our own
logs; writes one stage-1 line to logs/refund-plans.jsonl. Two cases:
  settled   our settle + a broken row for the same order, hours_after_sale <= warranty_hours
  unsettled the customer paid us on chain but /api/settle refused (our private_checkout logged it in logs/checkouts.jsonl)
The amount is the shop's net receipt (REFUND_BASIS): what reached our wallet, settle amount minus the shop's sale fee.
Stage 2 (refund_check_payer) reads the payer from the chain; stage 3 (refund_send) pays once.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = 'https://market.ourolabs.ai'
REFUND_BASIS = 'net'        # 'net' = settle amount - shop fee (13주차 리허설 선례), 'gross' = settle amount
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


def already(kit, order):
    for r in _lines(_p(kit, 'logs/refunds.jsonl')) + _lines(_p(kit, 'logs/sends.jsonl')):
        if r.get('purpose') == 'refund' and str(r.get('order')) == order:
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
            return {'ok': False, 'error': '이 주문의 정산 기록도, 결제 후 정산 실패 기록도 없습니다'}
        if not miss.get('tx_hash') or not miss.get('digest_hex'):
            return {'ok': False, 'error': '정산 실패 기록에 결제 tx나 결제서 해시가 없습니다'}
        sales = _lines('logs/sales.jsonl') if not kit.testing else args.get('_sales') or []
        if any(s.get('tx_hash') == miss['tx_hash'] or str(s.get('order')) == order for s in sales):
            return {'ok': False, 'error': '이 결제는 정산된 판매에 쓰였습니다. 정산 실패 환불 대상이 아닙니다'}
        plan = {'kind': 'unsettled', 'source': 'checkouts', 'tx_hash': miss['tx_hash'], 'digest_hex': miss['digest_hex'],
                'gross': _num(miss.get('price')), 'shop_fee': _num(miss.get('shop_fee')), 'amount': None}
    plan.update({'stage': 1, 'order': order, 'ts': int(time.time()), 'basis': REFUND_BASIS})
    with open(_p(kit, PLANS), 'a', encoding='utf-8') as f:
        f.write(json.dumps(plan, ensure_ascii=False, separators=(',', ':')) + '\n')
    return {'ok': True, 'plan': plan, 'next': 'refund_check_payer {order}'}
