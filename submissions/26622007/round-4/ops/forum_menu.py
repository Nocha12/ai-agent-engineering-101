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
change or 10 minutes). No wallet, no payment, no customer names. The lines of each post that went up (SKU, grade,
price, and for a clearance line its unit and expiry) are kept for UNITS_KEEP in logs/forum-menu-units.json, so
check_item_inventory can mark the unit a customer read on the forum (11/27 05fcd1d5).
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
SHORT = 3                     # hours: a line with less left goes after every other line of the post (메인 11/27 03:5x:
                              # the market settles almost nothing this close to its date; nothing is hidden or refused)
LONG = 10                     # hours: up to LEAD_LONG lines with this long left (or no date) lead the post (11/27 06:40
LEAD_LONG = 2                 # check: of 880 market settles only 8 had under 10 h left, and two customers turned down the
                              # 3-5 h clearance lines; clearance lines still follow them; order only, prices unchanged)
LEAD_AB = 24                  # hours: the lead is first filled with lines not on clearance, of a LEAD_GRADES grade, with
LEAD_GRADES = ('A', 'B')      # this long left or no date (11/27 12:40 check: 20/20 posts led by 10-24 h clearance lines,
                              # 86% of market settles had 24 h+ left); too few: the LONG rule fills the rest. Order only
WINDOW_SPAN = 900             # an entry this long before the window's last open tick belongs to it
SETTLE_TTL = 24 * 3600      # settles kept for the medians and the popularity order
MIN_SETTLES = 3             # settles of one SKU and grade before its median counts
VIEW = 160                  # a customer sees the first 160 characters of a shop post
UNITS_FILE = 'forum-menu-units.json'   # logs/: the lines of our recent posts, read by check_item_inventory (11/27 05fcd1d5)
UNITS_KEEP = 3 * 3600       # posts older than this are dropped from it
UNITS_MAX = 40              # and at most this many are kept (one post per 5 minutes: about 3 hours)
TAIL = ' — 말씀하시면 바로 결제서 드려요'
FIXED_PRICE = ('생수', '샘물', '삼다수', '먹는물', '아이시스', '백산수', '에비앙', '워터', 'water', 'Water')
FIXED_SKU = ('G00b8acf0',)      # 생수 2L: never requoted (operator: the water price stays as it is)
# --- shared display name block (대표님 11/27 01:26; same text in ops/forum_menu.py, check_item_inventory, write_shop_card, refresh_full_card, restock_shoutout; ops/test_display_name.py)
# One rule for every place that shows an item's name (forum menu, check_item_inventory, the card and its 떨이 list):
# a product word, then every size and pack token of the catalog name (g·kg·ml·L·구·개·팩·병·입·마리·미·박스…, and a
# bare ×3 count) as written, never cut by characters; only the product word is short. display_names() names a whole
# shop at once so two items never read the same; display_check() is a separate rule check with its own reading of the
# name (① no two items alike ② every size/pack token kept ③ a kind word kept when the name has one).
DN_UNITS = r'(kg|mg|ml|cc|g|l|리터|개입|구|입|팩|개|병|판|캔|봉|펫|마리|미|박스|매|장|롤)'
DN_SIZE = re.compile(r'(?<![0-9.])((?:\d+(?:\.\d+)?\s*[~\-]\s*)?\d+(?:\.\d+)?)\s*' + DN_UNITS + r'(?![a-wyz](?<=[glc][a-wyz]))(?!국|월|년)'
                     r'(?!(?<=구)[성역간])(?!(?<=입)니)(?!(?<=미)[터역])(?!(?<=장)[어미])(?!(?<=롤)케)'
                     r'(?!(?<=\s[가-힣])(?!세트|묶음)[가-힣])(?!(?<=\s[가-힣]{2})(?!세트|묶음)[가-힣])', re.I)   # 3 병아리콩: a word, not 3병   # 1.4kg내외, 2L6병
DN_TIMES = re.compile(r'(?:(?<=[가-힣)\]])|(?<=kg|mg|ml|cc)|(?<=[0-9][gl]))\s*(?:(?:세트|박스)\s*)?[x×X*]\s*(\d+)(?![\d.])'
                      r'(?!\s*' + DN_UNITS + r')', re.I)   # 10개세트X3, 1kgx3: a bare count, ×3
DN_MORE = re.compile(r'\s*(?:(?:세트|박스)\s*)?[x×X*]\s*(\d+)(?![\d.])(?!\s*' + DN_UNITS + r')', re.I)   # 2 L x 6, x5 x2
DN_MEASURE = ('kg', 'mg', 'g', 'ml', 'cc', 'l')
DN_KINDS = ('실큰두부', '실크두부', '연두부', '순두부', '메추리알', '깻잎김치', '총각김치', '열무김치', '깍두기', '백김치', '파김치',
            '갓김치', '물김치', '두유', '그릭요거트')
