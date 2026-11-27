"""Print one v5.1 forge_tool move for best_sellers to stdout. Does not install or register it.

From round-4: python3 -B ops/best_sellers_forge.py [--data ops/best_sellers_data.json] > /tmp/best-sellers-move.json
The tool embeds the paid-sale rows built by best_sellers.py --build and, at call time, reads only newer rows from
the public ledger (GET /api/ledger?after=<last seq>, at most 3 pages, 1.1 s apart). When the ledger fails or answers
nothing it reads the newest blocks from the Maroo chain instead (read-only JSON-RPC, at most 3 batch POSTs 1.1 s
apart) and says which path answered in `source`. It has no identity, wallet or write permission. Inside the kit's test (kit.testing) it does not touch the network.
"""

import argparse
import json
from pathlib import Path

OPS = Path(__file__).resolve().parent
BEGIN, END = '# BEGIN FORGE KERNEL', '# END FORGE KERNEL'
MAX_LOGIC = 40_000

RUN = '''

PAGE = 500                                  # the market's /api/ledger page size
RPC = 'https://rpc-testnet.maroo.io'        # Maroo testnet, read-only calls only
CHAIN_BATCH, CHAIN_POSTS = 25, 3            # at most 75 blocks (about 75 s of chain) per call, 1.1 s apart
BUDGET = 22                                 # seconds for all network reads; the kit stops a tool at 30
_LAST = [0.0]                               # last request time, shared across calls (1 request per 1.1 s)


def _left(deadline):
    import time as _t
    return deadline - _t.time()


def _gap(deadline, need):
    """Wait out the 1.1 s request gap; False when the time left cannot fit one more request of `need` seconds."""
    import time as _t
    wait = max(0.0, _LAST[0] + 1.1 - _t.time())
    if _left(deadline) < wait + need:
        return False
    _t.sleep(wait)
    _LAST[0] = _t.time()
    return True


def _live(kit, data, deadline):
    """Rows after data['last_seq']: at most 3 GET pages, 1.1 s apart, within 15 s. Returns (rows, truncated).
    The first page starts one row early: a working ledger always returns row last_seq, so an empty answer means
    the ledger is closed or was reset, and raises."""
    import time as _t
    import httpx
    rows, after = [], max(0, data['last_seq'] - 1)
    for page in range(3):
        if not _gap(deadline, 3):
            return rows, True
        r = httpx.get(kit.market.rstrip('/') + '/api/ledger', params={'after': after},
                      timeout=httpx.Timeout(min(5.0, _left(deadline) - 1), connect=2.0))
        r.raise_for_status()
        got = [e for e in r.json() if isinstance(e, dict) and isinstance(e.get('seq'), int) and e['seq'] > after]
        if not page and data['last_seq']:
            mine = [e for e in got if e['seq'] == data['last_seq']]
            if not mine:
                raise RuntimeError('ledger answered nothing at its last known row')
            if data.get('last_hash') and mine[0].get('hash') != data['last_hash']:
                raise RuntimeError('ledger was reset (its last known row has another hash)')
        rows += [e for e in got if e['seq'] > data['last_seq']]
        if len(got) < PAGE:
            return rows, False
        after = max(e['seq'] for e in got)
    return rows, True


def _chain(kit, data, deadline):
    """When the ledger fails: the newest blocks from the chain (eth_getBlockByNumber, full tx, JSON-RPC batches).
    Reads from data['chain']['next'] but at most CHAIN_BATCH x CHAIN_POSTS blocks back from the tip.
    Returns (settles [ts, shop, price, buyer, tx], gap): gap = blocks between the build and what was read."""
    import time as _t
    import httpx
    c = data['chain']

    def call(batch):
        if not _gap(deadline, 4):
            raise TimeoutError('no time left for a chain read')
        r = httpx.post(RPC, json=batch, timeout=httpx.Timeout(min(6.0, _left(deadline) - 1), connect=2.0))
        r.raise_for_status()
        out = r.json()
        if not isinstance(out, list) or any('error' in o for o in out):
            raise RuntimeError('chain read failed')
        return [o['result'] for o in sorted(out, key=lambda o: o['id'])]

    tip = int(call([{'jsonrpc': '2.0', 'id': 0, 'method': 'eth_blockNumber', 'params': []}])[0], 16)
    first = max(c['next'], tip - CHAIN_BATCH * CHAIN_POSTS + 1)
    txs, n = [], first
    while n <= tip and n < first + CHAIN_BATCH * CHAIN_POSTS and _left(deadline) > 1.1 + 4:
        hi = min(tip, n + CHAIN_BATCH - 1)
        try:
            blocks = call([{'jsonrpc': '2.0', 'id': i, 'method': 'eth_getBlockByNumber', 'params': [hex(x), True]}
                           for i, x in enumerate(range(n, hi + 1))])
        except TimeoutError:                    # out of time: keep what was read, the rest counts as gap
            break
        for b in blocks:
            for t in (b or {}).get('transactions') or []:
                txs.append(dict(ts=int(b['timestamp'], 16), hash=t['hash'].lower(), frm=t['from'].lower(),
                                to=(t.get('to') or '').lower(), credits=int(t['value'], 16) // 10 ** 15,
                                input=(t.get('input') or '0x').lower()))
        n = hi + 1
    return chain_settles(txs, c['market'], c['shops']), max(0, first - c['next']) + max(0, tip - n + 1)


def run(args, kit):
    import time as _t
    from runpy import run_path
    args = dict(args or {})
    live = args.pop('live', True)
    if type(live) is not bool:
        raise ValueError('live is true or false')
    testing = getattr(kit, 'testing', False)
    data, extra, source = DATA, {}, 'built'
    if globals().get('__file__', '').endswith('logic.py'):
        data = run_path('ops/price_demand.py')['load_cache']()
    base, base_time = data, data['as_of']
    now = _t.time() if not testing else data['as_of']
    deadline = _t.time() + BUDGET
    if live and not testing:
        try:
            rows, truncated = _live(kit, base, deadline)
            data = merge_live(base, rows, now)
            if truncated or any(row.get('kind') in ('supply', 'settle', 'expire', 'bankrupt') for row in rows):
                data['stock_pending'] = True
            if truncated:                       # the newest rows are missing: date the answer by what was read
                now = max([e.get('ts', 0) for e in rows if isinstance(e.get('ts'), (int, float))] or [base_time])
                extra['live_truncated'] = True
            data, source = dict(data, as_of=now), 'ledger'
        except Exception as e:
            extra['live_error'] = 'ledger read failed: ' + type(e).__name__ + ': ' + str(e)[:80]
            extra['ledger_down'] = True
            data = base
            if base.get('chain'):
                try:
                    settles, gap = _chain(kit, base, deadline)
                    data, unlabelled = merge_chain(base, settles)
                    # new chain sales (or unread blocks) took units the stock snapshot still counts
                    if gap or unlabelled or len(data['sales']) != len(base['sales']):
                        data['stock_pending'] = True
                    extra.update(chain_sales=len(settles), chain_unlabelled=unlabelled, chain_gap_blocks=gap)
                    source = 'chain'
                    if not gap:                 # the build and the tail meet: the answer is current
                        data = dict(data, as_of=now)
                    # with a gap the rows are counted but as_of stays the build's, so age/stale stay honest
                except Exception as e2:
                    extra['chain_error'] = 'chain read failed, using built data: ' + type(e2).__name__
    out = rank(args, data, now)
    out['source'] = source                      # ledger | chain | built (what the newest rows came from)
    out['built_from'] = base.get('source', 'ledger')
    if extra.get('live_truncated') and not testing:      # age against the clock, not the newest row read
        out['data_age_min'] = int((_t.time() - now) // 60)
        out['stale'] = True
        for item in out['items']:
            item['buy_qty_basis'] = 'no_stock_data'
    out.update(extra)
    return out
'''

