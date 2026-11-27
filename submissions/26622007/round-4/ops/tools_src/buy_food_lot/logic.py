"""buy_food_lot v4 (operator): only the checks on 대표님's minimum list (11/25 02:25 "안전장치 최대한 빼봐"): food only,
expected sale price above cost + fees (price_ceiling break-even ceiling), at most 4 per item, and a confirm that does
not lose a paid lot (same quote re-confirmed). Market facts stay: the restock window, the balance must cover the lot.
11/26 removed (no 대표님 source): no-buy while holding 2+ of the SKU, sale-price cap 20,000, cash kept for a next lot.

The market hands a lot over only after POST /api/supply {quote, tx_hash}. v1 sent that once; when it failed the
money was gone and the lot never came (seen at three other shops). Now every purchase leaves a trail in
logs/supply-pending.jsonl, written before the step it covers, latest row per quote wins:
  paying       before kit.pay (quote, sku, qty, total, unit IDs)
  unconfirmed  right after kit.pay returned (tx_hash), before the confirm; again after a failed confirm
  confirmed    the market handed the lot over (2xx; 409 'already paid for'; or after a 404 the lot is found in our
               inventory or the public ledger by unit ID / tx_hash)
  failed       the transfer was reverted on chain (no money moved): not blocking; or kit.pay gave a tx that is not
               ours (foreign_tx) while our nonce stayed the intent's (latest = pending): nothing of ours went out
  resolved     written by the operator after checking the chain by hand
The tx kit.pay gives is read on chain (11/28 02:42: the RPC gave another wallet's tx and the quote stayed open):
from = the shop wallet, to = pay.to, value = wei. Not ours and our nonce moved or cannot be read: the row stays
'paying' (foreign_tx noted, no tx_hash) - outcome uncertain, the operator checks. The chain cannot say: as before.
When the wallet gave no tx (outcome unknown), {confirm: <quote>, tx_hash: <0x...>} confirms with the tx the operator
found (agent-kit.log '[maroo] buy_food_lot sent ... tx/0x...'); the market itself checks that tx pays the quote.
A quote in 'paying' or 'unconfirmed' blocks every new purchase until args {confirm: <quote>} confirms it (the market
keeps a quote 6 h after it ran out). Confirm retries use the same quote and tx_hash and never pay. The tool stops
retrying DEADLINE seconds after it started, inside the kit's 180 s limit, so the row is always there. The operator's
alert cron reads the file and reports open quotes.
"""
import os, re, json, time, httpx
from runpy import run_path
BASE = 'https://market.ourolabs.ai'; RPC = 'https://rpc-testnet.maroo.io'
CONFIRM_TRIES = 5
CONFIRM_WAITS = (2, 4, 8, 16)                 # seconds between tries
CONFIRM_TIMEOUT = 15                          # seconds per confirm request
DEADLINE = 150                                # seconds after the tool started; the kit stops a tool at 180
PAY_NEEDS = 120                               # seconds to keep for the payment (receipt wait up to 90 s) and one confirm
TX = re.compile(r'0x[0-9a-fA-F]{64}')
RETRY_STATUS = (402, 429, 500, 502, 503, 504)
OPEN = ('paying', 'unconfirmed')
MAX_QTY = 4                                   # 대표님 11/25 02:25: 한 창 3품목·품목당 4개 (the market allows 20 per call)
PAY_LIMIT = 5000000                           # 11/27: = permission.pay_limit, the 5,000,000 starting balance; the 500,000
                                              # cap was ours, not a class hard limit (kit default 200,000, set by permission)
GAS = 1000                                    # credits for gas: the balance must cover the lot and its gas
SELL_HOURS = 24                               # the goal sizes qty to about a day of our sales
NEAR_EXPIRY_QTY = 4                           # a lot that expires inside SELL_HOURS: at most this many
HOLD_HOURS = 1                                # the goal sizes qty to about 1 h of our sales: food must last longer
_sleep, _clock = time.sleep, time.time


