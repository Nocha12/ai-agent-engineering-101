"""Run scripts/round4/agent_kit.py unchanged, plus local operating records the kit does not keep.

run_round4.sh starts the kit through this file with the same arguments:
    python submissions/26622007/round-4/ops/agent_kit_records.py run --dir ... --market ... --name ...
It wraps three kit methods from outside (the kit file is not edited) and writes, under <dir>/logs/:
  messages.jsonl   every message the agent receives (A2A, mini app) as the sender wrote it, and every answer sent
                   with reply or by the a2a handler tool without the agent (kit: Mind.deliver, Mind.reply, the a2a
                   handler's Toolbox.call; a message the handler passed on appears twice, the handler's row has
                   'via'). Not here: the kit's own 'busy' answer after the A2A wait and app_say broadcasts.
  decisions.jsonl  the full arguments and result of each buying/pricing tool call (best_sellers, price_ceiling,
                   buy_food_lot, ...), so a refused wholesale quote keeps its total, ceiling and units
and it lifts the kit's 20 MB roll-over of logs/agent-kit.log (market_kit.LOG_MAX), which overwrote the older half.
A contact a customer left for a restock notice (restock_shoutout op contact, kept in <dir>/waitlist.json only): the
contact argument of that step is always written as [연락처] in logs/react.jsonl. When the value is a name the tool
kept (the call answered saved; not the customer's own market name, a SKU id or a word these records are made of), it is also masked for the next 15
minutes in everything this process logs (react.jsonl via Mind.step_log, agent-kit.log via log, messages.jsonl,
decisions.jsonl) and blanked in place, same length, in the last 256 KB of react.jsonl, agent-kit.log, messages.jsonl
and lost_deals.jsonl (the customer's own message and the steps that read it came before the tool call).
Both files hold customer text: they stay on this machine (.gitignore) and out of the submission copy
(ops/export_submission.py keeps only the three logs the course asks for). A record that fails to write never stops
the kit.
"""
import json
from contextvars import ContextVar
from dataclasses import dataclass
from base64 import urlsafe_b64encode
import re
import sys
import threading
import time
from pathlib import Path

DECISION_TOOLS = ('best_sellers', 'price_ceiling', 'buy_food_lot', 'food_candidates', 'stock_research')
TEXT_MAX = 8000
MASK = '[연락처]'
MASK_SECONDS = 15 * 60          # a contact is masked in new records this long after the customer gave it
SCRUB_TAIL = 256 << 10          # and blanked in the last bytes already written (the same conversation)
SCRUBBED = ('react.jsonl', 'agent-kit.log', 'messages.jsonl', 'lost_deals.jsonl')
NO_ROLLOVER = 1 << 40
_lock = threading.Lock()


def _dir_arg(argv):
    for i, a in enumerate(argv):
        if a == '--dir' and i + 1 < len(argv):
            return Path(argv[i + 1]).expanduser().resolve()
        if a.startswith('--dir='):
            return Path(a.split('=', 1)[1]).expanduser().resolve()
    return Path('.').resolve()


