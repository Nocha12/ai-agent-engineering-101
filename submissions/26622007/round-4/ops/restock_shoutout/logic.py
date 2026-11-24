"""restock_shoutout: thank the customers who waited for a SKU, once it is really back in stock.

ops (args.op):
  want      {customer, sku, name?, qty?, price?}  a customer asked for a SKU we do not hold (checked on /api/inventory)
  bought    {customer, sku, name?, order}          a sale we settled (the order must be in logs/sales.jsonl with tx_hash)
  opt_out   {customer}                              the customer said stop: no mention, no notice from now on
  restocked {sku, price}                            after a lot is ours: pick who waited (wants) and regulars (2+ buys)
  announce  {dry_run?}                              one forum post for this supply window, every restocked SKU in it
  notice    {customer}                              at the start of that customer's next conversation: one short line
Customers have no A2A card, so the personal notice waits for the customer's next message instead of being pushed.
Stock, grade and price are read again from /api/inventory before anything is said. No wallet, no payment.
"""
import json
import math
import re
import time
import urllib.error
import urllib.request

BASE = 'https://market.ourolabs.ai'
REGULAR_MIN = 2              # buys of the same SKU that make a regular
DAY_CAP = 2                  # restock events per customer in 24 h (a mention and its notice are one event)
WANT_TTL = 7 * 86400         # a sold-out request older than this is dropped
NOTICE_TTL = 3 * 86400       # an undelivered notice older than this is dropped
NAMES_PER_SKU = 5            # names in one forum line
GAS, FEE = 205, 210          # per-lot gas and per-sale fee, credits (agent.json (3))
POST_GAP_DISLIKED = 6 * 3600 # after a dislike on one of our posts, at most one post per 6 h
DISLIKE_PAUSE = 3            # this many dislikes on our posts in the last hour: no post for 24 h
ANON = ('did not say who it is', 'a person in your app')


def _file(kit):
    return 'shoutout.test.json' if kit.testing else 'shoutout.json'


def _load(kit):
    try:
        with open(_file(kit), encoding='utf-8') as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    s.setdefault('customers', {})
    s.setdefault('windows', {})
    s.setdefault('posts', [])
    return s


def _save(kit, s):
    with open(_file(kit), 'w', encoding='utf-8') as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


def customer_key(frm):
    """The same key customer_memory uses: the quoted name if the from line says it, None for anonymous customers."""
    t = str(frm or '')
    if any(a in t for a in ANON):
        return None
    m = re.search(r"says it is '([^']+)'", t)
    if m:
        t = m.group(1)
    t = t.strip().strip('"\'').strip('[]').strip()
    return t[:40] or None


def _display(key):
    return re.sub(r'[@\s]+', ' ', key).strip()[:20]


def _http(kit, method, path, body=None, query=''):
    req = urllib.request.Request(BASE + path + query, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'authorization': 'Bearer ' + kit.token('shop'),
                                          'content-type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read().decode() or 'null')
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or 'null')
        except ValueError:
            return e.code, None


def inventory(kit, args):
    if kit.testing:
        return args.get('_inventory') or {'units': []}
    st, body = _http(kit, 'GET', '/api/inventory')
    if st != 200 or not isinstance(body, dict):
        raise RuntimeError(f'inventory unreadable ({st})')
    return body


def forum(kit, args):
    if kit.testing:
        return args.get('_forum') or []
    st, body = _http(kit, 'GET', '/api/forum', query='?limit=100')
    if st != 200:
        raise RuntimeError(f'forum unreadable ({st})')
    return body if isinstance(body, list) else (body or {}).get('posts') or []


def post(kit, text):
    st, body = _http(kit, 'POST', '/api/forum', {'text': text})
    return st, body


def stock(inv, sku):
    units = [u for u in inv.get('units') or [] if u.get('sku') == sku]
    if not units:
        return None
    grades = sorted({str(u.get('grade') or '?') for u in units})
    costs = [float(u.get('cost') or 0) for u in units]
    return {'count': len(units), 'name': units[0].get('name') or sku, 'grades': grades,
            'floor': math.ceil((max(costs) + GAS + FEE) / 0.99)}


def _short(name):
    n = re.sub(r'\[[^\]]*\]', '', str(name)).strip()
    return n[:24] if len(n) > 24 else n


