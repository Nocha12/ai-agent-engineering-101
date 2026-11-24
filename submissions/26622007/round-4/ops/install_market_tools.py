"""Forge best_sellers and price_ceiling into tools/ through the kit's own forge path (the same test run, permission
check and forge.json hashes as the agent's forge_tool move printed by ops/best_sellers_forge.py and
ops/price_ceiling_forge.py). Run on the market host while the kit is stopped:
    ~/ax-round4-venv/bin/python -I ops/install_market_tools.py --kit ~/ax-round4/scripts/round4 --data <best_sellers cache>
--data is required: best_sellers_forge.py would otherwise embed ops/best_sellers_data.json, whatever is there.
In the kit both tools read the ops/ caches at call time (pushed by the analytics host): a missing cache is an error,
a stale one answers stale:true / buy_qty 0 (best_sellers) or verdict skip (price_ceiling). The data embedded at forge
time is not used there. --print only prints the two forge_tool moves. An earlier version moves to tools/.deprecated/<name>@<ver>."""
import argparse
import json
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def moves(data):
    best = runpy.run_path(str(HERE / 'best_sellers_forge.py'))['forge_move'](str(data))
    price = runpy.run_path(str(HERE / 'price_ceiling_forge.py'))['forge_move']()
    return [m['args'] for m in (best, price)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kit', help='the directory with agent_kit.py')
    ap.add_argument('--data', required=True, help='best_sellers cache built by best_sellers.py --build')
    ap.add_argument('--print', action='store_true')
    a = ap.parse_args()
    data = Path(a.data).expanduser().resolve()
    if not data.is_file():
        sys.exit(f'no best_sellers cache at {data}')
    todo = moves(data)
    if a.print or not a.kit:
        print(json.dumps(todo, ensure_ascii=False, indent=1))
        return
    sys.path.insert(0, str(Path(a.kit).expanduser().resolve()))
    import agent_kit as ak                                  # noqa: E402
    import maroo                                            # noqa: E402
    d = HERE.parent
    ak.mk.LOG_FILE = d / 'logs' / 'agent-kit.log'
    keys = {r: maroo.Wallet.load(d / f'{r}_wallet.key') for r in ('shop', 'buyer')}
    ctx = {'market': 'https://market.ourolabs.ai', 'name': 'nocha12', 'tokens': {'shop': 'offline', 'buyer': 'offline'},
           'addresses': {r: k.address for r, k in keys.items()}, 'signer': ak.Signer(keys, d)}
    box = ak.Toolbox(d, ctx)
    ok = True
    for m in todo:
        out = box.forge(m)
        print(m['name'], json.dumps(out, ensure_ascii=False)[:400])
        ok = ok and isinstance(out, dict) and out.get('test') == 'passed'
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
