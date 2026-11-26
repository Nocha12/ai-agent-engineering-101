def test(run, kit):
    import json
    now = 1_800_000_000
    inv = {'balance': 1, 'units': [{'unit': 'u1', 'sku': 'EGG', 'grade': 'B', 'cost': 6000, 'name': '계란 20구'},
                                   {'unit': 'u2', 'sku': 'EGG', 'grade': 'C', 'cost': 5000, 'name': '계란 20구'},
                                   {'unit': 'u3', 'sku': 'EGG', 'grade': 'B', 'cost': 6500, 'name': '계란 20구'},
                                   {'unit': 'u4', 'sku': 'EGG', 'grade': 'B', 'cost': 1, 'name': '계란 20구', 'expires_at': now - 5}]}
    led = [{'kind': 'settle', 'data': {'sku': 'EGG', 'grade': 'B', 'amount': 10000}},
           {'kind': 'settle', 'data': {'sku': 'EGG', 'grade': 'B', 'amount': 10200}},
           {'kind': 'settle', 'data': {'order': 'o-sealed'}}]
    r = run({'_inventory': inv, '_ledger': led, '_now': now}, kit)
    assert r['ok'] and r['units'] == 3 and r['expired'] == 1, r
    assert r['count'] == 1 and r['items'][0]['sku'] == 'EGG' and r['items'][0]['name'] == '계란 20구', r
    g = r['items'][0]['grades']
    assert g['B'] == {'n': 2, 'floor': 6985, 'median_price': 10200, 'settles': 2}, g     # 6985 x 0.99 = 6915.15 >= 6500+415
    assert g['C']['median_price'] is None and g['C']['n'] == 1, g
    # the recent rows, not the oldest: /api/ledger?after=-N starts N rows before the end and gives 500 a call
    recent = run.__globals__['recent_ledger']
    rows = [{'seq': n, 'kind': 'settle', 'data': {}} for n in range(1, 7001)]
    calls = []

    def get(after):
        calls.append(after)
        start = len(rows) + after if after < 0 else after
        return [x for x in rows if x['seq'] > start][:500]
    got = recent(get)
    assert [got[0]['seq'], got[-1]['seq'], len(got)] == [5501, 7000, 1500], (got[0]['seq'], got[-1]['seq'], len(got), calls)
    assert calls[0] == -1500 and len(calls) <= 4, calls
    assert recent(lambda after: {'error': 'x'}) == []                    # an odd answer is no rows, not a crash
    # 60 units stay inside what the kit shows the agent
    units = [{'unit': 'u%d' % i, 'sku': 'G%08x' % (i % 20), 'grade': 'ABC'[i % 3], 'cost': 3000,
              'name': '아주 긴 이름의 먹을거리 %d번 500g 1팩 상온보관' % (i % 20)} for i in range(60)]
    big = run({'_inventory': {'units': units}, '_ledger': led, '_now': now}, kit)
    assert big['units'] == 60 and big['count'] == 20 and len(json.dumps(big, ensure_ascii=False)) < 5500, len(json.dumps(big, ensure_ascii=False))
    many = run({'_inventory': {'units': [{'unit': 'u%d' % i, 'sku': 'G%08x' % i, 'grade': 'A', 'cost': 1} for i in range(300)]}, '_now': now}, kit)
    assert len(json.dumps(many, ensure_ascii=False)) < 5500 and many['more_skus'] == 300 - len(many['items']) > 0, many.get('more_skus')
    odd = run({'_inventory': {'restock': {'x': 'y' * 9000}, 'units': [{'unit': 'u', 'sku': 'G1', 'grade': 'A' * 300, 'cost': '12,000원'},
                                                                       {'unit': 'v', 'sku': 'G1', 'grade': 'B', 'expires_at': 0}]},
               '_ledger': [{'kind': 'settle', 'data': 'not a dict'}, 'x'], '_now': now}, kit)
    assert odd['ok'] and odd['units'] == 2 and odd['ledger_rows'] == 2 and len(json.dumps(odd, ensure_ascii=False)) < 5500, odd
    assert r['ledger_rows'] == 3
    weird = run({'_inventory': {'balance': 10 ** 400, 'units': [{'unit': 'u', 'sku': 'G1', 'grade': 'A', 'cost': 10 ** 400}]},
                 '_ledger': [{'kind': 'settle', 'data': {'sku': ['x'], 'grade': {}, 'amount': 5}}], '_now': now}, kit)
    assert weird['ok'] and weird['units'] == 1, weird
    none = run({'_inventory': inv, '_ledger': {'error': 'closed'}, '_now': now}, kit)
    assert none['ok'] and none['ledger_rows'] == 0 and '장부' in none['note'], none
    return 'ok'