TEST = '''
def test(run, kit):
    out = run({'top': 3}, kit)
    assert isinstance(out, dict) and isinstance(out['items'], list) and 'as_of' in out
    for item in out['items']:
        assert item['tx'] >= 1 and item['buy_qty'] >= 0 and item['price'][0] <= item['price'][1] <= item['price'][2]
    try:
        run({'wallet': 'x'}, kit)
    except ValueError:
        pass
    else:
        raise AssertionError('unknown args must be refused')
'''


def forge_move(data_path):
    lines = (OPS / 'best_sellers.py').read_text(encoding='utf-8').splitlines()
    if lines.count(BEGIN) != 1 or lines.count(END) != 1 or lines.index(BEGIN) >= lines.index(END):
        raise ValueError('expected one ordered pair of forge kernel markers')
    kernel = '\n'.join(lines[lines.index(BEGIN) + 1:lines.index(END)])
    data = json.loads(Path(data_path).read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('sales'), list):
        raise ValueError('expected best_sellers data with a sales list (run best_sellers.py --build)')
    if data.get('chain'):                       # buyer names only keep chain ids equal to ledger ids; not worth the size
        data['chain'] = {k: v for k, v in data['chain'].items() if k != 'buyers'}
    for k in ('sku_stock', 'stock_as_of', 'stock_unconfirmed'):   # the forged tool reads the live ops/ cache; without stock data this embedded copy ranks as stale (buy_qty_basis no_stock_data)
        data.pop(k, None)
    while True:                                 # the kit reads a tool back 12,000 chars at a time; keep it small
        logic = f'DATA = {data!r}\n\n{kernel}\n{RUN}'      # a Python literal: JSON's null/true would not parse
        if len(logic) < MAX_LOGIC or not data['sales']:
            break
        keep = data['sales'][max(1, len(data['sales']) // 4):]  # drop the oldest quarter (at least one row)
        kept = {(r[0], r[2]) for r in keep}
        only = {h: v for h, v in (data.get('chain_only_tx') or {}).items() if tuple(v) in kept}
        data = dict(data, sales=keep, chain_only_tx=only, trimmed=data.get('trimmed', 0) + len(data['sales']) - len(keep))
    for k in ('url_names', 'chain'):            # 11/27: still too big with no sales (url_names ~38k, chain ~36k). In the kit
        if len(logic) < MAX_LOGIC:                # the forged tool reads the live ops/ cache (load_cache), never this copy
            break
        data = dict(data)
        data.pop(k, None)
        logic = f'DATA = {data!r}\n\n{kernel}\n{RUN}'
    if len(logic) >= MAX_LOGIC:
        raise ValueError(f'tool logic is {len(logic)} chars even with no sales; shrink the catalog part of the data')
    compile(logic, 'logic.py', 'exec')
    return {'thought': 'Forge the read-only best sellers lookup from paid, on-chain sales.', 'action': 'forge_tool',
            'args': {'name': 'best_sellers',
                     'description': ('실제 결제(체인 tx)된 판매만으로 잘 팔리는 품목 상위 목록. 선택 top(1-20, 기본 5), '
                                     'horizon_hours(기본 3), own_stock {sku: 개수}, kind(all|food|goods), min_tx(기본 1), '
                                     'live(기본 true: 공개 장부 새 행을 GET으로 읽고, 장부가 실패하거나 비면 Maroo 체인 최근 블록을 읽기 전용으로 읽음). '
                                     'source(ledger|chain|built)는 최신 행의 출처, ledger_down은 장부 실패 표시. buy_qty는 참고값이며 0이거나 stale이어도 매입을 막지 않는다. 출력 items[sku, name, per_hour, tx, '
                                     'tx_on_chain, shops, repeat_buyers, price[최저,중앙,최고], price_vs_list, buy_qty(참고값), buy_qty_basis(stock|no_stock_data), '
                                     'price_ceiling_args]. 거래·송금을 하지 않는다.'),
                     'permission': {'network': ['market.ourolabs.ai', 'rpc-testnet.maroo.io'], 'timeout': 30},
                     'logic': logic, 'test': TEST}}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=str(OPS / 'best_sellers_data.json'))
    print(json.dumps(forge_move(ap.parse_args().data), ensure_ascii=False, separators=(',', ':')))
