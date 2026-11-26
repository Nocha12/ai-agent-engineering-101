def test(run, kit):
    """Offline: the market and the lost-deal log are fakes; nothing is written."""
    g = run.__globals__
    now = g['_clock']()
    CAT = [
        {'sku': 'G-ramen', 'kind': 'food', 'name': '농심 신라면 120g 5개입', 'category': '식품>면>라면', 'list_price': 4500, 'shelf_hours': 400},
        {'sku': 'G-ramen2', 'kind': 'food', 'name': '오뚜기 진라면 매운맛 멀티팩 120g 5개', 'category': '식품>면>라면', 'list_price': 4200, 'shelf_hours': 400},
        {'sku': 'G-mack', 'kind': 'food', 'name': '손질 고등어 1손 600g', 'category': '식품>수산', 'list_price': 9900, 'shelf_hours': 48},
        {'sku': 'G-flat', 'kind': 'food', 'name': '국내산 손질 두절 가자미 4미 1팩', 'category': '식품>수산', 'list_price': 15000, 'shelf_hours': 48},
        {'sku': 'G-egg', 'kind': 'food', 'name': '무항생제 계란 20구', 'category': '식품>계란', 'list_price': 12000, 'shelf_hours': 168},
        {'sku': 'N-phone', 'kind': 'goods', 'name': '스마트폰 본체', 'category': '디지털', 'list_price': 900000},
    ]
    INV = {'balance': 4600000, 'restock': {'open': True}, 'units': [
        {'unit': 'u1', 'sku': 'G-egg'},                                     # eggs in stock: an egg ask is not a fetch
        {'unit': 'u2', 'sku': 'G-mack', 'expires_at': now - 60}]}           # spoiled mackerel is not stock
    LOST = [
        {'ts': now - 600, 'customer': 'a', 'item': '라면', 'sku': '라면', 'reason': '재고없음', 'grade_stock': '재고 0'},
        {'ts': now - 500, 'customer': 'b', 'item': '봉지라면', 'reason': '재고없음', 'grade_stock': '재고 0'},
        {'ts': now - 400, 'customer': 'c', 'item': '라면', 'reason': '재고없음', 'grade_stock': '재고 0'},
        {'ts': now - 300, 'customer': 'd', 'item': '손질 고등어', 'reason': '재고없음', 'grade_stock': '재고 0'},
        {'ts': now - 200, 'customer': 'e', 'item': '국내산 손질 두절 가자미 4미 1팩', 'sku': 'G-flat', 'reason': '재고없음', 'grade_stock': '재고 0'},
        {'ts': now - 100, 'customer': 'f', 'item': '계란 20구', 'reason': '재고없음', 'grade_stock': '재고 0'},
        {'ts': now - 100, 'customer': 'g', 'item': '스마트폰 본체', 'reason': '재고없음', 'grade_stock': '재고 0'},
        {'ts': now - 50, 'customer': 'h', 'item': '두부', 'reason': '다른 손님 결제서 예약으로 발급 거절', 'grade_stock': 'A등급 재고 확인'},
        {'ts': now - 30 * 3600, 'customer': 'i', 'item': '커피', 'reason': '재고없음', 'grade_stock': '재고 0'},
        {'ts': float('nan'), 'customer': 'j', 'item': '커피', 'reason': '재고없음', 'grade_stock': '재고 0'},   # bad row: skipped
        {'ts': now - 20, 'customer': '', 'item': '두절 가자미 4미', 'reason': '재고없음', 'grade_stock': '재고 0'},  # same SKU as G-flat
    ]
    saved = {k: g[k] for k in ('fetch', 'read_lost', 'market_sold')}
    try:
        g['fetch'] = lambda base, token: (CAT, {'surge': [{'sku': 'G-ramen2'}]}, INV)
        g['read_lost'] = lambda path=None: LOST
        g['market_sold'] = lambda path=None: {'G-ramen2': 5}          # the market sells this one: it ranks first

        class K:
            market, testing = 'https://market.example', True
            def token(self, role): return 'offline'
        r = run({}, K())
        cands = r['candidates']
        assert r['balance'] == 4600000 and all(x['list_price'] > 3000 for x in cands), cands
        assert [x['surge'] for x in cands] == sorted(x['surge'] for x in cands), cands   # surge last
        asked = r['customer_asked_out_of_stock']
        assert isinstance(asked, list), asked
        names = {x['asked'][0]: x for x in asked}
        # asks in other words for the same SKU are one entry (라면 a,c + 봉지라면 b); the name with fewer words the
        # customer did not write (매운맛, 멀티팩 ...) ranks before the one the market sells more
        ramen = names['라면']
        assert ramen['asked'] == ['라면', '봉지라면'] and ramen['customers'] == 3 and ramen['asks'] == 3, ramen
        assert ramen['buy_options'][0]['sku'] == 'G-ramen' and ramen['buy_options'][1]['sku'] == 'G-ramen2', ramen
        assert ramen['buy_options'][1]['market_sold'] == 5 and '_who' not in ramen
        assert ramen['buy_options'][0]['pack'] == '5개입' and ramen['buy_options'][0]['pack_size'] == 5, ramen
        # spoiled units are not stock, so the mackerel ask stays; a catalog SKU in the log is taken as is
        assert names['손질 고등어']['buy_options'][0]['sku'] == 'G-mack'
        flat_e = [x for x in asked if x['buy_options'][0]['sku'] == 'G-flat'][0]
        assert flat_e['asks'] == 2 and flat_e['customers'] == 1, flat_e        # blank customer not counted
        # food we hold now, non-food, asks that were not about stock and asks older than LOOKBACK_H are left out
        flat = [o['sku'] for x in asked for o in x['buy_options']]
        assert 'G-egg' not in flat and 'N-phone' not in flat, flat
        assert not any('두부' in x['asked'] or '커피' in x['asked'] for x in asked)
        assert all(x['buy_options'][0]['we_hold'] == 0 for x in asked)
        assert r['inventory_ok'] is True
        # one SKU is listed once even when it is asked for in different words
        tops = [x['buy_options'][0]['sku'] for x in asked]
        assert len(tops) == len(set(tops)) and tops.count('G-flat') == 1, tops
        # merged entries keep the latest ask time; odd rows are skipped one by one; long text stays fast
        import time as _t
        assert ramen['last_asked'] == _t.strftime('%m-%d %H:%M', _t.localtime(now - 400)), ramen
        many = LOST + [{'ts': 10 ** 400, 'customer': 'x', 'item': '라면', 'reason': '재고없음'}] + [
            {'ts': now - 5, 'customer': 'c%d' % i, 'item': '가나다라마바사아자차카타파하' * 40 + str(i), 'reason': '재고없음'} for i in range(40)] + [
            {'ts': now - 5, 'customer': 'w%d' % i, 'item': '가나다라' + ' ' * (i + 1) + '마바사아' + '파' * i, 'reason': '재고없음'} for i in range(2500)]
        g['read_lost'] = lambda path=None: many
        CAT.extend({'sku': 'G-x%d' % i, 'kind': 'food', 'name': '상품 이름 가나다 %d 번째 포장' % i, 'list_price': 5000} for i in range(4000))
        t0 = _t.time()
        r = run({}, K())
        assert isinstance(r['customer_asked_out_of_stock'], list) and _t.time() - t0 < 8, _t.time() - t0
        # the kit cuts at 6,000 characters and messages that came meanwhile go after the result (11/26 10:01, 11:24)
        n = len(g['json'].dumps(r, ensure_ascii=False, default=str))
        assert n <= 5900 and r['note'] and r['customer_asked_out_of_stock'], n
        assert list(r).index('note') < list(r).index('customer_asked_out_of_stock'), list(r)   # the note is never the cut part
        big = {'note': 'n', 'customer_asked_out_of_stock': [{'asked': ['쌀%d' % i], 'buy_options': [{'name': '가' * 300}]}
                                                            for i in range(20)],
               'candidates': [{'name': '나' * 100} for _ in range(80)]}
        f = g['fit'](big)
        assert len(g['json'].dumps(f, ensure_ascii=False)) <= 5900 and f['more_candidates'] and f['more_asked'] > 0, f.keys()
        assert f['customer_asked_out_of_stock'][0]['asked'] == ['쌀0']                         # the most asked stay
        del CAT[6:]
        # a broken log reader does not hide the catalog
        g['read_lost'] = lambda path=None: (_ for _ in ()).throw(ValueError('bad'))
        r = run({}, K())
        assert 'error' in r['customer_asked_out_of_stock'] and r['candidates']
        # no log file yet: an empty list
        g['read_lost'] = saved['read_lost']
        assert g['read_lost']('logs/no-such-file.jsonl') == []
        assert saved['market_sold']('ops/no-such-file.json') == {}
        _test_match_refuse_pack(g, K, now)
    finally:
        g.update(saved)
    return 'ok'


