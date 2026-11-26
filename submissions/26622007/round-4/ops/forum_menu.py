"""forum_menu: a short forum post of what we really hold, at the price a checkout will carry, every ~5 minutes.

Why (rank-up 11/26): customers have no A2A card, so a shop cannot write to them first. What a customer reads before
choosing a shop is the forum: each visit shows the newest shop posts, cut to 160 characters each, in about 1,300
characters (house/customer.py forum_text). The top shops post every 5 minutes (the market's limit for a shop) with a
price list; we posted about twice an hour, only after a restock. This keeps one line of ours in that view.

Prices: the shown price per SKU and grade lives in logs/quotes.json (the shared quote block that check_item_inventory,
restock_shoutout and private_checkout read). Goal (4) (operator 11/25) says to price a grade at what the same SKU and
grade recently settled for on the public ledger. Before a post, a grade we hold whose stored quote is missing or above
that 24 h median (3 or more settles) is requoted to max(its floor, the median). The floor is restock_shoutout's
(dearest unit's cost + gas + fee) / 0.99, so nothing is offered below it. Water keeps its fixed price: it is never
requoted here.

Posting (q8, 11/26 19:3x): a customer sees only the newest ~7 shop posts (160 characters each), about 3 minutes
of forum, so the menu takes every market slot (POST_GAP) it can. It holds back only around the window's own restock
post: while a supply window is open, AFTER_WINDOW after it, AFTER_WINDOW_LONG when restock_shoutout has no entry for
it yet (its entries come 1-2 minutes into the window), SOON before a window opens (so our slot is free again early
in the window), and while a restocked window's post is still to come: ANNOUNCE_WAIT when it names customers (the
window's own names: waitlist, waited, regular), ANNOUNCE_GRACE otherwise (the menu then shows that stock itself). Only when our newest post on the forum is
at least POST_GAP old. Items: soonest to expire first (EXPIRING hours), then the most units we hold, then market sales. The market applies its own forum rules. Turn the menu off without a
restart by creating ops/forum_menu.off; posts and skips are logged to logs/forum-menu.jsonl (a skip once per reason
change or 10 minutes). No wallet, no payment, no customer names.
"""
import json
import math
import os
import re
import statistics
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = 'https://market.ourolabs.ai'  # used when the kit's Market has no base
POST_GAP = 305              # the market allows one shop post per 5 minutes
ANNOUNCE_WAIT = 600         # a restocked window whose post names customers keeps the menu quiet this long (q8: every
                            # restock post seen on 11/26 went up within 5 s of its 'restocked' entry, or never)
ANNOUNCE_GRACE = 300        # ... and one that names nobody: its post is the agent's to make first, then the menu's
BACKFILL = 8000             # ledger rows read once at start (about a day), 500 a page, 1.1 s apart
SKIP_LOG = 600              # the same skip reason is logged again after this long
TICK = 30                   # how often the thread looks
AFTER_WINDOW = 90
AFTER_WINDOW_LONG = 180       # no restock entry yet for the window that closed: hold the slot this long
SOON = POST_GAP - 50          # a post this close to the next window's opening would still hold the slot inside it
EXPIRING = 48                 # hours: stock this close to its date is shown first
WINDOW_SPAN = 900             # an entry this long before the window's last open tick belongs to it
SETTLE_TTL = 24 * 3600      # settles kept for the medians and the popularity order
MIN_SETTLES = 3             # settles of one SKU and grade before its median counts
VIEW = 160                  # a customer sees the first 160 characters of a shop post
TAIL = ' — 말씀하시면 바로 결제서 드려요'
FIXED_PRICE = ('생수', '샘물', '삼다수', '먹는물', '아이시스', '백산수', '에비앙', '워터', 'water', 'Water')
FIXED_SKU = ('G00b8acf0',)      # 생수 2L: never requoted (operator: the water price stays as it is)
WORDS = [('총각김치', '총각김치'), ('배추김치', '배추김치'), ('포기김치', '배추김치'), ('김치', '김치'), ('계란', '계란'),
         ('유정란', '계란'), ('등급란', '계란'), ('특란', '계란'), ('대란', '계란'), ('삼겹살', '삼겹살'), ('목살', '목살'), ('둥굴레차', '둥굴레차'), ('취나물', '취나물'), ('시금치', '시금치'), ('콩나물', '콩나물'),
         ('두부', '두부'), ('쌈채소', '쌈채소'), ('샐러드', '샐러드채소'), ('가자미', '가자미'), ('고등어', '고등어'),
         ('커피', '커피'), ('요거트', '요거트'), ('우유', '우유'), ('생수', '생수'), ('사과', '사과'), ('감귤', '감귤'),
         ('단호박', '단호박'), ('컵라면', '컵라면'), ('라면', '라면'), ('씻어나온쌀', '쌀'), ('백미', '쌀'), ('쌀', '쌀'),
         ('단팥빵', '단팥빵'), ('찰보리빵', '찰보리빵'), ('빵', '빵'), ('고구마스틱', '고구마스틱'), ('과자', '과자'),
         ('오뚜기밥', '즉석밥'), ('햇반', '즉석밥'), ('한돈', '돼지고기'), ('돼지', '돼지고기'), ('녹차', '녹차'),
         ('허브차', '허브차'), ('깻잎', '깻잎'), ('애호박', '애호박'), ('양파', '양파'), ('감자', '감자'), ('두유', '두유')]


