"""Round 4 best sellers from money that actually moved (read-only; no wallet, token or operating state).

Input is the same public data directory price_ceiling's cache reads (~/work/ax-round4-analytics/data:
ledger.json from GET /api/ledger, catalog.json, txs.json with the chain check of each settle). Only settle rows with
a tx_hash count, and a row whose on-chain transfer does not match price - (1% + 210) is dropped.

  python3 -B ops/best_sellers.py --build --data-dir DIR [--output ops/best_sellers_data.json]
  python3 -B ops/best_sellers.py [--top 5] [--horizon-hours 3] [--kind food] [--json]
  python3 -B ops/best_sellers_forge.py > /tmp/best-sellers-move.json      # the v5.1 forge_tool move
"""

import argparse
import json
import time
from pathlib import Path


# BEGIN FORGE KERNEL
RECENT_H, WIDE_H, W_RECENT = 2, 6, 0.6     # velocity: 0.6 x (sales/h in the last 2 h) + 0.4 x (sales/h in 6 h)
STALE_MIN = 90
QTY_CAP = 4                                # per SKU per restock, as the 20:07 analysis found safe
MIN_EVIDENCE = 2                           # paid sales in the last 6 h before any buy is suggested
ARGS = {'top', 'horizon_hours', 'own_stock', 'kind', 'min_tx'}


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
        if e.get('kind') == 'join' and d.get('role') == 'shop' and d.get('url'):
            data['url_names'][norm_url(d['url'])] = str(d.get('name'))
        if e.get('kind') != 'settle' or not d.get('tx_hash') or not isinstance(d.get('amount'), (int, float)):
            continue
        sku = d.get('sku') or d.get('item')
        if sku not in data['items']:
            data['items'][sku] = [str(d.get('name') or sku)[:40], None, None]
        shop = data['url_names'].get(norm_url(d.get('shop')), norm_url(d.get('shop')))
        if shop not in data['shops']:
            data['shops'].append(shop)
        data['sales'].append([e.get('ts', now), sku, d['amount'], data['shops'].index(shop), _bid(d.get('buyer')), 0])
    data['last_seq'] = last
    return data


def rank(args, data, now):
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
        recent = sum(1 for r in rows if now - r[0] <= RECENT_H * 3600)
        wide = sum(1 for r in rows if now - r[0] <= WIDE_H * 3600)
        v = W_RECENT * recent / RECENT_H + (1 - W_RECENT) * wide / WIDE_H
        prices = [r[1] for r in rows]
        med = _median(prices)
        buyers = {}
        for r in rows:
            buyers[r[3]] = buyers.get(r[3], 0) + 1
        sellers = len({r[2] for r in rows if now - r[0] <= WIDE_H * 3600}) or 1
        have = own.get(sku, 0) if type(own.get(sku, 0)) is int else 0
        want = int(v * horizon / (sellers + 1) + 0.5)
        qty = min(QTY_CAP, max(0, want - max(0, have))) if wide >= MIN_EVIDENCE else 0
        items.append(dict(sku=sku, name=name, kind=k, per_hour=round(v, 2), tx=len(rows),
                          tx_on_chain=sum(1 for r in rows if r[4] > 0), tx_6h=wide, shops=len({r[2] for r in rows}),
                          repeat_buyers=sum(1 for n in buyers.values() if n > 1),
                          price=[min(prices), round(med), max(prices)],
                          price_vs_list=round(med / listed, 2) if listed else None, buy_qty=qty,
                          price_ceiling_args=dict(sku=sku, sell=round(med), qty=max(qty, 1))))
    items.sort(key=lambda x: (-x['per_hour'], -x['tx'], x['sku']))
    age = int((now - data['as_of']) // 60)
    return dict(as_of=int(data['as_of']), data_age_min=age, stale=age > STALE_MIN, window_h=data['keep_hours'],
                items=items[:top],
                note=('실제 결제된 판매만 집계. buy_qty는 권장 상한(0이면 사지 않음)이며 price_ceiling verdict가 buy일 '
                      '때만 매입. price_ceiling_args.qty는 계산용으로 최소 1.'))
# END FORGE KERNEL


def build_data(ledger, catalog, now, keep_hours=24, txs=None):
    """Compact paid-sale rows for the last keep_hours: [ts, sku, amount, shop index, buyer id, chain check].
    chain check: 1 = on-chain transfer equals amount - (1% + 210), -1 = it does not, 0 = not checked yet."""
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
    return dict(as_of=now, keep_hours=keep_hours, last_seq=last, shops=shops, items=items, sales=sales,
                url_names={u: n for u, n in url_name.items() if n in shops})


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
        data = build_data(load('ledger.json', []), load('catalog.json', []), time.time(), a.keep_hours,
                          load('txs.json', {}))
        out = Path(a.output)
        tmp = out.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        tmp.replace(out)
        print(f'{out} sales={len(data["sales"])} last_seq={data["last_seq"]}')
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
