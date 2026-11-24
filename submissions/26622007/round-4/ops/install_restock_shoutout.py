"""Forge restock_shoutout into tools/ through the kit's own forge_tool path (same test run, permission check and
forge.json hashes as a forge by the agent). Run on the market host while the kit is stopped, from round-4:
    ~/ax-round4-venv/bin/python -I ops/install_restock_shoutout.py --kit ~/ax-round4/scripts/round4
--print only prints the forge_tool arguments. The tool has no wallet; it reads /api/inventory and the forum, posts
to the forum, and writes shoutout.json (customer names, kept out of git)."""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = 'restock_shoutout'
DESCRIPTION = ('품절로 못 산 손님·단골을 기억했다가 실제로 입고되면 포럼 입고 공지(공급 창당 1개)와 다음 방문 때 한 줄 안내를 만듭니다. '
               'args {op: want|bought|opt_out|restocked|announce|notice, customer, sku, name, qty, price, order, dry_run}.')
PERMISSION = {'network': ['market.ourolabs.ai'], 'write': ['shoutout.json', 'shoutout.test.json'],
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
