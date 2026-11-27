"""python -I ops/test_checkout_customer_flow.py (market venv): the production kit app (install(checkout=True), the real
agent_app, its CheckoutResponse middleware, over HTTP) answered and then read exactly as the market's customer tool
reads it (house/customer.py talk() 325-345 and buy() 347-357, Q00/agent-market 710ac4d).

11/27 sig-reject RCA: the customer only got the checkout as text, its model copied an ~850-character token into
buy(checkout_jwt=...), and any changed character gives "not signed by this shop's wallet". With the checkout also in
the data part, buy({agent}) pays the same bytes we signed. Every other reply carries a data marker without a token, so
the customer's last_data never keeps an old checkout (buy({agent}) after a plain reply must not pay)."""
import asyncio
import base64
import hashlib
import json
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
KIT = HERE.parents[3] / 'scripts' / 'round4'
sys.path.insert(0, str(KIT if (KIT / 'agent_kit.py').exists() else Path.home() / 'ax-round4' / 'scripts' / 'round4'))
import agent_kit as ak                                      # noqa: E402
import checkout_delivery as cd                              # noqa: E402
import agent_kit_records as rec                             # noqa: E402
import maroo                                                # noqa: E402
import httpx                                                # noqa: E402
from eth_account import Account                             # noqa: E402
from eth_account.messages import encode_defunct             # noqa: E402

KEY = Account.from_key('0x' + '11' * 32)                    # a test key only
SHOP = KEY.address


def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def sign(payload):                                          # scripts/round4/market_kit.py jwt_sign
    head = b64url(canon({'alg': 'EIP191', 'typ': 'JWT'})) + '.' + b64url(canon(payload))
    sig = Account.sign_message(encode_defunct(primitive=head.encode()), KEY.key).signature
    return head + '.' + b64url(bytes(sig))


# ---- the market's customer tool, house/customer.py (only the parts that read our answer) ----
def jwt_payload(token, address):                            # customer.py 92-100
    try:
        head, body, sig = token.split('.')
        pad = lambda s: s + '=' * (-len(s) % 4)            # noqa: E731
        if not maroo.verify_bytes(address, base64.urlsafe_b64decode(pad(sig)), f'{head}.{body}'.encode()):
            return None
        return json.loads(base64.urlsafe_b64decode(pad(body)))
    except (ValueError, TypeError, AttributeError):
        return None


class Customer:
    def __init__(self, client):
        self.c, self.tasks, self.paid = client, {}, []

    async def talk(self, agent, text, new_conversation=False, v03=False):   # customer.py 325-345
        msg = {'messageId': hashlib.md5(f'{text}{time.time()}'.encode()).hexdigest(), 'role': 'ROLE_USER',
               'parts': [{'text': text}], 'metadata': {'from': 'buyer'}}
        if not new_conversation and self.tasks.get(agent):
            msg['taskId'] = self.tasks[agent]
        r = await self.c.post('/', json={'jsonrpc': '2.0', 'id': 1, 'method': 'SendMessage', 'params': {'message': msg}},
                              headers={'A2A-Version': '1.0'}, timeout=60)
        res = r.json()
        self.raw = res
        if 'error' in res:
            self.tasks.pop(agent, None)
            return f"error {res['error']}"
        task = res.get('result', {}).get('task') or res.get('result', {})
        state = (task.get('status') or {}).get('state', '')
        self.tasks[agent] = task.get('id') if 'COMPLETED' not in state and 'REJECTED' not in state else None
        said = ' '.join(p.get('text', '') for p in ((task.get('status') or {}).get('message') or {}).get('parts', []))
        data = [p['data'] for a in task.get('artifacts') or [] for p in a.get('parts', []) if 'data' in p]
        out = {'agent_says': said[:2000], 'state': state}
        if data:
            out['data'] = data[:3]
            self.last_data = (agent, data)
        return out

    def buy(self, args):                                    # customer.py 347-357 (settle recorded, not paid)
        agent = str(args.get('agent', ''))
        jwt, receipt = args.get('checkout_jwt'), args.get('receipt')
        if not jwt and not receipt and getattr(self, 'last_data', (None,))[0] == agent:
            for x in self.last_data[1]:
                if isinstance(x, dict):
                    jwt = jwt or x.get('checkout_jwt')
                    receipt = receipt or (x if x.get('sig_seller') else x.get('receipt'))
        if jwt:
            co = jwt_payload(jwt, SHOP)
            if not co:
                return "that checkout is not signed by this shop's wallet; not paying"
            self.paid.append(jwt)
            return 'paid'
        return 'nothing to pay'


