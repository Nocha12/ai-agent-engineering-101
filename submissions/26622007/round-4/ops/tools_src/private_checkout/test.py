import hashlib
import json
import os


def logic_digest(jwt):
    return hashlib.sha256(jwt.encode()).hexdigest()

FILES = ('logs/checkouts.test.jsonl', 'logs/sales.test.jsonl', 'logs/refunds.test.jsonl', 'logs/quotes.test.json')


def test(run, kit):
    for f in FILES:
        if os.path.exists(f):
            os.remove(f)
    os.makedirs('logs', exist_ok=True)
    shop = kit.address('shop')
    unit = {'unit': 'u-t1', 'sku': 'G1', 'name': '쌀 20kg', 'kind': 'food', 'cost': 59634, 'grade': 'B', 'expires_at': None,
            'cert': {'unit': 'u-t1', 'grade': 'B'}}
    inv = {'wallet': shop, 'units': [unit]}
    a = {'op': 'create', 'unit': 'u-t1', 'price': 78675, 'task': 't', 'warranty_hours': 0, 'claim': 'B', 'order': 'o-t1',
         'inquiry_task': 't', '_inventory': inv, '_registered': shop}
    try:
        assert '재고' in run(dict(a, unit='u-gone'), kit)['error']
        assert '지갑' in run(dict(a, _inventory=dict(inv, wallet='0x' + '9' * 40)), kit)['error']
        assert '지갑' in run(dict(a, _registered='0x' + '9' * 40), kit)['error']
        assert '등급' in run(dict(a, claim='A'), kit)['error']
        assert '등급' in run(dict(a, _inventory=dict(inv, units=[dict(unit, cert={'grade': 'A'})])), kit)['error']
        assert run(dict(a, price=60000), kit)['floor'] == 60656                 # ceil((59634 + 205 + 210) / 0.99)
        assert not run(dict(a, warranty_hours=73), kit)['ok']
        r = run(a, kit)
        assert r['ok'] and r['payload']['private'] is True and r['payload']['claim'] == 'B', r
        assert '잡혀' in run(dict(a, order='o-t2', inquiry_task='t-other'), kit)['error']   # one open checkout per unit
        with open(FILES[0]) as f:
            assert '"holder":"t"' in f.read()                                  # issued rows name the inquiry
        same = run(dict(a, order='o-t1b'), kit)                                # same task (customer) reissues
        assert same['ok'] and same['payload']['unit'] == 'u-t1', same            # o-t1b, or o-t1 again within the second
        assert same['order'] == ('o-t1' if same.get('reused') else 'o-t1b'), same
        w = run(dict(a, order='o-t1c', task='쌀 다시', inquiry_task='t'), kit)      # wrapper names the inquiry
        assert w['ok'] and w['payload']['task'] == '쌀 다시' and 'inquiry_task' not in w['payload'], w
        assert run(dict(a, order='o-t1d', task='t', inquiry_task='t-z'), kit).get('code') == 'unit_held'
        # 11/27: one inquiry, a second unit of the same SKU and grade within 30 s may be a double order (930158ae
        # 11/26 20:40): issued, and flagged (duplicate_of) so the reply keeps one unless the customer wants both
        ud = dict(unit, unit='u-d2', cert={'unit': 'u-d2', 'grade': 'B'})
        ud3 = dict(unit, unit='u-d3', cert={'unit': 'u-d3', 'grade': 'B'})
        ud4 = dict(unit, unit='u-d4', cert={'unit': 'u-d4', 'grade': 'B'})
        ue = dict(unit, unit='u-e1', sku='G7', cert={'unit': 'u-e1', 'grade': 'B'})
        uc = dict(unit, unit='u-c3', grade='C', cert={'unit': 'u-c3', 'grade': 'C'})
        dinv = dict(inv, units=[unit, ud, ud3, ud4, ue, uc])
        dd = run(dict(a, unit='u-d2', order='t-2', _inventory=dinv), kit)
        assert dd['ok'] and dd['duplicate_of'] in ('o-t1', 'o-t1b', 'o-t1c'), dd
        de = run(dict(a, unit='u-e1', order='t-3', _inventory=dinv), kit)                 # another SKU
        assert de['ok'] and 'duplicate_of' not in de, de
        do = run(dict(a, unit='u-d3', order='t-4', inquiry_task='t-dd', _inventory=dinv), kit)   # another inquiry
        assert do['ok'] and 'duplicate_of' not in do, do
        dc = run(dict(a, unit='u-c3', claim='C', order='t-5', _inventory=dinv), kit)      # B -> C: a change (review I3)
        assert dc['ok'] and 'duplicate_of' not in dc, dc
        # a checkout dropped from its reply (delivery_waived) or given up as undelivered no longer counts (review I3)
        log = FILES[0]
        rows = [json.loads(x) for x in open(log, encoding='utf-8') if x.strip()]
        live = [x for x in rows if x.get('event') == 'issued' and x.get('holder') == 't' and x.get('grade') == 'B']
        with open(log, 'a', encoding='utf-8') as f:
            for i, x in enumerate(live):
                ev = ('delivery_waived', 'delivery_overdue')[i % 2]
                f.write(json.dumps({'event': ev, 'order': x['order'], 'digest_hex': x['digest_hex'], 'ts': x['ts']}) + '\n')
        d4 = run(dict(a, unit='u-d4', order='t-6', _inventory=dinv), kit)
        assert d4['ok'] and 'duplicate_of' not in d4, d4
        un = dict(unit, unit='u-n', cert={'unit': 'u-n', 'grade': 'B'})
        n0 = dict(a, unit='u-n', order='o-n0', task='쌀 1개', inquiry_task=None, _inventory=dict(inv, units=[un]))
        assert run(n0, kit)['ok']                                               # no wrapper: no holder,
        nx = run(dict(n0, order='o-n1'), kit)                                   # so the same task text is not spared
        assert nx.get('code') == 'unit_held', nx
        held = run(dict(a, order='o-t2b', inquiry_task='t-other'), kit)
        assert held.get('code') == 'unit_held' and '다른 손님' not in held['error'], held
        assert held['alternatives'] == [] and held['held_until'] >= r['payload']['iat'] + 300, held
        u2 = dict(unit, unit='u-t2', cert={'unit': 'u-t2', 'grade': 'B'})
        r2 = run(dict(a, order='o-t3', inquiry_task='t-other2', _inventory=dict(inv, units=[unit, u2])), kit)
        assert r2['ok'] and r2['payload']['unit'] == 'u-t2', r2                # held unit gives way to the same SKU/grade
        u3 = dict(u2, unit='u-t3', grade='C', cert={'unit': 'u-t3', 'grade': 'C'}, cost=30000)
        u4 = dict(u2, unit='u-t4', cost=80000, cert={'unit': 'u-t4', 'grade': 'B'})       # same grade, floor above price
        u5 = dict(u2, unit='u-t5', sku='G9', cert={'unit': 'u-t5', 'grade': 'B'})         # other SKU: never offered
        x = run(dict(a, order='o-t4', inquiry_task='t-x', _inventory=dict(inv, units=[unit, u2, u3, u4, u5])), kit)
        assert x.get('code') == 'unit_held' and '다른 손님' not in x['error'], x  # u-t2 is held by o-t3 now
        alts = x['alternatives']
        assert [y['unit'] for y in alts] == ['u-t3', 'u-t4'], alts              # cheapest floor first, same SKU only
        assert alts[0] == {'unit': 'u-t3', 'grade': 'C', 'floor': 30723, 'hours_left': None}, alts
        assert alts[1]['grade'] == 'B' and alts[1]['floor'] == 81228, alts
        assert x['cheapest'] == alts[0], x
        clock = run.__globals__['time']                                        # freeze the tool's clock below
        run.__globals__['time'] = type('T', (), {'time': staticmethod(lambda: 1795650000.0)})
        try:
            T = 1795650000
            ud = dict(unit, unit='u-d', cert={'unit': 'u-d', 'grade': 'B'})
            d0 = dict(a, unit='u-d', order='o-d1', inquiry_task='h-d', _inventory=dict(inv, units=[ud]))
            first = run(d0, kit)
            again = run(dict(d0, order='o-d2'), kit)                            # same terms, same second: same JWT
            assert again['ok'] and again['order'] == 'o-d1' and again.get('reused'), again
            assert again['checkout_jwt'] == first['checkout_jwt'], again
            with open(FILES[0]) as f:
                assert f.read().count(logic_digest(first['checkout_jwt'])) == 1   # one JWT, one order
            with open(FILES[0], 'a') as f:
                f.write('{"event":"issued","ts":%d,"order":"o-old","unit":"u-old","digest_hex":"x1"}\n' % (T - 10))
                for i, back in enumerate((1000, 760, 520, 280, 40)):            # h-c reissued u-c back to back
                    f.write('{"event":"issued","ts":%d,"order":"o-c%d","unit":"u-c","digest_hex":"c%d","holder":"h-c"}\n'
                            % (T - back, i, i))
                f.write('{"event":"issued","ts":%d,"order":"o-e","unit":"u-e","digest_hex":"e1","holder":"h-e"}\n' % (T - 40))
            uo, uc, ue = (dict(unit, unit=n, cert={'unit': n, 'grade': 'B'}) for n in ('u-old', 'u-c', 'u-e'))
            many = dict(inv, units=[uo, uc, ue])
            old = run(dict(a, unit='u-old', order='o-n1', inquiry_task='h-new', _inventory=many), kit)
            assert old.get('code') == 'unit_held', old                          # a row without holder still holds
            w = run(dict(a, unit='u-c', order='o-n2', inquiry_task='h-c', _inventory=many), kit)
            assert w.get('code') == 'reissue_wait' and w['held_until'] == T - 40 + 300, w
            e = run(dict(a, unit='u-e', order='o-n3', inquiry_task='h-e', _inventory=many), kit)
            assert e['ok'] and e['payload']['unit'] == 'u-e', e                 # a short own chain is spared
        finally:
            run.__globals__['time'] = clock
        assert 'grade_evidence' not in r, r                                      # a certificate with its grade needs no note
        gcert = {'cert': {'unit': 'u-0a0b0c0d0e0f', 'sku': 'G1', 'kind': 'food', 'supplied_at': 1795600000, 'expires_at': 4102444800,
                          'grade': None}, 'sig': 's'}                           # the market's food certificate: grade null
        ug = dict(unit, unit='u-0a0b0c0d0e0f', expires_at=4102444800, cert=gcert)
        g1 = run(dict(a, unit='u-0a0b0c0d0e0f', order='o-g1', inquiry_task='h-g', _inventory=dict(inv, units=[ug])), kit)
        assert g1['ok'] and g1['grade_evidence'] == {'unit': 'u-0a0b0c0d0e0f', 'claim': 'B', 'supplied_at': 1795600000,
                                                     'expires_at': 4102444800}, g1
        assert 'grade_evidence' not in g1['payload'] and g1['payload']['cert'] == gcert, g1   # signed checkout unchanged
        ux = dict(ug, unit='u-0a0b0c0d0e1f', cert=dict(gcert, cert=dict(gcert['cert'], unit='u-other')))
        gx = run(dict(a, unit='u-0a0b0c0d0e1f', order='o-x1', inquiry_task='h-x', _inventory=dict(inv, units=[ux])), kit)
        assert gx['ok'] and 'grade_evidence' not in gx, gx                      # a certificate of another unit proves nothing
        for n, bad in enumerate(({'expires_at': 10 ** 400}, {'expires_at': 1.7e12}, {'kind': 'goods'}, {'grade': 'B'})):
            ub = dict(ug, unit='u-0a0b0c0d0e2%d' % n, cert=dict(gcert, cert=dict(gcert['cert'], unit='u-0a0b0c0d0e2%d' % n, **bad)))
            gb = run(dict(a, unit=ub['unit'], order='o-b%d' % n, inquiry_task='h-b%d' % n, _inventory=dict(inv, units=[ub])), kit)
            assert gb['ok'] and 'grade_evidence' not in gb, (bad, gb)            # odd certificates: no note, checkout still goes
        assert r.get('quote') == 78675 and 'quote_note' not in r, r                # first price became the shown price
        uq = [dict(unit, unit='u-q%d' % i, cert={'unit': 'u-q%d' % i, 'grade': 'B'}) for i in (1, 2)]
        uc = dict(unit, unit='u-q3', grade='C', cost=30000, cert={'unit': 'u-q3', 'grade': 'C'})
        ug = dict(unit, unit='u-q4', sku='G2', cert={'unit': 'u-q4', 'grade': 'B'})
        qi = dict(inv, units=uq + [uc, ug])
        q = dict(a, _inventory=qi, inquiry_task='t-q', task='t-q')
        q.pop('price')
        nq = run(dict(q, unit='u-q4', order='o-q4'), kit)
        assert nq.get('code') == 'no_quote' and not nq['ok'], nq                # no shown price yet, no guess
        p1 = run(dict(q, unit='u-q1', order='o-q1'), kit)
        assert p1['ok'] and p1['payload']['price'] == 78675 and p1['quote'] == 78675, p1   # price left out: shown price
        p2 = run(dict(q, unit='u-q2', order='o-q2', inquiry_task='t-q2', task='t-q2', price=80000), kit)
        assert p2['ok'] and p2['payload']['price'] == 80000, p2                  # a differing price is not refused
        assert p2['quote'] == 78675 and '78,675' in p2['quote_note'], p2
        p3 = run(dict(q, unit='u-q3', claim='C', order='o-q3', inquiry_task='t-q3', task='t-q3', price=90000), kit)
        assert p3['ok'] and p3['quote'] == 78675 and 'quote_note' in p3, p3      # C never shown above B
        clock = run.__globals__['time']                                        # clearance (떨이 11/26 19:05)
        run.__globals__['time'] = type('T', (), {'time': staticmethod(lambda: 1795650000.0)})
        try:
            T = 1795650000
            uk = {'unit': 'u-k1', 'sku': 'G7', 'name': '계란 20구', 'kind': 'food', 'category': '식품>계란/유제품>계란',
                  'list_price': 12900, 'cost': 5000, 'grade': 'C', 'supplied_at': T - 3600, 'expires_at': T + 6 * 3600,
                  'cert': {'unit': 'u-k1', 'grade': None}}
            uw = dict(uk, unit='u-k2', sku='G00b8acf0', name='몽베스트 생수 2Lx6병', cert={'unit': 'u-k2', 'grade': None})
            uf = dict(uk, unit='u-k3', sku='G8', expires_at=T + 40 * 3600, cert={'unit': 'u-k3', 'grade': None})
            ki = dict(inv, units=[uk, uw, uf])
            k = dict(a, _inventory=ki, claim='C', inquiry_task='t-k', task='t-k')
            k.pop('price')
            c1 = run(dict(k, unit='u-k1', order='o-k1'), kit)                  # r = 6 / min(96, 48) = 0.125
            assert c1['ok'] and c1['payload']['price'] == 4840, c1              # 0.6 x 12,900 x (0.5 + 0.125)
            assert c1['clearance'] == dict(c1['clearance'], price=4840, floor=2945, hours_left=6.0, loss=True), c1
            assert '떨이' in c1['clearance']['note'] and 'quote_note' not in c1, c1
            with open(FILES[3]) as f:
                assert 'G7|C' not in f.read()                                    # a clearance price is not the shown price
            lo = run(dict(k, unit='u-k1', order='o-k2', inquiry_task='t-k2', task='t-k2', price=2900), kit)
            assert not lo['ok'] and lo['floor'] == 2945, lo                     # under cost, not under the clearance floor
            ok = run(dict(k, unit='u-k1', order='o-k3', inquiry_task='t-k', task='t-k', price=2945), kit)
            assert ok['ok'] and ok['payload']['price'] == 2945, ok
            u6 = dict(uk, unit='u-k6', cost=100, expires_at=T + 40 * 3600, cert={'unit': 'u-k6', 'grade': None})
            sw = run(dict(k, unit='u-k1', order='o-k6', inquiry_task='t-k6', task='t-k6', _inventory=dict(ki, units=[uk, u6])), kit)
            assert sw['ok'] and sw['payload']['unit'] == 'u-k6' and 'clearance' not in sw, sw   # held: swapped to a unit
            with open(FILES[3]) as f:                                            # not on clearance; its auto price
                assert 'G7|C' not in f.read()                                    # is never stored as the quote (I1)
            u7 = dict(uk, unit='u-k7', sku='G9', expires_at=T + 19 * 3600, cert={'unit': 'u-k7', 'grade': None})
            u8 = dict(u7, unit='u-k8', expires_at=T + 40 * 3600, cert={'unit': 'u-k8', 'grade': None})
            i78 = dict(ki, units=[u7, u8])
            assert run(dict(k, unit='u-k7', order='o-k7', inquiry_task='t-k7', task='t-k7', _inventory=i78), kit)['ok']
            hp = run(dict(k, unit='u-k7', order='o-k8', inquiry_task='t-k8', task='t-k8', price=6930, _inventory=i78), kit)
            assert hp['ok'] and hp['payload']['unit'] == 'u-k8' and hp['payload']['price'] == 6930, hp
            with open(FILES[3]) as f:
                assert 'G9|C' not in f.read()                                    # a held clearance unit's price, given
                                                                                  # explicitly, is not stored either
            wn = run(dict(k, unit='u-k2', order='o-k4', inquiry_task='t-k4', task='t-k4'), kit)
            assert wn.get('code') == 'no_quote', wn                              # water: never on clearance
            fr = run(dict(k, unit='u-k3', order='o-k5', inquiry_task='t-k5', task='t-k5', price=3000), kit)
            assert not fr['ok'] and fr['floor'] == 5470, fr                      # 40 h of 48 left: the usual floor
        finally:
            run.__globals__['time'] = clock
        mand = {'mandate': {'tx_hash': '0x' + 'a' * 64}}
        s = {'op': 'settle', 'order': 'o-t1', 'checkout_jwt': r['checkout_jwt'], 'checkout_mandate': {}, 'payment_mandate': mand,
             '_inventory': {'wallet': shop, 'units': []}}
        assert '우리가 낸' in run(dict(s, checkout_jwt='x.y.z'), kit)['error']
        assert '다른 주문' in run(dict(s, order='o-other'), kit)['error']
        with open('logs/refunds.test.jsonl', 'w') as f:
            f.write('{"purpose":"refund","order":"o-x","paid_tx":"0x%s","tx_hash":"0x1"}\n' % ('a' * 64))
        rr = run(s, kit); assert '환불' in rr['error'], rr
        os.remove('logs/refunds.test.jsonl')
        # refund owed review r3 (W-20261124-2007): the refund line's paid_tx in other case is the same payment
        with open('logs/refunds.test.jsonl', 'w') as f:
            f.write('{"purpose":"refund","order":"o-x","paid_tx":"0x%s","nonce":4}\n' % ('A' * 64))
        rr = run(s, kit); assert '이미 환불' in rr.get('error', ''), rr
        os.remove('logs/refunds.test.jsonl')
        # a refund_send holds the refunds lock: settle waits, then asks again later, writing nothing
        import fcntl
        with open('logs/refunds.test.jsonl', 'a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            rl = run(dict(s, _lock_wait=0.3), kit)
        assert rl.get('retry') is True and '환불' in rl.get('error', ''), rl
        with open(FILES[0]) as f:
            assert '"unsettled"' not in f.read()
        # settle keeps a shared hold on the lock through its market calls, so refund_send cannot pay in between
        g0 = run.__globals__['sealed_settle']
        probe = []

        def sealed_probe(kit_, args_, order_):
            with open('logs/refunds.test.jsonl', 'a') as other:
                try:
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    probe.append('free')
                except BlockingIOError:
                    probe.append('held')
            return g0(kit_, args_, order_)
        run.__globals__['sealed_settle'] = sealed_probe
        try:
            run(dict(s, _tx_down=True), kit)
        finally:
            run.__globals__['sealed_settle'] = g0
        assert probe == ['held'], probe
        os.remove('logs/refunds.test.jsonl')
        d = run(dict(s, _tx_down=True), kit)
        assert d.get('retry') is True, d                                        # chain down: no record, ask again
        with open(FILES[0]) as f:
            assert '"unsettled"' not in f.read()
        g = run(s, kit)
        assert g.get('refund') is True, g                                       # paid, unit gone: refund path
        with open(FILES[0]) as f:
            assert '"unsettled"' in f.read()
        # refund owed review r6 I1: the unit is held, the POST answer is lost: a settle_sent row before it, no outcome row
        lost = run(dict(s, _inventory={'wallet': shop, 'units': [{'unit': 'u-t1'}]}), kit)
        assert lost.get('status') == 0 and not lost.get('ok'), lost
        with open(FILES[0]) as f:
            last = json.loads([l for l in f if l.strip()][-1])
        assert last.get('event') == 'settle_sent' and last.get('order') == 'o-t1' and last.get('tx_hash') == '0x' + 'a' * 64, last
        # review r7 I1: a retry while that POST has no answer and the unit is gone writes no unit_gone row
        n0 = sum(1 for _ in open(FILES[0]))
        again = run(s, kit)
        assert again.get('retry') is True and '앞선 정산 요청' in again.get('error', ''), again
        assert sum(1 for _ in open(FILES[0])) == n0
        # the unit still held: the retry sends the POST again (a new settle_sent row, then the lost answer again)
        held = run(dict(s, _inventory={'wallet': shop, 'units': [{'unit': 'u-t1'}]}), kit)
        assert held.get('status') == 0, held
        with open(FILES[0]) as f:
            assert sum(1 for l in f if '"settle_sent"' in l) == 2
        # review r9 I1: after SENT_OPEN the lost answer no longer blocks: the unit_gone way (refund) as before
        g1 = run.__globals__['SENT_OPEN']
        run.__globals__['SENT_OPEN'] = -5
        try:
            late = run(s, kit)
        finally:
            run.__globals__['SENT_OPEN'] = g1
        assert late.get('refund') is True, late
        return 'ok'
    finally:
        for f in FILES:
            if os.path.exists(f):
                os.remove(f)
