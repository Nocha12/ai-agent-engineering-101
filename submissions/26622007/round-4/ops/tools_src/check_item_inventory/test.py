def test(run, kit):
    import json
    import os
    QF = 'logs/quotes.test.json'
    if os.path.exists(QF):
        os.remove(QF)
    now = 1_800_000_000
    inv = {'units': [{'unit': 'u1', 'sku': 'EGG', 'grade': 'B', 'cost': 6300}, {'unit': 'u2', 'sku': 'W', 'grade': 'A'}]}
    r = run({'sku': 'EGG', '_inventory': inv}, kit)
    assert r['ok'] and r['count'] == 1 and r['items'][0]['unit'] == 'u1', r
    assert run({'sku': 'X', '_inventory': inv}, kit)['count'] == 0
    # the whole shop in one answer the agent can read: the kit shows a tool result up to 6,000 characters, and
    # shop_snapshot's raw list (about 500 per unit, certificates included) showed only the first 10 of 60 units
    blob = {'cert': {'unit': 'u', 'sku': 's', 'shop': 'nocha12', 'kind': 'food', 'grade': None}, 'sig': 'x' * 88}
    units = []
    for i in range(20):                                      # 20 SKUs x 3 units, long names
        for j, g in enumerate('ABC'):
            units.append({'unit': 'u-%02d%d' % (i, j), 'sku': 'G%08x' % i, 'grade': g, 'cost': 2000 + 100 * i + j,
                          'name': '정식수입 아주 긴 이름의 먹을거리 %d번 500g 1팩 상온보관 단백질 식단관리' % i, 'kind': 'food',
                          'list_price': 5000, 'expires_at': now + 3600 * (5 + j), 'cert': blob})
    units.append({'unit': 'u-old', 'sku': 'G%08x' % 0, 'grade': 'C', 'cost': 1, 'name': 'x', 'expires_at': now - 60})
    big = {'balance': 4632038, 'restock': {'open': False, 'opens_at': now + 600}, 'units': units}
    r = run({'_inventory': big, '_now': now}, kit)
    assert r['ok'] and r['units'] == 60 and r['expired'] == 1 and r['balance'] == 4632038, r
    assert len(json.dumps(r, ensure_ascii=False)) < 5000, len(json.dumps(r, ensure_ascii=False))
    assert len(r['skus']) == 20 and all(s['count'] == 3 and set(s['grades']) == set('ABC') for s in r['skus']), r['skus'][:2]
    first = [s for s in r['skus'] if s['sku'] == 'G00000000'][0]
    assert first['grades']['C'] == {'n': 1, 'floor': 2442, 'hours_left': 7.0}, first        # 2442 x 0.99 = 2417.58 >= 2002+205+210, 2441 x 0.99 is not; the expired unit is not stock
    # by item name, the way customers and the agent say it; details stay short
    one = run({'sku': '먹을거리 7번', '_inventory': big, '_now': now}, kit)
    assert one['count'] == 3 and {i['sku'] for i in one['items']} == {'G00000007'} and 'cert' not in one['items'][0], one
    assert one['items'][0]['floor'] == 3147 and one['items'][0]['hours_left'] == 5.0, one['items'][0]
    assert run({'sku': 'G00000000', '_inventory': big, '_now': now}, kit)['count'] == 3    # expired unit left out
    many = {'units': [{'unit': 'u%d' % i, 'sku': 'G%08x' % i, 'grade': 'A', 'cost': 1000} for i in range(300)]}
    r = run({'_inventory': many, '_now': now}, kit)                      # more SKUs than fit: cut, and said so
    assert len(json.dumps(r, ensure_ascii=False)) < 5000 and r['units'] == 300 and r['more_skus'] == 300 - len(r['skus']) > 0, r['more_skus']
    # review af47eb54 C1: one-letter staples by name; a miss says how to look again
    shelf = {'units': [{'unit': 'r1', 'sku': 'G0000aaaa', 'grade': 'B', 'cost': 59000, 'name': '[안동농협] 백진주쌀 10kg'},
                       {'unit': 'n1', 'sku': 'G0000bbbb', 'grade': 'A', 'cost': 2000, 'name': '오뚜기 옛날 소면 900g'},
                       {'unit': 's1', 'sku': 'G0000cccc', 'grade': 'A', 'cost': 2000, 'name': '무농약 콩나물 500g', 'expires_at': 0}]}
    assert [i['unit'] for i in run({'sku': '쌀', '_inventory': shelf, '_now': now}, kit)['items']] == ['r1']
    assert [i['unit'] for i in run({'sku': '면', '_inventory': shelf, '_now': now}, kit)['items']] == ['n1']
    miss = run({'sku': '무', '_inventory': shelf, '_now': now}, kit)                  # 무농약 is not 무
    assert miss['count'] == 0 and 'args {}' in miss['note'], miss
    assert run({'_inventory': shelf, '_now': now}, kit)['units'] == 3              # expires_at 0: no date, not expired
    # q8 11/26: the customer's '2L×6병' is the shelf's '2Lx6병(1팩)'; a near name is offered, never counted as a match
    water = {'units': [{'unit': 'w%d' % i, 'sku': 'G00b8acf0', 'grade': 'B', 'cost': 2500,
                        'name': '[본사공식] 몽베스트 무라벨 생수 2Lx6병(1팩)'} for i in range(3)]
             + [{'unit': 'm1', 'sku': 'GMILK', 'grade': 'B', 'cost': 9000, 'name': '소화가잘되는우유 오리지널 190ML 24팩'}]}
    for ask in ('몽베스트 무라벨 생수 2L×6병', '몽베스트 무라벨 생수 2L x 6병', '몽베스트 무라벨 생수 2L*6병', '생수 2L 6병'):
        assert run({'sku': ask, '_inventory': water, '_now': now}, kit)['count'] == 3, ask
    near = run({'sku': '몽베스트 생수 2L 12병', '_inventory': water, '_now': now}, kit)
    assert near['count'] == 0 and near['similar'][0]['sku'] == 'G00b8acf0' and near['similar'][0]['count'] == 3, near
    assert '품절' in near['note'] and 'similar' in near['note'], near
    none = run({'sku': '샘표 쌀소면 400g x 3개', '_inventory': water, '_now': now}, kit)
    assert none['count'] == 0 and 'similar' not in none and 'args {}' in none['note'], none
    milk = run({'sku': '우유 190ml 24팩', '_inventory': water, '_now': now}, kit)     # words apart: offered, not matched
    assert milk['count'] == 0 and [s['sku'] for s in milk['similar']] == ['GMILK'], milk
    assert run({'sku': '400g x 3', '_inventory': {'units': [{'unit': 'a', 'sku': 'S', 'grade': 'A', 'cost': 1,
                'name': '샘표 쌀소면 400g x 3개'}]}, '_now': now}, kit)['count'] == 1
    cola = {'units': [{'unit': 'c1', 'sku': 'PEP', 'grade': 'A', 'cost': 1, 'name': '펩시콜라 제로슈거 MAX 2'}]}
    assert run({'sku': 'max', '_inventory': cola, '_now': now}, kit)['count'] == 1           # review I4: x of a word stays
    assert run({'sku': '생수 2L 6병', '_inventory': {'units': [{'unit': 'f', 'sku': 'FW', 'grade': 'A', 'cost': 1,
               'name': '생수 2Lｘ6병'}]}, '_now': now}, kit)['count'] == 1                   # full-width x (review N1)
    # odd market answers never raise and never grow past what the agent can read
    odd = {'restock': {'open': True, 'x': 'y' * 9000}, 'units': [
        {'unit': 'u' * 5000, 'sku': 'G0000dddd', 'grade': 'A' * 300, 'cost': '12,000원', 'name': '이상한 값', 'list_price': 'z' * 9000},
        {'unit': 'u2', 'sku': 'G0000dddd', 'grade': None, 'cost': None, 'name': None, 'expires_at': True}]}
    for a in ({}, {'sku': 'G0000dddd'}):
        r = run(dict(a, _inventory=odd, _now=now), kit)
        assert r['ok'] and len(json.dumps(r, ensure_ascii=False)) < 5000, (a, len(json.dumps(r, ensure_ascii=False)))
    assert run({'sku': 'g', '_inventory': shelf, '_now': now}, kit)['count'] == 0     # one letter: Korean only (not "900g")
    huge = {'balance': 10 ** 400, 'restock': {'open': True, 'opens_at': float('inf')},
            'units': [{'unit': 'u', 'sku': 'G0000dddd', 'grade': 'A', 'cost': 10 ** 400, 'name': 'x'}]}
    r = run({'_inventory': huge, '_now': now}, kit)
    assert r['ok'] and r['units'] == 1 and r['restock'] == {'open': True}, r
    # one shown price per SKU and grade (operator 11/26 12:40): set once, kept in grade order, shown everywhere
    os.makedirs('logs', exist_ok=True)
    try:
        rice = {'units': [{'unit': 'k1', 'sku': 'R5', 'grade': 'C', 'cost': 9000, 'name': '쌀 5kg'},
                          {'unit': 'k2', 'sku': 'R5', 'grade': 'B', 'cost': 9500, 'name': '쌀 5kg'},
                          {'unit': 'k3', 'sku': 'R5', 'grade': 'B', 'cost': 12000, 'name': '쌀 5kg'}]}
        r0 = run({'sku': 'R5', '_inventory': rice, '_now': now}, kit)
        assert all(i['price'] is None for i in r0['items']) and 'quote_error' not in r0, r0
        r1 = run({'sku': 'R5', 'quote': {'C': 13740, 'B': 13000}, '_inventory': rice, '_now': now}, kit)
        assert 'quote_error' not in r1 and r1['quoted_sku'] == 'R5', r1           # review r1 I3
        pr = {i['grade']: i['price'] for i in r1['items']}
        assert pr == {'C': 13740, 'B': 13740}, pr                         # B never below C (식혜 C 9,000 > B 8,900)
        r2 = run({'sku': 'R5', 'quote': {'C': 17175}, '_inventory': rice, '_now': now}, kit)
        assert {i['grade']: i['price'] for i in r2['items']}['C'] == 13740, r2   # first price stays (9,004→13,740→17,175)
        assert r2['quote_kept'] == {'C': 13740} and 'requote' in r2['quote_kept_note'], r2   # r2 M-5: said, not silent
        r3 = run({'sku': 'R5', 'quote': {'B': 15000}, 'requote': True, '_inventory': rice, '_now': now}, kit)
        assert {i['grade']: i['price'] for i in r3['items']} == {'C': 13740, 'B': 15000}, r3
        whole = run({'_inventory': rice, '_now': now}, kit)
        g = whole['skus'][0]['grades']
        assert g['C']['price'] == 13740 and g['B']['price'] == 15000 and g['B']['floor'] == 12541, g
        low = run({'sku': 'R5', 'quote': {'X': 1}, '_inventory': rice, '_now': now}, kit)
        assert low['ok'] and 'quote_error' in low, low                    # unknown grade: said, stock still shown
        fl = run({'sku': 'R5', 'quote': {'C': 100}, 'requote': True, '_inventory': rice, '_now': now + 1}, kit)
        assert {i['grade']: i['price'] for i in fl['items']}['C'] == 9511, fl     # never below the grade floor
        two = {'units': rice['units'] + [{'unit': 'k9', 'sku': 'R10', 'grade': 'B', 'cost': 9000, 'name': '쌀 10kg'}]}
        amb = run({'sku': '쌀', 'quote': {'B': 20000}, '_inventory': two, '_now': now + 2}, kit)
        assert 'R10' in amb['quote_error'] and 'quoted_sku' not in amb, amb         # review r1 M9: no silent pick
        # 떨이 11/26 19:05: r = hours left / min(shelf life, 48 h); r <= 0.5 on clearance, r <= 0.25 may go under cost
        egg = {'kind': 'food', 'category': '식품>계란/유제품>계란', 'list_price': 12900, 'name': '계란 20구', 'sku': 'EG'}
        cl = {'units': [dict(egg, unit='e1', grade='C', cost=5000, expires_at=now + 6 * 3600),
                        dict(egg, unit='e2', grade='C', cost=5000, expires_at=now + 2 * 3600),
                        dict(egg, unit='e3', grade='A', cost=7000, expires_at=now + 47 * 3600),
                        dict(egg, unit='w1', sku='G00b8acf0', name='몽베스트 생수 2Lx6병', grade='C', cost=2500,
                             category='식품>음료>주스/코코아/생수', expires_at=now + 3600)]}
        one = run({'sku': 'EG', '_inventory': cl, '_now': now}, kit)
        by = {i['unit']: i.get('clearance') for i in one['items']}
        assert by['e2'] == {'price': 4190, 'floor': 1261} and by['e1'] == {'price': 4840, 'floor': 2945}, by
        assert by['e3'] is None and '떨이' in one['note_clearance'], one   # 47 h of 48: not on clearance
        whole = run({'_inventory': cl, '_now': now}, kit)
        assert whole['clearance'] == ['EG C 4190/1261 2.0h'], whole   # soonest unit per grade; no water
        assert list(whole)[:6] == ['ok', 'balance', 'restock', 'units', 'expired', 'clearance'], list(whole)
        assert 'clearance' not in run({'_inventory': rice, '_now': now}, kit)
        with open(QF) as f:
            assert '"EG|' not in f.read()                                  # never stored as the shown price
    finally:
        if os.path.exists(QF):
            os.remove(QF)
    bad = run({'_inventory': 'not a dict'}, kit)
    assert bad['ok'] is False and '재고 0이 아님' in bad['error'], bad
    return 'ok'
