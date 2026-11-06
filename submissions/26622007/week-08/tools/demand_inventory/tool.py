"""Connect explicit forum requests to catalog and own live stock; never send messages."""
import json
import os
import sys
import time


def summarize(posts, catalog, inventory, now, hours=6, limit=10):
    out = []
    for p in sorted(posts, key=lambda p: p.get('ts', 0), reverse=True):
        text = p.get('text', '')
        if p.get('role') != 'buyer' or not now-hours*3600 <= p.get('ts', 0) <= now or ' 구합니다' not in text:
            continue
        category = text.split(' 구합니다', 1)[0].strip()
        matches = {sku: item for sku, item in catalog.items()
                   if isinstance(item, dict) and item.get('category', '').split('>')[-1] == category}
        if not matches:
            continue
        units = [u for u in inventory.get('units', []) if u.get('sku') in matches
                 and (u.get('expires_at') is None or u['expires_at'] > now)]
        out.append({'post_id': p.get('id'), 'author': p.get('author'), 'ts': p.get('ts'),
                    'request': text, 'category': category, 'catalog_matches': len(matches),
                    'own_live_units': len(units), 'stock_skus': sorted({u['sku'] for u in units}),
                    'catalog_sample': [{'sku': s, 'list_price': v.get('list_price')} for s, v in list(matches.items())[:3]],
                    'caution': 'request is text, not an order; budget, shelf requirement, margin and available demand unverified'})
        if len(out) >= limit:
            break
    return {'requests': out, 'as_of': now, 'basis': 'exact leaf category; buyer requests; own unexpired inventory only'}


def main():
    args = json.load(sys.stdin); md = os.environ['MARKET_DIR']
    missing = []
    def read(name, default):
        try:
            with open(os.path.join(md, name), encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            missing.append(name)
            return default
    result = summarize(read('forum.json', []), read('catalog.json', {}), read('inventory.json', {}),
                       time.time(), hours=6, limit=max(1, min(10, int(args.get('limit', 5)))))
    result['missing_sources'] = missing
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
