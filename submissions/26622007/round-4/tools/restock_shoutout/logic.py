"""restock_shoutout: thank the customers who waited for a SKU, once it is really back in stock.

ops (args.op):
  want      {customer, sku, name?, qty?, price?}  a customer asked for a SKU we do not hold (checked on /api/inventory);
                                                  the answer asks for a contact for the restock notice
  contact   {customer, sku, name?, contact}       the customer gave a contact for that item: kept in waitlist.json
  bought    {customer, sku, name?, order}          a sale we settled (the order must be in logs/sales.jsonl with tx_hash)
  opt_out   {customer}                              the customer said stop: no mention, no notice from now on
  restocked {sku, price}                            after a lot is ours: pick who waited (wants) and regulars (2+ buys)
  announce  {dry_run?}                              one forum post for this supply window, every restocked SKU in it
  notice    {customer}                              at the start of that customer's next conversation: one short line
Customers have no A2A card, so the personal notice waits for the customer's next message instead of being pushed.
Stock, grade and price are read again from /api/inventory before anything is said. No wallet, no payment.
The forum names only customers who asked for the SKU while it was sold out; regulars are thanked in their notice
(our sales are sealed, so a public "you keep buying X" would undo that) unless NAME_REGULARS is set.

Waiting list (operator 11/25 07:42): a contact is stored only when the customer gave it, only for the restock notice
of that one item, and only if it is a way to reach them inside the market (a forum name; phone numbers, e-mail and URLs
are refused and not stored). It lives in waitlist.json and nowhere else: no result, log line or other file carries its
value. When the item is back the waiting customers are named first in the window's forum post (by that contact) and
told first at their next message; an entry is deleted once its customer was told, and the same customer and item are
never told twice from the list. A want or a contact may name the item instead of its SKU id ("콩나물"): it then meets
the stock and the restock of every SKU whose name carries those words.

Shown price (operator 11/26 12:40): restocked reads the one shown price per SKU and grade from logs/quotes.json (the
shared quote block, also in check_item_inventory and private_checkout). A grade that has one keeps it; a grade that
has none takes the price given here (raised to its floor, never below a lower grade's). The post and the notices then
name every grade at its shown price, so the forum, a conversation and the checkout say the same number. price and
prices may be left out when every grade in stock already has a shown price.
"""
import json
import math
import os
import re
import time
import urllib.error
import urllib.request

BASE = 'https://market.ourolabs.ai'
REGULAR_MIN = 2              # buys of the same SKU that make a regular
DAY_CAP = 2                  # restock events per customer in 24 h (a mention and its notice are one event)
WANT_TTL = 7 * 86400         # a sold-out request older than this is dropped
NOTICE_TTL = 3 * 86400       # an undelivered notice older than this is dropped
NAMES_PER_SKU = 5            # names in one forum line
NAME_REGULARS = True         # name regulars on the forum too (operator's choice; off keeps sealed sales private)
GAS, FEE = 205, 210          # per-lot gas and per-sale fee, credits (agent.json (3))
POST_GAP_DISLIKED = 6 * 3600 # after a dislike on one of our posts, at most one post per 6 h
DISLIKE_PAUSE = 3            # this many dislikes on our posts in the last hour: no post for 24 h
PENDING_RECHECK = 600        # a post whose outcome is unknown blocks a new one this long, unless the forum shows it
MAX_TEXT = 1900
WAIT_TTL = 7 * 86400         # a contact left for an item is dropped after this long
WAIT_MAX = 500               # entries kept in waitlist.json
SENT_TTL = 30 * 86400        # how long "this customer was told about this item from the list" is remembered
WAIT_PER_CUSTOMER = 5        # items one customer may wait for with a contact
WAIT_PER_CONTACT = 5         # entries one contact may stand in (and only for one customer, unless it is their own name)
# --- shared quote block (same text in check_item_inventory, restock_shoutout, private_checkout; ops/test_quote_block.py)
QUOTES, QUOTE_TTL, QUOTE_RANK = 'logs/quotes.json', 24 * 3600, ('C', 'B', 'A')


def quotes_load(path):
    """{'<sku>|<grade>': {'price': int, 'ts': float, 'by': str}}; {} when missing or unreadable."""
    try:
        with open(path, encoding='utf-8') as f:
            q = json.load(f)
        return q if isinstance(q, dict) else {}
    except (OSError, ValueError, RecursionError):         # a deeply nested file reads as no quotes (r19 M2)
        return {}


def quote_get(q, sku, grade, now):
    r = q.get(f'{sku}|{grade}')
    if isinstance(r, dict) and type(r.get('price')) is int and r['price'] > 0 \
            and isinstance(r.get('ts'), (int, float)) and 0 <= now - r['ts'] < QUOTE_TTL:
        return r['price']
    return None


def quote_prices(q, sku, floors, now):
    """{grade: shown price or None} for the grades in floors: the stored quote raised to that grade's floor, and a
    higher grade never below a lower one (C <= B <= A)."""
    out, low = {}, 0
    for g in sorted(floors, key=lambda g: QUOTE_RANK.index(g) if g in QUOTE_RANK else -1):
        p = quote_get(q, sku, g, now)
        out[g] = None if p is None else max(p, floors[g], low)
        low = max(low, out[g] or 0)
    return out


