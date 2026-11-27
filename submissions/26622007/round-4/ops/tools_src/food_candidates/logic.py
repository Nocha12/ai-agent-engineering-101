"""food_candidates v2 (operator): the food catalog the shop restocks from, plus customer_asked_out_of_stock -
foods customers asked for in the last LOOKBACK_H hours that we had none of (logs/lost_deals.jsonl, written by
log_lost_deal and restock_shoutout), each matched to catalog food SKUs that buy_food_lot can buy.
An ask whose first buy_options SKU (not 'near') we hold now in the size asked is not "stock 0": it goes to
asked_but_held. A held 2nd / 3rd option may be another food, so that ask stays listed with held_maybe; a held SKU of
another size / not a small pack when one was asked leaves it listed (that size is stock 0, notes 11/26) with
held_not_fit (11/27: 흰우유 small-pack ask, we held the 3rd option 190ml x 18, bought 3 times, sold 0).
It only reads; it buys nothing and blocks nothing."""
import json, math, re, time
import httpx

LOOKBACK_H = 24                      # lost deals this recent count
MAX_GROUPS = 15                      # asked items returned, most asked first
FIT = 5900                           # characters of the answer (the kit shows 6,000; the wrapper re-adds cut messages)
OPTIONS = 3                          # catalog SKUs offered per asked item
MATCH_SECS = 4                       # matching time budget inside the tool's 20 s (3 requests x 4 s + this)
HTTP_SECS = 4
LOST = 'logs/lost_deals.jsonl'
SOLD = 'ops/best_sellers_data.json'   # market-wide sales (ledger), refreshed by the operator's cron
OUT_OF_STOCK = ('재고없음', '재고 없음', '품절')
STOP = {'국내산', '국산', '수입산', '손질', '두절', '특대', '대용량', '소용량', '소포장', '포장', '내외', '또는', '오늘',
        '등급', '상품', '일반', '신선', '구이용', '찌개용', '팩', '개입', '세트'}
_clock = time.time
_mono = time.monotonic
# sizes in names / asks: '20kg', '1.4kg', '2L', '30구', '36개입', '100T', '12캔', '4미', '1손' ('4~6미' ranges and '6개국' are not sizes)
_WEIGHT = {'kg': (1000, 'g'), 'g': (1, 'g'), 'ml': (1, 'ml'), 'l': (1000, 'ml'), '리터': (1000, 'ml')}
_COUNT = '개입|구|개|입|t|캔|병|팩|미|봉|손|마리|롤'
_KIND = ('개입', '구', 't', '캔', '병', '미', '손', '롤', '입')     # pack kinds; '개', '팩', '봉', '마리' count how many
_MULT = ('판', '박스')                                    # only multiply a pack ('30구 2판')
_SIZE = re.compile(r'(?<![\d.~\-])(\d{1,5}(?:\.\d{1,3})?)\s?(?:(kg|ml|g|l|리터)(?![a-wyz])|(' + _COUNT + r'|판|박스)(?![a-wyz가-힣]))', re.I)
_X = re.compile(r'\s*[x×*]\s*(\d{1,4})(?![\d.])\s?(kg|ml|g|l|리터)?', re.I)
# the customer takes only this item: '생깻잎 외 품목은 원치 않음', '다른 채소는 대신 사지 않겠습니다', '대체품 사절', '말고는 안'.
# Not '애호박만 원해요' / '한 판만 찾아요': that is an amount or the item itself, not a refusal (review r1 C1)
REFUSE = re.compile(r'외\s*(?:다른\s*)?(?:품목|상품|제품|것)\S{0,2}\s*(?:원치|원하지|사지|안\s?사)'
                    r'|다른[^.!?\n]{0,24}?(?:사지\s*않|안\s?산|안\s?(?:사|삽|살)|원치\s*않|원하지\s*않)'
                    r'|대체[^.!?\n]{0,16}?(?:사절|싫|않|말아|말고|원치|원하지|안\s?(?:됨|됩|돼|되|해|함|삼|사|산|받))'
                    r'|말고는\s*(?:안|않|싫|사지|원치)')
FILLER = {'좀', '꼭', '것', '도', '더', '등', '요'}         # one-letter words in an ask that are not the item (r16 M-1)
UNITS = {'kg', 'g', 'ml', 'l', 'mg', 'cc'}
DISH = ('장아찌', '볶음', '김치', '무침', '조림', '찌개', '칼국수', '어묵', '과자', '스틱', '소시지', '샌드', '튀김', '부침개',
        '전골', '국밥', '덮밥', '국수', '밀키트', '반찬', '양념', '절임', '소스', '만두', '떡볶이', '케이크', '주스', '젤리', '아이스크림',
        '빵', '푸딩', '튀밥', '스프', '쿠키', '라떼', '떡', '케익')   # another food after the item: '우유 식빵', '쌀 튀밥' (q4 r1 I1)