DN_WORDS = [('총각김치', '총각김치'), ('배추김치', '배추김치'), ('포기김치', '배추김치'), ('김치', '김치'), ('계란', '계란'),
            ('유정란', '계란'), ('등급란', '계란'), ('특란', '계란'), ('대란', '계란'), ('달걀', '계란'), ('삼겹살', '삼겹살'),
            ('목살', '목살'), ('둥굴레차', '둥굴레차'), ('취나물', '취나물'), ('시금치', '시금치'), ('콩나물', '콩나물'),
            ('두부', '두부'), ('쌈채소', '쌈채소'), ('샐러드', '샐러드채소'), ('가자미', '가자미'), ('고등어', '고등어'),
            ('커피', '커피'), ('요거트', '요거트'), ('우유', '우유'), ('생수', '생수'), ('사과', '사과'), ('감귤', '감귤'),
            ('단호박', '단호박'), ('컵라면', '컵라면'), ('라면', '라면'), ('씻어나온쌀', '쌀'), ('백미', '쌀'), ('쌀', '쌀'),
            ('단팥빵', '단팥빵'), ('찰보리빵', '찰보리빵'), ('빵', '빵'), ('고구마스틱', '고구마스틱'), ('크래커', '크래커'),
            ('그래놀라', '그래놀라'), ('과자', '과자'),
            ('오뚜기밥', '즉석밥'), ('햇반', '즉석밥'), ('한돈', '돼지고기'), ('돼지', '돼지고기'), ('녹차', '녹차'),
            ('허브차', '허브차'), ('깻잎', '깻잎'), ('애호박', '애호박'), ('양파', '양파'), ('감자', '감자'),
            ('된장', '된장'), ('청국장', '청국장'), ('콜라', '콜라'), ('식혜', '식혜'), ('조미김', '김'), ('오이', '오이'),
            ('방울토마토', '방울토마토'), ('토마토', '토마토'), ('단감', '단감'), ('블루베리', '블루베리'), ('치킨', '치킨'),
            ('보리차', '보리차'), ('보리', '보리'), ('젓갈', '젓갈'), ('액젓', '액젓'), ('노가리', '노가리'), ('프레첼', '프레첼'), ('양갱', '양갱'),
            ('오믈렛', '오믈렛'), ('크런치바', '크런치바'), ('에너지바', '에너지바'), ('초코바', '초코바'), ('버섯탕', '버섯탕'),
            ('야채컵', '야채컵'), ('찹쌀떡', '찹쌀떡'), ('치즈케이크', '치즈케이크')]
DN_TAILS = ('탕', '케이크', '주스', '생수')   # 갈비탕, 초코 케이크, 토마토 주스: the word that ends in it (콤부차 after DN_WORDS)
DN_NOT_TAIL = ('사탕', '설탕', '명차', '홍차티백')
DN_ALONE = ('배', '귤', '무', '파', '김', '떡', '밤', '감', '쌀')   # one-syllable kinds: only as a word by itself (나주 배 5kg)
DN_NOT_IT = ('과자', '만두', '호떡', '떡', '면', '사발', '전분', '주스', '스낵', '칩', '양념', '소스', '라떼', '음료', '쿠키', '케이크',
             '국수', '즙', '식빵', '스틱', '찌개')
DN_SKIP = re.compile(r'^(\d{2,4}년산?|\d{2}년|유기농|국내산|국산|정식수입|산지직송|당일수확|무농약|본사공식|햅쌀|햇곡|Fresh|'
                     r'내외|이상|이하|미만|짜리|덤|세트|무료배송|\d+(?:\.\d+)?\w*|[A-Za-z]+)$')
DN_WORD_MAX = 8


def dn_norm(name):
    """The name as the rules read it: NFKC (㎏ ㎖ ℓ, full-width), ⅹ ✕ as ×, 1,000 as 1000; None is ''."""
    import unicodedata
    s = unicodedata.normalize('NFKC', '' if name is None else str(name))
    s = re.sub(r'[ⅹ✕✖]', '×', s)
    return re.sub(r'(?<=\d),(?=\d{3}(?!\d))', '', s)


def dn_num(num):
    """'1.50' -> '1.5', '007' -> '7', '4 ~ 6' -> '4~6'."""
    parts = [x.strip() for x in re.split(r'[~\-]', num)]
    return '~'.join(x.rstrip('0').rstrip('.') if '.' in x else x.lstrip('0') or '0' for x in parts)


