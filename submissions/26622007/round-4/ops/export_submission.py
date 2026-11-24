"""Copy the Round 4 folder into a submission copy that shows what the course asks for and as little of the shop's
strategy as possible. The running folder is only read; nothing in it changes.

    python3 -I ops/export_submission.py --src <round-4 folder> --out <empty folder>   # then --check <out>

What the course asks (weeks/week-12/README.md "What to submit"; week-12.html HW):
  agent.json (persona, goal, model; no key), tools/ as the agent left it, notes.md, logs/forge.jsonl,
  logs/react.jsonl, logs/agent-kit.log, card.json if the agent wrote one, app/.
  Evidence the course reads: agent.json, tools/, logs/forge.jsonl, logs/react.jsonl (the agent forged its tools);
  app/ (the mini app); "settlement lines in logs/agent-kit.log" (deals paid and settled on Maroo testnet).
  Checks: agent.json has persona, goal and a model id and no key; app/index.html has at least one miniapp.offer.
Everything else is left out. Where the text of a required file carries strategy (prices, margins, buying and stock
judgement, rule readings, customer talk), the field stays and its text becomes MASK.
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

MASK = '[가림]'
CUSTOMER = re.compile(r'[가-힣]{2,6}-[0-9a-f]{4}')       # village buyers: 이름-1a2b (as restock_shoutout reads them)
GOAL = ("동네 골목 '노차네 구멍가게'를 운영한다. 공급 창에서 먹을거리를 들여 손님과 다른 에이전트에게 팔고, 필요한 도구는 "
        "직접 만들며(forge_tool), 결제와 정산은 Maroo testnet에서 한다. 재고·등급·보증·가격을 속이지 않고, 보증 안의 고장은 "
        "장부로 확인한 뒤 환불한다. 시장 규칙 밖의 방법은 쓰지 않는다. 가격·매입·재고 판단의 기준과 수치, 손님 응대 방식은 "
        + MASK + ".")
KEEP_LOG = ('forge', 'kit', 'maroo', 'market', 'tunnel')    # agent-kit.log tags kept as written (customer names masked)
APP_LINES = ('the mini app is up on port', 'waiting up to', 'the page offers', 'no tools from the page')
# settle evidence: the kit logs no settle line of its own; private_checkout op=settle carries it (order ids are not names)
ARGS_KEEP = {'forge_tool': ('name',), 'read_tool': ('name',), 'private_checkout': ('op', 'order')}
ERROR = re.compile(r'^([A-Za-z_.]+(?:Error|Exception|Exit|Interrupt))\b.*', re.S)   # keep the exception class only


def agent_json(src):
    a = json.loads(src.read_text(encoding='utf-8'))
    out = {'persona': a.get('persona'), 'goal': GOAL}
    llm = a.get('llm') or {}
    out['llm'] = {k: llm[k] for k in ('base', 'model', 'key_env') if k in llm}   # key_env is a variable name, not a key
    for k in ('run_minutes', 'max_steps'):
        if k in a:
            out[k] = a[k]
    return json.dumps(out, ensure_ascii=False, indent=2) + '\n'


def notes(src):
    """One masked line per note; even the operator's time stamps stay out (they show when the operator stepped in)."""
    n = sum(1 for line in src.read_text(encoding='utf-8').splitlines() if line.strip())
    return (MASK + '\n') * n


def _args(action, raw):
    keep = ARGS_KEEP.get(action)
    if not keep:
        return MASK
    try:
        d = json.loads(raw) if isinstance(raw, str) and raw.strip().startswith('{"') else None
    except ValueError:
        d = None
    if d is None:
        import ast
        try:
            d = ast.literal_eval(raw) if isinstance(raw, str) else raw
        except (ValueError, SyntaxError):
            return MASK
    return {k: d.get(k) for k in keep if isinstance(d, dict) and k in d} or MASK


def _observation(raw):
    """Only whether the move worked: {ok} for a tool call, {test, tool, version} for a forge; the rest is masked."""
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:                                   # observations are cut at a fixed length: read the head only
        m = re.match(r'\{"ok": (true|false)', raw or '')
        return {'ok': m.group(1) == 'true'} if m else MASK
    if isinstance(d, dict) and 'test' in d:
        return {k: d.get(k) for k in ('test', 'tool', 'version')}
    if isinstance(d, dict) and 'ok' in d:
        return {'ok': d['ok']}
    return MASK


def react(src):
    out = []
    for line in src.read_text(encoding='utf-8').splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        r = {'ts': d.get('ts'), 'action': d.get('action'), 'thought': MASK, 'args': _args(d.get('action'), d.get('args'))}
        if 'observation' in d:
            r['observation'] = _observation(d.get('observation'))
        out.append(json.dumps(r, ensure_ascii=False))
    return '\n'.join(out) + ('\n' if out else '')


def _error(e):
    m = ERROR.match(str(e or '').strip())
    return m.group(1) if m else MASK


def forge_log(src):
    out = []
    for line in src.read_text(encoding='utf-8').splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get('error'):
            d['error'] = _error(d['error'])
        out.append(json.dumps(d, ensure_ascii=False))
    return '\n'.join(out) + ('\n' if out else '')


