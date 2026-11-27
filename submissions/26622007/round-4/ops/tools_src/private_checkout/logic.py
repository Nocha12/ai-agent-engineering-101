"""private_checkout - a sealed (private) checkout for one unit we really hold, and the settle that follows it.

op create {unit, price?, task, warranty_hours, claim, order?, endgame?, inquiry_task?}
  Before signing: the unit is in our inventory now (GET /api/inventory), not promised to another inquiry's open checkout
  (an open checkout issued for the same inquiry - the same customer - does not block that customer's reissue; the
  inquiry is inquiry_task, set only by the A2A wrapper from the live message's task; without it nothing is spared),
  its grade now equals the claimed grade and the grade on its certificate, the inventory's wallet and the market's
  registered key for our shop are both the shop wallet that signs (kit role 'shop'), and the price is at least the
  goal (3) floor. Logs the checkout (with sha256 of the JWT, the data a buyer's payment must carry, and the holder) to
  logs/checkouts.jsonl. For a food unit whose market-signed certificate has grade null, the answer also carries
  grade_evidence (that certificate's unit, supplied_at, expires_at and our claim); the signed checkout is unchanged.
  When another inquiry holds the unit and no same-SKU/grade unit fits the price, answers
  code 'unit_held' with the same SKU's free units and their floors (cheapest first) so the offer can move to one.
op settle {order, checkout_jwt, checkout_mandate, payment_mandate}
  Only for a checkout we issued. Reads the unit again; forwards to /api/settle. On success appends the receipt to
  logs/sales.jsonl. When the buyer has paid us on chain but the market refuses the settle (the unit went, the order
  expired, ...), logs an 'unsettled' line so refund_check_order can refund that payment.
Clearance (떨이, 대표님 11/26 19:05): a food unit whose time left is half or less of min(its shelf life, 48 h) is on
clearance (shared clearance block): create without price issues its clearance price, and the floor for it drops with
the time left (below cost in the last quarter; never below 420, more than the sale fee). The answer carries clearance;
a clearance price never becomes the shown price of its SKU and grade. Water is never on clearance.
Cash-out (11/27): a cash-out quote (by 'cashout', see quote_floor) lowers the floor of the units of its SKU and grade
with CASH_HOURS or less left (unit_cash) to the market median it was set at, under cost (goal (2) gives way for these,
as for clearance). A longer-dated unit keeps its floor; asked under it, create names the short units (cashout_unit).
Shown price (operator 11/26 12:40): one price per SKU and grade lives in logs/quotes.json (the shared quote block,
also in check_item_inventory and restock_shoutout). create without price uses it; the first price a create uses
for an SKU and grade becomes it when there is none; a different agreed price is still issued (no refusal) and the
answer carries quote so the next offer uses the shown price.
No wallet transfer here: the buyer pays, the kit only signs the checkout.
"""
import base64
import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request

BASE = 'https://market.ourolabs.ai'
RPC = 'https://rpc-testnet.maroo.io'
GAS, FEE = 205, 210          # goal (3): floor = (cost + 205/qty + 210) / 0.99; one unit is the conservative qty
DUP_SPAN = 30                # s: a second unit of a SKU and grade for one inquiry in this span is flagged (대표님 11/27)
DEAD = ('settled', 'refused', 'unsettled', 'delivery_waived', 'delivery_overdue')
HOLD = 300                   # an open checkout keeps its unit for 5 minutes (대표님 06:2x: 15 min lost deals)
REISSUE_SPAN = 900           # one inquiry's back-to-back reissues of a unit stop sparing it its own hold after 15 min
MAX_WARRANTY = 72
LOG = 'logs/checkouts.jsonl'


# --- shared quote block (same text in check_item_inventory, restock_shoutout, private_checkout, write_shop_card,
#     refresh_full_card; ops/test_quote_block.py)
QUOTES, QUOTE_TTL, QUOTE_RANK = 'logs/quotes.json', 24 * 3600, ('C', 'B', 'A')
CASHOUT_BY, CASHOUT_MIN, CASH_HOURS = 'cashout', 420, 48


def quotes_load(path):
    """{'<sku>|<grade>': {'price': int, 'ts': float, 'by': str}}; {} when missing or unreadable."""
    try:
        with open(path, encoding='utf-8') as f:
            q = json.load(f)
        return q if isinstance(q, dict) else {}
    except (OSError, ValueError, RecursionError):         # a deeply nested file reads as no quotes (r19 M2)
        return {}