# size / degree words (2+ letters, as tokens): they say which one of the item, never the item itself. They do not rank a
# name before one with the item ('보통 두부': 떡볶이(보통맛) after 연두부, 11/27 07:43), only among names with it ('중간
# 계란': 중간 크기 계란 first, review I1). Order only, nothing is left out for it
MOD = {'보통', '작은', '중간', '소형', '중형', '대형'}
SMALL_G = 1000                       # a small pack: the whole pack at most this many g / ml ...
SMALL_N = 4                          # ... or at most this many pieces; an unknown size is not small (it stays listed)
SMALL = re.compile(r'소량|소포장|작은|조금|혼자|(?<!\d)1\s?인|한\s?봉지|소용량|적당한\s?양')


def fetch(base, token):
    """(catalog, supply-prices, inventory) from the market."""
    h = {'authorization': 'Bearer ' + token}
    with httpx.Client(timeout=HTTP_SECS, follow_redirects=True) as c:
        return (c.get(base + '/api/catalog').json(), c.get(base + '/api/supply-prices').json(),
                c.get(base + '/api/inventory', headers=h).json())


def read_lost(path=LOST):
    rows = []
    try:
        lines = open(path, encoding='utf-8', errors='replace').read().splitlines()[-5000:]   # recent rows only
    except FileNotFoundError:
        return rows
    for line in lines:
        try:
            r = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(r, dict):
            rows.append(r)
    return rows


def market_sold(path=SOLD):
    """{sku: market-wide sales in the file's window}; {} when the file is missing or unreadable (ranking only)."""
    try:
        rows = json.load(open(path, encoding='utf-8')).get('sales') or []
    except Exception:
        return {}
    if not isinstance(rows, list):
        return {}
    out = {}
    for r in rows:
        if isinstance(r, list) and len(r) > 1 and isinstance(r[1], str):
            out[r[1]] = out.get(r[1], 0) + 1
    return out


def out_of_stock(r):
    reason, stock = str(r.get('reason', '')), str(r.get('grade_stock', ''))
    return any(k in reason for k in OUT_OF_STOCK) or stock.replace(' ', '') in ('재고0', '0', '0개')


QTY = re.compile(r'(?<![가-힣])(?:(?:한|두|세|네|다섯|열)\s*)?(?:개|단|모|판|봉지|봉|박스|포대|통|묶음|팩|마리|손|키로|킬로)(?![가-힣])'
                 r'|(?<![가-힣])(?:한|두|세|네|다섯|열)(?![가-힣])')


def _clean(text):
    """An ask or name without sizes and quantity words ('5kg', '10키로', '한 포대', '한 박스'): what is left names the
    thing (review r8 I1)."""
    t = re.sub(r'(\d)\s*(?:키로|킬로)', r'\1kg', str(text)[:200])
    return QTY.sub(' ', _SIZE.sub(' ', t))


def tokens(text):
    return [t[:12] for t in re.findall(r'[가-힣A-Za-z]{2,}', str(text)[:40]) if t not in STOP][:8]   # bounded work


def common(t, name):
    """Length of the longest piece of token t (2+ letters) that appears in name."""
    for n in range(len(t), 1, -1):
        for i in range(len(t) - n + 1):
            if t[i:i + n] in name:
                return n
    return 0


def score(toks, name):
    name = name.replace(' ', '')
    return sum(c for c in (common(t, name) for t in toks) if c >= 2)


def sizes(text):
    """[(start, end, number, unit)] of the sizes in text, bounded."""
    out = []
    for m in _SIZE.finditer(str(text)[:160]):
        out.append((m.start(), m.end(), float(m.group(1)), (m.group(2) or m.group(3)).lower()))
        if len(out) >= 8:
            break
    return out


def _norm(num, unit):
    """(value, dimension): g and ml for weights and volumes, the unit itself for counts."""
    if unit in _WEIGHT:
        f, dim = _WEIGHT[unit]
        return round(num * f, 3), dim
    return round(num, 3), unit