NOT_IT = ('과자', '만두', '호떡', '떡', '면', '사발', '전분', '주스', '스낵', '칩', '양념', '소스', '라떼', '음료', '쿠키', '케이크', '국수', '즙', '식빵', '스틱')
SKIP_WORD = re.compile(r'^(\d{2,4}년?|유기농|국내산|국산|정식수입|산지직송|당일수확|무농약|본사공식|햅쌀|\d+(?:\.\d+)?\w*|[A-Za-z]+)$')


def label(name):
    """A short word a customer would ask for, with its size: '계란20구', '쌀5kg'. A keyword counts only when the
    word it sits in is not another product made with it (두부과자, 김치만두, 찹쌀호떡). Without a keyword, the first
    word that is not a year, a brand in Latin letters or a selling word."""
    n = re.sub(r'\[[^\]]*\]|\([^)]*\)', ' ', str(name or '')).strip()
    n = re.sub(r'(\d)\s*[x×*]\s*(\d)', r'\1 x\2', n)
    sizes = re.findall(r'(\d+(?:\.\d+)?)\s*(kg|g|L|ml|구|개입|입|팩|개)(?![A-WYZa-wyz가-힣])', n, re.I)
    tag = (sizes[0][0] + sizes[0][1]) if sizes else ''
    words = [re.sub(r'\d+(?:\.\d+)?\s*(kg|g|L|ml|구|개입|입|팩|개)$', '', w, flags=re.I) or w for w in n.split()]
    words = [w for w in words if w]
    made = next((w for w in words if any(x in w for x in NOT_IT)), None)
    if made:                                   # 꿀호떡, 두부과자, 고구마스틱: the made thing is what is sold
        return re.sub(r'[^0-9A-Za-z가-힣]', '', made)[:8] + tag
    for key, word in WORDS:                    # 1+등급란, 생 삼겹살: a word that ends in the keyword
        for w in words:
            if w.endswith(key) and len(w) - len(key) <= 3:
                return word + tag
    for key, word in WORDS:
        for w in words:
            if key in w and not any(x in w.replace(key, '', 1) for x in NOT_IT):
                return word + tag
    first = next((w for w in words if not SKIP_WORD.match(w)), words[0] if words else '?')
    return re.sub(r'[^0-9A-Za-z가-힣]', '', first)[:8] + tag


def load_quote_block(path):
    """restock_shoutout's logic.py, run in its own namespace (as the tool runner and its tests do)."""
    ns = {'__name__': 'restock_shoutout_logic'}
    exec(compile(Path(path).read_text(encoding='utf-8'), str(path), 'exec'), ns)
    return ns


class Settles:
    """Recent settles from the public ledger: sku -> grade -> [(ts, amount)], plus counts per SKU."""

    def __init__(self):
        self.rows, self.last = {}, None           # seq -> (ts, sku, grade, amount or None)

    def add(self, rows, now):
        for r in rows or []:
            if not isinstance(r, dict) or not isinstance(r.get('seq'), int):
                continue
            self.last = r['seq'] if self.last is None else max(self.last, r['seq'])
            d = r.get('data') if isinstance(r.get('data'), dict) else {}
            if r.get('kind') == 'settle' and d.get('sku'):
                amt = d.get('amount')
                amt = amt if isinstance(amt, int) and not isinstance(amt, bool) and amt > 0 else None
                try:
                    self.rows[r['seq']] = (_sec(r.get('ts')), d['sku'], str(d.get('grade') or ''), amt)
                except (TypeError, ValueError):
                    continue                       # one odd row is skipped, not the menu
        self.rows = {k: v for k, v in self.rows.items() if now - v[0] < SETTLE_TTL}   # rows newer than now stay

    def count(self, sku):
        return sum(1 for v in self.rows.values() if v[1] == sku)

    def median(self, sku, grade):
        a = [v[3] for v in self.rows.values() if v[1] == sku and v[2] == grade and v[3]]
        return int(statistics.median(a)) if len(a) >= MIN_SETTLES else None


