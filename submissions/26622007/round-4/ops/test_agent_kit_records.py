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

        def step_log(self, entry):
            steps.append(entry)

    class Toolbox:
        def __init__(self, tools, answers):
            self.tools, self.answers = tools, answers

        def call(self, name, args):
            if name == 'boom':
                raise RuntimeError('tool crashed')
            return self.answers[name]

    steps, lines = [], []
    return types.SimpleNamespace(Mind=Mind, Toolbox=Toolbox, mk=types.SimpleNamespace(LOG_MAX=20_000_000),
                                 log=lines.append, steps=steps, lines=lines)


def rows(p):
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def contact_masking(ak, logs):
    """A contact a customer left for a restock notice (restock_shoutout op contact) is in waitlist.json only: the
    kit's trajectory, its log and the local records carry a mask instead (대표님 11/25 07:42; review af47eb54 I1 I2)."""
    m = ak.Mind()
    jin = "an agent that says it is '진주댁-1a2b' (unverified: anyone can claim a name), over A2A"
    said = '콩나물 없으면 포럼 우유사랑-9f3c 로 불러 주세요'
    m.deliver(types.SimpleNamespace(id='m7', kind='a2a', frm=jin, text=said, data=None, task='t7'))
    # what the kit and the tools had already written before anyone knew this was a contact
    react, kitlog, lost = logs / 'react.jsonl', logs / 'agent-kit.log', logs / 'lost_deals.jsonl'
    react.write_text(json.dumps({'thought': '손님이 우유사랑-9f3c 로 불러 달라고 했다', 'action': 'restock_shoutout',
                                 'args': {'op': 'want', 'sku': 'MILK', 'last_message': said}, 'observation': '{}'}, ensure_ascii=False) + '\n', encoding='utf-8')
    kitlog.write_text('07:59:14 [mind] restock_shoutout | 우유사랑-9f3c 로 불러 달라고 함\n07:59:15 [market] tick\n', encoding='utf-8')
    lost.write_text(json.dumps({'customer': '진주댁-1a2b', 'last_message': said}, ensure_ascii=False) + '\n', encoding='utf-8')
    sizes = [p.stat().st_size for p in (react, kitlog, lost, logs / 'messages.jsonl')]
    tb = ak.Toolbox({'restock_shoutout': {'perm': {}}, 'reply_helper': {'perm': {}}},
                    {'restock_shoutout': {'ok': True, 'result': {'ok': True, 'saved': True}}})
    args = {'op': 'contact', 'customer': jin, 'sku': 'MILK', 'contact': '@우유사랑-9f3c'}
    assert tb.call('restock_shoutout', args) == {'ok': True, 'result': {'ok': True, 'saved': True}}     # the tool gets it as given
    assert args['contact'] == '@우유사랑-9f3c'
    m.step_log({'thought': '손님이 우유사랑-9f3c 를 남겼다', 'action': 'restock_shoutout', 'args': args, 'observation': '{"ok": true}'})
    ak.log('[mind] restock_shoutout | 연락처 우유사랑-9f3c 저장')
    assert m.reply({'to': 'm7', 'text': '네, 우유사랑-9f3c 님으로 적어 두었어요'}) == 'sent'
    files = [p.read_text(encoding='utf-8') for p in (react, kitlog, lost, logs / 'messages.jsonl')]
    seen = json.dumps([ak.steps, ak.lines], ensure_ascii=False) + ''.join(files)
    assert '우유사랑' not in seen, seen
    assert ak.steps[-1]['args'] == {'op': 'contact', 'customer': jin, 'sku': 'MILK', 'contact': rec.MASK}   # the rest of the step is kept
    # the files were masked in place: same size, same lines, every row still JSON, nothing else touched
    assert [p.stat().st_size for p in (react, kitlog, lost)] == sizes[:3]
    assert files[1].endswith('07:59:15 [market] tick\n') and '진주댁-1a2b' in files[3] and '콩나물 없으면 포럼' in files[3]
    for text in (files[0], files[2], files[3]):
        for line in text.splitlines():
            json.loads(line)
    # a contact that is just the customer's own market name is not a new secret: only the argument is masked
    tb.call('restock_shoutout', {'op': 'contact', 'customer': jin, 'sku': 'RICE', 'contact': '진주댁-1a2b'})
    ak.log('[market] 진주댁-1a2b asked for rice')
    assert ak.lines[-1] == '[market] 진주댁-1a2b asked for rice'
    # a crafted "contact" cannot blank the records: words the records are made of, and non-names, mask nothing else
    before = [p.read_bytes() for p in (react, kitlog, lost, logs / 'messages.jsonl')]
    for crafted in ('text', 'restock_shoutout', 'reply_helper', '": "', 'a2a', 'x' * 400, '{"dir', '철'):
        tb.call('restock_shoutout', {'op': 'contact', 'customer': jin, 'sku': 'RICE', 'contact': crafted})
        m.step_log({'thought': 'text a2a restock_shoutout', 'action': 'restock_shoutout',
                    'args': {'op': 'contact', 'contact': crafted, 'sku': 'RICE'}, 'observation': 'text'})
        assert ak.steps[-1] == {'thought': 'text a2a restock_shoutout', 'action': 'restock_shoutout',
                                'args': {'op': 'contact', 'contact': rec.MASK, 'sku': 'RICE'}, 'observation': 'text'}, ak.steps[-1]
    assert [p.read_bytes() for p in (react, kitlog, lost, logs / 'messages.jsonl')] == before
    # re-review 41b2dfbd I-2: nothing is searched for before the tool has taken the value as a contact
    refused = ak.Toolbox({'restock_shoutout': {'perm': {}}}, {'restock_shoutout': {'ok': True, 'result': {'ok': False, 'saved': False}}})
    lost.write_text(lost.read_text(encoding='utf-8') + json.dumps({'reason': '재고없음', 'item': '콩나물', 'sku': 'G1a2b3c4d'}, ensure_ascii=False) + '\n', encoding='utf-8')
    before = [p.read_bytes() for p in (react, kitlog, lost, logs / 'messages.jsonl')]
    for word in ('재고없음', '콩나물'):
        refused.call('restock_shoutout', {'op': 'contact', 'customer': jin, 'sku': 'RICE', 'contact': word})
        ak.log('[mind] log_lost_deal | 재고없음 콩나물')
        assert ak.lines[-1] == '[mind] log_lost_deal | 재고없음 콩나물'
    tb.call('restock_shoutout', {'op': 'contact', 'customer': jin, 'sku': 'RICE', 'contact': 'G1a2b3c4d'})   # a SKU id is no name
    assert [p.read_bytes() for p in (react, kitlog, lost, logs / 'messages.jsonl')] == before
    # I-1, I-3: JSON escapes in the rows already written: a newline then "oah" is no "noah" (and its backslash
    # must stay), a newline then "yms979" is "yms979"
    m.deliver(types.SimpleNamespace(id='m8', kind='a2a', frm=jin, text='hi\noah 그리고 a\tom', data=None, task='t8'))
    m.deliver(types.SimpleNamespace(id='m9', kind='a2a', frm=jin, text='제 포럼 이름은\nyms979 입니다\tyms979 맞아요 yms979', data=None, task='t9'))
    for word in ('noah', 'tom', 'yms979'):
        tb.call('restock_shoutout', {'op': 'contact', 'customer': jin, 'sku': 'RICE', 'contact': word})
    rows = [json.loads(line) for line in (logs / 'messages.jsonl').read_text(encoding='utf-8').splitlines()]   # every row still JSON
    texts = [r.get('text') for r in rows]
    assert 'hi\noah 그리고 a\tom' in texts, texts                                     # a newline then 'oah': no 'noah' was written
    assert not any('yms979' in str(t) for t in texts), texts
    # a two-letter Korean name is a contact the tool takes: it is masked too
    tb.call('restock_shoutout', {'op': 'contact', 'customer': jin, 'sku': 'EGG', 'contact': '철수'})
    ak.log('[mind] reply | 철수 님 연락처로 적었다')
    assert '철수' not in ak.lines[-1], ak.lines[-1]
    # the mask is for that conversation's minutes, not for good
    clock = rec.time.time
    rec.time.time = lambda: clock() + rec.MASK_SECONDS + 1
    try:
        ak.log('[market] 철수 is a common word again')
        assert ak.lines[-1] == '[market] 철수 is a common word again'
    finally:
        rec.time.time = clock