def getj(c, url, headers=None, params=None):
    r = c.get(url, headers=headers, params=params); r.raise_for_status(); return r.json()


def post(c, url, obj, headers):
    r = c.post(url, json=obj, headers=headers)
    try:
        body = r.json()
    except ValueError:
        body = {'text': r.text[:200]}
    return r.status_code, body


def records(obj):
    if isinstance(obj, list): return obj
    if isinstance(obj, dict):
        for k in ('items', 'catalog', 'products', 'skus', 'data'):
            if isinstance(obj.get(k), list): return obj[k]
    return []


def skuval(x):
    return x.get('sku') or x.get('id') or x.get('item')


def has_surge(obj, sku):
    if isinstance(obj, list):
        for x in obj:
            if x == sku: return True
            if isinstance(x, dict) and sku in (x.get('sku'), x.get('id')): return True
    elif isinstance(obj, dict):
        if sku in obj: return True
        for k in ('surge', 'skus', 'items', 'prices'):
            if k in obj and has_surge(obj[k], sku): return True
    return False


def rpc(method, params, timeout=20):
    r = httpx.post(RPC, json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}, timeout=timeout); r.raise_for_status(); j = r.json()
    if j.get('error') or 'result' not in j: raise RuntimeError('RPC failed')
    return j['result']


FOREIGN_NOTE = 'rpc returned a foreign tx; no credits moved'


def whose_tx(kit, txid, to, wei):
    """'ours' (from the shop wallet to pay.to for wei), 'foreign' (another sender), 'other' (from us, not this
    payment) or None when the chain cannot say (lookup failed twice or no such tx yet)."""
    tx = None
    for i in range(2):
        try:
            tx = rpc('eth_getTransactionByHash', [txid], 10)
        except Exception:
            tx = None
        if isinstance(tx, dict) or i: break
        _sleep(2)
    if not isinstance(tx, dict) or not tx.get('from'): return None
    if str(tx['from']).lower() != str(kit.address('shop')).lower(): return 'foreign'
    try:
        value = int(str(tx.get('value') or '0x0'), 16)
    except ValueError:
        return 'other'
    return 'ours' if str(tx.get('to') or '').lower() == str(to).lower() and value == wei else 'other'


def not_ours(kit, qid, txid, nonce, who):
    """kit.pay gave a tx that is not this payment. Our nonce still the intent's (latest = pending): nothing of ours
    went out, 'failed'. Else (moved, unreadable, or a tx of ours to someone else): the 'paying' row stays open."""
    try:
        now = [int(rpc('eth_getTransactionCount', [kit.address('shop'), t], 10), 16) for t in ('latest', 'pending')]
    except Exception:
        now = None
    row = {'quote': qid, 'foreign_tx': txid, 'nonce': nonce, 'nonce_now': now}
    if who == 'foreign' and now == [nonce, nonce]:
        row.update(state='failed', note=FOREIGN_NOTE)
        out = {'ok': False, 'quote': qid, 'foreign_tx': txid, 'error': 'the wallet RPC gave a tx that is not ours and our '
               'nonce did not move: no credits moved. Nothing to confirm; ask for a new quote and buy again'}
    else:
        row.update(state='paying', note='rpc gave a tx that is not this payment; our nonce moved or is unreadable')
        print('WARNING buy_food_lot: payment outcome uncertain for quote', qid, '(the wallet gave a tx that is not ours)')
        out = {'ok': False, 'quote': qid, 'foreign_tx': txid, 'error': 'payment outcome uncertain: the wallet gave a tx '
               'that is not this payment and our nonce moved or cannot be read; new purchases are blocked until checked'}
    try:
        append(pending_path(kit), row)
    except Exception as e:                      # no row: the paying row stays open, which blocks (safe side)
        print('WARNING buy_food_lot: could not write', pending_path(kit), type(e).__name__)
    return out