def pack_of(name):
    """('30구 2판', 60, size keys) from a catalog name; (None, None, set()) when it has no size.
    A count ('36개입') wins over the weight of one piece ('8g x 36개입'); '×N', 'N판', 'N박스', '+same' are applied."""
    name = str(name)[:160]
    toks = sizes(name)
    real = [t for t in toks if t[3] not in _MULT]
    if not real:
        return None, None, set()
    first = real[0]
    base = first
    if first[3] in _WEIGHT:                              # '120g 5개입', '2Lx6병': the count is the pack
        nxt = [t for t in toks if t[0] >= first[1]][:1]
        if nxt and nxt[0][3] not in _WEIGHT and nxt[0][3] not in _MULT and re.fullmatch(r'\s*[x×*]?\s*\(?', name[first[1]:nxt[0][0]], re.I):
            base = nxt[0]
    keys = {_norm(first[2], first[3]), _norm(base[2], base[3])}
    value, dim = _norm(base[2], base[3])
    start, end, mult, plus = base[0], base[1], 1, False
    for _ in range(2):                                   # at most two more pieces: '110개입 x 2개', '30구 2판'
        m = _X.match(name, end)
        if m and not m.group(2):
            mult, end = mult * int(m.group(1)), m.end()
            u = re.match(r'\s?(?:' + _COUNT + r'|판|박스|통)(?![a-wyz가-힣])', name[end:], re.I)
            if u:
                end += u.end()
            continue
        nxt = [t for t in toks if t[0] >= end][:1]
        if not nxt:
            break
        t, gap = nxt[0], name[end:nxt[0][0]]
        if re.fullmatch(r'\s*\+\s*', gap) and t[3] == base[3]:
            value, end, plus = value + _norm(t[2], t[3])[0], t[1], True
        elif re.fullmatch(r'\s+', gap) and base[3] not in _WEIGHT and t[3] != base[3] and t[3] in _MULT + ('팩', '봉'):
            mult, end = mult * int(t[2]), t[1]
        else:
            break
    size = value * mult
    size = int(size) if float(size).is_integer() else round(size, 3)
    text = name[start:end] if (mult != 1 or plus) else name[base[0]:base[1]]
    return re.sub(r'\s+', ' ', text).strip()[:24], size, keys


def amount(name):
    """(kind, total) for the small-pack order (review r5 I2): weight or volume of the whole pack (one piece's g/ml times
    the counts after it, up to the next weight: '두부 300g×5팩' 1500, '햇반 210g x12개' 2520, '쌀 10kg (5kgX2봉)' 10000);
    a count-only pack by its count after those; no size last."""
    toks = sizes(name)
    w = next((t for t in toks if t[3] in _WEIGHT), None)
    if w:
        total, after = _norm(w[2], w[3])[0], w[1]
        m = _X.match(name, w[1])
        if m and not m.group(2):                        # '210g x 12', '300g×5팩': the bare multiplier (r6 M2)
            total, after = total * max(1, int(m.group(1))), m.end()
        for t in toks:
            if t[0] < after:
                continue
            if t[3] in _WEIGHT:
                break
            total *= max(1.0, t[2])
        return 0, total
    _, size, _ = pack_of(name)
    return (1, size) if size else (2, 0)


def _pieces(words):
    """Every 2+ letter piece of the customer's words (bounded: 16 words of up to 12 letters)."""
    out = set()
    for t in words:
        for i in range(len(t)):
            for j in range(i + 2, len(t) + 1):
                out.add(t[i:j])
    return out


def _words(name):
    """Name words without [..]/(..), sizes and STOP words; Korean and Latin apart ('맛있는우유GT': 맛있는우유, GT)."""
    n = _SIZE.sub(' ', re.sub(r'\[[^\]]*\]|\([^)]*\)', ' ', str(name)[:160]))
    return [w[:20] for w in re.findall(r'[가-힣]{2,}|[A-Za-z]{2,}', n) if w not in STOP][:12]


def _item_words(item):
    """The words of a customer's ask with one-letter Korean words kept ('유기농 무': 유기농, 무), sizes, counts, STOP
    words and lone Latin letters ('x2') left out."""
    return [w for w in re.findall(r'[가-힣]+|[A-Za-z]{2,}', _clean(item))
            if w not in STOP and w not in FILLER and w.lower() not in UNITS][:12]


def _dish(w):
    """A name word making it another food ('김치', '식빵'); a use word is not ('제빵용', '떡국용', '찌개용', q4 r2 I1)."""
    return not w.endswith('용') and any(d in w for d in DISH)


