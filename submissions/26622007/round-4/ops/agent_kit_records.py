"""Run scripts/round4/agent_kit.py unchanged, plus local operating records the kit does not keep.

run_round4.sh starts the kit through this file with the same arguments:
    python submissions/26622007/round-4/ops/agent_kit_records.py run --dir ... --market ... --name ...
It wraps three kit methods from outside (the kit file is not edited) and writes, under <dir>/logs/:
  messages.jsonl   every message the agent receives (A2A, mini app) as the sender wrote it, and every answer sent
                   with reply or by the a2a handler tool without the agent (kit: Mind.deliver, Mind.reply, the a2a
                   handler's Toolbox.call; a message the handler passed on appears twice, the handler's row has
                   'via'). Not here: the kit's own 'busy' answer after the A2A wait and app_say broadcasts.
  decisions.jsonl  the full arguments and result of each buying/pricing tool call (best_sellers, price_ceiling,
                   buy_food_lot, ...), so a refused wholesale quote keeps its total, ceiling and units
and it lifts the kit's 20 MB roll-over of logs/agent-kit.log (market_kit.LOG_MAX), which overwrote the older half.
Both files hold customer text: they stay on this machine (.gitignore) and out of the submission copy
(ops/export_submission.py keeps only the three logs the course asks for). A record that fails to write never stops
the kit.
"""
import json
import sys
import threading
import time
from pathlib import Path

DECISION_TOOLS = ('best_sellers', 'price_ceiling', 'buy_food_lot', 'food_candidates', 'stock_research')
TEXT_MAX = 8000
NO_ROLLOVER = 1 << 40
_lock = threading.Lock()


def _dir_arg(argv):
    for i, a in enumerate(argv):
        if a == '--dir' and i + 1 < len(argv):
            return Path(argv[i + 1]).expanduser().resolve()
        if a.startswith('--dir='):
            return Path(a.split('=', 1)[1]).expanduser().resolve()
    return Path('.').resolve()


def _clip(v):
    if isinstance(v, str):
        return v[:TEXT_MAX]
    try:
        s = json.dumps(v, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(v)[:TEXT_MAX]
    return v if len(s) <= TEXT_MAX else s[:TEXT_MAX]


def _writer(logs):
    def write(name, row):
        try:
            line = json.dumps(dict(row, ts=round(time.time(), 3)), ensure_ascii=False, default=str)
            with _lock, open(logs / name, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:                       # a record never stops the kit
            pass
    return write


def install(ak, logs):
    """Wrap ak.Mind.deliver/reply and ak.Toolbox.call; lift ak.mk.LOG_MAX. Returns the names that were wrapped."""
    logs.mkdir(parents=True, exist_ok=True)
    write = _writer(logs)
    done = []
    if hasattr(ak.mk, 'LOG_MAX'):
        ak.mk.LOG_MAX = NO_ROLLOVER
        done.append('LOG_MAX')

    deliver = ak.Mind.deliver

    def deliver_rec(self, ev):
        try:
            write('messages.jsonl', {'dir': 'in', 'id': getattr(ev, 'id', None), 'kind': getattr(ev, 'kind', None),
                                     'from': _clip(getattr(ev, 'frm', None)), 'text': _clip(getattr(ev, 'text', None)),
                                     'data': _clip(getattr(ev, 'data', None)), 'task': getattr(ev, 'task', None)})
        except Exception:
            pass
        return deliver(self, ev)

    reply = ak.Mind.reply

    def reply_rec(self, args):
        try:
            ev = self.pending.get(str((args or {}).get('to')))
        except Exception:
            ev = None
        out = reply(self, args)
        try:
            if out == 'sent':
                write('messages.jsonl', {'dir': 'out', 'id': (args or {}).get('to'), 'kind': getattr(ev, 'kind', None),
                                         'task': getattr(ev, 'task', None), 'text': _clip((args or {}).get('text')),
                                         'data': _clip((args or {}).get('data')), 'state': (args or {}).get('state')})
        except Exception:
            pass
        return out

    call = ak.Toolbox.call

    def call_rec(self, name, args):
        out = call(self, name, args)
        try:
            _record_call(self, name, args, out)
        except Exception:
            pass
        return out

    def _record_call(self, name, args, out):
        try:
            handles = (self.tools.get(name) or {}).get('perm', {}).get('handles') or []
        except Exception:
            handles = []
        if 'a2a' in handles:
            a = args or {}
            r = out.get('result') if isinstance(out, dict) and out.get('ok') else None
            write('messages.jsonl', {'dir': 'in', 'via': name, 'kind': 'a2a', 'from': _clip(a.get('claimed_from')),
                                     'text': _clip(a.get('text')), 'data': _clip(a.get('data')), 'task': a.get('task_id')})
            if isinstance(r, dict) and not r.get('defer') and (r.get('reply') or r.get('data') is not None):
                write('messages.jsonl', {'dir': 'out', 'via': name, 'kind': 'a2a', 'task': a.get('task_id'),
                                         'text': _clip(r.get('reply')), 'data': _clip(r.get('data')), 'state': r.get('state')})
        if name in DECISION_TOOLS:
            # the kit's answer is {ok: the run worked, result: what the tool returned (its own ok/error inside)}
            res = out.get('result') if isinstance(out, dict) else None
            tool_ok = res.get('ok') if isinstance(res, dict) and 'ok' in res else (isinstance(out, dict) and out.get('ok'))
            err = (res.get('error') if isinstance(res, dict) else None) or (out.get('error') if isinstance(out, dict) else None)
            write('decisions.jsonl', {'tool': name, 'args': _clip(args), 'ok': bool(tool_ok), 'error': _clip(err),
                                      'quote_refused': name == 'buy_food_lot' and isinstance(res, dict)
                                      and 'total' in res and not res.get('ok') and not res.get('tx_hash'),
                                      'result': _clip(res if res is not None else out)})

    ak.Mind.deliver, ak.Mind.reply, ak.Toolbox.call = deliver_rec, reply_rec, call_rec
    return done + ['Mind.deliver', 'Mind.reply', 'Toolbox.call']


def main():
    kit_dir = Path(__file__).resolve().parents[4] / 'scripts' / 'round4'     # round-4/ops -> repo root
    sys.path.insert(0, str(kit_dir))
    import agent_kit as ak                                   # noqa: E402
    if sys.argv[1:2] == ['run']:
        try:
            install(ak, _dir_arg(sys.argv[2:]) / 'logs')
        except Exception as e:                               # records are optional; the kit is not
            print(f'[records] not installed: {e}', file=sys.stderr)
    sys.argv[0] = str(kit_dir / 'agent_kit.py')
    ak.main()


if __name__ == '__main__':
    main()
