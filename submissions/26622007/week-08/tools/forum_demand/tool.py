"""Unmet requests are leads, never purchase authority. No network or writes."""
import json
import os
import sys
import time
from collections import Counter

from profit_cohorts import read_json, read_rows


def search(posts, catalog, inventory, ledger, now, limit=10):
    counts, seen = Counter(), set()
    for row in ledger:
        seq = row.get('seq')
        if (row.get('k') == 'settle' and seq is not None and seq not in seen
                and now - 21600 <= row.get('ts', 0) <= now and row.get('honest') is not False):
            seen.add(seq)
            counts[row.get('sku')] += 1
    stock = Counter(u.get('sku') for u in inventory.get('units', []))
    out = []
    for post in posts if isinstance(posts, list) else []:
        text = post.get('text', '')
        if ('구합니다' not in text or post.get('role') != 'buyer'
                or not now - 21600 <= post.get('ts', 0) <= now):
            continue
        candidates = []
        for sku, product in catalog.items():
            leaf = product.get('category', '').split('>')[-1].strip()
            if leaf and leaf in text:
                candidates.append({'sku': sku, 'name': product.get('name'),
                                   'list_price': product.get('list_price'),
                                   'own_stock': stock[sku], 'paid_6h': counts[sku]})
        candidates.sort(key=lambda c: (c['own_stock'] > 0, c['paid_6h']), reverse=True)
        out.append({'post': post.get('id'), 'author': post.get('author'), 'text': text,
                    'score': post.get('score'), 'candidates': candidates[:3]})
    return {'leads': out[-max(1, min(20, int(limit))):],
            'basis': 'Exact category leaf match; paid counts deduplicated. Budget, acceptable SKU, '
                     'food lifetime and warranty still require order/guard validation.'}


def main():
    args = json.load(sys.stdin)
    md = os.environ['MARKET_DIR']
    print(json.dumps(search(read_json(os.path.join(md, 'forum.json'), []),
                            read_json(os.path.join(md, 'catalog.json'), {}),
                            read_json(os.path.join(md, 'inventory.json'), {}),
                            read_rows(os.path.join(md, 'ledger_rows.jsonl')),
                            time.time(), args.get('limit', 10)), ensure_ascii=False))


if __name__ == '__main__':
    main()
