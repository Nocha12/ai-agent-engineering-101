"""The refund tools' chain calls carry a user-agent: rpc-testnet.maroo.io answers urllib's default one with HTTP 403
(11/27, so refund_check_payer and refund_send could not read the chain), the same reason private_checkout sends
'ax-shop/1.0'. Runs each rpc() with testing off and urlopen replaced; nothing leaves this computer.
python3 -I ops/test_refund_rpc_ua.py"""
import importlib.util
import io
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = 'ax-shop/1.0'


class LiveKit:
    testing = False


def load(tool):
    spec = importlib.util.spec_from_file_location(tool + '_ua', os.path.join(HERE, 'tools_src', tool, 'logic.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        return io.BytesIO(json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': '0x1'}).encode())

    real = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        for tool in ('refund_check_payer', 'refund_send'):
            seen.clear()
            m = load(tool)
            assert m.rpc(LiveKit(), {}, 'eth_blockNumber', []) == '0x1', tool
            assert len(seen) == 1, (tool, seen)
            req = seen[0]
            assert req.full_url == m.RPC, (tool, req.full_url)
            assert req.get_header('User-agent') == UA, (tool, req.header_items())
            assert req.get_header('Content-type') == 'application/json', (tool, req.header_items())
            assert json.loads(req.data)['method'] == 'eth_blockNumber', tool
        pc = open(os.path.join(HERE, 'tools_src', 'private_checkout', 'logic.py'), encoding='utf-8').read()
        assert f"'user-agent': '{UA}'" in pc                    # the same value private_checkout already sends
    finally:
        urllib.request.urlopen = real
    print('ok')


if __name__ == '__main__':
    main()
