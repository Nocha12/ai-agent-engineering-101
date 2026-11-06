"""Public paid-deal search with explicit own-payment provenance."""
import json
import os
import sys

import market_model
from tools.auto_market_scan.tool import search as public_search


def search(catalog, ledger, payments, args):
    public = public_search(catalog, ledger, args)
    out = {'catalog_count': public['matched_products'], 'catalog': public['catalog'],
           'paid_deal_count': public['matched_paid'], 'paid_deals': public['ledger']}
    if args.get('include_purchases'):
        # Apply filters before pagination; only our confirmed payments establish ownership.
        eligible = {p.get('ledger') for p in payments if p.get('paid') is True}
        own = public_search(catalog, [r for r in ledger if r.get('seq') in eligible], args)
        out['personal_history'] = own['ledger']
        out['personal_history_count'] = own['matched_paid']
    out['basis'] = 'deduplicated settle; catalog SKU/category intersection; personal rows require paid=true payment ledger id'
    return out


def main():
    args = json.load(sys.stdin)
    md = os.environ['MARKET_DIR']
    with open(os.path.join(md, 'catalog.json'), encoding='utf-8') as f:
        catalog = json.load(f)
    payments = []
    try:
        with open(os.path.join(md, 'payments.jsonl'), encoding='utf-8') as f:
            for line in f:
                try:
                    p = json.loads(line)
                    if isinstance(p, dict):
                        payments.append(p)
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    print(json.dumps(search(catalog, market_model.load_rows(md), payments, args), ensure_ascii=False))


if __name__ == '__main__':
    main()