def dn_tokens(name):
    """[(key, text, start, end)]: each size or pack token of the name once, in order (a range as 4~6미), and each bare
    count after a size or a word (key ('3', '×')); a count after a size already given is that size's again, not more."""
    s, out, seen = dn_norm(name), [], set()
    hits = {m.start(): (m.end(), m.group(1), m.group(2).lower()) for m in DN_SIZE.finditer(s)}
    for m in DN_TIMES.finditer(s):
        hits.setdefault(m.start(), (m.end(), m.group(1), '×'))
    for a in sorted(hits):                              # 생수 2 L x 6, 라면 120g x5 x2: the count right after a hit
        end = hits[a][0]
        while True:
            m = DN_MORE.match(s, end)
            if not m or any(x < m.end() and hits[x][0] > m.start() for x in hits if x != a and x >= end):
                break
            hits.setdefault(m.start(), (m.end(), m.group(1), '×'))
            end = m.end()
    rows = []
    for a in sorted(hits):
        b, num, unit = hits[a]
        num, unit = dn_num(num), 'l' if unit == '리터' else unit
        rows.append(((num, unit), '×' + num if unit == '×' else num + ('L' if unit == 'l' else unit), a, b))
    pairs, size, held = set(), None, None
    for row in rows:                                    # 1kg x3 (1kg x3): one; 500g x2 + 500g x3: both, each with its size
        key = row[0]
        if key[1] != '×':
            size, held = key, None
            if key in seen:
                held = row                              # a size given before: again only if a new count follows it
                continue
            seen.add(key)
            out.append(row)
            continue
        if (size, key[0]) in pairs:
            continue
        pairs.add((size, key[0]))
        if held:
            out.append(held)
            held = None
        out.append(row)
    return out


def dn_size(name):
    """'300g×5팩', '2L×6병,1팩', '10개×3': every token as written; a pack count right after a size joins it with ×."""
    s, toks, text = dn_norm(name), dn_tokens(name), ''
    for i, (key, t, a, b) in enumerate(toks):
        if key[1] == '×':
            text += t
        elif i and re.fullmatch(r'\s*[x×X*]?\s*', s[toks[i - 1][3]:a]) and key[1] not in DN_MEASURE:
            text += '×' + t
        else:
            text += (', ' if text[-1:].isdigit() else ',' if text else '') + t   # 135g×2, 150g: never read as 2,150
    return text


def dn_kind(name):
    """The kind word of the first name word that ends in one (실큰두부 in '실큰두부 300g … 연두부 순두부', 두유 in 완전두유),
    or ''; a word that only contains it is another thing (두유과자, 순두부찌개)."""
    for w in dn_words(name):
        hit = [k for k in DN_KINDS if w.endswith(k)]
        if hit:
            return max(hit, key=len)
    return ''


def dn_words(name):
    n = re.sub(r'\[[^\]]*\]|\([^)]*\)', ' ', dn_norm(name))
    n = re.sub(r'[x×*]\s*\d+[가-힣]*', ' ', DN_SIZE.sub(' ', n), flags=re.I)     # 라면x20봉: a count, not a word (1kgx3 사과 keeps 사과)
    n = re.sub(r'(?<=[가-힣])[x×*](?=\s|$)', ' ', n)                                   # the x left of 라면x20봉
    words = (re.sub(r'[^0-9A-Za-z가-힣+]', '', x) for x in re.split(r'[\s/,]+', n))
    words = (w if DN_SKIP.match(w) else re.sub(r'[\d+]+$', '', w) for w in words)   # 국산100% is 국산: no digit runs into the size
    return [w for w in words if w and not re.fullmatch(r'[\d+]+', w)]


def dn_word(name):
    """The product word: the kind word when there is one, else the made thing (두부과자), else a customer word for a
    keyword, else a one-syllable kind standing alone (나주 배), else the first word that is not a year, a Latin brand
    or a selling word. Only this part is ever shortened."""
    kind = dn_kind(name)
    if kind:
        return kind
    words = dn_words(name)
    tail = dn_tail(words, DN_TAILS)
    if tail:
        return tail
    made = next((w for w in words if any(x in w for x in DN_NOT_IT) and w not in DN_NOT_IT
                 and not DN_SKIP.match(w) and not w.endswith('용')), None)   # 10스틱 is a count, 주스용 사과 is 사과   # 양념 치킨: 양념 alone is not it
    if made:
        return made.replace('+', '')[:DN_WORD_MAX]
    for key, word in DN_WORDS:
        for w in words:
            if w.endswith(key) and len(w) - len(key) <= 3:
                return word
    for key, word in DN_WORDS:
        for w in words:
            if key in w and not any(x in w.replace(key, '', 1) for x in DN_NOT_IT):
                return word
    tail = dn_tail(words, ('차',))                                  # 콤부차, 청보리차 (보리 커피 is 커피 above)
    if tail:
        return tail
    alone = next((w for w in words if w in DN_ALONE), None)
    if alone:
        return alone
    first = next((w for w in words if not DN_SKIP.match(w)), words[0] if words else '')
    return first.replace('+', '')[:DN_WORD_MAX]


