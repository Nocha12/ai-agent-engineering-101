"""Build the public Round 4 price sample from two local JSON files, without network I/O.

python3 -I ops/price_ceiling_cache.py --data-dir ~/work/ax-round4-analytics/data
To undo an update, replace the output with its adjacent .bak file.
The backup is published first; on an interrupted update it can equal the unchanged output.
The end time is the latest observed Round 4 event, not a forecast or the round deadline.
"""

import argparse
import json
import math
import os
import re
import statistics
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


FIRST_HASH = '63363a384a5fc9f697413597cfd90874bbeaccf8fa69da9c96ceaf9fc442995b'
ESSENTIAL_LEAVES = ('곡물', '계란', '우유/두유', '국수/파스타/누들', '신선과일(부패주의)', '신선야채(부패주의)')
KINDS = ('food', 'goods')


def require(valid, message):
    if not valid:
        raise ValueError(message)


def text(value):
    require(isinstance(value, str) and value.strip(), 'expected nonempty text')
    return value


def integer(value, minimum=0):
    require(type(value) is int and value >= minimum, 'invalid count or price')
    return value


def essential(category, name):
    leaf = category.rsplit('>', 1)[-1]
    return (leaf in ESSENTIAL_LEAVES or (leaf == '즉석조리식품' and '라면' in name)
            or (leaf == '주스/코코아/생수' and '생수' in name))


def kst(timestamp):
    return datetime.fromtimestamp(timestamp, timezone(timedelta(hours=9))).isoformat(timespec='milliseconds')


def build(catalog, ledger):
    require(isinstance(catalog, list) and catalog, 'empty or invalid catalog')
    items = {}
    for product in catalog:
        sku, name, category = (text(product[key]) for key in ('sku', 'name', 'category'))
        kind, shelf = product['kind'], product['shelf_hours']
        require(sku not in items and kind in KINDS, 'duplicate SKU or unknown kind')
        if kind == 'food':
            integer(shelf, 1)
        else:
            require(shelf is None, 'goods must not have a food shelf life')
        items[sku] = [name, kind, integer(product['list_price'], 1), shelf,
                      kind == 'food' and essential(category, name), None, None, 0, 0]

    require(isinstance(ledger, list) and ledger, 'empty or invalid ledger')
    require((ledger[0]['hash'], ledger[0]['ts']) == (FIRST_HASH, 1794929865.983), 'wrong public ledger origin')
    previous_hash, previous_ts = '0' * 64, 0
    start = end = None
    last_seq = batches = 0
    units, sold = {}, set()
    costs, counts, prices = defaultdict(list), Counter(), defaultdict(list)
    for seq, event in enumerate(ledger, 1):
        timestamp, digest = event['ts'], text(event['hash'])
        require(integer(event['seq'], 1) == seq and event['prev'] == previous_hash and
                re.fullmatch('[0-9a-f]{64}', digest), f'broken ledger sequence at {seq}')
        require(type(timestamp) in (int, float) and math.isfinite(timestamp) and timestamp >= previous_ts,
                f'invalid ledger time at {seq}')
        previous_hash, previous_ts = digest, timestamp
        kind = text(event['kind'])
        if integer(event['round']) != 4 or kind == 'round':
            continue
        if start is None:
            start = timestamp
        end = timestamp
        last_seq = seq
        if kind not in ('supply', 'settle'):
            continue
        data = event['data']
        sku = text(data['sku'])
        require(sku in items, f'unknown ledger SKU at {seq}')
        if kind == 'supply':
            qty, batch = integer(data['qty'], 1), data['units']
            require(isinstance(batch, list) and len(batch) == qty, f'invalid supply quantity at {seq}')
            for unit in batch:
                require(text(unit) not in units, f'duplicate supplied unit at {seq}')
                units[unit] = sku
            costs[sku].extend([integer(data['total']) / qty] * qty)
            counts[sku] += qty
            batches += 1
        else:
            unit = text(data['unit'])
            require(units.get(unit) == sku and unit not in sold, f'unmatched settlement at {seq}')
            sold.add(unit)
            prices[sku].append(integer(data['amount']))

    require(start is not None, 'no Round 4 activity')
    rates = {kind: [0, 0] for kind in KINDS}
    for sku, row in items.items():
        sales, count = len(prices[sku]), counts[sku]
        row[5:] = [statistics.median(prices[sku]) if sales else None,
                   statistics.median(costs[sku]) if count else None, sales, count]
        rates[row[1]][0] += sales
        rates[row[1]][1] += count
    return {'schema_version': 2, 'items': items, 'rates': rates,
            'sample': {'round': 4, 'start': kst(start), 'end': kst(end), 'last_seq': last_seq,
                       'observed_minutes': round((end - start) / 60, 3),
                       'supply_batches': batches},
            'fallback': {'sell_list_multiplier': 0.60, 'cost_list_multiplier': 0.54},
            'essential_rule': {'food_category_leaves': ESSENTIAL_LEAVES,
                               'food_leaf_and_name_contains': {'즉석조리식품': '라면', '주스/코코아/생수': '생수'}}}


def replace_bytes(path, content):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary.close()
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True, help='local public catalog.json and ledger.json directory')
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('price_ceiling_data.json'))
    args = parser.parse_args()
    try:
        paths = [args.data_dir / name for name in ('catalog.json', 'ledger.json')]
        backup = args.output.with_name(args.output.name + '.bak')
        require(not args.data_dir.is_symlink() and all(path.is_file() and not path.is_symlink() for path in paths),
                'expected regular local public input files')
        require(not {path.resolve() for path in paths} & {args.output.resolve(), backup.resolve()},
                'output overlaps input')
        require(not args.output.is_symlink() and not backup.is_symlink(), 'symlink output not allowed')
        cache = build(*(json.loads(path.read_text(encoding='utf-8')) for path in paths))
        content = (json.dumps(cache, ensure_ascii=False, allow_nan=False, sort_keys=True,
                              separators=(',', ':')) + '\n').encode('utf-8')
        previous = args.output.read_bytes() if args.output.exists() else None
        if previous != content:
            if previous is not None:
                old_sample = json.loads(previous)['sample']
                new_sample = cache['sample']
                require(new_sample['start'] == old_sample['start'] and new_sample['end'] >= old_sample['end']
                        and new_sample['last_seq'] >= old_sample['last_seq'], 'public observation regressed')
                replace_bytes(backup, previous)
            replace_bytes(args.output, content)
        print(json.dumps({'items': len(cache['items']), 'rates': cache['rates'], 'sample': cache['sample']}))
    except (OSError, KeyError, TypeError, ValueError, OverflowError) as exc:
        parser.exit(1, f'cache not updated: {exc}\n')


if __name__ == '__main__':
    main()
