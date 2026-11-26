"""check_item_inventory: what the shop really holds, short enough for the agent to read whole.

args {}            every SKU we hold: name, count, and per grade the count, the floor price and the hours left
args {sku}         the units of one item: a SKU id, or words of its name ("콩나물")
The kit shows a tool result up to 6,000 characters. shop_snapshot returns the raw /api/inventory list (about 500
characters a unit with its certificate), so past 10 units the rest was cut off and the agent answered "sold out" for
stock we held (11/25 06:39: bean sprouts bought at 06:24). This reads the same signed-in /api/inventory and answers
in under 5,000 characters. Units past their date are counted apart (expired), not as stock. A failed lookup is
ok:false, never zero stock.

Shown price (operator 11/26 12:40): each grade carries price, the one shown price for that SKU and grade kept in
logs/quotes.json by the shared quote block (also in private_checkout and restock_shoutout): never below the grade's
floor, never below a lower grade's. args {sku, quote: {grade: price}} sets the shown price for a grade that has none;
requote: true replaces it. Answers and checkouts quote that price, so one unit is not offered at two prices."""
import json
import math
import os
import re
import time

import httpx

GAS, FEE = 205, 210            # per-lot gas and per-sale fee: the floor write_shop_card and private_checkout use
LIMIT = 4800                   # characters of the answer
ITEMS = 20                     # units listed for one item


# --- shared quote block (same text in check_item_inventory, restock_shoutout, private_checkout; ops/test_quote_block.py)
QUOTES, QUOTE_TTL, QUOTE_RANK = 'logs/quotes.json', 24 * 3600, ('C', 'B', 'A')


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


def quote_prices(q, sku, floors, now):
    """{grade: shown price or None} for the grades in floors: the stored quote raised to that grade's floor, and a
    higher grade never below a lower one (C <= B <= A)."""
    out, low = {}, 0
    for g in sorted(floors, key=lambda g: QUOTE_RANK.index(g) if g in QUOTE_RANK else -1):
        p = quote_get(q, sku, g, now)
        out[g] = None if p is None else max(p, floors[g], low)
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


def _quotes_path(kit):
    return 'logs/quotes.test.json' if kit.testing else QUOTES


def inventory(kit, args):
    if kit.testing:
        inv = args.get('_inventory')
        if inv is None:
            return {'units': []}
        if not isinstance(inv, dict):
            raise RuntimeError('inventory is not an object')
        return inv
    auth = {'authorization': 'Bearer ' + kit.token('shop')}
    with httpx.Client(timeout=12, follow_redirects=True) as c:
        r = c.get(kit.market.rstrip('/') + '/api/inventory', headers=auth)
    if r.status_code != 200:
        raise RuntimeError('inventory HTTP %d' % r.status_code)
    inv = r.json()
    if not isinstance(inv, dict):
        raise RuntimeError('inventory is not an object')
    return inv


def num(v):
    """A market number, or 0 for anything else (a string, None, a bool): the answer must not stop on one odd unit."""
    try:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > 1e15:
            return 0
        return int(v) if float(v).is_integer() else float(v)
    except (OverflowError, ValueError):
        return 0


def floor(cost):
    return math.ceil((num(cost) + GAS + FEE) / 0.99)


def hours_left(u, now):
    e = u.get('expires_at')                             # 0 or missing: the unit has no date
    return round((e - now) / 3600, 1) if isinstance(e, (int, float)) and not isinstance(e, bool) and e > 0 else None


def window(inv):
    r = inv.get('restock') if isinstance(inv.get('restock'), dict) else {}
    return {k: r[k] for k in ('open', 'opens_at', 'closes_at')
            if isinstance(r.get(k), bool) or (isinstance(r.get(k), (int, float)) and num(r[k]) == r[k])}


def live(inv, now):
    """(units still good, how many are past their date)."""
    units = [u for u in inv.get('units') or [] if isinstance(u, dict) and u.get('sku')]
    good = [u for u in units if hours_left(u, now) is None or hours_left(u, now) > 0]
    return good, len(units) - len(good)


def short(name, n):
    return re.sub(r'\[[^\]]*\]', '', str(name or '')).strip()[:n]