def main():
    ak = fake_kit()
    with tempfile.TemporaryDirectory() as d:
        logs = Path(d) / 'logs'
        names = rec.install(ak, logs)
        assert names == ['LOG_MAX', 'Mind.deliver', 'Mind.reply', 'Toolbox.call', 'Mind.step_log', 'log'], names
        assert ak.mk.LOG_MAX == rec.NO_ROLLOVER
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
        contact_masking(ak, logs)
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
    missed_messages()
    assert rec.held_result({'ok': False, 'code': 'no_quote', 'error': 'RAW TOOL OUTPUT'})['code'] == 'no_quote'
    assert 'RAW' not in str(rec.held_result({'ok': False, 'code': 'no_quote', 'error': 'RAW TOOL OUTPUT'}))
    held_longest()
    print('records tests ok')


def held_longest():
    """메인 11/27 18:10 (7f2b4297): A held, C offered, the customer left C out and was told only to ask again. unit_held
    now names the unit with the most time left and when the held checkout ends (KST); alternatives as before."""
    until = 1795768200                                   # 11/27 17:30 KST
    r = rec.held_result({'ok': False, 'code': 'unit_held', 'error': 'RAW', 'held_until': until, 'alternatives': [
        {'unit': 'u-c1', 'grade': 'C', 'floor': 2000, 'hours_left': 6.5},
        {'unit': 'u-b1', 'grade': 'B', 'floor': 3000, 'hours_left': 40.0},
        {'unit': 'u-b2', 'grade': 'B', 'floor': 3100, 'hours_left': 30.0}]})
    assert r['held_until'] == until and r['held_until_kst'] == '11/27 17:30 KST', r
    assert r['longest']['unit'] == 'u-b1', r
    assert [x['unit'] for x in r['alternatives']] == ['u-c1', 'u-b1', 'u-b2']   # order unchanged
    assert '11/27 17:30 KST까지 예약돼' in r['error'] and 'longest' in r['error'] and 'RAW' not in str(r)
    assert 'held_until_kst' not in r['error'] and '다른 손님 이야기는 하지 않음' in r['error']
    undated = rec.held_result({'ok': False, 'code': 'unit_held', 'held_until': until, 'alternatives': [
        {'unit': 'u-b1', 'grade': 'B', 'floor': 3000, 'hours_left': 40.0},
        {'unit': 'u-n1', 'grade': 'B', 'floor': 3000, 'hours_left': None}]})
    assert undated['longest']['unit'] == 'u-n1'           # no date reads as the longest
    # review C1/I1/M4: longest is never C or under LONG_MIN; a tie goes to A; up to the minute, never earlier
    def longest(alts):
        return rec.held_result({'ok': False, 'code': 'unit_held', 'held_until': until, 'alternatives': alts})
    cb = longest([{'unit': 'u-c1', 'grade': 'C', 'floor': 2000, 'hours_left': None},
                  {'unit': 'u-b1', 'grade': 'B', 'floor': 3000, 'hours_left': 40.0}])
    assert cb['longest']['unit'] == 'u-b1', cb
    conly = longest([{'unit': 'u-c1', 'grade': 'C', 'floor': 2000, 'hours_left': 6.0}])   # 33647847
    assert conly['longest'] is None and 'C·임박을 뺀 단위가 없습니다' in conly['error'], conly
    assert [x['unit'] for x in conly['alternatives']] == ['u-c1']                    # still offered when asked
    short = longest([{'unit': 'u-b1', 'grade': 'B', 'floor': 3000, 'hours_left': 11.9}])
    assert short['longest'] is None
    tie = longest([{'unit': 'u-b1', 'grade': 'B', 'floor': 3000, 'hours_left': 30.0},
                   {'unit': 'u-a1', 'grade': 'A', 'floor': 3500, 'hours_left': 30.0}])
    assert tie['longest']['unit'] == 'u-a1'
    assert rec.held_kst(until + 59) == '11/27 17:31 KST' and rec.held_kst(True) is None and rec.held_kst(None) is None
    # r2 C1: a unit on clearance (떨이, up to 24 h left) is never longest; the flag and the expiry reach the model
    now = 1795767000.0                                   # 11/27 17:10 KST
    cl = rec.held_result({'ok': False, 'code': 'unit_held', 'held_until': until, 'alternatives': [
        {'unit': 'u-b1', 'grade': 'B', 'floor': 2500, 'hours_left': 18.0, 'clearance': {'price': 2600, 'floor': 2500}},
        {'unit': 'u-b2', 'grade': 'B', 'floor': 3000, 'hours_left': 14.0}]}, now=now)
    assert cl['longest']['unit'] == 'u-b2', cl
    assert cl['alternatives'][0]['clearance'] is True and cl['alternatives'][1]['clearance'] is False
    assert cl['alternatives'][0]['until_kst'] == '11/28 11시 KST'          # hour only, as with_until (r3 I1)
    assert cl['alternatives'][0]['clearance_price'] == 2600 and 'clearance_price' not in cl['alternatives'][1]
    # r3 I1: 39.96 h shows as 40.0 h; the hour is never later than the real expiry (11/29 09:07 -> 09시)
    assert rec.alt_until(40.0, now) == '11/29 09시 KST' and rec.alt_until(0.04, now) is None
    only_cl = rec.held_result({'ok': False, 'code': 'unit_held', 'held_until': until, 'alternatives': [
        {'unit': 'u-b1', 'grade': 'B', 'floor': 2500, 'hours_left': 18.0, 'clearance': {'price': 2600}}]}, now=now)
    assert only_cl['longest'] is None and '떨이 단위(clearance true)' in only_cl['error']
    assert 'check_item_inventory {sku}로 다른 단위가 있는지 확인' in only_cl['error']   # r3 I2: the 6 are the cheapest
    six = rec.held_result({'ok': False, 'code': 'unit_held', 'held_until': until, 'alternatives': [
        {'unit': f'u-c{i}', 'grade': 'C', 'floor': 2000 + i, 'hours_left': 5.0} for i in range(6)] + [
        {'unit': 'u-a1', 'grade': 'A', 'floor': 4000, 'hours_left': 100.0}]}, now=now)
    assert len(six['alternatives']) == 6 and six['longest'] is None          # the A unit past the 6th is not listed:
    assert 'check_item_inventory {sku}로 다른 단위가 있는지 확인' in six['error']   # the model looks before saying none
    # r2 M3: an expired or odd hours_left has no until_kst; alternatives keep their order for every customer
    odd = rec.held_result({'ok': False, 'code': 'unit_held', 'held_until': until, 'alternatives': [
        {'unit': 'u-c1', 'grade': 'C', 'floor': 2000, 'hours_left': -1.0},
        {'unit': 'u-b1', 'grade': 'B', 'floor': 3000, 'hours_left': 40.0}]}, now=now)
    assert odd['alternatives'][0]['until_kst'] is None and [x['unit'] for x in odd['alternatives']] == ['u-c1', 'u-b1']
    assert odd['alternatives'][1]['until_kst'] == '11/29 09시 KST'
    none = rec.held_result({'ok': False, 'code': 'unit_held', 'held_until': until, 'alternatives': []})
    assert none['longest'] is None and none['held_until_kst'] == '11/27 17:30 KST' and 'longest' not in none['error']
    bad = rec.held_result({'ok': False, 'code': 'unit_held', 'held_until': 'x', 'alternatives': []})
    assert bad['held_until'] is None and bad['held_until_kst'] is None
    assert '예약돼' not in bad['error'] and 'None' not in bad['error']   # r2 I1: no time, no time sentence
    rw = rec.held_result({'ok': False, 'code': 'reissue_wait', 'held_until': until})
    assert rw['held_until_kst'] == '11/27 17:30 KST'


