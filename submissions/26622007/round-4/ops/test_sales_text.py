"""Sales text (W-20261127-0950-AX판매개선, 대표님 #13645): what the kit tells the model about near-expiry units and
asked-for buys, read from the exact sources the kit loads (ak.FORMAT addition, notes.md operator lines).
Run: python3 -I -m pytest ops/test_sales_text.py  (from the round-4 folder)"""
import ast
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def kit_format():
    src = (HERE / 'agent_kit_records.py').read_text(encoding='utf-8')
    fmt = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.AugAssign)
               and getattr(n.target, 'attr', None) == 'FORMAT')
    return ast.literal_eval(fmt.value)


def operator_lines():
    return [x for x in (HERE.parent / 'notes.md').read_text(encoding='utf-8').splitlines() if x.startswith('[운영자')]


class NearExpiry(unittest.TestCase):
    def test_format_holds_checkout_under_12h(self):
        text = kit_format()
        self.assertIn('under 12 hours left', text)
        self.assertIn('do not create its checkout first', text)
        self.assertIn('the date and hour it expires', text)
        # checkout-first for everything else stays
        self.assertIn('create the checkout in this same reply at the shown price', text)

    def test_clearance_line_no_longer_issues_first(self):
        line = next(x for x in operator_lines() if '떨이]' in x)
        self.assertNotIn('첫 답에서 그 unit으로 private_checkout을 price 없이 바로 내서', line)
        self.assertIn('12시간 미만', line)
        self.assertIn('결제서를 먼저 내지 않는다', line)
        self.assertIn('clearance.floor까지만', line)              # prices and floors unchanged
        self.assertIn('생수 가격은 그대로다', line)

    def test_asked_buy_reads_all_options(self):
        line = next(x for x in operator_lines() if x.startswith('[운영자 최신 지시 11/26] 손님이 찾았는데'))
        self.assertIn('buy_options 1~3번째', line)
        self.assertIn('공급 창당 최대 3품목, 기존 hard_limits 유지', line)   # caps unchanged


if __name__ == '__main__':
    unittest.main()
