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
import copy
import json
import queue
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
PAR_MAX = 4                     # A2A conversations served at the same time, each in its own thread (대표님 11/27 00:55)
WORKER_NO = ('buy_food_lot', 'forum_post', 'write_shop_card', 'refresh_full_card')   # not from a customer's run (r1 I2, r2 M3)
REWRITE_ROOM = 25               # s: a reply this close to the A2A window goes with a line of the new values, not back
RECHECK_MAX = 3                 # items looked up again (each on its own, at the same time) right before a reply
RECHECK_BUDGET = 8              # s: look-ups not back by then count as no answer (the reply goes as written)
RECHECK_ROOM = 30               # s: a customer with less of the A2A window left than this gets the reply as written
STOCK_EVERY = 20                # s: a parallel run's live stock (check_item_inventory) is fetched again after this long
PAR_NOTE = ("\n\nThis run serves only the customer message(s) listed above; other customers are answered at the same "
            "time by other runs of you. Item, price and stock: say only what the 'Live stock now' list in this run (fetched "
            "just now, again every %d s) or a check_item_inventory call in this run shows, never a value from your notes "
            "or an earlier run. private_checkout looks the unit up live again and holds it; when it answers unit_held, "
            "offer what it lists instead. In this run do not buy stock, forge tools or post on the forum: reply, then "
            "sleep." % STOCK_EVERY)


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
             '손님께는 그 단위를 지금 낼 수 없다고 알립니다(다른 손님 이야기는 하지 않음). ')
HELD_TIME = ('손님이 원한 단위면 "그 단위는 {t}까지 예약돼 있고, 그때까지 결제되지 않으면 다시 안내드릴 수도 있다"고 함께 '
             '말합니다(약속하지 않음). ')
LONG_MIN = 12                   # h: a free unit shorter than this (or of grade C) is not 'longest' (C·임박, 떨이 line's 12 h)
HELD_LONG = ('손님이 C등급이나 기한 임박(12시간 미만이거나 떨이)을 빼 달라고 했으면 C·임박 단위를 먼저 내밀지 말고 longest'
             '(C도 떨이도 아니고 남은 기한이 가장 긴 단위)부터 안내합니다. ')
HELD_NO_LONG = ('alternatives(같은 품목의 빈 단위, 싼 순 최대 6개)에는 C·임박을 뺀 단위가 없습니다. C·임박을 빼 달라고 한 손님이면 '
                '아래 조회로 다른 단위가 있는지 확인한 뒤, 없으면 그렇다고 솔직히 알리고 원하면 C·임박 단위의 등급·기한·가격을 '
                '안내합니다. ')
HELD_LOOK = ('기한을 말하거나 longest보다 긴 단위를 찾을 때는 check_item_inventory {sku}의 until(인증서와 같은 분 단위 값)로 '
             '확인합니다(alternatives는 싼 순 최대 6개라 더 긴 단위가 그 조회에만 있을 수 있고, until_kst는 시 단위 참고값). '
             '그 조회는 잡힌 단위도 보여 주니 held_unit은 다시 내밀지 않고, 조회에서만 찾은 단위로 낸 결제서가 다시 unit_held면 '
             '그 단위도 잡혀 있는 것입니다. ')
HELD_CLEAR = ('떨이 단위(clearance true)의 가격은 check_item_inventory {sku}가 그 unit에 보여 주는 clearance.price(price 없이 낼 '
              '때 결제서에 찍히는 값)로 말하고 price 없이 냅니다. ')
KST = timezone(timedelta(hours=9))


