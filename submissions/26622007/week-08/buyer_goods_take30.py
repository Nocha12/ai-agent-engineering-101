"""Default-OFF finite goods metadata-ask take; no transport, parser or selection changes.

Numeric buyer.take_at bypasses its final decision_guard. This buyer-only adapter
therefore uses the existing ladder/final guard seam for the same bare-ask wire.
Assignment is ITT (before selection); a successful durable intent is not payment.
"""
import fcntl
import hashlib
import json
import math
import os
import re
import time

import buyer
import ask_read
import buyer_warranty_said

SWITCH = 'buyer_goods_take30.json'
JOURNAL = 'buyer_goods_take30_journal.json'
POLICY = 'goods-cheap-first-take30'
KEY = '_goods_take30'


def epoch(v):
    return type(v) in (int, float) and abs(v) < 1e12 and math.isfinite(v)


def money(v):
    return type(v) is int and 0 < v < 10**12


def ident(v):
    return isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,96}', v) is not None


def config(md, env=None, now=None):
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    if not epoch(now) or str(env.get('BUYER_GOODS_TAKE30','')).strip().lower() in ('off','0','false','no'):
        return None
    try:
        with open(os.path.join(md, SWITCH)) as f:
            raw = f.read()
            mtime = os.fstat(f.fileno()).st_mtime
        d = json.loads(raw)
        s,e = d.get('start'),d.get('end')
        if (d.get('enabled') is not True or not all(ident(d.get(k)) for k in ('study','salt'))
                or not all(epoch(v) for v in (s,e)) or not 0 < s < e == s+1800
                or not mtime <= s <= now < e):
            return None
        return {k:d[k] for k in ('study','salt','start','end')} | {
            'config_sha256':hashlib.sha256(raw.encode()).hexdigest()}
    except (OSError,ValueError,TypeError,AttributeError,RecursionError):
        return None


def switch_on(md, *, study, salt, start, end):
    """Parent-only explicit finite window generator; never extends/reuses an existing study."""
    now=time.time()
    if (not ident(study) or not ident(salt) or not epoch(start) or not epoch(end)
            or not 0 < now < start < end == start+1800):
        raise ValueError('future exact 1800-second window required')
    path=os.path.join(md,SWITCH)
    d=dict(enabled=True,study=study,salt=salt,start=start,end=end)
    with open(path,'x') as f:
        json.dump(d,f,allow_nan=False); f.flush(); os.fsync(f.fileno())
    return d


def arm(cfg, oid):
    return 'AB'[hashlib.sha256((cfg['salt']+':'+oid).encode()).digest()[0] & 1]


def _journal(md, cfg, event, oid, note=None):
    """Locked atomic journal; fail closed on corruption, old namespace or any IO error."""
    path = os.path.join(md,JOURNAL)
    with open(path+'.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try:
            with open(path) as f: doc=json.load(f)
        except FileNotFoundError:
            doc={'config_sha256':cfg['config_sha256'],'orders':{}}
        if (not isinstance(doc,dict) or doc.get('config_sha256') != cfg['config_sha256']
                or not isinstance(doc.get('orders'),dict)):
            raise ValueError('journal_namespace')
        rows=doc['orders']
        if event == 'assign':
            if oid in rows: raise ValueError('reused_order')
            rows[oid]=note
        else:
            prior=rows.get(oid)
            if (not isinstance(prior,dict) or any(prior.get(k) != note.get(k) for k in
                    ('invocation','arm','study','salt','start','end','config_sha256','order','assigned_at'))):
                raise ValueError('journal_identity')
            if event == 'intent' and prior.get('intent'):
                raise ValueError('repeated_intent')
            rows[oid]={**prior, **note}
        tmp=path+'.tmp.'+str(os.getpid())
        try:
            with open(tmp,'x') as f:
                json.dump(doc,f,allow_nan=False); f.flush(); os.fsync(f.fileno())
            os.replace(tmp,path)
            fd=os.open(md,os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)


def assign(md, order, *, now=None, env=None):
    now=time.time() if now is None else now
    if not isinstance(order,dict) or order.get('kind') != 'goods':
        return None
    cfg=config(md,env,now)
    if cfg is None: return None
    note=dict(cfg, policy=POLICY, order=order.get('order'), arm=None, applied=False, fallback='invalid_start')
    order[KEY]=note
    started=order.get('_buyer_started_at')
    if not ident(order.get('order')) or not epoch(started) or not cfg['start'] <= started <= now < cfg['end']:
        return None
    note.update(arm=arm(cfg,order['order']), assigned_at=now,
                invocation=os.urandom(16).hex(), fallback='not_reached', intent=False)
    try:
        _journal(md,cfg,'assign',order['order'],note)
    except (OSError,ValueError,TypeError,RecursionError) as e:
        note.update(arm=None,fallback='reused_order' if str(e)=='reused_order' else 'journal_failure')
        return None
    return note


def _active(md, order):
    n=order.get(KEY)
    c=config(md)
    return (isinstance(n,dict) and n.get('arm') in ('A','B') and c is not None
            and all(n.get(k)==v for k,v in c.items()))


def reason(*args, **kwargs):
    try:
        return _reason(*args, **kwargs)
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError, RecursionError):
        return 'invalid_runtime_shape'