def quote_get(q, sku, grade, now):
    r = q.get(f'{sku}|{grade}')
    if isinstance(r, dict) and type(r.get('price')) is int and r['price'] > 0 \
            and isinstance(r.get('ts'), (int, float)) and 0 <= now - r['ts'] < QUOTE_TTL:
        return r['price']
    return None


def quote_floor(q, sku, grade, now):
    """Cash-out (현금화, 11/27): a fresh quote stored by 'cashout' (ops/forum_menu.py, a food grade with CASH_HOURS or less left
    whose market median is under the goal (3) floor) carries its own floor, the market median, never under CASHOUT_MIN
    (more than the sale fee: a unit that would be thrown away still brings money). None for any other quote."""
    r = q.get(f'{sku}|{grade}') if grade in QUOTE_RANK else None       # A, B or C only (review M2)
    if isinstance(r, dict) and r.get('by') == CASHOUT_BY and type(r.get('floor')) is int and r['floor'] >= CASHOUT_MIN \
            and quote_get(q, sku, grade, now) is not None:
        return r['floor']
    return None


def unit_cash(q, u, now):
    """The cash-out floor for one unit: its SKU and grade's quote_floor, only when that unit itself has CASH_HOURS or
    less left. A longer-dated unit of the same grade keeps the goal (3) floor (review I1). None otherwise."""
    e = u.get('expires_at') if isinstance(u, dict) else None
    if isinstance(e, bool) or not isinstance(e, (int, float)) or not 0 < e - now <= CASH_HOURS * 3600:
        return None
    return quote_floor(q, u.get('sku'), u.get('grade'), now)


def cash_clear(cl, shown, cash):
    """A unit both on clearance and on cash-out (cash, unit_cash) is never dearer than its cash-out shown price: the
    clearance curve assumes a base at the goal (3) floor or over it (review r2 I-N1). cl as given otherwise."""
    if not cl or cash is None or shown is None or cl['price'] <= shown:
        return cl
    return dict(cl, price=shown, floor=min(cl['floor'], cash), loss=True)


def quote_prices(q, sku, floors, now):
    """{grade: shown price or None} for the grades in floors: the stored quote raised to that grade's floor (a cash-out
    quote's own floor when lower), and a higher grade never below a lower one (C <= B <= A)."""
    out, low = {}, 0
    for g in sorted(floors, key=lambda g: QUOTE_RANK.index(g) if g in QUOTE_RANK else -1):
        p, f = quote_get(q, sku, g, now), quote_floor(q, sku, g, now)
        out[g] = None if p is None else max(p, floors[g] if f is None else min(floors[g], f), low)
        low = max(low, out[g] or 0)
    return out


def quote_fit(q, sku, grade, price, floor, now):
    """A new quote for one grade kept in order with the other grades' quotes: not below a lower grade's, not above
    a higher grade's unless its own floor (or a lower grade's quote) needs it."""
    r = QUOTE_RANK.index(grade) if grade in QUOTE_RANK else -1
    lower = [quote_get(q, sku, g, now) for g in QUOTE_RANK[:r]] if r > 0 else []
    higher = [quote_get(q, sku, g, now) for g in QUOTE_RANK[r + 1:]] if r >= 0 else []
    low = max([p for p in lower if p] + [floor])
    high = min([p for p in higher if p], default=None)
    price = max(price, low)
    if high is not None and price > high:
        price = max(high, low)
    return price


def quote_set(path, sku, prices, floors, by, now, replace=False):
    """Store {grade: price} (first one wins unless replace); returns the shown {grade: price} for those grades: what
    is stored, raised to today's floors and kept in grade order, the same number quote_prices gives (review r1 I5)."""
    q = quotes_load(path)
    q = {k: v for k, v in q.items() if isinstance(v, dict) and isinstance(v.get('ts'), (int, float))
         and 0 <= now - v['ts'] < QUOTE_TTL}
    out = {}
    for g in sorted(prices, key=lambda g: QUOTE_RANK.index(g) if g in QUOTE_RANK else -1):
        have = quote_get(q, sku, g, now)
        if have is not None and not replace:
            out[g] = have
            continue
        p = quote_fit(q, sku, g, int(prices[g]), int(floors.get(g, 0)), now)
        q[f'{sku}|{g}'] = {'price': p, 'ts': now, 'by': by}
        out[g] = p
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(q, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, path)
    shown = quote_prices(q, sku, dict({g: 0 for g in out}, **floors), now)
    return {g: shown.get(g) or out[g] for g in out}
