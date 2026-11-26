"""inventory_sales_check: what we hold per SKU and grade (signed-in /api/inventory) beside the public ledger's recent
median settle price for the same SKU and grade (sealed sales show no price and are skipped). args {}.
One row per SKU with its grades (count, floor price, median, how many settles), so the whole shop fits in the 6,000
characters the kit shows; units past their date are counted apart. Unit ids: check_item_inventory {sku}."""
import json
import math
import re
import time
from collections import defaultdict

import httpx

RECENT = 1500                  # ledger rows read, counted back from the newest
PAGE = 500                     # rows /api/ledger gives a call
GAS, FEE = 205, 210
LIMIT = 5200


def recent_ledger(get):
    """The newest RECENT rows. get(after) is one /api/ledger call: after=-N starts N rows before the end (review
    bc0f4234 I1: paging from after=0 read the oldest 6,000 rows, so a long round would have priced from stale sales)."""
    rows, after = [], -RECENT
    for _ in range(RECENT // PAGE + 1):
        page = get(after)
        if not isinstance(page, list) or not page:
            break
        rows += [r for r in page if isinstance(r, dict)]
        last = page[-1].get('seq') if isinstance(page[-1], dict) else None
        if len(page) < PAGE or not isinstance(last, int):
            break
        after = last
    return rows[-RECENT:]


def fetch(kit, args):
    if kit.testing:
        inv = args.get('_inventory')
        return (inv if isinstance(inv, dict) else {'units': []}), args.get('_ledger') or []
    base = kit.market.rstrip('/')
    auth = {'authorization': 'Bearer ' + kit.token('shop')}
    with httpx.Client(timeout=12, follow_redirects=True) as c:
        r = c.get(base + '/api/inventory', headers=auth)
        if r.status_code != 200:
            raise RuntimeError('inventory HTTP %d' % r.status_code)
        inv = r.json()
        if not isinstance(inv, dict):
            raise RuntimeError('inventory is not an object')
        rows = recent_ledger(lambda after: c.get(base + '/api/ledger', params={'after': after}).json())
    return inv, rows


def short(name, n):
    return re.sub(r'\[[^\]]*\]', '', str(name or '')).strip()[:n]


def num(v):
    try:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > 1e15:
            return 0
        return int(v) if float(v).is_integer() else float(v)
    except (OverflowError, ValueError):
        return 0


def run(args, kit):
    try:
        return answer(args if isinstance(args, dict) else {}, kit)
    except Exception as e:                              # an answer the market never gave before: say so, never "0"
        return {'ok': False, 'error': '조회 실패(재고 0이 아님, 잠시 뒤 다시 조회): ' + (type(e).__name__ + ' ' + str(e))[:80]}


def answer(args, kit):
    now = float(args.get('_now') or time.time()) if kit.testing else time.time()
    inv, rows = fetch(kit, args)
    rows = rows if isinstance(rows, list) else []
    prices = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict) or row.get('kind') != 'settle':
            continue
        d = row.get('data') if isinstance(row.get('data'), dict) else {}
        if isinstance(d.get('sku'), str) and isinstance(d.get('grade'), str) and isinstance(d.get('amount'), (int, float)) \
                and not isinstance(d.get('amount'), bool):
            prices[(d['sku'], d['grade'])].append(d['amount'])
    held, expired, total = {}, 0, 0
    for u in inv.get('units') or []:
        if not isinstance(u, dict) or not u.get('sku'):
            continue
        e = u.get('expires_at')
        if isinstance(e, (int, float)) and not isinstance(e, bool) and 0 < e <= now:      # 0 or missing: no date
            expired += 1
            continue
        total += 1
        h = held.setdefault((str(u['sku'])[:20], str(u.get('grade') or '?')[:8]), {'name': u.get('name') or u['sku'], 'n': 0, 'cost': 0})
        h['n'] += 1
        h['cost'] = max(h['cost'], num(u.get('cost')))
    out = {}
    for name_len in (18, 10, 0):
        by_sku = {}
        for (sku, grade), h in held.items():
            vals = sorted(prices.get((sku, grade), []))
            row = by_sku.setdefault(sku, {'sku': sku, 'name': short(h['name'], name_len), 'grades': {}})
            row['grades'][str(grade or '?')] = {'n': h['n'], 'floor': math.ceil((h['cost'] + GAS + FEE) / 0.99),
                                                'median_price': vals[len(vals) // 2] if vals else None, 'settles': len(vals)}
            if not name_len:
                row.pop('name', None)
        items = list(by_sku.values())
        r = inv.get('restock') if isinstance(inv.get('restock'), dict) else {}
        out = {'ok': True, 'balance': num(inv.get('balance')), 'units': total, 'expired': expired,
               'restock': {k: r[k] for k in ('open', 'opens_at', 'closes_at')
                           if isinstance(r.get(k), bool) or (isinstance(r.get(k), (int, float)) and num(r[k]) == r[k])},
               'ledger_rows': len(rows), 'count': len(items), 'items': items}
        if not rows:
            out['note'] = '공개 장부를 읽지 못해 정산가 중앙값이 없습니다(재고 수는 맞습니다)'
        if len(json.dumps(out, ensure_ascii=False)) <= LIMIT:
            break
    while len(json.dumps(out, ensure_ascii=False)) > LIMIT and out['items']:     # still too long: cut, and say so
        out['items'] = out['items'][:len(out['items']) * 3 // 4]
        out['more_skus'] = len(items) - len(out['items'])
    return out
