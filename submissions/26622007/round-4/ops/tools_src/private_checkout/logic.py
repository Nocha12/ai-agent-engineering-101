"""private_checkout - a sealed (private) checkout for one unit we really hold, and the settle that follows it.

op create {unit, price, task, warranty_hours, claim, order?, endgame?}
  Before signing: the unit is in our inventory now (GET /api/inventory), not promised to another open checkout,
  its grade now equals the claimed grade and the grade on its certificate, the inventory's wallet and the market's
  registered key for our shop are both the shop wallet that signs (kit role 'shop'), and the price is at least the
  goal (3) floor. Logs the checkout (with sha256 of the JWT, the data a buyer's payment must carry) to
  logs/checkouts.jsonl.
op settle {order, checkout_jwt, checkout_mandate, payment_mandate}
  Only for a checkout we issued. Reads the unit again; forwards to /api/settle. On success appends the receipt to
  logs/sales.jsonl. When the buyer has paid us on chain but the market refuses the settle (the unit went, the order
  expired, ...), logs an 'unsettled' line so refund_check_order can refund that payment.
No wallet transfer here: the buyer pays, the kit only signs the checkout.
"""
import base64
import hashlib
import json
import math
import time
import urllib.error
import urllib.request

BASE = 'https://market.ourolabs.ai'
GAS, FEE = 205, 210          # goal (3): floor = (cost + 205/qty + 210) / 0.99; one unit is the conservative qty
HOLD = 900                   # an open checkout keeps its unit for 15 minutes
MAX_WARRANTY = 72
LOG = 'logs/checkouts.jsonl'


def _p(kit, path):
    """Test runs (forge) use logs/*.test.jsonl so a test never touches the real records."""
    return path.replace('.jsonl', '.test.jsonl') if kit.testing else path


def _http(kit, method, path, body=None, auth=True):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers=dict({'content-type': 'application/json'},
                                              **({'authorization': 'Bearer ' + kit.token('shop')} if auth else {})))
    try:
        with urllib.request.urlopen(req, timeout=40 if method == 'POST' else 8) as r:
            return r.status, json.loads(r.read().decode() or 'null')
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or 'null')
        except ValueError:
            return e.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None


def _lines(path):
    try:
        with open(path, encoding='utf-8') as f:
            return [x for x in (json.loads(l) for l in f if l.strip().startswith('{')) if isinstance(x, dict)]
    except (OSError, ValueError):
        return []


def _log(kit, line):
    with open(_p(kit, LOG), 'a', encoding='utf-8') as f:
        f.write(json.dumps(line, ensure_ascii=False, separators=(',', ':')) + '\n')


def inventory(kit, args):
    if kit.testing:
        return args.get('_inventory') or {}
    st, body = _http(kit, 'GET', '/api/inventory')
    if st != 200 or not isinstance(body, dict):
        raise RuntimeError(f'inventory unreadable ({st})')
    return body


def registered_key(kit, args):
    if kit.testing:
        return args.get('_registered')
    st, body = _http(kit, 'GET', '/api/shops', auth=False)
    shops = (body or {}).get('shops') if isinstance(body, dict) else None
    me = next((s for s in shops or [] if isinstance(s, dict) and s.get('name') == kit.name), None)
    return (me or {}).get('pubkey')


def sealed_settle(kit, args, order):
    """Our settle row for this order if the market already has it (a settle whose answer was lost)."""
    if kit.testing:
        return args.get('_sealed_settle')
    import urllib.parse
    st, rows = _http(kit, 'GET', '/api/ledger/sealed?order=' + urllib.parse.quote(order))
    if st == 200 and isinstance(rows, list):
        return next((dict(r.get('data') or {}, seq=r.get('seq')) for r in rows if r.get('kind') == 'settle'), None)
    if st in (403, 404):
        return None
    raise RuntimeError(f'sealed ledger unreadable ({st})')