def _test_match_refuse_pack(g, K, now):
    """Name match before sales, refused substitutes, pack sizes and small-pack asks (11/26 생깻잎 → 생깻잎김치)."""
    f = lambda sku, name: {'sku': sku, 'kind': 'food', 'name': name, 'category': '식품', 'list_price': 9900, 'shelf_hours': 48}
    CAT = [f('G-kkimchi', '생깻잎김치 1kg'), f('G351493cf', '셰프애찬 마늘밤깻잎김치600g/팩'),
           f('G4a5dfb19', '[마녀바스켓] 밥도둑 깻잎무침 1kg'),
           f('G-zuk1', '국내산 애호박 1개'), f('G-zuk2', '[산지직송] 애호박 2kg'), f('G-zukjeon', '애호박전 반찬 300g'),
           f('G-mack', '손질 고등어 1손 600g'), f('G-mackstew', '고등어김치찌개 밀키트 800g'),
           f('G0dc76743', '2026년 햅쌀 국산 백미 쌀 5kg 상등급'), f('G281176ec', '2026년 햅쌀 국산 백미 쌀 10kg (5kgX2봉) 상등급'),
           f('G3c4127d8', '2026년 햅쌀 국산 백미 쌀 2kg 소포장 상등급'), f('G0c699766', '26년산 새청무 특등급 단일품종 20kg 쌀 백미'),
           f('G-ricens', '햅쌀 국산 백미 상등급'),
           f('G-tofu1', '풀무원 두부 300g'), f('G-tofu2', '종가집 두부 300g'),
           f('G476cc094', '외갓집 송탄 서정리 부대찌개 1kg x 2팩 (라면사리 2개)'), f('G18c3a609', '삼양 라면 120g X 20봉')]
    SOLD = {'G-kkimchi': 0, 'G-zukjeon': 9, 'G-mackstew': 9, 'G281176ec': 7, 'G0c699766': 8, 'G-ricens': 9, 'G-tofu2': 3}
    row = lambda who, item, msg, wanted='1개': {'ts': now - 60, 'customer': who, 'item': item, 'sku': item, 'wanted': wanted,
                                                'grade_stock': '재고 0', 'last_message': msg, 'reason': '재고없음', 'by': 'restock_shoutout'}
    g['fetch'] = lambda base, token: (CAT, {'surge': []}, {'balance': 1, 'units': []})
    g['market_sold'] = lambda path=None: SOLD

    def ask(rows):
        g['read_lost'] = lambda path=None: rows
        return g['run']({}, K())

    # 1) the 11/26 case: two customers refuse anything but 생깻잎, one does not. The refusing asks are their own entry with
    #    no buy_options (no catalog name is only 생깻잎), so they are never the reason to buy 생깻잎김치.
    r = ask([row('김소심-58cb', '생깻잎', '깻잎지 담글 생깻잎을 찾습니다. 생깻잎 외 품목은 원치 않음.'),
             row('박단호-11aa', '생깻잎', '양념 깻잎지나 다른 채소는 대신 사지 않겠습니다.'),
             row('이보통-22bb', '생깻잎', '생깻잎 있나요?')])
    asked = r['customer_asked_out_of_stock']
    refused = [e for e in asked if e.get('substitutes_refused')]
    assert len(refused) == 1 and refused[0]['buy_options'] == [] and refused[0]['customers'] == 2, asked
    assert 'substitutes_refused' in refused[0]['note'] and refused[0]['asked'] == ['생깻잎'], refused
    plain = [e for e in asked if not e.get('substitutes_refused')]
    assert len(plain) == 1 and plain[0]['customers'] == 1 and plain[0]['buy_options'], asked
    assert all(e['customers'] == 1 for e in asked if any(o['sku'] == 'G-kkimchi' for o in e['buy_options'])), asked
    note = r['note']
    assert 'buy_options' in note and 'small_pack_asked' in note and 'substitutes_refused' in note, note

    # 2) a refusing ask gets no name with something glued onto the customer's word ('애호박전'),
    #    even when a longer name sells more
    for msg in ('애호박 말고는 안 사요', '대체품 사절입니다', '대체품은 싫어요', '대체 안 됩니다',
                '애호박 외 품목은 원치 않음', '다른 반찬은 사지 않겠습니다'):
        e = ask([row('최애호', '애호박', msg)])['customer_asked_out_of_stock']
        assert len(e) == 1 and e[0].get('substitutes_refused') is True, (msg, e)
        assert [o['sku'] for o in e[0]['buy_options']] == ['G-zuk1', 'G-zuk2'], (msg, e)
    for msg in ('애호박 있나요? 차량용은 원치 않음.', '컵라면 말고 봉지라면을 찾아요', '대체 가능하면 알려주세요',
                '신선 애호박만 원함.', '애호박만 찾아요', '계란 한 판만 원해요', '두부 한 모만 찾아요', '백미 조금만 찾아요'):   # amounts (r1 C1)
        e = ask([row('최애호', '애호박', msg)])['customer_asked_out_of_stock']
        assert len(e) == 1 and not e[0].get('substitutes_refused'), (msg, e)
        assert 'G-zukjeon' in [o['sku'] for o in e[0]['buy_options']], (msg, e)       # a near match is still offered

    # 2b) refused, but the exact item carries an origin or brand: it is still bought (review r1 I2)
    CAT.append(f('G-kkae', '충남 금산 생깻잎 200g'))
    e = ask([row('김소심-58cb', '생깻잎', '생깻잎 외 품목은 원치 않음.')])['customer_asked_out_of_stock']
    assert e[0].get('substitutes_refused') and [o['sku'] for o in e[0]['buy_options']] == ['G-kkae'], e
    CAT.pop()
    CAT.extend([f('G-milk', '[서울우유] 흰우유 1L'), f('G-garlic', '깐마늘 1kg'), f('G-garjang', '마늘장아찌 500g')])
    e = ask([row('k', '우유', '우유만요, 다른 건 원치 않아요')])['customer_asked_out_of_stock']
    assert [o['sku'] for o in e[0]['buy_options']] == ['G-milk'], e               # r2 I-2: 흰우유 is milk
    e = ask([row('k', '마늘', '대체품 사절')])['customer_asked_out_of_stock']
    assert [o['sku'] for o in e[0]['buy_options']] == ['G-garlic'], e             # 깐마늘 yes, 마늘장아찌 no
    del CAT[-3:]
    # r4 I1: real names that end in descriptors stay for a refusing ask (best_sellers_data.json names)
    CAT.extend([f('G-soy', '1.맛있는 두유 GT 담백한맛/검은콩 190ml x 24개'), f('G-ban', '델몬트 바나나 2송이 2.6kg내외'),
                f('G-yog', '그릭요거트 플레인 400g'), f('G-egg30', '계란 30구 특란')])
    for item, sku in (('두유', 'G-soy'), ('바나나', 'G-ban'), ('요거트', 'G-yog'), ('계란', 'G-egg30')):
        e = ask([row('k', item, '대체품 사절')])['customer_asked_out_of_stock']
        assert sku in [o['sku'] for o in e[0]['buy_options']], (item, e)
    del CAT[-4:]
    # r5 I1: part of the customer's word is not the item (양념깻잎 for 생깻잎, 돼지고기 for 소고기)
    CAT.extend([f('G-yang', '온밀담 양념깻잎 1kg+1kg 국내제조 HACCP/ GS단독'), f('G-pork', '찌개용 돼지고기 500g'),
                f('G-beef', '국내산 소고기 국거리 300g'), f('G-gtmilk', '1.맛있는우유GT 고소한저지방 190ml x 24개')])
    e = ask([row('김소심-58cb', '생깻잎', '생깻잎 외 품목은 원치 않음.')])['customer_asked_out_of_stock']
    assert 'G-yang' not in [o['sku'] for o in e[0]['buy_options']], e
    e = ask([row('k', '소고기', '대체품 사절')])['customer_asked_out_of_stock']
    assert [o['sku'] for o in e[0]['buy_options']] == ['G-beef'], e
    e = ask([row('k', '우유', '대체품 사절')])['customer_asked_out_of_stock']
    assert 'G-gtmilk' in [o['sku'] for o in e[0]['buy_options']], e              # r5 M1: Latin glued on is not a food
    del CAT[-4:]
    # r5 I2: a small amount asked: the single pack before the multi-pack of the same thing
    amount = g['amount']
    assert amount('풀무원 두부 300g×5팩') == (0, 1500) and amount('CJ 햇반 210g x12개') == (0, 2520), amount('CJ 햇반 210g x12개')
    assert amount('2026년 햅쌀 국산 백미 쌀 10kg (5kgX2봉) 상등급') == (0, 10000) and amount('계란 30구')[0] == 1
    CAT.extend([f('G-tofu5', '풀무원 두부 300g×5팩'), f('G-bean10', '무농약 콩나물 300g 10봉'), f('G-bean1k', '무농약 콩나물 1kg')])
    e = ask([row('k', '두부', '혼자라 조금만')])['customer_asked_out_of_stock']
    o = [x['sku'] for x in e[0]['buy_options']]
    assert o.index('G-tofu1') < o.index('G-tofu5'), o
    e = ask([row('k', '콩나물', '소량만')])['customer_asked_out_of_stock']
    o = [x['sku'] for x in e[0]['buy_options']]
    assert o.index('G-bean1k') < o.index('G-bean10'), o
    del CAT[-3:]
    assert amount('[씨제이] 햇반 찰기가득 쌀밥 210g x 12 (1박스)') == (0, 2520), amount('[씨제이] 햇반 찰기가득 쌀밥 210g x 12 (1박스)')
    # r6 I1: one-letter staples get options: 쌀 → 햅쌀·백미쌀 names, never 쌀국수 for a refusing ask
    CAT.extend([f('G-rnood', '쌀국수 밀키트 2인분'), f('G-radish', '제주 무 1개'), f('G-mu', '무말랭이 무침 200g')])
    e = ask([row('d', '쌀 5kg', '쌀 5kg 한 포대 찾습니다')])['customer_asked_out_of_stock']
    o = [x['sku'] for x in e[0]['buy_options']]
    assert o and o[0] == 'G0dc76743' and 'G-rnood' not in o, e                   # the 5kg rice first; 쌀국수 starts with 쌀
    e = ask([row('d', '무', '무 하나요, 다른 건 원치 않음')])['customer_asked_out_of_stock']
    o = [x['sku'] for x in e[0]['buy_options']]
    assert o[0] == 'G-radish' and 'G-mu' not in o, e                             # the word itself first; 무말랭이 never
    # r15: a one-letter item next to a longer word is its own item, bought by its own name, refused on its own
    e = ask([row('a', '유기농 쌀 10kg', '유기농 쌀 10kg'), row('b', '유기농 무', '유기농 무 있나요'), row('c', '유기농 무', '무')])
    e = {x['asked'][0]: x for x in e['customer_asked_out_of_stock']}
    assert set(e) == {'유기농 쌀 10kg', '유기농 무'} and e['유기농 무']['buy_options'][0]['sku'] == 'G-radish', e
    e = ask([row('a', '유기농 쌀', '유기농 쌀. 다른 건 사지 않겠습니다'), row('a', '유기농 무', '유기농 무도')])
    e = {x['asked'][0]: x for x in e['customer_asked_out_of_stock']}
    assert e['유기농 쌀'].get('substitutes_refused') and not e['유기농 무'].get('substitutes_refused'), e
    # r16: a refused one-letter item next to a longer word buys the item itself, never 찹쌀 / 쌀국수 / 단무지
    CAT.extend([f('X-rice4', '유기농 쌀 4kg'), f('X-chap', '유기농 찹쌀 4kg'), f('X-noodle', '유기농 쌀국수 500g'),
                f('X-mu', '유기농 무 1개'), f('X-danmu', '유기농 단무지')])
    for item, want in (('유기농 쌀', 'X-rice4'), ('유기농 무', 'X-mu')):
        e = ask([row('a', item, item + '. 다른 건 안 사요')])['customer_asked_out_of_stock']
        assert [x['sku'] for x in e[0]['buy_options']] == [want], (item, e)
    e = ask([row('a', '유기농 무 좀', '유기농 무 좀')])['customer_asked_out_of_stock']
    assert e[0]['buy_options'][0]['sku'] == 'X-mu', e
    del CAT[-5:]
    # r17: a refused ask finds its item spaced otherwise ('깐마늘' / '깐 마늘', '유기농 쌀' / '유기농쌀'), not 찹쌀 / 쌀국수
    CAT.extend([f('Y-garlic', '깐 마늘 1kg'), f('Y-rice', '유기농쌀 4kg'), f('Y-chap', '유기농찹쌀 4kg'), f('Y-noodle', '유기농쌀국수')])
    for item, want in (('깐마늘', 'Y-garlic'), ('유기농 쌀', 'Y-rice')):
        e = ask([row('a', item, item + '. 다른 건 안 사요')])['customer_asked_out_of_stock']
        assert [x['sku'] for x in e[0]['buy_options']] == [want], (item, e)
    del CAT[-4:]
    # r18: a refused ask is never offered a name its word only straddles, nor a rice noodle
    CAT.extend([f('Z-pear', '나주 배 추석 선물세트 7.5kg'), f('Z-tur', '국산 생 강황 300g'), f('Z-nood', '유기농쌀 국수 500g')])
    for item in ('배추', '생강', '유기농 쌀'):
        e = ask([row('a', item, item + '. 다른 건 안 사요')])['customer_asked_out_of_stock']
        assert not {'Z-pear', 'Z-tur', 'Z-nood'} & {x['sku'] for x in e[0]['buy_options']}, (item, e)
    del CAT[-3:]
    # 14:21 live: '라면' with a small amount asked bought '맛있는라면 115g x 20개' first; the 8-pack goes first now,
    # brand words ('오뚜기') no longer beat the pack size. Same for coffee: 200g before '1L x12'
    CAT.extend([f('L-20', '맛있는라면 115g x 20개'), f('L-40', '[농심] 신라면 120g 1박스 40봉 (멀티팩 5입X8팩)'),
                f('L-8', '오뚜기 마열라면 120g 8개'), f('C-12', '[이디야] 믹스커피 1L x12'),
                f('C-100', '비나카페 아메리카노 블랙커피 100입 200g'), f('C-200', '코델리/헤이즐넛향 원두커피 200g (홀빈 or 분쇄)')])
    e = ask([row('a', '라면', '혼자 먹을 라면 조금만요')])['customer_asked_out_of_stock']
    assert e[0]['small_pack_asked'] and e[0]['buy_options'][0]['sku'] == 'L-8', e
    e = ask([row('a', '커피', '아침에 마실 커피 소포장으로요')])['customer_asked_out_of_stock']
    assert e[0]['buy_options'][0]['sku'] in ('C-100', 'C-200'), e
    # 15:02 live (q5): a name full of other words ('보리차 … 보리 커피') is not bought first for a small 커피 ask,
    # nor a cup noodle soup for 라면
    CAT.extend([f('C-barley', '크라스탄 오르조 유기농 보리차 디카페인 보리 커피 125g 카페인없는 차 임산부'),
                f('L-cup', '컵라면 소컵 농심 사리곰탕 61g 12개')])
    e = ask([row('a', '커피', '아침에 마실 커피 소포장으로요')])['customer_asked_out_of_stock']
    assert e[0]['buy_options'][0]['sku'] in ('C-100', 'C-200'), e
    e = ask([row('a', '라면', '혼자 먹을 라면 조금만요')])['customer_asked_out_of_stock']
    assert e[0]['buy_options'][0]['sku'] == 'L-8', e
    del CAT[-2:]
    # q5 r1 I1: the one-letter path counts extra words like the main path ('2026년 햅쌀 국산 백미 쌀 2kg 소포장' is small)
    CAT.extend([f('S-20', '쌀 20kg'), f('S-10', '백미 쌀 10kg')])
    e = ask([row('a', '쌀', '쌀 조금만 주세요')])['customer_asked_out_of_stock']
    assert e[0]['buy_options'][0]['sku'] == 'G3c4127d8', e
    del CAT[-2:]
    e = ask([row('a', '라면', '라면 있어요?')])['customer_asked_out_of_stock']
    assert not e[0].get('small_pack_asked') and [x['sku'] for x in e[0]['buy_options']][:2] == ['L-40', 'L-20'], e   # as before
    del CAT[-6:]
    # q4 r1 I1: small first never picks another food ('우유 식빵', '쌀 튀밥') over the item itself
    CAT.extend([f('M-1', '서울우유 1L'), f('M-bread', '우유 식빵 400g'), f('M-900', '매일 우유 900ml'),
                f('M-pud', '우유 푸딩 100g x 4개'), f('R-1', '쌀 1kg'), f('R-puff', '현미 쌀 튀밥 50g')])
    e = ask([row('a', '우유', '우유 조금만요')])['customer_asked_out_of_stock']
    assert e[0]['buy_options'][0]['sku'] == 'M-900', e
    e = ask([row('a', '쌀', '쌀 조금만')])['customer_asked_out_of_stock']
    assert e[0]['buy_options'][0]['sku'] != 'R-puff', e
    del CAT[-6:]
    # q4 r2 I1: a use word ('제빵용', '떡국용') is the item itself, also for a refused ask
    CAT.extend([f('U-flour', '곰표 밀가루 강력분 제빵용 1kg'), f('U-beef', '한우 양지 떡국용 300g')])
    for item, want in (('밀가루', 'U-flour'), ('한우 양지', 'U-beef')):
        e = ask([row('a', item, item + '. 다른 건 안 사요')])['customer_asked_out_of_stock']
        assert [x['sku'] for x in e[0]['buy_options']][:1] == [want], (item, e)
    del CAT[-2:]
    del CAT[-3:]
    # r7: '쌀 20kg' (kg is not a word to match) and one-letter asks: the word itself, '양파' only as near for 파
    CAT.extend([f('G-spin', '시금치 1kg'), f('G-onion', '햇양파 3kg'), f('G-saecheong', '독도사랑 새청무 쌀 5kg')])
    for msg in ('쌀 20kg 있어요?', '대체품 사절'):
        e = ask([row('d', '쌀 20kg', msg)])['customer_asked_out_of_stock']
        o = [x['sku'] for x in e[0]['buy_options']]
        assert o and 'G-spin' not in o and 'G-onion' not in o and o[0] == 'G0c699766', (msg, e)   # the 20kg rice
    e = ask([row('d', '파', '대체품은 안 사요')])['customer_asked_out_of_stock']
    assert e[0]['buy_options'] == [] and e[0]['substitutes_refused'], e            # 양파 is not 파
    e = ask([row('d', '파', '파 있어요?')])['customer_asked_out_of_stock']
    assert [x['sku'] for x in e[0]['buy_options']] == ['G-onion'] and e[0]['buy_options'][0]['near'] is True, e
    e = ask([row('d', '무', '무 있어요?')])['customer_asked_out_of_stock']
    assert all(x['near'] for x in e[0]['buy_options']), e                        # 새청무 only as near, named so
    del CAT[-3:]
    # r8: quantity and packaging words ('한 포대', '10키로', '한 박스') are not the item
    CAT.extend([f('G-hb', '[씨제이] 햇반 찰기가득 쌀밥 210g x 12 (1박스)'), f('G-pear', '신고배 5kg'), f('G-salad', '신선한 샐러드채소 1kg')])
    for item, msg in (('쌀 5kg 한 포대', '쌀 5kg 한 포대를 찾습니다'), ('쌀 5kg 한 포대', '대체품 사절'), ('쌀 10키로', '쌀 10키로요')):
        e = ask([row('d', item, msg)])['customer_asked_out_of_stock']
        o = [x['sku'] for x in e[0]['buy_options']]
        assert o and o[0] in ('G0dc76743', 'G281176ec') and 'G-hb' not in o, (item, msg, e)
    e = ask([row('d', '쌀 10키로', '쌀 10키로요')])['customer_asked_out_of_stock']
    assert e[0]['buy_options'][0]['sku'] == 'G281176ec', e                       # 10키로 is 10kg
    e = ask([row('d', '배 한 박스', '배 한 박스')])['customer_asked_out_of_stock']
    o = [x['sku'] for x in e[0]['buy_options']]
    assert 'G-hb' not in o and o[0] == 'G-pear' and e[0]['buy_options'][0].get('near'), e   # 신고배: near, 햇반 never
    e = ask([row('d', '무 한 개', '무 한 개')])['customer_asked_out_of_stock']
    assert 'G-salad' not in [x['sku'] for x in e[0]['buy_options']], e          # '신선한' is not 무
    del CAT[-3:]
    # r10 I2: letters before the customer's word (자반고등어) are not another food; the size asked (1손) decides
    CAT.extend([f('G-mack10', '순천만수산 국내산 고등어 140-160g x 10팩'), f('G-jaban', 'Fresh 노르웨이산 자반고등어 /1손 (2미) / 대 / 800g 내외')])
    e = ask([row('a', '고등어 1손', '고등어 1손 있어요?')])['customer_asked_out_of_stock']
    assert e[0]['buy_options'][0]['sku'] in ('G-mack', 'G-jaban') and 'list_price' not in e[0]['buy_options'][1], e
    e = ask([row('a', '고등어 1손', '고등어 1손'), row('b', '손질 고등어 한 마리', '혼자 먹을 손질 고등어 한 마리')])
    e = e['customer_asked_out_of_stock']
    assert len(e) == 1 and e[0]['customers'] == 2 and e[0]['buy_options'][0]['sku'] != 'G-mack10', e   # one item (r11)
    # r12 I-1: different sizes of the same item stay apart, each buying its own size; no size joins the one sized ask
    e = ask([row('a', '백미 20kg', '백미 20kg 있나요'), row('b', '백미 2kg', '혼자라 조금만, 백미 2kg 원함'),
             row('c', '백미', '백미 있어요?')])['customer_asked_out_of_stock']
    first = {x['asked'][0]: x['buy_options'][0]['sku'] for x in e}
    assert len(e) == 3 and first['백미 20kg'] == 'G0c699766' and first['백미 2kg'] == 'G3c4127d8', e   # two sizes: 'c' apart
    e = ask([row('a', '백미 2kg', '백미 2kg 있나요'), row('c', '백미', '백미 있어요?')])['customer_asked_out_of_stock']
    assert len(e) == 1 and e[0]['customers'] == 2 and e[0]['buy_options'][0]['sku'] == 'G3c4127d8', e
    # r13: a refusal covers that customer's ask of every size; how many ('2마리') does not split an item
    e = ask([row('a', '백미 20kg', '백미 20kg 있나요'), row('a', '백미 2kg', '없으면 2kg. 다른 건 안 사요')])
    e = e['customer_asked_out_of_stock']
    assert e and all(x.get('substitutes_refused') for x in e), e
    e = ask([row('a', '고등어 1손', '고등어 1손'), row('b', '고등어 2마리', '고등어 2마리요'),
             row('c', '손질 고등어 한 마리', '손질 고등어 한 마리')])['customer_asked_out_of_stock']
    assert len(e) == 1 and e[0]['customers'] == 3, e
    # r14: one-letter staples group and refuse by their letters too
    e = ask([row('b', '쌀 20kg', '쌀 20kg 주세요'), row('c', '쌀', '쌀 있어요?')])['customer_asked_out_of_stock']
    assert len(e) == 1 and e[0]['customers'] == 2 and e[0]['buy_options'][0]['sku'] == 'G0c699766', e
    e = ask([row('a', '쌀 20kg', '쌀 20kg. 다른 건 안 사요'), row('a', '쌀', '쌀 있나요')])['customer_asked_out_of_stock']
    assert e and all(x.get('substitutes_refused') for x in e), e
    del CAT[-2:]
    # r9 I1: one plain and one refusing customer of the same item: one entry with both, the exact item first
    CAT.append(f('G-kimchi', '비비고 포기배추김치 1kg'))
    e = ask([row('a', '일반 배추김치', '일반 배추김치 있어요?'), row('b', '일반 배추김치', '총각김치로 대체하진 말아 주세요')])
    e = [x for x in e['customer_asked_out_of_stock'] if x['buy_options'] and x['buy_options'][0]['sku'] == 'G-kimchi']
    assert len(e) == 1 and e[0]['customers'] == 2 and e[0]['substitutes_refused_customers'] == 1, e
    e = ask([row('k', '파', '다른 건 안 삽니다')])['customer_asked_out_of_stock']
    assert e[0].get('substitutes_refused'), e                                    # r9 M2: 안 삽니다
    CAT.pop()
    # r3 I1: the substitute written as a separate word after the item; a keep-word after it is fine
    CAT.extend([f('G-kjang', '생깻잎 장아찌 1kg'), f('G-zukbok', '애호박 볶음 반찬 300g'), f('G-kcold', '생깻잎 200g 냉장')])
    e = ask([row('k', '생깻잎', '생깻잎 외 품목은 원치 않음')])['customer_asked_out_of_stock']
    assert [o['sku'] for o in e[0]['buy_options']] == ['G-kcold'], e
    e = ask([row('k', '애호박', '대체품 사절')])['customer_asked_out_of_stock']
    assert 'G-zukbok' not in [o['sku'] for o in e[0]['buy_options']] and e[0]['buy_options'], e
    del CAT[-3:]
    # r2 I-3: the refusal came in a later row of the same customer: the earlier ask is not a plain one
    e = ask([row('김씨', '생깻잎', '생깻잎 있어요?'), row('김씨', '생깻잎', '깻잎김치 말고 다른 건 사지 않겠습니다')])['customer_asked_out_of_stock']
    assert len(e) == 1 and e[0]['substitutes_refused'] and e[0]['buy_options'] == [] and e[0]['asks'] == 2, e
    # r2 M-3: restock_shoutout's wanted '3개' is a count, not a pack size
    e = ask([row('d', '두부', '두부 있나요', wanted='3개')])['customer_asked_out_of_stock']
    assert [o['sku'] for o in e[0]['buy_options']] == ['G-tofu2', 'G-tofu1'], e

    # 3) extra words in the name ('김치찌개') rank below a plain match even when the market sells them more;
    #    a size the customer wrote ('5kg') wins over market sales; with the same match, market sales decide
    e = ask([row('d', '고등어', '고등어 한 손')])['customer_asked_out_of_stock']
    assert e[0]['buy_options'][0]['sku'] == 'G-mack', e
    e = ask([row('d', '백미 5kg', '백미 5kg 있나요')])['customer_asked_out_of_stock']
    assert e[0]['buy_options'][0]['sku'] == 'G0dc76743' and 'small_pack_asked' not in e[0], e
    e = ask([row('d', '두부', '두부 한 모')])['customer_asked_out_of_stock']
    assert [o['sku'] for o in e[0]['buy_options']] == ['G-tofu2', 'G-tofu1'], e

    # 4) small amount asked: smaller known pack first, unknown size after the known ones
    for msg in ('혼자 먹을 거라 소포장 있으면 좋겠어요', '조금만 필요해요', '1인 가구예요', '한 봉지면 됩니다', '적당한 양으로'):
        e = ask([row('s', '백미', msg)])['customer_asked_out_of_stock']
        assert e[0].get('small_pack_asked') is True, (msg, e)
        assert [o['sku'] for o in e[0]['buy_options']] == ['G3c4127d8', 'G0dc76743', 'G281176ec'], (msg, e)
    e = ask([row('s', '라면', '혼자 먹을 작은 묶음')])['customer_asked_out_of_stock']      # a small pack of another food does not jump ahead
    assert e[0]['buy_options'][0]['sku'] == 'G18c3a609', e
    e = ask([row('s', '백미', '백미 있나요')])['customer_asked_out_of_stock']
    assert 'small_pack_asked' not in e[0] and e[0]['buy_options'][0]['sku'] == 'G-ricens', e

    # 5) every option carries pack and pack_size parsed from the name (real catalog names); options stay small
    PACKS = [('26년산 새청무 특등급 단일품종 20kg 쌀 백미', '20kg', 20000),
             ('HACCP인증 무항생제 농장직송 달걀 생계란 대란 30구 2판', '30구 2판', 60),
             ('핸드드립커피 드립백커피 6개국 모듬 8g x 36개입', '36개입', 36),
             ('국내산 손질 두절 가자미 4미*1팩 (팩당 400g 내외)', '4미', 4),
             ('국내산 손질 두절 가자미 4미 1팩', '4미', 4),
             ('손질 고등어 1손 600g', '1손', 1),
             ('농심 신라면 120g 5개입', '5개입', 5),
             ('[제주어멍] 손질 간고등어 특대 200g X 10팩', '10팩', 10),
             ('2026년 햅쌀 국산 백미 쌀 10kg (5kgX2봉) 상등급', '10kg', 10000),
             ('온밀담 양념깻잎 1kg+1kg 국내제조 HACCP/ GS단독', '1kg+1kg', 2000),
             ('[무라벨 빈병회수] 스파클 생수 2리터×48병', '48병', 48),
             ('[본사공식] 몽베스트 무라벨 생수 2Lx6병(1팩)', '6병', 6),
             ('손질 가자미 특대 4~6미 1.4kg내외', '1.4kg', 1400),
             ('[매일유업] 소화가 잘되는 우유 미니 125ml 48팩(24팩 2박스)', '48팩', 48),
             ('셰프애찬 마늘밤깻잎김치600g/팩', '600g', 600),
             ('[이디야] 카페라떼 30T x2', '30T x2', 60),
             ('롯데칠성 펩시 콜라 제로슈거 라임향(업소용) 245ml x 60캔', '60캔', 60),
             ('칠성사이다 제로 355ml 12캔(업소용)', '12캔', 12),
             ('[이디야] 스페셜 골드블렌드 커피믹스 120T', '120T', 120),
             ('G7커피 퓨어블랙 라이트 110개입 x 2개 (총 220T)', '110개입 x 2개', 220),
             ('[스파클 빈통 회수] 스파클 생수 말통 (대용량 18.9L)×1통', '18.9L', 18900),
             ('오늘담란 난각번호1번 계란 20구 무항생제 신선한 유정란', '20구', 20),
             ('카누 미니 디카페인 아메리카노100T+20T', '100T+20T', 120),
             ('[코카콜라]캐나다드라이 진저에일토닉 250mlx(10캔)', '10캔', 10),
             ('[순천만수산] 국내산 고등어 140-160g x 10팩', '10팩', 10),
             ('아이들 생일 케이크', None, None)]
    for name, pack, size in PACKS:
        CAT[:] = [f('G-p', name)]
        e = ask([row('p', name, '있나요')])['customer_asked_out_of_stock']
        o = e[0]['buy_options'][0]
        assert (o['pack'], o['pack_size']) == (pack, size), (name, o)
        assert set(o) == {'sku', 'name', 'list_price', 'shelf_hours', 'market_sold', 'we_hold', 'pack', 'pack_size'}, o
