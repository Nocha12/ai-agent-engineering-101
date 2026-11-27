"""Runs each forged C-2 tool's test.py through the kit's forge_runner (python -I forge_runner.py <dir> test), the
same process and permission guard (guarded_open: every write must be in permission.write) a forge on the market host
uses, with the permission the installers forge. test_tools_src.py calls test.py in-process with a fake kit and so
cannot see a write outside permission.write (11/27 C-2 deploy: write_shop_card v8 'logs/quotes.test.json is not in
permission.write'). Offline: an empty throwaway root, a fake local signer (sign_jwt only, pay refused).
Run with the kit's python (the tools import httpx): ~/ax-round4-venv/bin/python -I ops/test_tools_forge_perm.py"""
import base64
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OPS = Path(__file__).resolve().parent
RUNNER = OPS.parents[3] / 'scripts' / 'round4' / 'forge_runner.py'
sys.path.insert(0, str(OPS))
import install_restock_shoutout  # noqa: E402  (stdlib at import; the kit is imported only in main())
import install_tools  # noqa: E402

TOOLS = ['private_checkout', 'check_item_inventory', 'write_shop_card', 'refresh_full_card', 'restock_shoutout']
MARK = '@@forge-result@@ '


def move(name):
    return install_restock_shoutout.move() if name == 'restock_shoutout' else install_tools.move(name)


def permission_yml(name, perm):
    """The lines agent_kit.write_permission writes (kit v5.1) for the keys forge_runner reads."""
    val = lambda v: '[' + ', '.join(str(x) for x in v) + ']' if isinstance(v, list) else \
        ('true' if v else 'false') if isinstance(v, bool) else str(v)
    lines = [f'name: {name}', 'description: x', 'permission:']
    for k in ('network', 'write', 'identity', 'wallet', 'pay_limit', 'timeout', 'handles', 'exec'):
        if k in perm and perm[k] not in (None, [], ''):
            lines.append(f'  {k}: {val(perm[k])}')
    return '\n'.join(lines + ['logic: logic.py', 'test: test.py', 'version: 1']) + '\n'


class Signer(BaseHTTPRequestHandler):
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get('content-length', '0'))))
        b = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip('=')
        out = {'result': b({'alg': 'EIP191'}) + '.' + b(req.get('payload')) + '.c2ln'} if req.get('op') == 'sign_jwt' \
            else {'error': 'the test signer only signs JWTs'}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header('content-length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def forge_test(name, root, signer):
    m = move(name)
    tool = Path(tempfile.mkdtemp(dir=root / 'tools'))
    (tool / 'permission.yml').write_text(permission_yml(name, m['permission']), encoding='utf-8')
    (tool / 'logic.py').write_text(m['logic'], encoding='utf-8')
    (tool / 'test.py').write_text(m['test'], encoding='utf-8')
    perm = m['permission']
    req = {'args': {}, 'market': 'https://market.ourolabs.ai', 'name': 'nocha12', 'root': str(root),
           'entities': ['buyer', 'shop'], 'addresses': {'shop': '0x' + 'ae' * 20, 'buyer': '0x' + 'be' * 20},
           'tokens': {r: 'offline' for r in perm.get('identity') or []},
           'signer': signer if perm.get('wallet') else None, 'cap': 'test' if perm.get('wallet') else None}
    p = subprocess.run([sys.executable, '-I', '-B', str(RUNNER), str(tool), 'test'], input=json.dumps(req),
                       capture_output=True, text=True, timeout=120, cwd=root)
    line = next((x for x in reversed(p.stdout.splitlines()) if x.startswith(MARK)), None)
    return json.loads(line[len(MARK):]) if line else {'ok': False, 'error': f'no result (exit {p.returncode})',
                                                        'trace': p.stderr[-1500:]}


class ForgePermission(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), Signer)
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()
        cls.signer = f'http://127.0.0.1:{cls.http.server_port}/'

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()

    def test_each_tool_test_passes_inside_its_permission(self):
        for name in TOOLS:
            with self.subTest(tool=name), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                (root / 'logs').mkdir()
                (root / 'tools').mkdir()
                out = forge_test(name, root, self.signer)
                self.assertTrue(out.get('ok'), f"{name}: {out.get('error')}\n{out.get('trace', '')}")
                real = [str(p.relative_to(root)) for p in root.rglob('*')
                        if p.is_file() and 'tools' not in p.parts and '.test.' not in p.name]
                self.assertEqual(real, [], f'{name} wrote real (non-test) records')

    def test_guard_refuses_a_write_outside_permission(self):   # the guard is really on in this harness
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'logs').mkdir()
            (root / 'tools').mkdir()
            tool = root / 'tools' / 'probe'
            tool.mkdir()
            (tool / 'permission.yml').write_text(permission_yml('probe', {'write': ['card.test.json']}), encoding='utf-8')
            (tool / 'logic.py').write_text('def run(args, kit):\n    return 1\n', encoding='utf-8')
            (tool / 'test.py').write_text("def test(run, kit):\n    open('logs/quotes.test.json', 'w').write('{}')\n",
                                          encoding='utf-8')
            req = {'root': str(root), 'signer': None}
            p = subprocess.run([sys.executable, '-I', '-B', str(RUNNER), str(tool), 'test'], input=json.dumps(req),
                               capture_output=True, text=True, timeout=30, cwd=root)
            out = json.loads(p.stdout.split(MARK)[-1])
            self.assertFalse(out['ok'])
            self.assertIn('logs/quotes.test.json is not in permission.write', out['error'])


if __name__ == '__main__':
    unittest.main()
