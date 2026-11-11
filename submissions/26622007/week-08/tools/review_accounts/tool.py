"""Read-only review diagnostics joined to our paid orders; never a board estimate."""
import json
import math
import os
import sys
from pathlib import Path


def summarize(reviews, sales, mine, limit=10):
    if reviews is None or sales is None or mine is None:
        return {'available': False, 'reason': 'missing or malformed local evidence'}
    orders = {r['order']: r for r in sales if r.get('k') == 'settle'
              and str(r.get('seq')) in mine and r.get('order')}
    groups, seen = {}, set()
    skipped = {'unverified': 0, 'unlinked': 0, 'invalid': 0}
    for r in reviews:
        seq, stars, buyer = r.get('seq'), r.get('stars'), r.get('buyer')
        if type(seq) is not int or seq in seen:
            continue
        seen.add(seq)
        if r.get('verified') is not True:
            skipped['unverified'] += 1
            continue
        sale = orders.get(r.get('order'))
        if sale is None or sale.get('buyer') != buyer:
            skipped['unlinked'] += 1
            continue
        if not isinstance(buyer, str) or not buyer or type(stars) not in (int, float) or not math.isfinite(stars) or not 1 <= stars <= 5:
            skipped['invalid'] += 1
            continue
        g = groups.setdefault(buyer, {'buyer': buyer, 'reviews': 0, 'sum': 0, 'examples': []})
        g['reviews'] += 1
        g['sum'] += stars
        if len(g['examples']) < 2:
            g['examples'].append({'review_seq': seq, 'paid_seq': sale['seq'], 'sku': sale.get('sku'), 'stars': stars})
    values = []
    for g in groups.values():
        g['mean'] = g.pop('sum') / g['reviews']
        values.append(g)
    values.sort(key=lambda g: (g['mean'], -g['reviews'], g['buyer']))
    return {'available': True, 'reviewer_names': len(values),
            'linked_verified_reviews': sum(g['reviews'] for g in values),
            'name_mean_proxy': sum(g['mean'] for g in values) / len(values) if values else None,
            'reviewers': values[:max(1, min(10, int(limit)))], 'skipped': skipped,
            'basis': 'partial local review cache; grouped by buyer name, NOT GitHub account; '
                     'not stars_rank or a causal price signal; join verified reviews to own paid orders; '
                     'do not change owner refusal settings or reward reviews'}


def read_rows(path):
    try:
        return [json.loads(s) for s in path.read_text().splitlines() if s.strip()]
    except (OSError, ValueError):
        return None


def main():
    args = json.load(sys.stdin)
    md = Path(os.environ['MARKET_DIR'])
    try:
        mine = json.loads((md / 'loss_state.json').read_text()).get('mine')
    except (OSError, ValueError):
        mine = None
    print(json.dumps(summarize(read_rows(md / 'ledger_reviews.jsonl'),
                               read_rows(md / 'ledger_rows.jsonl'), mine, args.get('limit', 10)), ensure_ascii=False))


if __name__ == '__main__':
    main()