def dn_tail(words, tails):
    for i, w in enumerate(words):
        t = next((t for t in tails if w.endswith(t)), None)
        if not t or DN_SKIP.match(w) or w.endswith(DN_NOT_TAIL) or (t == '차' and len(w) < 3):
            continue
        if w == t and t in ('케이크', '주스') and i and not DN_SKIP.match(words[i - 1]):
            w = words[i - 1] + w                                   # 초코 케이크, 토마토 주스: the two words
        if w != t:
            return w.replace('+', '')[-DN_WORD_MAX:]
    return ''


def display_name(name):
    return dn_word(name) + dn_size(name)


def dn_own(name, base, others):
    """A word of this name that none of the others has: a plain word, else one in brackets or with a number (one letter
    tags too: (A) / (B)), else two words together; sizes are never the word. None when there is none."""
    def plain(w):
        return re.sub(r'[^0-9A-Za-z가-힣]', '', DN_SIZE.sub('', w))
    own = [w for w in dn_words(name) if not DN_SKIP.match(w) and w != base and not any(w in o for o in others)]
    own = next((w for w in own if base in w), None) or next(iter(own), None)   # 백진주쌀 before 밥이
    if own:
        return own
    raw = [plain(x) for x in re.split(r'[\s/,()\[\]]+', dn_norm(name))]
    raw = [w for w in raw if w and not re.fullmatch(r'\d+', w) and w != base]
    own = next((w for w in raw if len(w) >= 2 and not any(w in o for o in others)), None)
    if own:
        return own
    tags = [plain(t) for t in re.findall(r'[(\[]([^)\]]{1,3})[)\]]', dn_norm(name))]
    own = next((t for t in tags if t and not any(re.search(r'[(\[]\s*' + re.escape(t) + r'\s*[)\]]', o) for o in others)), None)
    if own:
        return own
    return next((a + b for i, a in enumerate(raw) for b in raw[i + 1:]
                 if a not in b and b not in a and not any(a in o and b in o for o in others)), None)


def display_names(names):
    """{sku: name} -> {sku: shown}. Items that would read the same get a word of their own name that the others do not
    have ('씻어나온쌀5kg', '백미쌀5kg'), never cut; any shown name still alike, with these or any other item, ends in the
    last 4 characters of its SKU (계란20구·7c8b), so no two items of a shop ever read the same."""
    names = {k: dn_norm(v) for k, v in names.items()}
    shown = {k: display_name(v) for k, v in names.items()}
    groups = {}
    for k, v in shown.items():
        groups.setdefault(v, []).append(k)
    for v, ks in groups.items():
        if len(ks) < 2:
            continue
        for k in ks:
            others = [names[o] for o in ks if o != k]
            base = dn_word(names[k])
            own = dn_own(names[k], base, others)
            if own:
                own = own.replace('+', '')
                cut = next((n for n in range(len(base) - 1, 0, -1) if own.endswith(base[:n])), 0)   # 유럽샐러드+샐러드채소
                shown[k] = (own if base in own else own + base[cut:]) + dn_size(names[k])
    by = {}
    for k in sorted(shown, key=str):
        by.setdefault(shown[k], []).append(k)
    for v, ks in by.items():
        if len(ks) > 1:
            n = 4
            while n < max(len(str(k)) for k in ks) and len({str(k)[-n:] for k in ks}) < len(ks):
                n += 1                                  # Gaaaa1111 / Gbbbb1111: as long as it takes to differ
            for k in ks:
                shown[k] = f'{v}·{str(k)[-n:]}'
    seen = {}
    for k in sorted(shown, key=str):
        seen.setdefault(shown[k], []).append(k)
    for v, ks in seen.items():
        if len(ks) > 1:                                 # still alike (1 and '1', a name that reads like a tail)
            for k in ks:
                shown[k] = f'{v}·{k!r}'
    return shown


