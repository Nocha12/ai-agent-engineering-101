"""check_item_inventory: what the shop really holds, short enough for the agent to read whole.

args {}            every SKU we hold: name, count, and per grade the count, the floor price and the hours left
args {sku}         the units of one item: a SKU id, or words of its name ("콩나물"); a dated unit carries until
                   (its expiry 'MM/DD HH:MM KST', the minute its signed certificate shows)
The kit shows a tool result up to 6,000 characters. shop_snapshot returns the raw /api/inventory list (about 500
characters a unit with its certificate), so past 10 units the rest was cut off and the agent answered "sold out" for
stock we held (11/25 06:39: bean sprouts bought at 06:24). This reads the same signed-in /api/inventory and answers
in under 5,000 characters. Units past their date are counted apart (expired), not as stock. A failed lookup is
ok:false, never zero stock.

Shown price (operator 11/26 12:40): each grade carries price, the one shown price for that SKU and grade kept in
logs/quotes.json by the shared quote block (also in private_checkout and restock_shoutout): never below the grade's
floor, never below a lower grade's. args {sku, quote: {grade: price}} sets the shown price for a grade that has none;
requote: true replaces it. Answers and checkouts quote that price, so one unit is not offered at two prices.

Clearance (떨이, 대표님 11/26 19:05; shared clearance block): units whose time left is half or less of min(shelf life,
48 h) carry clearance {price, floor}; args {} lists them first (clearance, soonest first, CLEAR_LIST at most). The
clearance price is what private_checkout issues without price for that unit; it is never stored as the shown price."""
import json
import math
import os
import re
import time

import httpx

GAS, FEE = 205, 210            # per-lot gas and per-sale fee: the floor write_shop_card and private_checkout use
LIMIT = 4800                   # characters of the answer
ITEMS = 20                     # units listed for one item
CLEAR_LIST = 12               # clearance lines in the whole-shop answer


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


def _quotes_path(kit):
    return 'logs/quotes.test.json' if kit.testing else QUOTES


def inventory(kit, args):
    if kit.testing:
        inv = args.get('_inventory')
        if inv is None:
            return {'units': []}
        if not isinstance(inv, dict):
            raise RuntimeError('inventory is not an object')
        return inv
    auth = {'authorization': 'Bearer ' + kit.token('shop')}
    with httpx.Client(timeout=12, follow_redirects=True) as c:
        r = c.get(kit.market.rstrip('/') + '/api/inventory', headers=auth)
    if r.status_code != 200:
        raise RuntimeError('inventory HTTP %d' % r.status_code)
    inv = r.json()
    if not isinstance(inv, dict):
        raise RuntimeError('inventory is not an object')
    return inv


def num(v):
    """A market number, or 0 for anything else (a string, None, a bool): the answer must not stop on one odd unit."""
    try:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > 1e15:
            return 0
        return int(v) if float(v).is_integer() else float(v)
    except (OverflowError, ValueError):
        return 0


def floor(cost):
    return math.ceil((num(cost) + GAS + FEE) / 0.99)


def hours_left(u, now):
    e = u.get('expires_at')                             # 0 or missing: the unit has no date
    return round((e - now) / 3600, 1) if isinstance(e, (int, float)) and not isinstance(e, bool) and e > 0 else None


def until(u):
    """The unit's expires_at as 'MM/DD HH:MM KST', the same minute the checkout's signed certificate line shows
    (ops/checkout_delivery.grade_note: seconds dropped); None without a date. Customers compare the reply with that
    signed line word for word (11/27 0b2461b6: '18시까지' against 18:27, no sale)."""
    c = u.get('cert') if isinstance(u.get('cert'), dict) else {}
    c = c.get('cert') if isinstance(c.get('cert'), dict) else {}      # the signed certificate itself (review 1 M2):
    e = c.get('expires_at') if c.get('unit') == u.get('unit') else None   # private_checkout's grade_evidence reads it
    if not (isinstance(e, (int, float)) and not isinstance(e, bool) and 1e9 < e < 1e11):
        e = u.get('expires_at')                         # no certificate of this unit: the inventory's own date
    if not (isinstance(e, (int, float)) and not isinstance(e, bool) and 1e9 < e < 1e11):
        return None
    return time.strftime('%m/%d %H:%M KST', time.gmtime(int(e) + 9 * 3600))