def quote_fit(q, sku, grade, price, floor, now):
    """A new quote for one grade kept in order with the other grades' quotes: not below a lower grade's, not above
    a higher grade's unless its own floor (or a lower grade's quote) needs it."""
    r = QUOTE_RANK.index(grade) if grade in QUOTE_RANK else -1
    lower = [quote_get(q, sku, g, now) for g in QUOTE_RANK[:r]] if r > 0 else []
    higher = [quote_get(q, sku, g, now) for g in QUOTE_RANK[r + 1:]] if r >= 0 else []
    low = max([p for p in lower if p] + [floor])
    high = min([p for p in higher if p], default=None)
    price = max(price, low)
    if high is not None and price > high:
        price = max(high, low)
    return price


def quote_set(path, sku, prices, floors, by, now, replace=False):
    """Store {grade: price} (first one wins unless replace); returns the shown {grade: price} for those grades: what
    is stored, raised to today's floors and kept in grade order, the same number quote_prices gives (review r1 I5)."""
    q = quotes_load(path)
    q = {k: v for k, v in q.items() if isinstance(v, dict) and isinstance(v.get('ts'), (int, float))
         and 0 <= now - v['ts'] < QUOTE_TTL}
    out = {}
    for g in sorted(prices, key=lambda g: QUOTE_RANK.index(g) if g in QUOTE_RANK else -1):
        have = quote_get(q, sku, g, now)
        if have is not None and not replace:
            out[g] = have
            continue
        p = quote_fit(q, sku, g, int(prices[g]), int(floors.get(g, 0)), now)
        q[f'{sku}|{g}'] = {'price': p, 'ts': now, 'by': by}
        out[g] = p
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(q, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, path)
    shown = quote_prices(q, sku, dict({g: 0 for g in out}, **floors), now)
    return {g: shown.get(g) or out[g] for g in out}
# --- end shared quote block


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


def _quotes_path(kit):
    return 'logs/quotes.test.json' if kit.testing else QUOTES


HEAD = '[노차네 구멍가게 입고]'
ANON = ('did not say who it is', 'a person in your app')
SAFE_NAME = re.compile(r'[가-힣]{2,6}(-[0-9a-f]{4})?')     # village names: Korean name, a dash, 4 hex digits
SKU_ID = re.compile(r'G[0-9a-f]{8}')                      # the market's SKU ids; anything else is an item name
HANDLE = re.compile(r'[A-Za-z][A-Za-z0-9_-]{2,29}')       # a forum name in Latin letters (yms979, SUNGJINHO)
NOT_A_NAME = ('admin', 'everyone', 'owner', 'operator', 'system', 'moderator', 'market', 'refund', 'free', 'approved',
              'official', 'support', 'notice', 'coupon', 'event', 'sale')    # Latin handles that read like an order
NOT_A_HANDLE = ('all', 'here', 'mod', 'bot', 'shop', 'null', 'none', 'true', 'false')
TOKEN = re.compile(r'[가-힣]+|[a-z]+|[0-9]+')
ASK_CONTACT = '지금은 재고가 없어요. 연락처(포럼에서 쓰시는 이름 같은 마켓 안 연락 수단) 남겨 주시면 입고될 때 연락드리겠습니다.'
RISKY = ('할인', '무료', '공짜', '환불', '승인', '지시', '운영자', '사장', '관리자', '반값', '세일', '쿠폰', '이벤트', '공지',
         '특가', '무상', '입고', '재고', '가게')


class StateError(Exception):
    pass


def _file(kit):
    return 'shoutout.test.json' if kit.testing else 'shoutout.json'


def _load(kit):
    try:
        with open(_file(kit), encoding='utf-8') as f:
            s = json.load(f)
    except FileNotFoundError:
        s = {}
    except (OSError, ValueError) as e:
        raise StateError(f'{_file(kit)} is unreadable ({type(e).__name__}); fix it by hand, opt-outs live there')
    if not isinstance(s, dict):
        raise StateError(f'{_file(kit)} is not an object; fix it by hand, opt-outs live there')
    for k, v in (('customers', {}), ('windows', {}), ('posts', []), ('orders', [])):
        if not isinstance(s.get(k), type(v)):
            s[k] = v
    return s


def _save(kit, s):
    tmp = _file(kit) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _file(kit))                        # a crash never leaves half a file (opt-outs live there)


def _wfile(kit):
    return 'waitlist.test.json' if kit.testing else 'waitlist.json'


def _wload(kit):
    """The waiting list: {'entries': [{customer, sku, name, contact, ts, pending}], 'sent': {'customer|sku': ts}}."""
    try:
        with open(_wfile(kit), encoding='utf-8') as f:
            w = json.load(f)
    except FileNotFoundError:
        w = {}
    except (OSError, ValueError) as e:
        raise StateError(f'{_wfile(kit)} is unreadable ({type(e).__name__}); fix it by hand, customer contacts live there')
    if not isinstance(w, dict):
        raise StateError(f'{_wfile(kit)} is not an object; fix it by hand, customer contacts live there')
    w['entries'] = [e for e in w.get('entries') or [] if isinstance(e, dict) and e.get('customer') and e.get('sku')
                    and e.get('contact')] if isinstance(w.get('entries'), list) else []
    if not isinstance(w.get('sent'), dict):
        w['sent'] = {}
    return w


def _wsave(kit, w):
    tmp = _wfile(kit) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        os.chmod(tmp, 0o600)                            # contacts: this account's eyes only
        json.dump(w, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _wfile(kit))


