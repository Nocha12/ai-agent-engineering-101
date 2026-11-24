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

RUN = '''

def _live(kit, data):
    import time as _t
    import httpx
    rows, after = [], data['last_seq']
    for page in range(3):
        if page:
            _t.sleep(1.1)
        r = httpx.get(kit.market.rstrip('/') + '/api/ledger', params={'after': after}, timeout=8)
        r.raise_for_status()
        got = [e for e in r.json() if isinstance(e, dict) and isinstance(e.get('seq'), int) and e['seq'] > after]
        if not got:
            break
        rows += got
        after = max(e['seq'] for e in got)
    return rows


def run(args, kit):
    import time as _t
    args = dict(args or {})
    live = args.pop('live', True)
    data, note = DATA, None
    if live and not getattr(kit, 'testing', False):
        try:
            data = merge_live(DATA, _live(kit, DATA), _t.time())
            data = dict(data, as_of=_t.time())
        except Exception as e:
            note = 'live ledger read failed, using built data: ' + type(e).__name__
    out = rank(args, data, _t.time() if not getattr(kit, 'testing', False) else DATA['as_of'])
    if note:
        out['live_error'] = note
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
    logic = f'DATA = {json.dumps(data, ensure_ascii=False, separators=(",", ":"))}\n\n{kernel}\n{RUN}'
    compile(logic, 'logic.py', 'exec')
    return {'thought': 'Forge the read-only best sellers lookup from paid, on-chain sales.', 'action': 'forge_tool',
            'args': {'name': 'best_sellers',
                     'description': ('실제 결제(체인 tx)된 판매만으로 잘 팔리는 품목 상위 목록. 선택 top(1-20, 기본 5), '
                                     'horizon_hours(기본 3), own_stock {sku: 개수}, kind(all|food|goods), min_tx(기본 1), '
                                     'live(기본 true: 공개 장부 새 행을 읽음). 출력 items[sku, name, per_hour, tx, '
                                     'tx_on_chain, shops, repeat_buyers, price[최저,중앙,최고], price_vs_list, buy_qty, '
                                     'price_ceiling_args]. 거래·송금을 하지 않는다.'),
                     'permission': {'network': ['market.ourolabs.ai'], 'timeout': 30},
                     'logic': logic, 'test': TEST}}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default=str(OPS / 'best_sellers_data.json'))
    print(json.dumps(forge_move(ap.parse_args().data), ensure_ascii=False, separators=(',', ':')))