def name_fit(name, pieces):
    """(extra letters glued to matched words, unmatched words, letters glued after the matched part) for a catalog name.
    Korean puts the thing itself last: 흰우유·깐마늘 are milk and garlic, 깻잎김치·애호박전 are other foods.
    Words in [..] / (..), STOP words and sizes do not count: '[마녀바스켓]', '국내산', '1kg'."""
    words = _words(name)
    extra = other = tail = 0
    hit_at = [k for k, w in enumerate(words) if any(w[i:j] in pieces for i in range(len(w)) for j in range(i + 2, len(w) + 1))]
    if hit_at:                                          # a dish word after the item makes it another food (r3 I1, r4):
        tail += sum(1 for w in words[hit_at[-1] + 1:] if _dish(w))   # '생깻잎 장아찌', '애호박 볶음'
    for w in words:
        mark = [False] * len(w)
        for i in range(len(w)):
            for j in range(len(w), i + 1, -1):
                if w[i:j] in pieces:
                    mark[i:j] = [True] * (j - i)
                    break
        c = sum(mark)
        if c == 0:
            other += 1
        else:
            extra += len(w) - c
            tail += len(w) - 1 - max(i for i, m in enumerate(mark) if m)   # after the customer's word: '김치' in 생깻잎김치
    return extra, other, tail


def held_by_sku(inv, now):
    out = {}
    for u in (inv.get('units') or []) if isinstance(inv, dict) else []:
        if not isinstance(u, dict):
            continue
        exp = u.get('expires_at')
        if isinstance(exp, (int, float)) and not isinstance(exp, bool) and 0 < exp <= now:
            continue                                    # past its date: not stock
        s = u.get('sku') or u.get('id') or u.get('item')
        if isinstance(s, str) and s:
            out[s] = out.get(s, 0) + 1
    return out