def held_kst(until):
    """'MM/DD HH:MM KST' of an epoch-second held_until, rounded up to the minute (the hold's end, to the second), else None."""
    if type(until) is not int or not 1e9 < until < 1e11:
        return None
    return datetime.fromtimestamp(-(-until // 60) * 60, KST).strftime('%m/%d %H:%M KST')


def alt_until(hours, now):
    """'MM/DD HH시 KST' of a unit's expiry from its hours left (rounded to 0.1 h by the tool, so 0.05 h is taken off and
    only the hour is shown, as with_until's fallback: never later than the real one, no made-up minutes; a reference
    only, the minute to say is check_item_inventory's until), else None."""
    if type(hours) not in (int, float) or not 0.05 < hours < 1e5:
        return None
    return datetime.fromtimestamp(now + (hours - 0.05) * 3600, KST).strftime('%m/%d %H시 KST')


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


def held_result(r, now=None, wanted=None):
    """private_checkout create answered code unit_held (or reissue_wait): pass only checked fields (no tool text) to
    the model so it can offer a free unit of the same item at that unit's floor or more. None for any other result."""
    if isinstance(r, dict) and r.get('code') == 'no_quote':
        return {'ok': False, 'code': 'no_quote',
                'error': '이 품목·등급 안내가가 아직 없습니다. 가격을 정해 price로 넘기거나 check_item_inventory {sku, quote}로 먼저 정합니다'}
    if isinstance(r, dict) and r.get('code') == 'reissue_wait':
        until = r.get('held_until')
        return {'ok': False, 'code': 'reissue_wait', 'held_until': until if type(until) is int else None,
                'held_until_kst': held_kst(until),
                'error': '이 문의에서 같은 단위를 15분 넘게 다시 발급해 잠시 멈췄습니다. 이미 보낸 결제서는 그대로 쓸 수 있습니다. '
                         'held_until 전에는 다시 발급하지 않습니다.'}
    if not isinstance(r, dict) or r.get('code') != 'unit_held' or not isinstance(r.get('alternatives'), list):
        return None
    alts, now = [], time.time() if now is None else now
    for x in r['alternatives'][:6]:
        if not isinstance(x, dict):
            continue
        unit, grade, floor, hours = x.get('unit'), x.get('grade'), x.get('floor'), x.get('hours_left')
        if (isinstance(unit, str) and HELD_UNIT.fullmatch(unit) and grade in ('A', 'B', 'C')
                and type(floor) is int and floor > 0
                and (hours is None or (type(hours) in (int, float) and hours == hours and abs(hours) < 1e6))):
            cl = x.get('clearance')
            cp = cl.get('price') if isinstance(cl, dict) else None
            alts.append({'unit': unit, 'grade': grade, 'floor': floor, 'hours_left': hours,
                         'until_kst': alt_until(hours, now), 'clearance': type(cp) is int})
    until = r.get('held_until')
    # 메인 11/27 18:10 (7f2b4297, a57f3944, 33647847): a customer who leaves out C·임박 hears the longest-dated free unit
    # first, and when the unit they wanted is held, when that checkout ends. Order and floors unchanged (no new guard).
    good = [x for x in alts if x['grade'] != 'C' and not x['clearance']
            and (x['hours_left'] is None or x['hours_left'] >= LONG_MIN)]
    longest = max(good, key=lambda x: (x['hours_left'] is None, x['hours_left'] or 0, x['grade'] == 'A')) if good else None
    return {'ok': False, 'code': 'unit_held', 'held_until': until if type(until) is int else None,
            'held_until_kst': held_kst(until), 'alternatives': alts, 'longest': longest,
            'held_unit': wanted if isinstance(wanted, str) and HELD_UNIT.fullmatch(wanted) else None,
            'error': HELD_TEXT + (HELD_TIME.format(t=held_kst(until)) if held_kst(until) else '')
            + ((HELD_LONG if longest else HELD_NO_LONG) + HELD_LOOK if alts else '')
            + (HELD_CLEAR if any(x['clearance'] for x in alts) else '') + ('손님이 원하면 alternatives의 단위를 그 하한가(floor) 이상으로 다시 제안하고, 손님이 받아들이면 그 단위로 '
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


COMMON = {'정식수입', '국내산', '국산', '무항생제', '상온보관', '유기농', '당일수확', '산지직송', '무농약', '본사공식', '상등급'}
NAMES, _names_lock = {}, threading.Lock()      # sku -> shown name, from every answer that had names (all runs)
SIZE_RE = re.compile(r'\d+(?:\.\d+)?(?:kg|ml|g|l|개입|구|입|팩|개|병|판|캔|봉|펫)', re.I)


def told_from(res, by_id=False):
    """{sku: {'name', 'count', 'prices': {grade: price}}} from a check_item_inventory answer: the whole shop (skus; names
    left out when the shop is large) or one item ({sku}: count is the whole SKU, items are its first units with the
    shown name). Prices only where set: a first quote is not a change (review I2). Clearance prices are not read: they
    move with the clock, and private_checkout issues at the price of the moment by itself."""
    out = {}
    if not isinstance(res, dict) or res.get('ok') is False:
        return out
    for s in res.get('skus') or []:
        if isinstance(s, dict) and isinstance(s.get('sku'), str):
            g = s.get('grades') if isinstance(s.get('grades'), dict) else {}
            out[s['sku']] = {'name': s.get('name') if isinstance(s.get('name'), str) else None,
                             'count': s.get('count') if type(s.get('count')) is int else None,
                             'prices': {str(k): v['price'] for k, v in g.items()
                                        if isinstance(v, dict) and type(v.get('price')) is int}}
    asked = res.get('sku') if isinstance(res.get('sku'), str) else None
    items = [i for i in res.get('items') or [] if isinstance(i, dict) and isinstance(i.get('sku'), str)]
    one = asked is not None and all(i['sku'] == asked for i in items) and type(res.get('count')) is int
    for i in items:
        t = out.setdefault(i['sku'], {'name': None, 'count': res['count'] if one else None, 'prices': {}})
        if isinstance(i.get('name'), str):
            t['name'] = i['name']
        if type(i.get('price')) is int:
            t['prices'].setdefault(str(i.get('grade')), i['price'])
    if by_id and one and not items and res['count'] == 0:   # asked by its SKU id and none left (r2 M3, r3 I1)
        out.setdefault(asked, {'name': None, 'count': 0, 'prices': {}})
    with _names_lock:
        NAMES.update({k: v['name'] for k, v in out.items() if v['name']})
    return out


def merge_told(told, new):
    """Field by field: a whole-shop answer without names keeps the name an earlier answer gave (review r1 I1)."""
    for k, v in new.items():
        old = told.get(k) or {}
        told[k] = {'name': v.get('name') or old.get('name'),
                   'count': v['count'] if v.get('count') is not None else old.get('count'),   # by-name: none (r2 I1)
                   'prices': dict(old.get('prices') or {}, **(v.get('prices') or {}))}
    return told


def named_in(text, sku, name):
    """The reply speaks of this SKU: its id, its shown name as one word (spaces left out), or a word of the name
    together with every size of it (계란 and 20구 for 계란20구); a product word alone names every egg (review r1 I3)."""
    if sku in text:
        return True
    if not name:
        return False
    flat = re.sub(r'\s+', '', text)
    if re.sub(r'\s+', '', name) in flat:
        return True
    sizes = [x.lower() for x in SIZE_RE.findall(name)]
    words = [w for w in re.findall(r'[가-힣]{2,}', SIZE_RE.sub(' ', name)) if w not in COMMON]
    low = flat.lower()                          # 20구 is not inside 120구 (r2 M1)
    return bool(sizes) and any(w in text for w in words) and all(re.search(r'(?<![\d.])' + re.escape(z), low) for z in sizes)


def stock_changes(old, new):
    """What moved between what the model was told and the live answer now: sold out, or a set price that is another
    now. Fewer left is not (other customers buy all the time; review r1 I3); a first price is not (review I2)."""
    out = []
    if type(old.get('count')) is int and old['count'] > 0 and new.get('count') == 0:
        out.append('지금 재고 없음')
    for g, p in sorted((old.get('prices') or {}).items()):
        q = (new.get('prices') or {}).get(g)
        if type(p) is int and type(q) is int and p != q:
            out.append(f"{g}등급 {q:,}크레딧")
    return out


PIN_TAG = '[운영자 최신 지시'        # only the operator's current instructions; older '[운영자 지시 …]' lines are history (review I2)
PIN_HEAD = 'Current operator instructions in your notes (kept in view even when the notes below no longer reach them; file order, later lines are newer):\n'


def pinned_notes(text, tail):
    """The kit shows only the last 3,000 chars of notes.md (agent_kit.Mind.notes), so operator lines written early fall
    out of view as the shop adds its own notes (11/26 21:40 check). Every line that starts with '[운영자 최신 지시' and is
    not already whole inside the tail goes in front of the tail, once each, in file order. Nothing is cut or changed."""
    lines, seen = [], set()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(PIN_TAG) and line not in seen and line not in tail:
            seen.add(line)
            lines.append(line)
    return PIN_HEAD + '\n'.join(lines) + '\n\n' + tail if lines else tail


def with_until(out, now):
    """check_item_inventory {sku} answer + the expiry of each listed unit ('until') and the minute it was read ('now'),
    so the model says a date and time instead of hours (operator 11/27). The tool (v12+) gives each dated unit its
    until from expires_at, 'MM/DD HH:MM KST', the same minute the checkout's signed certificate line shows; that one is
    kept as it is (11/27 0b2461b6: '18시까지' was said against a signed 18:27 and the customer did not pay). An older
    tool gives none: then the hour from hours_left ('MM/DD HH시 KST'; hours_left is rounded to 0.1 h, so minutes made
    up from it would look more exact than they are). Only answers that carry an items list (the per-item answer); a
    whole-shop answer or any other shape is passed on untouched."""
    try:
        res = out.get('result') if isinstance(out, dict) and out.get('ok') else None
        items = res.get('items') if isinstance(res, dict) else None
        if not isinstance(items, list):
            return out
        kst = timezone(timedelta(hours=9))
        day = lambda h: datetime.fromtimestamp(now + h * 3600, kst).strftime('%m/%d %H시 KST')
        for i in items:
            if not isinstance(i, dict) or (isinstance(i.get('until'), str) and UNTIL.fullmatch(i['until'])):
                continue
            i.pop('until', None)
            h = i.get('hours_left')
            if type(h) in (int, float) and 0 <= h < 24 * 400:
                i['until'] = day(h)
        res['now'] = datetime.fromtimestamp(now, kst).strftime('%m/%d %H:%M KST')
    except Exception:
        pass
    return out


UNTIL = re.compile(r'\d\d/\d\d \d\d:\d\d KST')
KST9 = timezone(timedelta(hours=9))


def issued_terms(payload, ev, now):
    """What the model may say of a checkout it just issued, read from what is signed (11/27 01daff51: '4,720' said
    against a signed 4,710 clearance price, 0b2461b6: '18시' against 18:27; both customers did not pay): the
    payload price and the certificate expiry in the minute ops/checkout_delivery.grade_note writes into the reply's
    data. Returns {} for nothing checkable; never raises. Prices and the signed checkout are not changed here."""
    out = {}
    try:
        p = payload.get('price') if isinstance(payload, dict) else None
        if type(p) is int and 0 < p < 10 ** 9:
            out['price'] = p
        ea = ev.get('expires_at') if isinstance(ev, dict) else None
        if type(ea) in (int, float) and 1e9 < ea < 1e11 and ea > now:
            out['until'] = datetime.fromtimestamp(ea, KST9).strftime('%m/%d %H:%M KST')
        if out:
            said = ' · '.join(([f"가격 {out['price']:,}크레딧"] if 'price' in out else [])
                              + ([f"소비기한 {out['until'][:-4]}까지"] if 'until' in out else []))
            what = '·'.join((['가격'] if 'price' in out else []) + (['소비기한'] if 'until' in out else []))
            out['reply_note'] = (f'답장의 {what}은 이 결제서에 서명된 값 그대로 씁니다: {said}. 앞서 재고 조회에서 본 값과 '
                                 '달라도 이 값을 쓰고, 시각을 시 단위로 줄이지 않습니다(손님이 결제서 데이터와 글자 그대로 대조합니다)')
    except Exception:
        return {}
    return out


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
        from checkout_delivery import Checkout, attach_data, clean_reply, compose, grade_free_reply, grade_note, _ungraded_food, input_required, tokens, JWT, SIGNED, ORDER, HOLD_SECONDS
        http_request = ContextVar('checkout_http', default=None)
        inquiry = ContextVar('checkout_inquiry', default=None)
        a2a_event = ContextVar('checkout_a2a_event', default=None)
        owners = {}
        shop = {}                               # agent_app's name (--name): the buy line in a checkout reply (check-1510 6-1)

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
            "confirmation first. Near-expiry units (operator 11/27): when a unit with 12 hours or more left also fits, issue with "
            "that one as usual and you may mention the near-expiry one at its lower price, unless the customer named the "
            "near-expiry or clearance unit (then that one). When only a unit with under 12 hours "
            "left (hours_left < 12, clearance units too) fits, do not create its checkout first: say the item, grade, the date "
            "and time it expires and the price, and create it once the customer says they want it; if the customer already "
            "asked for a checkout or named that unit (for example from the forum menu's clearance line), create it in this same "
            "reply and say its expiry date and time there. A clearance unit is created without price (it goes out at its "
            "clearance price, the one you quote), unless a lower price down to clearance.floor was agreed: then pass that "
            "price. Otherwise hold back only when the customer said not to issue yet, or no held unit meets a condition "
            "they stated. A stock menu is posted on the forum for you about every 5 minutes (ops/forum_menu.py); use forum_post "
            "only for replies and threads, not for stock or price lists. Make a checkout only when the customer wants it and the agreed conditions "
            "are met. The actual reply text includes the matching signed originals automatically; do not copy "
            "or invent tokens. The signed originals open the reply because the customer reads only its first 2,000 characters, "
            "so one reply carries one checkout (operator 11/27); when the customer wants more, issue the one they asked for first, "
            "answer with state input_required and issue the next in the customer's next message. Put only customer-facing prose in an issuing reply (no tool data or dumps). On explicit customer refusal or your explicit decision to cancel issuance, "
            "pass checkout_cancel: true (all) or a list of order ids (only those) in reply. "
            "A reply state alone or a sentence about payment does not cancel issuance. "
            "When private_checkout answers grade_evidence: attached, the reply normally also carries a fixed grade-evidence "
            "line (food grade is the shelf life left, and the market-signed certificate states the supply and expiry time; a very "
            "long reply drops the line). Point to that line when asked; do not claim the market verified the grade. A food checkout with no certificate grade lists only the item, signed price and expiry. If the buyer requires an independently certified grade, explain it is unavailable and wait for agreement rather than sending a checkout."
            "\nCustomer replies (operator 11/27): say what you can do first. Leave out disclaimer lines nobody asked about "
            "(결제서는 발급하지 않겠습니다, 검증한다는 뜻은 아닙니다, 취소되지 않았으니 유의). When you must say no (a price below what you "
            "can take, the refund rules, a fact no tool gives you), say it in one sentence and add what you can offer instead; "
            "never make up a fact. Never write null, 하한가, 하한, 떨이가, floor or clearance to a customer, nor 안내가 meaning a "
            "price (say 가격 or 할인 가격); a price null in a tool answer only means no price is set yet: set one at or above the "
            "floor, never tell the customer the price is undecided. Do not bring up certificate grade or settlement state unless asked; if asked "
            "about grade, one sentence: 시장 규칙상 식품 등급은 남은 소비기한이고 소비기한은 시장 서명 인증서에 있습니다, then keep selling. "
            "When no unit matches exactly (size, pack, grade or item), offer the one closest unit you hold (same food first) "
            "with its name, grade, expiry and price and ask if they want it (not when they refused substitutes). Say expiry as "
            "a date and time from the tool's until as written (e.g. 11/29 03:27까지, 약 2일), not as hours alone and not cut to the "
            "hour; once private_checkout has issued, say its result's price and until exactly as written (they are what the "
            "checkout signs, and customers compare the two word for word); the whole-shop answer has "
            "hours only, so look the item up with {sku} before you give its expiry. Only greet with an earlier "
            "visit (지난번 …) once a conversation and only when it is the same item they ask for now; read the pack size from "
            "the item name (계란 20구 is 20 eggs)."
        )

        BaseUpdater = a2a_tasks.TaskUpdater
        PAY_KEYS = ('checkout_jwt', 'receipt', 'sig_seller')

        def without_checkout(value):
            if isinstance(value, str):
                return JWT.sub('[checkout]', value)
            if isinstance(value, list):
                return [without_checkout(x) for x in value]
            if isinstance(value, dict):
                return {without_checkout(k): without_checkout(v) for k, v in value.items()}
            return value

        def model_data(value):
            # 11/27 review I1: the kit stores the model's data as a task artifact that comes back on every later answer
            # of the task, and the customer's buy({agent}) pays the first checkout_jwt / receipt / sig_seller it finds
            # there. Only our own data part (send_response) may carry those keys; '[checkout]' left under one of them
            # would fail exactly like a hand-copied token.
            value = without_checkout(value)
            if isinstance(value, dict):
                return {k: v for k, v in value.items() if k not in PAY_KEYS}
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
                if http is not None:
                    http['send'] = True         # this HTTP answer is a SendMessage one: send_response marks it
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
                start, planned = None, False

                def json_body(event):
                    return any(k.lower() == b'content-type' and v.split(b';')[0].strip().lower() == b'application/json'
                               for k, v in event.get('headers') or ())

                async def send_marked(header, event):
                    # 11/27 sig-reject RCA: every other JSON answer that is a task with a status message carries a data
                    # part without a token ({checkouts: [], cancelled: []}). The customer tool (house/customer.py talk)
                    # keeps the last reply that had data and buy({agent}) pays the first checkout_jwt in it, so without
                    # this an old checkout would stay payable after a plain reply. Errors, streams and other shapes pass.
                    marked = None
                    if event['type'] == 'http.response.body' and not event.get('more_body', False):
                        try:
                            response = json.loads(event['body'])
                            envelope = response.get('result') if isinstance(response, dict) else None
                            result = envelope.get('task', envelope) if isinstance(envelope, dict) else None
                            if (isinstance(result, dict) and result.get('id') and isinstance(result.get('status'), dict)
                                    and isinstance(result['status'].get('message'), dict)):
                                attach_data(result, ())
                                raw = json.dumps(response, ensure_ascii=False, separators=(',', ':')).encode()
                                headers = [(k, v) for k, v in header['headers']
                                           if k.lower() not in (b'content-length', b'etag')]
                                headers.append((b'content-length', str(len(raw)).encode()))
                                header = dict(header, headers=headers)
                                event = dict(event, body=raw)
                                marked = (result['id'], result['status']['message'].get('messageId'))
                        except Exception:
                            marked = None       # the SDK answer as it was
                    await send(header)
                    await send(event)
                    if marked:
                        write('checkout-sends.jsonl', {'kind': 'data_marker', 'task': marked[0], 'status_message_id': marked[1]})

                async def send_response(event):
                    nonlocal start, planned
                    if (event['type'] == 'http.response.start' and event['status'] == 200
                            and (http.get('answer') or (http.get('send') and json_body(event)))):
                        # The pinned SDK returns a single JSONResponse, not a stream.
                        start, planned = event, bool(http.get('answer'))
                        return
                    if start is not None and not planned:
                        header, start = start, None
                        await send_marked(header, event)
                        return
                    if start is not None:
                        header, start = start, None
                        emitted, waived, sent_data, rewritten, body = (), (), 0, False, ''
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
                                    now = time.time()
                                    body, attached = compose(baseline, issued, cancelled, now,
                                                             agent=shop.get('name'), item_names=NAMES,
                                                             continuation=input_required(result['status'].get('state')))
                                    part['text'] = body
                                    raw = json.dumps(response, ensure_ascii=False, separators=(',', ':')).encode()
                                    serialized = json.loads(raw)['result']
                                    checks = tokens(serialized.get('task', serialized)['status']['message']['parts'][0]['text'])
                                    emitted = tuple(c for c in attached if c.token in checks)
                                    waived = tuple(c for c in issued if c.order in cancelled)
                                    # 11/27: the same originals as the text also go whole as data parts (first artifact)
                                    sent_data = attach_data(result, emitted, cancelled, now=now)
                                    raw = json.dumps(response, ensure_ascii=False, separators=(',', ':')).encode()
                                    headers = [(k, v) for k, v in header['headers']
                                               if k.lower() not in (b'content-length', b'cache-control', b'etag')]
                                    headers.extend([(b'content-length', str(len(raw)).encode()),
                                                    (b'cache-control', b'no-store')])
                                    header = dict(header, headers=headers)
                                    event = dict(event, body=raw)
                                    rewritten = True
                            except Exception:
                                # Preserve the SDK answer but do not record a delivery on shape mismatch.
                                emitted, waived, sent_data = (), (), 0
                        if rewritten:
                            await send(header)
                            await send(event)
                        else:                           # review I2: the SDK answer as it was, plus the token-free marker
                            await send_marked(header, event)
                        for c in emitted:               # the grade line as sent (operator 11/26 12:40), '' when dropped
                            write('checkout-sends.jsonl', {'kind': 'checkout_asgi_handoff', 'task': task, 'message_id': msg_id,
                                                           'order': c.order, 'checkout_hash': c.digest,
                                                           'grade_line': c.note if c.note and c.note in body else '',
                                                           'data_part': sent_data})
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
            shop['name'] = args[1] if len(args) > 1 else kwargs.get('name')
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

    def conv_key(ev):
        return str(getattr(ev, 'task', None) or getattr(ev, 'id', ''))

    def mine_only():
        try:                                    # a copy: other threads add and answer messages meanwhile (r2 M1)
            ids = [e.id for e in list(par.mind.pending.values())
                   if conv_key(e) == getattr(par, 'key', None) and not e.done.is_set()]
        except Exception:
            ids = []
        return f"error: reply only to this run's customer message. Waiting here: {ids}"

    def not_mine(ev):
        """A parallel run: a message of another conversation (or none). The kit's loop: a conversation a parallel run
        serves (r2 I1); the kit's error lists every waiting id, those too."""
        if getattr(par, 'worker', False):
            return ev is None or conv_key(ev) != getattr(par, 'key', None)
        if ev is None:
            return False                        # the kit's own error, as before
        with wlock:
            return conv_key(ev) in workers

    def elsewhere():
        return 'error: that customer is being answered in another run of you; reply only to the messages listed in this run'

    def recheck(mind, ev, text, cancel):
        """대표님 11/27: right before a reply goes, each item it names (RECHECK_MAX at most, the ones this run asked
        about first) is looked up live on its own (check_item_inventory {sku}; review I1), all at the same time within
        RECHECK_BUDGET, and compared with what the model was told in this run. Changed: once per message the reply
        goes back to be written again with the new values; after that, or when the customer has waited too long for
        another model step, it goes with a line of the new values added (review I2). None: send as written."""
        told = getattr(par, 'told', None)
        issued = getattr(ev, 'checkout_issued', ())
        if issued and not (cancel is True or (isinstance(cancel, list) and {c.order for c in issued} <= set(cancel))):
            return None                         # a checkout goes with it and carries its own price (r1 M5)
        if not isinstance(told, dict) or not told:
            return None
        age = time.time() - getattr(ev, 'at', time.time())
        wait = getattr(ak, 'A2A_WAIT', 80)
        if wait - age < RECHECK_ROOM:
            return None                         # no time to look: as written (r1 I2)
        with _names_lock:
            names = {k: v.get('name') or NAMES.get(k) for k, v in told.items()}
        asked = getattr(par, 'asked', ())
        named = sorted((k for k in told if named_in(text, k, names[k])), key=lambda k: (k not in asked, k))
        got = {}

        def look(sku):
            try:
                out = mind.toolbox.call('check_item_inventory', {'sku': sku})
                res = out.get('result') if isinstance(out, dict) and out.get('ok') else None
                if isinstance(res, dict) and res.get('sku') == sku:
                    got[sku] = told_from(res, by_id=True).get(sku)
            except Exception:
                pass
        ts = [threading.Thread(target=look, args=(k,), daemon=True) for k in named[:RECHECK_MAX]]
        for t in ts:
            t.start()
        end = time.time() + RECHECK_BUDGET
        for t in ts:
            t.join(max(0.0, end - time.time()))
        changed = []
        for sku in named[:RECHECK_MAX]:
            new = got.get(sku)
            if not new:
                continue                        # no answer for it in time: that item goes as written
            moved = stock_changes(told[sku], new)
            merge_told(told, {sku: new})
            if moved:
                changed.append(f"{new.get('name') or names[sku] or sku}({sku}): " + ', '.join(moved))
        if not changed:
            return None
        age = time.time() - getattr(ev, 'at', time.time())
        with _lock:                             # once per message, also when two runs reply to it (r1 M1)
            late = age >= wait - REWRITE_ROOM or getattr(ev, 'recheck_back', 0) >= 1 \
                or getattr(par, 'calls', 0) >= int(mind.agent.get('max_steps') or 40) - 1 \
                or time.time() > getattr(par, 'deadline', float('inf')) - REWRITE_ROOM   # no step or time left (r2 M5, r3 M2)
            if not late:
                ev.recheck_back = 1
        write('missed-messages.jsonl', {'kind': 'reply_stock_changed', 'ids': [ev.id], 'tasks': [ev.task],
                                        'waited': int(age), 'changed': changed[:6], 'sent_with_note': late,
                                        'looked_up': len(ts), 'answered': len(got), 'named': len(named)})
        if late:
            return '\n\n(보내기 직전 다시 확인한 재고) ' + ' / '.join(changed[:6])
        return ('error: not sent. Stock or price of an item in your reply changed since you looked (live, just now): '
                + ' / '.join(changed[:6]) + '. Write the reply again with these values and send it with reply.')

    def reply_rec(self, args):
        try:
            ev = self.pending.get(str((args or {}).get('to')))
        except Exception:
            ev = None
        if not_mine(ev):                        # another customer's message: never answered from here (r1 I1, r2 I1)
            return mine_only() if getattr(par, 'worker', False) else elsewhere()
        fixed = None
        try:                                    # never stops a reply: on any error it goes as written
            if ev is not None and getattr(ev, 'kind', None) == 'a2a' and not ev.done.is_set():
                fixed = recheck(self, ev, str((args or {}).get('text') or ''), (args or {}).get('checkout_cancel'))
        except Exception:
            fixed = None
        if isinstance(fixed, str) and fixed.startswith('error:'):
            return fixed
        if fixed:
            args = dict(args, text=str(args.get('text') or '') + fixed)
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
                compose(str(args.get('text') or ''), issued, cancelled, time.time(), agent=shop.get('name'))
            except ValueError as e:
                return f'error: {e}'
            ev.checkout_plan = (issued, cancelled)
            canonical = grade_free_reply(issued, cancelled, time.time(), NAMES,
                                         input_required(args.get('state', 'input_required')))
            if canonical is not None:
                args = dict(args, text=canonical)
        if checkout and ev is not None and ev.kind == 'a2a':
            body = str(args.get('text') or '')
            effective = dict(args, text=clean_reply(body) if issued else without_checkout(body),
                             data=None if issued else model_data(args.get('data')))
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
    par = threading.local()                     # set in the threads that serve one A2A conversation each
    model = getattr(ak, 'Model', None)
    patch_seen = all(callable(getattr(c, n, None)) for c, n in ((ak.Mind, 'fresh'), (ak.Mind, 'run_once'), (model, 'complete')))
    fresh0, run_once0, complete0 = (ak.Mind.fresh, ak.Mind.run_once, model.complete) if patch_seen else (None,) * 3

    def fresh_rec(self):
        out = fresh0(self)
        if out and getattr(seen, 'mind', None) is self:
            seen.taken.extend(out)
            if any(getattr(e, 'kind', None) == 'a2a' for e in out):
                par.a2a = True                  # a customer came in this run: live stock from the next call on
        return out

    def run_seen(self, events):
        seen.mind, seen.taken, seen.readded = self, list(events or []), set()   # the run's first prompt is cut too (r5 M2)
        par.stock_at, par.told, par.asked, par.calls = 0, {}, set(), 0
        par.deadline = time.time() + 60 * float((getattr(self, 'agent', None) or {}).get('run_minutes') or 8)   # as the kit's run (r3 M2)
        # every run that serves a customer starts with a live stock lookup
        par.a2a = getattr(par, 'worker', False) or any(getattr(e, 'kind', None) == 'a2a' for e in events or [])
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
        try:
            if getattr(par, 'a2a', False) and messages and messages[-1].get('role') == 'user':
                if getattr(par, 'worker', False) and len(messages) > 1 and messages[1].get('role') == 'user' and PAR_NOTE not in messages[1]['content']:
                    messages[1] = dict(messages[1], content=messages[1]['content'] + PAR_NOTE)
                now = time.time()
                if now - getattr(par, 'stock_at', 0) >= STOCK_EVERY:
                    par.stock_at = now          # set first: a retried call within STOCK_EVERY adds nothing twice
                    try:
                        out = seen.mind.toolbox.call('check_item_inventory', {})
                    except Exception:           # no such tool or it crashed: the failure note below (review r2 M1)
                        out = None
                    res = out.get('result') if isinstance(out, dict) and out.get('ok') else None
                    when = time.strftime('%H:%M:%S', time.localtime(now))
                    if isinstance(res, dict) and res.get('ok') is not False:
                        par.told = merge_told(dict(getattr(par, 'told', None) or {}), told_from(res))   # what it is told
                        if isinstance(res.get('skus'), list) and not res.get('more_skus'):   # gone from the list: none left (r2 M6)
                            listed = {x.get('sku') for x in res['skus'] if isinstance(x, dict)}
                            for sku_, t_ in par.told.items():   # not k: complete_rec's **k (kwargs)
                                if sku_ not in listed:
                                    t_['count'] = 0
                        add = (f"\n\nLive stock now ({when}, check_item_inventory; refreshed every {STOCK_EVERY} s): "
                               + json.dumps(res, ensure_ascii=False)[:5000])
                    else:
                        add = (f"\n\nLive stock lookup failed at {when}: call check_item_inventory before you state an "
                               "item, price or stock.")
                    messages[-1] = dict(messages[-1], content=messages[-1]['content'] + add)
        except Exception:
            pass
        par.calls = getattr(par, 'calls', 0) + 1        # model calls in this run (the reply check's last-step rule)
        out = complete0(self, messages, *a, **k)
        seen.taken = []                                 # only once the model got them; a failed call keeps them (r3 M1)
        return out

    if patch_seen:
        ak.Mind.fresh, ak.Mind.run_once, model.complete = fresh_rec, run_seen, complete_rec
        done += ['Mind.fresh', 'Mind.run_once', 'Model.complete']

    call = ak.Toolbox.call
    checkout_lock, pay_lock = threading.RLock(), threading.RLock()

    def call_rec(self, name, args):
        """private_checkout, every wallet tool (refund_send, buy_food_lot, any with permission.wallet; review C1) and
        the calls that set a shown price in quotes.json run one call at a time: create reads the live inventory and the open holds and logs its
        issue before it returns, so two customers served in parallel never get the same unit (the last one of an
        item goes to one; the other gets unit_held and the free alternatives). An unpaid hold runs out (HOLD)."""
        if getattr(par, 'worker', False) and name in WORKER_NO:
            return {'ok': False, 'error': f'{name} is not used in a customer run; the main run does it (reply, then sleep)'}
        try:                                    # every wallet tool: one nonce and one balance at a time
            w = ((getattr(self, 'tools', None) or {}).get(name) or {}).get('perm', {}).get('wallet')
            wallet = {w} if isinstance(w, str) else set(w or ())       # 'pay' is not {'p', 'a', 'y'} (r2 M5)
        except Exception:
            wallet = {'pay'}                    # unknown: the safe side
        if 'pay' in wallet or name in ('refund_send', 'buy_food_lot'):
            with pay_lock:                      # sends: their own lock, so a 180 s send never holds a checkout (r1 M1)
                return call_one(self, name, args)
        if wallet or name in ('private_checkout', 'restock_shoutout') or (
                name == 'check_item_inventory' and isinstance(args, dict) and args.get('quote')):   # quotes.json too
            with checkout_lock:
                return call_one(self, name, args)
        return call_one(self, name, args)

    def call_one(self, name, args):
        ev = None
        if checkout and name == 'private_checkout' and isinstance(args, dict) and args.get('op') == 'create':
            mind = getattr(active_mind, 'current', None)
            ev = mind.pending.get(str(args.get('reply_to'))) if mind is not None else None
            if not_mine(ev):                    # another customer's message (r1 I1, r2 I1)
                return {'ok': False, 'error': (mine_only() if getattr(par, 'worker', False) else elsewhere())[7:]}
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
                held = held_result(r, wanted=args.get('unit') if isinstance(args, dict) else None)
                if held is not None:
                    if held.get('code') == 'unit_held':
                        ev.checkout_held = (held.get('held_unit') or '', held.get('held_until') or 0)
                    return held
                # Raw stdout, tracebacks, and tool-returned errors are not customer-facing text.
                return {'ok': False, 'error': 'checkout tool did not confirm issuance; do not automatically reissue'}
            token, order = r.get('checkout_jwt'), r.get('order')
            payload = r.get('payload') or {}
            at = payload.get('iat') if isinstance(payload, dict) else None
            if not (isinstance(token, str) and SIGNED.fullmatch(token) and isinstance(order, str)
                    and ORDER.fullmatch(order) and type(at) is int):
                return {'ok': False, 'error': 'invalid checkout metadata; do not automatically reissue'}
            c = Checkout(order, token, at, grade_note(order, r.get('grade_evidence'), time.time()),
                         *getattr(ev, 'checkout_held', ('', 0)))
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
            no_grade = _ungraded_food(payload)
            res = {'ok': True, 'order': order}
            if kept.note and no_grade is None:
                res['grade_evidence'] = 'attached'
            dup = r.get('duplicate_of')
            if isinstance(dup, str) and re.fullmatch(r'[A-Za-z0-9-]{1,80}', dup) and dup != order:
                res['duplicate_of'] = dup       # 대표님 11/27: same buyer, same item and grade, 30 s
                here = dup in {c.order for c in ev.checkout_issued}
                res['duplicate_note'] = (f'이 문의에 30초 안에 같은 상품·같은 등급 결제서({dup})를 이미 냈습니다. 한 답장에는 결제서 1장만 '
                                         '들어갑니다. '
                                         + (f'답장에 checkout_cancel: ["{dup}"]를 넣어 이번 것 하나만 남기세요(손님이 두 개를 원했으면 둘째는 '
                                            '다음 메시지에 다시 냅니다)' if here else      # review 3 M3: one rule whether or not two were wanted
                                            f'손님이 두 개를 원한 게 아니면 {dup}는 앞 답장으로 이미 갔으니 이번 답장에 '
                                            f'checkout_cancel: ["{order}"]를 넣으세요'))   # r2 M2
            q = r.get('quote')
            if type(q) is int and 0 < q < 10 ** 9:      # the shown price for this SKU and grade (operator 11/26 12:40)
                res['quote'] = q
                if type(payload.get('price')) is int and payload['price'] != q:
                    res['quote_note'] = (f'이 품목·등급 안내가는 {q:,}입니다. 이번 결제서는 합의한 {payload["price"]:,}로 냈고, '
                                         f'다음 안내는 {q:,}로 합니다')
            # last: the notes above keep their place. The expiry only when the reply's data part carries the certificate
            # line (grade_note checked it; review 1 M1), so the model is never told of a signed time the customer cannot see
            res.update(issued_terms(payload, no_grade if no_grade is not None else
                                    (r.get('grade_evidence') if kept.note else None), now))
            if no_grade is not None:
                res['reply_note'] = (res.get('reply_note', '') + ' 이 식품 결제서 답장에는 등급을 쓰지 않습니다.').strip()
            return {'ok': True, 'result': res}
        out = call(self, name, args)
        if name == 'check_item_inventory':
            out = with_until(out, time.time())
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
        try:                                    # what the model reads of the stock in this run (the reply check's base)
            # par is per thread: the reply check's own look-ups run in threads of their own and add nothing here
            if name == 'check_item_inventory' and isinstance(getattr(par, 'told', None), dict):
                res = out.get('result') if isinstance(out, dict) and out.get('ok') else None
                a = args.get('sku') if isinstance(args, dict) and isinstance(args.get('sku'), str) else None
                with _names_lock:
                    by_id = a is not None and (a in par.told or a in NAMES)
                got = told_from(res, by_id=by_id)
                merge_told(par.told, got)
                if a:                           # one item asked: the SKUs it answered for, never the name (r2 M3, r3 M1)
                    par.asked.update(got)
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
    forge = getattr(ak.Toolbox, 'forge', None)
    if callable(forge):
        def forge_rec(self, args):
            if getattr(par, 'worker', False):
                return 'error: forge_tool is not used in a customer run; the main run does it (reply, then sleep)'
            try:
                w = ((args or {}).get('permission') or {}).get('wallet')
                ops = {w} if isinstance(w, str) else set(w or ())
            except Exception:
                ops = {'pay'}
            name = str((args or {}).get('name') or '')
            locks = ([pay_lock] if 'pay' in ops else []) + (
                [checkout_lock] if ops or name in ('private_checkout', 'restock_shoutout', 'check_item_inventory') else [])
            for lk in locks:                    # only what its test may touch (r1 M3, r2 M2, r3 M1); pay before checkout
                lk.acquire()
            try:
                return forge(self, args)
            finally:
                for lk in reversed(locks):
                    lk.release()
        ak.Toolbox.forge = forge_rec
    done += ['Mind.deliver', 'Mind.reply', 'Toolbox.call'] + (['Toolbox.forge'] if callable(forge) else [])

    # 대표님 11/27 00:55 "동시에 들어오면 동시에 해": the kit serves every message in one loop, so a customer who wrote
    # while the agent worked for another waited past the A2A window (11/26 21:37 6842e6f2, 23:14 728bb36b). Each A2A
    # conversation (its task id) now gets its own thread running the kit's own run_once on a copy of the mind that
    # shares pending (reply), tools and model but has its own inbox; later messages of the same conversation go to
    # the same thread. Mini-app messages and timers stay in the kit's loop, and so does a conversation that comes
    # while PAR_MAX are being served.
    workers, wlock, home = {}, threading.Lock(), {}
    HOME_SPAN = 120                         # s: a conversation the kit's loop took stays there this long
    lg = getattr(ak, 'log', None) or (lambda line='': None)

    def serve(w, key):
        par.worker, par.key, par.mind = True, key, w
        try:
            while True:
                with wlock:
                    events = w.fresh()
                    if not events:
                        workers.pop(key, None)
                        return
                try:
                    ak.Mind.run_once(w, events)
                except Exception as e:          # like the kit's loop: one run never ends the shop
                    lg(f"[mind] parallel run failed: {type(e).__name__}: {e}")
                    time.sleep(5)               # not a hot loop when a run fails before its first model call
        finally:
            with wlock:
                if workers.get(key) is w:
                    workers.pop(key, None)
                left = []
                try:
                    while True:
                        left.append(w.inbox.get_nowait())
                except queue.Empty:
                    pass
                if left:                        # the kit's loop serves them: the conversation stays there (r2 M4)
                    home[key] = time.time()
            for e in left:                      # never dropped: back to the kit's loop
                if not e.done.is_set():
                    w.par_home.inbox.put(e)

    def dispatch(self, ev):
        if getattr(ev, 'kind', None) != 'a2a' or getattr(self, 'par_home', None) is not None:
            return deliver_rec(self, ev)
        key = str(getattr(ev, 'task', None) or getattr(ev, 'id', ''))
        start = None
        with wlock:
            w = workers.get(key)
            for k, t in list(home.items()):
                if time.time() - t > HOME_SPAN:
                    home.pop(k, None)
            if w is None and len(workers) < PAR_MAX and key not in home:
                try:
                    w = copy.copy(self)
                    w.inbox, w.wake_at, w.par_home = queue.Queue(), 0.0, self
                    workers[key] = start = w
                except Exception:
                    w = None
            if w is not None:
                try:
                    return_ev = deliver_rec(w, ev)  # pending is shared: reply() from any run finds it
                except Exception:               # never a slot nobody serves (r1 M4)
                    if start is None:
                        raise                   # its worker still serves the conversation (r3 M2)
                    workers.pop(key, None)
                    w = start = None
            if w is None:
                home[key] = time.time()         # this conversation stays in the kit's loop for a while (r1 M6)
        if w is None:
            return deliver_rec(self, ev)
        if start is not None:
            try:
                threading.Thread(target=serve, args=(start, key), daemon=True, name=f'a2a-{key[:12]}').start()
            except Exception:                   # no thread: the kit's loop takes it
                with wlock:
                    workers.pop(key, None)
                    home[key] = time.time()     # and keeps the conversation (r2 M4)
                    try:
                        while True:
                            self.inbox.put(start.inbox.get_nowait())
                    except queue.Empty:
                        pass
        return return_ev

    if all(callable(getattr(ak.Mind, n, None)) for n in ('fresh', 'run_once', 'loop')):   # the kit's loop as we know it
        ak.Mind.deliver = dispatch
        done.append('Mind.deliver(parallel)')

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

    if callable(getattr(ak.Mind, 'notes', None)):   # operator lines of notes.md stay in view (pinned_notes)
        notes0 = ak.Mind.notes

        def notes_pinned(self):
            tail = notes0(self)                 # the kit's own answer and its own errors, unchanged
            try:
                for _ in range(2):              # a note appended between the two reads: read both once more (M1)
                    text = (self.root / 'notes.md').read_text(encoding='utf-8')
                    if isinstance(tail, str) and text and text.endswith(tail):   # any kit window size (M2)
                        return pinned_notes(text, tail)
                    tail = notes0(self)
                return tail
            except Exception:
                return tail

        ak.Mind.notes = notes_pinned
        done.append('Mind.notes(pinned)')

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