# --- end shared quote block


# --- shared clearance block (떨이, 대표님 11/26 19:05; same text in private_checkout, check_item_inventory, write_shop_card, refresh_full_card, ops/forum_menu.py; ops/test_clearance_block.py)
# r = hours left / min(the item's whole shelf life, 48 h) (shelf life: catalog shelf_hours by category, the
# certificate's span when unknown; 48 h so a 30-day item with days left is not marked down).
# r > 0.5: not on clearance. 0.25 < r <= 0.5: price = base x (0.5 + r), never under cost + fees (no loss).
# r <= 0.25: the same price, and the floor drops with time: ceil((cost x r / 0.25 + 205 + 210) / 0.99), 420 at the end,
# so a unit that would be thrown away (0 credits) still sells for more than its fee. Water is never on clearance.
CLEAR_HALF, CLEAR_LOSS, CLEAR_SPAN = 0.5, 0.25, 48
CLEAR_GAS, CLEAR_FEE = 205, 210
CLEAR_WATER = ('생수', '샘물', '삼다수', '먹는물', '아이시스', '백산수', '에비앙', '워터', 'water', 'Water')
CLEAR_WATER_SKU = ('G00b8acf0',)
CLEAR_SHELF = {                     # catalog shelf_hours by food category (식품> dropped), /api/catalog 11/26 19:30
    '건강용품/다이어트/건강기능>다이어트식품/장건강': 720, '건강용품/다이어트/건강기능>비타민/미네랄/영양제': 720, '건강용품/다이어트/건강기능>전통건강식품': 720,
    '건강용품/다이어트/건강기능>헬스보조식품': 720, '계란/유제품>계란': 96, '계란/유제품>버터': 96, '계란/유제품>요거트': 96, '계란/유제품>우유/두유': 96,
    '계란/유제품>치즈': 96, '기타 가공식품>과자': 720, '기타 가공식품>국수/파스타/누들': 720, '기타 가공식품>기능성 가공식품': 720, '기타 가공식품>기타 가공식품': 720,
    '기타 가공식품>꿀/잼': 720, '기타 가공식품>디저트': 48, '기타 가공식품>샌드위치': 720, '기타 가공식품>생식/선식': 720, '기타 가공식품>스프': 720,
    '기타 가공식품>영유아 식음료': 720, '기타 가공식품>통조림캔': 720, '기타 가공식품>한과/떡': 48, '김치/반찬/즉석요리>김치': 168, '김치/반찬/즉석요리>반찬/젓갈': 168,
    '김치/반찬/즉석요리>즉석조리식품': 24, '김치/반찬/즉석요리>찌개/탕류/장류': 720, '빵/제과>베이킹 용품/재료': 720, '빵/제과>비스켓/쿠키': 720, '빵/제과>빵': 48,
    '빵/제과>케이크/파이': 48, '시리얼/곡물/콩류>가공 시리얼 제품': 720, '시리얼/곡물/콩류>곡물': 720, '시리얼/곡물/콩류>시리얼/곡물/콩류세트': 720,
    '시리얼/곡물/콩류>씨리얼/밀가루': 720, '식용류/지방유지류>오일': 720, '식품선물세트>식품선물세트': 720, '야채/과일>기타 채소/과일': 168,
    '야채/과일>신선과일(부패주의)': 48, '야채/과일>신선야채(부패주의)': 48, '양념/조미료/소스>소스/양념/조미료': 720, '양념/조미료/소스>식초/요리용 와인': 720,
    '양념/조미료/소스>허브/향신료': 720, '육류>가공/조리 육류': 720, '육류>기타 육류': 720, '육류>신선육류': 24, '음료>주스/코코아/생수': 720, '음료>커피/차': 720,
    '조리/가공 과일/야채/견과류>조리/가공 견과류': 720, '조리/가공 과일/야채/견과류>조리/가공 과일': 720, '조리/가공 과일/야채/견과류>조리/가공 야채': 168,
    '초콜릿/캔디/시럽>시럽': 720, '초콜릿/캔디/시럽>초콜릿/캔디/젤리': 720, '해산물>가공/조리 생선': 720, '해산물>가공/조리 오징어/문어류': 720,
    '해산물>가공/조리 해조류': 720, '해산물>기타 해산물': 720, '해산물>신선 오징어/문어류': 24, '해산물>신선생선': 24, '해산물>신선조개류/갑각류': 24,
    '해산물>신선해조류': 24,
}


