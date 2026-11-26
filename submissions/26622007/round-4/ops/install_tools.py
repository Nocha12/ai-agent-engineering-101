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
        'args create {op, unit, price?, task, warranty_hours, claim, order?, endgame?}, '
        'settle {op, order, checkout_jwt, checkout_mandate, payment_mandate}. 결과에 refund:true가 있으면 환불 3단계로 갑니다. '
        'price를 빼면 그 품목·등급 안내가(check_item_inventory의 price)로 냅니다. 처음 낸 가격이 안내가가 되고, 결과 quote가 안내가입니다. '
        '떨이(기한 임박) unit은 price를 빼면 떨이가로 내고, 하한도 남은 시간만큼 낮아 원가 아래로 낼 수 있습니다(결과 clearance).',
        {'network': [MARKET, RPC], 'write': w('logs/checkouts.jsonl', 'logs/sales.jsonl') + ['logs/refunds.test.jsonl'] + ['logs/quotes.json', 'logs/quotes.json.tmp', 'logs/quotes.test.json', 'logs/quotes.test.json.tmp'],
         'identity': ['shop'],
         'wallet': ['sign_jwt'], 'timeout': 90}),
    'buy_food_lot': (
        '식품 SKU 1종을 매입합니다. args {sku, qty(1~4)}. 확인은 대표님 최소 기준뿐: 식품만, price_ceiling 손익분기 상한 이하의 견적(예상 판매가>원가+수수료), 품목당 4개. '
        '이미 가진 재고가 있어도 매입할 수 있습니다. 손님이 찾았는데 재고 0이던 식품은 food_candidates의 customer_asked_out_of_stock에서 SKU를 고릅니다. 송금 뒤 같은 견적으로 매입 확정을 '
        '여러 번 시도하고, 끝내 확정이 안 되면 다시 송금하지 않고 기록합니다(그 뒤 새 매입은 거절). '
        '나중에 args {confirm: 견적 ID}(송금 결과를 모르면 tx_hash도)로 확정만 다시 합니다.',
        {'network': [MARKET, RPC], 'write': w('logs/sends.jsonl', 'logs/supply-pending.jsonl'),
         'identity': ['shop'], 'wallet': ['pay'], 'pay_limit': 500000, 'timeout': 180}),
    'food_candidates': (
        '매입 후보 조회(읽기만). customer_asked_out_of_stock: 최근 24시간 손님이 찾았는데 재고 0이던 식품과 그에 맞는 카탈로그 SKU(buy_options, 시장 판매 많은 순). '
        'candidates: 생필품 식품 카탈로그. 공급 창이 열리면 customer_asked_out_of_stock의 SKU부터 buy_food_lot으로 삽니다. args 없음.',
        {'network': [MARKET], 'identity': ['shop'], 'timeout': 20}),
    'write_shop_card': (
        '가게 카드를 정해진 이름·설명으로 작성하고 현재 재고의 모든 품목을 짧은 이름과 등급별 가격으로 표시합니다. '
        'args {stock?: {sku: {등급: 가격}}}. 현재 재고와 등급, 하한가를 검증합니다. '
        '떨이·기한 임박 재고는 고정 설명 바로 뒤에 떨이가로 자동으로 붙습니다.',
        {'network': [MARKET], 'write': ['card.json', 'card.test.json'], 'identity': ['shop'], 'timeout': 20}),
    'check_item_inventory': (
        '우리 가게 실제 재고 전체를 잘리지 않게 봅니다(로그인 /api/inventory). args {}: 품목마다 이름·개수와 등급별 개수(n)·하한가(floor)·남은 시간(hours_left). '
        'args {sku: SKU ID나 품목 이름(예: 콩나물)}: 그 품목의 unit ID·등급·원가·하한가·남은 시간. 소비기한이 지난 unit은 재고로 세지 않습니다. '
        '조회가 실패하면 ok:false이고 재고 0이 아닙니다. price는 품목·등급 안내가(같은 단위는 이 값 하나로 안내, 높은 등급이 같거나 비쌈). '
        '안내가가 없으면 args {sku, quote: {등급: 가격}}로 정하고, 바꿀 때만 requote: true. '
        '떨이·기한 임박 unit은 clearance {price, floor}(전체 조회는 맨 앞 clearance 목록): 그 price로 먼저 안내합니다.',
        {'network': [MARKET], 'write': ['logs/quotes.json', 'logs/quotes.json.tmp', 'logs/quotes.test.json', 'logs/quotes.test.json.tmp'], 'identity': ['shop'], 'timeout': 20}),
    'refresh_full_card': (
        '현재 판매 가능한 재고 전체를 조회해 지정된 가게 카드에 품목별 등급과 하한 이상 가격을 기록합니다. args 없음. '
        '떨이·기한 임박 재고는 고정 설명 바로 뒤에 떨이가로 먼저 적습니다.',
        {'network': [MARKET], 'write': ['card.json', 'card.test.json'], 'identity': ['shop'], 'pay_limit': 0, 'timeout': 20}),
    'inventory_sales_check': (
        '우리 재고(로그인 /api/inventory)를 품목·등급별 개수·하한가로 묶고, 공개 장부 최근 1,500줄의 같은 SKU·등급 정산가 중앙값을 붙입니다. args {}. '
        '조회가 실패하면 ok:false이고 재고 0이 아닙니다.',
        {'network': [MARKET], 'identity': ['shop'], 'timeout': 60}),
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
    ap.add_argument('--only', action='append', choices=sorted(TOOLS), help='forge only these tools (repeatable); no retiring')
    a = ap.parse_args()
    names = a.only or list(TOOLS)
    if a.print or not a.kit:
        print(json.dumps([move(n) for n in names], ensure_ascii=False, indent=1))
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
    for n in names:
        out = box.forge(move(n))
        print(n, json.dumps(out, ensure_ascii=False))
        ok = ok and isinstance(out, dict) and out.get('test') == 'passed'
    if not ok:
        print('a test failed' + ('' if a.only else ': refund_order was left in place'))
        sys.exit(1)
    for n in ([] if a.only else RETIRE):
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