def _test_files(kit):
    return bool(getattr(kit, 'testing', False) or getattr(kit, 'test_files', False))


def pending_path(kit):
    return 'logs/supply-pending.test.jsonl' if _test_files(kit) else 'logs/supply-pending.jsonl'


def sends_path(kit):
    return 'logs/sends.test.jsonl' if _test_files(kit) else 'logs/sends.jsonl'


def append(path, row):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(dict({'ts': int(_clock())}, **row), ensure_ascii=False, separators=(',', ':')) + '\n')


def latest(path):
    """{quote: merged row} - later rows of a quote add to (and override) earlier ones."""
    out = {}
    try:
        lines = open(path, encoding='utf-8').read().splitlines()
    except FileNotFoundError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get('quote') is not None:
            q = str(row['quote'])
            out[q] = dict(out.get(q, {}), **row)
    return out


def unconfirmed(path):
    """Quotes that were (or may have been) paid and are not confirmed yet."""
    return {q: r for q, r in latest(path).items() if r.get('state') in OPEN}


def break_even(sku, qty, quote, list_price, now):
    """Most the goods of this lot may cost: price_ceiling's break-even ceiling per unit (its median paid price, fee 1% +
    210, gas, this quote's storage + forum fees; unsold 0 because the goal sizes qty to sales speed) x qty.
    A SKU price_ceiling has no data for falls back to selling at list x 0.6. Returns (max goods total, source)."""
    dues = int(quote.get('storage') or 0) + int(quote.get('forum_fees') or 0)
    pc = run_path('ops/price_ceiling.py')
    item = pc['DATA']['items'].get(sku)
    if not item:
        return list_price * 0.6 * 0.99 * qty - 210 * qty - 206 - dues, 'list x0.6 (no price_ceiling data)'
    shelf = item[3]
    left = [(u['expires_at'] - now) / 3600 for u in quote.get('units') or []
            if isinstance(u, dict) and isinstance(u.get('expires_at'), (int, float))]
    if left and min(left) <= HOLD_HOURS:
        return -1, 'price_ceiling: a unit expires within %d h' % HOLD_HOURS
    remaining = min(min(left), shelf) if left else shelf
    if remaining < SELL_HOURS and qty > NEAR_EXPIRY_QTY:
        return -1, 'expires in %.0f h (< %d h of selling): buy at most %d' % (remaining, SELL_HOURS, NEAR_EXPIRY_QTY)
    goods = quote['goods']
    r = pc['quote'](dict(sku=sku, action='buy', cost=goods / qty, qty=qty, batch_size=qty, lot_dues=dues, unsold=0,
                         hold_hours=max(HOLD_HOURS, min(SELL_HOURS, remaining * 0.75)), remaining_hours=remaining))
    if r['max_buy_cost'] is None:
        return -1, 'price_ceiling: no profitable price'
    return r['max_buy_cost'] * qty, 'price_ceiling'


def confirm_supply(send, quote, tx_hash, deadline=None, tries=None, waits=None):
    """send(body) -> (status, json). Confirms one paid quote with the same tx_hash; never pays.
    Returns {confirmed, status, result, attempts, error}."""
    tries = CONFIRM_TRIES if tries is None else tries
    waits = CONFIRM_WAITS if waits is None else waits
    last, n = {'status': None, 'result': None, 'error': None}, 0
    for i in range(tries):
        if deadline is not None and _clock() + CONFIRM_TIMEOUT > deadline:
            last = dict(last, error=last['error'] or 'out of time before the confirm')
            break
        n = i + 1
        try:
            st, body = send({'quote': quote, 'tx_hash': tx_hash})
        except Exception as e:                  # network: the request may or may not have arrived; asking again is safe
            last = {'status': None, 'result': None, 'error': type(e).__name__ + ': ' + str(e)[:120]}
        else:
            if 200 <= st < 300:
                return {'confirmed': True, 'status': st, 'result': body, 'attempts': n, 'error': None}
            if st == 409 and 'already paid' in json.dumps(body):
                return {'confirmed': True, 'status': st, 'result': body, 'attempts': n, 'error': None,
                        'note': 'an earlier confirm handed the lot over'}
            last = {'status': st, 'result': body, 'error': None}
            if st not in RETRY_STATUS:          # 404: the market no longer knows the quote - asking again will not help
                break
        if i < tries - 1:
            wait = waits[min(i, len(waits) - 1)]
            if deadline is not None and _clock() + wait + CONFIRM_TIMEOUT > deadline:
                last = dict(last, error=last['error'] or 'out of time')
                break
            _sleep(wait)
    return dict(last, confirmed=False, attempts=n)