def dn_check_tokens(name):
    """The rule check's own reading (not dn_tokens/DN_SIZE): the name is cut into number runs and the letters right
    after each; a number counts when those letters start with a unit of its own list, unless a longer word starts there
    (6개국, 3개월, 3구성, 2입니다, 6미터); a ×N or xN after a size or a Korean word is a count. Returns {(num, unit)}."""
    import unicodedata
    s = unicodedata.normalize('NFKC', '' if name is None else str(name))
    s = s.replace('ⅹ', '×').replace('✕', '×').replace('✖', '×')
    units = {'kg': 'kg', 'mg': 'mg', 'ml': 'ml', 'cc': 'cc', 'g': 'g', 'l': 'l', '리터': 'l', '개입': '개입', '구': '구',
             '입': '입', '팩': '팩', '개': '개', '병': '병', '판': '판', '캔': '캔', '봉': '봉', '펫': '펫', '마리': '마리',
             '미': '미', '박스': '박스', '매': '매', '장': '장', '롤': '롤'}
    not_unit = ('개국', '개월', '개년', '구성', '구역', '구간', '입니', '미터', '미역', '장어', '장미', '롤케', '판매', '봉지')
    out, i, n = set(), 0, len(s)
    while i < n:
        if not s[i].isdigit() or (i and (s[i - 1].isdigit() or (s[i - 1] == '.' and i > 1 and s[i - 2].isdigit()))):
            i += 1
            continue
        m = re.compile(r'\d{1,3}(?:,\d{3})+(?!\d)(?:\.\d+)?|\d+(?:\.\d+)?').match(s, i)   # 1,000g is one number; 2,4캔 two
        j, num = m.end(), m.group(0).replace(',', '')
        r = re.compile(r'\s*[~\-]\s*(\d+(?:\.\d+)?)').match(s, j)      # 4~6미: the range is the size
        if r:
            j, num = r.end(), num + '~' + r.group(1)
        k = j
        while k < n and s[k] == ' ':
            k += 1
        word = ''
        while k < n and (s[k].isalpha()):
            word += s[k]
            k += 1
        low = word.lower()
        hit = None
        if low and low[0].isascii():
            lat = re.match(r'[a-z]+', low).group(0)
            for u in ('kg', 'mg', 'ml', 'cc', 'g', 'l'):
                if lat == u or (lat.startswith(u) and lat[len(u)] == 'x'):
                    hit = u
                    break
        elif not any(low.startswith(w) for w in not_unit):
            hit = next((u for u in sorted(units, key=len, reverse=True) if low.startswith(u)), None)
            han = re.match(r'[가-힣]*', low).group(0)
            if hit and k - len(word) > j and han not in (hit, hit + '세트', hit + '묶음'):   # 3 병아리콩: a word
                hit = None
            hit = units[hit] if hit else None
        if hit:
            v = '~'.join(x.rstrip('0').rstrip('.') if '.' in x else x.lstrip('0') or '0' for x in num.split('~'))
            out.add((v, hit))
        i = j
    for m in re.finditer(r'[x×X*]\s*(\d+)', s):
        a = m.start()
        while a and s[a - 1] == ' ':
            a -= 1
        before = re.sub(r'\s+', '', s[max(0, a - 12):a]).lower()       # 2 L x 6: the size before, spaces out
        after = s[m.end():m.end() + 3].lstrip().lower()
        if any(after.startswith(u) for u in units) or (m.end() < n and s[m.end()] in '.'):
            continue                                    # 300g x 5팩: a size of its own, read above
        if before.endswith(('세트', '박스')):
            before = before[:-2]
        if (before and ('가' <= before[-1] <= '힣' or before[-1] in ')]')) or re.search(r'(kg|mg|ml|cc|\d[gl])$', before):
            out.add((m.group(1).lstrip('0') or '0', '×'))
    return out


def dn_check_kinds(name):
    """The rule check's own kind word: the longest kind of its own list that the first word ending in one ends with
    (outside [shop tags] and (notes)), as {kind: words that show it}; {} when no word ends in one."""
    kinds = {'실큰두부': ('실큰두부',), '실크두부': ('실크두부',), '연두부': ('연두부',), '순두부': ('순두부',),
             '총각김치': ('총각김치',), '열무김치': ('열무김치',), '포기김치': ('포기김치', '배추김치'), '두부': ('두부',), '두유': ('두유',), '김치': ('김치',), '깍두기': ('깍두기',), '계란': ('계란',), '달걀': ('달걀', '계란'),
             '우유': ('우유',), '요거트': ('요거트',), '커피': ('커피',), '양갱': ('양갱',), '오믈렛': ('오믈렛',), '탕': ('탕',),
             '케이크': ('케이크',), '생수': ('생수',), '주스': ('주스',), '라면': ('라면', '면'), '그래놀라': ('그래놀라',),
             '크래커': ('크래커',), '고등어': ('고등어',), '가자미': ('가자미',), '삼겹살': ('삼겹살',), '목살': ('목살',),
             '사과': ('사과',), '감귤': ('감귤',), '노가리': ('노가리',), '젓갈': ('젓갈',), '액젓': ('액젓',), '식혜': ('식혜',),
             '콜라': ('콜라',), '빵': ('빵',)}
    n = re.sub(r'\[[^\]]*\]|\([^)]*\)', ' ', '' if name is None else str(name))
    found = []
    for i, w in enumerate(x for x in re.split(r'[\s/,+]+', n) if x.strip()):
        w = re.sub(r'[^가-힣]', '', w)
        hit = [k for k in kinds if w.endswith(k)] if w and not w.endswith(('사탕', '설탕')) else []
        if hit:
            found.append((i, max(hit, key=len), w))
    if not found:
        return {}
    first = found[0]
    if first[0] == 0 and len(first[2]) > len(first[1]) and len(found) > 1:   # 연세우유 … 두유: the first word is the brand
        first = found[1]
    k = first[1]
    return {k: kinds[k]}