def contact_handle(raw):
    """The in-market contact we keep: a forum name. None for anything else (phone, e-mail, URL, a sentence)."""
    t = str(raw or '').strip().lstrip('@').strip()
    if t.endswith('님'):
        t = t[:-1].strip()
    if speakable(t):
        return t
    words = re.findall(r'[a-z]+', t.lower())            # admin123, free-refund: a whole word of it, not "freeman"
    if (HANDLE.fullmatch(t) and not SKU_ID.fullmatch(t) and t.lower() not in NOT_A_HANDLE
            and not any(w in NOT_A_NAME for w in words)):
        return t
    return None


def _norm(text):
    return re.sub(r'sku|미확인|\s', '', str(text or '').lower())


def same_item(asked, sku, stock_name):
    """A want/contact key meets a SKU: the same id, or an item name (not an id, 2+ letters) inside the stock's name."""
    asked = str(asked or '').strip()
    if asked == sku:
        return True
    if SKU_ID.fullmatch(asked):
        return False
    q = _norm(asked)
    if len(q) >= 2:
        return q in _norm(stock_name)
    # one Korean letter (쌀, 면, 김): the end of a word of the name (백진주쌀, 소면), never its start (무 is not 무농약)
    return bool(re.fullmatch('[가-힣]', q)) and any(t.endswith(q) for t in TOKEN.findall(str(stock_name or '').lower()))


def _was_told(w, key, asked):
    """This customer already had a waiting-list notice for this item, whatever words named it then."""
    for pair in w['sent']:
        who, _, item = pair.rpartition('|')             # the item never holds a '|'; a customer's name might
        if who != key:
            continue
        if item == asked:
            return True
        if not SKU_ID.fullmatch(item) and not SKU_ID.fullmatch(asked):
            a, b = _norm(item), _norm(asked)
            if len(a) >= 2 and len(b) >= 2 and (a in b or b in a):      # 쌀 told is not 쌀국수 told
                return True
    return False


def _fresh(u, now):
    """Not past its date (0 or no date: it has none)."""
    e = u.get('expires_at')
    return not (isinstance(e, (int, float)) and not isinstance(e, bool) and 0 < e <= now)


def _hide(text, handles, mask='(대기 손님)'):
    for h in sorted(handles, key=len, reverse=True):
        text = re.sub(re.escape(h), mask, text, flags=re.IGNORECASE)
    return text


def _forget_in_lost_deals(kit, key, handle):
    """The message kept with a sold-out record may carry the contact the customer then left: take it out."""
    path = 'logs/lost_deals.test.jsonl' if kit.testing else 'logs/lost_deals.jsonl'
    try:
        with open(path, 'r+', encoding='utf-8') as f:
            out, changed = [], False
            for line in f.read().split('\n'):
                if handle.lower() in line.lower():
                    try:
                        row = json.loads(line)
                    except ValueError:
                        row = None
                    if isinstance(row, dict) and row.get('customer') == key and row.get('last_message'):
                        row['last_message'] = _hide(str(row['last_message']), [handle], '[연락처]')
                        line, changed = json.dumps(row, ensure_ascii=False), True
                out.append(line)
            if changed:                                 # in place (the sandbox allows no temporary file beside it)
                f.seek(0)
                f.write('\n'.join(out))
                f.truncate()
    except (OSError, ValueError):                       # unreadable (bad bytes included): the record stays as it is
        pass


def _told(w, now, key, *skus):
    """This customer was told about the item from the list: drop the entries, remember the pair (no contact kept)."""
    for e in [e for e in w['entries'] if e.get('customer') == key and e.get('sku') in skus]:
        w['entries'].remove(e)
    for sku in skus:
        w['sent'][f'{key}|{sku}'] = now


def _num(v, default=0):
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return v
    m = re.search(r'\d+(?:\.\d+)?', str(v or '').replace(',', ''))
    return float(m.group()) if m else default


def customer_key(frm):
    """The same key customer_memory uses: the quoted name if the from line says it, None for anonymous customers."""
    t = str(frm or '')
    if any(a in t for a in ANON):
        return None
    m = re.search(r"says it is '([^']+)'", t)
    if m:
        t = m.group(1)
    t = t.strip().strip('"\'').strip('[]').strip()
    return t[:40] or None


def speakable(key):
    """A name we may print: short, plain letters, and nothing that reads like an order or a price."""
    k = str(key or '').strip()
    return k if SAFE_NAME.fullmatch(k) and not any(w in k for w in RISKY) else None


def _http(kit, method, path, body=None, query=''):
    req = urllib.request.Request(BASE + path + query, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'authorization': 'Bearer ' + kit.token('shop'),
                                          'content-type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read().decode() or 'null')
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or 'null')
        except ValueError:
            return e.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None                                  # unknown outcome (a POST may still have landed)


def inventory(kit, args):
    if kit.testing:
        return args.get('_inventory') or {'units': []}
    st, body = _http(kit, 'GET', '/api/inventory')
    if st != 200 or not isinstance(body, dict):
        raise RuntimeError(f'inventory unreadable ({st})')
    return body


def forum(kit, args):
    if kit.testing:
        return args.get('_forum') or []
    st, body = _http(kit, 'GET', '/api/forum', query='?limit=100')
    if st != 200:
        raise RuntimeError(f'forum unreadable ({st})')
    posts = body if isinstance(body, list) else (body or {}).get('posts') if isinstance(body, dict) else []
    return [p for p in posts or [] if isinstance(p, dict)]


def post(kit, text):
    return _http(kit, 'POST', '/api/forum', {'text': text})