def _clear_num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e15 else None


def clear_water(u):
    name = str(u.get('name') or '')
    return u.get('sku') in CLEAR_WATER_SKU or any(w in name for w in CLEAR_WATER)


def clear_ratio(u, now):
    """(hours left, r) for a food unit still good; None for goods, water, a unit without a date or past it."""
    if not isinstance(u, dict) or u.get('kind') not in (None, 'food') or clear_water(u):
        return None
    e = _clear_num(u.get('expires_at'))
    if e is None or e <= now:
        return None
    left = (e - now) / 3600
    cat = str(u.get('category') or '')
    shelf = CLEAR_SHELF.get(cat[3:] if cat.startswith('식품>') else cat)
    if not shelf:
        c = u.get('cert') if isinstance(u.get('cert'), dict) else {}
        c = c.get('cert') if isinstance(c.get('cert'), dict) else c
        s = _clear_num(c.get('supplied_at')) or _clear_num(u.get('supplied_at'))
        shelf = (e - s) / 3600 if s is not None and s < e else None
    if not shelf:
        return None
    return left, min(1.0, left / min(shelf, CLEAR_SPAN))


def clearance(u, base, now):
    """None, or {'price', 'floor', 'hours_left', 'r', 'loss'} for a unit on clearance. base: the unit's usual price
    (the shown price; 0.6 x list price when there is none)."""
    lr = clear_ratio(u, now)
    if lr is None or lr[1] > CLEAR_HALF:
        return None
    left, r = lr
    cost = _clear_num(u.get('cost'))
    if cost is None or cost < 0:                       # no number to price from (review M1): not on clearance
        return None
    share = min(1.0, r / CLEAR_LOSS)
    floor = math.ceil((cost * share + CLEAR_GAS + CLEAR_FEE) / 0.99)
    b = _clear_num(base) or round(0.6 * (_clear_num(u.get('list_price')) or 0))
    price = max(floor, min(int(b), int(round(b * (0.5 + r), -1))))   # never above the usual price (review M2)
    return {'price': price, 'floor': floor, 'hours_left': round(left, 1), 'r': round(r, 3),
            'loss': price < math.ceil((cost + CLEAR_GAS + CLEAR_FEE) / 0.99)}
# --- end shared clearance block


def _quotes_path(kit):
    return 'logs/quotes.test.json' if kit.testing else QUOTES


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


def paid_us(kit, args, tx_hash, hx):
    """The buyer's transfer really came to our shop wallet with this checkout's digest as its data (mined, ok).
    Without this a buyer could name someone else's payment and hold that refund for a person to check."""
    if kit.testing:
        return None if args.get('_tx_down') else not args.get('_tx_bad')
    if not isinstance(tx_hash, str) or len(tx_hash) != 66 or not tx_hash.startswith('0x'):
        return False
    try:
        def call(m, p):
            req = urllib.request.Request(RPC, data=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': m, 'params': p}).encode(),
                                         headers={'content-type': 'application/json', 'user-agent': 'ax-shop/1.0'})
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode()).get('result')
        tx, rc = call('eth_getTransactionByHash', [tx_hash]), call('eth_getTransactionReceipt', [tx_hash])
    except Exception:
        return None                                  # chain not reachable: not a no, ask again later
    return isinstance(tx, dict) and isinstance(rc, dict) and rc.get('status') == '0x1' \
        and str(tx.get('to') or '').lower() == kit.address('shop').lower() \
        and str(tx.get('input') or '').lower().removeprefix('0x') == hx.lower()


def _cost(v):
    """A unit's cost as a number, 0 for anything odd: another unit's bad cost never stops this checkout."""
    try:
        f = float(v) if not isinstance(v, bool) else 0.0
        return f if math.isfinite(f) and 0 <= f < 1e12 else 0.0
    except (TypeError, ValueError):
        return 0.0