def norm(text):
    """Lower case without spaces; 2L×6병, 2Lx6병, 2L*6병 and 2L 6병 read the same (q8 11/26: '몽베스트 무라벨 생수 2L×6병'
    found nothing while 13 packs of '…생수 2Lx6병(1팩)' were in stock)."""
    t = re.sub(r'[×✕✖╳⨯＊*ｘ]', 'x', str(text or '').lower())
    t = re.sub(r'\s', '', t)
    return re.sub(r'(\d(?:kg|ml|g|l|t)?|[가-힣])x(?=\d)', r'\1', t)     # 2Lx6, 400gx3, 라면x20; not MAX 2 (review I4)


SIZE_WORD = re.compile(r'^\d+(?:\.\d+)?(?:kg|g|l|ml|구|개입|입|팩|개|병|봉|매|t)?$', re.I)


def words(text):
    """Name words of two letters or more that are not a size or a count, brackets dropped: what 'similar' compares."""
    t = re.sub(r'[\[\]()]', ' ', str(text or ''))
    return {w for w in (norm(x) for x in re.split(r'[\s/·,]+', t)) if len(w) >= 2 and not SIZE_WORD.match(w)}


def similar(asked, units, n=5):
    """No item matched the name: the SKUs whose names share most of its words (at least half, and one), so the agent
    can look again by SKU instead of answering 'sold out' for stock we hold. Never counted as a match."""
    want = words(asked)
    if not want:
        return []
    need = max(1, (len(want) + 1) // 2)
    best = {}
    for u in units:
        have = norm(u.get('name'))
        hit = sum(1 for w in want if w in have)
        if hit >= need:
            b = best.setdefault(u['sku'], {'sku': str(u['sku'])[:20], 'name': short(u.get('name'), 30), 'count': 0, 'hit': hit})
            b['count'] += 1
    return [{k: v for k, v in b.items() if k != 'hit'} for b in sorted(best.values(), key=lambda b: (-b['hit'], -b['count'], b['sku']))[:n]]


def named(asked, name):
    """Words of an item name: two letters or more anywhere in the name; one Korean letter (쌀, 면, 김) at the end of a word
    of it (백진주쌀, 소면), never at its start (무 is not 무농약). restock_shoutout uses the same words rule but its own
    _norm, which (as of q8, 11/26) does not read 2L×6 as 2Lx6: a known gap there, not here."""
    q = norm(asked)
    if len(q) >= 2:
        return q in norm(name)
    return bool(re.fullmatch('[가-힣]', q)) and any(t.endswith(q) for t in re.findall(r'[가-힣]+', str(name or '')))


def summary(units, name_len):
    skus = {}
    for u in units:
        s = skus.setdefault(u['sku'], {'sku': u['sku'], 'name': short(u.get('name') or u['sku'], name_len), 'count': 0, 'grades': {}})
        s['count'] += 1
        g = s['grades'].setdefault(str(u.get('grade') or '?')[:8], {'n': 0, 'cost': 0, 'left': []})
        g['n'] += 1
        g['cost'] = max(g['cost'], num(u.get('cost')))
        g['left'].append(u)
    return list(skus.values())


def run(args, kit):
    try:
        return answer(args if isinstance(args, dict) else {}, kit)
    except Exception as e:                              # an answer the market never gave before: say so, never "0"
        return {'ok': False, 'error': '재고 조회 실패(재고 0이 아님, 잠시 뒤 다시 조회): ' + (type(e).__name__ + ' ' + str(e))[:80]}


def answer(args, kit):
    now = float(args.get('_now') or time.time()) if kit.testing else time.time()
    asked = str(args.get('sku') or args.get('name') or '').strip()[:60]
    inv = inventory(kit, args)
    units, expired = live(inv, now)
    if asked:
        mine = [u for u in units if u['sku'] == asked] or [u for u in units if named(asked, u.get('name'))]
        mine.sort(key=lambda u: (str(u.get('grade') or '?'), hours_left(u, now) if hours_left(u, now) is not None else 1e9))
        items = [{'unit': str(u.get('unit'))[:40], 'sku': str(u['sku'])[:20], 'name': short(u.get('name'), 24),
                  'grade': str(u.get('grade') or '?')[:8], 'cost': num(u.get('cost')), 'floor': floor(u.get('cost')),
                  'hours_left': hours_left(u, now), 'list_price': num(u.get('list_price')) or None} for u in mine[:ITEMS]]
        gf = {}
        for u in mine:
            k = (u['sku'], str(u.get('grade') or '?')[:8])
            gf[k] = max(gf.get(k, 0), floor(u.get('cost')))
        set_note, quoted = None, None
        if isinstance(args.get('quote'), dict) and mine:
            skus = {u['sku'] for u in mine}
            sku = mine[0]['sku']
            want = {g: p for g, p in args['quote'].items() if (sku, g) in gf and type(p) is int and p > 0}
            if len(skus) > 1:                           # a name that matched several items: say which SKU to use
                set_note = 'quote는 SKU ID 하나로 정합니다(이 이름에 맞는 품목이 여럿): ' + ', '.join(sorted(skus))[:200]
            elif not want:
                set_note = 'quote는 이 SKU에 있는 등급과 양의 정수 가격만 받습니다'
            else:
                try:
                    got = quote_set(_quotes_path(kit), sku, want, {g: f for (s, g), f in gf.items() if s == sku},
                                    'check_item_inventory', now, replace=bool(args.get('requote')))
                    quoted = sku
                    kept = {g: v for g, v in got.items() if v != want.get(g)}
                    if kept:                            # say what is shown instead (r2 M-5); requote: true changes it
                        set_note = None
                        quoted = {'sku': sku, 'kept': kept}
                except Exception as e:                  # the stock answer still goes out
                    set_note = '안내가 저장 실패: ' + type(e).__name__
        q = quotes_load(_quotes_path(kit))
        shown = {}
        for (s, g), f in gf.items():
            shown[(s, g)] = quote_prices(q, s, {g2: f2 for (s2, g2), f2 in gf.items() if s2 == s}, now).get(g)
        for it in items:
            it['price'] = shown.get((it['sku'], it['grade']))
        out = {'ok': True, 'sku': asked, 'count': len(mine), 'items': items, 'more': max(0, len(mine) - ITEMS),
               'note_price': 'price: 이 품목·등급 안내가(손님께 이 값으로 안내). null이면 아직 없음 → args {sku, quote:{등급:가격}}로 정함'}
        if set_note:
            out['quote_error'] = set_note
        if isinstance(quoted, dict):
            out['quoted_sku'], out['quote_kept'] = quoted['sku'], quoted['kept']
            out['quote_kept_note'] = '이미 정한 안내가(또는 하한·등급 순서로 올린 값)를 씁니다. 바꾸려면 requote: true'
        elif quoted:
            out['quoted_sku'] = quoted
        if not mine:
            near = similar(asked, units)
            if near:
                out['similar'] = near
                out['note'] = ('이 이름과 정확히 맞는 재고는 없지만 이름이 비슷한 품목이 있습니다(similar). 같은 상품·포장인지 '
                               'args {sku: similar의 SKU}로 다시 조회한 뒤 답하고, 확인 전에는 품절이라고 하지 마세요')
            else:
                out['note'] = '이 이름·SKU와 맞는 재고가 없습니다. 다른 이름으로 들여놨을 수 있으니 args {}로 전체 재고를 확인하세요'
        while len(json.dumps(out, ensure_ascii=False)) > LIMIT and out['items']:
            out['items'].pop()
            out['more'] = len(mine) - len(out['items'])
        return out
    out = {}
    for name_len in (22, 14, 8, 0):                     # shorter names until the whole shop fits
        skus = summary(units, name_len)
        q = quotes_load(_quotes_path(kit))
        for s in skus:
            shown = quote_prices(q, s['sku'], {g: floor(v['cost']) for g, v in s['grades'].items()}, now)
            for g, v in s['grades'].items():
                left = [h for h in (hours_left(u, now) for u in v['left']) if h is not None]
                s['grades'][g] = {'n': v['n'], 'floor': floor(v['cost']), 'hours_left': min(left) if left else None}
                if shown.get(g):                        # only when set: 60 nulls would push SKUs out of the answer
                    s['grades'][g]['price'] = shown[g]
            if not name_len:
                s.pop('name')
        out = {'ok': True, 'balance': num(inv.get('balance')), 'restock': window(inv), 'units': len(units),
               'expired': expired, 'skus': skus,
               'note': 'grades: 등급별 n(개수)·floor(하한가)·price(안내가, 없으면 아직 안 정함)·hours_left(가장 빨리 끝나는 소비기한까지 시간). unit ID는 args {sku}로 봅니다'}
        if len(json.dumps(out, ensure_ascii=False)) <= LIMIT:
            break
    while len(json.dumps(out, ensure_ascii=False)) > LIMIT and out['skus']:      # still too long: cut, and say so
        out['skus'] = out['skus'][:len(out['skus']) * 3 // 4]
        out['more_skus'] = len(skus) - len(out['skus'])
    return out
