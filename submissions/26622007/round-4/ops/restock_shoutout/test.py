import os


def test(run, kit):
    """The whole path in test mode: sold-out request, purchases, restock, one forum text, notices, caps, opt-out."""
    for f in ('shoutout.test.json', 'shoutout.test.json.tmp', 'waitlist.test.json', 'waitlist.test.json.tmp',
              'logs/quotes.test.json'):
        if os.path.exists(f):
            os.remove(f)
    os.makedirs('logs', exist_ok=True)
    t0 = 1_800_000_000
    milk = {'unit': 'u1', 'sku': 'MILK', 'name': '[서울우유] 흰우유 1L 24팩', 'kind': 'food', 'cost': 20000, 'grade': 'A'}
    rice = {'unit': 'u2', 'sku': 'RICE', 'name': '백진주쌀 20kg', 'kind': 'food', 'cost': 59000, 'grade': 'B'}
    empty = {'units': [], 'restock': {'open': False}}
    both = {'units': [milk, dict(milk, unit='u3', grade='B'), rice], 'restock': {'open': True, 'opens_at': t0}}
    sales = {'o1': {'order': 'o1', 'tx_hash': '0x1'}, 'o2': {'order': 'o2', 'tx_hash': '0x2'}, 'o3': {'order': 'o3', 'tx_hash': '0x3'}}
    jin = "a customer that says it is '진주댁'"
    try:
        assert run({'op': 'want', 'customer': 'a person in your app', 'sku': 'MILK', '_inventory': empty}, kit)['anonymous']
        r = run({'op': 'want', 'customer': jin, 'sku': 'MILK', 'qty': 2, 'price': 15000, '_inventory': empty, '_now': t0}, kit)
        assert r['ok'] and r['ask_contact'] and '포럼에서 쓰시는 이름' in r['say'] and '들어오면 먼저 알려 드릴게요' in r['say'] and '연락처(' not in r['say'], r
        bad = run({'op': 'contact', 'customer': jin, 'sku': 'MILK', 'contact': '010-1234-5678', '_inventory': empty, '_now': t0}, kit)
        assert not bad['ok'] and not bad['saved'] and '010' not in str(bad), bad      # out-of-market contact: not stored, not echoed
        r = run({'op': 'contact', 'customer': jin, 'sku': 'MILK', 'contact': '@우유사랑-1a2b', '_inventory': empty, '_now': t0}, kit)
        assert r['ok'] and r['saved'] and '우유사랑' not in str(r), r
        with open('waitlist.test.json', encoding='utf-8') as f:
            saved = f.read()
        with open('shoutout.test.json', encoding='utf-8') as f:
            assert '우유사랑-1a2b' in saved and '우유사랑' not in f.read()                # one file holds the contact
        r = run({'op': 'want', 'customer': '박씨', 'sku': 'RICE', '_inventory': both, '_now': t0}, kit)
        assert r['in_stock'] == 1 and not r['ok']                     # in stock: no sold-out record
        assert not run({'op': 'bought', 'customer': '김씨', 'sku': 'MILK', 'order': 'nope', '_sales': sales}, kit)['ok']
        for o in ('o1', 'o2', 'o2'):
            r = run({'op': 'bought', 'customer': '김씨', 'sku': 'MILK', 'order': o, '_sales': sales, '_now': t0}, kit)
        assert r['count'] == 2 and r['duplicate']                    # the same order counts once
        run({'op': 'bought', 'customer': '최씨', 'sku': 'MILK', 'order': 'o3', '_sales': sales, '_now': t0}, kit)
        assert not run({'op': 'restocked', 'sku': 'EGG', 'price': 9000, '_inventory': both, '_now': t0}, kit)['ok']
        assert run({'op': 'restocked', 'sku': 'MILK', 'price': 100, '_inventory': both, '_now': t0}, kit)['floor'] > 100
        r = run({'op': 'restocked', 'sku': 'MILK', 'price': 26000, '_inventory': both, '_now': t0 + 10}, kit)
        got = {p['customer']: p['why'] for p in r['picked']}
        assert got == {'진주댁': 'waitlist', '김씨': 'regular'}, got    # 최씨 bought once: not a regular
        assert r['picked'][0]['customer'] == '진주댁' and '우유사랑' not in str(r), r   # the waiting list goes first
        r = run({'op': 'restocked', 'sku': 'MILK', 'price': 26000, '_inventory': both, '_now': t0 + 20}, kit)
        assert r['picked'] == [] and r['skipped'].get('김씨') == 'already_this_restock'
        run({'op': 'restocked', 'sku': 'RICE', 'price': 78000, '_inventory': both, '_now': t0 + 30}, kit)
        a = run({'op': 'announce', '_inventory': both, '_forum': [], '_now': t0 + 40}, kit)
        named = bool(getattr(run, '__globals__', {}).get('NAME_REGULARS'))
        assert a['ok'] and '남겨 주신 연락처로 먼저' in a['text'] and '우유사랑' not in a['text'] and ('김씨' in a['text']) == named and '쌀20kg' in a['text'], a  # regulars named only when NAME_REGULARS
        n = run({'op': 'notice', 'customer': jin, '_inventory': both, '_now': t0 + 50}, kit)['notice']
        assert '2개' in n and 'A·B등급' in n and '26,000' in n and '그만' in n, n
        with open('waitlist.test.json', encoding='utf-8') as f:
            assert '우유사랑' not in f.read()                                          # told once: the entry is deleted
        again = run({'op': 'contact', 'customer': jin, 'sku': 'MILK', 'contact': '우유사랑-1a2b', '_inventory': empty, '_now': t0 + 55}, kit)
        assert again.get('already_notified') and not again['ok'], again                # one notice per customer and item
        assert run({'op': 'notice', 'customer': jin, '_inventory': both, '_now': t0 + 60}, kit)['notice'] is None
        assert run({'op': 'notice', 'customer': '김씨', '_inventory': empty, '_now': t0 + 60}, kit)['notice'] is None  # sold out: say nothing
        assert run({'op': 'opt_out', 'customer': '김씨', '_now': t0 + 70}, kit)['opt_out']
        later = dict(both, restock={'open': True, 'opens_at': t0 + 7200})
        r = run({'op': 'restocked', 'sku': 'MILK', 'price': 26000, '_inventory': later, '_now': t0 + 7200}, kit)
        assert r['skipped'].get('김씨') == 'opt_out'
        bad = [{'author': kit.name, 'ts': t0 + 7000, 'dislikes': 3}]
        assert not run({'op': 'announce', '_inventory': later, '_forum': bad, '_now': t0 + 7210}, kit)['ok']
        # each grade's floor is its own cost + gas + fee (react.jsonl 6069: coffee C 5,500 / A 9,000 was refused by
        # the A unit's 7,238 and went up as 10,000 for both); one '개당' price still has to clear every grade
        t1 = t0 + 100000                               # after the 24 h dislike pause above
        coffee = {'unit': 'u9', 'sku': 'COFFEE', 'name': 'UCC 하와이 코나 커피 337ml 12개입', 'kind': 'food', 'cost': 6750, 'grade': 'A'}
        tea = {'unit': 'u11', 'sku': 'TEA', 'name': '동서 둥굴레차 50T 60g', 'kind': 'food', 'cost': 3032, 'grade': 'A'}
        cup = {'units': [coffee, dict(coffee, unit='u10', cost=3545, grade='C'), tea, dict(tea, unit='u12', cost=2577),
                         dict(tea, unit='u13', cost=1300, grade='C')], 'restock': {'open': True, 'opens_at': t1}}
        run({'op': 'want', 'customer': '이씨', 'sku': 'COFFEE', '_inventory': empty, '_now': t1 - 100}, kit)
        low = run({'op': 'restocked', 'sku': 'COFFEE', 'prices': {'C': 3999, 'A': 9000}, '_inventory': cup, '_now': t1}, kit)
        assert not low['ok'] and low['floors'] == {'A': 7238, 'C': 4000}, low       # C under its own floor
        part = run({'op': 'restocked', 'sku': 'COFFEE', 'price': 5500, 'prices': {'C': 5500}, '_inventory': cup, '_now': t1}, kit)
        assert not part['ok'] and part['floor'] == 7238, part                       # A has no price: '개당 5,500' would sell A under
        assert not run({'op': 'restocked', 'sku': 'COFFEE', 'prices': {'C': 5500, 'A': 9000, 'B': 1}, '_inventory': cup,
                        '_now': t1}, kit)['ok']                                    # a grade not in stock: the highest floor, as before
        r = run({'op': 'restocked', 'sku': 'COFFEE', 'prices': {'C': 5500, 'A': 9000}, '_inventory': cup, '_now': t1 + 1}, kit)
        assert r['ok'] and [p['customer'] for p in r['picked']] == ['이씨'], r
        t = run({'op': 'restocked', 'sku': 'TEA', 'prices': {'A': 3481, 'C': 2000}, '_inventory': cup, '_now': t1 + 2}, kit)
        assert not t['ok'] and t['floors'] == {'A': 3482, 'C': 1733}, t           # the dearest unit of a grade sets its floor
        assert run({'op': 'restocked', 'sku': 'TEA', 'price': 1733, 'prices': {'A': 3482, 'C': 1733}, '_inventory': cup,
                    '_now': t1 + 3}, kit)['ok']
        # one shown price per grade (operator 11/26 12:40): a later restock keeps the price customers were already told
        again = run({'op': 'restocked', 'sku': 'COFFEE', 'prices': {'C': 6000, 'A': 9500}, '_inventory': cup, '_now': t1 + 4}, kit)
        assert again['ok'] and again['prices'] == {'A': 9000, 'C': 5500} and '5,500' in again['quote_note'], again
        bare = run({'op': 'restocked', 'sku': 'COFFEE', '_inventory': cup, '_now': t1 + 5}, kit)
        assert bare['ok'] and bare['prices'] == {'A': 9000, 'C': 5500} and 'quote_note' not in bare, bare   # nothing given
        half = {'units': [coffee, dict(coffee, unit='u15', cost=3545, grade='C')], 'restock': cup['restock']}
        hq = run({'op': 'restocked', 'sku': 'COFFEE', 'prices': {'C': 6000}, '_inventory': half, '_now': t1 + 7}, kit)
        assert hq['ok'] and hq['prices'] == {'A': 9000, 'C': 5500}, hq             # r2 I-1: C given, A shown: no refusal
        a = run({'op': 'announce', '_inventory': cup, '_forum': [], '_now': t1 + 10}, kit)
        assert a['ok'] and 'A 9,000·C 5,500크레딧' in a['text'] and 'A 3,482·C 1,733크레딧' in a['text'], a
        n = run({'op': 'notice', 'customer': '이씨', '_inventory': cup, '_now': t1 + 20}, kit)['notice']
        assert 'A 9,000·C 5,500크레딧' in n, n
        dear = dict(cup, units=cup['units'] + [dict(coffee, unit='u14', cost=8000, grade='C')])   # a dearer C unit came in
        up = run({'op': 'restocked', 'sku': 'COFFEE', '_inventory': dear, '_now': t1 + 30}, kit)
        assert up['ok'] and up['prices'] == {'A': 9000, 'C': 8500}, up            # stored C raised to today's floor (r1 I5)
        # cash-out (11/27): a cash-out C quote under the C floor (12,778) does not block a B restock (review I3)
        t2 = t1 + 40
        gm = {'unit': 'u20', 'sku': 'GM', 'name': '서울우유 멸균우유 190ml 24팩', 'kind': 'food', 'cost': 12235, 'grade': 'C',
              'expires_at': t2 + 29 * 3600}
        gmi = {'units': [gm, dict(gm, unit='u21', grade='B', expires_at=t2 + 200 * 3600)], 'restock': cup['restock']}
        import json
        with open('logs/quotes.test.json', encoding='utf-8') as f:
            q = json.load(f)
        q['GM|C'] = {'price': 11787, 'ts': t2, 'by': 'cashout', 'floor': 11787}
        with open('logs/quotes.test.json', 'w', encoding='utf-8') as f:
            json.dump(q, f)
        c = run({'op': 'restocked', 'sku': 'GM', 'prices': {'B': 13000}, '_inventory': gmi, '_now': t2 + 1}, kit)
        assert c['ok'] and c['prices'] == {'B': 13000, 'C': 11787}, c
        return 'ok'
    finally:
        for f in ('shoutout.test.json', 'waitlist.test.json', 'logs/quotes.test.json'):
            if os.path.exists(f):
                os.remove(f)
