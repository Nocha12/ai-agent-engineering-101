"""Read the shared Round 4 best-sellers cache; no kit, wallet or network access."""

import json
from math import isfinite
from pathlib import Path


CACHE_PATH = Path(__file__).with_name('best_sellers_data.json')
MAX_AGE = 90 * 60


def timestamp(value):
    if type(value) not in (int, float) or not isfinite(value) or value <= 0:
        raise ValueError('invalid market timestamp')
    return value


def velocity(times, now):
    recent = wide = 0
    for observed in times:
        age = now - timestamp(observed)
        if 0 <= age <= 6 * 3600:
            wide += 1
            recent += age <= 2 * 3600
    return 0.6 * recent / 2 + 0.4 * wide / 6, wide


def stock_counts(data, sku):
    stock = data.get('sku_stock')
    if not isinstance(stock, dict):
        raise ValueError('missing public stock observation')
    counts = stock.get(sku, [0, 0, 0])
    if (not isinstance(counts, list) or len(counts) != 3 or
            not all(type(value) is int and value >= 0 for value in counts) or
            counts[1] > counts[0] or bool(counts[0]) != bool(counts[1])):
        raise ValueError('invalid public stock observation')
    return tuple(counts)


def buy_limit(speed, count, shops, own, horizon):
    want = int(speed * horizon / (shops + 1) + 0.5)
    return min(4, max(0, want - own)) if count >= 2 else 0


def load_cache():
    try:
        data = json.loads(CACHE_PATH.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('invalid public market cache')
        return data
    except (OSError, ValueError) as exc:
        raise ValueError('shared best-sellers cache unavailable') from exc


def market_as_of():
    """Replay at this Unix timestamp; the cache build time is not an observation."""
    return timestamp(load_cache().get('as_of'))


def require_fresh(data, now):
    observed = timestamp(data.get('as_of'))
    clocks = [timestamp(data.get(key)) for key in ('stock_as_of', 'ledger_as_of')]
    hours = data.get('keep_hours')
    if (observed != max(clocks) or not observed <= timestamp(now) <= min(clocks) + MAX_AGE or
            type(hours) not in (int, float) or not isfinite(hours) or hours < 6):
        raise ValueError('stale, future or incomplete market observation')


def signals_from(data, sku, now):
    try:
        require_fresh(data, now)
        if sku in data['stock_unconfirmed']:
            raise ValueError('stock observation ahead of ledger')
        if not isinstance(sku, str) or not sku or not isinstance(data['sales'], list):
            raise ValueError('invalid SKU or paid-sale sample')
        times = (row[0] for row in data['sales'] if row[1] == sku and row[5] >= 0)
        return (*velocity(times, now), *stock_counts(data, sku))
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError('invalid or incomplete shared best-sellers cache') from exc


def signals(sku, now):
    return signals_from(load_cache(), sku, now)
