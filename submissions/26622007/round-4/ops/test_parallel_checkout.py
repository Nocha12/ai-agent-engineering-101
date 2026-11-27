"""python3 -I ops/test_parallel_checkout.py: the parallel runs with the checkout wrapper as it runs in production
(install(checkout=True)): two customers ask for the last unit at once through the wrapper's own private_checkout path
(reply_to, order = the inquiry's task, holds, unit_held answer) and one gets it; a customer's run never answers or
issues for another customer's message, and never buys stock, forges a tool or posts on the forum (review r1 I1-I3)."""
import base64
import hashlib
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
import checkout_delivery as cd                              # noqa: E402
import agent_kit_records as rec                             # noqa: E402

WINDOW = 8.0
SEEN = {}                                                   # task -> what the run got back from each move


class Req:                                                  # the HTTP inquiry the wrapper ties a checkout to
    open, answer = True, None


class Ev(ak.Event):
    """An A2A message that came over HTTP: the wrapper's deliver sets checkout_request from the request context,
    which a test has not got; this one keeps its own."""
    def __init__(self, *a):
        self._req = Req()
        super().__init__(*a)

    @property
    def checkout_request(self):
        return self._req

    @checkout_request.setter
    def checkout_request(self, v):
        if v is not None:
            self._req = v


def complete(self, messages, max_tokens=4000, timeout=180):
    time.sleep(0.3)
    ctx = messages[1]['content']
    if 'task t-slow' in ctx and not any(m['role'] == 'assistant' for m in messages):
        time.sleep(5)                                       # a parallel run still thinking
    if '"id": "m' not in ctx:
        return json.dumps({'thought': 'idle', 'action': 'sleep', 'args': {'seconds': 3600}})
    mid = re.search(r'"id": "(m[0-9a-f]+)"', ctx).group(1)
    task = re.search(r'task (t-[a-z0-9-]+)', ctx).group(1)
    unit = re.search(r'unit (U\d)', ctx).group(1)
    other = re.search(r'other (m[0-9a-f]+)', ctx)
    done = sum(1 for m in messages if m['role'] == 'assistant')
    if done:
        SEEN.setdefault(task, []).append(messages[-1]['content'][:300])
    plan = []
    if other:                                               # a run that tries what a customer's run must not do
        plan = [('buy_food_lot', {'sku': 'G1', 'qty': 1}), ('forum_post', {'text': 'x'}), ('forge_tool', {'name': 'x'}),
                ('private_checkout', {'op': 'create', 'unit': unit, 'reply_to': other.group(1)}),
                ('reply', {'to': other.group(1), 'text': 'not yours'})]
    if 'twice' in ctx:                                      # a second unit of the same item right after the first
        plan = [('private_checkout', {'op': 'create', 'unit': 'U5', 'reply_to': mid})]
    plan += [('private_checkout', {'op': 'create', 'unit': unit, 'reply_to': mid}), ('reply', {'to': mid, 'text': f'{unit} A등급 결제서예요' if task == 't-null' else f'{unit} 결제서예요'})]
    if done < len(plan):
        name, args = plan[done]
        return json.dumps({'thought': name, 'action': name, 'args': args})
    return json.dumps({'thought': 'done', 'action': 'sleep', 'args': {'seconds': 600}})