def display_check(names, shown):
    """{sku: [why]} for items whose shown name breaks a rule: ① another item reads the same ② a size/pack token of the
    name is missing ③ the name has a kind word and the shown name does not show it."""
    bad, by = {}, {}
    for k, v in shown.items():
        by.setdefault(v, []).append(k)
    for k, v in shown.items():
        why = []
        if len(by[v]) > 1:
            why.append('같은 표시 ' + v)
        have = dn_check_tokens(v)
        lost = ['×' + a if u == '×' else a + ('L' if u == 'l' else u)
                for a, u in sorted(dn_check_tokens(names.get(k))) if (a, u) not in have]
        if lost:
            why.append('용량·묶음 빠짐 ' + ','.join(lost))
        kinds = dn_check_kinds(names.get(k))
        if kinds and not any(x in v for shows in kinds.values() for x in shows):
            why.append('종류어 빠짐 ' + '/'.join(sorted(kinds)))
        if why:
            bad[k] = why
    return bad


def dn_live(units, now=None):
    """The units still good at now (no date, or a date after now); all of them when now is None."""
    out = []
    for u in units or []:
        if not isinstance(u, dict):
            continue
        e = u.get('expires_at')                         # as _fresh: 0 or no date is none
        if now is None or not (isinstance(e, (int, float)) and not isinstance(e, bool) and 0 < e <= now):
            out.append(u)
    return out


def shop_names(units, now=None):
    """{sku: shown name}, by the shared display name block (대표님 11/27 01:26): the SKUs on sale at now named among
    themselves (every place names the shop the same way; review r3 I5), a SKU with nothing on sale among all."""
    names = {}
    for u in units or []:
        if isinstance(u, dict) and u.get('sku') and u['sku'] not in names:
            names[u['sku']] = str(u.get('name') or u['sku'])
    if now is None:
        return display_names(names)
    on = {u.get('sku') for u in dn_live(units, now)}
    out = display_names({k: v for k, v in names.items() if k in on})
    every = display_names(names)
    used = set(out.values())
    for k in sorted((k for k in names if k not in on), key=str):   # nothing on sale: named among all, never like another
        v, n = every[k], 4
        while v in used:
            v = f'{every[k]}·{str(k)[-n:]}' if n <= len(str(k)) else f'{every[k]}·{k!r}·{len(used)}'
            n += 1
        used.add(v)
        out[k] = v
    return out


def shop_check(units):
    """(shown, bad): the shop's shown names and display_check over all of them — run before each forum post."""
    names = {}
    for u in units:
        if isinstance(u, dict) and u.get('sku') and u['sku'] not in names:
            names[u['sku']] = str(u.get('name') or u['sku'])
    shown = display_names(names)
    return shown, display_check(names, shown)
# --- end shared display name block


label = display_name          # one name rule for the whole shop (대표님 11/27 01:26); build_text names the shop at once


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
    """(hours left, grade, clearance) of this SKU's soonest unit on clearance with at least SHORT hours left, else of
    its soonest unit on clearance; None when none is (shared block). Each unit keeps its own clearance price."""
    best = None
    for u in inv.get('units') or []:
        if not (isinstance(u, dict) and u.get('sku') == sku):
            continue
        g = str(u.get('grade') or '?')
        try:
            cl = clearance(u, shown.get(g), now)
        except Exception:
            cl = None
        if cl and (best is None or (cl['hours_left'] < SHORT, cl['hours_left']) < (best[0] < SHORT, best[0])):
            unit = u.get('unit') if isinstance(u.get('unit'), str) else None
            best = (cl['hours_left'], g, dict(cl, until=float(u['expires_at']), unit=unit))
    return best


def fixed(name, sku=None):
    """Water keeps the price it has: by SKU, or by any water word in the name (also inside brackets)."""
    return sku in FIXED_SKU or any(w in str(name or '') for w in FIXED_PRICE)


CASH_HOURS = 48        # cash-out (현금화, 11/27): a food grade whose soonest unit has this many hours or less left


def soonest(inv, sku, grade, now):
    """Hours left of the soonest dated food unit of this SKU and grade we hold, or None."""
    left = [(float(u['expires_at']) - now) / 3600 for u in inv.get('units') or []
            if isinstance(u, dict) and u.get('sku') == sku and str(u.get('grade') or '?') == grade
            and u.get('kind') in (None, 'food') and isinstance(u.get('expires_at'), (int, float))
            and not isinstance(u.get('expires_at'), bool) and float(u['expires_at']) > now]
    return min(left) if left else None


