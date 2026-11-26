"""python3 -I ops/test_parallel_inquiries.py: customers who write at the same time are served at the same time by the
kit's own Mind.run_once (scripts/round4/agent_kit.py, not a fake), and the last unit of an item goes to one of them only.
대표님 11/27 00:55; 11/26 23:14 728bb36b waited 98 s behind c2f80b1a and got no answer."""
import json
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
KIT = HERE.parents[3] / 'scripts' / 'round4'          # the repo layout; a copy outside it uses ~/ax-round4
sys.path.insert(0, str(KIT if (KIT / 'agent_kit.py').exists() else Path.home() / 'ax-round4' / 'scripts' / 'round4'))
import agent_kit as ak                                      # noqa: E402
import agent_kit_records as rec                             # noqa: E402

STEP = 1.0          # s per model call
WINDOW = 6.0        # s a customer waits here (the kit's A2A_WAIT is 80 s; a step takes 5-20 s there)


PROMPTS = []


def complete(self, messages, max_tokens=4000, timeout=180):
        """The kit's Model.complete, faked: per conversation, private_checkout the unit the customer named, reply
        with what came back, sleep."""
        PROMPTS.append([m['content'] for m in messages])
        time.sleep(STEP)
        ctx = messages[1]['content']
        if '"id": "m' not in ctx:                       # the kit's own timer run: nothing waiting
            return json.dumps({'thought': 'idle', 'action': 'sleep', 'args': {'seconds': 3600}})
        mid = re.search(r'"id": "(m[0-9a-f]+)"', ctx).group(1)
        unit = re.search(r'unit (U\d)', ctx).group(1)
        done = sum(1 for m in messages if m['role'] == 'assistant')
        last = messages[-1]['content']
        item = '연두부' if unit in ('U1', 'U2') else '계란'
        if done == 0:
            return json.dumps({'thought': 'issue', 'action': 'private_checkout', 'args': {'op': 'create', 'unit': unit}})
        if done == 1:
            got = 'held' if 'unit_held' in last else 'issued'
            seen = re.findall(r'"price": (\d+)', [m['content'] for m in messages if 'Live stock now (' in m['content']][-1])
            return json.dumps({'thought': 'answer', 'action': 'reply', 'args': {'to': mid, 'text': f'{unit} {got} {item} {seen}'}})
        return json.dumps({'thought': 'done', 'action': 'sleep', 'args': {'seconds': 600}})


class Tools(ak.Toolbox):
    """The kit's Toolbox.call (stats, index.json) over a market of four units; create checks then holds, with a gap."""
    def __init__(self, root):
        self.root, self.dir, self.stats = root, root / 'tools', {}
        self.dir.mkdir()
        self.tools = {'private_checkout': {}, 'check_item_inventory': {}}
        self.held, self.stock_calls, self.issued = set(), 0, []
        self.inside = self.most = 0
        self.price = {'G1': 5000, 'G2': 7000}

    def listing(self):
        return '- private_checkout\n- check_item_inventory'

    def run(self, name, mode, args=None, d=None):
        if name != 'private_checkout' or args.get('op') == 'settle':
            if name != 'check_item_inventory':
                self.inside += 1                # calls inside at once
                self.most = max(self.most, self.inside)
                time.sleep(0.2)
                self.inside -= 1
                return {'ok': True, 'result': {'ok': True}}
        if name == 'check_item_inventory':
            self.stock_calls += 1
            return {'ok': True, 'result': {'ok': True, 'units': 4, 'skus': [
                {'sku': 'G1', 'name': '연두부 300g×5팩', 'count': 2, 'grades': {'A': {'n': 2, 'floor': 4000, 'price': self.price['G1']}}},
                {'sku': 'G2', 'name': '한계란 난각번호 1번 계란 무항생제 유정란 20구', 'count': 2, 'grades': {'B': {'n': 2, 'floor': 6000, 'price': self.price['G2']}}}]}}
        u = args['unit']
        free = u not in self.held       # read ...
        time.sleep(0.3)                 # ... the live inventory and the log take a moment ...
        if not free:
            return {'ok': True, 'result': {'ok': False, 'code': 'unit_held'}}
        self.held.add(u)                # ... then the issue is logged
        self.issued.append(u)
        return {'ok': True, 'result': {'ok': True, 'order': 'o-' + u}}


def ask(mind, text, task, answers):
    ev = mind.deliver(ak.Event('a2a', 'buyer', text, None, task))
    t0 = time.time()
    ok = ev.done.wait(WINDOW)
    answers[task] = (ok and ev.answer is not None, round(time.time() - t0, 1), (ev.answer or {}).get('reply'))
    ev.done.set()                       # the kit's busy answer