class Tools(ak.Toolbox):
    def __init__(self, root):
        self.root, self.dir, self.stats = root, root / 'tools', {}
        self.dir.mkdir()
        self.tools = {'private_checkout': {'perm': {'wallet': ['sign_jwt']}}, 'check_item_inventory': {},
                      'buy_food_lot': {'perm': {'wallet': ['pay']}}, 'forum_post': {}}
        self.held, self.issued, self.calls = {}, [], []

    def listing(self):
        return '\n'.join('- ' + n for n in self.tools)

    def run(self, name, mode, args=None, d=None):
        self.calls.append(name)
        if name == 'check_item_inventory':
            return {'ok': True, 'result': {'ok': True, 'units': 2, 'skus': [{'sku': 'G1', 'name': '연두부300g×5팩', 'count': 1,
                                                                             'grades': {'A': {'n': 1, 'floor': 4000, 'price': 5000}}}]}}
        if name != 'private_checkout':
            return {'ok': True, 'result': {'ok': True}}
        u, holder = args['unit'], args.get('inquiry_task')
        free = self.held.get(u) in (None, holder)           # read the live inventory and the holds ...
        time.sleep(0.3)                                     # ... which takes a moment ...
        if not free:
            return {'ok': True, 'result': {'ok': False, 'code': 'unit_held', 'alternatives': [], 'held_until': int(time.time()) + 300}}
        self.held[u] = holder                               # ... then the issue is logged
        self.issued.append((u, args.get('order'), holder))
        payload = {'iat': int(time.time()), 'price': 5000, 'order': args.get('order')}
        if holder == 't-null':
            unit = 'u-f5a2635c268b'
            payload.update(unit=unit, claim='A', cert={'cert': {'kind': 'food', 'unit': unit, 'grade': None,
                           'expires_at': int(time.time()) + 3600}})
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
        tok = f'eyJhbGciOiJFZERTQSJ9.{encoded}.c2ln'
        dg = hashlib.sha256(tok.encode()).digest()
        dup = {'duplicate_of': holder} if str(args.get('order')).endswith('-2') else {}
        # 't-badcert': a certificate grade_note turns down (claim not A/B/C), so the data part has no certificate line
        claim = '?' if args.get('order') == 't-badcert' else 'A'
        return {'ok': True, 'result': {'ok': True, 'order': args.get('order'), 'checkout_jwt': tok, **dup,
                                       'payload': payload, 'quote': 5000,
                                       **({} if dup else {'grade_evidence': {'unit': 'u-f5a2635c268b', 'claim': claim, 'supplied_at':
                                           int(time.time()) - 3600, 'expires_at': int(time.time()) + 30 * 3600}}),
                                       'checkout_hash': base64.urlsafe_b64encode(dg).decode().rstrip('=')}}


def ask(mind, text, task, out):
    ev = Ev('a2a', 'buyer', text, None, task)
    mind.deliver(ev)
    ok = ev.done.wait(WINDOW)
    out[task] = (ok, (ev.answer or {}).get('reply'), ev)
    ev.done.set()