def floor_price(cost, endgame):
    return math.ceil(((0 if endgame else cost) + GAS + FEE) / 0.99)


def unit_floor(x, endgame, now, base=None, cash=None):
    """The lowest price for this unit: the goal (3) floor (or the endgame floor), or its clearance floor or its cash-out
    floor (cash, unit_cash: only a unit with CASH_HOURS or less left) when lower."""
    low = floor_price(_cost(x.get('cost')), endgame)
    if cash is not None:
        low = min(low, cash)
    try:                                             # the clearance note is extra: a bad unit field never stops a checkout
        cl = clearance(x, base, now)
    except Exception:
        cl = None
    return min(low, cl['floor']) if cl else low


def digest(jwt):
    d = hashlib.sha256(jwt.encode()).digest()
    return d.hex(), base64.urlsafe_b64encode(d).decode().rstrip('=')


def _own_chain_start(rows, unit, holder):
    """Start of the holder's back-to-back issues of this unit (each within HOLD of the previous one)."""
    ts = sorted((r.get('ts', 0) for r in rows if r.get('event') == 'issued' and r.get('unit') == unit
                 and r.get('holder') == holder), reverse=True)
    start = ts[0] if ts else None
    for t in ts[1:]:
        if start - t >= HOLD:
            break
        start = t
    return start


def open_hold(kit, unit, order, now, holder=None, rows=None):
    """The latest checkout issued for this unit in the last HOLD seconds that has not settled or failed and holds it.
    A checkout for the same order does not hold. One issued for the same inquiry (holder: the same customer asking
    again) does not hold either, until that inquiry has reissued the unit back to back for REISSUE_SPAN; then its own
    hold runs out like anyone's. Rows without a holder (older rows, calls without the wrapper) always hold."""
    rows = _lines(_p(kit, LOG)) if rows is None else rows
    state = {}
    for r in rows:
        if r.get('unit') == unit and r.get('event') == 'issued':
            state[r.get('digest_hex')] = r
        elif r.get('event') in ('settled', 'refused', 'unsettled'):
            state.pop(r.get('digest_hex'), None)
    live = [r for r in state.values() if now - r.get('ts', 0) < HOLD and r.get('order') != order]
    spare = bool(holder) and any(r.get('holder') == holder for r in live) \
        and now - _own_chain_start(rows, unit, holder) < REISSUE_SPAN
    return max((r for r in live if not (spare and r.get('holder') == holder)), key=lambda r: r.get('ts', 0), default=None)


def _hours_left(u, now):
    try:
        return round((float(u['expires_at']) - now) / 3600, 1) if u.get('expires_at') else None
    except (TypeError, ValueError):
        return None


def free_alternatives(kit, inv, u, order, now, holder, endgame, rows):
    """Other units of the same SKU (any grade) in stock, not held by another inquiry, with their floors."""
    out, qs = [], quotes_load(_quotes_path(kit))
    for x in inv.get('units') or []:
        if not isinstance(x, dict) or x.get('unit') == u.get('unit') or x.get('sku') != u.get('sku'):
            continue
        g = x.get('grade')
        if g not in ('A', 'B', 'C') or cert_grade(x.get('cert')) not in (None, g) or not unit_now(inv, x.get('unit')):
            continue
        if open_hold(kit, x.get('unit'), order, now, holder, rows):
            continue
        alt = {'unit': x.get('unit'), 'grade': g, 'floor': unit_floor(x, endgame, now, cash=unit_cash(qs, x, now)),
               'hours_left': _hours_left(x, now)}
        try:
            cl = clearance(x, None, now)
        except Exception:
            cl = None
        if cl:                                       # offer it at its clearance price first (review M7)
            alt['clearance'] = {'price': cl['price'], 'floor': cl['floor']}
        out.append(alt)
    return sorted(out, key=lambda y: ((y.get('clearance') or {}).get('price', y['floor']), y['unit'] or ''))[:6]