def asked_out_of_stock(lost, foods, held, now, sold=None, want_held=False):
    """Group recent out-of-stock asks by the item text (asks refusing substitutes apart) and attach the catalog food
    SKUs that match it best: matching letters minus letters glued on that the customer did not write ('생깻잎김치' for
    '생깻잎'); then a size the customer wrote; then fewer unmatched name words; [small amount asked: smaller known
    pack, unknown size last]; then the ones the market sells most; then the shortest name. An ask refusing substitutes
    only gets names where nothing is glued after the customer's words (origin, brand, words before it are fine:
    '충남 금산 생깻잎 200g', '흰우유' yes, '생깻잎김치' no); one refusal counts for that customer's every ask of the item, else buy_options [] (substitutes_refused)."""
    sold = sold or {}
    foods = [x for x in foods if isinstance(x.get('sku'), str)]
    by_sku = {x['sku']: x for x in foods}
    groups, rows = {}, []
    for r in lost:
        try:
            ts = r.get('ts')
            if isinstance(ts, bool) or not isinstance(ts, (int, float)): continue
            ts = float(ts)
            if not math.isfinite(ts) or not 0 <= now - ts <= LOOKBACK_H * 3600 or not out_of_stock(r): continue
        except (OverflowError, ValueError, TypeError):
            continue                                    # one bad row is skipped, not the whole list
        item = re.sub(r'\s+', ' ', str(r.get('item') or r.get('sku') or '')).strip()[:60]
        sku = str(r.get('sku') or '')
        wanted = str(r.get('wanted') or '')[:200]
        if re.fullmatch(r'\s*\d{1,4}\s*개\s*', wanted):
            wanted = ''                                 # restock_shoutout's unit count ('3개'), not a pack size (r2 M-3)
        # the item by its words and the sizes the customer wrote: '고등어 1손' and '손질 고등어 한 마리' meet (r11),
        # '백미 20kg' and '백미 2kg' stay two items, each buying its own size (r12 I-1)
        words = ' '.join(_item_words(item)) or item     # '유기농 쌀' and '유기농 무' are two items (r14 I-1, r15 I-1)
        # the first weight / volume / pack kind written; how many ('2마리', '2봉', '3팩') is not a different item (r13 M-1)
        rsz = [_norm(t[2], t[3]) for t in sizes(re.sub(r'(\d)\s*(?:키로|킬로)', r'\1kg', item + ' ' + wanted))
               if t[3] in _WEIGHT or t[3] in _KIND][:1]
        key = sku if sku in by_sku else (words + ''.join(f' |{v:g}{u}' for v, u in rsz) if words else '')
        if not key:
            continue
        said = ' '.join((item, wanted, str(r.get('last_message') or '')[:400]))
        rows.append([r, ts, item, sku, key, wanted, said, bool(REFUSE.search(said)), words, bool(rsz)])
    sized = {}                                          # words -> the sized keys asked for them
    for x in rows:
        if x[9] and x[3] not in by_sku:
            sized.setdefault(x[8], set()).add(x[4])
    for x in rows:                                      # no size written: joins the one size asked for, if only one
        if not x[9] and x[3] not in by_sku and len(sized.get(x[8], ())) == 1:
            x[4] = next(iter(sized[x[8]]))
    # what a refusal covers: the item by its words, every size of it (r13 I-1); a catalog SKU by itself
    rows = [tuple(x[:8]) + ({x[4], x[8]} if x[3] in by_sku else {x[8]},) for x in rows]     # a SKU row: by both (r14 M-1)
    refusers = {(str(x[0].get('customer')), k) for x in rows if x[7] and x[0].get('customer') for k in x[8]}
    for r, ts, item, sku, key, wanted, said, refused, rkeys in rows:
        # one refusal stands for that customer's every ask of the item, earlier rows too (review r2 I-3)
        refused = refused or any((str(r.get('customer')), k) in refusers for k in rkeys)
        g = groups.setdefault((key, refused), {'asks': 0, 'customers': set(), 'last': 0, 'items': [], 'sku': sku if sku in by_sku else None,
                                               'refused': refused, 'small': False, 'words': [], 'sizes': set(), 'heads': set(),
                                               'key': key})
        g['small'] = g['small'] or bool(SMALL.search(said))
        g['heads'] |= set(_item_words(item)[-1:])       # the thing itself: the last word of the item ('유기농 무': 무)
        if len(g['words']) < 16:                        # the customer's own words / sizes (item and wanted, not the chat)
            g['words'] = list(dict.fromkeys(g['words'] + tokens(_clean(item)) + tokens(_clean(wanted))))[:16]
        if len(g['sizes']) < 8:
            g['sizes'] |= {_norm(t[2], t[3]) for t in sizes(re.sub(r'(\d)\s*(?:키로|킬로)', r'\1kg', item + ' ' + wanted))
                           if t[3] not in _MULT}
        g['asks'] += 1
        if r.get('customer'):
            g['customers'].add(str(r['customer']))
        g['last'] = max(g['last'], ts)
        if item and item not in g['items'] and len(g['items']) < 3:
            g['items'].append(item)
    out, seen = [], {}
    both = {}                                           # an item counts all who asked it, refusing or not (r9 I1)
    for (k, _), g in groups.items():
        both.setdefault(k, set()).update(g['customers'])
    order = [g for _, g in sorted(groups.items(), key=lambda kv: (-len(both[kv[0][0]]), -len(kv[1]['customers']),
                                                                 -kv[1]['asks'], -kv[1]['last']))]
    stop = _mono() + MATCH_SECS
    for g in order[:MAX_GROUPS * 2]:                    # bounded work: only the most asked are matched
        if _mono() > stop:
            break                                       # the tool must answer well inside its 20 s limit
        syl, one = [], False
        if g['sku']:
            ranked = [(99, by_sku[g['sku']])]
        else:
            toks = list(dict.fromkeys(t for it in g['items'] for t in tokens(_clean(it))
                                      if t.lower() not in UNITS))[:8]          # '쌀 10kg 한 포대': only 쌀 (r7 I1, r8 I1)
            syl = list(dict.fromkeys(w for it in g['items'] for w in re.findall(r'[가-힣]+', _clean(it)) if len(w) == 1))[:2]
            if not toks and not syl:
                continue
            # the ask written without spaces too: '유기농 쌀' is in '유기농쌀 4kg' (r17 I-2)
            joined = [j for j in (''.join(_item_words(it))[:24] for it in g['items']) if len(j) > 1]
            pieces = _pieces((g['words'] or toks) + joined)
            core = [t for t in toks if t not in MOD] or toks       # '보통 두부': rank by 두부, not by 보통
            # letters glued after a size word ('대형급', '보통맛') are no tail of the item itself (review 2 I1)
            cpieces = None if core == toks else _pieces([w for w in (g['words'] or toks) if w not in MOD] + [
                j for j in (''.join(w for w in _item_words(it) if w not in MOD)[:24] for it in g['items']) if len(j) > 1])
            scored = []
            for i, x in enumerate(foods if toks else ()):
                if i % 200 == 0 and _mono() > stop:
                    break
                name = str(x.get('name', ''))
                s = score(toks, name)
                if s < 2:
                    continue
                extra, other, tail = name_fit(name, pieces)
                nws = re.findall(r'[가-힣]+|[A-Za-z]{2,}', _clean(re.sub(r'\[[^\]]*\]|\([^)]*\)', ' ', name[:160])))
                if g['refused'] and (tail or (g['heads'] and not any((h == w or any(w.endswith(j) for j in joined)
                                                                      if len(h) == 1 else h in w)
                                                                     for h in g['heads']
                                                                     for w in nws + [a + b for a, b in zip(nws, nws[1:])
                                                                                     if a + b in g['heads']]))):
                    continue                            # only this item: no other food glued after it ('생깻잎김치'), and
                                                        # the customer's word whole in a name word (양념깻잎 is not 생깻잎);
                                                        # a one-letter word is the word itself (단무지 is not 무, r16 I-1);
                                                        # '깐 마늘' is '깐마늘' only as the whole pair, never
                                                        # '배 추석' for 배추 (r17 I-1, r18 I-1)
                pack, size, keys = pack_of(name)
                hit = 1 if keys & g['sizes'] else 0
                small = amount(name) if g['small'] else ()
                # a one-letter word asked next to longer ones ('유기농 무'): names with a word ending in it first (r15 I-1)
                miss = 1 if syl and not any(w.endswith(c) for c in syl for w in re.findall(r'[가-힣]+', _clean(name[:160]))) else 0
                # letters after the customer's word make another food (생깻잎김치); letters before it do not (자반고등어,
                # 흰우유) (review r10 I2)
                # small amount asked: the smaller pack before brand / extra words ('오뚜기 마열라면 8개' before
                # '맛있는라면 x 20개', r19 follow-up)
                # a brand or two before the item is fine ('오뚜기 마열라면'), a name full of other words is not the item
                # asked ('보리차 디카페인 보리 커피' for 커피, bought 15:02): those stay behind whatever their size (q5)
                ctail = tail if cpieces is None else name_fit(name, cpieces)[2]
                scored.append(((-(score(core, name) - ctail), -(s - tail), miss, -hit) + ((other > 2,) if g['small'] else ()) + small
                               + (other, -sold.get(x['sku'], 0), extra, len(name)), x, pack, size))
            one = bool(syl) and not scored              # no word of 2+ letters matched: the one-letter staple (쌀, 무, 파)
            for i, x in enumerate(foods if one else ()):  # the name word is it or ends with it (햅쌀, 백미쌀),
                if i % 200 == 0 and _mono() > stop:        # never starts with it (쌀국수) (r6 I1)
                    break
                name = str(x.get('name', ''))
                ws = re.findall(r'[가-힣]+', _clean(re.sub(r'\[[^\]]*\]|\([^)]*\)', ' ', name[:160])))
                at = [k for k, w in enumerate(ws) if any(w.endswith(c) for c in syl)]
                if not at:
                    continue
                dish = sum(1 for w in ws[at[-1] + 1:] if _dish(w))
                if g['refused'] and dish:
                    continue
                exact = 0 if any(ws[k] in syl for k in at) else 1
                if exact and g['refused']:
                    continue                            # refused: the word itself only (양파 is not 파, r7 I2)
                pack, size, keys = pack_of(name)
                hit = 1 if keys & g['sizes'] else 0
                small = amount(name) if g['small'] else ()
                many = sum(1 for k, w in enumerate(ws) if k not in at and len(w) > 1 and w not in STOP) > 2   # as `other`
                scored.append(((exact + dish, -hit) + ((many,) if g['small'] else ()) + small
                               + (len(ws), -sold.get(x['sku'], 0), len(name)), x, pack, size))
            if one and any(p[0][0] == 0 for p in scored):
                scored = [p for p in scored if p[0][0] == 0]   # the word itself is sold: no 'ends with' (새청무, 양파)
            scored.sort(key=lambda p: p[0])
            ranked = scored
        if not ranked and not g['refused']:
            continue
        opts = []
        for n, p in enumerate(ranked[:OPTIONS]):
            x = p[1]
            pack, size = (p[2], p[3]) if len(p) > 2 else pack_of(x.get('name', ''))[:2]
            o = {'sku': x.get('sku'), 'name': str(x.get('name'))[:60 if n == 0 else 40], 'pack': pack, 'pack_size': size,
                 'market_sold': sold.get(x.get('sku'), 0), 'we_hold': held.get(x.get('sku'), 0),
                 **({'near': True} if one and p[0][0] else {})}             # only a word ending in it: check the name
            if n == 0:                                  # the one to buy in full; the others short so more asks fit (r10, r11)
                o.update(list_price=x.get('list_price'), shelf_hours=x.get('shelf_hours'))
            else:
                o = {k: v for k, v in o.items() if k != 'pack_size' and v not in (None, 0)}
            opts.append(o)
        top = opts[0]['sku'] if opts else None          # the same first SKU: one entry, whoever refused or not (r9 I1);
        if opts and top in seen:                        # a refusing ask's first SKU already passed its filter
            e = seen[top]
            if g['refused'] and not e.get('substitutes_refused'):
                e['substitutes_refused_customers'] = e.get('substitutes_refused_customers', 0) + len(g['customers'] - e['_who'])
            elif not g['refused'] and e.get('substitutes_refused'):
                e.pop('substitutes_refused')            # one plain ask among them: the first option is still the item
                e['substitutes_refused_customers'] = len(e['_who'])
            e['asked'] = (e['asked'] + [i for i in g['items'] if i not in e['asked']])[:3]
            e['asks'] += g['asks']
            e['_last'] = max(e['_last'], g['last'])
            e['_who'] |= g['customers']
            e['_sizes'] |= g['sizes']
            e['customers'] = len(e['_who'])
            if g['small']:
                e['small_pack_asked'] = True
            continue
        e = {'asked': g['items'][:3], 'asks': g['asks'], 'customers': len(g['customers']), '_who': set(g['customers']),
             '_both': len(both.get(g['key'], ())),
             '_last': g['last'], '_sizes': set(g['sizes']), 'buy_options': opts}
        if g['small']:
            e['small_pack_asked'] = True
        if g['refused']:
            e['substitutes_refused'] = True
            if not opts:
                e['note'] = 'substitutes_refused: no catalog item is exactly what was asked; do not buy a substitute'
        if opts:
            seen[top] = e
        out.append(e)
    out.sort(key=lambda x: (-max(x['customers'], x.get('_both', 0)), -x['asks']))   # all who asked the item (r10 M2)
    listed, held_out = [], []
    for e in out:
        e.pop('_who', None)
        e.pop('_both', None)
        e['last_asked'] = time.strftime('%m-%d %H:%M', time.localtime(e.pop('_last')))
        fits, unfit = held_fit(e, by_sku, e.pop('_sizes', set()))
        # only the first option, matched as the item (not 'near'), moves the ask out: a held 2nd / 3rd option may be
        # another food (양념치킨 -> 닭볶음탕양념, 파 -> 양파: review 1 I1), so that ask stays listed, marked held_maybe
        first = (e.get('buy_options') or [{}])[0]
        moved = [h for h in fits if h['sku'] == first.get('sku') and not first.get('near')]
        if moved:                                       # we hold what was asked: not stock 0 (sell it first)
            e['held'] = moved
            held_out.append(e)
            continue
        if fits:
            e['held_maybe'] = fits
        if unfit:                                       # we hold another size only: that size is still stock 0
            e['held_not_fit'] = unfit
        listed.append(e)
    if want_held:
        return listed[:MAX_GROUPS], held_out[:MAX_GROUPS]
    return listed[:MAX_GROUPS]


