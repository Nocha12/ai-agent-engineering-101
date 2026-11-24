"""python3 -I ops/test_export_submission.py: the submission copy keeps the required evidence and masks strategy."""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_submission as ex                              # noqa: E402

SECRET_GOAL = '매입가는 정가의 0.6 이하, 마진 35%'


def build(root):
    (root / 'logs').mkdir()
    (root / 'app').mkdir()
    (root / 'ops').mkdir()
    t = root / 'tools' / 'shop_snapshot'
    t.mkdir(parents=True)
    (root / 'tools' / '.deprecated' / 'old@1').mkdir(parents=True)
    for f, s in (('logic.py', 'def run(a, k): return 1\n'), ('test.py', 'def test(r, k): return "ok"\n'), ('permission.yml', 'name: x\n')):
        (t / f).write_text(s)
    (t / 'forge.json').write_text(json.dumps({'sha256': {f: hashlib.sha256((t / f).read_bytes()).hexdigest()
                                                         for f in ('logic.py', 'test.py', 'permission.yml')}}))
    (root / 'agent.json').write_text(json.dumps({'persona': '노차', 'goal': SECRET_GOAL, 'run_minutes': 8,
                                                 'llm': {'base': 'b', 'model': 'm', 'key_env': 'K'}}, ensure_ascii=False))
    (root / 'notes.md').write_text('보관료 해석 0.5%\n[운영자 지시 11/24 23:40] 매입 재개: 쌀 위주\n')
    (root / 'card.json').write_text('{"name": "노차네 구멍가게"}')
    (root / 'app' / 'index.html').write_text("miniapp.offer('show_card', 'x', a => a)")
    (root / 'run_round4.sh').write_text('#!/bin/sh\n')
    (root / 'shop_wallet.key').write_text('x')
    (root / 'ops' / 'price_ceiling.py').write_text('x')
    (root / 'logs' / 'sends.jsonl').write_text('{}\n')
    (root / 'logs' / 'forge.jsonl').write_text('{"tool": "shop_snapshot", "passed": true}\n')
    rows = [{'ts': '1', 'action': 'forge_tool', 'thought': '마진 35%', 'args': "{'name': 'shop_snapshot', 'logic': 'x'}",
             'observation': '{"test": "passed", "tool": "shop_snapshot", "version": 1, "result": null}'},
            {'ts': '2', 'action': 'app_say', 'thought': '이정희-5163 손님에게 할인', 'args': "{'text': '이정희-5163님 쌀 9만'}",
             'observation': '{"ok": true, "result": {"said": "이정희-5163님 쌀 9만 크레딧'}]
    (root / 'logs' / 'react.jsonl').write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n')
    (root / 'logs' / 'agent-kit.log').write_text(
        '19:00:08 [kit] agent_kit v5.1\n'
        '19:02:05 [mind] app_say | 이정희-5163에게 마진 35%로 판다\n'
        '19:03:00 [mind] sleeps 3600 s: 공급 창 기다림\n'
        '19:04:00 [market] settled o-1 with 이정희-5163 for 90000\n'
        '19:05:00 [app] 이정희-5163: 쌀 있어요?\n'
        '19:05:01 [app] the mini app is up on port 8800, behind its access code\n'
        '  Traceback line\n')


def main():
    with tempfile.TemporaryDirectory() as d:
        src, out = Path(d) / 'src', Path(d) / 'out'
        src.mkdir()
        build(src)
        ex.export(src, out)
        assert ex.check(out) == [], ex.check(out)
        names = sorted(p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file())
        assert names == ['agent.json', 'app/index.html', 'card.json', 'logs/agent-kit.log', 'logs/forge.jsonl',
                         'logs/react.jsonl', 'notes.md', 'tools/shop_snapshot/forge.json', 'tools/shop_snapshot/logic.py',
                         'tools/shop_snapshot/permission.yml', 'tools/shop_snapshot/test.py'], names
        a = json.loads((out / 'agent.json').read_text())
        assert a['persona'] == '노차' and a['llm']['model'] == 'm' and SECRET_GOAL not in a['goal'] and ex.MASK in a['goal']
        assert (out / 'notes.md').read_text() == '[가림]\n[운영자 지시 11/24 23:40] [가림]\n'
        r = [json.loads(l) for l in (out / 'logs/react.jsonl').read_text().splitlines()]
        assert r[0] == {'ts': '1', 'action': 'forge_tool', 'thought': '[가림]', 'args': {'name': 'shop_snapshot'},
                        'observation': {'test': 'passed', 'tool': 'shop_snapshot', 'version': 1}}, r[0]
        assert r[1] == {'ts': '2', 'action': 'app_say', 'thought': '[가림]', 'args': '[가림]', 'observation': {'ok': True}}, r[1]
        log = (out / 'logs/agent-kit.log').read_text()
        assert log == ('19:00:08 [kit] agent_kit v5.1\n19:02:05 [mind] app_say | [가림]\n19:03:00 [mind] sleeps 3600 s | [가림]\n'
                       '19:04:00 [market] settled o-1 with [손님] for 90000\n19:05:00 [app] [가림]\n'
                       '19:05:01 [app] the mini app is up on port 8800, behind its access code\n'), log
        for p in out.rglob('*'):
            if p.is_file():
                s = p.read_text(encoding='utf-8')
                assert '이정희' not in s and '35%' not in s and '0.6' not in s, p
        (out / 'ops').mkdir()
        assert ex.check(out) == ['ops is not part of the submission'], ex.check(out)
    print('export tests ok')


if __name__ == '__main__':
    main()