def grade_evidence(u, unit_id, grade):
    """The market signs food certificates with grade null (Round 4; food grade is the shelf life left, rules "Food").
    The certificate cannot be changed by the shop, so return its own signed unit, supplied_at and expires_at for the
    reply to show beside the checkout. None when the certificate carries a grade or is not this unit's."""
    try:                                             # the note is extra: nothing here may stop the checkout
        c = u.get('cert')
        inner = c.get('cert') if isinstance(c, dict) else None
        if not isinstance(inner, dict) or inner.get('unit') != unit_id or inner.get('kind') != 'food' \
                or 'grade' not in inner or inner['grade'] is not None:
            return None
        sa, ea = inner.get('supplied_at'), inner.get('expires_at')
        if not all(type(x) in (int, float) and 1e9 < x < 1e11 for x in (sa, ea)) or sa >= ea:   # epoch seconds
            return None
        return {'unit': unit_id, 'claim': grade, 'supplied_at': sa, 'expires_at': ea}
    except Exception:
        return None


def unit_now(inv, unit_id):
    now = time.time()
    u = next((u for u in inv.get('units') or [] if isinstance(u, dict) and u.get('unit') == unit_id), None)
    if u and u.get('expires_at') and float(u['expires_at']) <= now:
        return None
    return u