def scene(mind, asks):
    answers, ts = {}, []
    for text, task in asks:
        ts.append(threading.Thread(target=ask, args=(mind, text, task, answers)))
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return answers


def main():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / 'logs').mkdir()
        ak.Model.complete = complete                    # before install: the wrapper wraps it as it wraps the real one
        names = rec.install(ak, root / 'logs')
        assert 'Mind.deliver(parallel)' in names and 'Model.complete' in names, names
        tools, model = Tools(root), object.__new__(ak.Model)
        mind = ak.Mind(root, {'max_steps': 6, 'run_minutes': 1}, model, tools,
                       {'addresses': {'shop': '0x' + 'ae' * 20}, 'name': 'nocha12', 'market': 'http://m', 'shop_url': None},
                       ['shop'])
        threading.Thread(target=mind.loop, daemon=True).start()
        # two customers want the last unit U1 at the same moment
        a = scene(mind, [('연두부 unit U1 주세요', 't-last-1'), ('연두부 unit U1 결제서', 't-last-2')])
        assert all(ok for ok, _, _ in a.values()), a                    # both answered inside the window
        assert tools.issued.count('U1') == 1, tools.issued              # the last unit went to one customer only
        assert sorted(r.split(" 연두부")[0] for _, _, r in a.values()) == ['U1 held', 'U1 issued'], a
        # two customers, two different items, at the same moment
        b = scene(mind, [('unit U2 있어요?', 't-two-1'), ('unit U3 주세요', 't-two-2')])
        assert all(ok for ok, _, _ in b.values()), b
        assert sorted(r.split(" ")[0] + ' ' + r.split(" ")[1] for _, _, r in b.values()) == ['U2 issued', 'U3 issued'], b
        # each run read the live stock before its first move and was told to state stock only from it
        firsts = [p for p in PROMPTS if 'Live stock now' in p[-1]]
        assert len(firsts) >= 4 and tools.stock_calls >= 4, (len(firsts), tools.stock_calls)
        served = [p for p in PROMPTS if '"id": "m' in p[1]]
        assert len(served) >= 8 and all('never a value from your notes' in p[1] for p in served), 'focus note missing'
        assert not any('never a value from your notes' in p[1] for p in PROMPTS if p not in served)   # the kit's loop: unchanged
        # a customer served by the kit's own loop (a timer run that takes a message) gets live stock too (review I2)
        before = tools.stock_calls
        loop_mind = ak.Mind(root, {'max_steps': 6, 'run_minutes': 1}, mind.model, tools, mind.ctx, ['shop'])
        ev = ak.Event('a2a', 'buyer', 'unit U4 주세요', None, 't-loop')
        loop_mind.pending[ev.id] = ev
        ak.Mind.run_once(loop_mind, [ev])
        mine = [p for p in PROMPTS if 'unit U4' in p[1]]            # by content: workers may still be going to sleep
        assert tools.stock_calls > before and mine and 'Live stock now' in mine[0][-1], tools.stock_calls
        assert not any('never a value from your notes' in p[1] for p in mine)
        # one run, one live lookup: the reply goes as written, no second lookup before it (the reply-time check is
        # not in this release; main 11/27 01:29)
        tools.held = set()
        calls0 = tools.stock_calls
        c = scene(mind, [('계란 unit U4 주세요', 't-one')])
        assert c['t-one'][0] and c['t-one'][2].startswith('U4 issued'), c
        assert tools.stock_calls - calls0 == 1, tools.stock_calls - calls0
        # every paying tool runs one call at a time (one nonce, one balance; review C1), named or by permission.wallet
        # pay, on its own lock (r1 M1); checkouts on theirs; a tool without a wallet does not wait for them
        tools.tools.update({'refund_send': {'perm': {'wallet': ['pay']}}, 'forged_pay': {'perm': {'wallet': ['pay']}},
                            'buy_food_lot': {'perm': {}}, 'forum_read': {'perm': {'network': ['m']}}})
        for names_, overlap in ((['refund_send', 'forged_pay', 'buy_food_lot'], False),           # one send at a time
                                (['private_checkout', 'private_checkout'], False),                 # one checkout at a time
                                (['private_checkout', 'refund_send'], True),     # a 180 s send never holds a checkout
                                (['forum_read', 'forum_read', 'forum_read'], True)):
            tools.inside, tools.most = 0, 0
            ts = [threading.Thread(target=tools.call, args=(n, {'op': 'settle', 'unit': 'U9'})) for n in names_]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            assert (tools.most > 1) is overlap, (names_, tools.most)
        print('parallel ok', {k: v[:2] for k, v in {**a, **b}.items()})


if __name__ == '__main__':
    main()