def stock(inv, sku, now=None):
    now = time.time() if now is None else now
    units = [u for u in inv.get('units') or [] if isinstance(u, dict) and u.get('sku') == sku and _fresh(u, now)]
    if not units:
        return None
    floors = {}                                         # grade -> its dearest unit's cost + gas + fee (write_shop_card's floor)
    for u in units:
        g = str(u.get('grade') or '?')
        floors[g] = max(floors.get(g, 0), math.ceil((_num(u.get('cost')) + GAS + FEE) / 0.99))
    return {'count': len(units), 'name': units[0].get('name') or sku, 'grades': sorted(floors), 'floors': floors,
            'floor': max(floors.values())}             # what one '개당' price for every grade has to clear


def find_stock(inv, asked, now):
    """(stock, [sku ids], [names], exact) for a SKU id or an item name; (None, [], [], False) when we hold none."""
    st = stock(inv, asked, now)
    live = [u for u in inv.get('units') or [] if isinstance(u, dict) and u.get('sku') and _fresh(u, now)]
    shown = shop_names(live)                            # the whole shop at once: two items never read the same (review I6)
    if st:
        return st, [asked], [shown.get(asked) or _short(st['name'])], True
    held = {}
    for u in live:
        if same_item(asked, u.get('sku'), u.get('name')):
            held.setdefault(u['sku'], shown.get(u['sku']) or _short(u.get('name') or u['sku']))
    if not held:
        return None, [], [], False
    skus = sorted(held)
    return {'count': sum(stock(inv, k, now)['count'] for k in skus)}, skus, [held[k] for k in skus][:8], False


def _ts(v):
    t = _num(v, 0)
    return t / 1000 if t > 1e12 else t                 # the market writes epoch seconds; ms just in case


def _mine(kit, p):
    return isinstance(p, dict) and kit.name in (p.get('author'), p.get('owner'))


def _short(name):
    return display_name(name)          # the shared display name rule (대표님 11/27 01:26), never cut by characters


