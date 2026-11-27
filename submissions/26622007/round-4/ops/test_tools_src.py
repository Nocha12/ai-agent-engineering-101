"""Offline harness: runs each ops/tools_src tool's test.py against its logic.py with a fake kit.

python3 -I ops/test_tools_src.py   (from anywhere; runs in a throwaway directory)
"""
import importlib.util
import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'tools_src')
SHOP = '0x' + 'ae' * 20


class FakeKit:
    testing = True
    name = 'nocha12'
    shop_url = 'https://nocha.example'
    market = 'https://market.ourolabs.ai'

    def address(self, role):
        return SHOP

    def sign_jwt(self, payload, role):
        import base64, json
        b = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip('=')
        return b({'alg': 'ES256K'}) + '.' + b(payload) + '.c2ln'

    def token(self, role):
        return 'x'

    def pay(self, *a, **k):
        raise AssertionError('test mode must not pay')


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    tools = sorted(d for d in os.listdir(SRC) if os.path.isdir(os.path.join(SRC, d)) and not d.startswith('_'))
    fails = 0
    with tempfile.TemporaryDirectory() as root:
        os.chdir(root)
        os.makedirs('logs')
        for t in tools:
            logic = load(os.path.join(SRC, t, 'logic.py'), t + '_logic')
            test = load(os.path.join(SRC, t, 'test.py'), t + '_test')
            try:
                out = test.test(logic.run, FakeKit())
                assert out == 'ok', out
                leftovers = [f for f in os.listdir('logs') if '.test.' in f]
                assert not leftovers, leftovers
                real = [f for f in os.listdir('logs') if '.test.' not in f]
                assert not real, ('test touched real records', real)
                print('PASS', t)
            except Exception:
                fails += 1
                print('FAIL', t)
                traceback.print_exc()
    print('tools', len(tools), 'fail', fails)
    # the refund tools' chain user-agent (f19827cf) is checked here too, so this suite keeps it (review M3)
    import subprocess
    ua = subprocess.run([sys.executable, '-I', '-B', os.path.join(HERE, 'test_refund_rpc_ua.py')])
    print('PASS' if ua.returncode == 0 else 'FAIL', 'refund_rpc_ua')
    sys.exit(1 if fails or ua.returncode else 0)


if __name__ == '__main__':
    main()