def create(args, kit):
    unit_id, claim = str(args.get('unit') or ''), args.get('claim', args.get('grade'))   # v1 callers said grade
    price, warranty, task = args.get('price'), args.get('warranty_hours', 0), args.get('task')
    holder = args.get('inquiry_task')                # set only by the A2A wrapper (live task); see open_hold
    order = str(args.get('order') or '') or 'o-' + os.urandom(6).hex()
    if price is not None and (type(price) is not int or price <= 0):
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
    now = time.time()
    sku = u.get('sku')
    gfloors = {}                                     # every grade of this SKU, as check_item_inventory (review r1 I4)
    for x in inv.get('units') or []:
        if isinstance(x, dict) and x.get('sku') == sku and x.get('grade') and unit_now(inv, x.get('unit')):
            g = str(x.get('grade'))
            gfloors[g] = max(gfloors.get(g, 0), floor_price(_cost(x.get('cost')), False))
    gfloors.setdefault(grade, floor_price(_cost(u.get('cost')), False))
    qs = quotes_load(_quotes_path(kit))
    shown = quote_prices(qs, sku, gfloors, now).get(grade)
    cash = unit_cash(qs, u, now)                     # cash-out (11/27): this unit's floor under cost, 48 h or less left
    try:
        cl = cash_clear(clearance(u, shown, now), shown, cash)
    except Exception:
        cl = None
    cl0 = cl                                         # the unit asked for was on clearance: no quote from it (re-review I1)
    auto_clear = price is None and cl is not None      # the price is the clearance price, never a quote (review I1)
    if auto_clear:
        price = cl['price']
    if price is None:
        if shown is None:
            return {'ok': False, 'code': 'no_quote',
                    'error': '이 품목·등급 안내가가 아직 없습니다. 가격을 정해 price로 넘기거나 check_item_inventory {sku, quote}로 먼저 정합니다'}
        price = shown
    low = floor_price(float(u.get('cost') or 0), bool(args.get('endgame')))
    if cl:
        low = min(low, cl['floor'])
    if cash is not None:
        low = min(low, cash)
    if price < low:
        short = [{'unit': x.get('unit'), 'hours_left': _hours_left(x, now), 'floor': unit_cash(qs, x, now)}
                 for x in inv.get('units') or [] if isinstance(x, dict) and x.get('unit') != unit_id
                 and x.get('sku') == sku and x.get('grade') == grade and unit_now(inv, x.get('unit'))
                 and cert_grade(x.get('cert')) in (None, grade) and unit_cash(qs, x, now) is not None
                 and unit_cash(qs, x, now) <= price][:4]
        if short:                                    # the cash-out price is for the units about to expire (review I1)
            return {'ok': False, 'code': 'cashout_unit', 'floor': low, 'cashout_units': short,
                    'error': '이 가격은 기한이 48시간 이하인 단위에만 됩니다. cashout_units의 단위로 다시 내거나, 이 단위는 floor 이상으로 냅니다'}
        return {'ok': False, 'error': '가격이 하한보다 낮습니다', 'floor': low}
    holder, rows = (str(holder) if holder else None), _lines(_p(kit, LOG))
    dup = None
    if holder:                                       # the A2A task is what is known at issue (the payer comes at settle):
        # only a live checkout counts: not paid or failed, not dropped from its reply (delivery_waived), not given up
        # as undelivered (delivery_overdue, or bound when the inquiry could no longer take it) (review I3)
        closed = {r.get('digest_hex') for r in rows if r.get('event') in DEAD
                  or (r.get('event') == 'delivery_bound' and r.get('can_attach_at_issue_return') is False)}
        dup = next((r for r in reversed(rows) if r.get('event') == 'issued' and r.get('holder') == holder
                    and r.get('sku') == sku and r.get('grade') == grade and r.get('unit') != unit_id
                    and r.get('order') != order and r.get('digest_hex') not in closed
                    and 0 <= now - (r.get('ts') or 0) < DUP_SPAN), None)   # another grade or item: a change, not a double
        # 11/26 20:40 930158ae: a second unit of the same item 11 s later. Not refused (two 판 asked at once, a
        # checkout the customer never got, a unit switch all look the same here; review r1 I4/I5): issued, and the
        # answer says so, so the reply cancels one with checkout_cancel unless the customer asked for both
    held = open_hold(kit, unit_id, order, now, holder, rows)
    if held and holder and held.get('holder') == holder:        # this inquiry has kept the unit for REISSUE_SPAN already
        return {'ok': False, 'code': 'reissue_wait', 'held_until': int(held['ts'] + HOLD),
                'error': '이 문의에서 같은 단위를 15분 넘게 다시 발급해 잠시 멈췄습니다. 이미 보낸 결제서는 그대로 쓸 수 있습니다'}
    if held:                                         # another unit of the same SKU and grade, not held, at its own floor
        alt = next((x for x in inv.get('units') or [] if isinstance(x, dict) and x.get('unit') != unit_id
                    and unit_now(inv, x.get('unit')) and x.get('sku') == u.get('sku') and x.get('grade') == grade
                    and (cert_grade(x.get('cert')) in (None, grade))
                    and price >= unit_floor(x, bool(args.get('endgame')), now, shown, unit_cash(qs, x, now))
                    and not open_hold(kit, x.get('unit'), order, now, holder, rows)), None)
        if not alt:
            alts = free_alternatives(kit, inv, u, order, now, holder, bool(args.get('endgame')), rows)
            return {'ok': False, 'code': 'unit_held',
                    'error': '이 단위는 다른 문의에 낸 결제서로 잠시(최대 5분) 잡혀 있고, 같은 품목·등급의 다른 단위는 이 가격으로 낼 수 없습니다. '
                             + ('alternatives의 단위를 그 하한가 이상으로 다시 제안할 수 있습니다' if alts
                                else '같은 품목의 다른 재고는 없습니다. held_until 뒤 다시 확인합니다'),
                    'held_until': int(held['ts'] + HOLD), 'alternatives': alts, 'cheapest': alts[0] if alts else None}
        u, unit_id = alt, alt.get('unit')
        cash = unit_cash(qs, u, now)                 # and so does the cash-out note
        try:                                         # the note follows the unit the checkout is for
            cl = cash_clear(clearance(u, shown, now), shown, cash)
        except Exception:
            cl = None
        if auto_clear:                               # that unit's clearance price, or the shown price when it has none
            price = cl['price'] if cl else (shown if shown is not None else price)
    payload = {'id': 'checkout', 'shop': kit.name, 'shop_url': kit.shop_url, 'item': u.get('sku'), 'price': price,
               'task': task, 'currency': 'credits', 'iat': int(now), 'unit': unit_id, 'claim': grade,
               'warranty_hours': warranty, 'private': True}
    if u.get('cert') is not None:
        payload['cert'] = u['cert']
    jwt = kit.sign_jwt(payload, 'shop')
    hx, b64 = digest(jwt)
    # The signature is deterministic: the same terms in the same second give the same JWT. Hand back that checkout
    # under its first order instead of logging one JWT under two orders.
    same = next((r for r in rows if r.get('event') == 'issued' and r.get('digest_hex') == hx), None)
    if same and same.get('holder') != holder:
        return {'ok': False, 'error': '같은 결제서가 다른 문의에 나가 있습니다. 1초 뒤 다시 발급합니다'}
    ev = grade_evidence(u, unit_id, grade)
    evidence = {'grade_evidence': ev} if ev else {}
    if not same:                                     # logged first: a failed create never sets the shown price (r2 M-6)
        _log(kit, {'event': 'issued', 'ts': int(now), 'order': order, 'unit': unit_id, 'sku': u.get('sku'), 'price': price,
              'grade': grade, 'warranty_hours': warranty, 'digest_hex': hx, 'holder': holder})
    if cash is not None and not cl and price < floor_price(_cost(u.get('cost')), False):   # under cost only (M4)
        evidence['cashout'] = {'floor': cash, 'note': '기한 안에 팔리도록 시세에 맞춘 가격입니다(원가보다 낮을 수 있음)'}
    if cl:
        evidence['clearance'] = dict(cl, note=f"떨이·기한 임박({cl['hours_left']}시간 남음): {price:,}크레딧. 기한이 지나면 버리게 되어 값을 낮췄습니다")
    if shown is None and not args.get('endgame') and not cl and not cl0 and not auto_clear:
        try:                                         # the first price used becomes the shown price; never blocks
            shown = quote_set(_quotes_path(kit), sku, {grade: price}, gfloors, 'private_checkout', now).get(grade)
        except Exception:
            pass
    if shown is not None:
        evidence['quote'] = shown
        if shown != price and not cl and not cl0 and not auto_clear:
            evidence['quote_note'] = f'이 품목 {grade}등급 안내가는 {shown:,}입니다. 이번 결제서는 합의한 {price:,}로 냈고, 다음 안내는 {shown:,}로 합니다'
    if same:
        return dict({'ok': True, 'order': str(same.get('order')), 'checkout_jwt': jwt, 'checkout_hash': b64,
                     'payload': payload, 'reused': True}, **evidence)
    return dict({'ok': True, 'order': order, 'checkout_jwt': jwt, 'checkout_hash': b64, 'payload': payload}, **evidence,
                **({'duplicate_of': str(dup.get('order'))} if dup else {}))