def small_pack(name):
    """True only for a pack of known size that is small: at most SMALL_G g/ml in all, or SMALL_N pieces."""
    kind, total = amount(name)
    return bool(total) and ((kind == 0 and total <= SMALL_G) or (kind == 1 and total <= SMALL_N))


def held_fit(e, by_sku, asked_sizes):
    """(held options that are what was asked, held options of another size). By SKU (held = inventory units of that
    SKU, live); a size the customer wrote must all be on the held name ('200g' is not '300g'); a small-pack ask
    needs a small pack. Every buy_options SKU is looked at, not only the first (11/27 G22fcdb90 was the 3rd)."""
    fits, unfit = [], []
    for o in e.get('buy_options') or []:
        n = o.get('we_hold') or 0
        if not n:
            continue
        name = str((by_sku.get(o.get('sku')) or {}).get('name') or o.get('name') or '')
        why = None
        on_name = pack_of(name)[2] | {_norm(t[2], t[3]) for t in sizes(name) if t[3] not in _MULT}
        # weights / volumes / pack kinds only: how many ('우유 2개', '1팩') is not a size (review 1 M3)
        want = {k for k in asked_sizes if k[1] in ('g', 'ml') or k[1] in _KIND}
        if want and not want <= on_name:
            why = 'other_size'
        elif e.get('small_pack_asked') and not small_pack(name):
            why = 'not_small_pack'
        h = {'sku': o.get('sku'), 'pack': o.get('pack'), 'we_hold': n}
        if why:
            h['why'] = why
            unfit.append(h)
        else:
            fits.append(h)
    return fits, unfit


