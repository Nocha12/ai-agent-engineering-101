"""Publish the peer-built paid-sale sample and public stock as one offline cache.

python3 -I ops/price_demand_cache.py --data-dir ~/work/ax-round4-analytics/data
The peer's best_sellers.py --build must finish before this command.
"""

import argparse
import hashlib
import json
import math
import os
import tempfile
from collections import Counter
from pathlib import Path


def text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('expected nonempty public identifier')
    return value


def positive_number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError('invalid public observation time or window')
    return value


def verify_public_chain(ledger):
    previous = '0' * 64
    for seq, event in enumerate(ledger, 1):
        if not isinstance(event, dict) or event.get('prev') != previous:
            raise ValueError(f'broken public ledger link at {seq}')
        entry = {key: value for key, value in event.items() if key != 'hash'}
        encoded = json.dumps(entry, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        previous = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
        if event.get('hash') != previous:
            raise ValueError(f'invalid public ledger hash at {seq}')


def removals(kind, data, live, sealed=0):
    """sealed: sealed sales so far. Each took one unit we cannot name, so some shop holds one unit fewer than we count."""
    if kind == 'settle':
        if data.get('sealed') and 'unit' not in data and 'sku' not in data:
            return []                            # a sealed sale (Patch 4.1) names no unit: the next shop snapshot corrects the count
        return [(text(data.get('unit')), None, text(data.get('sku')))]
    owner = text(data.get('shop'))
    if kind == 'bankrupt':
        seized = [(unit, owner, key[1]) for unit, (key, cohort) in live.items() if key[0] == owner]
        if type(data.get('seized_units')) is not int or not max(0, len(seized) - sealed) <= data['seized_units'] <= len(seized):
            raise ValueError('seized unit count does not match public supply')
        return seized
    units, skus = data.get('units'), data.get('skus')
    if not isinstance(units, list) or not units or not isinstance(skus, list) or len(units) != len(skus):
        raise ValueError('invalid expired unit batch')
    return [(text(unit), owner, text(sku)) for unit, sku in zip(units, skus)]


def ledger_stock(ledger, stock_time, observed):
    """Cap pre-snapshot inventory only; later sales consume their actual supply cohort."""
    live, seen, sealed = {}, set(), 0
    earlier, later = Counter(), Counter()
    previous_time = 0
    for seq, event in enumerate(ledger, 1):
        if (not isinstance(event, dict) or type(event.get('seq')) is not int or event['seq'] != seq or
                type(event.get('round')) is not int or not 0 <= event['round'] <= 4):
            raise ValueError(f'incomplete public ledger at {seq}')
        when = positive_number(event.get('ts'))
        if when < previous_time:
            raise ValueError(f'out-of-order public ledger at {seq}')
        previous_time = when
        kind = text(event.get('kind'))
        if event['round'] != 4 or kind in ('round', 'join', 'order', 'grant', 'storage', 'broken', 'result', 'review'):
            continue
        if kind not in ('supply', 'settle', 'expire', 'bankrupt'):
            raise ValueError(f'unknown public ledger event at {seq}')
        data = event.get('data')
        if not isinstance(data, dict):
            raise ValueError(f'missing stock event data at {seq}')
        if kind == 'supply':
            key = (text(data.get('shop')), text(data.get('sku')))
            cohort = earlier if when <= stock_time else later
            batch, qty = data.get('units'), data.get('qty')
            if not isinstance(batch, list) or type(qty) is not int or qty <= 0 or qty != len(batch):
                raise ValueError(f'invalid supply quantity at {seq}')
            for unit in batch:
                if text(unit) in seen:
                    raise ValueError(f'duplicate supplied unit at {seq}')
                seen.add(unit)
                live[unit] = (key, cohort)
                cohort[key] += 1
            continue
        gone = removals(kind, data, live, sealed)
        sealed += kind == 'settle' and not gone
        for unit, owner, sku in gone:
            if unit not in live:
                raise ValueError(f'unmatched or duplicate unit removal at {seq}')
            key, cohort = live.pop(unit)
            if key[1] != sku or (owner is not None and key[0] != owner):
                raise ValueError(f'unit identity mismatch at {seq}')
            if when > stock_time and cohort is earlier:
                if kind == 'expire' and 0 < observed[key] < earlier[key]:
                    raise ValueError('ambiguous delayed expiry; refresh public shops')
                observed[key] = max(0, observed[key] - 1)
            cohort[key] -= 1
    unconfirmed = sorted({sku for (shop, sku), count in observed.items() if count > earlier[shop, sku]})
    return {key: observed[key] + later[key] for key in sorted(observed.keys() | later.keys())}, unconfirmed


def merge_observations(paid, ledger, snapshot):
    if (not isinstance(ledger, list) or not ledger or not isinstance(ledger[-1], dict) or
            type(paid.get('last_seq')) is not int or paid['last_seq'] != ledger[-1].get('seq') or
            ledger[-1].get('round') != 4 or snapshot['shops']['round'] != 4):
        raise ValueError('paid-sale cache and Round 4 ledger/snapshot do not match')
    shops = snapshot['shops']['shops']
    public_stock = Counter()
    names = set()
    for shop in shops:
        name = text(shop['name'])
        if name in names or not isinstance(shop['stock'], dict):
            raise ValueError('duplicate or incomplete public shop record')
        names.add(name)
        for sku, units in shop['stock'].items():
            if type(units) is not int or units < 0:
                raise ValueError('invalid public SKU stock')
            public_stock[name, text(sku)] = units
    if 'nocha12' not in names:
        raise ValueError('own shop missing from public inventory')
    stock_time, ledger_time = positive_number(snapshot.get('ts')), positive_number(ledger[-1].get('ts'))
    observed = max(stock_time, ledger_time)
    if (abs(stock_time - ledger_time) > 90 * 60 or
            positive_number(paid.get('as_of')) - positive_number(paid.get('keep_hours')) * 3600 > observed - 6 * 3600):
        raise ValueError('observations do not cover the common demand window')
    stock = {}
    holdings, unconfirmed = ledger_stock(ledger, stock_time, public_stock)
    for (name, sku), units in holdings.items():
        counts = stock.setdefault(sku, [0, 0, 0])
        if name == 'nocha12':
            counts[2] += units
        else:
            counts[0] += units
            counts[1] += units > 0
    return dict(paid, as_of=observed, ledger_as_of=ledger_time, stock_as_of=stock_time, sku_stock=stock, stock_unconfirmed=unconfirmed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('best_sellers_data.json'))
    args = parser.parse_args()
    root = args.data_dir
    files = [root / 'best_sellers_data.json', root / 'ledger.json', max((root / 'snapshots').glob('*.json'))]
    if args.output.resolve() in {path.resolve() for path in files}:
        parser.error('output must not overwrite a public input')
    paid, ledger, snapshot = (json.loads(path.read_text(encoding='utf-8')) for path in files)
    verify_public_chain(ledger)
    if not isinstance(paid.get('sales'), list):
        raise ValueError('missing public paid-sale sample')
    data = merge_observations(paid, ledger, snapshot)
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