def settle(args, kit):
    order, jwt = str(args.get('order') or ''), args.get('checkout_jwt')
    cm, pm = args.get('checkout_mandate'), args.get('payment_mandate')
    if not order or not isinstance(jwt, str) or not isinstance(cm, dict) or not isinstance(pm, dict):
        return {'ok': False, 'error': 'order, checkout_jwt and the buyer-signed checkout_mandate/payment_mandate are required'}
    hx, _ = digest(jwt)
    issued = next((r for r in reversed(_lines(_p(kit, LOG))) if r.get('event') == 'issued' and r.get('digest_hex') == hx), None)
    if not issued:
        return {'ok': False, 'error': '우리가 낸 결제서가 아닙니다'}
    if issued.get('order') and str(issued['order']) != order:
        return {'ok': False, 'error': '이 결제서는 다른 주문 번호로 발급했습니다', 'order': issued['order']}
    pmm = pm.get('mandate') if isinstance(pm.get('mandate'), dict) else {}
    tx_hash = pmm.get('tx_hash')
    for r in _lines(_p(kit, 'logs/refunds.jsonl')) + _lines(_p(kit, 'logs/sends.jsonl')):
        if r.get('purpose') == 'refund' and (str(r.get('order')) == order or (tx_hash and r.get('paid_tx') == tx_hash)):
            return {'ok': False, 'error': '이 주문(결제)은 이미 환불했거나 환불 중입니다. 정산하지 않습니다'}
    base = {'ts': int(time.time()), 'order': order, 'unit': issued.get('unit'), 'digest_hex': hx,
            'price': issued.get('price'), 'tx_hash': tx_hash}
    done = sealed_settle(kit, args, order)
    if done:                                        # already settled (an earlier answer was lost): record it, no refund
        if not any(str(s.get('order')) == order for s in _lines(_p(kit, 'logs/sales.jsonl'))):
            _record_sale(kit, done, done.get('seq'), base)
        return {'ok': True, 'settled': True, 'note': '이미 정산된 주문입니다', 'sale': {k: done.get(k) for k in ('order', 'amount', 'tx_hash')}}
    if not unit_now(inventory(kit, args), issued.get('unit')):
        paid = paid_us(kit, args, tx_hash, hx) if tx_hash else False
        if paid is None:
            return {'ok': False, 'error': '체인 조회가 지금 안 됩니다. 기록하지 않았습니다. 몇 분 뒤 같은 인자로 다시 호출합니다', 'retry': True}
        if paid:
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
