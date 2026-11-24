"""python3 -I ops/test_agent_kit_records.py: the wrapper records messages and decisions without changing the kit."""
import json
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_kit_records as rec                             # noqa: E402


def fake_kit():
    class Mind:
        def __init__(self):
            self.pending = {}

        def deliver(self, ev):
            self.pending[ev.id] = ev
            return ev

        def reply(self, args):
            ev = self.pending.pop(str(args.get('to')), None)
            return 'sent' if ev else 'error: no message'

    class Toolbox:
        def __init__(self, tools, answers):
            self.tools, self.answers = tools, answers

        def call(self, name, args):
            if name == 'boom':
                raise RuntimeError('tool crashed')
            return self.answers[name]

    return types.SimpleNamespace(Mind=Mind, Toolbox=Toolbox, mk=types.SimpleNamespace(LOG_MAX=20_000_000))


def rows(p):
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def main():
    ak = fake_kit()
    with tempfile.TemporaryDirectory() as d:
        logs = Path(d) / 'logs'
        names = rec.install(ak, logs)
        assert names == ['LOG_MAX', 'Mind.deliver', 'Mind.reply', 'Toolbox.call'] and ak.mk.LOG_MAX == rec.NO_ROLLOVER
        m = ak.Mind()
        ev = types.SimpleNamespace(id='m1', kind='human', frm='a person in your app (u1)', text='쌀 있어요? 9만이면 살게요',
                                   data=None, task=None)
        assert m.deliver(ev) is ev                                        # the kit's own result passes through
        assert m.reply({'to': 'm1', 'text': '네, 있어요', 'state': 'completed'}) == 'sent'
        assert m.reply({'to': 'm9', 'text': 'x'}).startswith('error')     # failed replies are not recorded
        tb = ak.Toolbox({'hello': {'perm': {'handles': ['a2a']}}, 'buy_food_lot': {'perm': {}}},
                        {'hello': {'ok': True, 'result': {'reply': '안녕하세요', 'state': 'completed'}},
                         'buy_food_lot': {'ok': True, 'result': {'ok': False, 'error': 'quote exceeds conservative break-even ceiling',
                                                                  'total': 130000, 'max_total': 119000, 'units': [{'storage': 12}]}},
                         'market_info': {'ok': True, 'result': {'x': 1}}})
        out = tb.call('hello', {'text': '두부 있나요', 'claimed_from': 'shop-z', 'task_id': 't1', 'data': {'offer': 5000}})
        assert out['result']['reply'] == '안녕하세요'
        tb.call('buy_food_lot', {'sku': 'G1', 'qty': 2})
        tb.call('market_info', {})                                        # not a decision tool: no record
        msgs = rows(logs / 'messages.jsonl')
        assert [(r['dir'], r.get('via'), r['text']) for r in msgs] == [
            ('in', None, '쌀 있어요? 9만이면 살게요'), ('out', None, '네, 있어요'),
            ('in', 'hello', '두부 있나요'), ('out', 'hello', '안녕하세요')], msgs
        assert msgs[0]['kind'] == 'human' and msgs[1]['state'] == 'completed' and msgs[2]['data'] == {'offer': 5000}
        dec = rows(logs / 'decisions.jsonl')
        assert len(dec) == 1 and dec[0]['tool'] == 'buy_food_lot' and dec[0]['ok'] is False and dec[0]['quote_refused'] is True
        assert dec[0]['result']['total'] == 130000 and dec[0]['result']['units'] == [{'storage': 12}]
        paid = ak.Toolbox({'buy_food_lot': {'perm': {}}},             # paid, then the finalize step failed: not a refusal
                          {'buy_food_lot': {'ok': True, 'result': {'ok': False, 'error': 'finalize failed', 'total': 9000,
                                                                   'tx_hash': '0x' + 'b' * 64}}})
        paid.call('buy_food_lot', {'sku': 'G2', 'qty': 2})
        dec = rows(logs / 'decisions.jsonl')
        assert len(dec) == 2 and dec[1]['quote_refused'] is False and dec[1]['result']['tx_hash'].startswith('0xb')
        # a record that cannot be written never stops the kit
        (logs / 'messages.jsonl').unlink()
        (logs / 'messages.jsonl').mkdir()
        assert m.deliver(types.SimpleNamespace(id='m2', kind='a2a', frm='x', text='y', data=None, task=None)).id == 'm2'
        try:
            tb.call('boom', {})
        except RuntimeError:
            pass
        else:
            raise AssertionError('the tool error must reach the kit unchanged')
        assert rec._dir_arg(['--market', 'm', '--dir', d]) == Path(d).resolve()
        assert rec._clip('가' * 9000) == '가' * rec.TEXT_MAX
    print('records tests ok')


if __name__ == '__main__':
    main()
