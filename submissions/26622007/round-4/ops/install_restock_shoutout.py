"""Forge restock_shoutout into tools/ through the kit's own forge_tool path (same test run, permission check and
forge.json hashes as a forge by the agent). Run on the market host while the kit is stopped, from round-4:
    ~/ax-round4-venv/bin/python -I ops/install_restock_shoutout.py --kit ~/ax-round4/scripts/round4
--print only prints the forge_tool arguments. The tool has no wallet; it reads /api/inventory and the forum, posts
to the forum, and writes shoutout.json (customer names) and waitlist.json (the contacts customers left for a restock notice); both
are kept out of git and out of the submission copy."""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = 'restock_shoutout'
DESCRIPTION = ('품절로 못 산 손님·단골을 기억했다가 실제로 입고되면 포럼 입고 공지(공급 창당 1개)와 다음 방문 때 한 줄 안내를 만듭니다. '
               'want의 say는 연락처를 묻고, 손님이 준 연락처(포럼 이름)는 op contact로 대기 명단에 저장해 입고 때 먼저 알린 뒤 지웁니다. '
               'args {op: want|contact|bought|opt_out|restocked|announce|notice, customer, sku, name, qty, price, prices({등급: 가격}), contact, last_message, order, force, dry_run}. '
               'restocked는 등급별 안내가(check_item_inventory의 price)가 있으면 그 값으로 알리고, 없는 등급만 준 가격을 안내가로 정합니다.')
PERMISSION = {'network': ['market.ourolabs.ai'], 'write': ['shoutout.json', 'shoutout.json.tmp', 'shoutout.test.json', 'shoutout.test.json.tmp',
                        'waitlist.json', 'waitlist.json.tmp', 'waitlist.test.json', 'waitlist.test.json.tmp', 'logs/lost_deals.jsonl', 'logs/lost_deals.test.jsonl',
                        'logs/quotes.json', 'logs/quotes.json.tmp', 'logs/quotes.test.json', 'logs/quotes.test.json.tmp'],
              'identity': ['shop'], 'timeout': 30}


def move():
    return {'name': NAME, 'description': DESCRIPTION, 'permission': PERMISSION,
            'logic': (HERE / NAME / 'logic.py').read_text(encoding='utf-8'),
            'test': (HERE / NAME / 'test.py').read_text(encoding='utf-8')}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kit', help='the directory with agent_kit.py')
    ap.add_argument('--print', action='store_true')
    a = ap.parse_args()
    if a.print or not a.kit:
        print(json.dumps(move(), ensure_ascii=False, indent=1))
        return
    sys.path.insert(0, str(Path(a.kit).expanduser().resolve()))
    import agent_kit as ak                                  # noqa: E402
    import maroo                                            # noqa: E402
    d = HERE.parent
    ak.mk.LOG_FILE = d / 'logs' / 'agent-kit.log'
    keys = {r: maroo.Wallet.load(d / f'{r}_wallet.key') for r in ('shop', 'buyer')}
    ctx = {'market': 'https://market.ourolabs.ai', 'name': 'nocha12', 'tokens': {'shop': 'offline', 'buyer': 'offline'},
           'addresses': {r: k.address for r, k in keys.items()}, 'signer': ak.Signer(keys, d)}
    out = ak.Toolbox(d, ctx).forge(move())
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0 if isinstance(out, dict) and out.get('test') == 'passed' else 1)


if __name__ == '__main__':
    main()
