"""Source-OFF goods bundle: one extra bounded counter and payment-aware scheduling.

The observed .952 final/first-ask ratio is not a baseline parameter. Baseline
oneshot accepts a counter within its value limit. This treatment instead offers
.92 of the first ask once after .90, then accepts only within that lower cap.
Unparsed turns retain the existing harness path.
"""
import hashlib
import collections
import json
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
            and context.get('kind') == 'goods' and _number(context.get('at'))
            and _number(context.get('started_at')) and START <= context['started_at'] <= context['at'] <= now)


def reserve(md, order, *, rule_engine=False, conflict=False, now=None):
    at = time.time() if now is None else now
    oid, started = _order_id(order.get('order')), order.get('_buyer_started_at')
    if (rule_engine is not True or conflict or not _number(at) or not window(at) or not oid or
            order.get('kind') != 'goods' or not _number(started) or not START <= started <= at or
            order.get('task') or order.get('resume') or order.get('state') in ('REJECTED', 'CANCELED', 'COMPLETED')):
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


def turn(baseline, meta, text, history, limit, steps, *, order, rule_engine=False,
         quoted=None, now=None):
    context = order.get('_buyer_bundle')
    if (not valid(context, rule_engine=rule_engine, now=now) or context['arm'] != 'B'
            or order.get('kind') != 'goods' or baseline is None):
        return baseline
    import oneshot
    ask = meta if meta is not None else oneshot.text_price(text, history, quoted, True)
    result = counter(baseline, ask, history, limit, steps)
    if result != baseline:
        decisions = order.setdefault('_bundle_counters', [])
        if len(decisions) < 8:
            decisions.append(dict(history_index=len(history), action=result['action'],
                                  baseline_price=baseline.get('price'), decision_price=result['price']))
    return result


def measurement(order, out):
    context = order.get('_buyer_bundle')
    if not context:
        return None
    history = out.get('history') or []
    decisions = []
    for row in order.get('_bundle_counters') or []:
        index = row['history_index'] + 1
        entry = history[index] if index < len(history) else None
        decisions.append({**row, 'history_offer_attempt':
                          entry[1] if entry and entry[0] == 'you offered' else None})
    return dict(study=context['study'], arm=context['arm'],
                eligible=(order.get('_bundle_plan') or {}).get('eligible'),
                slot_eligible=(order.get('_bundle_plan') or {}).get('slot_eligible'),
                counter_decisions=decisions)


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


def settlements(path):
    """Bounded local review ledger tail; no market/model/API calls."""
    try:
        with open(path, 'rb') as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - (8 << 20)))
            lines = stream.read().splitlines()
        if size > (8 << 20):
            lines = lines[1:]
        rows = [json.loads(line) for line in lines]
        return rows if len(rows) <= 20000 and all(isinstance(r, dict) for r in rows) else []
    except (OSError, ValueError):
        return []  # unknown ledger keeps every shop at the common prior


def payment_scores(trades, settled, category, *, now, payments=()):
    """Recent known outcomes, with unresolved payments explicitly excluded.

    Payment fields come from sync_payments' official kit log. Success additionally
    needs exactly one matching official settlement. Saved quality is counted only
    after the reported warranty (at least 6 h); observed breaks contribute zero.
    This is historical ranking evidence, not verified refund completion.
    """
    trades = trades[-10000:]
    repeated = collections.Counter(t.get('order') for t in trades)
    index = collections.defaultdict(list)
    for row in settled:
        index[row.get('order')].append(row)
    paid_index = collections.defaultdict(list)
    for row in payments:
        paid_index[row.get('order')].append(row)
    groups = collections.defaultdict(lambda: [0, 0, []])
    audit = dict(attempts=0, resolved=0, unknown=0, paid_settled=0, mature_quality=0)
    for trade in trades:
        try:
            at = time.mktime(time.strptime(trade['time'], '%Y-%m-%dT%H:%M:%S'))
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        if trade.get('category') != category or not 0 <= now - at <= 86400:
            continue
        shop, sku, oid = trade.get('shop'), trade.get('item'), trade.get('order')
        if not all(isinstance(x, str) and x for x in (shop, sku, oid)):
            continue
        audit['attempts'] += 1
        pay = paid_index[oid]
        payment_matches = (len(pay) == 1 and pay[0].get('item') == sku
                           and pay[0].get('price') == trade.get('price')
                           and isinstance(pay[0].get('shop'), str) and bool(pay[0]['shop']))
        paid = pay[0].get('paid') if payment_matches else None
        matching = index[oid]
        success = (paid is True and len(matching) == 1 and matching[0].get('shop') == pay[0]['shop']
                   and matching[0].get('sku') == sku and matching[0].get('price') == trade.get('price')
                   and _number(matching[0].get('time')) and at <= matching[0]['time'] <= now)
        if repeated[oid] != 1 or not (paid is False or success):
            audit['unknown'] += 1
            continue
        counts = groups[(shop, sku)]
        counts[0] += 1
        counts[1] += int(success)
        audit['resolved'] += 1
        audit['paid_settled'] += int(success)
        if success:
            row, lp, price = matching[0], trade.get('list_price'), trade.get('price')
            hours = row.get('warranty_hours')
            if (_number(hours) and hours >= 6 and now - row['time'] >= hours * 3600
                    and _number(lp) and _number(price) and 0 < price <= lp):
                quality = (1 - price / lp) if row.get('claim_honest') is True and not trade.get('broke') else 0
                counts[2].append(quality)
                audit['mature_quality'] += 1
    scores = {}
    for key, (n, paid, qualities) in groups.items():
        if n >= 3:
            quality = (sum(qualities) + .5) / (len(qualities) + 2)
            scores[key] = (paid + 1) / (n + 2) * (.5 + .5 * quality)
    return scores, audit


def decide(context, baseline, fits, rivals, skus, scores, audit, *, k, code_eligible,
           rule_engine=False, now=None):
    names = [s['name'] for s in baseline]
    if not valid(context, rule_engine=rule_engine, now=now):
        return names, None
    treatment = [s['name'] for s in schedule(baseline, fits, rivals, skus, scores)]
    batch = max(0, k - 1)
    plan = dict(protocol='goods-bundle-v1', baseline=names, treatment=treatment, k=k,
                first_baseline=names[:batch], first_treatment=treatment[:batch],
                slot_eligible=set(names[:batch]) != set(treatment[:batch]),
                counter_eligible=bool(code_eligible), eligible=bool(code_eligible or names[:batch] != treatment[:batch]),
                inputs=[dict(name=s['name'], usable=[dict(sku=skus[s['name']])]) for s in baseline],
                history_audit=audit)
    row = dict(schema=1, event='decision', order=context['order'], study=STUDY, arm=context['arm'],
               at=time.time() if now is None else now, plan=plan)
    prior = _io(lambda: _once(os.path.join(context['directory'], 'decision.json'), row))
    if (not prior or prior.get('plan') != plan or prior.get('arm') != context['arm'] or
            not valid(context, rule_engine=rule_engine, now=now)):
        return names, None
    return (treatment if context['arm'] == 'B' else names), plan
