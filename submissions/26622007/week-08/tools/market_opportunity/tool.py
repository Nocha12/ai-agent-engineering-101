import json
import os
import sys
from pathlib import Path
try:
    import strategy_screen   # private: the public branch does not carry it (cdcbb01b)
except ModuleNotFoundError as e:
    if e.name != 'strategy_screen':
        raise
    strategy_screen = None

args = json.load(sys.stdin)
if strategy_screen is None:
    # Nothing was screened: say so plainly, so the answer is never read as "no demand" and never as a buy signal.
    print(json.dumps(dict(available=False, candidates=[],
                          basis='strategy_screen is not on this branch; no screen was computed, '
                                'this is not a finding of no demand'), ensure_ascii=False))
else:
    print(json.dumps(strategy_screen.build(Path(os.environ['MARKET_DIR']).resolve().parent,
                                           sku=args.get('sku')), ensure_ascii=False))
