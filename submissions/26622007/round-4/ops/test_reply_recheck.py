"""python3 -I ops/test_reply_recheck.py: right before a reply goes, the items it names are looked up live one by one
and compared with what the model was told (대표님 11/27; review I1, I2): a shop of 70 SKUs whose whole-shop answer has no
names, a price that moves (back once, then sent with the new values), a first quote (not a change), a late reply."""
import json
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
KIT = HERE.parents[3] / 'scripts' / 'round4'
sys.path.insert(0, str(KIT if (KIT / 'agent_kit.py').exists() else Path.home() / 'ax-round4' / 'scripts' / 'round4'))
import agent_kit as ak                                      # noqa: E402
import agent_kit_records as rec                             # noqa: E402

WINDOW = 8.0
SKUS = 70                                                   # the live shop has 66 (11/27 02:08)


def complete(self, messages, max_tokens=4000, timeout=180):
    """The kit's Model.complete, faked: look the asked SKU up, reply with its price, write again when sent back."""
    time.sleep(0.2)
    ctx = messages[1]['content']
    if '"id": "m' not in ctx:
        return json.dumps({'thought': 'idle', 'action': 'sleep', 'args': {'seconds': 3600}})
    mid = re.search(r'"id": "(m[0-9a-f]+)"', ctx).group(1)
    sku = re.search(r'sku (G\d+)', ctx).group(1)
    done = sum(1 for m in messages if m['role'] == 'assistant')
    last = messages[-1]['content']
    label = {'G2': '계란20구', 'G3': '연두부300g×5팩'}[sku]
    if done == 0:
        return json.dumps({'thought': 'look', 'action': 'check_item_inventory', 'args': {'sku': sku}})
    if 'error: not sent' in last:
        price = re.findall(r'(\d[\d,]*)크레딧', last)[-1]
        return json.dumps({'thought': 'again', 'action': 'reply', 'args': {'to': mid, 'text': f'{label} {price}원이에요 (다시)'}})
    if done == 1 or (done == 2 and 'sent' not in last):
        seen = re.findall(r'"price": (\d+)', last)
        return json.dumps({'thought': 'answer', 'action': 'reply',
                           'args': {'to': mid, 'text': f"{label} {int(seen[0]) if seen else 0:,}원이에요"}})
    return json.dumps({'thought': 'done', 'action': 'sleep', 'args': {'seconds': 600}})


class Tools(ak.Toolbox):
    def __init__(self, root):
        self.root, self.dir, self.stats = root, root / 'tools', {}
        self.dir.mkdir()
        self.tools = {'check_item_inventory': {}}
        self.calls, self.price, self.bump, self.quote_g3 = [], 7000, 0, None

    def listing(self):
        return '- check_item_inventory'

    def run(self, name, mode, args=None, d=None):
        self.calls.append(dict(args or {}))
        sku = (args or {}).get('sku')
        if sku:                                             # one item, as the tool answers it (shown names)
            if sku == 'G2' and sum(1 for c in self.calls if c.get('sku') == 'G2') > 1:
                self.price += self.bump                     # another customer's quote moved it meanwhile
            price = self.price if sku == 'G2' else self.quote_g3
            name = {'G2': '계란20구', 'G3': '연두부300g×5팩'}.get(sku, '생수2L×6병')
            if sku == 'G3' and self.quote_g3 is None:
                self.quote_g3 = 4500                        # set by this look-up's quote (a first price)
            return {'ok': True, 'result': {'ok': True, 'sku': sku, 'count': 2, 'more': 0, 'items': [
                {'unit': 'u1', 'sku': sku, 'name': name, 'grade': 'B', 'price': price},
                {'unit': 'u2', 'sku': sku, 'name': name, 'grade': 'B', 'price': price}]}}
        skus = [{'sku': f'G{i}', 'count': 2, 'grades': {'B': {'n': 2, 'floor': 4000, **({'price': self.price} if i == 2 else {})}}}
                for i in range(1, SKUS + 1)]                # 70 SKUs: names left out, as the tool does past ~32
        return {'ok': True, 'result': {'ok': True, 'units': SKUS * 2, 'skus': skus}}


def ask(mind, text, task, back=0):
    ev = ak.Event('a2a', 'buyer', text, None, task)
    ev.at -= back
    mind.deliver(ev)
    ok = ev.done.wait(WINDOW)
    ev.done.set()
    return ok, (ev.answer or {}).get('reply')


def rows(root):
    p = root / 'logs' / 'missed-messages.jsonl'
    return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []


