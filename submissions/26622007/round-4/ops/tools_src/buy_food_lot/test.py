def test(run, kit):
    """Offline: the market and the wallet are fakes; only logs/*.test.jsonl are written, and removed at the end."""
    import os, json
    g = run.__globals__
    assert run({'sku': 'x', 'qty': 0}, kit)['ok'] is False
    assert 'testing mode' in run({'sku': 'x', 'qty': 2}, kit)['error']

    class Wallet:                               # a kit that pays (counted) and writes only the .test files
        testing, test_files = False, True

        def __init__(self, fail=None):
            self.paid, self.fail = [], fail

        def token(self, role): return 't'

        def address(self, role): return '0x' + 'ae' * 20

        def pay(self, to, wei, data, role):
            self.paid.append((to, wei, data))
            if self.fail:
                raise self.fail
            return '0x' + 'ab' * 32

    INV = {'restock': {'open': True}, 'balance': 5000000, 'units': []}
    CAT = [{'sku': 'egg', 'kind': 'food', 'name': '계란 30구', 'category': '계란', 'list_price': 12000}]
    QUOTE = {'quote': 'q1', 'total': 9000, 'goods': 9000, 'storage': 0, 'forum_fees': 0, 'pay': {'to': '0x' + 'cd' * 20, 'wei': str(9000 * 10 ** 15), 'data': '0x' + 'ee' * 32},
             'units': [{'unit': 'u-1'}, {'unit': 'u-2'}]}
    confirms, ledger = [], []

    def fake_getj(c, url, headers=None, params=None):
        if url.endswith('/api/inventory'): return INV
        if url.endswith('/api/catalog'): return CAT
        if url.endswith('/api/supply-prices'): return {'surge': []}
        if url.endswith('/api/ledger'): return ledger
        raise AssertionError(url)

    def market(answers):
        def fake_post(c, url, obj, headers):
            if 'sku' in obj: return 402, QUOTE
            confirms.append(dict(obj))
            a = answers[min(len(confirms), len(answers)) - 1]
            if isinstance(a, Exception): raise a
            return a
        return fake_post

    # no confirm is started that could not finish before the deadline (the kit stops the tool at 180 s)
    late = g['confirm_supply'](lambda body: (_ for _ in ()).throw(AssertionError('sent')), 'q', '0x1', deadline=g['_clock']() + 10)
    assert not late['confirmed'] and late['attempts'] == 0
    saved = {k: g[k] for k in ('getj', 'post', 'rpc', '_sleep', 'break_even')}
    files = [g['pending_path'](Wallet()), g['sends_path'](Wallet())]
    assert all(f.endswith('.test.jsonl') for f in files)

    def clean():
        for f in files:
            if os.path.exists(f): os.remove(f)
        del confirms[:]
    try:
        g.update(getj=fake_getj, rpc=lambda m, p: '0x5', _sleep=lambda s: None, break_even=lambda *a: (10 ** 9, 'test'))
        # 0. 11/26: no sale-price cap (it had no 대표님 source) - a dear food goes to the quote and the break-even check
        CAT.append({'sku': 'rice', 'kind': 'food', 'name': '쌀 20kg', 'category': '곡물', 'list_price': 104900})
        g['post'] = market([(200, {'units': ['u-1', 'u-2']})])
        w = Wallet()
        r = run({'sku': 'rice', 'qty': 2}, w)
        assert r['ok'] and len(w.paid) == 1, r
        CAT.pop()
        clean()
        # 11/27: no 500,000-a-call cap (not a class hard limit; the kit default is 200,000 and permission.pay_limit sets it)
        CAT.append({'sku': 'beef', 'kind': 'food', 'name': '한우 세트', 'category': '정육', 'list_price': 435000})
        big = dict(QUOTE, quote='q-big', total=1200000, goods=1200000, pay=dict(QUOTE['pay'], wei=str(1200000 * 10 ** 15)))
        g['post'] = lambda c, url, obj, headers: (402, big) if 'sku' in obj else (200, {'units': ['u-1', 'u-2']})
        w = Wallet()
        r = run({'sku': 'beef', 'qty': 4}, w)
        assert r['ok'] and len(w.paid) == 1 and w.paid[0][1] == 1200000 * 10 ** 15, r
        CAT.pop()
        clean()
        # non-food is never bought
        CAT.append({'sku': 'chair', 'kind': 'goods', 'name': '의자', 'category': '가구', 'list_price': 50000})
        w = Wallet()
        assert run({'sku': 'chair', 'qty': 2}, w)['error'] == 'food only' and not w.paid
        CAT.pop()
        # 1. confirm fails once (chain not readable), then the lot is handed over: one payment, same quote+tx
        g['post'] = market([(503, {'error': 'the chain cannot be read right now'}), (200, {'units': ['u-1', 'u-2']})])
        w = Wallet()
        r = run({'sku': 'egg', 'qty': 2}, w)
        assert r['ok'] and r['confirm_attempts'] == 2 and len(w.paid) == 1, r
        assert confirms == [{'quote': 'q1', 'tx_hash': '0x' + 'ab' * 32}] * 2
        assert g['unconfirmed'](files[0]) == {} and g['latest'](files[0])['q1']['state'] == 'confirmed'
        # the market may hand the same quote out again: it is never paid twice
        r = run({'sku': 'egg', 'qty': 2}, w)
        assert not r['ok'] and len(w.paid) == 1 and 'already paid' in r['error'], r
        rows = [json.loads(x) for x in open(files[1], encoding='utf-8')]      # intent, then the tx, in sends
        assert [('tx_hash' in x, x['order']) for x in rows] == [(False, 'q1'), (True, 'q1')], rows
        clean()
        # 2. it never goes through: paid once, the row stays open (WARNING), new purchases are blocked
        g['post'] = market([(503, {'error': 'x'})])
        w = Wallet()
        r = run({'sku': 'egg', 'qty': 2}, w)
        assert not r['ok'] and r['paid_unconfirmed'] and len(w.paid) == 1 and len(confirms) == 5, r
        assert list(g['unconfirmed'](files[0])) == ['q1']
        blocked = run({'sku': 'egg', 'qty': 2}, w)
        assert not blocked['ok'] and blocked['unconfirmed'] == ['q1'] and len(w.paid) == 1
        # {confirm} retries only the confirm; the quote is gone (404) but the lot is in the public ledger -> confirmed
        ledger.append({'kind': 'supply', 'data': {'units': ['u-1', 'u-2'], 'tx_hash': '0x' + 'ab' * 32}})
        g['post'] = market([(404, {'error': 'no such quote'})])
        r = run({'confirm': 'q1'}, w)
        assert r['ok'] and len(w.paid) == 1, r
        assert g['unconfirmed'](files[0]) == {}
        del ledger[:]
        clean()
        # 3. the wallet raised after sending (timeout naming the tx): confirm with that tx, no second payment
        g['post'] = market([(200, {'units': ['u-1']})])
        w = Wallet(fail=TimeoutError('transfer 0x' + 'ab' * 32 + ' has no receipt after 90 s'))
        r = run({'sku': 'egg', 'qty': 2}, w)
        assert r['ok'] and len(w.paid) == 1 and confirms[0]['tx_hash'] == '0x' + 'ab' * 32, r
        clean()
        # 4. the wallet raised with no tx: outcome unknown, the paying row blocks new purchases
        w = Wallet(fail=PermissionError('signer unreachable'))
        r = run({'sku': 'egg', 'qty': 2}, w)
        assert not r['ok'] and confirms == [] and list(g['unconfirmed'](files[0])) == ['q1'], r
        assert run({'sku': 'egg', 'qty': 2}, w)['unconfirmed'] == ['q1'] and len(w.paid) == 1
        # the operator found the tx: {confirm, tx_hash} confirms it without paying; a wrong tx first does not stick
        assert not run({'confirm': 'q1', 'tx_hash': '0x12'}, w)['ok']
        g['post'] = market([(402, {'error': 'that transfer does not pay this quote'})])
        assert not run({'confirm': 'q1', 'tx_hash': '0x' + '11' * 32}, w)['ok']
        assert 'tx_hash' not in g['unconfirmed'](files[0])['q1']
        g['post'] = market([(200, {'units': ['u-1']})])
        del confirms[:]
        r = run({'confirm': 'q1', 'tx_hash': '0x' + 'cd' * 32}, w)
        assert r['ok'] and confirms[-1]['tx_hash'] == '0x' + 'cd' * 32 and len(w.paid) == 1, r
        assert g['unconfirmed'](files[0]) == {}
        clean()
        # 5. reverted on chain: no credits moved, recorded as failed, does not block
        w = Wallet(fail=RuntimeError('transfer 0x' + 'ef' * 32 + ' failed on chain'))
        r = run({'sku': 'egg', 'qty': 2}, w)
        assert not r['ok'] and 'reverted' in r['error'] and confirms == [] and g['unconfirmed'](files[0]) == {}, r
        # the market hands the reverted quote out again: refused with the reason, still no payment
        assert 'reverted' in run({'sku': 'egg', 'qty': 2}, w)['error'] and len(w.paid) == 1
        clean()
        # 6. too little time left in the call: no payment at all
        clock = g['_clock']
        try:
            ticks = iter([0, 100] + [100] * 50)
            g['_clock'] = lambda: next(ticks)
            w = Wallet()
            r = run({'sku': 'egg', 'qty': 2}, w)
            assert not r['ok'] and 'too little time' in r['error'] and w.paid == [], r
        finally:
            g['_clock'] = clock
        # 7. the pending file cannot be written after paying: the confirm still runs
        append = g['append']

        def no_disk(path, row):
            if row.get('state') == 'unconfirmed': raise OSError('disk full')
            return append(path, row)
        try:
            g['append'], g['post'] = no_disk, market([(200, {'units': ['u-1']})])
            w = Wallet()
            r = run({'sku': 'egg', 'qty': 2}, w)
            assert r['ok'] and len(w.paid) == 1 and len(confirms) == 1, r
        finally:
            g['append'] = append
        clean()
        # 8. the break-even ceiling (대표님 02:25: 예상 판매가 > 원가+수수료); the balance only has to cover this lot and its gas
        g['post'] = market([(200, {'units': ['u-1', 'u-2']})])
        g['break_even'] = lambda *a: (8999, 'test')
        r = run({'sku': 'egg', 'qty': 2}, Wallet())
        assert 'break-even' in r['error'] and r['max_goods'] == 8999, r
        g['break_even'] = lambda *a: (9000, 'test')
        INV['balance'] = 9000 + 1000 - 1
        w = Wallet()
        assert 'does not cover' in run({'sku': 'egg', 'qty': 2}, w)['error'] and not w.paid
        INV['balance'] = 9000 + 1000                     # no cash is kept back for a next lot (11/26: no 대표님 source)
        w = Wallet()
        assert run({'sku': 'egg', 'qty': 2}, w)['ok'] and len(w.paid) == 1
        INV['balance'] = 5000000
        clean()
        # 9. 11/26: holding unsold units of the SKU does not stop a refill (the HELD_MAX rule had no 대표님 source)
        INV['units'] = [{'unit': 'h%d' % i, 'sku': 'egg'} for i in range(3)] + [{'unit': 'h9', 'sku': 'water'}]
        w = Wallet()
        r = run({'sku': 'egg', 'qty': 4}, w)
        assert r['ok'] and len(w.paid) == 1 and 'held' not in r, r
        INV['units'] = []
        clean()
        # 대표님 02:25: at most 4 of an item per lot
        w = Wallet()
        r = run({'sku': 'egg', 'qty': 5}, w)
        assert 'qty must be 1 to 4' in r['error'] and not w.paid, r
        assert 'qty must be 1 to 4' in run({'sku': 'egg', 'qty': 17}, Wallet())['error']
        # the real break_even: unknown SKU -> list x0.6 rule; a unit about to expire -> nothing is worth paying
        be = saved['break_even']
        q = {'total': 9000, 'goods': 8000, 'storage': 900, 'forum_fees': 100, 'units': [{'expires_at': g['_clock']() + 1800}]}
        if os.path.exists('ops/price_ceiling.py'):      # the kit runs this test in the shop root
            from runpy import run_path
            m, src = be('not-a-sku', 2, q, 12000, g['_clock']())
            assert src.startswith('list') and m == 12000 * 0.6 * 0.99 * 2 - 420 - 206 - 1000, (m, src)
            sku = next(iter(run_path('ops/price_ceiling.py')['DATA']['items']))
            assert be(sku, 2, q, 12000, g['_clock']())[0] == -1
        g['append'](files[0], {'quote': 'q9', 'state': 'paying'})     # an operator 'resolved' row closes an open one
        g['append'](files[0], {'quote': 'q9', 'state': 'resolved'})
        assert g['unconfirmed'](files[0]) == {}
    finally:
        g.update(saved)
        clean()
    return 'ok'
