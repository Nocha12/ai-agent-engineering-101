"""Catalog and paid-deal lookup with independently matched forum context."""
import json
import os
import sys

import market_model
from tools.auto_market_scan.tool import search


def digest(catalog, ledger, forum, args):
    found = search(catalog, ledger, args)
    limit = max(1, min(100, int(args.get('limit', 20))))
    query = str(args.get('query', '')).strip().lower()
    category = str(args.get('category', '')).strip().lower()
    posts = forum if isinstance(forum, list) else forum.get('posts', []) if isinstance(forum, dict) else []
    # Forum posts rarely have a category field: match the category or a SKU
    # from the complete catalog intersection in their untrusted text.
    products = ([dict(v, sku=k) for k, v in catalog.items() if isinstance(v, dict)]
                if isinstance(catalog, dict) else catalog)
    skus = {str(p.get('sku', '')).lower() for p in products if isinstance(p, dict)
            and (not query or query in json.dumps(p, ensure_ascii=False).lower())
            and (not category or category in str(p.get('category', '')).lower())}
    skus.discard('')
    def matches(post):
        t = json.dumps(post, ensure_ascii=False).lower()
        return (not query or query in t or any(s in t for s in skus)) and (not category or category in t or any(s in t for s in skus))
    posts = [p for p in posts if isinstance(p, dict) and matches(p)]
    posts.sort(key=lambda p: p.get('ts', 0), reverse=True)
    return {'catalog_count': found['matched_products'], 'catalog': found['catalog'],
            'paid_deal_count': found['matched_paid'], 'recent_paid_deals': found['ledger'],
            'forum_count': len(posts), 'recent_forum_posts': posts[:limit]}


def main():
    args = json.load(sys.stdin)
    md = os.environ['MARKET_DIR']
    with open(os.path.join(md, 'catalog.json'), encoding='utf-8') as f:
        catalog = json.load(f)
    try:
        with open(os.path.join(md, 'forum.json'), encoding='utf-8') as f:
            forum = json.load(f)
    except (OSError, ValueError):
        forum = []
    print(json.dumps(digest(catalog, market_model.load_rows(md), forum, args), ensure_ascii=False))


if __name__ == '__main__':
    main()