def delivered(c, headers, units, tx_hash, deadline=None):
    """After a 404 (the quote is gone): did the lot reach us anyway? Our inventory or the public ledger shows it."""
    marks = [u for u in units or [] if isinstance(u, str) and u] + ([tx_hash] if tx_hash else [])
    if not marks:
        return False
    for url, h, params in ((BASE + '/api/inventory', headers, None), (BASE + '/api/ledger', None, {'after': -500})):
        if deadline is not None and _clock() + 8 > deadline:
            break
        try:
            text = json.dumps(getj(c, url, h, params))
        except Exception:
            continue
        if any(m in text for m in marks):
            return True
    return False


def settle_confirm(c, headers, kit, row, deadline, given=None):
    """Confirm one open row; writes the outcome row. Returns the confirm result (with 'confirmed').
    given: a tx the operator supplied - kept as tx_hash only once the market confirms it (a typo must not stick), and
    not used for the ledger lookup after a 404 (only our own unit IDs and our own payment's tx count there)."""
    path, quote = pending_path(kit), str(row['quote'])
    tx = given or row.get('tx_hash')
    if not tx:
        return {'confirmed': False, 'status': None, 'result': None, 'attempts': 0,
                'error': 'no tx_hash known for this quote: the payment outcome is unknown; the operator checks the chain'}
    cc = httpx.Client(timeout=CONFIRM_TIMEOUT)
    try:
        res = confirm_supply(lambda body: post(cc, BASE + '/api/supply', body, headers), quote, tx, deadline=deadline)
    finally:
        cc.close()
    if not res['confirmed'] and res['status'] == 404 and delivered(c, headers, row.get('units'), None if given else tx, deadline):
        res = dict(res, confirmed=True, note='the quote is gone but the lot is in our inventory or the ledger')
    if res['confirmed']:
        append(path, {'quote': quote, 'tx_hash': tx, 'state': 'confirmed', 'status': res['status'], 'attempts': res['attempts']})
    else:
        append(path, dict({'tried_tx': tx} if given else {'tx_hash': tx},
                          quote=quote, state='unconfirmed', status=res['status'], error=res['error']))
        print('WARNING buy_food_lot: paid quote', quote, 'is not confirmed (status', res['status'], ')')
    return res


def _confirm_only(args, kit, deadline):
    quote = str(args.get('confirm'))
    row = unconfirmed(pending_path(kit)).get(quote)
    if not row:
        return {'ok': False, 'error': 'no unconfirmed paid lot with that quote in ' + pending_path(kit)}
    given = args.get('tx_hash')
    if given is not None:                       # the operator found the tx (outcome was unknown); the market verifies it
        if not isinstance(given, str) or not TX.fullmatch(given):
            return {'ok': False, 'error': 'tx_hash must be 0x and 64 hex digits'}
        if row.get('tx_hash') and row['tx_hash'].lower() != given.lower():
            return {'ok': False, 'error': 'this quote already has another tx_hash', 'tx_hash': row['tx_hash']}
    headers = {'authorization': 'Bearer ' + kit.token('shop')}
    with httpx.Client(timeout=25) as c:
        res = settle_confirm(c, headers, kit, row, deadline, given=given)
    if res['confirmed']:
        return {'ok': True, 'confirmed': quote, 'result': res['result'], 'note': res.get('note')}
    return {'ok': False, 'paid_unconfirmed': True, 'quote': quote, 'status': res['status'], 'response': res['result'],
            'error': res['error'] or 'the market did not confirm; do not pay again, the operator is alerted'}