def window(inv):
    r = inv.get('restock') if isinstance(inv.get('restock'), dict) else {}
    return {k: r[k] for k in ('open', 'opens_at', 'closes_at')
            if isinstance(r.get(k), bool) or (isinstance(r.get(k), (int, float)) and num(r[k]) == r[k])}


def live(inv, now):
    """(units still good, how many are past their date)."""
    units = [u for u in inv.get('units') or [] if isinstance(u, dict) and u.get('sku')]
    good = [u for u in units if hours_left(u, now) is None or hours_left(u, now) > 0]
    return good, len(units) - len(good)


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


def norm(text):
    """Lower case without spaces; 2L×6병, 2Lx6병, 2L*6병 and 2L 6병 read the same (q8 11/26: '몽베스트 무라벨 생수 2L×6병'
    found nothing while 13 packs of '…생수 2Lx6병(1팩)' were in stock)."""
    t = re.sub(r'[×✕✖╳⨯＊*ｘ]', 'x', str(text or '').lower())
    t = re.sub(r'\s', '', t)
    return re.sub(r'(\d(?:kg|ml|g|l|t)?|[가-힣])x(?=\d)', r'\1', t)     # 2Lx6, 400gx3, 라면x20; not MAX 2 (review I4)


SIZE_WORD = re.compile(r'^\d+(?:\.\d+)?(?:kg|g|l|ml|구|개입|입|팩|개|병|봉|매|t)?$', re.I)


def words(text):
    """Name words of two letters or more that are not a size or a count, brackets dropped: what 'similar' compares."""
    t = re.sub(r'[\[\]()]', ' ', str(text or ''))
    return {w for w in (norm(x) for x in re.split(r'[\s/·,]+', t)) if len(w) >= 2 and not SIZE_WORD.match(w)}