# ---- the shop: real kit + wrapper, a model that issues on 결제서 and otherwise just answers ----
ISSUED = []


def complete(self, messages, max_tokens=4000, timeout=180):
    ctx = messages[1]['content']
    if '"id": "m' not in ctx:
        return json.dumps({'thought': 'idle', 'action': 'sleep', 'args': {'seconds': 3600}})
    m = re.search(r'"id": "(m[0-9a-f]+)", "from": "(?:[^"\\]|\\.)*", "text": "((?:[^"\\]|\\.)*)"', ctx)
    mid, text = m.group(1), json.loads('"' + m.group(2) + '"')   # Event.view(): this message's own text
    done = sum(1 for m in messages if m['role'] == 'assistant')
    if '데이터' in text:                                     # the model writes pay-looking keys in its own data
        plan = [('reply', {'to': mid, 'text': '여기 정보예요.',
                           'data': {'checkout_jwt': 'eyJhbGciOi.eyJmYWtl.c2ln', 'receipt': {'sig_seller': 'x'},
                                    'sig_seller': 'y', 'note': '상품 정보'}})]
    elif '결제서' in text:
        unit = (re.findall(r'u-[0-9a-f]{12}', text) or ['u-c654df1133b3'])[0]
        plan = [('private_checkout', {'op': 'create', 'unit': unit, 'reply_to': mid}),
                ('reply', {'to': mid, 'text': '배추김치 2kg B등급 결제서예요.' if unit == 'u-abcdefabcdef'
                           else '배추김치 2kg B 결제서예요.', 'state': 'input_required'})]
    else:
        plan = [('reply', {'to': mid, 'text': '네, 또 필요하시면 말씀해 주세요.'})]
    if done < len(plan):
        name, args = plan[done]
        return json.dumps({'thought': name, 'action': name, 'args': args})
    return json.dumps({'thought': 'done', 'action': 'sleep', 'args': {'seconds': 600}})


class Tools(ak.Toolbox):
    def __init__(self, root):
        self.root, self.dir, self.stats = root, root / 'tools', {}
        self.dir.mkdir()
        self.tools = {'private_checkout': {'perm': {'wallet': ['sign_jwt']}}, 'check_item_inventory': {'perm': {}}}
        self.ctx = {'tokens': {}, 'addresses': {'shop': SHOP}, 'name': 'nocha12'}
        self.lock = threading.Lock()

    def listing(self):
        return '\n'.join('- ' + n for n in self.tools)

    def run(self, name, mode, args=None, d=None):
        if name == 'check_item_inventory':
            return {'ok': True, 'result': {'ok': True, 'units': 1, 'skus': [{'sku': 'G1', 'name': '배추김치2kg', 'count': 1,
                                                                             'grades': {'B': {'n': 1, 'floor': 8000, 'price': 8534}}}]}}
        if name != 'private_checkout':
            return {'ok': True, 'result': {'ok': True}}
        pl = {'id': 'checkout', 'item': 'G1', 'price': 8534, 'claim': 'B', 'unit': args['unit'], 'order': args.get('order'),
              'task': args.get('inquiry_task'), 'iat': int(time.time()), 'n': len(ISSUED), 'cert': {'pad': 'x' * 450}}
        if args['unit'] == 'u-abcdefabcdef':
            pl['cert'] = {'cert': {'kind': 'food', 'unit': args['unit'], 'grade': None,
                                  'expires_at': pl['iat'] + 3600}}
        tok = sign(pl)
        ISSUED.append(tok)
        dg = hashlib.sha256(tok.encode()).digest()
        return {'ok': True, 'result': {'ok': True, 'order': args.get('order'), 'checkout_jwt': tok,
                                       'payload': pl, 'quote': 8534,
                                       'checkout_hash': b64url(dg)}}