def main():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / 'logs').mkdir()
        ak.Model.complete = complete
        rec.install(ak, root / 'logs')
        tools = Tools(root)
        mind = ak.Mind(root, {'max_steps': 6, 'run_minutes': 1}, object.__new__(ak.Model), tools,
                       {'addresses': {'shop': '0x' + 'ae' * 20}, 'name': 'nocha12', 'market': 'http://m', 'shop_url': None},
                       ['shop'])
        threading.Thread(target=mind.loop, daemon=True).start()
        # 1) nothing moved: sent as written, one single look-up of the named item, no second whole-shop list
        ok, text = ask(mind, '계란 sku G2 얼마예요', 't-same')
        assert ok and text == '계란20구 7,000원이에요', text
        whole = [c for c in tools.calls if not c.get('sku')]
        assert len(whole) == 1 and [c for c in tools.calls if c.get('sku')] == [{'sku': 'G2'}] * 2, tools.calls
        # 2) the price moves on every look-up: back once with 7,500, then (once is the limit) sent with 8,000 added
        tools.calls.clear()
        tools.bump = 500
        ok, text = ask(mind, '계란 sku G2 다시 얼마', 't-moved')
        assert ok and text.startswith('계란20구 7,500원이에요 (다시)') and '(보내기 직전 다시 확인한 재고)' in text \
            and '8,000크레딧' in text, text
        moved = [r for r in rows(root) if r['tasks'] == ['t-moved']]
        assert [r['sent_with_note'] for r in moved] == [False, True] and all(r['looked_up'] == 1 for r in moved), moved
        assert not [c for c in tools.calls if not c.get('sku')][1:], tools.calls       # rechecks: one item each
        # 3) a first price (null when told, set now) is not a change: sent as written
        n0 = len(rows(root))
        ok, text = ask(mind, '연두부 sku G3 있어요?', 't-first')
        assert ok and text, text
        assert len(rows(root)) == n0, rows(root)[n0:]
        # 4) the customer has little of the window left: no look-ups, sent as written (review r1 I2)
        tools.bump, n1 = 500, len(rows(root))
        calls0 = len([c for c in tools.calls if c.get('sku')])
        ok, text = ask(mind, '계란 sku G2 지금 얼마', 't-late', back=60)
        assert ok and '(보내기 직전' not in text and len(rows(root)) == n1, (text, rows(root)[n1:])
        assert len([c for c in tools.calls if c.get('sku')]) - calls0 == 1, tools.calls       # the model's own only
        # 5) the whole-shop list (no names: 70 SKUs) comes again before every model call: the name the model's own
        # look-up gave is kept, so the reply is still checked (review r1 I1)
        rec.STOCK_EVERY, tools.bump = 0, 500
        ok, text = ask(mind, '계란 sku G2 또 얼마', 't-refresh')
        rec.STOCK_EVERY = 20
        moved = [r for r in rows(root) if r['tasks'] == ['t-refresh']]
        assert ok and moved and moved[0]['looked_up'] == 1, (text, moved)
        # helpers: first price not a change, sold out, fewer left not; a word with every size of the name (r1 I3, M3)
        assert rec.stock_changes({'count': 2, 'prices': {'B': None}}, {'count': 2, 'prices': {'B': 4500}}) == []
        assert rec.stock_changes({'count': 2, 'prices': {}}, {'count': 0, 'prices': {}}) == ['지금 재고 없음']
        assert rec.stock_changes({'count': 5, 'prices': {'B': 7000}}, {'count': 4, 'prices': {'B': 7000}}) == []   # r1 I3
        assert not rec.named_in('계란 있어요', 'G2', '계란20구') and rec.named_in('계란 20구 있어요', 'G2', '계란20구')
        assert not rec.named_in('사과드립니다', 'G5', '사과2kg') and rec.named_in('G5 있어요', 'G5', None)
        told = rec.merge_told({'G2': {'name': '계란20구', 'count': 2, 'prices': {'B': 7000}}},
                              rec.told_from({'ok': True, 'skus': [{'sku': 'G2', 'count': 2, 'grades': {'B': {'n': 2}}}]}))
        assert told['G2']['name'] == '계란20구' and told['G2']['prices'] == {'B': 7000}, told
        one = rec.told_from({'ok': True, 'sku': 'X9', 'count': 0, 'items': [], 'more': 0}, by_id=True)
        assert one == {'X9': {'name': None, 'count': 0, 'prices': {}}}, one                 # r1 M2: any id
        gone = {'ok': True, 'sku': 'G2', 'count': 0, 'items': [], 'more': 0,
                'note': 'nothing in stock matches that; check the SKU or name'}             # the tool's real no-stock answer
        assert rec.told_from(gone, by_id=True) == {'G2': {'name': None, 'count': 0, 'prices': {}}}   # r3 I1
        assert rec.told_from(gone) == {}                                                   # a name asked: no SKU
        old = {'name': '계란20구', 'count': 3, 'prices': {'B': 7000}}
        assert rec.stock_changes(old, rec.merge_told({'G2': dict(old)}, rec.told_from(gone, by_id=True))['G2']), 'sold out'
        big = rec.told_from({'ok': True, 'sku': 'G2', 'count': 25, 'more': 5, 'items': [{'sku': 'G2', 'grade': 'B', 'price': 1}]})
        assert big['G2']['count'] == 25, big
        kept = rec.merge_told({'G2': {'name': '계란20구', 'count': 3, 'prices': {}}},
                              {'G2': {'name': '계란20구', 'count': None, 'prices': {}}})
        assert kept['G2']['count'] == 3, kept                                              # r2 I1: by-name has no count
        assert not rec.named_in('계란 120구 있어요', 'G2', '계란20구')                       # r2 M1
        miss = rec.told_from({'ok': True, 'sku': '두부', 'count': 0, 'items': [], 'similar': [{'sku': 'G3'}], 'note': 'x'})
        assert rec.told_from({'ok': True, 'sku': '두부', 'count': 0, 'items': []}) == {}
        assert miss == {}, miss                                                            # r2 M3: a name is no SKU
        print('reply recheck ok:', len(tools.calls), 'look-ups')


if __name__ == '__main__':
    main()