def missed_messages():
    """11/26 10:01·11:24: a message taken by fresh() after a long tool result was cut off at OBS_LIMIT and never seen."""
    import threading
    ak = fake_kit()
    seen_by_model = []

    class Model:
        def complete(self, messages, max_tokens=4000):
            seen_by_model.append(messages[-1]['content'])
            return 'raw'

    class Ev:
        def __init__(self, i, task):
            self.id, self.task, self.done = i, task, threading.Event()

        def view(self):
            return {'id': self.id, 'text': '쌀 5kg 찾습니다', 'task_id': self.task}

    def fresh(self):
        out, self.inbox = self.inbox, []
        return out

    def run_once(self, events):                 # the kit's order: tool text + new messages, then cut at OBS_LIMIT
        messages = [{'role': 'user', 'content': 'context'}]
        self.model.complete(messages)
        text = 'x' * 5920                       # the cut lands inside the message: its id is seen, its text is not
        new = self.fresh()
        if new:
            text += '\n\nNew messages waiting for you (answer with reply):\n' + json.dumps([e.view() for e in new], ensure_ascii=False)
        messages.append({'role': 'user', 'content': 'Observation: ' + text[:6000]})
        self.model.complete(messages)
        short = self.fresh()
        messages.append({'role': 'user', 'content': 'Observation: ok\n\nNew messages waiting for you (answer with reply):\n'
                                                    + json.dumps([e.view() for e in short], ensure_ascii=False)})
        self.model.complete(messages)

    ak.Mind.fresh, ak.Mind.run_once, ak.Model = fresh, run_once, Model
    with tempfile.TemporaryDirectory() as d:
        logs = Path(d) / 'logs'
        names = rec.install(ak, logs)
        assert names[-3:] == ['Mind.fresh', 'Mind.run_once', 'Model.complete'] or 'Model.complete' in names, names
        m = ak.Mind()
        m.model = Model()
        lost, fine = Ev('mlost1', 'ea45d5f0'), Ev('mfine2', 't2')
        m.inbox = [lost]
        orig = ak.Mind.fresh
        calls = [0]

        def fresh_twice(self):                  # first step: the lost one; second step: one that fits
            calls[0] += 1
            if calls[0] == 2:
                self.inbox = [fine]
            return orig(self)
        ak.Mind.fresh = fresh_twice
        m.run_once([])
        assert 'mlost1' not in seen_by_model[0]
        assert '"id": "mlost1"' in seen_by_model[1][:6013]                       # the id survived the cut (r3 I2)
        assert seen_by_model[1].count('쌀 5kg 찾습니다') == 1, seen_by_model[1][-300:]   # the whole view, once
        assert seen_by_model[2].count('mfine2') == 1 and 'mlost1' not in seen_by_model[2]   # shown once, not again
        r = rows(logs / 'missed-messages.jsonl')
        assert r == [dict(r[0], kind='readded_after_cut', ids=['mlost1'], tasks=['ea45d5f0'])], r
        done = Ev('mdone3', 't3')
        done.done.set()
        m.inbox = [done]
        ak.Mind.fresh = orig
        m.run_once([])
        assert seen_by_model[4].count('mdone3') <= 1 and len(rows(logs / 'missed-messages.jsonl')) == 1   # answered: no re-add
        # r2 I-4: taken in the run's last step with no model call after it: back to the queue for the next run
        import queue
        q = queue.Queue()
        late = Ev('mlate4', 't4')

        def fresh_q(self):
            out = []
            while not q.empty():
                out.append(q.get_nowait())
            return out

        def run_last(self, events):
            q.put(late)
            self.fresh()                        # the kit's last step: fresh(), observation appended, loop ends

        ak.Mind.fresh, m.inbox = fresh_q, q
        rec_run = ak.Mind.run_once
        assert rec_run is not run_once
        # re-install over these two so the wrapper sees them
        ak.Mind.run_once = run_last
        rec.install(ak, logs)
        m.run_once([])
        assert q.qsize() == 1 and q.get_nowait() is late, q.qsize()


if __name__ == '__main__':
    main()