def requote_plan(qb, inv, quotes, settles, now):
    """{sku: {grade: new price}} for the grades we hold whose quote is missing or above max(floor, 24 h median).
    Cash-out (11/27): when that grade's soonest unit has CASH_HOURS or less left and the 24 h median is under the
    floor, the entry is {'price': median, 'floor': median} instead (a cash-out quote, below cost: a unit that expires
    is 0 credits), never under the quote block's CASHOUT_MIN and never for water."""
    plan, work = {}, dict(quotes)                # work: the quotes as they will be once the plan is written
    cmin = qb.get('CASHOUT_MIN')
    for sku in sorted({u.get('sku') for u in inv.get('units') or [] if isinstance(u, dict) and u.get('sku')}):
        st = qb['stock'](inv, sku, now)
        if not st or fixed(st['name'], sku):
            continue
        for g in sorted(st['floors'], key=lambda g: qb['QUOTE_RANK'].index(g) if g in qb['QUOTE_RANK'] else -1):
            med = settles.median(sku, g)
            if med is None:
                continue
            have = qb['quote_get'](quotes, sku, g, now)
            left = soonest(inv, sku, g, now)
            if cmin is not None and med < int(st['floors'][g]) and left is not None and left <= CASH_HOURS:
                target = max(int(cmin), med)
                if have is not None and have <= target and qb['quote_floor'](quotes, sku, g, now) is not None \
                        and qb['quote_floor'](quotes, sku, g, now) <= target:
                    continue                         # already a cash-out quote at or under the median
                fit = qb['quote_fit'](work, sku, g, target, target, now)
                plan.setdefault(sku, {})[g] = {'price': target, 'floor': target}
                work[f'{sku}|{g}'] = {'price': fit, 'ts': now, 'by': qb['CASHOUT_BY'], 'floor': target}
                continue
            target = max(int(st['floors'][g]), med)
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
            v = prices[g]
            if isinstance(v, dict):                # cash-out entry (requote_plan): its own floor, under cost
                cf = int(v['floor'])
                p = qb['quote_fit'](q, sku, g, int(v['price']), cf, now)
                old = qb['quote_floor'](q, sku, g, now)
                if have is not None and p >= have and old is not None and old <= cf:
                    continue
                q[f'{sku}|{g}'] = {'price': p, 'ts': now, 'by': qb['CASHOUT_BY'], 'floor': cf}
                out.setdefault(sku, {})[g] = p
                continue
            p = qb['quote_fit'](q, sku, g, int(v), int(st['floors'].get(g, 0)), now)
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