FORCE = {'mismatch': False}
_tokens = cd.tokens


def tokens(text):                                           # a shape mismatch in the middleware, on demand
    if FORCE['mismatch']:
        raise ValueError('forced mismatch')
    return _tokens(text)


cd.tokens = tokens                                          # before install(): the middleware imports it there

ROOT = Path(tempfile.mkdtemp())
(ROOT / 'logs').mkdir()
ak.Model.complete = complete
rec.install(ak, ROOT / 'logs', checkout=True)
TOOLS = Tools(ROOT)
MIND = ak.Mind(ROOT, {'max_steps': 10, 'run_minutes': 1}, object.__new__(ak.Model), TOOLS,
               {'addresses': {'shop': SHOP}, 'name': 'nocha12', 'market': 'http://m', 'shop_url': None}, ['shop'])
threading.Thread(target=MIND.loop, daemon=True).start()
APP = ak.agent_app(ROOT, 'nocha12', MIND, TOOLS, ['http://shop.test'])


def run(coro):
    async def with_lifespan():                             # the SDK's event dispatcher starts with the app
        async with APP.router.lifespan_context(APP):
            return await coro
    return asyncio.run(with_lifespan())


class CustomerFlow(unittest.TestCase):
    def client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=APP), base_url='http://shop.test')

    def test_food_without_signed_grade_keeps_a2a_continuation(self):
        async def go():
            async with self.client() as client:
                return await Customer(client).talk('nocha12', '배추김치 u-abcdefabcdef 결제서 주세요')
        out = run(go())
        self.assertIn(cd.FOLLOWUP, out['agent_says'])
        self.assertNotIn('B등급', out['agent_says'])
        data = out['data'][0]
        self.assertEqual(data['checkout_jwt'], ISSUED[-1])
        self.assertEqual(data['next_message'], cd.FOLLOWUP)
        self.assertNotIn('claim', data)
        self.assertIn('until', data)

    def test_checkout_paid_from_data_without_copying(self):
        async def go():
            async with self.client() as c:
                cu = Customer(c)
                out = await cu.talk('nocha12', '배추김치 2kg 결제서 주세요')
                return cu, out
        n0 = len(ISSUED)
        cu, out = run(go())
        self.assertEqual(len(ISSUED), n0 + 1)
        tok = ISSUED[-1]
        self.assertIn(tok, out['agent_says'])                            # the text keeps the token (dual path)
        self.assertEqual([d.get('checkout_jwt') for d in out.get('data', [])], [tok])
        self.assertEqual(cu.buy({'agent': 'nocha12'}), 'paid')          # buy({agent}) only, as the tool says
        self.assertEqual(cu.paid, [tok])                                 # the very bytes we signed
        d = out['data'][0]
        self.assertEqual((d['item'], d['price'], d['claim'], d['unit']), ('G1', 8534, 'B', 'u-c654df1133b3'))
        # a copied token with one changed character still fails as before (args come first, customer.py 348)
        bad = tok[:200] + ('A' if tok[200] != 'A' else 'B') + tok[201:]
        self.assertIn('not signed', cu.buy({'agent': 'nocha12', 'checkout_jwt': bad}))

    HINT = '결제는 buy에 agent: nocha12만 넣으면 됩니다. 토큰은 옮겨 적지 않아도 돼요'   # check-1510 §6-1

    def test_checkout_and_reissue_replies_say_buy_with_agent_only(self):
        """11/27 12:47-14:59: 7 refusals, 0/7 after a reissue. Both replies carry the line; data parts as before."""
        async def go():
            async with self.client() as c:
                cu = Customer(c)
                first = await cu.talk('nocha12', '배추김치 2kg 결제서 주세요')
                t1 = ISSUED[-1]
                bad = t1[:300] + ('A' if t1[300] != 'A' else 'B') + t1[301:]
                refused = cu.buy({'agent': 'nocha12', 'checkout_jwt': bad})
                again = await cu.talk('nocha12', f"buy said: {refused}. 결제서 다시 주세요")
                t2 = ISSUED[-1]
                return cu, first, again, refused, t1, t2, cu.buy({'agent': 'nocha12'})
        cu, first, again, refused, t1, t2, paid = run(go())
        self.assertIn('not signed', refused)
        self.assertNotEqual(t1, t2)
        for out, tok in ((first, t1), (again, t2)):
            says = out['agent_says']
            self.assertEqual(says.count(self.HINT), 1, says[-300:])
            self.assertEqual(cd.tokens(says), {tok})                      # only the signed original, as before
            self.assertNotIn('eyJ', says.replace(tok, ''))
            self.assertLessEqual(len(says), 2000)
            d, = out['data'][:1]
            self.assertEqual(sorted(d), ['checkout_jwt', 'claim', 'item', 'order', 'price', 'unit'])   # data part unchanged
            self.assertEqual(d['checkout_jwt'], tok)
        self.assertEqual(paid, 'paid')
        self.assertEqual(cu.paid[-1], t2)

    def test_plain_reply_has_no_hint(self):
        async def go():
            async with self.client() as c:
                return await Customer(c).talk('nocha12', '안녕하세요, 뭐 파세요?', new_conversation=True)
        self.assertNotIn('buy에', run(go())['agent_says'])

    def test_plain_reply_after_checkout_does_not_pay_the_old_one(self):
        async def go():
            async with self.client() as c:
                cu = Customer(c)
                await cu.talk('nocha12', '배추김치 2kg 결제서 주세요')
                first = ISSUED[-1]
                same = await cu.talk('nocha12', '고마워요, 잠깐 생각해 볼게요')
                r1 = cu.buy({'agent': 'nocha12'})
                await cu.talk('nocha12', '다른 단위 u-0000aaaa1111 결제서 주세요', new_conversation=True)   # a fresh one pays ...
                r2 = cu.buy({'agent': 'nocha12'})
                second = ISSUED[-1]
                new = await cu.talk('nocha12', '안녕하세요, 뭐 파세요?', new_conversation=True)
                r3 = cu.buy({'agent': 'nocha12'})                      # ... and a new conversation's plain reply clears it
                return cu, same, new, r1, r2, r3, first, second
        cu, same, new, r1, r2, r3, first, second = run(go())
        self.assertNotEqual(first, second)
        for out in (same, new):
            self.assertTrue(out.get('data'), out)                         # a marker replaces last_data ...
            self.assertFalse(any('checkout_jwt' in x for x in out['data']), out['data'])   # ... and holds no token
            self.assertNotRegex(out['agent_says'], r'eyJ')
        self.assertEqual((r1, r2, r3), ('nothing to pay', 'paid', 'nothing to pay'))
        self.assertEqual(cu.paid, [second])

    def test_model_data_never_pays_and_ours_comes_first(self):
        """Review I1: the kit stores the model's data as a task artifact returned on every later answer; buy pays the
        first checkout_jwt found and the customer's model sees data[:3] only."""
        async def go():
            async with self.client() as c:
                cu = Customer(c)
                o1 = await cu.talk('nocha12', '상품 데이터 보여 주세요')
                r1 = cu.buy({'agent': 'nocha12'})
                o2 = await cu.talk('nocha12', '그럼 결제서 주세요')
                tok = ISSUED[-1]
                r2 = cu.buy({'agent': 'nocha12'})
                o3 = await cu.talk('nocha12', '고마워요')
                r3 = cu.buy({'agent': 'nocha12'})
                return cu, o1, o2, o3, r1, r2, r3, tok
        cu, o1, o2, o3, r1, r2, r3, tok = run(go())
        for out in (o1, o2, o3):
            for x in out['data']:
                if x.get('note') == '상품 정보':                        # the model's own data, still there ...
                    self.assertFalse({'checkout_jwt', 'receipt', 'sig_seller'} & set(x), x)   # ... without pay keys
        self.assertEqual(o2['data'][0].get('checkout_jwt'), tok)          # ours first, inside data[:3]
        self.assertIn('checkouts', o3['data'][0])
        self.assertEqual((r1, r2, r3), ('nothing to pay', 'paid', 'nothing to pay'))
        self.assertEqual(cu.paid, [tok])

    def test_shape_mismatch_falls_back_to_the_marker(self):
        """Review I2: when the planned rewrite cannot run, the SDK answer goes as it was plus the marker, so an
        earlier checkout in the customer's last_data is not paid."""
        async def go():
            async with self.client() as c:
                cu = Customer(c)
                await cu.talk('nocha12', '배추김치 결제서 주세요')
                FORCE['mismatch'] = True
                try:
                    out = await cu.talk('nocha12', '다른 단위 u-0000aaaa2222 결제서 주세요', new_conversation=True)
                finally:
                    FORCE['mismatch'] = False
                return cu, out, cu.buy({'agent': 'nocha12'})
        cu, out, r = run(go())
        self.assertEqual(out['data'], [{'checkouts': [], 'cancelled': []}])
        self.assertNotRegex(out['agent_says'], r'eyJ')
        self.assertEqual(r, 'nothing to pay')

    def test_get_task_is_not_marked(self):
        """Review M5: only SendMessage answers get the marker (no GetTask polls in checkout-sends.jsonl)."""
        async def go():
            async with self.client() as c:
                cu = Customer(c)
                await cu.talk('nocha12', '안녕하세요')
                tid = cu.raw['result']['task']['id']
                r = await c.post('/', json={'jsonrpc': '2.0', 'id': 2, 'method': 'GetTask', 'params': {'id': tid}},
                                 headers={'A2A-Version': '1.0'})
                return r.json()
        res = run(go())
        task = res.get('result', {}).get('task') or res.get('result', {})
        self.assertTrue(task.get('id'), res)
        self.assertFalse([a for a in task.get('artifacts') or [] if a.get('name') == 'checkout'], res)

    def test_v03_binding_parts_have_kind(self):
        async def go():
            async with self.client() as c:
                msg = {'messageId': 'v03' + str(time.time()), 'role': 'user', 'kind': 'message',
                       'parts': [{'kind': 'text', 'text': '안녕하세요'}]}
                r = await c.post('/', json={'jsonrpc': '2.0', 'id': 1, 'method': 'message/send', 'params': {'message': msg}},
                                 timeout=60)
                return r.json()
        res = run(go())
        task = res['result'].get('task') or res['result']
        parts = [p for a in task.get('artifacts') or [] for p in a.get('parts', [])]
        self.assertTrue(parts and all(p.get('kind') == 'data' and 'checkout_jwt' not in p['data'] for p in parts), res)

    def test_errors_and_other_paths_untouched(self):
        async def go():
            async with self.client() as c:
                bad = await c.post('/', json={'jsonrpc': '2.0', 'id': 1, 'method': 'NoSuch', 'params': {}},
                                   headers={'A2A-Version': '1.0'})
                card = await c.get('/.well-known/agent-card.json')
                return bad, card
        bad, card = run(go())
        self.assertIn('error', bad.json())
        self.assertNotIn('artifacts', json.dumps(bad.json()))
        self.assertIn(card.status_code, (200, 404))
        if card.status_code == 200:
            self.assertNotIn('checkouts', card.text)


if __name__ == '__main__':
    unittest.main()