def _window(inv, s, now):
    r = inv.get('restock') or {}
    if r.get('open') and r.get('opens_at'):
        w = str(int(r['opens_at']))
    elif s.get('last_window') and now - s.get('last_window_ts', 0) < 5400:
        w = s['last_window']
    else:
        w = 'h' + str(int(now // 3600))
    s['last_window'], s['last_window_ts'] = w, now
    return w


def _prune(s, now):
    for c in s['customers'].values():
        c['wants'] = {k: v for k, v in (c.get('wants') or {}).items() if now - v.get('ts', 0) < WANT_TTL}
        c['notices'] = [n for n in c.get('notices') or [] if now - n.get('ts', 0) < NOTICE_TTL]
        c['events'] = [e for e in c.get('events') or [] if now - e.get('ts', 0) < 86400]
    s['posts'] = [p for p in s['posts'] if now - p.get('ts', 0) < 7 * 86400]
    old = sorted(s['windows'], key=lambda w: s['windows'][w].get('ts', 0))[:-20]
    for w in old:
        s['windows'].pop(w, None)


def _cust(s, key):
    return s['customers'].setdefault(key, {'wants': {}, 'buys': {}, 'events': [], 'notices': [], 'opt_out': None})


def _sales_orders():
    out = {}
    try:
        with open('logs/sales.jsonl', encoding='utf-8') as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get('order') and r.get('tx_hash'):
                    out[str(r['order'])] = r
    except OSError:
        pass
    return out


def notice_text(key, sku_name, st, price, why='waited'):
    g = '·'.join(st['grades'])
    lead = '기다리시던' if why == 'waited' else '자주 찾아 주시던'
    return (f"{_display(key)}님, {lead} {_short(sku_name)} 다시 들어왔어요. 지금 {st['count']}개 있고 {g}등급, "
            f"개당 {price:,}크레딧이에요. 이런 입고 안내를 그만 받으시려면 '그만'이라고 말씀해 주세요.")


def run(args, kit):
    op = args.get('op')
    now = float(args.get('_now') or time.time()) if kit.testing else time.time()
    s = _load(kit)
    _prune(s, now)
    out = _op(op, args, kit, s, now)
    _save(kit, s)
    return out


def _op(op, args, kit, s, now):
    if op in ('want', 'bought', 'opt_out', 'notice'):
        key = customer_key(args.get('customer'))
        if not key:
            return {'ok': False, 'anonymous': True, 'note': '이름을 밝히지 않은 손님은 기록하지 않습니다'}
        c = _cust(s, key)
    sku = str(args.get('sku') or '')

    if op == 'opt_out':
        c['opt_out'] = now
        c['notices'] = []
        return {'ok': True, 'customer': key, 'opt_out': True}

    if op == 'want':
        if not sku:
            return {'ok': False, 'error': 'sku is required'}
        st = stock(inventory(kit, args), sku)
        if st:
            return {'ok': False, 'in_stock': st['count'], 'note': '재고가 있으니 품절 기록 대신 지금 재고로 안내하세요'}
        c['wants'][sku] = {'ts': now, 'name': str(args.get('name') or sku)[:60],
                           'qty': int(args.get('qty') or 1), 'price': args.get('price')}
        return {'ok': True, 'customer': key, 'want': sku}

    if op == 'bought':
        order = str(args.get('order') or '')
        sale = _sales_orders().get(order) if not kit.testing else (args.get('_sales') or {}).get(order)
        if not sale:
            return {'ok': False, 'error': '이 주문은 logs/sales.jsonl의 정산 기록(tx_hash)에 없어서 세지 않습니다'}
        b = c['buys'].setdefault(sku, {'count': 0, 'orders': [], 'name': str(args.get('name') or sku)[:60]})
        if order in b['orders']:
            return {'ok': True, 'customer': key, 'sku': sku, 'count': b['count'], 'duplicate': True}
        b['orders'] = (b['orders'] + [order])[-50:]
        b['count'] += 1
        c['wants'].pop(sku, None)
        return {'ok': True, 'customer': key, 'sku': sku, 'count': b['count'], 'regular': b['count'] >= REGULAR_MIN}

    if op == 'restocked':
        if not sku:
            return {'ok': False, 'error': 'sku is required'}
        inv = inventory(kit, args)
        st = stock(inv, sku)
        if not st:
            return {'ok': False, 'error': '재고에 이 SKU가 없어 입고 안내를 하지 않습니다'}
        price = int(args.get('price') or 0)
        if price < st['floor']:
            return {'ok': False, 'error': '가격이 하한보다 낮습니다', 'floor': st['floor']}
        w = _window(inv, s, now)
        event = f'{w}:{sku}'
        win = s['windows'].setdefault(w, {'ts': now, 'skus': {}, 'posted': None})
        picked, skipped = [], {}
        for key, c in s['customers'].items():
            waited = sku in c['wants']
            regular = (c['buys'].get(sku) or {}).get('count', 0) >= REGULAR_MIN
            if not (waited or regular):
                continue
            if c.get('opt_out'):
                skipped[key] = 'opt_out'
            elif any(e.get('event') == event for e in c['events']):
                skipped[key] = 'already_this_restock'
            elif len(c['events']) >= DAY_CAP:
                skipped[key] = 'day_cap'
            else:
                c['events'].append({'ts': now, 'event': event, 'sku': sku})
                c['notices'] = [n for n in c['notices'] if n['sku'] != sku] + [
                    {'ts': now, 'sku': sku, 'event': event, 'price': price, 'name': st['name'],
                     'why': 'waited' if waited else 'regular'}]
                if waited:
                    c['wants'].pop(sku)
                picked.append({'customer': key, 'why': 'waited' if waited else 'regular'})
        prev = win['skus'].get(sku, {}).get('names', {})
        win['skus'][sku] = {'name': st['name'], 'price': price,
                            'names': dict(prev, **{p['customer']: p['why'] for p in picked if p['customer'] not in prev})}
        return {'ok': True, 'sku': sku, 'window': w, 'in_stock': st['count'], 'grades': st['grades'],
                'picked': picked, 'skipped': skipped,
                'next': 'announce once this supply window is done; notice at each picked customer\'s next message'}

    if op == 'announce':
        dry = bool(args.get('dry_run'))
        w = s.get('last_window')
        win = s['windows'].get(w) if w else None
        if not win or not win['skus']:
            return {'ok': False, 'error': '이 공급 창에 입고 안내할 품목이 없습니다'}
        if win.get('posted'):
            return {'ok': False, 'error': '이 공급 창의 입고 공지는 이미 올렸습니다', 'post': win['posted']}
        if now < s.get('paused_until', 0):
            return {'ok': False, 'error': '우리 글 비추천이 많아 공지를 쉬는 중입니다', 'paused_until': int(s['paused_until'])}
        mine = [p for p in forum(kit, args) if p.get('author') == kit.name]
        hour = sum(int(p.get('dislikes') or 0) for p in mine if now - float(p.get('ts') or 0) < 3600)
        disliked = any(int(p.get('dislikes') or 0) > 0 for p in mine if now - float(p.get('ts') or 0) < 86400)
        last = max([float(p.get('ts') or 0) for p in s['posts'] + mine] + [0])
        if hour >= DISLIKE_PAUSE:
            s['paused_until'] = now + 86400
            return {'ok': False, 'error': '최근 1시간 우리 글 비추천이 많아 24시간 공지를 쉽니다', 'dislikes_last_hour': hour}
        if disliked and now - last < POST_GAP_DISLIKED:
            return {'ok': False, 'error': '우리 글에 비추천이 있어 공지 간격을 6시간으로 늘렸습니다',
                    'next_after': int(last + POST_GAP_DISLIKED)}
        inv = inventory(kit, args)
        lines, gone = [], []
        for sku, v in win['skus'].items():
            st = stock(inv, sku)
            if not st:
                gone.append(sku)
                continue
            ok = {k: why for k, why in v['names'].items() if not (s['customers'].get(k) or {}).get('opt_out')}
            waited = [k for k, why in ok.items() if why == 'waited'][:NAMES_PER_SKU]
            regular = [k for k, why in ok.items() if why == 'regular'][:max(0, NAMES_PER_SKU - len(waited))]
            item = f"{_short(st['name'])} {st['count']}개(지금 {'·'.join(st['grades'])}등급, 개당 {max(v['price'], st['floor']):,}크레딧)"
            thanks = []
            if waited:
                thanks.append(' '.join('@' + _display(k) + '님' for k in waited) + ' 기다려 주셔서 감사해요.')
            if regular:
                thanks.append(' '.join('@' + _display(k) + '님' for k in regular) + ' 늘 찾아 주셔서 감사해요.')
            lines.append(' '.join(thanks + [f'{item} 다시 들어왔어요.']))
        if not lines:
            return {'ok': False, 'error': '공지하려던 품목이 이미 다 팔렸습니다', 'sold_out': gone}
        text = '[노차네 구멍가게 입고] ' + ' '.join(lines) + ' 재고가 있는 동안만 드릴 수 있어요.'
        text = text[:1900]
        if dry or kit.testing:
            return {'ok': True, 'dry_run': True, 'text': text, 'sold_out': gone}
        code, body = post(kit, text)
        if code != 200:
            return {'ok': False, 'status': code, 'response': body, 'text': text}
        pid = (body or {}).get('post') if isinstance(body, dict) else body
        win['posted'] = pid
        s['posts'].append({'ts': now, 'id': pid, 'window': w})
        return {'ok': True, 'post': pid, 'text': text, 'sold_out': gone}

    if op == 'notice':
        if c.get('opt_out') or not c['notices']:
            return {'ok': True, 'customer': key, 'notice': None}
        inv = inventory(kit, args)
        texts = []
        for n in c['notices']:
            st = stock(inv, n['sku'])
            if st:
                texts.append(notice_text(key, n['name'], st, max(int(n['price']), st['floor']), n.get('why', 'waited')))
        c['notices'] = []
        return {'ok': True, 'customer': key, 'notice': ' '.join(texts) or None}

    return {'ok': False, 'error': 'op must be want, bought, opt_out, restocked, announce or notice'}
