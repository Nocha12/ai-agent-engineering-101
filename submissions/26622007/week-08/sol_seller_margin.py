"""Default OFF, fresh seller study: .97 M versus 1.00 M on 300k+ goods.

SKU x grade is the randomization unit; tasks are observations, not independent
samples. SQLite commits assignment and intended price before the caller uses it.
No inventory, payment, model, network, legacy controller or pricing writes.
"""
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time

import profit_price

VERSION = 1
# Capture at import, never claim a long-running process loaded edited disk code.
SOURCE_BINDING = hashlib.sha256(b''.join(
    (Path(__file__).parent/name).read_bytes()
    for name in ('sol_seller_margin.py', 'seller.py', 'profit_price.py'))).hexdigest()


class StudyError(RuntimeError):
    """No public quote may consume an uncommitted or conflicting assignment."""


def number(v):
    return type(v) in (int, float) and math.isfinite(v)


class MarginStudy:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else Path(
            os.environ.get('MARKET_DIR') or Path(__file__).parent/'.market')/'sol_seller_margin_v1'

    def config(self, now=None):
        now = time.time() if now is None else now
        path = self.root/'controller.json'
        if not path.exists():
            return None
        try:
            if self.root.is_symlink() or path.is_symlink():
                raise ValueError('symlink')
            c = json.loads(path.read_text())
            if not isinstance(c, dict) or type(c.get('enabled')) is not bool:
                raise ValueError('enabled')
            if c['enabled'] is False:
                return None
            if set(c) != {'enabled', 'version', 'study_id', 'epoch', 'start', 'end', 'salt', 'source_sha256'}:
                raise ValueError('schema')
            if type(c['version']) is not int or c['version'] != VERSION:
                raise ValueError('version')
            if any(not isinstance(c[k], str) or not re.fullmatch(r'[A-Za-z0-9_-]{12,80}', c[k])
                   for k in ('study_id', 'salt')):
                raise ValueError('identity')
            if not all(number(c[k]) for k in ('epoch', 'start', 'end')):
                raise ValueError('time')
            if c['epoch'] != c['start'] or not 9*3600 <= c['end']-c['start'] <= 12*3600:
                raise ValueError('window')
            if c['source_sha256'] != SOURCE_BINDING:
                raise ValueError('source_binding')
            return c if c['start'] <= now < c['end'] else None
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise StudyError('margin controller invalid') from exc

    @staticmethod
    def peer_active(controller, now):
        if (controller.root/'disabled').exists():
            return False
        # Adopted interventions other than the common opening default also conflict.
        if (controller.root/'adopted').is_file():
            return True
        cfg = controller.config()
        if not cfg or cfg.get('stop'):
            return False
        return cfg.get('start', float('-inf')) <= now < cfg.get('end', float('inf'))

    def pre_pick_ok(self, others=(), now=None):
        now = time.time() if now is None else now
        if not self.config(now):
            return True
        return not any(self.peer_active(c, now) for c in others)

    def _db(self):
        if self.root.is_symlink():
            raise StudyError('margin root symlink')
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root/'journal.sqlite3'
        if any(Path(str(path)+s).is_symlink() for s in ('', '-journal', '-wal', '-shm')):
            raise StudyError('margin journal symlink')
        db = sqlite3.connect(path, timeout=2)
        db.execute('PRAGMA synchronous=FULL')
        db.execute('CREATE TABLE IF NOT EXISTS study (id TEXT PRIMARY KEY, config TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS observations (task TEXT PRIMARY KEY, data TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS events (task TEXT, event TEXT, at REAL, data TEXT)')
        return db

    def quote(self, task, unit, median, measured, paid, pricing, now=None):
        """Real price seam; A retains round(), B changes only high_median_up.factor.

        The caller supplies the same ledger median/risk/history to both policies.
        Missing median, floor/cap clipping and warranty refusals remain in ITT.
        Valid fixed scarce asks exclude the SKU from target ITT in both arms,
        even when that ask is below the median and absent from baseline why.
        """
        now = time.time() if now is None else now
        q = profit_price.quote(unit, median, measured, paid, pricing)
        c = self.config(now)
        if not c:
            return q, None
        try:
            price = (pricing or {}).get('price') or {}
            if ((price.get('high_median_up') or {}).get('factor') != .97
                    or (price.get('high_median_up') or {}).get('min_list') != 300000
                    or (price.get('high_price_trial') or {}).get('share', 0) != 0):
                raise StudyError('legacy price policy conflicts')
            if not isinstance(task, str) or not task or len(task) > 128:
                raise StudyError('invalid task')
            cluster = [unit.get('sku'), unit.get('grade')]
            if not all(isinstance(v, str) and v for v in cluster):
                raise StudyError('missing assignment unit')
            scarce_asks = profit_price.cfg_of(pricing).get('scarce_asks')
            scarce = scarce_asks.get(unit.get('sku')) if isinstance(scarce_asks, dict) else None
            scarce_fixed = number(scarce) and scarce > 0
            # Record excluded selections, but compare only non-scarce high goods.
            high_goods = unit.get('kind') == 'goods' and (unit.get('list_price') or 0) >= 300000
            eligible = high_goods and not scarce_fixed
            arm = 'B' if hashlib.sha256(json.dumps(
                [c['salt'], *cluster], separators=(',', ':')).encode()).digest()[0] < 128 else 'A'
            candidate = q
            reason = 'scarce_fixed_ask' if high_goods and scarce_fixed else 'outside_target'
            if eligible:
                reason = 'no_median'
                if number(median) and median > 0:
                    bprice = dict(price, high_median_up=dict(price.get('high_median_up') or {}, factor=1.0))
                    candidate = profit_price.quote(unit, median, measured, paid, dict(pricing, price=bprice))
                    reason = ('warranty_refusal' if q['refuse'] else 'control' if arm == 'A'
                              else 'floor_or_policy_clipped' if candidate['open'] == q['open'] else 'price_change')
            changed = bool(eligible and reason == 'price_change')
            chosen = candidate if changed else q
            rec = dict(study_id=c['study_id'], version=VERSION, epoch=c['epoch'], salt=c['salt'],
                       source_sha256=SOURCE_BINDING, task=task, at=now, cluster=cluster,
                       unit=unit.get('unit'), sku=unit.get('sku'), grade=unit.get('grade'),
                       cost=unit.get('cost'), list_price=unit.get('list_price'), kind=unit.get('kind'),
                       median=median, floor=q['floor'], b=q['b'], eligible=eligible, arm=arm,
                       baseline=q['open'], candidate=candidate['open'], selected=chosen['open'],
                       prepared_treatment=changed, reason=reason)
            encoded = json.dumps(rec, sort_keys=True, allow_nan=False)
            config = json.dumps(c, sort_keys=True, allow_nan=False)
            with closing(self._db()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                prior = db.execute('SELECT id, config FROM study').fetchall()
                if prior and prior != [(c['study_id'], config)]:
                    raise StudyError('fresh study requires a fresh controller root')
                db.execute('INSERT OR IGNORE INTO study VALUES (?,?)', (c['study_id'], config))
                old = db.execute('SELECT data FROM observations WHERE task=?', (task,)).fetchone()
                if old:
                    prior = json.loads(old[0])
                    # Idempotence only for the identical input and cohort. No repricing retry.
                    if {k:v for k,v in prior.items() if k != 'at'} != {k:v for k,v in rec.items() if k != 'at'}:
                        raise StudyError('task snapshot changed')
                    rec = prior
                else:
                    db.execute('INSERT INTO observations VALUES (?,?)', (task, encoded))
            return chosen, rec
        except StudyError:
            raise
        except Exception as exc:
            # No baseline fallback: otherwise failed B assignments disappear from ITT.
            raise StudyError('margin assignment unavailable') from exc

    def selection_miss(self, task, now=None):
        """No selected unit is outside the target ITT, but keep the ingress count."""
        now = time.time() if now is None else now
        c = self.config(now)
        if c:
            self.event(task, dict(study_id=c['study_id'], epoch=c['epoch']), 'selection_miss', now=now)

    def ready(self, task, assignment, deal, keep, others=(), now=None):
        """Durable final price before publication; common markdown/opening stay common."""
        if not assignment:
            return
        now = time.time() if now is None else now
        c = self.config(now)
        if (not c or any(c[k] != assignment[k] for k in ('study_id', 'epoch', 'salt', 'source_sha256'))
                or not self.pre_pick_ok(others, now)):
            raise StudyError('study changed before quote consumption')
        baseline = assignment['baseline']
        if keep < 1:
            baseline = max(deal['reserve'], round(baseline*keep))
        opening = deal.get('opening_default') or {}
        if opening.get('policy') == 'opening-3pct-v1':
            baseline = max(deal['reserve'], round(baseline*.97))
        actual = bool(assignment['prepared_treatment'] and deal['ask'] != baseline)
        self.event(task, assignment, 'quote_ready', now, selected_unit=deal.get('unit'),
                   baseline_final=baseline, final_price=deal['ask'], reserve=deal['reserve'],
                   warranty=deal.get('warranty'), actual_treatment=actual,
                   untreated_reason=None if actual else (
                       'downstream_clipped' if assignment['prepared_treatment'] else assignment['reason']))

    def event(self, task, assignment, event, now=None, **data):
        if not assignment:
            return
        now = time.time() if now is None else now
        payload = dict(data, study_id=assignment['study_id'], epoch=assignment['epoch'])
        try:
            with closing(self._db()) as db, db:
                db.execute('INSERT INTO events VALUES (?,?,?,?)',
                           (task, event, now, json.dumps(payload, allow_nan=False, sort_keys=True)))
        except Exception as exc:
            raise StudyError('margin event unavailable') from exc

    def observations(self):
        path = self.root/'journal.sqlite3'
        if not path.exists():
            return []
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM observations ORDER BY task')]

    def events(self):
        path = self.root/'journal.sqlite3'
        if not path.exists():
            return []
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            return [dict(task=task, event=event, at=at, **json.loads(data))
                    for task,event,at,data in db.execute('SELECT task,event,at,data FROM events ORDER BY rowid')]


def exact_outcome(task, payments, ledger, shop):
    """Read-only exact join. Local paid/unit disappearance is never confirmed.

    Accept the actual full ledger envelope or the collector's explicit k rows.
    Result.receipt.task joins seller task to order; no fuzzy unit/time matching.
    Missing buyer payment evidence stays unknown, even with a settle row.
    """
    if not isinstance(shop, str) or not shop or not isinstance(payments, list):
        return dict(status='unknown', reason='input_schema')
    rows = []
    for r in ledger:
        if not isinstance(r, dict):
            return dict(status='unknown', reason='ledger_schema')
        if isinstance(r.get('data'), dict) and 'kind' in r:
            if 'seq' not in r or 'ts' not in r:
                return dict(status='unknown', reason='ledger_schema')
            r = dict(r['data'], k=r['kind'], seq=r['seq'], ts=r['ts'])
        rows.append(r)
    results = [r for r in rows if r.get('k') == 'result' and r.get('shop') == shop
               and isinstance(r.get('receipt'), dict) and r['receipt'].get('task') == task]
    keys = {(r.get('order'), r.get('buyer')) for r in results}
    if len(keys) != 1:
        return dict(status='unknown', reason='result_missing_or_ambiguous')
    order, buyer = next(iter(keys))
    if not isinstance(order, str) or not isinstance(buyer, str):
        return dict(status='unknown', reason='identity_missing')
    if any(not isinstance(p, dict) for p in payments):
        return dict(status='unknown', reason='payment_schema')
    ps = [p for p in payments if p.get('order') == order]
    p = ps[-1] if ps else {}
    if p.get('paid') is False:
        return dict(status='refused', order=order)
    seq = p.get('ledger')
    if isinstance(seq, str) and seq.isdigit():
        seq = int(seq)
    settles = [r for r in rows if r.get('k') == 'settle' and type(r.get('seq')) is int and r['seq'] == seq]
    if (p.get('paid') is not True or type(seq) is not int or len(settles) != 1
            or not number(p.get('price')) or p['price'] <= 0):
        return dict(status='unknown', reason='payment_or_settle_missing')
    s = settles[0]
    if any(r.get('verified') is not True or r['receipt'].get('shop') != shop
           or r['receipt'].get('price') != p['price'] for r in results):
        return dict(status='unknown', reason='result_receipt_unverified_or_mismatch')
    if not number(s.get('amount')) or any(s.get(k) != v for k,v in dict(order=order, buyer=buyer, shop=shop, amount=p['price']).items()):
        return dict(status='unknown', reason='settle_mismatch')
    return dict(status='confirmed', order=order, seq=seq, price=p['price'], at=s.get('ts'),
                broken=[r['seq'] for r in rows if r.get('k') == 'broken' and r.get('order') == order
                        and r.get('buyer') == buyer and r.get('shop') == shop])
