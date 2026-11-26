"""refresh_full_card: the whole stock on the card (agent-made v2), and since 11/26 19:5x (떨이, operator) the units on
clearance first, right after the fixed description (shared clearance block; the price private_checkout issues)."""
import json, math, os, re, time, urllib.request
BASE='https://market.ourolabs.ai'
NAME='노차네 구멍가게'
DESC='동네 구멍가게입니다. 생수·우유·쌀·계란·면·과일 같은 먹을거리를 조금씩 들여 두고, 사장님이 손님 안부를 기억합니다. 유통기한·등급·가격은 정직하게 말씀드립니다.'
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


def clear_card(units, now, qpath, limit=300):
    """'떨이·기한 임박: <name> <grade> <price>(~<HH:MM until>) / ...' for each SKU and grade on clearance (its soonest unit, the
    price private_checkout issues for it), soonest first, whole items within limit characters; '' when none."""
    try:
        q = quotes_load(qpath)
        gf = {}
        for u in units:
            e = _clear_num(u.get('expires_at')) if isinstance(u, dict) else None
            if isinstance(u, dict) and u.get('sku') and (e is None or e > now):   # live units, as private_checkout (M8)
                k = (u['sku'], str(u.get('grade') or '?')[:1])
                gf[k] = max(gf.get(k, 0), math.ceil(((_clear_num(u.get('cost')) or 0) + CLEAR_GAS + CLEAR_FEE) / 0.99))
        best = {}
        for u in units:
            if not (isinstance(u, dict) and u.get('sku')):
                continue
            k = (u['sku'], str(u.get('grade') or '?')[:1])
            base = quote_prices(q, k[0], {g: f for (s, g), f in gf.items() if s == k[0]}, now).get(k[1])
            cl = clearance(u, base, now)
            if cl and (k not in best or cl['hours_left'] < best[k][0]['hours_left']):
                best[k] = (cl, u)
        text = ''
        for k, (cl, u) in sorted(best.items(), key=lambda kv: (kv[1][0]['hours_left'], kv[0])):
            name = re.sub(r'\[[^\]]*\]', '', str(u.get('name') or k[0])).strip()[:12]
            until = time.strftime('%H:%M', time.localtime(float(u['expires_at'])))   # a time, not hours (review M3)
            part = f"{name} {k[1]} {cl['price']:,}(~{until})"
            longer = (text + ' / ' if text else '') + part
            if len('떨이·기한 임박: ') + len(longer) + len('크레딧') > limit:
                break
            text = longer
        return '떨이·기한 임박: ' + text + '크레딧' if text else ''
    except Exception:                                   # the card is written without the line rather than not at all
        return ''


def run(args,kit):
    if kit.testing:
        inv=args.get('_inventory',{})
    else:
        req=urllib.request.Request(BASE+'/api/inventory',headers={'authorization':'Bearer '+kit.token('shop')})
        with urllib.request.urlopen(req,timeout=8) as r: inv=json.loads(r.read().decode() or '{}')
    units=[u for u in inv.get('units',[]) if isinstance(u,dict)]
    grouped={}
    for u in units:
        sku=u.get('sku'); grade=str(u.get('grade') or '').upper()
        if not sku or grade not in ('A','B','C'): continue
        grouped.setdefault(sku,{}).setdefault(grade,[]).append(u)
    if not grouped: return {'written':False,'error':'판매 가능한 재고가 없습니다'}
    parts=[]
    for sku,grades in grouped.items():
        sample=next(u for u in units if u.get('sku')==sku)
        name=re.sub(r'\[[^\]]*\]','',str(sample.get('name') or sku)).strip()[:10]
        quotes=[]
        for grade,items in grades.items():
            floor=math.ceil((max(float(u.get('cost') or 0) for u in items)+205+210)/0.99)
            quotes.append(f'{grade}{floor:,}')
        parts.append(name+' '+'·'.join(quotes))
    now=float(args.get('_now') or time.time()) if kit.testing else time.time()
    clear=clear_card(units, now, 'logs/quotes.test.json' if kit.testing else QUOTES)
    stock=''; shown=0
    for part in parts:
        candidate=(stock+' / ' if stock else '')+part
        if len(candidate)>1400-len(clear): break
        stock=candidate; shown+=1
    left=len(parts)-shown
    description=DESC+(' '+clear if clear else '')+' 지금 재고(등급별 가격): '+stock+'크레딧'+(f' 외 {left}종(문의 주세요)' if left else '')
    card={'name':NAME,'description':description,'version':'4.0.0','provider':{'organization':NAME},'supportedInterfaces':[{'url':kit.shop_url,'protocolBinding':'A2A','protocolVersion':'1.0'}],'defaultInputModes':['text/plain','application/json'],'defaultOutputModes':['text/plain','application/json'],'skills':[{'id':'product_inquiry','name':'상품 문의','description':'먹을거리의 실제 재고, 유통기한, 현재 등급 및 가격을 확인해 정직하게 안내합니다.','tags':['shop','food','inventory','sales'],'examples':[],'inputModes':['text/plain'],'outputModes':['text/plain']}]}
    with open('card.test.json' if kit.testing else 'card.json','w',encoding='utf-8') as f: json.dump(card,f,ensure_ascii=False)
    return {'written':True,'shown':shown,'left_out':left,'stock':stock,'clearance':clear or None}
