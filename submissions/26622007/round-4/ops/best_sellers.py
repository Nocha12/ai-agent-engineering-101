"""Round 4 best sellers from money that actually moved (read-only; no wallet, token or operating state).

Input is the same public data directory price_ceiling's cache reads (~/work/ax-round4-analytics/data:
ledger.json from GET /api/ledger, catalog.json, txs.json with the chain check of each settle, and chain/rebuild.json
from chain_scan.py). Only settle rows with a tx_hash count, and a row whose on-chain transfer does not match
price - (1% + 210) is dropped. Sales found on chain but not in the ledger are added with the SKU guessed from price.
The newest snapshots/*.json (public shop stock) is merged in by price_demand_cache.merge_observations, so the output
is the shared cache price_demand.py / price_ceiling read (sku_stock, stock_as_of, ledger_as_of, stock_unconfirmed).

  python3 -B ops/best_sellers.py --build --data-dir DIR [--output ops/best_sellers_data.json]
  python3 -B ops/best_sellers.py [--top 5] [--horizon-hours 3] [--kind food] [--json]
  python3 -B ops/best_sellers_forge.py > /tmp/best-sellers-move.json      # the v5.1 forge_tool move
"""

import argparse
import json
import sys
import time
from pathlib import Path


# BEGIN FORGE KERNEL
QTY_CAP = 4                                # per SKU per restock, as the 20:07 analysis found safe
MIN_EVIDENCE = 2                           # paid sales in the last 6 h before any buy is suggested
ARGS = {'top', 'horizon_hours', 'own_stock', 'kind', 'min_tx'}
CHAIN_ONLY = 2                             # sales row check value: found on chain only, SKU guessed from its price


def norm_url(u):
    """The market's norm_url: strip, no trailing slash (settle rows use it, join rows may not)."""
    return str(u or '').strip().rstrip('/')


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return None if not n else xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _bid(name):
    """A short buyer id (repeat buyers without keeping names)."""
    return sum(ord(c) * (i + 1) for i, c in enumerate(str(name))) % 99991


def merge_live(data, rows, now):
    """Add rows newer than data['last_seq'] (from GET /api/ledger?after=...): shop joins update the URL map, paid
    settles become sales rows of the same shape as build_data's."""
    data = dict(data, sales=list(data['sales']), items=dict(data['items']), shops=list(data['shops']),
                url_names=dict(data.get('url_names') or {}))
    last = data['last_seq']
    for e in rows:
        if not isinstance(e, dict) or not isinstance(e.get('seq'), int) or e['seq'] <= last:
            continue
        last = max(last, e['seq'])
        d = e.get('data') or {}
        if e.get('kind') == 'join' and d.get('role') == 'shop' and d.get('url') and d.get('name'):
            data['url_names'][norm_url(d['url'])] = str(d['name'])
        if e.get('kind') != 'settle' or not d.get('tx_hash') or not isinstance(d.get('amount'), (int, float)):
            continue
        sku = d.get('sku') or d.get('item')
        if sku not in data['items']:
            data['items'][sku] = [str(d.get('name') or sku)[:40], None, None]
        shop = data['url_names'].get(norm_url(d.get('shop')), norm_url(d.get('shop')))
        if shop not in data['shops']:
            data['shops'].append(shop)
        seen = (data.get('chain_only_tx') or {}).get(str(d['tx_hash']).lower())
        if seen:                             # the build already counted this sale from the chain: swap, not add
            for i, r in enumerate(data['sales']):
                if r[5] == CHAIN_ONLY and r[0] == seen[0] and r[2] == seen[1]:
                    del data['sales'][i]
                    break
        data['sales'].append([e.get('ts', now), sku, d['amount'], data['shops'].index(shop), _bid(d.get('buyer')), 0])
    data['last_seq'] = last
    return data


