"""Read-only paid-sale cohorts. Never treat open warranties or missing costs as zero risk."""
import json
import os
import re
import time


def read_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read_rows(path):
    out = []
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError:
        pass
    return out


def summarize(rows, mine, negotiations, losses, now, start=0):
    """Join own settled seq -> unit -> paid memory cost. Refunds stay with sale cohorts.

    loss_watch's seq linkage is inferred from buyer/SKU chronology, not an authoritative
    refund receipt. Report it explicitly; absent cost invalidates a cohort's net total.
    Maturity uses six hours even if a seller offered a shorter warranty.
    """
    costs = {}
    for n in negotiations:
        if n.get('result') == 'deal' and n.get('unit') and isinstance(n.get('cost'), (int, float)):
            costs[n['unit']] = n['cost']
    refunds = {}
    for loss in losses:
        match = re.search(r'ledger #(\d+)', loss.get('detail', ''))
        if match and loss.get('kind') == 'break_refund' and loss.get('ts', now + 1) <= now:
            refunds[int(match[1])] = loss.get('amount', 0)
    def empty():
        return dict(paid=0, revenue=0, known_cost=0, missing_cost=0, refunds=0,
                    refunded_sales=0, known_net=0, net=None, net_per_sale=None)
    groups, seen = {}, set()
    for r in sorted(rows, key=lambda x: x.get('ts') or 0):
        seq = r.get('seq')
        if r.get('k') != 'settle' or seq in seen or str(seq) not in mine or not start <= (r.get('ts') or 0) <= now:
            continue
        seen.add(seq)
        age = now - r['ts']
        sku = r.get('sku') or 'unknown'
        cohort = groups.setdefault(sku, {'all': empty(), 'mature_6h': empty(), 'open_6h': empty(), 'by_grade': {}})
        grade = cohort['by_grade'].setdefault(r.get('grade') or 'unknown', {'all': empty(), 'mature_6h': empty()})
        targets = [cohort['all'], cohort['mature_6h' if age >= 21600 else 'open_6h'], grade['all']]
        if age >= 21600:
            targets.append(grade['mature_6h'])
        cost = costs.get(mine[str(seq)].get('unit'))
        refund = refunds.get(seq, 0)
        for t in targets:
            t['paid'] += 1
            t['revenue'] += r.get('amount') or 0
            t['refunds'] += refund
            t['refunded_sales'] += seq in refunds
            if cost is None:
                t['missing_cost'] += 1
            else:
                t['known_cost'] += cost
                t['known_net'] += (r.get('amount') or 0) - cost - refund
    def finish(node):
        if 'paid' in node:
            if node['paid'] and not node['missing_cost']:
                node['net'] = node['known_net']
                node['net_per_sale'] = round(node['net'] / node['paid'], 2)
        else:
            for v in node.values():
                if isinstance(v, dict):
                    finish(v)
    finish(groups)
    latest = max((r.get('ts') or 0 for r in rows if (r.get('ts') or 0) <= now), default=0)
    return {'as_of': now, 'start': start, 'maturity_hours': 6,
            'ledger_latest_ts': latest or None,
            'ledger_lag_seconds': round(now - latest) if latest else None,
            'refund_basis': 'loss_watch inferred sale seq; not a confirmed refund receipt',
            'net_basis': 'paid revenue minus unit cost minus linked refunds; excludes unsold stock/storage',
            'skus': groups}


def build(root, now=None, start=0):
    md = os.path.join(root, '.market')
    return summarize(read_rows(os.path.join(md, 'ledger_rows.jsonl')),
                     read_json(os.path.join(md, 'loss_state.json'), {}).get('mine', {}),
                     read_json(os.path.join(root, 'memory', 'seller_memory_r3.json'), {}).get('negotiations', []),
                     read_rows(os.path.join(root, 'memory', 'agent', 'losses.jsonl')),
                     time.time() if now is None else now, start)