def _clip(v):
    if isinstance(v, str):
        return v[:TEXT_MAX]
    try:
        s = json.dumps(v, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(v)[:TEXT_MAX]
    return v if len(s) <= TEXT_MAX else s[:TEXT_MAX]


SKU_ID = re.compile(r'G[0-9a-f]{8}')                                    # the market's SKU ids are not names
HANDLE_OK = re.compile(r'[가-힣]{2,6}(-[0-9a-f]{4})?|[A-Za-z][A-Za-z0-9_-]{2,29}')    # what restock_shoutout takes as a contact
# words these records are made of: a "contact" equal to one of them (or to a tool's name) masks only its own argument
RESERVED = frozenset((
    'text data from kind task state dir via id ts in out a2a human thought action args observation op sku name '
    'customer contact order unit price prices grade qty ok result error say note want saved true false null none '
    'sleep reply forge_tool read_tool write_note reason item by wanted mind kit forge market maroo tunnel app sent '
    'completed rejected input_required tool quote total units cost floor count items more status response').split())


def _handle(v):
    v = str(v or '').strip().lstrip('@').strip()
    return v[:-1].strip() if v.endswith('님') else v


def _pattern(v, as_bytes=False):
    """v where it stands as a word of its own; never a JSON key (followed by '":').
    as_bytes is for the files, where a JSON string holds escapes: in "hi\noah" the n belongs to the \n (no "noah"
    there, and the backslash must stay), and in "\nyms979" the word starts right after the escape."""
    if not v.isascii():
        pat = re.escape(v)
    elif as_bytes:
        pat = r'(?:(?<=\\[nrtbf])|(?<![A-Za-z0-9_\\-]))' + re.escape(v) + '(?![A-Za-z0-9_-])'
    else:
        pat = '(?<![A-Za-z0-9_-])' + re.escape(v) + '(?![A-Za-z0-9_-])'
    pat += r'(?!"\s*:)'
    return re.compile(pat.encode('utf-8')) if as_bytes else re.compile(pat)


class Contacts:
    """Contact values to keep out of the records for the minutes of the conversation that gave them."""

    def __init__(self):
        self.live = {}                          # value -> (pattern, until)
        self.lock = threading.Lock()            # the mind, the A2A handlers and the kit's own threads all log

    def add(self, args, out, tools=()):
        """A restock_shoutout {op: contact} call and its answer. Returns the value when the tool kept it as a contact
        and the records should be searched for it: a value the tool refused is an ordinary word (재고없음, 콩나물)."""
        if not isinstance(args, dict) or args.get('op') != 'contact':
            return None
        res = out.get('result') if isinstance(out, dict) else None
        if not (isinstance(res, dict) and res.get('ok') is True and res.get('saved') is True):
            return None
        v = _handle(args.get('contact'))
        if (not HANDLE_OK.fullmatch(v) or SKU_ID.fullmatch(v) or v.lower() in RESERVED or v in tools
                or v in str(args.get('customer') or '')):
            return None                         # not a name, a word of the records, or the customer's own name
        with self.lock:
            self.live[v] = (_pattern(v), time.time() + MASK_SECONDS)
        return v

    def mask(self, x):
        now = time.time()
        with self.lock:
            self.live = {v: p for v, p in self.live.items() if p[1] > now}
            pats = [p for _, (p, _) in sorted(self.live.items(), key=lambda i: -len(i[0]))]
        if not pats:
            return x

        def go(v):
            if isinstance(v, str):
                for p in pats:
                    v = p.sub(MASK, v)
                return v
            if isinstance(v, dict):
                return {k: go(i) for k, i in v.items()}
            if isinstance(v, (list, tuple)):
                return [go(i) for i in v]
            return v
        return go(x)

    def step(self, entry):
        """A react.jsonl step: the contact argument itself is always masked, whatever it holds."""
        if (isinstance(entry, dict) and entry.get('action') == 'restock_shoutout' and isinstance(entry.get('args'), dict)
                and 'contact' in entry['args']):
            entry = dict(entry, args=dict(entry['args'], contact=MASK))
        return self.mask(entry)


def _scrub(path, value):
    """Blank a contact in what was written before we knew it was one (the customer's message, the step that read it).
    In place and same length ('*' for each byte) in the file's last SCRUB_TAIL bytes: the kit appends to these files
    from other threads, so nothing is rewritten, moved or shortened."""
    try:
        with _lock:
            if not path.is_file():
                return
            size = path.stat().st_size
            start = max(0, size - SCRUB_TAIL)
            with open(path, 'r+b') as f:
                f.seek(start)
                tail = f.read(size - start)
                for m in _pattern(value, as_bytes=True).finditer(tail):
                    f.seek(start + m.start())
                    f.write(b'*' * (m.end() - m.start()))
    except Exception:
        pass


def _writer(logs, contacts=None):
    def write(name, row):
        try:
            if contacts is not None:
                row = contacts.mask(row)
            line = json.dumps(dict(row, ts=round(time.time(), 3)), ensure_ascii=False, default=str)
            with _lock, open(logs / name, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:                       # a record never stops the kit
            pass
    return write


HELD_UNIT = re.compile(r'u-[A-Za-z0-9_-]{1,40}')
HELD_TEXT = ('이 단위는 다른 문의에 낸 결제서로 잠시(최대 5분) 잡혀 있고, 같은 품목·등급의 다른 단위는 이 가격으로 낼 수 없습니다. '
             '손님께는 그 단위를 지금 낼 수 없다고만 알립니다. ')


def _view(e):
    try:
        return json.dumps(e.view(), ensure_ascii=False)
    except Exception:
        return '\x00'                                   # never found: re-added


def _fit_view(v, limit):
    """An event view under limit characters: the oldest history entries go first, then the text is cut."""
    v = dict(v)
    while len(json.dumps(v, ensure_ascii=False, default=str)) > limit and v.get('earlier_in_this_conversation'):
        v['earlier_in_this_conversation'] = v['earlier_in_this_conversation'][1:]
    if len(json.dumps(v, ensure_ascii=False, default=str)) > limit and isinstance(v.get('text'), str):
        v['text'] = v['text'][:max(200, limit - len(json.dumps(dict(v, text=''), ensure_ascii=False, default=str)))]
    return v


def held_result(r):
    """private_checkout create answered code unit_held (or reissue_wait): pass only checked fields (no tool text) to
    the model so it can offer a free unit of the same item at that unit's floor or more. None for any other result."""
    if isinstance(r, dict) and r.get('code') == 'no_quote':
        return {'ok': False, 'code': 'no_quote',
                'error': '이 품목·등급 안내가가 아직 없습니다. 가격을 정해 price로 넘기거나 check_item_inventory {sku, quote}로 먼저 정합니다'}
    if isinstance(r, dict) and r.get('code') == 'reissue_wait':
        until = r.get('held_until')
        return {'ok': False, 'code': 'reissue_wait', 'held_until': until if type(until) is int else None,
                'error': '이 문의에서 같은 단위를 15분 넘게 다시 발급해 잠시 멈췄습니다. 이미 보낸 결제서는 그대로 쓸 수 있습니다. '
                         'held_until 전에는 다시 발급하지 않습니다.'}
    if not isinstance(r, dict) or r.get('code') != 'unit_held' or not isinstance(r.get('alternatives'), list):
        return None
    alts = []
    for x in r['alternatives'][:6]:
        if not isinstance(x, dict):
            continue
        unit, grade, floor, hours = x.get('unit'), x.get('grade'), x.get('floor'), x.get('hours_left')
        if (isinstance(unit, str) and HELD_UNIT.fullmatch(unit) and grade in ('A', 'B', 'C')
                and type(floor) is int and floor > 0
                and (hours is None or (type(hours) in (int, float) and hours == hours and abs(hours) < 1e6))):
            alts.append({'unit': unit, 'grade': grade, 'floor': floor, 'hours_left': hours})
    until = r.get('held_until')
    return {'ok': False, 'code': 'unit_held', 'held_until': until if type(until) is int else None, 'alternatives': alts,
            'error': HELD_TEXT + ('손님이 원하면 alternatives의 단위를 그 하한가(floor) 이상으로 다시 제안하고, 손님이 받아들이면 그 단위로 '
                                  '결제서를 냅니다.' if alts else '같은 품목의 다른 재고는 없습니다. held_until 뒤에 다시 확인합니다.')}


def _task_order(path, task, issued):
    """The first of task, task-2, task-3, ... not yet used by a checkout of this conversation."""
    used = {getattr(c, 'order', None) for c in issued}
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                if task in line:
                    try:
                        used.add(str(json.loads(line).get('order')))
                    except (ValueError, AttributeError):
                        pass
    except OSError:
        pass
    n = 1                                       # used is finite, so this always ends
    while (task if n == 1 else f'{task}-{n}') in used:
        n += 1
    return task if n == 1 else f'{task}-{n}'


def _menu_hook(ak, shop_dir):
    """Start ops/forum_menu.py's thread once, after the kit has joined the market as the shop (it needs that token).
    Any failure to start leaves the kit as it was."""
    join = ak.mk.Market.join
    started = []

    def join_rec(self, role, *args, **kwargs):
        out = join(self, role, *args, **kwargs)
        if role == 'shop' and not started:
            started.append(True)
            try:
                import forum_menu
                forum_menu.start(shop_dir, self, shop_dir / 'tools' / 'restock_shoutout' / 'logic.py')
            except Exception as e:                      # noqa: BLE001  the shop runs without its menu
                print(f'[forum_menu] not started: {type(e).__name__}: {e}', flush=True)
        return out

    ak.mk.Market.join = join_rec


def install(ak, logs, checkout=False):
    """Wrap ak.Mind.deliver/reply and ak.Toolbox.call; lift ak.mk.LOG_MAX. Returns the names that were wrapped."""
    logs.mkdir(parents=True, exist_ok=True)
    contacts = Contacts()
    write = _writer(logs, contacts)
    done = []
    active_mind = threading.local()
    if checkout:
        from a2a.server import request_handlers, tasks as a2a_tasks
        from a2a.helpers import get_message_text
        from a2a.types import Task
        from checkout_delivery import Checkout, clean_reply, compose, grade_note, tokens, JWT, SIGNED, ORDER, HOLD_SECONDS
        http_request = ContextVar('checkout_http', default=None)
        inquiry = ContextVar('checkout_inquiry', default=None)
        a2a_event = ContextVar('checkout_a2a_event', default=None)
        owners = {}

        run_once = ak.Mind.run_once
        def run_rec(self, events):
            active_mind.current = self
            try:
                return run_once(self, events)
            finally:
                active_mind.current = None

        ak.Mind.run_once = run_rec
        ak.FORMAT += (
            "\nFor private_checkout create, pass reply_to: the current waiting synchronous A2A message id. The wrapper sets "
            "task to this inquiry's A2A task id (the buyer checks that task) and order to that id (id-2, id-3 for "
            "further checkouts), so do not write a description there. Checkout first: most customers send one message only and "
            "leave after an answer without a checkout. When the customer asks for a signed checkout (결제서, checkout, 결제해 주세요) or says "
            "they want to buy, and a unit you hold fits what they asked for (item, size, conditions they stated), create the "
            "checkout in this same reply at the shown price, then say its grade, time left and price; do not ask for another "
            "confirmation first. Hold back only when the customer said not to issue yet, or no held unit meets a condition "
            "they stated. A stock menu is posted on the forum for you about every 5 minutes (ops/forum_menu.py); use forum_post "
            "only for replies and threads, not for stock or price lists. Make a checkout only when the customer wants it and the agreed conditions "
            "are met. The actual reply text includes the matching signed originals automatically; do not copy "
            "or invent tokens. Put only customer-facing prose in an issuing reply (no tool data or dumps). On explicit customer refusal or your explicit decision to cancel issuance, "
            "pass checkout_cancel: true (all) or a list of order ids (only those) in reply. "
            "A reply state alone or a sentence about payment does not cancel issuance. "
            "When private_checkout answers grade_evidence: attached, the reply normally also carries a fixed grade-evidence "
            "line (the market-signed food certificate has grade null; food grade is the shelf life left, which that "
            "certificate states; a very long reply drops the line). Point to that line; do not claim the market verified the grade."
        )

        BaseUpdater = a2a_tasks.TaskUpdater

        def without_checkout(value):
            if isinstance(value, str):
                return JWT.sub('[checkout]', value)
            if isinstance(value, list):
                return [without_checkout(x) for x in value]
            if isinstance(value, dict):
                return {without_checkout(k): without_checkout(v) for k, v in value.items()}
            return value

        @dataclass
        class Inquiry:
            open: bool = True
            answer: object = None

        class DeliveryUpdater(BaseUpdater):
            async def update_status(self, state, message=None, *args, **kwargs):
                ev = a2a_event.get()
                req = inquiry.get()
                try:
                    # The unmodified status enters the SDK queue, plugins and task store.
                    result = await super().update_status(state, message, *args, **kwargs)
                    plan = getattr(ev, 'checkout_plan', None)
                    if (req and req.open and ev is not None and ev.checkout_request is req
                            and ev.task == self.task_id and plan and message is not None
                            and not self.event_queue.is_closed()
                            and get_message_text(message) == ev.answer['reply']):
                        req.answer = (ev.task, ev.id, message.message_id, ev.answer['reply'], *plan)
                    return result
                finally:
                    if ev is not None and ev.task == self.task_id:
                        ev.checkout_plan = None
                        ev.checkout_issued = ()
                        a2a_event.set(None)

        class DeliveryHandler(request_handlers.DefaultRequestHandler):
            async def on_message_send(self, params, context):
                http = http_request.get()
                if http is None or params.configuration.return_immediately:
                    return await super().on_message_send(params, context)
                req = Inquiry()
                previous = inquiry.set(req)
                try:
                    result = await super().on_message_send(params, context)
                    plan = req.answer
                    if (plan and isinstance(result, Task) and result.id == plan[0]
                            and result.status.message.message_id == plan[2]):
                        http['answer'] = plan
                    return result
                finally:
                    req.open = False
                    req.answer = None
                    inquiry.reset(previous)

        class CheckoutResponse:
            """Only the initiating JSON-RPC HTTP response gets the private attachment."""
            def __init__(self, app):
                self.app = app

            async def __call__(self, scope, receive, send):
                if not (scope['type'] == 'http' and scope['method'] == 'POST' and scope['path'] == '/'):
                    return await self.app(scope, receive, send)
                http = {}
                previous = http_request.set(http)
                start = None

                async def send_response(event):
                    nonlocal start
                    if event['type'] == 'http.response.start' and http.get('answer') and event['status'] == 200:
                        # The pinned SDK returns a single JSONResponse, not a stream.
                        start = event
                        return
                    if start is not None:
                        header, start = start, None
                        emitted, waived = (), ()
                        task, msg_id, status_id, baseline, issued, cancelled = http.pop('answer')
                        if event['type'] == 'http.response.body' and not event.get('more_body', False):
                            try:
                                response = json.loads(event['body'])
                                envelope = response['result']
                                result = envelope.get('task', envelope)
                                message = result['status']['message']
                                part, = message['parts']
                                # Both A2A JSON bindings use camelCase messageId and a text part.
                                if (result['id'] == task and message['messageId'] == status_id
                                        and part.get('text') == baseline):
                                    body, attached = compose(baseline, issued, cancelled, time.time())
                                    part['text'] = body
                                    raw = json.dumps(response, ensure_ascii=False, separators=(',', ':')).encode()
                                    serialized = json.loads(raw)['result']
                                    checks = tokens(serialized.get('task', serialized)['status']['message']['parts'][0]['text'])
                                    emitted = tuple(c for c in attached if c.token in checks)
                                    waived = tuple(c for c in issued if c.order in cancelled)
                                    headers = [(k, v) for k, v in header['headers']
                                               if k.lower() not in (b'content-length', b'cache-control', b'etag')]
                                    headers.extend([(b'content-length', str(len(raw)).encode()),
                                                    (b'cache-control', b'no-store')])
                                    header = dict(header, headers=headers)
                                    event = dict(event, body=raw)
                            except (TypeError, ValueError, KeyError):
                                # Preserve the SDK answer but do not record a delivery on shape mismatch.
                                pass
                        await send(header)
                        await send(event)
                        for c in emitted:               # the grade line as sent (operator 11/26 12:40), '' when dropped
                            write('checkout-sends.jsonl', {'kind': 'checkout_asgi_handoff', 'task': task, 'message_id': msg_id,
                                                           'order': c.order, 'checkout_hash': c.digest,
                                                           'grade_line': c.note if c.note and c.note in body else ''})
                        for c in waived:
                            write('checkouts.jsonl', {'event': 'delivery_waived', 'task': task, 'message_id': msg_id,
                                                      'order': c.order, 'digest_hex': c.digest})
                    else:
                        await send(event)

                try:
                    return await self.app(scope, receive, send_response)
                finally:
                    http.clear()
                    http_request.reset(previous)

        # The kit imports these classes locally inside agent_app; inject only while that app is built.
        agent_app = ak.agent_app
        def checkout_app(*args, **kwargs):
            updater, handler = a2a_tasks.TaskUpdater, request_handlers.DefaultRequestHandler
            a2a_tasks.TaskUpdater, request_handlers.DefaultRequestHandler = DeliveryUpdater, DeliveryHandler
            try:
                app = agent_app(*args, **kwargs)
            finally:
                a2a_tasks.TaskUpdater, request_handlers.DefaultRequestHandler = updater, handler
            app.add_middleware(CheckoutResponse)
            return app
        ak.agent_app = checkout_app

        # Fail startup if the watch cannot record its activation boundary.
        with open(logs / 'checkouts.jsonl', 'a', encoding='utf-8') as f:
            f.write(json.dumps({'event': 'delivery_tracking_started', 'ts': time.time()}) + '\n')

    if checkout and hasattr(ak.mk, 'Market'):
        _menu_hook(ak, logs.parent)
        done.append('forum_menu')

    if hasattr(ak.mk, 'LOG_MAX'):
        ak.mk.LOG_MAX = NO_ROLLOVER
        done.append('LOG_MAX')

    deliver = ak.Mind.deliver

    def deliver_rec(self, ev):
        if checkout and ev.kind == 'a2a':
            ev.checkout_request = inquiry.get()
            a2a_event.set(ev)
        try:
            write('messages.jsonl', {'dir': 'in', 'id': getattr(ev, 'id', None), 'kind': getattr(ev, 'kind', None),
                                     'from': _clip(getattr(ev, 'frm', None)), 'text': _clip(getattr(ev, 'text', None)),
                                     'data': _clip(getattr(ev, 'data', None)), 'task': getattr(ev, 'task', None)})
        except Exception:
            pass
        return deliver(self, ev)

    reply = ak.Mind.reply

    def reply_rec(self, args):
        try:
            ev = self.pending.get(str((args or {}).get('to')))
        except Exception:
            ev = None
        issued = getattr(ev, 'checkout_issued', ()) if checkout else ()
        effective = args
        if issued and not ev.done.is_set():
            setting = args.get('checkout_cancel')
            orders = {c.order for c in issued}
            if setting is True:
                cancelled = orders
            elif isinstance(setting, list) and all(isinstance(o, str) and o in orders for o in setting):
                cancelled = set(setting)
            elif setting is None or setting is False:
                cancelled = set()
            else:
                return 'error: checkout_cancel must be true or a list of order ids issued for this message'
            try:
                compose(str(args.get('text') or ''), issued, cancelled, time.time())
            except ValueError as e:
                return f'error: {e}'
            ev.checkout_plan = (issued, cancelled)
        if checkout and ev is not None and ev.kind == 'a2a':
            body = str(args.get('text') or '')
            effective = dict(args, text=clean_reply(body) if issued else without_checkout(body),
                             data=None if issued else without_checkout(args.get('data')))
        out = reply(self, effective)
        if checkout and out != 'sent' and ev is not None:
            ev.checkout_plan = None
        try:
            if out == 'sent':
                body = (effective or {}).get('text')
                if issued:
                    body = JWT.sub('[checkout]', str(body or ''))
                write('messages.jsonl', {'dir': 'out', 'id': (args or {}).get('to'), 'kind': getattr(ev, 'kind', None),
                                         'task': getattr(ev, 'task', None), 'text': _clip(body),
                                         'data': _clip((effective or {}).get('data')), 'state': (args or {}).get('state')})
        except Exception:
            pass
        return out

    # Messages that came during a step are added after its tool result and the whole is cut at OBS_LIMIT; a long
    # result cut them off, and fresh() had already taken them from the queue, so the model never saw them and the
    # kit's busy answer went out (11/26 10:01 bread 11ae3b1e, 11:24 rice ea45d5f0). Before each model call, any message
    # taken this run whose id is not in what the model reads is added back to the last observation, once.
    seen = threading.local()
    model = getattr(ak, 'Model', None)
    patch_seen = all(callable(getattr(c, n, None)) for c, n in ((ak.Mind, 'fresh'), (ak.Mind, 'run_once'), (model, 'complete')))
    fresh0, run_once0, complete0 = (ak.Mind.fresh, ak.Mind.run_once, model.complete) if patch_seen else (None,) * 3

    def fresh_rec(self):
        out = fresh0(self)
        if out and getattr(seen, 'mind', None) is self:
            seen.taken.extend(out)
        return out

    def run_seen(self, events):
        seen.mind, seen.taken, seen.readded = self, list(events or []), set()   # the run's first prompt is cut too (r5 M2)
        try:
            return run_once0(self, events)
        finally:
            left, seen.mind, seen.taken = seen.taken, None, []
            for e in left:                              # taken in the run's last step, no model call after it (r2 I-4):
                try:                                    # back to the queue, so the next run lists them
                    if not e.done.is_set():
                        self.inbox.put(e)
                except Exception:
                    pass

    def complete_rec(self, messages, *a, **k):
        try:
            taken = getattr(seen, 'taken', None)
            if taken and messages and messages[-1].get('role') == 'user':
                last = ' '.join(str(m.get('content')) for m in messages[-2:] if m.get('role') == 'user')   # 'almost over' nudge
                # seen only when the whole view is there (the kit dumps each the same way): a cut inside the text or
                # its data (a payment_mandate) counts as lost (review r3 I2)
                lost = [e for e in taken if not e.done.is_set() and e.id not in seen.readded and _view(e) not in last]
                views = []
                for e in lost:                          # the kit's own view, the oldest history going first if too long
                    try:
                        views.append(_fit_view(e.view(), 4000))
                        seen.readded.add(e.id)          # once a run, also when a model call fails after (r4 M1)
                    except Exception:
                        pass                            # one odd event never holds back the others (r4 M2)
                lost = [e for e in lost if e.id in seen.readded]
                if views:
                    messages[-1] = dict(messages[-1], content=messages[-1]['content']
                                        + "\n\nNew messages waiting for you (answer with reply):\n" + json.dumps(views, ensure_ascii=False))
                    write('missed-messages.jsonl', {'kind': 'readded_after_cut', 'ids': [e.id for e in lost],
                                                    'tasks': [e.task for e in lost]})
        except Exception:
            pass
        out = complete0(self, messages, *a, **k)
        seen.taken = []                                 # only once the model got them; a failed call keeps them (r3 M1)
        return out

    if patch_seen:
        ak.Mind.fresh, ak.Mind.run_once, model.complete = fresh_rec, run_seen, complete_rec
        done += ['Mind.fresh', 'Mind.run_once', 'Model.complete']

    call = ak.Toolbox.call

    def call_rec(self, name, args):
        ev = None
        if checkout and name == 'private_checkout' and isinstance(args, dict) and args.get('op') == 'create':
            mind = getattr(active_mind, 'current', None)
            ev = mind.pending.get(str(args.get('reply_to'))) if mind is not None else None
            req = getattr(ev, 'checkout_request', None)
            if ev is None or ev.kind != 'a2a' or not ev.task or ev.done.is_set() or not req or not req.open:
                return {'ok': False, 'error': 'private checkout needs reply_to of a live synchronous A2A message'}
            # The signed checkout's task is what the buyer's kit matches against its receipt: the A2A task id of
            # this inquiry (market_kit receipt_from_task falls back to task.id). A description from the model there
            # makes the buyer refuse ("not signed by the shop"), 11/26 09:32-15:50. The order is that task id too;
            # a further checkout in the same conversation gets -2, -3, ... (also across messages and restarts).
            order = _task_order(logs / 'checkouts.jsonl', str(ev.task), getattr(ev, 'checkout_issued', ()))
            if not ORDER.fullmatch(order):
                order = None                    # not an order id: the tool makes its own o-..., as before
            # The hold owner is the live inquiry's A2A task, never a value the model passed.
            args = dict({k: v for k, v in args.items() if k not in ('reply_to', 'inquiry_task', 'task', 'order')},
                        inquiry_task=ev.task, task=str(ev.task), **({'order': order} if order else {}))
        if ev is not None:
            try:
                out = call(self, name, args)
            except Exception:
                return {'ok': False, 'error': 'checkout tool could not finish; do not automatically reissue'}
            r = out.get('result') if isinstance(out, dict) and out.get('ok') is True else None
            if not isinstance(r, dict) or r.get('ok') is not True:
                held = held_result(r)
                if held is not None:
                    return held
                # Raw stdout, tracebacks, and tool-returned errors are not customer-facing text.
                return {'ok': False, 'error': 'checkout tool did not confirm issuance; do not automatically reissue'}
            token, order = r.get('checkout_jwt'), r.get('order')
            payload = r.get('payload') or {}
            at = payload.get('iat') if isinstance(payload, dict) else None
            if not (isinstance(token, str) and SIGNED.fullmatch(token) and isinstance(order, str)
                    and ORDER.fullmatch(order) and type(at) is int):
                return {'ok': False, 'error': 'invalid checkout metadata; do not automatically reissue'}
            c = Checkout(order, token, at, grade_note(order, r.get('grade_evidence'), time.time()))
            wallet_digest = urlsafe_b64encode(bytes.fromhex(c.digest)).decode().rstrip('=')
            if r.get('checkout_hash') != wallet_digest:
                return {'ok': False, 'error': 'inconsistent checkout digest; do not automatically reissue'}
            now = time.time()
            for digest, (expiry, _) in list(owners.items()):
                if expiry <= now:
                    del owners[digest]
            owner_key = (ev.task, ev.id)
            if c.digest in owners and owners[c.digest][1] != owner_key:
                return {'ok': False, 'error': 'this checkout digest is already bound to a different inquiry'}
            owners[c.digest] = (c.at + HOLD_SECONDS, owner_key)
            live = req.open and not ev.done.is_set() and 0 <= now - c.at < HOLD_SECONDS
            issued = getattr(ev, 'checkout_issued', ())
            if not live or not any(old.digest == c.digest for old in issued):
                # Keep the journal binding even if no HTTP response is possible: it must alert.
                write('checkouts.jsonl', {'event': 'delivery_bound', 'task': ev.task, 'message_id': ev.id,
                                          'order': c.order, 'digest_hex': c.digest, 'issued_at': c.at,
                                          'can_attach_at_issue_return': live})
            if not live or not req.open or ev.done.is_set():
                return {'ok': False, 'error': 'issued but inquiry no longer deliverable; do not reissue'}
            if not any(old.digest == c.digest for old in issued):
                ev.checkout_issued = (*issued, c)
            kept = next((old for old in ev.checkout_issued if old.digest == c.digest), c)   # a reissue keeps the first
            res = dict({'ok': True, 'order': order}, **({'grade_evidence': 'attached'} if kept.note else {}))
            q = r.get('quote')
            if type(q) is int and 0 < q < 10 ** 9:      # the shown price for this SKU and grade (operator 11/26 12:40)
                res['quote'] = q
                if type(payload.get('price')) is int and payload['price'] != q:
                    res['quote_note'] = (f'이 품목·등급 안내가는 {q:,}입니다. 이번 결제서는 합의한 {payload["price"]:,}로 냈고, '
                                         f'다음 안내는 {q:,}로 합니다')
            return {'ok': True, 'result': res}
        out = call(self, name, args)
        try:                                    # before the kit logs this step (step_log and log come after the call)
            new = contacts.add(args, out, tuple(getattr(self, 'tools', None) or ())) if name == 'restock_shoutout' else None
            for f in (SCRUBBED if new else ()):
                _scrub(logs / f, new)
        except Exception:
            pass
        try:
            _record_call(self, name, args, out)
        except Exception:
            pass
        return out

    def _record_call(self, name, args, out):
        try:
            handles = (self.tools.get(name) or {}).get('perm', {}).get('handles') or []
        except Exception:
            handles = []
        if 'a2a' in handles:
            a = args or {}
            r = out.get('result') if isinstance(out, dict) and out.get('ok') else None
            write('messages.jsonl', {'dir': 'in', 'via': name, 'kind': 'a2a', 'from': _clip(a.get('claimed_from')),
                                     'text': _clip(a.get('text')), 'data': _clip(a.get('data')), 'task': a.get('task_id')})
            if isinstance(r, dict) and not r.get('defer') and (r.get('reply') or r.get('data') is not None):
                write('messages.jsonl', {'dir': 'out', 'via': name, 'kind': 'a2a', 'task': a.get('task_id'),
                                         'text': _clip(r.get('reply')), 'data': _clip(r.get('data')), 'state': r.get('state')})
        if name in DECISION_TOOLS:
            # the kit's answer is {ok: the run worked, result: what the tool returned (its own ok/error inside)}
            res = out.get('result') if isinstance(out, dict) else None
            tool_ok = res.get('ok') if isinstance(res, dict) and 'ok' in res else (isinstance(out, dict) and out.get('ok'))
            err = (res.get('error') if isinstance(res, dict) else None) or (out.get('error') if isinstance(out, dict) else None)
            write('decisions.jsonl', {'tool': name, 'args': _clip(args), 'ok': bool(tool_ok), 'error': _clip(err),
                                      'quote_refused': name == 'buy_food_lot' and isinstance(res, dict)
                                      and 'total' in res and not res.get('ok') and not res.get('tx_hash'),
                                      'result': _clip(res if res is not None else out)})

    ak.Mind.deliver, ak.Mind.reply, ak.Toolbox.call = deliver_rec, reply_rec, call_rec
    done += ['Mind.deliver', 'Mind.reply', 'Toolbox.call']

    def safe(x):                                # masking never stops the kit: on any error the kit's own value goes on
        try:
            return contacts.mask(x)
        except Exception:
            return x

    def safe_step(entry):
        try:
            if checkout and entry.get('action') == 'reply':
                entry = without_checkout(entry)
            return contacts.step(entry)
        except Exception:
            return entry

    if hasattr(ak.Mind, 'step_log'):            # logs/react.jsonl: the step is kept, the contact is not
        step_log = ak.Mind.step_log
        ak.Mind.step_log = lambda self, entry: step_log(self, safe_step(entry))
        done.append('Mind.step_log')
    if callable(getattr(ak, 'log', None)):      # logs/agent-kit.log and the terminal: "[mind] tool | thought"
        log = ak.log
        ak.log = lambda line='': log(safe(line))
        done.append('log')
    return done


def main():
    kit_dir = Path(__file__).resolve().parents[4] / 'scripts' / 'round4'     # round-4/ops -> repo root
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(kit_dir))
    import agent_kit as ak                                   # noqa: E402
    if sys.argv[1:2] == ['run']:
        install(ak, _dir_arg(sys.argv[2:]) / 'logs', checkout=True)
    sys.argv[0] = str(kit_dir / 'agent_kit.py')
    ak.main()


if __name__ == '__main__':
    main()
