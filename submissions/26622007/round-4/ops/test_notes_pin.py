"""python3 -I ops/test_notes_pin.py: every '[운영자 최신 지시' line of notes.md stays in what the model reads, even when
the kit's own 3,000-char notes window (agent_kit.Mind.notes) has moved past it (11/26 21:40 check, 11/27 08:15).
Older '[운영자 지시 …]' lines are history and are not pinned (review I2)."""
import copy
import importlib.util
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import agent_kit_records as rec                             # noqa: E402

RACE = {'n': 0}


def fake_kit():
    class Mind:                                             # notes() is agent_kit.Mind.notes as in scripts/round4
        def __init__(self, root):
            self.root, self.pending = Path(root), {}

        def deliver(self, ev):
            self.pending[ev.id] = ev
            return ev

        def reply(self, args):
            return 'sent'

        def notes(self):
            try:
                out = (self.root / "notes.md").read_text(encoding="utf-8")[-3000:] or "(no notes yet)"
            except OSError:
                return "(no notes yet)"
            if RACE['n']:                                   # another writer appends right after the kit's read
                RACE['n'] -= 1
                with open(self.root / 'notes.md', 'a', encoding='utf-8') as f:
                    f.write('운영 교훈: 사이에 덧붙임.\n')
            return out

    class Toolbox:
        def call(self, name, args):
            return 'ok'

    return types.SimpleNamespace(Mind=Mind, Toolbox=Toolbox, mk=types.SimpleNamespace(LOG_MAX=1), log=lambda l='': None)


def real_kit_notes():
    """The real scripts/round4/agent_kit.py Mind.notes, when the kit imports here (market venv); else None."""
    p = HERE.parents[3] / 'scripts' / 'round4' / 'agent_kit.py'
    try:
        spec = importlib.util.spec_from_file_location('agent_kit_real', p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.Mind.notes
    except Exception as e:
        print('real kit not importable here (%s): fake only' % type(e).__name__)
        return None


A = '[운영자 최신 지시 11/26] 재고 0인 식품은 바로 품목당 2~4개 매입.'
B = '[운영자 최신 지시 11/26 19:5x 떨이] 곧 기한이 지나는 식품은 떨이로 판다.'
OLD = '[운영자 지시 11/25 03:40] 가격: 계란 20구 A 11,600 근처.'
C = '[운영자 최신 지시 11/27] 끝 쪽 지시.'


def check(ak, d, own):
    m = ak.Mind(d)
    (d / 'notes.md').unlink(missing_ok=True)
    assert m.notes() == '(no notes yet)', m.notes()
    (d / 'notes.md').write_text('', encoding='utf-8')
    assert m.notes() == own(m) == '(no notes yet)'

    short = '# 메모\n' + A + '\n운영 교훈: 짧다.\n'
    (d / 'notes.md').write_text(short, encoding='utf-8')
    assert m.notes() == own(m) == short[-3000:]

    text = '\n'.join(['# 메모', OLD, A, B, A, '운영 교훈: ' + '가' * 4000, C, '운영 교훈: 끝.']) + '\n'
    (d / 'notes.md').write_text(text, encoding='utf-8')
    out, tail = m.notes(), text[-3000:]
    assert out.endswith(tail), 'the kit tail comes last, unchanged'
    head = out[:-len(tail)]
    assert head.startswith(rec.PIN_HEAD)
    assert A in head and B in head and head.count(A) == 1 and head.index(A) < head.index(B), head
    assert OLD not in out, 'older operator lines are history, not pinned (I2)'
    assert C not in head and out.count(C) == 1, 'a line already in the tail is not repeated'
    assert '운영 교훈: 가' not in head

    w = copy.copy(m)                                        # a parallel worker's mind copy (install dispatch)
    assert w.notes() == out

    cut = '[운영자 최신 지시 11/26 반쪽] ' + '나' * 200
    text2 = '#\n' + '다' * 3000 + '\n' + cut + '\n' + '라' * (3000 - 100) + '\n'
    (d / 'notes.md').write_text(text2, encoding='utf-8')
    out2 = m.notes()
    assert cut in out2 and out2.endswith(text2[-3000:])

    (d / 'notes.md').write_bytes(b'\xff\xfe\x00bad')
    def outcome(f):
        try:
            return ('ok', f(m))
        except Exception as e:
            return ('raise', type(e))
    assert outcome(lambda x: x.notes()) == outcome(own), (outcome(lambda x: x.notes()), outcome(own))


def main():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        ak = fake_kit()
        own = ak.Mind.notes
        done = rec.install(ak, d / 'logs')
        assert 'Mind.notes(pinned)' in done, done
        check(ak, d, own)

        # a note appended between the kit's read and ours: read again, still pinned (M1)
        text = '\n'.join(['# 메모', A, '운영 교훈: ' + '가' * 4000]) + '\n'
        (d / 'notes.md').write_text(text, encoding='utf-8')
        RACE['n'] = 1
        out = ak.Mind(d).notes()
        assert A in out and out.startswith(rec.PIN_HEAD), out[:200]
        RACE['n'] = 0

        real = real_kit_notes()                             # the real kit's notes(), not the copy above (M2)
        if real:
            kit = fake_kit()
            kit.Mind.notes = real
            rec.install(kit, d / 'logs2')
            check(kit, d, real)
    print('test_notes_pin ok')


if __name__ == '__main__':
    main()