# --- shared clearance block (떨이, 대표님 11/26 19:05; same text in private_checkout, check_item_inventory, write_shop_card, refresh_full_card, ops/forum_menu.py; ops/test_clearance_block.py)
# r = hours left / min(the item's whole shelf life, 48 h) (shelf life: catalog shelf_hours by category, the
# certificate's span when unknown; 48 h so a 30-day item with days left is not marked down).
# r > 0.5: not on clearance. 0.25 < r <= 0.5: price = base x (0.5 + r), never under cost + fees (no loss).
# r <= 0.25: the same price, and the floor drops with time: ceil((cost x r / 0.25 + 205 + 210) / 0.99), 420 at the end,
# so a unit that would be thrown away (0 credits) still sells for more than its fee. Water is never on clearance.
CLEAR_HALF, CLEAR_LOSS, CLEAR_SPAN = 0.5, 0.25, 48
CLEAR_GAS, CLEAR_FEE = 205, 210
CLEAR_WATER = ('생수', '샘물', '삼다수', '먹는물', '아이시스', '백산수', '에비앙', '워터', 'water', 'Water')
CLEAR_WATER_SKU = ('G00b8acf0',)
CLEAR_SHELF = {                     # catalog shelf_hours by food category (식품> dropped), /api/catalog 11/26 19:30
    '건강용품/다이어트/건강기능>다이어트식품/장건강': 720, '건강용품/다이어트/건강기능>비타민/미네랄/영양제': 720, '건강용품/다이어트/건강기능>전통건강식품': 720,
    '건강용품/다이어트/건강기능>헬스보조식품': 720, '계란/유제품>계란': 96, '계란/유제품>버터': 96, '계란/유제품>요거트': 96, '계란/유제품>우유/두유': 96,
    '계란/유제품>치즈': 96, '기타 가공식품>과자': 720, '기타 가공식품>국수/파스타/누들': 720, '기타 가공식품>기능성 가공식품': 720, '기타 가공식품>기타 가공식품': 720,
    '기타 가공식품>꿀/잼': 720, '기타 가공식품>디저트': 48, '기타 가공식품>샌드위치': 720, '기타 가공식품>생식/선식': 720, '기타 가공식품>스프': 720,
    '기타 가공식품>영유아 식음료': 720, '기타 가공식품>통조림캔': 720, '기타 가공식품>한과/떡': 48, '김치/반찬/즉석요리>김치': 168, '김치/반찬/즉석요리>반찬/젓갈': 168,
    '김치/반찬/즉석요리>즉석조리식품': 24, '김치/반찬/즉석요리>찌개/탕류/장류': 720, '빵/제과>베이킹 용품/재료': 720, '빵/제과>비스켓/쿠키': 720, '빵/제과>빵': 48,
    '빵/제과>케이크/파이': 48, '시리얼/곡물/콩류>가공 시리얼 제품': 720, '시리얼/곡물/콩류>곡물': 720, '시리얼/곡물/콩류>시리얼/곡물/콩류세트': 720,
    '시리얼/곡물/콩류>씨리얼/밀가루': 720, '식용류/지방유지류>오일': 720, '식품선물세트>식품선물세트': 720, '야채/과일>기타 채소/과일': 168,
    '야채/과일>신선과일(부패주의)': 48, '야채/과일>신선야채(부패주의)': 48, '양념/조미료/소스>소스/양념/조미료': 720, '양념/조미료/소스>식초/요리용 와인': 720,
    '양념/조미료/소스>허브/향신료': 720, '육류>가공/조리 육류': 720, '육류>기타 육류': 720, '육류>신선육류': 24, '음료>주스/코코아/생수': 720, '음료>커피/차': 720,
    '조리/가공 과일/야채/견과류>조리/가공 견과류': 720, '조리/가공 과일/야채/견과류>조리/가공 과일': 720, '조리/가공 과일/야채/견과류>조리/가공 야채': 168,
    '초콜릿/캔디/시럽>시럽': 720, '초콜릿/캔디/시럽>초콜릿/캔디/젤리': 720, '해산물>가공/조리 생선': 720, '해산물>가공/조리 오징어/문어류': 720,
    '해산물>가공/조리 해조류': 720, '해산물>기타 해산물': 720, '해산물>신선 오징어/문어류': 24, '해산물>신선생선': 24, '해산물>신선조개류/갑각류': 24,
    '해산물>신선해조류': 24,
}