def _reason(order, offer, q, steps, shop, sku, budget, resume, take_at):
    """Current runtime full quote/task/selection; no inference from truncated measurement text."""
    if order.get('kind') != 'goods' or tuple(steps or ()) != (.9,): return 'kind_or_steps'
    if not resume or not isinstance(q,dict) or q.get('closed') or q.get('refused'): return 'no_resume'
    if (take_at is not None or q.get('ask_src') != 'metadata' or not money(q.get('ask'))
            or not money(budget) or not money(offer.get('list_price'))): return 'not_metadata_money'
    if q['ask'] > min(budget,offer['list_price']//2): return 'not_cheap'
    if (offer.get('sku') != sku or not isinstance(sku,str)
            or re.fullmatch(r'G[0-9a-f]{8}',sku) is None): return 'selection'
    t=q.get('task')
    if (not isinstance(t,dict) or not ident(t.get('id')) or resume != (q.get('url'),t)
            or t.get('status',{}).get('state') != 'TASK_STATE_INPUT_REQUIRED'
            or buyer.metadata_ask(t) != q['ask']): return 'task_mismatch'
    raw=(t.get('status',{}).get('message',{}).get('metadata') or {}).get('ask')
    if not money(raw) or raw != q['ask']: return 'raw_metadata_not_integer'
    text=buyer.message_text(t)
    if not isinstance(q.get('text'),str) or not text.startswith(q['text']): return 'quote_text_changed'
    h=q.get('warranty_h')
    short_ok=(isinstance(order.get('_sw'),dict) and epoch(h) and h > 0
              and ask_read.short_warranty_hours(text) == h)
    if (set(re.findall(r'\bG[0-9a-f]{8}\b',text)) != {sku}
            or not (buyer_warranty_said.stated_ok(text) or short_ok)):
        return 'sku_or_warranty'
    conflicts=('_goods_ladder','_goods_first_one_probe','_goods_extra_quote_probe',
        '_quote_alignment','_first95_trial','_cap95_trial','_firm_trial',
        '_moss_first','_final_m1_window','_our_ask')
    # B3 affects only a later counter after the first bid, not this first-turn factor.
    if (order.get('_b3') or {}).get('applied'): return 'b3_already_applied'
    if q.get('member_code_used') or any(order.get(k) is not None and order.get(k) is not False for k in conflicts):
        return 'overlapping_trial'
    return None


def negotiate(run, md, order, offer, q, steps, shop, sku, *args, **kwargs):
    """Only first unchanged machine ask can replace the baseline ladder, at its real final-wire boundary."""
    n=order.get(KEY)
    if not isinstance(n,dict) or n.get('arm') not in ('A','B'):
        return run(*args,**kwargs)
    budget=args[2]
    why=reason(order,offer,q,steps,shop,sku,budget,kwargs.get('resume'),kwargs.get('take_at'))
    n['fallback']=why or 'arm_A'
    if why or not _active(md,order) or n['arm'] != 'B':
        return run(*args,**kwargs)
    ladder=kwargs.get('ladder')
    old_guard=kwargs.get('decision_guard')
    pending={}
    def wrapped(meta,text,history):
        baseline=ladder(meta,text,history) if ladder else None
        pending.clear()
        if (not history and meta == q['ask'] and text == buyer.message_text(q['task'])
                and _active(md,order)
                and reason(order,offer,q,steps,shop,sku,budget,kwargs.get('resume'),kwargs.get('take_at')) is None
                and isinstance(baseline,dict) and baseline.get('action')=='offer'
                and baseline.get('price') == min(int(q['ask']*.9),budget,q['ask'])):
            pending.update(baseline=baseline)
            return dict(shop_price=meta,action='offer',price=meta)
        n['fallback']='turn_changed_or_not_baseline'
        return baseline
    def guard(d, history):
        # Preserve any pre-existing veto/price reduction; the new branch may not override it.
        checked=old_guard(d,history) if old_guard else d
        if not pending: return checked
        baseline=pending.pop('baseline')
        if (checked != d or history or not _active(md,order)
                or reason(order,offer,q,steps,shop,sku,budget,kwargs.get('resume'),kwargs.get('take_at')) is not None):
            n['fallback']='final_guard_changed'
            if not isinstance(checked,dict) or checked.get('action')!='offer': return checked
            return dict(baseline,price=min(baseline['price'],checked['price']))
        intent=dict(n,intent=True,ask=q['ask'],limit=budget,sku=sku,shop=shop['name'],
                    quote_task=q['task']['id'],list_price=offer['list_price'],baseline=baseline['price'],
                    wire_price=d['price'],fallback=None)
        try:
            _journal(md,n,'intent',order['order'],intent)
            if not _active(md,order):
                n['fallback']='expired_after_intent'
                return baseline
        except (OSError,ValueError,TypeError,RecursionError):
            n['fallback']='intent_failure'
            return baseline
        n.update(intent,applied=True)  # candidate delivered to send, not a confirmed server ACK/payment
        return d
    out=run(*args,**dict(kwargs,ladder=wrapped,decision_guard=guard))
    n['wire_history']=[list(h) for h in out.get('history',[]) if h[0]=='you offered']
    n['rpc_state']=out.get('state')
    rec=out.get('receipt')
    n['receipt_witness']={k:rec.get(k) for k in ('item','price','warranty_hours')} if isinstance(rec,dict) else None
    return out
