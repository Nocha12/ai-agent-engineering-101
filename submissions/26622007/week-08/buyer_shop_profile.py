import fcntl
import json
import os
import time

SWITCH = 'buyer_shop_profile.json'
CACHE = 'buyer_shop_profile_cache.json'


def enabled(md, env=None):
    env = os.environ if env is None else env
    if str(env.get('BUYER_SHOP_PROFILE') or '').strip().lower() in ('0', 'false', 'no', 'off'):
        return False
    try:
        with open(os.path.join(md, SWITCH), encoding='utf-8') as f:
            d = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(d, dict) and d.get('enabled') is True




BASE_PERCENT = 105
MAX_PERCENT = 110
HOURS = 24
MIN_TALKS = 30
HOLDS = 0.99          # median lowest ask / first ask at or over it: the shop does not come down
NEAR_SHARE = 0.3      # share of its failed talks that ended within MAX_PERCENT of my limit
QUANTILE = 0.8
PAID_MIN = 10
PAID_SHARE = 0.8


def _num(x):
    return type(x) in (int, float) and x > 0


def profile_from_rows(rows, paid, now):
    talks = {}
    for r in rows:
        ts, asks, limit, shop = r.get('ts'), r.get('asks'), r.get('limit'), r.get('shop')
        if not (_num(ts) and now - HOURS * 3600 <= ts <= now and isinstance(shop, str) and _num(limit)
                and isinstance(asks, list) and asks and all(_num(a) for a in asks)):
            continue
        if r.get('state') in (None, 'NO_VALUE'):
            continue
        talks.setdefault(shop, []).append(r)
    out = {}
    for shop, rs in talks.items():
        if len(rs) < MIN_TALKS:
            continue
        holds = sorted(min(r['asks']) / r['asks'][0] for r in rs)
        if holds[len(holds) // 2] < HOLDS:
            continue
        deal = [r for r in rs if r.get('state') == 'TASK_STATE_COMPLETED' and r.get('price') is not None]
        fails = [min(r['asks']) / r['limit'] for r in rs if r not in deal]
        pays = [paid[r['order']] for r in deal if r.get('passed_to_kit') is True and r.get('order') in paid]
        if len(pays) >= PAID_MIN and sum(pays) / len(pays) < PAID_SHARE:
            continue
        near = sorted(x for x in fails if x <= MAX_PERCENT / 100)
        if not fails or len(near) < NEAR_SHARE * len(fails):
            continue
        pct = int(near[max(0, int(len(near) * QUANTILE) - 1)] * 100 + 1e-9)
        pct = min(MAX_PERCENT, pct)
        if pct > BASE_PERCENT:
            out[shop] = pct
    return out


SAVED_FLOOR = 0.25    # a deal keeps at least this share of list in expectation: price <= list (1 - 0.25 / (1 - b))
REFRESH = 1800        # seconds a computed profile is used before it is computed again from the log
TAIL_BYTES = 30_000_000   # past 24 h of measure/orders.jsonl (about 22 MB a day on 11/08)


def _tail(path, nbytes=TAIL_BYTES):
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - nbytes))
            lines = f.read().decode('utf-8', 'replace').splitlines()
    except OSError:
        return
    for x in lines[1:] if size > nbytes else lines:
        try:
            r = json.loads(x)
        except ValueError:
            continue
        if isinstance(r, dict):
            yield r


def compute(md, now):
    paid = {p['order']: p['paid'] for p in _tail(os.path.join(md, 'payments.jsonl'), 4_000_000)
            if isinstance(p.get('order'), str) and isinstance(p.get('paid'), bool)}
    return profile_from_rows(_tail(os.path.join(md, 'measure', 'orders.jsonl')), paid, now)


def _read_cache(path):
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None, {}
    if not isinstance(d, dict) or not _num(d.get('at')) or not isinstance(d.get('shops'), dict):
        return None, {}
    return d['at'], {k: v for k, v in d['shops'].items() if type(v) is int and BASE_PERCENT < v <= MAX_PERCENT}


def profiles(md, now=None):
    """{shop: percent}: the cached profile, computed again from the log when older than REFRESH. Only one buyer run
    computes it at a time; the others go on with the cached one (or none)."""
    now = time.time() if now is None else now
    path = os.path.join(md, CACHE)
    at, shops = _read_cache(path)
    if at is not None and 0 <= now - at < REFRESH:
        return shops
    try:
        with open(path + '.lock', 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return shops
            shops = compute(md, now)
            tmp = f'{path}.{os.getpid()}.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({'at': now, 'shops': shops}, f, sort_keys=True)
            os.replace(tmp, path)
    except OSError:
        pass
    return shops


def percent(md, shop, base, list_price, chance, limit, env=None, now=None):
    """The share (%) of my limit I take a counter up to with this shop: base (plan A's, or None) when the switch is off,
    the shop has no profile, or the profile would not raise it; else the shop's percent, cut so that a deal keeps
    SAVED_FLOOR of list in expectation. Never under base. buyer_accept_band.ceiling keeps it under the budget."""
    if not enabled(md, env):
        return base
    try:
        pct = profiles(md, now).get(shop)
    except Exception:   # noqa: BLE001 - a profile never stops an order
        return base
    if pct is None or not _num(list_price) or not _num(limit):
        return base
    b = min(max(chance or 0, 0.0), 0.99)
    floor_cap = list_price * (1 - SAVED_FLOOR / (1 - b))
    pct = min(pct, int(floor_cap * 100 // limit))
    return pct if pct > (base or 100) else base