def _window(inv, s, now):
    r = inv.get('restock') if isinstance(inv.get('restock'), dict) else {}
    opens = _num(r.get('opens_at'), 0)
    last = s.get('last_window')
    if r.get('open') and opens:
        w = str(int(opens))
    elif last and now - _num(s.get('last_window_ts')) < 5400 and not (s['windows'].get(last) or {}).get('posted'):
        w = last
    else:
        w = 'h' + str(int(now // 3600))
    s['last_window'], s['last_window_ts'] = w, now
    return w


def _prune(s, now):
    for c in s['customers'].values():
        c['wants'] = {k: v for k, v in (c.get('wants') or {}).items() if now - _num(v.get('ts')) < WANT_TTL}
        c['notices'] = [n for n in c.get('notices') or [] if now - _num(n.get('ts')) < NOTICE_TTL]
        c['events'] = [e for e in c.get('events') or [] if now - _num(e.get('ts')) < 86400]
        c.setdefault('buys', {})
    s['posts'] = [p for p in s['posts'] if now - _num(p.get('ts')) < 7 * 86400]
    for w in sorted(s['windows'], key=lambda w: _num(s['windows'][w].get('ts')))[:-20]:
        s['windows'].pop(w, None)
    s['orders'] = s['orders'][-2000:]


def _cust(s, key):
    c = s['customers'].setdefault(key, {})
    for k, v in (('wants', {}), ('buys', {}), ('events', []), ('notices', []), ('opt_out', None)):
        c.setdefault(k, v)
    return c


def _sales_orders():
    out = {}
    try:
        with open('logs/sales.jsonl', encoding='utf-8') as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get('order') and r.get('tx_hash'):
                    out[str(r['order'])] = r
    except OSError:
        pass
    return out


def grade_prices(args):
    """Optional {grade: price} from the agent, e.g. {'C': 7200, 'B': 10100}; ints only, grades A-C."""
    g = args.get('prices') if isinstance(args, dict) else None
    out = {}
    if isinstance(g, dict):
        for k, v in g.items():
            k = str(k).strip().upper()[:1]
            if k in ('A', 'B', 'C') and _num(v, 0) > 0:
                out[k] = int(_num(v))
    return out


def price_text(st, price, prices=None):
    """'C 7,200·B 10,100크레딧' when per-grade prices cover the grades in stock, else '개당 N크레딧'."""
    prices = prices or {}
    if prices and set(st['grades']) <= set(prices):     # every grade in stock has its own price
        return '·'.join(f"{g} {max(prices[g], st['floors'][g]):,}" for g in st['grades']) + '크레딧'
    fallback = max([int(_num(price, 0))] + list(prices.values()))   # partial grade prices: never show a grade below its own
    return f"개당 {max(fallback, st['floor']):,}크레딧"


def notice_text(key, sku_name, st, price, why='waited', prices=None, shown=None):
    who = (speakable(key) + '님') if speakable(key) else '손님'
    lead = '기다리시던' if why == 'waited' else '자주 찾아 주시던'
    return (f"{who}, {lead} {shown or _short(sku_name)} 다시 들어왔어요. 지금 {st['count']}개 있고 "
            f"{'·'.join(st['grades'])}등급, {price_text(st, price, prices)}이에요. "
            f"이런 입고 안내를 그만 받으시려면 '그만'이라고 말씀해 주세요.")


def run(args, kit):
    op = args.get('op')
    now = float(args.get('_now') or time.time()) if kit.testing else time.time()
    try:
        s = _load(kit)
    except StateError as e:
        return {'ok': False, 'error': str(e)}
    _prune(s, now)
    try:
        w = _wload(kit)
    except StateError as e:                             # the other ops go on without the list; nothing overwrites it
        if op == 'contact':
            return {'ok': False, 'error': str(e)}
        w = None
    before = json.dumps(w, sort_keys=True) if w is not None else None
    if w is not None:
        w['entries'] = [e for e in w['entries'] if now - _num(e.get('ts')) < WAIT_TTL][-WAIT_MAX:]
        w['sent'] = {k: v for k, v in w['sent'].items() if now - _num(v) < SENT_TTL}
    out = _op(op, args, kit, s, now, w)
    _save(kit, s)
    if w is not None and json.dumps(w, sort_keys=True) != before:
        _wsave(kit, w)
    return out


def _op(op, args, kit, s, now, w=None):
    if op in ('want', 'bought', 'opt_out', 'notice', 'contact'):
        key = customer_key(args.get('customer'))
        if not key:
            return {'ok': False, 'anonymous': True, 'note': '이름을 밝히지 않은 손님은 기록하지 않습니다'}
        c = _cust(s, key)
    sku = str(args.get('sku') or '').strip()

    if op == 'opt_out':
        c['opt_out'] = now
        c['notices'] = []
        if w is not None:                               # the contact was for notices only: it goes with them
            w['entries'] = [e for e in w['entries'] if e.get('customer') != key]
        return {'ok': True, 'customer': key, 'opt_out': True}

    if op == 'want':
        if not sku:
            return {'ok': False, 'error': 'sku is required'}
        st, skus, names, exact = find_stock(inventory(kit, args), sku, now)
        if st and (exact or not args.get('force')):
            return {'ok': False, 'in_stock': st['count'], 'skus': skus, 'names': names,
                    'note': '재고가 있으니 품절 기록 대신 지금 재고로 안내하세요(check_item_inventory {sku}로 unit·등급 확인). '
                            '이름만 비슷한 다른 품목이면 force: true로 다시 부르면 품절로 기록합니다'}
        c['wants'][sku] = {'ts': now, 'name': str(args.get('name') or sku)[:60],
                           'qty': int(_num(args.get('qty'), 1)) or 1, 'price': _num(args.get('price'), None)}
        lost = {'ts': int(now), 'customer': key, 'item': str(args.get('name') or sku)[:60], 'sku': sku,
                'offered_price': None, 'grade_stock': '재고 0', 'wanted': f"{int(_num(args.get('qty'), 1)) or 1}개",
                'last_message': _hide(str(args.get('last_message') or '')[:200],
                                      [e['contact'] for e in (w['entries'] if w is not None else [])
                                       if e.get('customer') == key and e.get('contact') != key], '[연락처]'),
                'reason': '재고없음', 'by': 'restock_shoutout'}
        try:
            with open('logs/lost_deals.test.jsonl' if kit.testing else 'logs/lost_deals.jsonl', 'a', encoding='utf-8') as f:
                f.write(json.dumps(lost, ensure_ascii=False) + '\n')
        except OSError:
            pass
        out = {'ok': True, 'customer': key, 'want': sku, 'lost_deal_recorded': True}
        if w is None or c.get('opt_out') or _was_told(w, key, sku):
            return dict(out, ask_contact=False, say='지금은 재고가 없어요. 입고되면 다음에 들르실 때 가장 먼저 알려드릴게요.')
        if any(e.get('customer') == key and e.get('sku') == sku for e in w['entries']):
            return dict(out, ask_contact=False, say='지금은 재고가 없어요. 입고되면 남겨 주신 연락처로 가장 먼저 연락드릴게요.')
        return dict(out, ask_contact=True, say=ASK_CONTACT,
                    next='손님이 연락처를 주면 op contact {customer, sku, name, contact}로 저장합니다(손님이 준 것만)')

    if op == 'contact':
        if not sku:
            return {'ok': False, 'saved': False, 'error': 'sku is required'}
        handle = contact_handle(args.get('contact'))
        if not handle or handle.lower() == str(getattr(kit, 'name', '') or '').lower():   # never echo what was given: results are logged
            return {'ok': False, 'saved': False, 'error': 'not an in-market contact',
                    'say': '전화번호·이메일 같은 마켓 밖 연락처는 받지 않아요. 포럼에서 쓰시는 이름을 알려 주시면 입고될 때 포럼에서 먼저 불러 드릴게요.'}
        if c.get('opt_out'):
            return {'ok': False, 'saved': False, 'error': '입고 안내를 그만 받겠다고 한 손님입니다'}
        st, skus, names, exact = find_stock(inventory(kit, args), sku, now)
        if st and (exact or not args.get('force')):
            return {'ok': False, 'saved': False, 'in_stock': st['count'], 'skus': skus, 'names': names,
                    'note': '재고가 있으니 대기 명단 대신 지금 재고로 안내하세요(이름만 비슷한 다른 품목이면 force: true)'}
        if _was_told(w, key, sku):
            return {'ok': False, 'saved': False, 'already_notified': True,
                    'note': '이 손님에게 이 품목 입고는 대기 명단으로 이미 한 번 알렸습니다(손님당 한 품목 1회)'}
        others = [e for e in w['entries'] if not (e.get('customer') == key and e.get('sku') == sku)]
        same = [e for e in others if str(e.get('contact')).lower() == handle.lower()]
        if (sum(e.get('customer') == key for e in others) >= WAIT_PER_CUSTOMER
                or (handle != key and (len(same) >= WAIT_PER_CONTACT or any(e.get('customer') != key for e in same)))):
            return {'ok': False, 'saved': False, 'error': 'waiting list cap',       # nobody is named over and over
                    'say': '지금은 연락처를 더 적어 둘 수 없어요. 입고되면 다음에 들르실 때 가장 먼저 알려드릴게요.'}
        if handle != key:
            _forget_in_lost_deals(kit, key, handle)
        name = str(args.get('name') or sku)[:60]
        w['entries'] = [e for e in w['entries'] if not (e.get('customer') == key and e.get('sku') == sku)] + [
            {'customer': key, 'sku': sku, 'name': name, 'contact': handle, 'ts': now, 'pending': None}]
        c['wants'].setdefault(sku, {'ts': now, 'name': name, 'qty': int(_num(args.get('qty'), 1)) or 1, 'price': None})
        return {'ok': True, 'saved': True, 'customer': key, 'sku': sku,
                'say': f'네, 적어 두었어요. {_short(name)} 들어오면 가장 먼저 연락드릴게요. 남겨 주신 연락처는 입고 알림에만 써요.'}

    if op == 'bought':
        order = str(args.get('order') or '').strip()
        if not sku or not order:
            return {'ok': False, 'error': 'sku and order are required'}
        sale = (args.get('_sales') or {}).get(order) if kit.testing else _sales_orders().get(order)
        if not sale:
            return {'ok': False, 'error': '이 주문은 logs/sales.jsonl의 정산 기록(tx_hash)에 없어서 세지 않습니다'}
        b = c['buys'].setdefault(sku, {'count': 0, 'orders': [], 'name': str(args.get('name') or sku)[:60]})
        if order in s['orders']:
            return {'ok': True, 'customer': key, 'sku': sku, 'count': b['count'], 'duplicate': True}
        s['orders'].append(order)                       # one order is one purchase, whoever it is credited to
        b['orders'] = (b['orders'] + [order])[-50:]
        b['count'] += 1
        c['wants'].pop(sku, None)
        return {'ok': True, 'customer': key, 'sku': sku, 'count': b['count'], 'regular': b['count'] >= REGULAR_MIN}

    if op == 'restocked':
        if not sku:
            return {'ok': False, 'error': 'sku is required'}
        inv = inventory(kit, args)
        st = stock(inv, sku, now)
        if not st:
            return {'ok': False, 'error': '재고에 이 SKU가 없어 입고 안내를 하지 않습니다'}
        prices = grade_prices(args)
        given = dict(prices)
        shown = {g: v for g, v in quote_prices(quotes_load(_quotes_path(kit)), sku, st['floors'], now).items() if v}
        pa = int(_num(args.get('price')))
        if shown:                                       # a grade with a shown price keeps it; the rest take what was given
            merged = {g: shown.get(g) or prices.get(g) or pa for g in st['grades']}
            if all(merged.values()):                    # every grade priced: each is checked against its own floor (r2 I-1)
                prices = merged
        price = pa or (max(prices.values()) if prices else 0)
        if prices and set(st['grades']) <= set(prices):  # every grade is shown at its own price: each clears its own floor
            low = any(v < st['floors'].get(g, st['floor']) for g, v in prices.items())   # a grade not in stock: as before
        else:                                           # one '개당' price is shown for every grade: the highest floor
            low = price < st['floor'] or any(v < st['floor'] for v in prices.values())
        if low:
            return {'ok': False, 'error': '가격이 하한보다 낮습니다', 'floor': st['floor'], 'floors': st['floors']}
        try:                                            # one shown price per grade: stored ones stay, new ones are kept
            stored = quote_set(_quotes_path(kit), sku, {g: prices.get(g) or price for g in st['grades']}, st['floors'],
                               'restock_shoutout', now)
        except Exception:
            stored = {}
        if set(st['grades']) <= set(stored):
            prices = {g: stored[g] for g in st['grades']}
            price = max(prices.values())
        quote_note = None
        asked_for = {g: given.get(g) or int(_num(args.get('price'))) for g in st['grades']}
        if any(v and prices.get(g) != v for g, v in asked_for.items()):         # only grades a price was given for
            quote_note = '안내가가 이미 있는 등급은 그 값으로 알립니다: ' + '·'.join(f'{g} {v:,}' for g, v in prices.items())
        wkey = _window(inv, s, now)
        event = f'{wkey}:{sku}'
        win = s['windows'].setdefault(wkey, {'ts': now, 'skus': {}, 'posted': None})
        picked, skipped = [], {}
        listed = {}                                     # customer -> their waiting-list entries for this item
        for e in (w['entries'] if w is not None else []):
            if same_item(e.get('sku'), sku, st['name']):
                listed.setdefault(e['customer'], []).append(e)
        for key in sorted(set(s['customers']) | set(listed), key=lambda k: k not in listed):   # the list goes first
            c = _cust(s, key)
            asked = [k for k in c['wants'] if same_item(k, sku, st['name'])]
            regular = _num((c['buys'].get(sku) or {}).get('count')) >= REGULAR_MIN
            if not (asked or regular or key in listed):
                continue
            if c.get('opt_out'):
                skipped[key] = 'opt_out'
            elif any(e.get('event') == event for e in c['events']):
                skipped[key] = 'already_this_restock'
            elif len(c['events']) >= DAY_CAP and key not in listed:    # the list has its own cap: once per item
                skipped[key] = 'day_cap'
            else:
                why = 'waitlist' if key in listed else 'waited' if asked else 'regular'
                c['events'].append({'ts': now, 'event': event, 'sku': sku})
                c['notices'] = [n for n in c['notices'] if n.get('sku') != sku] + [
                    {'ts': now, 'sku': sku, 'event': event, 'price': price, 'prices': prices, 'name': st['name'],
                     'why': 'waited' if why == 'waitlist' else why}]
                for k in asked:
                    c['wants'].pop(k)
                for e in listed.get(key, []):
                    e['pending'] = event                # told by the window's post or the next notice, then deleted
                picked.append({'customer': key, 'why': why})
        prev = (win['skus'].get(sku) or {}).get('names') or {}
        win['skus'][sku] = {'name': st['name'], 'price': price, 'prices': prices,
                            'names': dict(prev, **{p['customer']: p['why'] for p in picked if p['customer'] not in prev})}
        return {'ok': True, 'sku': sku, 'window': wkey, 'in_stock': st['count'], 'grades': st['grades'],
                'picked': picked, 'skipped': skipped, 'prices': prices, **({'quote_note': quote_note} if quote_note else {}),
                'next': 'announce once this supply window is done; notice at each picked customer\'s next message'}

    if op == 'announce':
        return _announce(args, kit, s, now, w)

    if op == 'notice':
        if c.get('opt_out') or not c['notices']:
            return {'ok': True, 'customer': key, 'notice': None}
        inv = inventory(kit, args)
        live = [u for u in (inv.get('units') or [] if isinstance(inv, dict) else [])
                if isinstance(u, dict) and u.get('sku') and _fresh(u, now)]
        shown = shop_names(live)                        # as the shop names it (review I6)
        texts = []
        for n in c['notices']:
            st = stock(inv, n.get('sku'), now)
            if st:                                      # sold out since: say nothing rather than promise it
                now_q = {g: v for g, v in quote_prices(quotes_load(_quotes_path(kit)), n.get('sku'), st['floors'], now).items() if v}
                np = now_q if set(st['grades']) <= set(now_q) else n.get('prices')   # today's shown price, if every grade has one
                texts.append(notice_text(key, n.get('name') or n.get('sku'), st,
                                         max(int(_num(n.get('price'))), st['floor']), n.get('why', 'waited'), np,
                                         shown.get(n.get('sku'))))
                if w is not None:
                    mine = [e['sku'] for e in w['entries'] if e.get('customer') == key
                            and (e.get('pending') == n.get('event') or same_item(e.get('sku'), n.get('sku'), st['name']))]
                    if mine:
                        _told(w, now, key, n.get('sku'), *mine)
        c['notices'] = []
        return {'ok': True, 'customer': key, 'notice': ' '.join(texts) or None}

    return {'ok': False, 'error': 'op must be want, contact, bought, opt_out, restocked, announce or notice'}


def _listed_done(w, now, wkey, mentioned):
    """After this window's post is known to be up: the waiting-list customers it named are told, their entries go."""
    if w is None:
        return
    for key, sku in mentioned:
        for e in [e for e in w['entries'] if e.get('customer') == key and e.get('pending') == f'{wkey}:{sku}']:
            _told(w, now, key, sku, e['sku'])


def _announce(args, kit, s, now, wl=None):
    dry = bool(args.get('dry_run')) or kit.testing
    wkey = s.get('last_window')
    win = s['windows'].get(wkey) if wkey else None
    if not win or not win.get('skus'):
        return {'ok': False, 'error': '이 공급 창에 입고 안내할 품목이 없습니다'}
    if now < _num(s.get('paused_until')):
        return {'ok': False, 'error': '우리 글 비추천이 많아 공지를 쉬는 중입니다', 'paused_until': int(s['paused_until'])}
    rows = forum(kit, args)
    mine = [dict(p, ts=_ts(p.get('ts'))) for p in rows if _mine(kit, p)]
    posted = win.get('posted')
    if posted == 'pending':                             # the last POST had no clear answer: did it land?
        hit = next((p for p in mine if str(p.get('text', '')).startswith(HEAD) and p['ts'] >= _num(win.get('pending_ts')) - 60), None)
        if hit:
            win['posted'] = hit.get('id') or 'found'
            s['posts'].append({'ts': hit['ts'], 'id': hit.get('id'), 'window': wkey})
            _listed_done(wl, now, wkey, {tuple(m) for m in win.get('listed') or [] if isinstance(m, list) and len(m) == 2})
            return {'ok': False, 'error': '이 공급 창의 입고 공지는 이미 올라가 있습니다', 'post': hit.get('id')}
        if now - _num(win.get('pending_ts')) < PENDING_RECHECK:
            return {'ok': False, 'error': '지난 공지 결과를 아직 확인하지 못해 10분 뒤에 다시 봅니다'}
        if _num(win.get('attempts')) >= 2:              # twice without an answer: a missing post beats a duplicate
            win['posted'] = 'unknown'
            return {'ok': False, 'error': '공지 결과를 두 번 확인하지 못해 이 공급 창은 공지를 그만둡니다'}
    elif posted:
        return {'ok': False, 'error': '이 공급 창의 입고 공지는 이미 올렸습니다', 'post': posted}
    hour = sum(int(_num(p.get('dislikes'))) for p in mine if 0 <= now - p['ts'] < 3600)
    disliked = any(_num(p.get('dislikes')) > 0 for p in mine if 0 <= now - p['ts'] < 86400)
    last = min(now, max([_num(p.get('ts')) for p in s['posts']] + [p['ts'] for p in mine] + [0]))
    if hour >= DISLIKE_PAUSE:
        s['paused_until'] = now + 86400
        return {'ok': False, 'error': '최근 1시간 우리 글 비추천이 많아 24시간 공지를 쉽니다', 'dislikes_last_hour': hour}
    if disliked and now - last < POST_GAP_DISLIKED:
        return {'ok': False, 'error': '우리 글에 비추천이 있어 공지 간격을 6시간으로 늘렸습니다',
                'next_after': int(last + POST_GAP_DISLIKED)}
    inv = inventory(kit, args)
    shown_names = shop_names(inv.get('units') or [], now) if isinstance(inv, dict) else {}   # on sale (r3 I5)
    quotes = quotes_load(_quotes_path(kit))
    handles = set()                                     # contacts in the post: masked in what this call returns
    lines, gone, named, carried = [], [], {}, {}        # named: line -> its waiting-list (customer, sku, contact); carried: contact -> the line that names it
    for sku, v in win['skus'].items():
        st = stock(inv, sku, now)
        if not st:
            gone.append(sku)
            continue
        # today's shown price, if every grade has one: a requote since restocked shows here too (r19 M1)
        now_q = {g: p for g, p in quote_prices(quotes, sku, st['floors'], now).items() if p}
        shown = now_q if set(st['grades']) <= set(now_q) else v.get('prices')
        names = {k: why for k, why in (v.get('names') or {}).items() if not (s['customers'].get(k) or {}).get('opt_out')}
        ok = {k: why for k, why in names.items() if speakable(k)}
        # the waiting list first, by the contact each customer left; one already told in person is not named again
        listed, seen = [], set()
        for e in (wl['entries'] if wl is not None else []):
            if (e.get('pending') == f'{wkey}:{sku}' and names.get(e['customer']) == 'waitlist'
                    and contact_handle(e.get('contact')) and e['customer'] not in seen and len(listed) < NAMES_PER_SKU):
                seen.add(e['customer'])
                listed.append((e['customer'], e['contact']))
        waited = [k for k, why in ok.items() if why == 'waited'][:max(0, NAMES_PER_SKU - len(listed))]
        regular = [k for k, why in ok.items() if why == 'regular' and NAME_REGULARS][:max(0, NAMES_PER_SKU - len(listed) - len(waited))]
        item = (f"{shown_names.get(sku) or _short(st['name'])} {st['count']}개(지금 {'·'.join(st['grades'])}등급, "
                f"{price_text(st, _num(v.get('price')), shown)})")
        thanks = []
        call = [h for h in dict.fromkeys(contact_handle(h) for _, h in listed) if h not in handles]   # each contact once a post
        handles.update(call)
        first = call                                    # the contacts this line is the one to name
        if call:
            thanks.append(' '.join('@' + h + '님' for h in call) + ' 남겨 주신 연락처로 먼저 알려드려요.')
        if waited:
            thanks.append(' '.join('@' + speakable(k) + '님' for k in waited) + ' 기다려 주셔서 감사해요.')
        if regular:
            thanks.append(' '.join('@' + speakable(k) + '님' for k in regular) + ' 늘 찾아 주셔서 감사해요.')
        lines.append(' '.join(thanks + [f'{item} 다시 들어왔어요.']))
        named[lines[-1]] = [(k, sku, contact_handle(h)) for k, h in listed]
        for h in first:
            carried[h] = lines[-1]
    if not lines:
        return {'ok': False, 'error': '공지하려던 품목이 이미 다 팔렸습니다', 'sold_out': gone}
    tail = ' 재고가 있는 동안만 드릴 수 있어요.'
    text, dropped = HEAD, []
    for ln in lines:                                    # whole lines only: never cut a price or a count in half
        if len(text) + 1 + len(ln) + len(tail) <= MAX_TEXT:
            text += ' ' + ln
        else:
            dropped.append(ln)
    posted = {h for h, ln in carried.items() if ln not in dropped}                    # a dropped line told nobody
    mentioned = [(k, sku) for ln in lines for k, sku, h in named.get(ln, []) if h in posted and ln not in dropped]
    text += tail
    shown = _hide(text, handles)                        # results are logged: the post carries the contact, the log does not
    if dry:
        return {'ok': True, 'dry_run': True, 'text': shown, 'sold_out': gone, 'dropped_lines': len(dropped)}
    win['posted'], win['pending_ts'], win['attempts'] = 'pending', now, int(_num(win.get('attempts'))) + 1
    win['listed'] = [list(m) for m in mentioned]        # who this post names from the list (names, no contact)
    _save(kit, s)                                       # a retry after a lost answer finds 'pending', not a fresh window
    code, body = post(kit, text)
    if 200 <= code < 300:
        pid = body.get('post') if isinstance(body, dict) else body
        if isinstance(pid, dict):                       # a market that answers with the whole post: keep its id only
            pid = pid.get('id')
        pid = pid if isinstance(pid, (str, int)) and not isinstance(pid, bool) else None
        pid = _hide(pid, handles)[:80] if isinstance(pid, str) else pid
        win['posted'] = pid or f'posted@{int(now)}'        # any answer that says yes closes the window
        s['posts'].append({'ts': now, 'id': pid, 'window': wkey})
        _listed_done(wl, now, wkey, set(mentioned))
        return {'ok': True, 'post': pid, 'text': shown, 'sold_out': gone}
    if code and code != 0:                              # the market answered no: nothing was posted
        win['posted'] = None
        return {'ok': False, 'status': code, 'response': _hide(json.dumps(body, ensure_ascii=False, default=str), handles)[:300]}
    return {'ok': False, 'error': '포럼 응답을 받지 못했습니다. 10분 뒤 announce가 올라갔는지 확인합니다'}