def _record_sale(kit, rec, ledger, base):
    sale = {k: rec.get(k) for k in ('order', 'amount', 'shop_fee', 'tx_hash', 'warranty_hours')}
    sale.update(ts=int(time.time()), ledger=ledger)
    with open(_p(kit, 'logs/sales.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps(sale, separators=(',', ':')) + '\n')
    _log(kit, dict(base, event='settled', ledger=ledger))
    return sale


def settle_call(kit, args, body):
    if kit.testing:
        return args.get('_settle') or (0, None)
    return _http(kit, 'POST', '/api/settle', body)


def cert_grade(cert):
    if isinstance(cert, dict):
        for k in ('grade', 'claim'):
            if cert.get(k) in ('A', 'B', 'C'):
                return cert[k]
        inner = cert.get('cert') or cert.get('data')
        if isinstance(inner, dict):
            return cert_grade(inner)
    return None


def floor_price(cost, endgame):
    return math.ceil(((0 if endgame else cost) + GAS + FEE) / 0.99)


def digest(jwt):
    d = hashlib.sha256(jwt.encode()).digest()
    return d.hex(), base64.urlsafe_b64encode(d).decode().rstrip('=')


def open_hold(kit, unit, order, now):
    """Another checkout issued for this unit in the last HOLD seconds that has not settled or failed."""
    state = {}
    for r in _lines(_p(kit, LOG)):
        if r.get('unit') == unit and r.get('event') == 'issued':
            state[r.get('digest_hex')] = r
        elif r.get('event') in ('settled', 'refused', 'unsettled'):
            state.pop(r.get('digest_hex'), None)
    return next((r for r in state.values() if now - r.get('ts', 0) < HOLD and r.get('order') != order), None)


def unit_now(inv, unit_id):
    now = time.time()
    u = next((u for u in inv.get('units') or [] if isinstance(u, dict) and u.get('unit') == unit_id), None)
    if u and u.get('expires_at') and float(u['expires_at']) <= now:
        return None
    return u


def create(args, kit):
    unit_id, claim = str(args.get('unit') or ''), args.get('claim', args.get('grade'))   # v1 callers said grade
    price, warranty, task = args.get('price'), args.get('warranty_hours', 0), args.get('task')
    order = str(args.get('order') or '') or None
    if type(price) is not int or price <= 0:
        return {'ok': False, 'error': 'price must be a positive integer (credits)'}
    if type(warranty) is not int or not 0 <= warranty <= MAX_WARRANTY:
        return {'ok': False, 'error': f'warranty_hours must be an integer 0..{MAX_WARRANTY}'}
    if not isinstance(task, str) or not task:
        return {'ok': False, 'error': 'task (the order task text) is required'}
    inv = inventory(kit, args)
    shop = kit.address('shop').lower()
    if str(inv.get('wallet') or '').lower() != shop:
        return {'ok': False, 'error': '재고의 가게 지갑이 서명 지갑과 다릅니다. 결제서를 내지 않습니다'}
    key = registered_key(kit, args)
    if str(key or '').lower() != shop:
        return {'ok': False, 'error': '시장에 등록된 우리 가게 지갑과 서명 지갑이 다르거나 확인되지 않습니다'}
    u = unit_now(inv, unit_id)
    if not u:
        return {'ok': False, 'error': '이 단위는 지금 재고에 없습니다(이미 팔렸거나 유통기한이 지났습니다). 결제서를 내지 않습니다'}
    grade, cgrade = u.get('grade'), cert_grade(u.get('cert'))
    if claim != grade or (cgrade is not None and cgrade != grade):
        return {'ok': False, 'error': '안내할 등급이 지금 등급이나 인증서 등급과 다릅니다', 'grade_now': grade, 'cert_grade': cgrade}
    low = floor_price(float(u.get('cost') or 0), bool(args.get('endgame')))
    if price < low:
        return {'ok': False, 'error': '가격이 하한보다 낮습니다', 'floor': low}
    now = time.time()
    held = open_hold(kit, unit_id, order, now)
    if held:
        return {'ok': False, 'error': '이 단위는 다른 손님의 결제서가 아직 열려 있습니다(15분)', 'held_until': int(held['ts'] + HOLD)}
    payload = {'id': 'checkout', 'shop': kit.name, 'shop_url': kit.shop_url, 'item': u.get('sku'), 'price': price,
               'task': task, 'currency': 'credits', 'iat': int(now), 'unit': unit_id, 'claim': grade,
               'warranty_hours': warranty, 'private': True}
    if u.get('cert') is not None:
        payload['cert'] = u['cert']
    jwt = kit.sign_jwt(payload, 'shop')
    hx, b64 = digest(jwt)
    _log(kit, {'event': 'issued', 'ts': int(now), 'order': order, 'unit': unit_id, 'sku': u.get('sku'), 'price': price,
          'grade': grade, 'warranty_hours': warranty, 'digest_hex': hx})
    return {'ok': True, 'checkout_jwt': jwt, 'checkout_hash': b64, 'payload': payload}


def settle(args, kit):
    order, jwt = str(args.get('order') or ''), args.get('checkout_jwt')
    cm, pm = args.get('checkout_mandate'), args.get('payment_mandate')
    if not order or not isinstance(jwt, str) or not isinstance(cm, dict) or not isinstance(pm, dict):
        return {'ok': False, 'error': 'order, checkout_jwt and the buyer-signed checkout_mandate/payment_mandate are required'}
    hx, _ = digest(jwt)
    issued = next((r for r in reversed(_lines(_p(kit, LOG))) if r.get('event') == 'issued' and r.get('digest_hex') == hx), None)
    if not issued:
        return {'ok': False, 'error': '우리가 낸 결제서가 아닙니다'}
    pmm = pm.get('mandate') if isinstance(pm.get('mandate'), dict) else {}
    tx_hash = pmm.get('tx_hash')
    base = {'ts': int(time.time()), 'order': order, 'unit': issued.get('unit'), 'digest_hex': hx,
            'price': issued.get('price'), 'tx_hash': tx_hash}
    done = sealed_settle(kit, args, order)
    if done:                                        # already settled (an earlier answer was lost): record it, no refund
        if not any(str(s.get('order')) == order for s in _lines(_p(kit, 'logs/sales.jsonl'))):
            _record_sale(kit, done, done.get('seq'), base)
        return {'ok': True, 'settled': True, 'note': '이미 정산된 주문입니다', 'sale': {k: done.get(k) for k in ('order', 'amount', 'tx_hash')}}
    if not unit_now(inventory(kit, args), issued.get('unit')):
        if tx_hash:
            _log(kit, dict(base, event='unsettled', reason='unit_gone'))
            return {'ok': False, 'error': '결제는 왔지만 이 단위가 이미 재고에 없어 정산하지 않았습니다. 환불은 refund_check_order부터 합니다',
                    'refund': True}
        _log(kit, dict(base, event='refused', reason='unit_gone'))
        return {'ok': False, 'error': '이 단위는 이미 재고에 없습니다. 결제하지 마시라고 안내합니다'}
    code, body = settle_call(kit, args, {'order': order, 'checkout_jwt': jwt, 'checkout_mandate': cm, 'payment_mandate': pm})
    if code == 200 and isinstance(body, dict) and body.get('settled'):
        sale = _record_sale(kit, body.get('mandate_receipt') or {}, body.get('ledger'), base)
        return {'ok': True, 'settled': True, 'sale': sale}
    checks = (body or {}).get('checks') if isinstance(body, dict) else None
    if code == 400 and isinstance(checks, dict) and checks.get('paid_on_chain') is True \
            and checks.get('transaction_not_used_before') is not False and checks.get('not_settled_before') is not False:
        _log(kit, dict(base, event='unsettled', reason=[k for k, v in checks.items() if v is False][:8],
                  shop_fee=(body or {}).get('shop_fee')))
        return {'ok': False, 'error': '결제는 체인에 왔지만 시장이 정산을 거절했습니다. 환불은 refund_check_order부터 합니다',
                'checks': checks, 'refund': True}
    if code == 400:
        _log(kit, dict(base, event='refused'))
    return {'ok': False, 'status': code, 'response': body,
            'note': '503이나 응답 없음이면 같은 결제로 잠시 뒤 다시 settle합니다(새 결제를 요구하지 않습니다)'}


def run(args, kit):
    op = args.get('op')
    if op == 'create':
        return create(args, kit)
    if op == 'settle':
        return settle(args, kit)
    return {'ok': False, 'error': 'op must be create or settle'}
