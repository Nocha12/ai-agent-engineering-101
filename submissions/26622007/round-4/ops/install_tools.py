"""Forge the operator's week-13 tools (ops/tools_src) into tools/ through the kit's own forge path (same test run,
permission check and forge.json hashes as a forge by the agent), and retire the agent's refund_order v1 (it read the
sealed ledger answer as one object; the market returns a list). Run on the market host while the kit is stopped:
    ~/ax-round4-venv/bin/python -I ops/install_tools.py --kit ~/ax-round4/scripts/round4
--print only prints the forge_tool arguments.

Refund: refund_check_order -> refund_check_payer -> refund_send, once per order. The amount is the shop's net receipt
(settle amount - shop fee; REFUND_BASIS in refund_check_order/logic.py). refund_send pays exactly that order's amount;
pay_limit below is only the kit's fixed outer ceiling per call (the kit has no per-order cap)."""
import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / 'tools_src'
MARKET, RPC = 'market.ourolabs.ai', 'rpc-testnet.maroo.io'


def w(*names):
    return [n for x in names for n in (x, x.replace('.jsonl', '.test.jsonl'))]


TOOLS = {
    'refund_check_order': (
        '환불 1단계(읽기): 우리 주문인지, 보증 안 고장인지, 아직 환불 안 했는지 장부로 확인합니다. 결제는 됐는데 정산이 안 된 주문도 찾습니다. '
        '금액은 가게 실수령액(정산액 - 판매 수수료). args {order}. 다음은 refund_check_payer.',
        {'network': [MARKET], 'write': w('logs/refund-plans.jsonl') + ['logs/refunds.test.jsonl'],   # the test's fixture
         'identity': ['shop'], 'timeout': 60}),
    'refund_check_payer': (
        '환불 2단계(읽기): 원래 결제 트랜잭션에서 보낸 사람과 우리 지갑에 들어온 금액을 체인으로 확인합니다. args {order}. 다음은 refund_send.',
        {'network': [RPC], 'write': w('logs/refund-plans.jsonl'), 'timeout': 30}),
    'refund_send': (
        '환불 3단계(송금): 30분 안의 1·2단계 확인이 맞을 때만 그 주문 하나에 한 번, 확인된 금액만 원래 보낸 사람에게 보내고 기록합니다. args {order}.',
        {'network': [RPC], 'write': w('logs/sends.jsonl', 'logs/refunds.jsonl') + ['logs/refund-plans.test.jsonl'],
         'identity': ['shop'], 'wallet': ['pay'],
         'pay_limit': 200000, 'timeout': 180}),
    'private_checkout': (
        '비공개 결제서 발급(create)과 정산(settle). 발급 전에 실재고·다른 결제서 예약·등급(상품명·인증서)·서명 지갑=가게 지갑·하한가를 확인합니다. '
        'args create {op, unit, price, task, warranty_hours, claim, order?, endgame?}, '
        'settle {op, order, checkout_jwt, checkout_mandate, payment_mandate}. 결과에 refund:true가 있으면 환불 3단계로 갑니다.',
        {'network': [MARKET], 'write': w('logs/checkouts.jsonl', 'logs/sales.jsonl'), 'identity': ['shop'],
         'wallet': ['sign_jwt'], 'timeout': 90}),
}
RETIRE = ['refund_order']


def move(name):
    desc, perm = TOOLS[name]
    return {'name': name, 'description': desc, 'permission': perm,
            'logic': (SRC / name / 'logic.py').read_text(encoding='utf-8'),
            'test': (SRC / name / 'test.py').read_text(encoding='utf-8')}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kit', help='the directory with agent_kit.py')
    ap.add_argument('--print', action='store_true')
    a = ap.parse_args()
    if a.print or not a.kit:
        print(json.dumps([move(n) for n in TOOLS], ensure_ascii=False, indent=1))
        return
    sys.path.insert(0, str(Path(a.kit).expanduser().resolve()))
    import agent_kit as ak                                  # noqa: E402
    import maroo                                            # noqa: E402
    d = HERE.parent
    ak.mk.LOG_FILE = d / 'logs' / 'agent-kit.log'
    keys = {r: maroo.Wallet.load(d / f'{r}_wallet.key') for r in ('shop', 'buyer')}
    ctx = {'market': 'https://market.ourolabs.ai', 'name': 'nocha12', 'tokens': {'shop': 'offline', 'buyer': 'offline'},
           'addresses': {r: k.address for r, k in keys.items()}, 'signer': ak.Signer(keys, d)}
    box = ak.Toolbox(d, ctx)
    ok = True
    for n in TOOLS:
        out = box.forge(move(n))
        print(n, json.dumps(out, ensure_ascii=False))
        ok = ok and isinstance(out, dict) and out.get('test') == 'passed'
    if not ok:
        print('a test failed: refund_order was left in place')
        sys.exit(1)
    for n in RETIRE:
        src = d / 'tools' / n
        if src.exists():
            ver = json.loads((src / 'forge.json').read_text()).get('version', 1) if (src / 'forge.json').exists() else 1
            keep = d / 'tools' / '.deprecated' / f'{n}@{ver}'
            keep.parent.mkdir(exist_ok=True)
            shutil.rmtree(keep, ignore_errors=True)
            shutil.move(str(src), str(keep))
            print('retired', n, '->', keep.relative_to(d))


if __name__ == '__main__':
    main()
