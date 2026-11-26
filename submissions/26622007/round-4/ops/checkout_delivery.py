"""Private checkout reply integrity. JWTs are retained in pending Event memory only."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import base64
import json
import re

HOLD_SECONDS = 300   # Same as the existing private_checkout unit reservation.
DEADLINE = 600
REPLY_MAX = 4000
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
    """Keep prose, rebuild all order/token pairs from trusted issuance metadata."""
    active = {c.digest: c for c in issued if c.order not in cancelled and 0 <= now - c.at < HOLD_SECONDS}
    if len({c.order for c in active.values()}) != len(active):
        raise ValueError('duplicate checkout order ids')
    body = clean_reply(text)
    lines = lambda notes: '\n\n'.join((c.note + '\n' if notes and c.note else '') + f'checkout_jwt ({c.order}): {c.token}'
                                       for c in active.values())
    suffix = lines(True)
    if len(suffix) > REPLY_MAX:                      # the signed originals come first; grade notes give way
        suffix = lines(False)
    if len(suffix) > REPLY_MAX:
        raise ValueError('too many checkouts for one reply; explicitly cancel unneeded orders')
    if not suffix:
        return body[:REPLY_MAX], ()
    lead = body[:max(0, REPLY_MAX - len(suffix) - 2)].rstrip()
    return (lead + '\n\n' if lead else '') + suffix, tuple(active.values())


def _fields(token):
    """item, price, grade claim and unit from the token's own signed payload ({} when it does not read)."""
    try:
        body = token.split('.')[1]
        pl = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
        return {k: pl[k] for k in ('item', 'price', 'claim', 'unit') if k in pl and isinstance(pl[k], (str, int))}
    except Exception:
        return {}


def attach_data(result, attached, cancelled=()):
    """Also put each attached checkout in the task's artifacts as an A2A data part (11/27 06:40 check): the market's
    customer tool reads only the first 2,000 characters of a reply's text, so a second checkout after the first one's
    grade line never reached it, and a customer that copies an ~850-character token by hand can change it ("not
    signed by this shop's wallet"); the data part is read whole and buy uses it when the customer names no token.
    Each entry is {checkout_jwt, order} plus item, price, claim and unit read from the same signed payload, so a
    customer with several can tell them apart. A checkout reply that attaches none (all cancelled or past their hold)
    carries {checkouts: [], cancelled: [...]} instead, so the customer's last data no longer holds an old token.
    The same signed originals as in the text; nothing else changes. Returns the number of checkouts attached."""
    if not isinstance(result, dict):
        return 0
    data = [dict(_fields(c.token), checkout_jwt=c.token, order=c.order) for c in attached]
    parts = [{'data': d} for d in data] or [{'data': {'checkouts': [], 'cancelled': sorted(str(o) for o in cancelled)}}]
    if result.get('kind') == 'task':                 # the v0.3 binding names each part's kind
        for p in parts:
            p['kind'] = 'data'
    arts = result.get('artifacts')
    if not isinstance(arts, list):
        arts = result['artifacts'] = []
    arts.append({'artifactId': 'checkout-' + (data[0]['order'] if data else 'none'), 'name': 'checkout', 'parts': parts})
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
