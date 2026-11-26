"""Private checkout reply integrity. JWTs are retained in pending Event memory only."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re

HOLD_SECONDS = 300   # Same as the existing private_checkout unit reservation.
DEADLINE = 600
REPLY_MAX = 4000
VISIBLE = 2000       # the customer tool reads only agent_says = said[:2000] (Q00/agent-market house/customer.py)
JWT = re.compile(r'(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)?(?![A-Za-z0-9_-])')


@dataclass(frozen=True)
class Checkout:
    order: str
    token: str = field(repr=False)
    at: float
    note: str = ''          # grade_note() text, written just before this checkout's line

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


def compose(text, issued, cancelled, now):
    """Keep prose, rebuild all order/token pairs from trusted issuance metadata. The signed originals open the reply
    so every one of them is inside the VISIBLE characters the customer reads; prose follows, grade notes last."""
    active = {c.digest: c for c in issued if c.order not in cancelled and 0 <= now - c.at < HOLD_SECONDS}
    if len({c.order for c in active.values()}) != len(active):
        raise ValueError('duplicate checkout order ids')
    body = clean_reply(text)
    if not active:
        return body[:REPLY_MAX], ()
    lines = [f'checkout_jwt ({c.order}): {c.token}' for c in active.values()]
    head = '\n\n'.join(lines)
    if len(head) > VISIBLE:
        fit, used = 0, 0
        for x in lines:
            used += len(x) + (2 if fit else 0)
            if used > VISIBLE:
                break
            fit += 1
        raise ValueError(f'결제서 {len(lines)}장은 손님 도구가 읽는 답장 앞 2,000자를 넘습니다. 이 답장에는 {fit}장만 남기고 '
                         f'나머지 order는 checkout_cancel 목록으로 빼세요. 손님께는 나머지는 다음 메시지를 주시면 이어서 '
                         f'결제서를 드린다고 쓰세요')
    notes = '\n'.join(c.note for c in active.values() if c.note)
    rest = REPLY_MAX - len(head) - 2
    if notes and len(body) + 2 + len(notes) <= rest:   # grade notes give way to prose, prose to the originals
        tail = (body + '\n\n' if body else '') + notes
    else:
        tail = body[:rest].rstrip()
    return head + ('\n\n' + tail if tail else ''), tuple(active.values())


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
        day = lambda t: datetime.fromtimestamp(t, KST).strftime('%m-%d %H:%M KST')
        sold, ends, left = day(sa), day(ea), max(0, int((ea - now) // 3600))
    except Exception:                                # a note never stops the checkout itself
        return ''
    return (f'등급 근거 ({order}, {unit}): 결제서 안 cert는 시장이 서명한 원본이라 가게가 고칠 수 없고, 이 cert의 grade 칸은 '
            f'null입니다. 시장 규칙(market.ourolabs.ai/rules, Round 4에 이어지는 food 규칙: "The shelf life left is the grade: '
            f'A plenty, B some, C near its date")상 식품 등급은 남은 소비기한이고, 같은 cert에 시장 서명으로 입고 {sold}, '
            f'소비기한 {ends}(지금부터 약 {left}시간)이 들어 있습니다. claim={claim}는 가게가 서명한 등급입니다. 입고 기록은 '
            f'GET /api/ledger?after=<seq> supply 행의 data.units에 {unit}이 있는지로, 정산 뒤 봉인된 판매 내용은 거래 당사자(손님 '
            f'계정)로 /api/ledger/sealed?order={order}에서 확인할 수 있습니다.')


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