def run(args, kit):
    deadline = _clock() + DEADLINE
    if not isinstance(args, dict): return {'ok': False, 'error': 'args must be an object'}
    if 'confirm' in args:
        if kit.testing: return {'ok': False, 'error': 'testing mode: no confirm sent'}
        return _confirm_only(args, kit, deadline)
    sku = args.get('sku'); qty = args.get('qty')
    if not isinstance(sku, str) or not sku: return {'ok': False, 'error': 'sku required'}
    if not isinstance(qty, int) or qty < 1 or qty > MAX_QTY: return {'ok': False, 'error': 'qty must be 1 to %d (대표님: 품목당 4개)' % MAX_QTY}
    waiting = unconfirmed(pending_path(kit))
    if waiting:
        return {'ok': False, 'error': 'a paid lot is not confirmed yet: call buy_food_lot {confirm: quote} first, never pay it again',
                'unconfirmed': sorted(waiting)}
    if kit.testing: return {'ok': False, 'error': 'testing mode: no purchase sent'}
    headers = {'authorization': 'Bearer ' + kit.token('shop')}
    with httpx.Client(timeout=25) as c:
        inv = getj(c, BASE + '/api/inventory', headers)
        if not inv.get('restock', {}).get('open'): return {'ok': False, 'error': 'restock window closed', 'restock': inv.get('restock')}
        catalog = getj(c, BASE + '/api/catalog')
        entry = next((x for x in records(catalog) if skuval(x) == sku), None)
        if not entry: return {'ok': False, 'error': 'SKU not found in catalog'}
        kind = str(entry.get('kind', '')).lower(); name = str(entry.get('name', '')); category = str(entry.get('category', ''))
        if kind != 'food': return {'ok': False, 'error': 'food only'}
        list_price = entry.get('list_price')
        if not isinstance(list_price, (int, float)) or list_price <= 0: return {'ok': False, 'error': 'no list price'}
        bal = inv.get('balance', 0)
        status, quote = post(c, BASE + '/api/supply', {'sku': sku, 'qty': qty}, headers)
        if status not in (200, 402): return {'ok': False, 'status': status, 'response': quote}
        total = quote.get('total')
        pay = quote.get('pay')
        if not isinstance(total, (int, float)) or not isinstance(pay, dict): return {'ok': False, 'error': 'unexpected quote format', 'response': quote}
        goods = quote.get('goods')
        if not isinstance(goods, (int, float)): return {'ok': False, 'error': 'quote has no goods price', 'response': quote}
        try:
            ceiling, source = break_even(sku, qty, quote, list_price, _clock())
        except Exception as e:
            return {'ok': False, 'error': 'break-even check failed: ' + type(e).__name__ + ': ' + str(e)[:120]}
        if goods > ceiling: return {'ok': False, 'error': 'quote exceeds break-even ceiling', 'goods': goods, 'max_goods': ceiling, 'source': source}
        if not isinstance(bal, (int, float)) or bal < total + GAS:   # a payment the wallet cannot cover would leave the quote unsettled
            return {'ok': False, 'error': 'the balance does not cover this lot and its gas', 'balance': bal, 'total': total}
        if not quote.get('quote') or not pay.get('to') or not pay.get('wei') or not pay.get('data'): return {'ok': False, 'error': 'quote/payment fields incomplete'}
        wei = int(str(pay['wei']), 0) if isinstance(pay['wei'], str) else int(pay['wei'])
        if wei <= 0 or wei > PAY_LIMIT * 10 ** 15: return {'ok': False, 'error': 'payment is above the kit pay_limit (the starting balance)', 'total': total}
        qid = str(quote['quote'])
        seen = latest(pending_path(kit)).get(qid)
        if seen:                                # the market can hand the same quote out again: never pay a quote twice
            why = {'failed': ('no credits moved (the wallet gave a foreign tx)' if seen.get('foreign_tx') else
                              'its transfer was reverted') + '; the market gives a new quote about 2 minutes after this one',
                   'confirmed': 'it is already ours', 'resolved': 'the operator closed it'}.get(seen.get('state'),
                                                                                          'call buy_food_lot {confirm: quote}')
            return {'ok': False, 'error': 'this quote was already paid or tried: ' + why, 'quote': qid}
        if deadline - _clock() < PAY_NEEDS:      # not enough time left to pay and confirm inside the kit's limit
            return {'ok': False, 'error': 'too little time left in this call to pay and confirm; ask again', 'quote': qid}
        units = [u.get('unit') for u in quote.get('units') or [] if isinstance(u, dict) and u.get('unit')]
        nonce = int(rpc('eth_getTransactionCount', [kit.address('shop'), 'pending']), 16)
        append(sends_path(kit), {'purpose': 'supply', 'order': qid, 'to': pay['to'], 'amount': total, 'nonce': nonce})
        append(pending_path(kit), {'quote': qid, 'state': 'paying', 'sku': sku, 'qty': qty, 'total': total, 'units': units})
        try:
            tx = kit.pay(pay['to'], wei, pay['data'], role='shop')
        except Exception as e:
            found = TX.findall(str(e))
            if found and 'failed on chain' in str(e):     # reverted: no credits moved, nothing to confirm
                who = whose_tx(kit, found[0], pay['to'], wei)
                if who in ('foreign', 'other'):
                    return not_ours(kit, qid, found[0], nonce, who)
                append(pending_path(kit), {'quote': qid, 'tx_hash': found[0], 'state': 'failed'})
                return {'ok': False, 'error': 'the transfer was reverted on chain; no credits moved', 'tx_hash': found[0], 'quote': qid}
            if not found:                       # the paying row stays open: no new purchase until it is sorted out
                print('WARNING buy_food_lot: payment outcome unknown for quote', qid)
                return {'ok': False, 'error': 'payment outcome uncertain; verify nonce and chain before any retry; new purchases are blocked',
                        'detail': str(e)[:200], 'quote': qid}
            tx = found[0]
        txid = tx if isinstance(tx, str) else str(tx)
        who = whose_tx(kit, txid, pay['to'], wei)
        if who in ('foreign', 'other'):         # not our payment: never recorded as ours, never confirmed
            return not_ours(kit, qid, txid, nonce, who)
        for path, row in ((pending_path(kit), {'quote': qid, 'tx_hash': txid, 'state': 'unconfirmed'}),
                          (sends_path(kit), {'purpose': 'supply', 'order': qid, 'to': pay['to'], 'amount': total, 'tx_hash': txid})):
            try:
                append(path, row)
            except Exception as e:              # a record that cannot be written must not stop the confirm
                print('WARNING buy_food_lot: could not write', path, type(e).__name__)
        res = settle_confirm(c, headers, kit, {'quote': qid, 'tx_hash': txid, 'units': units}, deadline)
    out = {'sku': sku, 'name': name, 'qty': qty, 'total': total, 'tx_hash': txid, 'quote': qid, 'max_goods': ceiling, 'source': source,
           'confirm_attempts': res['attempts'], 'status': res['status'], 'result': res['result']}
    if res['confirmed']:
        return dict(out, ok=True, note=res.get('note'))
    return dict(out, ok=False, paid_unconfirmed=True,
                error='paid but the market did not confirm the lot; do not pay again: call buy_food_lot {confirm: "' + qid + '"} later')