def run(args, kit):
    base = kit.market.rstrip('/')
    cat, prices, inv = fetch(base, kit.token('shop'))
    surge = set()
    if isinstance(prices, dict):
        rows = prices.get('surge', [])
        if isinstance(rows, list): surge = {str(x.get('sku')) for x in rows if isinstance(x, dict)}
    foods = [x for x in cat if isinstance(x, dict) and x.get('kind') == 'food'] if isinstance(cat, list) else []
    wanted = ('생수', '우유', '두유', '쌀', '계란', '국수', '면', '과일')
    out = []
    for x in foods:
        text = str(x.get('name', '')) + ' ' + str(x.get('category', ''))
        if not any(w in text for w in wanted): continue
        p = x.get('list_price')
        if not isinstance(p, (int, float)) or p <= 3000: continue
        out.append({'sku': x.get('sku'), 'name': x.get('name'), 'category': x.get('category'), 'list_price': p, 'shelf_hours': x.get('shelf_hours'), 'surge': str(x.get('sku')) in surge})
    out.sort(key=lambda x: (x['surge'], -x['list_price']))
    inv = inv if isinstance(inv, dict) else {}
    held_asks = []
    try:
        asked, held_asks = asked_out_of_stock(read_lost(), foods, held_by_sku(inv, _clock()), _clock(), market_sold(),
                                              want_held=True)
    except Exception as e:                              # a bad log line must not hide the catalog
        asked = {'error': type(e).__name__ + ': ' + str(e)[:120]}
    return fit({'balance': inv.get('balance'), 'restock': inv.get('restock'), 'surge_skus': sorted(surge),
            'inventory_ok': isinstance(inv.get('units'), list),
            'note': ('customer_asked_out_of_stock: 최근 %dh 손님이 찾았는데 재고 0이었던 식품. 공급 창이 열리면 항목마다 '
                     'buy_options 1~3번째에서 손님이 쓴 것과 같은 식품(포장이 달라도 같은 식품, 쇼콜라≠콜라)을 골라 buy_food_lot {sku, qty 2~4}로 먼저 산다(셋 다 다르면 건너뜀). '
                     'small_pack_asked: true이면 pack/pack_size가 작은 것(크기를 아는 소포장)을 고른다. '
                     'buy_options가 빈 항목(substitutes_refused: 손님이 대체품 거부)은 사지 않는다. '
                     'near: true는 손님이 쓴 한 글자로 끝나는 다른 이름(양파 등)이니 이름을 보고 같은 것일 때만 산다. '
                     '수량은 품목당 2~4개 안에서 asks로 정한다: asks 1이면 2개, 2이면 3개, 3 이상이면 4개. '
                     'held_not_fit: 그 SKU를 이미 갖고 있지만 손님이 찾은 규격이 아니다(not_small_pack: 소포장 아님, other_size: 다른 크기). '
                     '그 규격은 재고 0이니 같은 SKU 말고 buy_options에서 손님 규격에 가까운 다른 SKU를 고른다. '
                     'held_maybe: buy_options 2·3번째 SKU를 갖고 있다. 손님이 찾은 식품이 맞으면 그 재고를 먼저 팔고, 아니면 위 기준대로 산다. '
                     'asked_but_held: buy_options 1번째 SKU(held)를 손님 규격으로 지금 갖고 있어 재고 0이 아닌 문의. 그 재고를 먼저 판다. '
                     'held가 손님이 찾은 식품이 아니면 buy_options에서 위 기준대로 산다. 매입 도구는 어느 경우에도 막히지 않는다.') % LOOKBACK_H,
            'customer_asked_out_of_stock': asked,
            'asked_but_held': held_asks,
            'candidates': out[:80]})


def fit(res, limit=FIT):
    """The kit cuts a tool result at 6,000 characters, so the end of a long answer was never read (and messages
    that came meanwhile were cut with it: 11/26 10:01 bread, 11:24 rice; the wrapper re-adds those). The answer stays
    under limit: the note is kept and comes first, then the catalog candidates go, then the least asked items; what
    was left out is counted."""
    size = lambda: len(json.dumps(res, ensure_ascii=False, default=str))
    while size() > limit and res.get('candidates'):
        res['candidates'] = res['candidates'][:len(res['candidates']) * 2 // 3]
        res['more_candidates'] = True
    held = res.get('asked_but_held')
    while size() > limit and isinstance(held, list) and held:   # already in stock: these go before the stock-0 asks
        held.pop()
        res['more_held'] = res.get('more_held', 0) + 1
    asked = res.get('customer_asked_out_of_stock')
    while size() > limit and isinstance(asked, list) and asked:
        asked.pop()
        res['more_asked'] = res.get('more_asked', 0) + 1
    return res