def price_from_transfers(shop_gets, market_fee):
    """A settle pays the shop price - (price//100 + 210) and the market 2 x (price//100) (checked on every ledger
    settle). The shop's part alone is ambiguous (2999 and 3000 give the same), the pair is not. None if they disagree."""
    p = shop_gets + market_fee // 2 + 210
    return p if market_fee % 2 == 0 and market_fee == 2 * (p // 100) and shop_gets >= 0 else None


def chain_settles(txs, market, shops):
    """Paid sales from raw transfers [{ts, hash, frm, to, credits, input}]: a buyer -> shop transfer with a 32-byte
    input counts only when the same buyer also paid the market a fee with the same input (the market's settle check).
    A buyer who paid the shop twice with one input (a retry) has one sale: the last payment at or before the fee.
    Returns [ts, shop name, price, buyer address, tx hash]."""
    fee = {}
    for t in txs:
        if t['to'] == market and len(t['input']) == 66:
            fee.setdefault((t['frm'], t['input']), t)
    pays = {}
    for t in txs:
        k = (t['frm'], t['input'])
        if t['to'] in shops and t['frm'] != market and len(t['input']) == 66 and k in fee:
            pays.setdefault(k, []).append(t)
    best = {}
    for k, ps in pays.items():                 # same rule as chain_scan.classify: the latest at or before the fee
        before = [t for t in ps if t['ts'] <= fee[k]['ts']] or ps
        best[k] = max(before, key=lambda t: (t['ts'], t['hash']))
    out = []
    for k, t in best.items():
        p = price_from_transfers(t['credits'], fee[k]['credits'])
        if p is not None:
            out.append([t['ts'], shops[t['to']], p, t['frm'], t['hash']])
    return sorted(out)


def guess_sku(price, bands):
    """The chain has no SKU: the one SKU whose paid prices (lowest..highest ever seen) hold this price, else None.
    No widening: a guessed row counts as evidence for buy_qty, so an ambiguous price stays unlabelled."""
    fits = [s for s, (lo, hi) in bands.items() if lo <= price <= hi]
    return fits[0] if len(fits) == 1 else None


def merge_chain(data, settles):
    """Add chain-found sales [ts, shop, price, buyer, hash]; returns (data, unlabelled). Skips sales at or before
    the ledger's newest row (those the ledger has, or payments the market refused) and ones already added.
    data['chain_only_tx'] {hash: [ts, price]} lets merge_live swap a chain-only row for its ledger row later."""
    data = dict(data, sales=list(data['sales']), shops=list(data['shops']),
                chain_only_tx=dict(data.get('chain_only_tx') or {}))
    c = data.get('chain') or {}
    unlabelled = 0
    for ts, shop, price, buyer, h in settles:
        if h in data['chain_only_tx'] or ts <= c.get('ledger_ts', 0):
            continue
        sku = guess_sku(price, c.get('bands') or {})
        if sku is None:
            unlabelled += 1
            continue
        if shop not in data['shops']:
            data['shops'].append(shop)
        name = (c.get('buyers') or {}).get(buyer, buyer)   # same id as its ledger rows
        data['sales'].append([ts, sku, price, data['shops'].index(shop), _bid(name), CHAIN_ONLY])
        data['chain_only_tx'][h] = [ts, price]
    return data, unlabelled


def rank(args, data, now):
    from pathlib import Path
    from runpy import run_path
    module = Path(globals().get('__file__', 'ops/best_sellers.py'))
    shared = run_path(str(module.with_name('price_demand.py') if module.name == 'best_sellers.py' else
                          Path('ops/price_demand.py')))
    try:
        observed = max(shared['timestamp'](data.get(key)) for key in ('stock_as_of', 'ledger_as_of'))
        shared['require_fresh'](dict(data, as_of=observed), now)
        stale = bool(data.get('trimmed') or data.get('stock_pending'))
    except ValueError:
        stale = True
    if not isinstance(args, dict) or set(args) - ARGS:
        raise ValueError('unknown input; allowed: ' + ', '.join(sorted(ARGS)))
    top, horizon, min_tx = args.get('top', 5), args.get('horizon_hours', 3), args.get('min_tx', 1)
    kind, own = args.get('kind', 'all'), args.get('own_stock') or {}
    if type(top) is not int or not 1 <= top <= 20 or type(min_tx) is not int or min_tx < 1:
        raise ValueError('top is 1..20 and min_tx >= 1 (integers)')
    if type(horizon) not in (int, float) or not 0 < horizon <= 48:
        raise ValueError('horizon_hours is in (0, 48]')
    if kind not in ('all', 'food', 'goods') or not isinstance(own, dict):
        raise ValueError('kind is all|food|goods, own_stock is {sku: units}')
    per = {}
    for ts, sku, amount, shop, buyer, check in data['sales']:
        if check < 0 or ts > now:
            continue
        per.setdefault(sku, []).append((ts, amount, shop, buyer, check))
    items = []
    for sku, rows in per.items():
        name, k, listed = (data['items'].get(sku) or [sku, None, None])[:3]
        if kind != 'all' and k != kind or len(rows) < min_tx:
            continue
        v, wide = shared['velocity']((row[0] for row in rows), now)
        prices = [r[1] for r in rows]
        med = _median(prices)
        buyers = {}
        for r in rows:
            buyers[r[3]] = buyers.get(r[3], 0) + 1
        _, sellers, have = (0, 0, 0) if stale else shared['stock_counts'](data, sku)
        have = max(have, own.get(sku, 0)) if type(own.get(sku, 0)) is int else have
        confirmed = 'stock_unconfirmed' in data and sku not in data['stock_unconfirmed']
        # 11/27 (대표님 11/26 "가드 넣지 말라고"): a reference number only. Stale or unconfirmed stock no longer forces 0;
        # the shares then use only what we know (own stock), and buy_qty_basis says so.
        qty = shared['buy_limit'](v, wide, sellers, have, horizon)
        items.append(dict(sku=sku, name=name, kind=k, per_hour=round(v, 2), tx=len(rows),
                          tx_on_chain=sum(1 for r in rows if r[4] > 0), tx_6h=wide, shops=len({r[2] for r in rows}),
                          tx_sku_guessed=sum(1 for r in rows if r[4] == CHAIN_ONLY),
                          repeat_buyers=sum(1 for n in buyers.values() if n > 1),
                          price=[min(prices), round(med), max(prices)],
                          price_vs_list=round(med / listed, 2) if listed else None, buy_qty=qty,
                          buy_qty_basis='stock' if confirmed and not stale else 'no_stock_data',
                          price_ceiling_args=dict(sku=sku, sell=round(med), qty=max(qty, 1), hold_hours=horizon)))
    items.sort(key=lambda x: (-x['per_hour'], -x['tx'], x['sku']))
    age = int((now - data['as_of']) // 60)
    return dict(as_of=data['as_of'], data_age_min=age, stale=stale, window_h=data['keep_hours'],
                items=items[:top],
                note=('실제 결제된 판매만 집계. buy_qty는 판매 속도로 본 참고값이고, 0이거나 stale이어도 매입을 막는 이유가 '
                      '아니다(매입은 buy_food_lot이 손익분기·품목당 4개로 확인). price_ceiling_args.qty는 계산용으로 최소 1.'))
# END FORGE KERNEL


def build_data(ledger, catalog, now, keep_hours=24, txs=None, chain=None):
    """Compact paid-sale rows for the last keep_hours: [ts, sku, amount, shop index, buyer id, chain check].
    chain check: 1 = on-chain transfer equals amount - (1% + 210), -1 = it does not, 0 = not checked yet,
    2 = found on chain only (chain_scan.py), SKU guessed from the price.
    chain (optional): {'book': addressbook, 'upto_block': n, 'settles': [[ts, shop address, price, buyer, tx]],
    'bands': {sku: [lo, hi]}}. Its sales the ledger does not have are added, so the build keeps working when the
    ledger stops answering. The forged tool gets data['chain'] to read the chain itself."""
    cat = {c['sku']: c for c in catalog if isinstance(c, dict) and 'sku' in c}
    url_name, shops, sales, items, last = {}, [], [], {}, 0
    for e in ledger:
        last = max(last, e.get('seq', 0))
        d = e.get('data') or {}
        if e['kind'] == 'join' and d.get('role') == 'shop' and d.get('url'):
            url_name[norm_url(d['url'])] = d['name']
        if e['kind'] != 'settle' or not d.get('tx_hash') or now - e['ts'] > keep_hours * 3600:
            continue
        sku = d.get('sku') or d.get('item')
        shop = url_name.get(norm_url(d['shop']), norm_url(d['shop']))
        if shop not in shops:
            shops.append(shop)
        check = 0
        t = (txs or {}).get(d['tx_hash'])
        if isinstance(t, dict) and isinstance(t.get('credits'), int):
            check = 1 if abs(t['credits'] - (d['amount'] - d['amount'] // 100 - 210)) <= 1 else -1
        sales.append([e['ts'], sku, d['amount'], shops.index(shop), _bid(d.get('buyer')), check])
        c = cat.get(sku, {})
        items[sku] = [c.get('name') or d.get('name') or sku, c.get('kind'), c.get('list_price')]
    tail = [e for e in ledger if e.get('seq') == last]
    data = dict(as_of=now, keep_hours=keep_hours, last_seq=last, last_hash=tail[-1].get('hash') if tail else None,
                shops=shops, items=items, sales=sales, url_names=url_name, source='ledger')
    if not chain:
        return data
    bands = dict(chain.get('bands') or {})
    for e in ledger:                         # every paid price ever seen, not just the window: the chain path
        d = e.get('data') or {}              # may run long after the ledger closed
        sku = d.get('sku') or d.get('item')
        if e['kind'] == 'settle' and d.get('tx_hash') and isinstance(d.get('amount'), int) and sku:
            lo, hi = bands.get(sku, [d['amount'], d['amount']])
            bands[sku] = [min(lo, d['amount']), max(hi, d['amount'])]
    book = chain.get('book') or {}
    addr = {a.lower(): s.get('name') for a, s in (book.get('shops') or {}).items() if s.get('name')}
    buyers = {a.lower(): c.get('name') for a, c in (book.get('customers') or {}).items() if c.get('name')}
    newest = max((e['ts'] for e in ledger), default=0)
    data['chain'] = dict(market=str((book.get('chain') or {}).get('market_address', '')).lower(), shops=addr,
                         next=int(chain.get('upto_block', 0)) + 1, bands=bands, buyers=buyers, ledger_ts=newest)
    known = {str((e.get('data') or {}).get('tx_hash', '')).lower() for e in ledger if e['kind'] == 'settle'}
    # only sales after the ledger's newest row: older chain-only payments are ones the market refused (or retries)
    fresh = [[ts, addr.get(str(to).lower(), str(to).lower()), p, b, h] for ts, to, p, b, h in chain.get('settles') or []
             if str(h).lower() not in known and ts > newest and now - ts <= keep_hours * 3600]
    data, unlabelled = merge_chain(data, fresh)
    for sku in {r[1] for r in data['sales'] if r[5] == CHAIN_ONLY}:
        c = cat.get(sku, {})
        data['items'].setdefault(sku, [c.get('name') or sku, c.get('kind'), c.get('list_price')])
    data['chain_only'], data['unlabelled'] = len(fresh), unlabelled
    if fresh:
        data['source'] = 'ledger+chain' if any(r[5] != CHAIN_ONLY for r in data['sales']) or last else 'chain'
    if not ledger:
        data['source'] = 'chain'
    return data


DATA_PATH = Path(__file__).with_name('best_sellers_data.json')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--data-dir')
    ap.add_argument('--output', default=str(DATA_PATH))
    ap.add_argument('--keep-hours', type=float, default=24)
    ap.add_argument('--top', type=int, default=5)
    ap.add_argument('--horizon-hours', type=float, default=3)
    ap.add_argument('--kind', default='all')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    if a.build:
        if not a.data_dir:
            ap.error('--build needs --data-dir')
        d = Path(a.data_dir)
        load = lambda n, dflt: json.loads((d / n).read_text(encoding='utf-8')) if (d / n).exists() else dflt
        from runpy import run_path
        merge = run_path(str(Path(__file__).with_name('price_demand_cache.py')))['merge_observations']
        ledger, chain = load('ledger.json', []), load('chain/rebuild.json', None)   # chain_scan.py output, if any
        snaps = sorted((d / 'snapshots').glob('*.json'))
        data = None
        if ledger and snaps:
            try:
                snapshot = json.loads(snaps[-1].read_text(encoding='utf-8'))
                observed = max(snapshot['ts'], ledger[-1]['ts'])
                data = merge(build_data(ledger, load('catalog.json', []), observed, a.keep_hours,
                                        load('txs.json', {}), chain), ledger, snapshot)
            except (ValueError, KeyError, TypeError) as e:  # e.g. the ledger stopped while snapshots still arrive
                print(f'warning: stock not merged ({e}); built without public stock', file=sys.stderr)
            else:
                # a chain-only sale after the shop snapshot took a unit the snapshot still counts: that SKU's stock
                # is not confirmed (buy_qty 0 here, price_demand refuses it) until the next snapshot
                late = {r[1] for r in data['sales'] if r[5] == CHAIN_ONLY and r[0] > data['stock_as_of']}
                data['stock_unconfirmed'] = sorted(set(data['stock_unconfirmed']) | late)
        else:
            print('warning: no ledger rows or no snapshots/*.json; built without public stock', file=sys.stderr)
        if data is None:    # paid sales (ledger and chain) only, no stock observation, so every buy_qty is 0
            data = build_data(ledger, load('catalog.json', []), time.time(), a.keep_hours, load('txs.json', {}),
                              chain)
        out = Path(a.output)
        tmp = out.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        tmp.replace(out)
        print(f'{out} sales={len(data["sales"])} last_seq={data["last_seq"]} source={data["source"]} '
              f'chain_only={data.get("chain_only", 0)} unlabelled={data.get("unlabelled", 0)}')
        return
    data = json.loads(Path(a.output).read_text(encoding='utf-8'))
    res = rank(dict(top=a.top, horizon_hours=a.horizon_hours, kind=a.kind), data, time.time())
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
        return
    print(f"as_of {time.strftime('%m-%d %H:%M', time.localtime(res['as_of']))} age {res['data_age_min']}m window {res['window_h']}h")
    print('SKU\t품목\t시간당\t근거tx(체인확인)\t6h\t가게\t재구매\t실거래가(최저/중앙/최고)\t정가대비\t권장수량')
    for i in res['items']:
        print(i['sku'], i['name'][:24], i['per_hour'], f"{i['tx']}({i['tx_on_chain']})", i['tx_6h'], i['shops'],
              i['repeat_buyers'], '/'.join(map(str, i['price'])), i['price_vs_list'], i['buy_qty'], sep='\t')


if __name__ == '__main__':
    main()
