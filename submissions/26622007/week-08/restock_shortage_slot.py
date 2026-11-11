"""Finite, default-off shortage-slot strategy; never expands the cash/stock guards.

Only one fixed food SKU replaces one fixed surplus refill. Other owners' lines
keep their positions. Windows alternate A/B; no prices or policy settings change.
"""
import json
import math
import os
from pathlib import Path
import re
import time

FILE = 'restock_shortage_slot.json'
TARGET = 'G5f5f42e0'
DONOR = 'Gdb8ba026'
ANCHORS = frozenset(('G6b0fd5bc', 'G2c023a8c', 'Gbec2b68f', 'G03d0199f', 'G1515d951', 'G24f5f979'))


def number(v):
    return type(v) in (int, float) and math.isfinite(v)


def apply(state, buy, inv, model, catalog, waiting, guarded, *, now=None):
    """Public strategy seam. guarded reapplies the original stock AND cash guards.

    A missing/invalid switch, unknown demand, occupied target, no eligible slot,
    failed guard or failed observation returns the original list unchanged.
    """
    try:
        now = time.time() if now is None else now
        cfg = json.loads((Path(state) / FILE).read_text())
        start, stop = cfg.get('start'), cfg.get('stop')
        window = inv.get('restock') or {}
        opens, closes = window.get('opens_at'), window.get('closes_at')
        if (cfg.get('enabled') is not True or not re.fullmatch(r'[a-z0-9-]{1,64}', cfg.get('study', ''))
                or not all(number(v) for v in (start, stop, opens, closes, now))
                or not 0 < stop-start <= 3600 or not start <= now < stop
                or not start <= opens < closes <= stop or not opens-50 <= now < closes
                or type(cfg.get('b_parity')) is not int or cfg['b_parity'] not in (0, 1)):
            return buy
        arm = 'B' if int(opens // 1200) % 2 == cfg['b_parity'] else 'A'
        proposed = list(buy)
        changed = False
        if arm == 'B' and isinstance(model, dict) and model.get('active'):
            skus = model.get('skus') or {}
            def rate(sku):
                m = skus.get(sku) or {}
                values = (m.get('rate'), m.get('my_share'), m.get('mine_rate', 0))
                if not all(number(v) and v >= 0 for v in values):
                    return None
                return max(values[0] * values[1], values[2])
            count = {}
            for u in inv.get('units') or []:
                if u.get('grade') != 'expired' and (not u.get('expires_at') or u['expires_at'] > now):
                    count[u['sku']] = count.get(u['sku'], 0) + 1
            for w in waiting:
                count[w['sku']] = count.get(w['sku'], 0) + w['qty']
            r, donor_rate = rate(TARGET), rate(DONOR)
            donor = next((i for i,b in enumerate(buy) if b.get('sku') == DONOR), None)
            anchor = next((i for i,b in enumerate(buy) if b.get('sku') in ANCHORS), None)
            target = catalog.get(TARGET) or {}
            ratio = (skus.get(TARGET) or {}).get('price_ratio')
            if (donor is not None and anchor is not None and anchor < donor and r and donor_rate
                    and count.get(TARGET, 0) == 0 and count.get(DONOR, 0) / donor_rate >= 2/3
                    and not any(b.get('sku') == TARGET for b in buy)
                    and target.get('kind') == 'food' and number(ratio) and ratio > .5):
                qty = min(2, math.ceil(r/3))  # at most one 20-minute window's observed demand
                proposed[anchor] = {'sku': TARGET, 'qty': qty}
                proposed[donor] = dict(buy[anchor])
                candidate = guarded(proposed)
                # A guard may shrink the new SKU, never another owner's line.
                if (len(candidate) == len(buy) and all(candidate[i] == proposed[i] for i in range(len(buy)) if i != anchor)
                        and candidate[anchor].get('sku') == TARGET and 0 < candidate[anchor].get('qty', 0) <= qty):
                    proposed, changed = candidate, True
        event = dict(at=now, study=cfg['study'], opens_at=opens, closes_at=closes, arm=arm, changed=changed,
                     target=TARGET, donor=DONOR, before=buy, after=proposed)
        data = (json.dumps(event, allow_nan=False, separators=(',', ':'))+'\n').encode()
        path = Path(state)/'measure/restock_shortage_slot.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
            if os.write(fd, data) != len(data):
                return buy
        finally:
            os.close(fd)
        return proposed if changed else buy
    except Exception:
        return buy
