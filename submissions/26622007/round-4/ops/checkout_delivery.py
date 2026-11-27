"""Private checkout reply integrity. JWTs are retained in pending Event memory only."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import base64
import json
import re
import time

HOLD_SECONDS = 300   # Same as the existing private_checkout unit reservation.
DEADLINE = 600
REPLY_MAX = 4000
VISIBLE = 2000       # the customer tool reads only agent_says = said[:2000] (Q00/agent-market house/customer.py)
PER_REPLY = 1        # checkouts in one reply (operator 11/27: one, the rest in the customer's next message)
JWT = re.compile(r'(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)?(?![A-Za-z0-9_-])')


@dataclass(frozen=True)
class Checkout:
    order: str
    token: str = field(repr=False)
    at: float
    note: str = ''          # grade_note() text, written just before this checkout's line

    held_unit: str = ''
    held_until: int = 0

    @property
    def digest(self):
        return sha256(self.token.encode('ascii')).hexdigest()


def tokens(text):
    return set(JWT.findall(text))


ORDER = re.compile(r'[A-Za-z0-9_-]{1,64}')
SIGNED = re.compile(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+')
LABEL_LINE = re.compile(r'(?im)^[ \t]*checkout_jwt\b[^\r\n]*(?:\r?\n|$)')


def clean_reply(text):
    return JWT.sub('', LABEL_LINE.sub('', text)).strip()


AGENT = re.compile(r'[A-Za-z0-9_.-]{1,64}')


def buy_hint(agent):
    """One line under the signed original (check-1510 6-1, 11/27 12:47-14:59: 7 customers copied the ~850-character
    token into buy and were refused "not signed by this shop's wallet", 0/7 after a reissue): the customer tool's buy
    pays the data part when no token is named (house/customer.py 347-352). '' without a plain agent name."""
    if not isinstance(agent, str) or not AGENT.fullmatch(agent) or agent.startswith('eyJ'):
        return ''
    return f'결제는 buy에 agent: {agent}만 넣으면 됩니다. 토큰은 옮겨 적지 않아도 돼요'


def compose(text, issued, cancelled, now, agent=None, item_names=None, continuation=False):
    """Keep prose, rebuild the order/token pair from trusted issuance metadata. The signed original opens the reply so
    it is inside the VISIBLE characters the customer reads (PER_REPLY: one a reply); then the buy line (buy_hint, only
    when it and the prose both still fit in VISIBLE: it never pushes the prose's signed price and until out of view),
    prose, the grade note last."""
    active = {c.digest: c for c in issued if c.order not in cancelled and 0 <= now - c.at < HOLD_SECONDS}
    if len({c.order for c in active.values()}) != len(active):
        raise ValueError('duplicate checkout order ids')
    body = clean_reply(text)
    if not active:
        return body[:REPLY_MAX], ()
    lines = [f'checkout_jwt ({c.order}): {c.token}' for c in active.values()]
    orders = [c.order for c in active.values()]
    if len(lines) > PER_REPLY:                  # one checkout a reply (operator 11/27): the rest in the next message
        # checkout_cancel is replaced, not added to, on each reply: the example is the whole list (review 3 I1)
        drop = list(dict.fromkeys([c.order for c in issued if c.order in cancelled] + orders[:-PER_REPLY]))
        raise ValueError(f'한 답장에는 결제서를 {PER_REPLY}장만 넣습니다(지금 {len(lines)}장). 결제서를 새로 만들지 말고 같은 reply에 '
                         f'checkout_cancel 목록만 넣어 바로 다시 보내세요. 1장만 남기고(같은 품목을 다시 냈으면 가장 나중 것, '
                         f'다른 품목이면 손님이 먼저 원한 것) 나머지 order를 빼세요. checkout_cancel은 매번 새 목록으로 바뀌니 '
                         f'이미 넣은 취소 order까지 모두 담은 전체 목록을 넣으세요(예: {drop}). 이 답장의 state는 '
                         f'input_required로 두고, 손님께 나머지는 다음 메시지를 주시면 이어서 결제서를 드린다고 쓰세요(같은 대화로 '
                         f'이어 와야 빼 둔 unit을 바로 다시 낼 수 있습니다)')
    head = lines[0]
    if len(head) > VISIBLE:
        raise ValueError('결제서 줄이 손님 도구가 읽는 답장 앞 2,000자보다 깁니다. 이 결제서는 checkout_cancel로 빼고 손님께 사실대로 말하세요')
    canonical = grade_free_reply(active.values(), cancelled, now, item_names, continuation)
    lead = body
    if canonical is not None:
        # signed terms first, then the shop's own prose (check-0010: replacing the prose lost other items' offers)
        lead = canonical
        body = canonical + ('\n\n' + body if body else '')
    hint = buy_hint(agent)
    if hint and len(head) + 2 + len(hint) + (2 + len(lead) if lead else 0) <= VISIBLE:
        head += '\n\n' + hint
    notes = '\n'.join(c.note for c in active.values() if c.note) if canonical is None else ''
    rest = REPLY_MAX - len(head) - 2
    if notes and len(body) + 2 + len(notes) <= rest:   # grade notes give way to prose, prose to the originals
        tail = (body + '\n\n' if body else '') + notes
    else:
        tail = body[:rest].rstrip()
    return head + ('\n\n' + tail if tail else ''), tuple(active.values())


def _payload(token):
    try:
        body = token.split('.')[1]
        payload = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
        return payload if isinstance(payload, dict) else {}
    except (ValueError, IndexError):
        return {}


def _ungraded_food(payload):
    signed = payload.get('cert')
    cert = (signed.get('cert') or signed.get('data') or signed) if isinstance(signed, dict) else None
    if not isinstance(cert, dict) or cert.get('kind') != 'food':
        return None
    if cert.get('unit') != payload.get('unit'):
        return {}  # food is known, but neither the grade nor the dates belong to this unit
    return None if cert.get('grade') in ('A', 'B', 'C') else cert


def input_required(state):
    return isinstance(state, str) and state.lower().replace('-', '_').removeprefix('task_state_') == 'input_required'


FOLLOWUP = '이어서 결제서가 필요하면 이 대화에서 말씀해 주세요.'
# check-0010 (11/27 22:22~00:10): with no grade anywhere in the reply, 5/28 food checkouts drew "등급 null·안내 없음"
# refusals (0 paid) against 6/118 while the reply still said the grade. Food certificates carry no grade by market design
# (notes 21·27·51), so the reply names the grade the shop signed into the checkout (claim) and says why the cert is empty.
CERT_GRADE_LINE = '시장 인증서는 설계상 식품 등급(grade)을 항상 비워 둡니다. 위 등급은 가게가 이 결제서에 서명한 값이고, 인증서에는 소비기한이 적혀 있어요.'
GRADES = ('A', 'B', 'C')


def grade_free_reply(issued, cancelled, now, item_names=None, continuation=False):
    """Use only issued terms and the catalog name if the certificate does not state a grade."""
    active = {c.digest: c for c in issued if c.order not in cancelled and 0 <= now - c.at < HOLD_SECONDS}
    if len(active) != 1:
        return None
    checkout = next(iter(active.values()))
    payload = _payload(checkout.token)
    cert = _ungraded_food(payload)
    if cert is None:
        return None
    unit, sku = payload.get('unit'), payload.get('item')
    name = (item_names or {}).get(sku) if isinstance(sku, str) else None
    terms = [name] if isinstance(name, str) and 0 < len(name) <= 120 and name.isprintable() else []
    claim = payload.get('claim')
    if claim in GRADES:
        terms.append(f'{claim}등급(가게 서명)')
    if isinstance(unit, str) and UNIT.fullmatch(unit):
        terms.append(f"재고 ID {unit}")
    price = payload.get('price')
    if type(price) is int and 0 < price < 10 ** 9:
        terms.append(f'가격 {price:,}크레딧')
    end = cert.get('expires_at')
    if type(end) in (int, float) and 1e9 < end < 1e11 and end > now:
        terms.append(f"소비기한 {datetime.fromtimestamp(end, KST):%m/%d %H:%M KST}까지")
    text = ' · '.join(terms) or '결제서를 발급했어요.'
    if claim in GRADES and cert:            # {} = the certificate is not this unit's (review M3): no line about it
        text += '\n' + CERT_GRADE_LINE
    if (isinstance(checkout.held_unit, str) and UNIT.fullmatch(checkout.held_unit) and checkout.held_unit != unit
            and type(checkout.held_until) is int and now < checkout.held_until < 1e11):
        text += (f"\n앞서 찾으신 재고 {checkout.held_unit}의 예약은 "
                 f"{datetime.fromtimestamp(checkout.held_until, KST):%m/%d %H:%M KST}까지예요.")
    if continuation:
        text += '\n' + FOLLOWUP
    return text


def _fields(token, now, continuation):
    """Item, price, signed grade label (claim) and unit next to the unchanged token; for ungraded food also until."""
    pl = _payload(token)
    keys = ('item', 'price', 'claim', 'unit')       # check-0010: the signed claim stays in the data part for food too
    fields = {k: pl[k] for k in keys if k in pl and isinstance(pl[k], (str, int))}
    cert = _ungraded_food(pl)
    if cert is not None:
        end = cert.get('expires_at')
        if type(end) in (int, float) and 1e9 < end < 1e11 and end > now:
            fields['until'] = datetime.fromtimestamp(end, KST).strftime('%m/%d %H:%M KST')
        if continuation:
            fields['next_message'] = FOLLOWUP
    return fields


def attach_data(result, attached, cancelled=(), now=None):
    """Also put each attached checkout in the task's artifacts as an A2A data part (11/27 06:40 check): the market's
    customer tool reads only the first 2,000 characters of a reply's text, so a second checkout after the first one's
    grade line never reached it, and a customer that copies an ~850-character token by hand can change it ("not
    signed by this shop's wallet"); the data part is read whole and buy uses it when the customer names no token.
    Each entry is {checkout_jwt, order} plus item, price and unit (and the signed claim) from the same signed payload, so a
    customer with several can tell them apart. A reply that attaches none (all cancelled or past their hold, or any
    other answer: agent_kit_records send_marked) carries {checkouts: [], cancelled: [...]} instead, so the customer's
    last data no longer holds an old token.
    The same signed originals as in the text; nothing else changes. Returns the number of checkouts attached."""
    if not isinstance(result, dict):
        return 0
    now = time.time() if now is None else now
    status = result.get('status')
    continuation = isinstance(status, dict) and input_required(status.get('state'))
    data = [dict(_fields(c.token, now, continuation), checkout_jwt=c.token, order=c.order) for c in attached]
    parts = [{'data': d} for d in data] or [{'data': {'checkouts': [], 'cancelled': sorted(str(o) for o in cancelled)}}]
    if result.get('kind') == 'task':                 # the v0.3 binding names each part's kind
        for p in parts:
            p['kind'] = 'data'
    arts = result.get('artifacts')
    if not isinstance(arts, list):
        arts = result['artifacts'] = []
    # First: buy({agent}) pays the first checkout_jwt in the reply's data and the customer's model sees data[:3] only;
    # the task's stored artifacts (the kit's model data of earlier turns) come back on every answer (review I1).
    arts.insert(0, {'artifactId': 'checkout-' + (str(data[0]['order']) if data else 'none'), 'name': 'checkout', 'parts': parts})
    return len(data)


UNIT = re.compile(r'u-[0-9a-f]{6,32}')
KST = timezone(timedelta(hours=9))


def grade_note(order, ev, now):
    """One line from checked fields of the market-signed food certificate (private_checkout grade_evidence), in a
    fixed template: no tool text reaches the customer. '' when anything does not check; it never raises."""
    try:
        if not isinstance(ev, dict) or not isinstance(order, str) or not ORDER.fullmatch(order):
            return ''
        unit, claim, sa, ea = ev.get('unit'), ev.get('claim'), ev.get('supplied_at'), ev.get('expires_at')
        if not (isinstance(unit, str) and UNIT.fullmatch(unit) and claim in ('A', 'B', 'C')
                and all(type(x) in (int, float) and 1e9 < x < 1e11 for x in (sa, ea)) and sa < ea):
            return ''
        day = lambda t: datetime.fromtimestamp(t, KST).strftime('%m/%d %H:%M KST')
        if ea <= now:                                # already past: no line that says time is left (review 3 M4)
            return ''
        sold, ends, hours = day(sa), day(ea), int((ea - now) // 3600)
    except Exception:                                # a note never stops the checkout itself
        return ''
    d, h = divmod(hours, 24)
    left = ('1시간 미만' if hours < 1 else f'약 {hours}시간' if hours < 48 else f'약 {d}일' + (f' {h}시간' if h else ''))
    # short and plain (operator 11/27): the customer reads only the first 2,000 characters; no 'null', no rules text
    return (f'등급 근거: 시장 규칙상 식품 등급은 남은 소비기한입니다. 이 결제서에 든 시장 서명 인증서에 입고 {sold}, '
            f'소비기한 {ends}까지({left} 남음)가 적혀 있고, 가게가 밝힌 등급은 {claim}등급입니다.')


def key(row, digest_field='digest_hex'):
    return row.get('task'), row.get('message_id'), row.get(digest_field)


def overdue(checkouts, sends, now):
    """Distinguish a missing local handoff from one without independent receipt evidence."""
    resolved = {key(row) for row in checkouts if row.get('event') == 'delivery_waived'}
    recorded = {key(row) for row in checkouts if row.get('event') == 'delivery_overdue'}
    handoffs, settlements = {}, {}
    for row in sends:
        if row.get('kind') == 'checkout_asgi_handoff' and isinstance(row.get('ts'), (float, int)):
            k = key(row, 'checkout_hash')
            handoffs[k] = min(row['ts'], handoffs.get(k, row['ts']))
    for row in checkouts:
        if row.get('event') == 'settled' and isinstance(row.get('ts'), (float, int)):
            k = row.get('order'), row.get('digest_hex')
            settlements[k] = min(row['ts'], settlements.get(k, row['ts']))
    for row in checkouts:
        k = key(row)
        at = row.get('issued_at')
        if (row.get('event') != 'delivery_bound' or not all(k) or not isinstance(at, (int, float))
                or now - at < DEADLINE or k in resolved or k in recorded):
            continue
        sent_at = handoffs.get(k)
        timely = sent_at is not None and at <= sent_at <= at + DEADLINE
        paid_at = settlements.get((row.get('order'), row['digest_hex']))
        if timely and paid_at is not None and at <= paid_at <= at + DEADLINE:
            continue  # exact body handoff + authenticated checkout use, not ASGI alone
        recorded.add(k)
        yield {**row, 'delivery_status': 'receipt_unconfirmed' if timely else 'no_timely_handoff'}


def unbound(checkouts, now):
    """Only issuances after this feature first activated; historical logs have no inquiry id."""
    starts = [r['ts'] for r in checkouts if r.get('event') == 'delivery_tracking_started'
              and isinstance(r.get('ts'), (int, float))]
    if not starts:
        return
    bound = {(r.get('order'), r.get('digest_hex')) for r in checkouts
             if r.get('event') in ('delivery_bound', 'delivery_unbound')}
    for r in checkouts:
        at = r.get('ts')
        k = r.get('order'), r.get('digest_hex')
        if (r.get('event') == 'issued' and all(k) and k not in bound and isinstance(at, (int, float))
                and int(min(starts)) <= at <= now - DEADLINE):
            bound.add(k)
            yield r