def build_text(qb, inv, quotes, settles, now, turn, name='노차네 구멍가게(nocha12)', failed=None, posted=None):
    """(text, [sku]) — the cheapest shown grade of each item we hold: what expires within EXPIRING hours first
    (soonest first), then the most units we hold, then the most-sold SKUs on the market; the first two stay, the rest
    rotate with turn so successive posts show different stock. None when nothing has a price. Clearance (떨이, 11/26
19:05): SKUs with a unit on clearance (shared clearance block) come before all of these, soonest first, at the clearance
price of that unit with its hours left, and the post reads '떨이·기한 임박'; water never. A line with under SHORT hours
left (메인 11/27 03:5x) goes after all the others, in the same order among themselves: still in the post, never hidden.
Before all of that, the first LEAD_LONG lines (in that order) with LONG hours or more left, or no date, lead the post
(11/27 06:40): the clearance lines come right after them. Those lead lines are first taken from lines not on clearance
whose grade is in LEAD_GRADES with LEAD_AB hours or more left, or no date (11/27 12:40); only when there are too few does
the LONG rule fill the rest. The header reads '떨이·기한 임박' when the post's first line is on clearance, else '지금 재고'.
posted (a list, optional; 11/27 05fcd1d5): one {'sku', 'grade', 'price', 'unit', 'until'} per line in the text, in text
order. unit and until only for a clearance line (it names one unit's time and price); a grade line's price is that
grade's shown price for every unit of it, so it names no unit (unit, until None). The text and chosen do not change."""
    items = []
    live = {}
    for sku in {u.get('sku') for u in inv.get('units') or [] if isinstance(u, dict) and u.get('sku')}:
        st = qb['stock'](inv, sku, now)
        if st:
            live[sku] = st
    # 대표님 11/27 01:26: the display rules over what is on sale now, before each post. A break is written down
    # (failed) and the item is still shown: the check never hides stock (review I4; no new guard).
    shown_name, bad = shop_check([u for u in inv.get('units') or [] if isinstance(u, dict) and u.get('sku') in live])
    for sku, st in live.items():
        if sku in bad and isinstance(failed, dict):
            failed[sku] = {'name': str(st.get('name') or '')[:80], 'shown': shown_name.get(sku), 'why': bad[sku]}
        shown = {g: p for g, p in qb['quote_prices'](quotes, sku, st['floors'], now).items() if p}
        clr = None if fixed(st['name'], sku) else clear_item(inv, sku, shown, now)
        if clr:                                    # 떨이 11/26 19:05: before everything else, at its clearance price
            left, g, cl = clr
            items.append(((-1, left), -st['count'], -settles.count(sku), sku,
                          f"{shown_name[sku]} {g}{cl['price']:,}(~{time.strftime('%H:%M', time.localtime(cl['until']))})",
                          shown_name[sku], left, g, {'price': cl['price'], 'unit': cl.get('unit'), 'until': cl['until']}))
            continue
        if not shown:
            continue
        g = min(shown, key=lambda k: (shown[k], k))
        left = soonest(inv, sku, g, now)
        urgent = left is not None and left <= EXPIRING
        items.append(((0, left) if urgent else (1, 0), -st['count'], -settles.count(sku), sku,
                      f"{shown_name[sku]} {g}{shown[g]:,}", shown_name[sku], left, g,
                      {'price': shown[g], 'unit': None, 'until': None}))
    if not items:
        return None, []
    items.sort()
    short = lambda it: it[6] is not None and it[6] < SHORT
    clear = [it for it in items if it[0][0] == -1]
    items = items[len(clear):]
    head, rest = items[:2], items[2:]
    if rest:
        k = (turn * 3) % len(rest)
        rest = rest[k:] + rest[:k]
    text, chosen, seen = '', [], set()
    room = VIEW - len(f"[{name} {'떨이·기한 임박' if clear else '지금 재고'}]")   # the longer header when it may be used
    order = clear + head + rest                    # 메인 11/27 03:5x: under SHORT hours left goes last, in the same order
    order = [it for it in order if not short(it)] + [it for it in order if short(it)]
    long_ = lambda it: it[6] is None or it[6] >= LONG
    ab = lambda it: it[0][0] != -1 and it[7] in LEAD_GRADES and (it[6] is None or it[6] >= LEAD_AB)
    lead = []                                      # 11/27 06:40: the first LEAD_LONG long lines (one per name) go first
    for pick in (ab, long_):                       # 11/27 12:40: A·B 24 h+ lines not on clearance first, then the rest
        for it in order:
            if len(lead) < LEAD_LONG and pick(it) and all(x[5] != it[5] for x in lead):
                lead.append(it)
    order = lead + [it for it in order if all(it is not x for x in lead)]
    for it in order:
        if it[5] in seen:                          # one line per name (r3 I2: by the shown name, never a cut of it)
            continue
        add = (' ' if not chosen else ' · ') + it[4]
        if len(text) + len(add) + len(TAIL) > room:
            continue
        text += add
        chosen.append(it[3])
        seen.add(it[5])
        if isinstance(posted, list):
            posted.append(dict(it[8], sku=it[3], grade=it[7]))
    if not chosen:
        return None, []
    first = next(it for it in order if it[3] == chosen[0])   # 11/27 12:40: the header follows the first line (N3: what fits)
    lead = '떨이·기한 임박' if first[0][0] == -1 else '지금 재고'
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
        self.units = self.dir / 'logs' / UNITS_FILE
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

    def save_units(self, ts, post, lines):
        """Add one posted menu's lines to logs/forum-menu-units.json (UNITS_KEEP s and UNITS_MAX posts kept, newest
        last): what check_item_inventory marks as the forum's unit (11/27 05fcd1d5: the post named one C unit, the
        checkout went out on the other). Written through a temp file of our own; a failure never stops the menu."""
        tmp = f'{self.units}.{os.getpid()}.tmp'
        try:
            try:
                with open(self.units, encoding='utf-8') as f:
                    old = json.load(f)
            except (OSError, ValueError, RecursionError):
                old = {}
            posts = old.get('posts') if isinstance(old, dict) and isinstance(old.get('posts'), list) else []
            posts = [p for p in posts if isinstance(p, dict) and isinstance(p.get('ts'), (int, float))
                     and not isinstance(p.get('ts'), bool) and 0 <= ts - p['ts'] < UNITS_KEEP]
            keep = [{k: ln.get(k) for k in ('sku', 'grade', 'price', 'unit', 'until')} for ln in lines if isinstance(ln, dict)]
            posts = (posts + [{'ts': ts, 'post': post, 'lines': keep}])[-UNITS_MAX:]
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({'posts': posts}, f, ensure_ascii=False, separators=(',', ':'))
            os.replace(tmp, self.units)
        except (OSError, TypeError, ValueError, RecursionError):
            try:
                os.remove(tmp)
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
        failed, posted = {}, []
        text, chosen = build_text(self.qb, inv, quotes, self.settles, now, self.turn,
                                  f'노차네 구멍가게({self.name()})', failed, posted)
        if failed:
            self._write({'ts': now, 'ok': None, 'display_fail': failed})   # 표시 규칙 위반: 적어 두고 글에는 그대로 올림
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
            self.save_units(received_at, (body.get('id') or body.get('post')) if isinstance(body, dict) else None, posted)
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
