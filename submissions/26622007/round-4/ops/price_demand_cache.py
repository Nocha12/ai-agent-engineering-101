"""Publish the peer-built paid-sale sample and public stock as one offline cache.

python3 -I ops/price_demand_cache.py --data-dir ~/work/ax-round4-analytics/data
The peer's best_sellers.py --build must finish before this command.
"""

import argparse
import json
import os
import tempfile
from pathlib import Path


def merge_observations(paid, ledger, snapshot):
    if (not ledger or type(paid.get('last_seq')) is not int or paid['last_seq'] != ledger[-1]['seq'] or
            ledger[-1]['round'] != 4 or snapshot['shops']['round'] != 4):
        raise ValueError('paid-sale cache and Round 4 ledger/snapshot do not match')
    shops = snapshot['shops']['shops']
    stock = {}
    names = set()
    for shop in shops:
        name = shop['name']
        if not name or name in names or not isinstance(shop['stock'], dict):
            raise ValueError('duplicate or incomplete public shop record')
        names.add(name)
        for sku, units in shop['stock'].items():
            if not sku or type(units) is not int or units < 0:
                raise ValueError('invalid public SKU stock')
            counts = stock.setdefault(sku, [0, 0, 0])
            if name == 'nocha12':
                counts[2] += units
            else:
                counts[0] += units
                counts[1] += units > 0
    if 'nocha12' not in names:
        raise ValueError('own shop missing from public inventory')
    stock_time, ledger_time = snapshot['ts'], ledger[-1]['ts']
    observed = max(stock_time, ledger_time)
    if (abs(stock_time - ledger_time) > 90 * 60 or
            paid['as_of'] - paid['keep_hours'] * 3600 > observed - 6 * 3600):
        raise ValueError('observations do not cover the common demand window')
    return dict(paid, as_of=observed, ledger_as_of=ledger_time, stock_as_of=stock_time, sku_stock=stock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('best_sellers_data.json'))
    args = parser.parse_args()
    root = args.data_dir
    files = [root / 'best_sellers_data.json', root / 'ledger.json', max((root / 'snapshots').glob('*.json'))]
    if args.output.resolve() in {path.resolve() for path in files}:
        parser.error('output must not overwrite a public input')
    data = merge_observations(*(json.loads(path.read_text(encoding='utf-8')) for path in files))
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=args.output.parent, delete=False) as tmp:
        try:
            json.dump(data, tmp, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
            tmp.write('\n')
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            os.replace(tmp.name, args.output)
        finally:
            Path(tmp.name).unlink(missing_ok=True)
    print(f'as_of={data["as_of"]} last_seq={data["last_seq"]} sales={len(data["sales"])} stock_skus={len(data["sku_stock"])}')


if __name__ == '__main__':
    main()