def _clear_num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e15 else None


def clear_water(u):
    name = str(u.get('name') or '')
    return u.get('sku') in CLEAR_WATER_SKU or any(w in name for w in CLEAR_WATER)


def clear_ratio(u, now):
    """(hours left, r) for a food unit still good; None for goods, water, a unit without a date or past it."""
    if not isinstance(u, dict) or u.get('kind') not in (None, 'food') or clear_water(u):
        return None
    e = _clear_num(u.get('expires_at'))
    if e is None or e <= now:
        return None
    left = (e - now) / 3600
    cat = str(u.get('category') or '')
    shelf = CLEAR_SHELF.get(cat[3:] if cat.startswith('식품>') else cat)
    if not shelf:
        c = u.get('cert') if isinstance(u.get('cert'), dict) else {}
        c = c.get('cert') if isinstance(c.get('cert'), dict) else c
        s = _clear_num(c.get('supplied_at')) or _clear_num(u.get('supplied_at'))
        shelf = (e - s) / 3600 if s is not None and s < e else None
    if not shelf:
        return None
    return left, min(1.0, left / min(shelf, CLEAR_SPAN))


def clearance(u, base, now):
    """None, or {'price', 'floor', 'hours_left', 'r', 'loss'} for a unit on clearance. base: the unit's usual price
    (the shown price; 0.6 x list price when there is none)."""
    lr = clear_ratio(u, now)
    if lr is None or lr[1] > CLEAR_HALF:
        return None
    left, r = lr
    cost = _clear_num(u.get('cost'))
    if cost is None or cost < 0:                       # no number to price from (review M1): not on clearance
        return None
    share = min(1.0, r / CLEAR_LOSS)
    floor = math.ceil((cost * share + CLEAR_GAS + CLEAR_FEE) / 0.99)
    b = _clear_num(base) or round(0.6 * (_clear_num(u.get('list_price')) or 0))
    price = max(floor, min(int(b), int(round(b * (0.5 + r), -1))))   # never above the usual price (review M2)
    return {'price': price, 'floor': floor, 'hours_left': round(left, 1), 'r': round(r, 3),
            'loss': price < math.ceil((cost + CLEAR_GAS + CLEAR_FEE) / 0.99)}
# --- end shared clearance block


def clear_item(inv, sku, shown, now):
    """(hours left, grade, clearance) of this SKU's soonest unit on clearance; None when none is (shared block)."""
    best = None
    for u in inv.get('units') or []:
        if not (isinstance(u, dict) and u.get('sku') == sku):
            continue
        g = str(u.get('grade') or '?')
        try:
            cl = clearance(u, shown.get(g), now)
        except Exception:
            cl = None
        if cl and (best is None or cl['hours_left'] < best[0]):
            best = (cl['hours_left'], g, dict(cl, until=float(u['expires_at'])))
    return best


def fixed(name, sku=None):
    """Water keeps the price it has: by SKU, or by any water word in the name (also inside brackets)."""
    return sku in FIXED_SKU or any(w in str(name or '') for w in FIXED_PRICE)


def requote_plan(qb, inv, quotes, settles, now):
    """{sku: {grade: new price}} for the grades we hold whose quote is missing or above max(floor, 24 h median)."""
    plan, work = {}, dict(quotes)                # work: the quotes as they will be once the plan is written
    for sku in sorted({u.get('sku') for u in inv.get('units') or [] if isinstance(u, dict) and u.get('sku')}):
        st = qb['stock'](inv, sku, now)
        if not st or fixed(st['name'], sku):
            continue
        for g in sorted(st['floors'], key=lambda g: qb['QUOTE_RANK'].index(g) if g in qb['QUOTE_RANK'] else -1):
            med = settles.median(sku, g)
            if med is None:
                continue
            target = max(int(st['floors'][g]), med)
            have = qb['quote_get'](quotes, sku, g, now)
            if have is not None and have <= target:
                continue
            # No quote yet: the menu sets max(floor, market median) and, as quote_set keeps the first quote, that is
            # the price the agent then gives for this grade. It is never below the floor (the hard price floor).
            fit = qb['quote_fit'](work, sku, g, target, int(st['floors'][g]), now)   # what would be stored
            if have is None or fit < have:
                plan.setdefault(sku, {})[g] = target
                work[f'{sku}|{g}'] = {'price': fit, 'ts': now, 'by': 'forum_menu'}
    return plan