LOG_LINE = re.compile(r'^(\d\d:\d\d:\d\d) \[([a-z]+)\] ?(.*)$')


def kit_log(src):
    out = []
    for line in src.read_text(encoding='utf-8', errors='replace').splitlines():
        m = LOG_LINE.match(line)
        if not m:
            continue                                         # continuation lines (traces, wrapped text): dropped
        ts, tag, text = m.groups()
        if tag == 'forge' and ': test failed: ' in text:        # an assertion message can print a price
            head, err = text.split(': test failed: ', 1)
            out.append(f'{ts} [forge] {head}: test failed: {_error(err)}')
        elif tag in KEEP_LOG:
            out.append(f'{ts} [{tag}] {CUSTOMER.sub("[손님]", text)}')
        elif tag == 'mind':
            act = re.match(r'(sleeps \d+ s|[a-z0-9_]+)', text)
            out.append(f'{ts} [mind] {act.group(1) if act else ""} | {MASK}')
        elif tag == 'app' and text.startswith(APP_LINES):
            out.append(f'{ts} [app] {text}')
        else:
            out.append(f'{ts} [{tag}] {MASK}')
    return '\n'.join(out) + '\n'


def export(src, dst):
    if dst.exists() and any(dst.iterdir()):
        sys.exit(f'{dst} is not empty')
    dst.mkdir(parents=True, exist_ok=True)
    (dst / 'agent.json').write_text(agent_json(src / 'agent.json'), encoding='utf-8')
    if (src / 'notes.md').exists():
        (dst / 'notes.md').write_text(notes(src / 'notes.md'), encoding='utf-8')
    (dst / 'logs').mkdir()
    if (src / 'logs/forge.jsonl').exists():
        (dst / 'logs/forge.jsonl').write_text(forge_log(src / 'logs/forge.jsonl'), encoding='utf-8')
    if (src / 'logs/react.jsonl').exists():
        (dst / 'logs/react.jsonl').write_text(react(src / 'logs/react.jsonl'), encoding='utf-8')
    if (src / 'logs/agent-kit.log').exists():
        (dst / 'logs/agent-kit.log').write_text(kit_log(src / 'logs/agent-kit.log'), encoding='utf-8')
    # tools/ as the agent left it (forge.json pins each file's sha256); retired versions in tools/.deprecated stay out
    shutil.copytree(src / 'tools', dst / 'tools', ignore=shutil.ignore_patterns('.deprecated', '__pycache__', '*.pyc'))
    if (src / 'card.json').exists():
        shutil.copy2(src / 'card.json', dst / 'card.json')
    shutil.copytree(src / 'app', dst / 'app', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))


def check(dst):
    errors = []
    a = json.loads((dst / 'agent.json').read_text(encoding='utf-8'))
    if not a.get('persona') or not a.get('goal') or not (a.get('llm') or {}).get('model') \
            or '<model id>' in str(a.get('llm')):
        errors.append('agent.json needs persona, goal and a model id')
    if any(k in a.get('llm', {}) for k in ('api_key', 'key')):
        errors.append('agent.json carries a key')
    if 'miniapp.offer' not in (dst / 'app/index.html').read_text(encoding='utf-8'):
        errors.append('app/index.html has no miniapp.offer')
    for f in ('notes.md', 'logs/forge.jsonl', 'logs/react.jsonl', 'logs/agent-kit.log'):
        if not (dst / f).exists():
            errors.append(f'{f} is missing')
    import hashlib
    for t in sorted(p for p in (dst / 'tools').iterdir() if p.is_dir()):
        rec = json.loads((t / 'forge.json').read_text()) if (t / 'forge.json').exists() else {}
        for f, h in (rec.get('sha256') or {}).items():
            if hashlib.sha256((t / f).read_bytes()).hexdigest() != h:
                errors.append(f'tools/{t.name}/{f} does not match its forge record')
    allowed = {'agent.json', 'notes.md', 'card.json', 'logs', 'tools', 'app'}
    errors += [f'{p.name} is not part of the submission' for p in dst.iterdir() if p.name not in allowed]
    errors += [f'logs/{p.name} is not part of the submission' for p in (dst / 'logs').iterdir()
               if p.name not in ('forge.jsonl', 'react.jsonl', 'agent-kit.log')]
    for p in dst.rglob('*'):
        if p.is_file() and (p.suffix == '.key' or p.name in ('meshkit.json', 'customers.json', 'shoutout.json')
                            or '.kit' in p.parts):
            errors.append(f'{p.relative_to(dst)} must stay out')
        if p.is_file() and CUSTOMER.search(p.read_text(encoding='utf-8', errors='ignore')):
            rel = p.relative_to(dst)
            errors.append(f'{rel} names a customer' + (' (tools/ is hash-pinned: re-forge the tool first)'
                                                       if rel.parts[0] == 'tools' else ''))
    return errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src')
    ap.add_argument('--out')
    ap.add_argument('--check')
    a = ap.parse_args()
    if a.src and a.out:
        export(Path(a.src).expanduser(), Path(a.out).expanduser())
    target = a.check or a.out
    if target:
        errs = check(Path(target).expanduser())
        print('\n'.join(errs) or 'check ok')
        sys.exit(1 if errs else 0)
    ap.print_help()


if __name__ == '__main__':
    main()
