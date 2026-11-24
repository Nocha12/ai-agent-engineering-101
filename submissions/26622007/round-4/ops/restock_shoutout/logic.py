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
The forum names only customers who asked for the SKU while it was sold out; regulars are thanked in their notice
(our sales are sealed, so a public "you keep buying X" would undo that) unless NAME_REGULARS is set.
"""
import json
import math
import os
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
NAME_REGULARS = False        # name regulars on the forum too (operator's choice; off keeps sealed sales private)
GAS, FEE = 205, 210          # per-lot gas and per-sale fee, credits (agent.json (3))
POST_GAP_DISLIKED = 6 * 3600 # after a dislike on one of our posts, at most one post per 6 h
DISLIKE_PAUSE = 3            # this many dislikes on our posts in the last hour: no post for 24 h
PENDING_RECHECK = 600        # a post whose outcome is unknown blocks a new one this long, unless the forum shows it
MAX_TEXT = 1900
HEAD = '[노차네 구멍가게 입고]'
ANON = ('did not say who it is', 'a person in your app')
SAFE_NAME = re.compile(r'[가-힣]{2,6}(-[0-9a-f]{4})?')     # village names look like 이름-1a2b (hex)
RISKY = ('할인', '무료', '공짜', '환불', '승인', '지시', '운영자', '사장', '관리자', '반값', '세일', '쿠폰', '이벤트', '공지',
         '특가', '무상', '입고', '재고', '가게')


class StateError(Exception):
    pass


def _file(kit):
    return 'shoutout.test.json' if kit.testing else 'shoutout.json'


def _load(kit):
    try:
        with open(_file(kit), encoding='utf-8') as f:
            s = json.load(f)
    except FileNotFoundError:
        s = {}
    except (OSError, ValueError) as e:
        raise StateError(f'{_file(kit)} is unreadable ({type(e).__name__}); fix it by hand, opt-outs live there')
    if not isinstance(s, dict):
        raise StateError(f'{_file(kit)} is not an object; fix it by hand, opt-outs live there')
    for k, v in (('customers', {}), ('windows', {}), ('posts', []), ('orders', [])):
        if not isinstance(s.get(k), type(v)):
            s[k] = v
    return s


def _save(kit, s):
    tmp = _file(kit) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _file(kit))                        # a crash never leaves half a file (opt-outs live there)


def _num(v, default=0):
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return v
    m = re.search(r'\d+(?:\.\d+)?', str(v or '').replace(',', ''))
    return float(m.group()) if m else default


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


def speakable(key):
    """A name we may print: short, plain letters, and nothing that reads like an order or a price."""
    k = str(key or '').strip()
    return k if SAFE_NAME.fullmatch(k) and not any(w in k for w in RISKY) else None


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
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None                                  # unknown outcome (a POST may still have landed)


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
    posts = body if isinstance(body, list) else (body or {}).get('posts') if isinstance(body, dict) else []
    return [p for p in posts or [] if isinstance(p, dict)]


def post(kit, text):
    return _http(kit, 'POST', '/api/forum', {'text': text})


def stock(inv, sku):
    units = [u for u in inv.get('units') or [] if isinstance(u, dict) and u.get('sku') == sku]
    if not units:
        return None
    grades = sorted({str(u.get('grade') or '?') for u in units})
    costs = [_num(u.get('cost')) for u in units]
    return {'count': len(units), 'name': units[0].get('name') or sku, 'grades': grades,
            'floor': math.ceil((max(costs) + GAS + FEE) / 0.99)}


def _ts(v):
    t = _num(v, 0)
    return t / 1000 if t > 1e12 else t                 # the market writes epoch seconds; ms just in case


def _mine(kit, p):
    return isinstance(p, dict) and kit.name in (p.get('author'), p.get('owner'))


def _short(name):
    n = re.sub(r'\[[^\]]*\]', '', str(name)).strip()
    return n[:24]


def _window(inv, s, now):
    r = inv.get('restock') if isinstance(inv.get('restock'), dict) else {}
    opens = _num(r.get('opens_at'), 0)
    last = s.get('last_window')
    if r.get('open') and opens:
        w = str(int(opens))
    elif last and now - _num(s.get('last_window_ts')) < 5400 and not (s['windows'].get(last) or {}).get('posted'):
        w = last
    else:
        w = 'h' + str(int(now // 3600))
    s['last_window'], s['last_window_ts'] = w, now
    return w


def _prune(s, now):
    for c in s['customers'].values():
        c['wants'] = {k: v for k, v in (c.get('wants') or {}).items() if now - _num(v.get('ts')) < WANT_TTL}
        c['notices'] = [n for n in c.get('notices') or [] if now - _num(n.get('ts')) < NOTICE_TTL]
        c['events'] = [e for e in c.get('events') or [] if now - _num(e.get('ts')) < 86400]
        c.setdefault('buys', {})
    s['posts'] = [p for p in s['posts'] if now - _num(p.get('ts')) < 7 * 86400]
    for w in sorted(s['windows'], key=lambda w: _num(s['windows'][w].get('ts')))[:-20]:
        s['windows'].pop(w, None)
    s['orders'] = s['orders'][-2000:]


def _cust(s, key):
    c = s['customers'].setdefault(key, {})
    for k, v in (('wants', {}), ('buys', {}), ('events', []), ('notices', []), ('opt_out', None)):
        c.setdefault(k, v)
    return c


def _sales_orders():
    out = {}
    try:
        with open('logs/sales.jsonl', encoding='utf-8') as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get('order') and r.get('tx_hash'):
                    out[str(r['order'])] = r
    except OSError:
        pass
    return out


def notice_text(key, sku_name, st, price, why='waited'):
    who = (speakable(key) + '님') if speakable(key) else '손님'
    lead = '기다리시던' if why == 'waited' else '자주 찾아 주시던'
    return (f"{who}, {lead} {_short(sku_name)} 다시 들어왔어요. 지금 {st['count']}개 있고 "
            f"{'·'.join(st['grades'])}등급, 개당 {int(price):,}크레딧이에요. "
            f"이런 입고 안내를 그만 받으시려면 '그만'이라고 말씀해 주세요.")


def run(args, kit):
    op = args.get('op')
    now = float(args.get('_now') or time.time()) if kit.testing else time.time()
    try:
        s = _load(kit)
    except StateError as e:
        return {'ok': False, 'error': str(e)}
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
    sku = str(args.get('sku') or '').strip()

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
                           'qty': int(_num(args.get('qty'), 1)) or 1, 'price': _num(args.get('price'), None)}
        return {'ok': True, 'customer': key, 'want': sku}

    if op == 'bought':
        order = str(args.get('order') or '').strip()
        if not sku or not order:
            return {'ok': False, 'error': 'sku and order are required'}
        sale = (args.get('_sales') or {}).get(order) if kit.testing else _sales_orders().get(order)
        if not sale:
            return {'ok': False, 'error': '이 주문은 logs/sales.jsonl의 정산 기록(tx_hash)에 없어서 세지 않습니다'}
        b = c['buys'].setdefault(sku, {'count': 0, 'orders': [], 'name': str(args.get('name') or sku)[:60]})
        if order in s['orders']:
            return {'ok': True, 'customer': key, 'sku': sku, 'count': b['count'], 'duplicate': True}
        s['orders'].append(order)                       # one order is one purchase, whoever it is credited to
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
        price = int(_num(args.get('price')))
        if price < st['floor']:
            return {'ok': False, 'error': '가격이 하한보다 낮습니다', 'floor': st['floor']}
        w = _window(inv, s, now)
        event = f'{w}:{sku}'
        win = s['windows'].setdefault(w, {'ts': now, 'skus': {}, 'posted': None})
        picked, skipped = [], {}
        for key, c in s['customers'].items():
            c = _cust(s, key)
            waited = sku in c['wants']
            regular = _num((c['buys'].get(sku) or {}).get('count')) >= REGULAR_MIN
            if not (waited or regular):
                continue
            if c.get('opt_out'):
                skipped[key] = 'opt_out'
            elif any(e.get('event') == event for e in c['events']):
                skipped[key] = 'already_this_restock'
            elif len(c['events']) >= DAY_CAP:
                skipped[key] = 'day_cap'
            else:
                why = 'waited' if waited else 'regular'
                c['events'].append({'ts': now, 'event': event, 'sku': sku})
                c['notices'] = [n for n in c['notices'] if n.get('sku') != sku] + [
                    {'ts': now, 'sku': sku, 'event': event, 'price': price, 'name': st['name'], 'why': why}]
                if waited:
                    c['wants'].pop(sku)
                picked.append({'customer': key, 'why': why})
        prev = (win['skus'].get(sku) or {}).get('names') or {}
        win['skus'][sku] = {'name': st['name'], 'price': price,
                            'names': dict(prev, **{p['customer']: p['why'] for p in picked if p['customer'] not in prev})}
        return {'ok': True, 'sku': sku, 'window': w, 'in_stock': st['count'], 'grades': st['grades'],
                'picked': picked, 'skipped': skipped,
                'next': 'announce once this supply window is done; notice at each picked customer\'s next message'}

    if op == 'announce':
        return _announce(args, kit, s, now)

    if op == 'notice':
        if c.get('opt_out') or not c['notices']:
            return {'ok': True, 'customer': key, 'notice': None}
        inv = inventory(kit, args)
        texts = []
        for n in c['notices']:
            st = stock(inv, n.get('sku'))
            if st:                                      # sold out since: say nothing rather than promise it
                texts.append(notice_text(key, n.get('name') or n.get('sku'), st,
                                         max(int(_num(n.get('price'))), st['floor']), n.get('why', 'waited')))
        c['notices'] = []
        return {'ok': True, 'customer': key, 'notice': ' '.join(texts) or None}

    return {'ok': False, 'error': 'op must be want, bought, opt_out, restocked, announce or notice'}


def _announce(args, kit, s, now):
    dry = bool(args.get('dry_run')) or kit.testing
    w = s.get('last_window')
    win = s['windows'].get(w) if w else None
    if not win or not win.get('skus'):
        return {'ok': False, 'error': '이 공급 창에 입고 안내할 품목이 없습니다'}
    if now < _num(s.get('paused_until')):
        return {'ok': False, 'error': '우리 글 비추천이 많아 공지를 쉬는 중입니다', 'paused_until': int(s['paused_until'])}
    rows = forum(kit, args)
    mine = [dict(p, ts=_ts(p.get('ts'))) for p in rows if _mine(kit, p)]
    posted = win.get('posted')
    if posted == 'pending':                             # the last POST had no clear answer: did it land?
        hit = next((p for p in mine if str(p.get('text', '')).startswith(HEAD) and p['ts'] >= _num(win.get('pending_ts')) - 60), None)
        if hit:
            win['posted'] = hit.get('id') or 'found'
            s['posts'].append({'ts': hit['ts'], 'id': hit.get('id'), 'window': w})
            return {'ok': False, 'error': '이 공급 창의 입고 공지는 이미 올라가 있습니다', 'post': hit.get('id')}
        if now - _num(win.get('pending_ts')) < PENDING_RECHECK:
            return {'ok': False, 'error': '지난 공지 결과를 아직 확인하지 못해 10분 뒤에 다시 봅니다'}
        if _num(win.get('attempts')) >= 2:              # twice without an answer: a missing post beats a duplicate
            win['posted'] = 'unknown'
            return {'ok': False, 'error': '공지 결과를 두 번 확인하지 못해 이 공급 창은 공지를 그만둡니다'}
    elif posted:
        return {'ok': False, 'error': '이 공급 창의 입고 공지는 이미 올렸습니다', 'post': posted}
    hour = sum(int(_num(p.get('dislikes'))) for p in mine if 0 <= now - p['ts'] < 3600)
    disliked = any(_num(p.get('dislikes')) > 0 for p in mine if 0 <= now - p['ts'] < 86400)
    last = min(now, max([_num(p.get('ts')) for p in s['posts']] + [p['ts'] for p in mine] + [0]))
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
        ok = {k: why for k, why in (v.get('names') or {}).items()
              if not (s['customers'].get(k) or {}).get('opt_out') and speakable(k)}
        waited = [k for k, why in ok.items() if why == 'waited'][:NAMES_PER_SKU]
        regular = [k for k, why in ok.items() if why == 'regular' and NAME_REGULARS][:max(0, NAMES_PER_SKU - len(waited))]
        item = (f"{_short(st['name'])} {st['count']}개(지금 {'·'.join(st['grades'])}등급, "
                f"개당 {max(int(_num(v.get('price'))), st['floor']):,}크레딧)")
        thanks = []
        if waited:
            thanks.append(' '.join('@' + speakable(k) + '님' for k in waited) + ' 기다려 주셔서 감사해요.')
        if regular:
            thanks.append(' '.join('@' + speakable(k) + '님' for k in regular) + ' 늘 찾아 주셔서 감사해요.')
        lines.append(' '.join(thanks + [f'{item} 다시 들어왔어요.']))
    if not lines:
        return {'ok': False, 'error': '공지하려던 품목이 이미 다 팔렸습니다', 'sold_out': gone}
    tail = ' 재고가 있는 동안만 드릴 수 있어요.'
    text, dropped = HEAD, []
    for ln in lines:                                    # whole lines only: never cut a price or a count in half
        if len(text) + 1 + len(ln) + len(tail) <= MAX_TEXT:
            text += ' ' + ln
        else:
            dropped.append(ln)
    text += tail
    if dry:
        return {'ok': True, 'dry_run': True, 'text': text, 'sold_out': gone, 'dropped_lines': len(dropped)}
    win['posted'], win['pending_ts'], win['attempts'] = 'pending', now, int(_num(win.get('attempts'))) + 1
    _save(kit, s)                                       # a retry after a lost answer finds 'pending', not a fresh window
    code, body = post(kit, text)
    if 200 <= code < 300:
        pid = body.get('post') if isinstance(body, dict) else body
        win['posted'] = pid or f'posted@{int(now)}'        # any answer that says yes closes the window
        s['posts'].append({'ts': now, 'id': pid, 'window': w})
        return {'ok': True, 'post': pid, 'text': text, 'sold_out': gone}
    if code and code != 0:                              # the market answered no: nothing was posted
        win['posted'] = None
        return {'ok': False, 'status': code, 'response': body}
    return {'ok': False, 'error': '포럼 응답을 받지 못했습니다. 10분 뒤 announce가 올라갔는지 확인합니다'}