def apply_plan(qb, path, inv, plan, now):
    """quote_set's rules (expired quotes dropped, grade order kept by quote_fit), written through a temp file of our
    own: the tools write through quotes.json.tmp, and two writers on one temp name can lose a quote. The file is read
    again right before the write so a quote a tool has just stored is kept."""
    path = str(path)
    try:
        seen = os.stat(path).st_mtime_ns
    except OSError:
        seen = None
    q = qb['quotes_load'](path)
    q = {k: v for k, v in q.items() if isinstance(v, dict) and isinstance(v.get('ts'), (int, float))
         and 0 <= now - v['ts'] < qb['QUOTE_TTL']}
    out = {}
    rank = qb['QUOTE_RANK']
    for sku, prices in plan.items():
        st = qb['stock'](inv, sku, now)
        if not st:
            continue
        for g in sorted(prices, key=lambda g: rank.index(g) if g in rank else -1):
            have = qb['quote_get'](q, sku, g, now)
            p = qb['quote_fit'](q, sku, g, int(prices[g]), int(st['floors'].get(g, 0)), now)
            if have is not None and p >= have:     # a tool re-quoted it meanwhile, or the fit would not lower it
                continue
            q[f'{sku}|{g}'] = {'price': p, 'ts': now, 'by': 'forum_menu'}
            out.setdefault(sku, {})[g] = p
    try:
        now_seen = os.stat(path).st_mtime_ns
    except OSError:
        now_seen = None
    if now_seen != seen:                           # a tool wrote quotes.json while we read it: try next post.
        # (A write between this stat and os.replace can still be lost; the window is a few ms and no price goes
        # below its floor either way.)
        return {}
    if out:
        tmp = f'{path}.menu.{os.getpid()}.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(q, f, ensure_ascii=False, separators=(',', ':'))
        os.replace(tmp, path)
    return out


def build_text(qb, inv, quotes, settles, now, turn, name='노차네 구멍가게(nocha12)'):
    """(text, [sku]) — the cheapest shown grade of each item we hold: what expires within EXPIRING hours first
    (soonest first), then the most units we hold, then the most-sold SKUs on the market; the first two stay, the rest
    rotate with turn so successive posts show different stock. None when nothing has a price. Clearance (떨이, 11/26
19:05): SKUs with a unit on clearance (shared clearance block) come before all of these, soonest first, at the clearance
price of that unit with its hours left, and the post reads '떨이·기한 임박'; water never."""
    items = []
    for sku in {u.get('sku') for u in inv.get('units') or [] if isinstance(u, dict) and u.get('sku')}:
        st = qb['stock'](inv, sku, now)
        if not st:
            continue
        shown = {g: p for g, p in qb['quote_prices'](quotes, sku, st['floors'], now).items() if p}
        clr = None if fixed(st['name'], sku) else clear_item(inv, sku, shown, now)
        if clr:                                    # 떨이 11/26 19:05: before everything else, at its clearance price
            left, g, cl = clr
            items.append(((-1, left), -st['count'], -settles.count(sku), sku,
                          f"{label(st['name'])} {g}{cl['price']:,}(~{time.strftime('%H:%M', time.localtime(cl['until']))})"))
            continue
        if not shown:
            continue
        g = min(shown, key=lambda k: (shown[k], k))
        left = soonest(inv, sku, g, now)
        urgent = left is not None and left <= EXPIRING
        items.append(((0, left) if urgent else (1, 0), -st['count'], -settles.count(sku), sku,
                      f"{label(st['name'])} {g}{shown[g]:,}"))
    if not items:
        return None, []
    items.sort()
    clear = [it for it in items if it[0][0] == -1]
    items = items[len(clear):]
    head, rest = items[:2], items[2:]
    if rest:
        k = (turn * 3) % len(rest)
        rest = rest[k:] + rest[:k]
    text, chosen, seen = '', [], set()
    room = VIEW - len(f"[{name} {'떨이·기한 임박' if clear else '지금 재고'}]")   # the longer header when it may be used
    for it in clear + head + rest:
        if it[4].split()[0] in seen:              # one line per label: two '계란20구' lines read as a mistake
            continue
        add = (' ' if not chosen else ' · ') + it[4]
        if len(text) + len(add) + len(TAIL) > room:
            continue
        text += add
        chosen.append(it[3])
        seen.add(it[4].split()[0])
    if not chosen:
        return None, []
    lead = '떨이·기한 임박' if any(it[0][0] == -1 and it[3] in chosen for it in clear) else '지금 재고'   # what fits (N3)
    return f'[{name} {lead}]' + text + TAIL, chosen


