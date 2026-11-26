"""python3 -I ops/test_notes_pin.py: every '[운영자' line of notes.md stays in what the model reads, even when the
kit's own 3,000-char notes window (agent_kit.Mind.notes) has moved past it (11/26 21:40 check, 11/27 08:15)."""
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_kit_records as rec                             # noqa: E402


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
                return (self.root / "notes.md").read_text(encoding="utf-8")[-3000:] or "(no notes yet)"
            except OSError:
                return "(no notes yet)"

    class Toolbox:
        def call(self, name, args):
            return 'ok'

    return types.SimpleNamespace(Mind=Mind, Toolbox=Toolbox, mk=types.SimpleNamespace(LOG_MAX=1), log=lambda l='': None)


def main():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        ak = fake_kit()
        own = Mind0 = ak.Mind.notes
        done = rec.install(ak, d / 'logs')
        assert 'Mind.notes(pinned)' in done, done
        m = ak.Mind(d)

        # no file / empty file: the kit's own answer
        assert m.notes() == '(no notes yet)', m.notes()
        (d / 'notes.md').write_text('', encoding='utf-8')
        assert m.notes() == Mind0(m) == '(no notes yet)'

        # short notes, everything inside the window: unchanged
        short = '# 메모\n[운영자 지시 11/25 03:40] 가격은 장부 시세로.\n운영 교훈: 짧다.\n'
        (d / 'notes.md').write_text(short, encoding='utf-8')
        assert m.notes() == own(m) == short[-3000:]

        # long notes: early operator lines fall out of the 3,000-char tail but are still shown, once, in file order
        a = '[운영자 지시 11/25 03:40] 가격: 같은 품목·등급의 공개 장부 시세로 부른다.'
        b = '[운영자 지시 11/25 08:20] 재고는 check_item_inventory로 본다.'
        c = '[운영자 최신 지시 11/26] 재고 0인 식품은 바로 매입.'
        text = '\n'.join(['# 메모', a, b, a, '운영 교훈: ' + '가' * 4000, c, '운영 교훈: 끝.']) + '\n'
        (d / 'notes.md').write_text(text, encoding='utf-8')
        out, tail = m.notes(), text[-3000:]
        assert out.endswith(tail), 'the kit tail comes last, unchanged'
        head = out[:-len(tail)]
        assert a in head and b in head and head.count(a) == 1 and head.index(a) < head.index(b), head
        assert c not in head and out.count(c) == 1, 'a line already in the tail is not repeated'
        assert '운영 교훈: 가' not in head, 'only operator lines are pinned'

        # a line the window cut in half is pinned whole
        cut = '[운영자 지시 11/26 반쪽] ' + '나' * 200
        text2 = '#\n' + '다' * 3000 + '\n' + cut + '\n' + '라' * (3000 - 100) + '\n'
        (d / 'notes.md').write_text(text2, encoding='utf-8')
        out2 = m.notes()
        assert cut in out2 and out2.endswith(text2[-3000:])

        # unreadable bytes: exactly what the kit's own notes() does (no new behaviour, no new exception type)
        (d / 'notes.md').write_bytes(b'\xff\xfe\x00bad')
        def outcome(f):
            try:
                return ('ok', f(m))
            except Exception as e:
                return ('raise', type(e))
        assert outcome(lambda x: x.notes()) == outcome(own), (outcome(lambda x: x.notes()), outcome(own))
    print('test_notes_pin ok')


if __name__ == '__main__':
    main()