def similar(asked, units, n=5, shown=None):
    """No item matched the name: the SKUs whose names share most of its words (at least half, and one), so the agent
    can look again by SKU instead of answering 'sold out' for stock we hold. Never counted as a match."""
    want = words(asked)
    if not want:
        return []
    need = max(1, (len(want) + 1) // 2)
    best, shown = {}, shown or shop_names(units)
    for u in units:
        have = norm(u.get('name'))
        hit = sum(1 for w in want if w in have)
        if hit >= need:
            b = best.setdefault(u['sku'], {'sku': str(u['sku'])[:20], 'name': shown.get(u['sku'], ''), 'count': 0, 'hit': hit})
            b['count'] += 1
    return [{k: v for k, v in b.items() if k != 'hit'} for b in sorted(best.values(), key=lambda b: (-b['hit'], -b['count'], b['sku']))[:n]]


def named(asked, name):
    """Words of an item name: two letters or more anywhere in the name; one Korean letter (쌀, 면, 김) at the end of a word
    of it (백진주쌀, 소면), never at its start (무 is not 무농약). restock_shoutout uses the same words rule but its own
    _norm, which (as of q8, 11/26) does not read 2L×6 as 2Lx6: a known gap there, not here."""
    q = norm(asked)
    if len(q) >= 2:
        return q in norm(name)
    return bool(re.fullmatch('[가-힣]', q)) and any(t.endswith(q) for t in re.findall(r'[가-힣]+', str(name or '')))


CLEAR_NOTE = ('기한 임박 할인: clearance는 "SKU 등급 할인가격/최저가 남은시간"이고 손님께는 할인 가격으로 그 price를 먼저 말합니다(곧 버리게 되는 재고). private_checkout을 price 없이 내면 '
              '그 unit의 할인 가격으로 나가고, 흥정은 floor까지만 내립니다(floor·최저가·clearance라는 말은 손님께 쓰지 않음). unit ID는 args {sku}로 봅니다')


def clear_safe(u, base, now):
    try:                                                # an odd unit never stops the stock answer
        return clearance(u, base, now)
    except Exception:
        return None


def clear_lines(units, now, qpath):
    """'<sku> <grade> <price>/<floor> <hours>h' for each SKU and grade on clearance, soonest first; the base
    price is that grade's shown price, as in the per-SKU answer."""
    q = quotes_load(qpath)
    gf = {}
    for u in units:
        k = (u['sku'], str(u.get('grade') or '?')[:8])
        gf[k] = max(gf.get(k, 0), floor(u.get('cost')))
    best = {}
    for u in units:
        k = (u['sku'], str(u.get('grade') or '?')[:8])
        base = quote_prices(q, k[0], {g: f for (s2, g), f in gf.items() if s2 == k[0]}, now).get(k[1])
        cl = clear_safe(u, base, now)
        if cl and (k not in best or cl['hours_left'] < best[k][0]['hours_left']):
            best[k] = (cl, u)
    rows = sorted(best.items(), key=lambda kv: (kv[1][0]['hours_left'], kv[0]))[:CLEAR_LIST]
    return [f"{k[0]} {k[1]} {cl['price']}/{cl['floor']} {cl['hours_left']}h" for k, (cl, u) in rows]   # short: SKUs must still fit


def summary(units, with_names, shown):
    skus = {}
    for u in units:
        s = skus.setdefault(u['sku'], {'sku': u['sku'], 'name': shown[u['sku']], 'count': 0, 'grades': {}})
        s['count'] += 1
        g = s['grades'].setdefault(str(u.get('grade') or '?')[:8], {'n': 0, 'cost': 0, 'left': []})
        g['n'] += 1
        g['cost'] = max(g['cost'], num(u.get('cost')))
        g['left'].append(u)
    return list(skus.values())


def run(args, kit):
    try:
        return answer(args if isinstance(args, dict) else {}, kit)
    except Exception as e:                              # an answer the market never gave before: say so, never "0"
        return {'ok': False, 'error': '재고 조회 실패(재고 0이 아님, 잠시 뒤 다시 조회): ' + (type(e).__name__ + ' ' + str(e))[:80]}


def answer(args, kit):
    now = float(args.get('_now') or time.time()) if kit.testing else time.time()
    asked = str(args.get('sku') or args.get('name') or '').strip()[:60]
    inv = inventory(kit, args)
    units, expired = live(inv, now)
    if asked:
        names_shown = shop_names(units)    # what is on sale, as the forum and the card name it (M4, r3 I5)
        mine = [u for u in units if u['sku'] == asked] or [u for u in units if named(asked, u.get('name'))]
        def clear_first(u):
            try:
                lr = clear_ratio(u, now)
            except Exception:
                lr = None
            return 0 if lr and lr[1] <= CLEAR_HALF else 1
        mine.sort(key=lambda u: (clear_first(u), str(u.get('grade') or '?'),     # clearance units are cut last (review M6)
                                 hours_left(u, now) if hours_left(u, now) is not None else 1e9))
        items = [{'unit': str(u.get('unit'))[:40], 'sku': str(u['sku'])[:20], 'name': names_shown[u['sku']],
                  'grade': str(u.get('grade') or '?')[:8], 'cost': num(u.get('cost')), 'floor': floor(u.get('cost')),
                  'hours_left': hours_left(u, now), 'list_price': num(u.get('list_price')) or None} for u in mine[:ITEMS]]
        for it, u in zip(items, mine):
            t = until(u)
            if t:
                it['until'] = t
        gf = {}
        for u in mine:
            k = (u['sku'], str(u.get('grade') or '?')[:8])
            gf[k] = max(gf.get(k, 0), floor(u.get('cost')))
        set_note, quoted = None, None
        if isinstance(args.get('quote'), dict) and mine:
            skus = {u['sku'] for u in mine}
            sku = mine[0]['sku']
            want = {g: p for g, p in args['quote'].items() if (sku, g) in gf and type(p) is int and p > 0}
            if len(skus) > 1:                           # a name that matched several items: say which SKU to use
                set_note = 'quote는 SKU ID 하나로 정합니다(이 이름에 맞는 품목이 여럿): ' + ', '.join(sorted(skus))[:200]
            elif not want:
                set_note = 'quote는 이 SKU에 있는 등급과 양의 정수 가격만 받습니다'
            else:
                try:
                    got = quote_set(_quotes_path(kit), sku, want, {g: f for (s, g), f in gf.items() if s == sku},
                                    'check_item_inventory', now, replace=bool(args.get('requote')))
                    quoted = sku
                    kept = {g: v for g, v in got.items() if v != want.get(g)}
                    if kept:                            # say what is shown instead (r2 M-5); requote: true changes it
                        set_note = None
                        quoted = {'sku': sku, 'kept': kept}
                except Exception as e:                  # the stock answer still goes out
                    set_note = '가격 저장 실패: ' + type(e).__name__
        q = quotes_load(_quotes_path(kit))
        shown = {}
        for (s, g), f in gf.items():
            shown[(s, g)] = quote_prices(q, s, {g2: f2 for (s2, g2), f2 in gf.items() if s2 == s}, now).get(g)
        on_clear = False
        for it, u in zip(items, mine):
            it['price'] = shown.get((it['sku'], it['grade']))
            cl = clear_safe(u, it['price'], now)
            if cl:
                it['clearance'] = {'price': cl['price'], 'floor': cl['floor']}
                on_clear = True
        out = {'ok': True, 'sku': asked, 'count': len(mine), 'items': items, 'more': max(0, len(mine) - ITEMS),
               'note_price': 'price: 손님께 부를 이 품목·등급 가격. 비어 있으면 args {sku, quote:{등급:가격}}로 먼저 정함(손님께 미정이라고 말하지 않음)'}
        if on_clear:
            out['note_clearance'] = CLEAR_NOTE
        if set_note:
            out['quote_error'] = set_note
        if isinstance(quoted, dict):
            out['quoted_sku'], out['quote_kept'] = quoted['sku'], quoted['kept']
            out['quote_kept_note'] = '이미 정한 가격(또는 최저 기준·등급 순서로 올린 값)을 씁니다. 바꾸려면 requote: true'
        elif quoted:
            out['quoted_sku'] = quoted
        if not mine:
            near = similar(asked, units, shown=names_shown)
            if near:
                out['similar'] = near
                out['note'] = ('이 이름과 정확히 맞는 재고는 없지만 이름이 비슷한 품목이 있습니다(similar). 같은 상품·포장인지 '
                               'args {sku: similar의 SKU}로 다시 조회한 뒤 답하고, 확인 전에는 품절이라고 하지 마세요')
            else:
                out['note'] = '이 이름·SKU와 맞는 재고가 없습니다. 다른 이름으로 들여놨을 수 있으니 args {}로 전체 재고를 확인하세요'
        while len(json.dumps(out, ensure_ascii=False)) > LIMIT and out['items']:
            out['items'].pop()
            out['more'] = len(mine) - len(out['items'])
        return out
    out, clear = {}, clear_lines(units, now, _quotes_path(kit))
    names_shown = shop_names(units)                     # what is on sale (r3 I5)
    for with_names in (True, False):                    # names are never cut (대표님 11/27 01:26): all of them, or none
        skus = summary(units, with_names, names_shown)
        q = quotes_load(_quotes_path(kit))
        for s in skus:
            shown = quote_prices(q, s['sku'], {g: floor(v['cost']) for g, v in s['grades'].items()}, now)
            for g, v in s['grades'].items():
                left = [h for h in (hours_left(u, now) for u in v['left']) if h is not None]
                s['grades'][g] = {'n': v['n'], 'floor': floor(v['cost']), 'hours_left': min(left) if left else None}
                if shown.get(g):                        # only when set: 60 nulls would push SKUs out of the answer
                    s['grades'][g]['price'] = shown[g]
            if not with_names:
                s.pop('name')
        out = {'ok': True, 'balance': num(inv.get('balance')), 'restock': window(inv), 'units': len(units),
               'expired': expired}
        if clear:
            out['clearance'], out['note_clearance'] = clear, CLEAR_NOTE
        out.update({'skus': skus,
               'note': 'grades: 등급별 n(개수)·floor(최저가, 손님께 말하지 않음)·price(손님께 부를 가격, 비어 있으면 {sku, quote}로 먼저 정함)·hours_left(가장 빨리 끝나는 소비기한까지 시간). unit ID는 args {sku}로 봅니다'})
        if len(json.dumps(out, ensure_ascii=False)) <= LIMIT:
            break
    while len(json.dumps(out, ensure_ascii=False)) > LIMIT and out['skus']:      # still too long: cut, and say so
        out['skus'] = out['skus'][:-1]                # one at a time: the clearance lines must not cost whole quarters
        out['more_skus'] = len(skus) - len(out['skus'])
    return out