def main():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / 'logs').mkdir()
        ak.Model.complete = complete
        names = rec.install(ak, root / 'logs', checkout=True)
        assert 'Mind.deliver(parallel)' in names and 'Toolbox.forge' in names, names
        tools = Tools(root)
        mind = ak.Mind(root, {'max_steps': 10, 'run_minutes': 1}, object.__new__(ak.Model), tools,
                       {'addresses': {'shop': '0x' + 'ae' * 20}, 'name': 'nocha12', 'market': 'http://m', 'shop_url': None},
                       ['shop'])
        threading.Thread(target=mind.loop, daemon=True).start()
        # two customers, the last unit U1, at once, through the production checkout path
        out = {}
        ts = [threading.Thread(target=ask, args=(mind, f'연두부 unit U1 결제서 task {t}', t, out)) for t in ('t-a', 't-b')]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert all(out[t][0] and out[t][1] for t in ('t-a', 't-b')), out
        assert [u for u, _, _ in tools.issued] == ['U1'], tools.issued                 # one customer got it
        u, order, holder = tools.issued[0]
        assert order == holder and holder in ('t-a', 't-b'), tools.issued             # order = that inquiry's task
        loser = 't-b' if holder == 't-a' else 't-a'
        assert any('unit_held' in x for x in SEEN[loser]), SEEN[loser]
        # 11/27 01daff51 / 0b2461b6: the issue result carries the signed price and expiry minute and says to use them
        win = [x for x in SEEN[holder] if '"order"' in x and '"ok": true' in x]
        assert win and '"price": 5000' in win[0] and '"until": "' in win[0] and '가격 5,000크레딧' in win[0], SEEN[holder]
        assert re.search(r'"until": "\d\d/\d\d \d\d:\d\d KST"', win[0]) and '그대로' in win[0], win[0]
        assert 'checkout_jwt' not in win[0] and 'expires_at' not in win[0], win[0]
        rows = [json.loads(x) for x in (root / 'logs' / 'checkouts.jsonl').read_text(encoding='utf-8').splitlines()]
        assert [r['task'] for r in rows if r.get('event') == 'delivery_bound'] == [holder], rows
        # a customer's run: no buying, posting or forging; no checkout or reply for another customer's message
        other = Ev('a2a', 'buyer2', 'someone else', None, 't-other')
        mind.pending[other.id] = other                      # waiting in another conversation
        o2 = {}
        ask(mind, f'unit U2 주세요 task t-rogue other {other.id}', 't-rogue', o2)
        got = SEEN['t-rogue']
        assert all('not used in a customer run' in got[i] for i in range(3)), got[:3]
        assert 'reply only to this run' in got[3] and 'reply only to this run' in got[4] and other.id not in got[4], got[3:5]
        assert not other.done.is_set() and other.answer is None and other.checkout_request.answer is None
        assert o2['t-rogue'][0] and 'U2' in o2['t-rogue'][1], o2
        assert 'buy_food_lot' not in tools.calls and 'forum_post' not in tools.calls, tools.calls
        assert [x for x in tools.issued if x[2] == 't-other'] == [], tools.issued
        other.done.set()
        # the kit's own loop (any run that is not a parallel one) never answers or issues for a conversation a parallel
        # run serves (r2 I1): this thread is such a run
        o3 = {}
        th = threading.Thread(target=ask, args=(mind, 'unit U2 주세요 task t-slow', 't-slow', o3))
        th.start()
        time.sleep(1)
        slow = next(e for e in list(mind.pending.values()) if e.task == 't-slow')
        kl = Ev('a2a', 'buyer3', f'unit U3 주세요 task t-kitloop other {slow.id}', None, 't-kitloop')
        mind.pending[kl.id] = kl
        ak.Mind.run_once(mind, [kl])                        # a run of the kit's loop, in this thread
        got = SEEN['t-kitloop']
        assert 'another run' in got[3] and 'another run' in got[4], got[3:5]
        assert not slow.done.is_set() and not [x for x in tools.issued if x[2] == 't-slow'], tools.issued
        assert kl.done.is_set() and kl.answer['reply'] == 'U3 결제서예요', kl.answer
        th.join()
        assert o3['t-slow'][0] and o3['t-slow'][1] == 'U2 결제서예요', o3
        # 3b: a second checkout of the same item and grade in 30 s is issued and flagged, with the order ids to keep one
        o4 = {}
        ask(mind, 'unit U6 두 번 task t-dup twice', 't-dup', o4)
        got = SEEN['t-dup']
        assert '"duplicate_of": "t-dup"' in got[1] and 'checkout_cancel' in got[1] and 't-dup-2' in got[1], got[1]
        # review 1 M1: no certificate line in the data part (no grade_evidence) -> the price only, no signed expiry
        assert '"price": 5000' in got[1] and '"until"' not in got[1] and '소비기한' not in got[1].split('reply_note')[-1][:60], got[1]
        # review 2 N1: grade_evidence is there but grade_note turns it down (no certificate line in the data part) ->
        # the price only; without the kept.note check (4a62cf8e) the signed expiry would be named
        o5 = {}
        ask(mind, 'unit U7 주세요 task t-badcert', 't-badcert', o5)
        got = SEEN['t-badcert']
        assert '"order": "t-badcert"' in got[0] and '"price": 5000' in got[0], got[0]
        assert '"until"' not in got[0] and '소비기한' not in got[0].split('reply_note')[-1][:60], got[0]
        o6 = {}
        ask(mind, 'unit U8 주세요 task t-null', 't-null', o6)
        ok, body, ev6 = o6['t-null']
        # check-0010 review C1: the wrapper keeps the model's prose; the HTTP answer's compose() puts the signed terms,
        # the claim grade and the certificate line in front of it once (end to end in test_checkout_customer_flow)
        assert ok and body == 'U8 A등급 결제서예요', body
        said, _ = cd.compose(body, ev6.checkout_issued, set(), time.time(), agent='nocha12')
        assert '가격 5,000크레딧' in said and '소비기한' in said and 'A등급(가게 서명)' in said, said
        assert said.count(cd.CERT_GRADE_LINE) == 1 and said.index(cd.CERT_GRADE_LINE) < said.index(body), said
        assert 'grade_evidence' not in SEEN['t-null'][0] and '"until"' in SEEN['t-null'][0], SEEN['t-null']
        # The SDK signals the waiter before the wrapper writes the outbound log.
        log = root / 'logs' / 'messages.jsonl'
        until = time.monotonic() + 2
        while True:
            sent = [json.loads(line) for line in log.read_text().splitlines()]
            if any(r.get('task') == 't-null' and r.get('dir') == 'out' and r.get('text') == body for r in sent):
                break
            assert time.monotonic() < until, body
            time.sleep(0.02)
        print('parallel checkout ok', out['t-a'][1], '/', out['t-b'][1])


if __name__ == '__main__':
    main()