def soonest(inv, sku, grade, now):
    """Hours to the nearest date among our units of this SKU and grade still good; None when none has a date."""
    left = []
    for u in inv.get('units') or []:
        if isinstance(u, dict) and u.get('sku') == sku and str(u.get('grade') or '?') == grade:
            e = u.get('expires_at')
            if isinstance(e, (int, float)) and not isinstance(e, bool) and e > now:
                left.append((e - now) / 3600)
    return round(min(left), 1) if left else None


def window_open(inv):
    r = inv.get('restock') if isinstance(inv.get('restock'), dict) else {}
    return bool(r.get('open'))                    # closed: opens_at/closes_at are the next window's, in the future


def window_noted(shoutout, last_open):
    """True when restock_shoutout has an entry for the window last seen open (or we never saw one open)."""
    if not last_open:
        return True
    if not isinstance(shoutout, dict) or not isinstance(shoutout.get('windows'), dict):
        return False
    w = shoutout['windows'].get(shoutout.get('last_window'))
    ts = w.get('ts') if isinstance(w, dict) and isinstance(w.get('ts'), (int, float)) else 0
    return ts >= last_open - WINDOW_SPAN


def waitlisted_window(shoutout, waitlist):
    """True when the last window has restocked SKUs, no settled restock post, and a waitlisted customer for one of
    them: the one restock post the menu cannot stand in for. Only SKUs and names are read, never contacts."""
    if not isinstance(shoutout, dict) or not isinstance(shoutout.get('windows'), dict):
        return False
    w = shoutout['windows'].get(shoutout.get('last_window'))
    if not isinstance(w, dict) or not isinstance(w.get('skus'), dict) or w.get('posted') not in (None, 'pending'):
        return False
    keys = set(w['skus']) | {str(v.get('name')) for v in w['skus'].values() if isinstance(v, dict) and v.get('name')}
    entries = waitlist.get('entries') if isinstance(waitlist, dict) else None
    return any(isinstance(e, dict) and e.get('sku') in keys for e in entries or [])


def last_window(shoutout):
    if not isinstance(shoutout, dict) or not isinstance(shoutout.get('windows'), dict):
        return None
    w = shoutout['windows'].get(shoutout.get('last_window'))
    return w if isinstance(w, dict) else None


def names_customers(shoutout):
    """True when restock_shoutout picked customers to name in this window's post (its own record, as the post reads)."""
    w = last_window(shoutout)
    skus = w.get('skus') if w else None
    return isinstance(skus, dict) and any(isinstance(v, dict) and isinstance(v.get('names'), dict) and v['names']
                                          for v in skus.values())


def window_age(shoutout, now):
    w = last_window(shoutout)
    ts = w.get('ts') if w and isinstance(w.get('ts'), (int, float)) and not isinstance(w.get('ts'), bool) else None
    return None if ts is None else now - ts


def announce_due(shoutout, now, waitlist=None):
    """restock_shoutout's window state: True while this window has restocked SKUs and no settled restock post, for
    ANNOUNCE_WAIT when that post names customers and ANNOUNCE_GRACE when it names nobody."""
    if not isinstance(shoutout, dict):
        return False
    if isinstance(shoutout.get('paused_until'), (int, float)) and shoutout['paused_until'] > now:
        return False                              # it is not going to post
    w = (shoutout.get('windows') or {}).get(shoutout.get('last_window')) if isinstance(shoutout.get('windows'), dict) else None
    if not isinstance(w, dict) or not w.get('skus') or w.get('posted') not in (None, 'pending'):
        return False
    ts = w.get('ts') if isinstance(w.get('ts'), (int, float)) else 0
    wait = ANNOUNCE_WAIT if names_customers(shoutout) or waitlisted_window(shoutout, waitlist) else ANNOUNCE_GRACE
    return 0 <= now - ts < wait


def _sec(v):
    t = float(v or 0)
    return t / 1000 if t > 1e11 else t            # milliseconds, as restock_shoutout's _ts reads them


