"""Print one v5.1 forge_tool move for best_sellers to stdout. Does not install or register it.

From round-4: python3 -B ops/best_sellers_forge.py [--data ops/best_sellers_data.json] > /tmp/best-sellers-move.json
The tool embeds the paid-sale rows built by best_sellers.py --build and, at call time, reads only newer rows from
the public ledger (GET /api/ledger?after=<last seq>, at most 3 pages, 1.1 s apart). It has no identity, wallet or
write permission. Inside the kit's test (kit.testing) it does not touch the network.
"""

import argparse
import json
from pathlib import Path

OPS = Path(__file__).resolve().parent
BEGIN, END = '# BEGIN FORGE KERNEL', '# END FORGE KERNEL'
MAX_LOGIC = 40_000

RUN = '''

PAGE = 500                                  # the market's /api/ledger page size


def _live(kit, data):
    """Rows after data['last_seq']: at most 3 GET pages, 1.1 s apart, within 15 s. Returns (rows, truncated)."""
    import time as _t
    import httpx
    rows, after, start = [], data['last_seq'], _t.time()
    for page in range(3):
        if page:
            _t.sleep(1.1)
        r = httpx.get(kit.market.rstrip('/') + '/api/ledger', params={'after': after}, timeout=5)
        r.raise_for_status()
        got = [e for e in r.json() if isinstance(e, dict) and isinstance(e.get('seq'), int) and e['seq'] > after]
        rows += got
        if len(got) < PAGE:
            return rows, False
        after = max(e['seq'] for e in got)
        if _t.time() - start > 6:              # a next page (1.1 s + up to 2 x 5 s) must end well inside 30 s
            break
    return rows, True


def run(args, kit):
    import time as _t
    args = dict(args or {})
    live = args.pop('live', True)
    if type(live) is not bool:
        raise ValueError('live is true or false')
    testing = getattr(kit, 'testing', False)
    now = max(_t.time(), DATA['as_of']) if not testing else DATA['as_of']
    data, extra = DATA, {}
    if live and not testing:
        try:
            rows, truncated = _live(kit, DATA)
            data = merge_live(DATA, rows, now)
            if truncated:                       # the newest rows are missing: date the answer by what was read
                now = max([e.get('ts', 0) for e in rows if isinstance(e.get('ts'), (int, float))] or [DATA['as_of']])
                extra['live_truncated'] = True
            data = dict(data, as_of=now)
        except Exception as e:
            extra['live_error'] = 'live ledger read failed, using built data: ' + type(e).__name__
    out = rank(args, data, now)
    if extra.get('live_truncated') and not testing:      # age against the clock, not the newest row read
        out['data_age_min'] = int((_t.time() - now) // 60)
        out['stale'] = out['data_age_min'] > STALE_MIN
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
    while True:                                 # the kit reads a tool back 12,000 chars at a time; keep it small
        logic = f'DATA = {json.dumps(data, ensure_ascii=False, separators=(",", ":"))}\n\n{kernel}\n{RUN}'
        if len(logic) < MAX_LOGIC or not data['sales']:
            break
        keep = data['sales'][len(data['sales']) // 4:]          # drop the oldest quarter
        data = dict(data, sales=keep, trimmed=data.get('trimmed', 0) + len(data['sales']) - len(keep))
    compile(logic, 'logic.py', 'exec')
    return {'thought': 'Forge the read-only best sellers lookup from paid, on-chain sales.', 'action': 'forge_tool',
            'args': {'name': 'best_sellers',
                     'description': ('실제 결제(체인 tx)된 판매만으로 잘 팔리는 품목 상위 목록. 선택 top(1-20, 기본 5), '
                                     'horizon_hours(기본 3), own_stock {sku: 개수}, kind(all|food|goods), min_tx(기본 1), '
                                     'live(기본 true: 공개 장부 새 행을 GET으로 읽음). buy_qty 0이면 사지 않는다. 출력 items[sku, name, per_hour, tx, '
                                     'tx_on_chain, shops, repeat_buyers, price[최저,중앙,최고], price_vs_list, buy_qty, '
                                     'price_ceiling_args]. 거래·송금을 하지 않는다.'),
                     'permission': {'network': ['market.ourolabs.ai'], 'timeout': 30},
                     'logic': logic, 'test': TEST}}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=str(OPS / 'best_sellers_data.json'))
    print(json.dumps(forge_move(ap.parse_args().data), ensure_ascii=False, separators=(',', ':')))
