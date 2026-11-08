"""Source-OFF goods bundle: one extra bounded counter and payment-aware scheduling.

The observed .952 final/first-ask ratio is not a baseline parameter. Baseline
oneshot accepts a counter within its value limit. This treatment instead offers
.92 of the first ask once after .90, then accepts only within that lower cap.
Unparsed turns retain the existing harness path.
"""
import hashlib
import os
import re
import time
import uuid

from buyer_stock_evidence import _io, _once, _hash, _order_id, _number

ENABLED = False
START = 0
END = 0
STUDY = 'goods-counter-payment-bundle'
ROOT = 'buyer_bundle_trial'


def allocation(order_id):
    identity = str(uuid.uuid5(uuid.NAMESPACE_URL, STUDY + ':' + order_id))
    arm = 'AB'[int.from_bytes(hashlib.sha256((STUDY + ':' + identity).encode()).digest(), 'big') % 2]
    return identity, arm


def window(now):
    return (ENABLED is True and type(START) is int and type(END) is int and
            END == START + 1800 and 0 < START <= now < END and
            isinstance(STUDY, str) and re.fullmatch('[A-Za-z0-9_-]{1,64}', STUDY) is not None)


def valid(context, *, rule_engine=False, now=None):
    now = time.time() if now is None else now
    if rule_engine is not True or not isinstance(context, dict) or not _number(now) or not window(now):
        return False
    oid = _order_id(context.get('order'))
    if not oid:
        return False
    identity, arm = allocation(oid)
    return (context.get('study') == STUDY and context.get('start') == START and context.get('end') == END
            and context.get('arm') == arm and context.get('allocation_id') == identity
            and _number(context.get('started_at')) and START <= context['started_at'] <= now)


def reserve(md, order, *, rule_engine=False, conflict=False, now=None):
    at = time.time() if now is None else now
    oid, started = _order_id(order.get('order')), order.get('_buyer_started_at')
    if (rule_engine is not True or conflict or not _number(at) or not window(at) or not oid or
            order.get('kind') != 'goods' or not _number(started) or not START <= started <= at or
            order.get('task') or order.get('resume')):
        return None
    identity, arm = allocation(oid)
    row = dict(schema=1, event='assignment', study=STUDY, order=oid, allocation_id=identity,
               arm=arm, start=START, end=END, at=at, started_at=started, kind='goods')
    directory = os.path.join(md, ROOT, _hash([STUDY, oid]))
    prior = _io(lambda: _once(os.path.join(directory, 'assignment.json'), row))
    if not valid(prior, rule_engine=rule_engine, now=at):
        return None
    return {**prior, 'directory': directory}


def counter(baseline, ask, history, limit, steps):
    if (steps != (.9,) or not isinstance(baseline, dict) or
            baseline.get('action') != 'offer' or type(ask) is not int or
            not 0 < ask <= 999999999999 or type(limit) is not int or limit <= 0):
        return baseline
    asks = [price for who, price in history if who == 'shop asked']
    offers = [price for who, price in history if who == 'you offered']
    if not asks or not offers or any(type(p) is not int or p <= 0 for p in asks + offers):
        return baseline  # the original first .90 offer is unchanged
    cap = min(limit, asks[0] * 92 // 100)
    if ask <= cap:
        return {**baseline, 'price': min(ask, limit)}
    if len(offers) == 1 and offers[-1] < cap:
        return {**baseline, 'price': cap}
    return {**baseline, 'action': 'walk_away', 'price': 0}


def schedule(baseline, fits, rivals, skus, scores):
    """Reorder fitted exploitation only, within the same requested SKU/rival class.

    Unseen shops keep their exploration positions. Missing history has the same
    prior for every shop; stable sorting preserves the original preference ties.
    """
    result = list(baseline)
    groups = {}
    for i, shop in enumerate(baseline):
        name = shop['name']
        if fits.get(name, 0) > 0 and skus.get(name):
            groups.setdefault((skus[name], name in rivals), []).append(i)
    for positions in groups.values():
        ordered = sorted((baseline[i] for i in positions),
                         key=lambda s: -scores.get(s['name'], .3125))
        for i, shop in zip(positions, ordered):
            result[i] = shop
    return result