def _ts(p):
    try:
        return _sec(p.get('ts'))
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _dis(p):
    try:
        return int(p.get('dislikes') or 0)
    except (TypeError, ValueError):
        return 0


class Menu:
    def __init__(self, shop_dir, market, qb_path, clock=time.time, sleep=time.sleep):
        self.dir, self.market, self.clock, self.sleep = Path(shop_dir), market, clock, sleep
        self.qb = load_quote_block(qb_path)
        self.settles, self.turn, self.last_post, self.last_open = Settles(), 0, 0.0, 0.0
        self.not_before, self.backfilled, self.last_skip = 0.0, False, (None, 0.0)
        self.log = self.dir / 'logs' / 'forum-menu.jsonl'
        self.state = self.dir / 'logs' / 'forum-menu-state.json'
        self.retry_after = 0.0
        self.recent_disliked_post = 0.0
        try:
            with open(self.state, encoding='utf-8') as f:
                st = json.load(f)
            num = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0
            self.last_open = num(st.get('last_open'))
            self.retry_after = num(st.get('retry_after'))
            legacy_seen = st.get('seen') or {}
            old = (_ts(p) for p in legacy_seen.values() if _dis(p)) if isinstance(legacy_seen, dict) else ()
            self.recent_disliked_post = max([num(st.get('recent_disliked_post')), *old])
        except (OSError, ValueError, AttributeError):
            pass

    def save(self):
        try:
            tmp = f'{self.state}.{os.getpid()}.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({'last_open': self.last_open, 'retry_after': self.retry_after,
                           'recent_disliked_post': self.recent_disliked_post}, f)
            os.replace(tmp, self.state)
        except OSError:
            pass

    def base(self):
        b = getattr(self.market, 'base', None)
        return b.rstrip('/') if isinstance(b, str) and b.startswith('http') else BASE

    def _http(self, method, path, body=None):
        tok = (getattr(self.market, 'token', None) or {}).get('shop')
        if not tok:
            return 0, None
        req = urllib.request.Request(self.base() + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={'authorization': 'Bearer ' + tok, 'content-type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode() or 'null')
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode() or 'null')
            except ValueError:
                return e.code, None
        except (urllib.error.URLError, OSError, ValueError):
            return 0, None

    def _write(self, row):
        with open(self.log, 'a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')

    def skip(self, why, now):
        r = {'skip': why}
        last_why, at = self.last_skip
        if why != last_why or now - at >= SKIP_LOG:
            self.last_skip = (why, now)
            self._write(dict(r, ts=now, ok=False))
        return r

    def name(self):
        j = getattr(self.market, 'joined', None) or {}
        return (j.get('shop') or ('nocha12',))[0]

    def refresh_settles(self, now):
        """The first call reads about a day back (BACKFILL rows), later calls only what is new."""
        if not self.backfilled:
            st, rows = self._http('GET', '/api/ledger?after=-1')
            if (st != 200 or not isinstance(rows, list) or not rows or not isinstance(rows[-1], dict)
                    or not isinstance(rows[-1].get('seq'), int)):
                return
            after, pages = max(0, rows[-1]['seq'] - BACKFILL), BACKFILL // 500 + 2
        else:
            after, pages = self.settles.last, 3
        for _ in range(pages):
            st, rows = self._http('GET', f'/api/ledger?after={after}')
            if st != 200 or not isinstance(rows, list):
                return
            self.settles.add(rows, self.clock())
            if self.settles.last is None:
                self.settles.last = after          # an empty page still moves us past the backfill start
            self.backfilled = True
            if len(rows) < 500 or self.settles.last is None or self.settles.last <= after:
                return
            after = self.settles.last
            self.sleep(1.1)

    def our_posts(self):
        """Our posts on the page, or None when the forum cannot be read."""
        st, rows = self._http('GET', '/api/forum?role=shop&limit=100')
        if st == 200 and isinstance(rows, dict) and isinstance(rows.get('posts'), list):
            rows = rows['posts']
        if st != 200 or not isinstance(rows, list):
            return None                            # unknown: do not post on a guess
        rows = [p for p in rows if isinstance(p, dict)]
        me = self.name()
        return [p for p in rows if me in (p.get('author'), p.get('owner'))]

    def waitlist(self):
        try:
            with open(self.dir / 'waitlist.json', encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def shoutout(self):
        try:
            with open(self.dir / 'shoutout.json', encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def once(self):
        now = self.clock()
        if (self.dir / 'ops' / 'forum_menu.off').exists():
            return self.skip('off', now)
        st, inv = self._http('GET', '/api/inventory')
        if st != 200 or not isinstance(inv, dict):
            return self.skip(f'inventory {st}', now)
        if window_open(inv):
            if now - self.last_open > 60:
                self.last_open = now
                self.save()                        # a restart right after the window still waits for the restock post
            self.last_open = now
            return self.skip('supply window', now)
        if now - self.last_open < AFTER_WINDOW:
            return self.skip('supply window', now)
        so = self.shoutout()
        if now - self.last_open < AFTER_WINDOW_LONG and not window_noted(so, self.last_open):
            return self.skip('restock post may come', now)   # the agent has not run 'restocked' for it yet
        r = inv.get('restock') if isinstance(inv.get('restock'), dict) else {}
        opens = r.get('opens_at') if isinstance(r.get('opens_at'), (int, float)) else 0
        if 0 < opens - now < SOON:                 # keep the slot free for the window's restock post
            return self.skip('supply window soon', now)
        if now - self.last_post < POST_GAP or now < max(self.not_before, self.retry_after):
            return {'skip': 'gap'}
        if announce_due(so, now, self.waitlist()):
            return self.skip('restock post to come', now)
        posts = self.our_posts()
        if posts is None:
            return self.skip('forum unreadable', now)
        recent = max([self.recent_disliked_post] + [_ts(p) for p in posts if _dis(p) > 0])
        if recent != self.recent_disliked_post:
            self.recent_disliked_post = recent
            self.save()
        # Preserve priority for pending named-customer restock posts under the existing restock policy.
        so = self.shoutout()
        age = window_age(so, now)                  # q8: a post that never comes does not hold the menu for hours
        if 0 <= now - recent < 86400 and age is not None and 0 <= age < ANNOUNCE_WAIT and waitlisted_window(so, self.waitlist()):
            return self.skip('restock post for waitlisted customers goes first', now)
        last = max([_ts(p) for p in posts] + [0.0])
        if now - last < POST_GAP:
            self.last_post = max(self.last_post, last)
            return {'skip': 'gap'}
        self.refresh_settles(now)
        qpath = self.dir / 'logs' / 'quotes.json'
        quotes = self.qb['quotes_load'](str(qpath))
        plan = requote_plan(self.qb, inv, quotes, self.settles, now)
        changed = apply_plan(self.qb, qpath, inv, plan, now) if plan else {}
        quotes = self.qb['quotes_load'](str(qpath))
        text, chosen = build_text(self.qb, inv, quotes, self.settles, now, self.turn,
                                  f'노차네 구멍가게({self.name()})')
        if not text:
            return self.skip('no priced stock', now)
        if announce_due(self.shoutout(), self.clock(), self.waitlist()):   # the backfill can take minutes: look again
            return self.skip('restock post to come', now)
        code, body = self._http('POST', '/api/forum', {'text': text})
        received_at = self.clock()
        ok = 200 <= code < 300
        if ok or code == 0:
            self.last_post = received_at                   # a lost answer may still have posted: wait a full gap
        if code == 429:
            wait = body.get('wait') if isinstance(body, dict) else None
            wait = wait if isinstance(wait, (int, float)) and not isinstance(wait, bool) and 0 < wait < 3600 else 60
            self.retry_after = max(self.retry_after, received_at + wait)
            self.save()
        elif code and not ok:
            self.not_before = max(self.not_before, now + 60)   # a refusal is not retried every tick
        if ok:
            self.turn += 1
        row = {'ts': now, 'ok': ok, 'status': code, 'text': text, 'skus': chosen, 'requoted': changed,
               'settles_seen': len(self.settles.rows),
               'post': (body.get('id') or body.get('post')) if isinstance(body, dict) else None}
        self._write(row)
        return row

    def loop(self, stop):
        stop.wait(60)                              # let the kit finish starting
        while not stop.is_set():
            try:
                self.once()
            except Exception as e:                 # the shop goes on without its menu
                try:
                    self.skip(f'error {type(e).__name__}: {str(e)[:200]}', self.clock())
                except OSError:
                    pass
            stop.wait(TICK)


def start(shop_dir, market, qb_path):
    """One daemon thread per process; returns it."""
    stop = threading.Event()
    m = Menu(shop_dir, market, qb_path)
    t = threading.Thread(target=m.loop, args=(stop,), name='forum_menu', daemon=True)
    t.start()
    return t
