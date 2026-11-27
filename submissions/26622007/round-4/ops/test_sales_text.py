"""Sales text (W-20261127-0950-AX판매개선, 대표님 #13645): what the kit tells the model about near-expiry units and
asked-for buys, read from the exact sources the kit loads (ak.FORMAT addition, notes.md operator lines).
Run from ops/: PYTHONPATH=<a temp dir with pytest> ~/ax-round4-venv/bin/python -m pytest -q test_sales_text.py"""
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
        self.assertIn('under 12 hours', text)
        self.assertIn('do not create its checkout first', text)
        self.assertIn('the date and hour it expires', text)
        self.assertIn('if the customer already asked for a checkout or named that unit', text)   # review 1 I2
        self.assertIn('when a unit with 12 hours or more left also fits, issue with that one as usual', text)   # I3
        self.assertIn('A clearance unit is created without price', text)                      # I4
        self.assertIn('unless a lower price down to clearance.floor was agreed: then pass that price', text)   # r2 I-1
        self.assertIn('unless the customer named the near-expiry or clearance unit', text)   # r2 M-1
        self.assertIn('Otherwise hold back only when', text)                                   # M1
        # checkout-first for everything else stays
        self.assertIn('create the checkout in this same reply at the shown price', text)

    def test_clearance_line_no_longer_issues_first(self):
        line = next(x for x in operator_lines() if '떨이]' in x)
        self.assertNotIn('첫 답에서 그 unit으로 private_checkout을 price 없이 바로 내서', line)
        self.assertIn('12시간 미만', line)
        self.assertIn('결제서를 먼저 내지 않는다', line)
        self.assertIn('clearance.floor까지만', line)              # prices and floors unchanged
        self.assertIn('생수 가격은 그대로다', line)
        self.assertIn('이미 결제서를 달라고 했거나 그 unit을 짚어 물었으면 이번 답에서', line)
        self.assertIn('12시간 이상 남은 unit도 맞으면 그 unit으로 평소대로 낸다(손님이 떨이 unit을 짚었으면 그 unit)', line)
        self.assertIn('떨이 unit은 남은 시간과 상관없이 price 없이 내고, 흥정으로 clearance.floor 이상에서 합의한 값만', line)
        self.assertIn("앞의 '만료 임박 재고 우선 판매' 메모보다 이 지시가 먼저다", line)       # r2 I-2 (notes 33)

    def test_asked_buy_reads_all_options(self):
        line = next(x for x in operator_lines() if x.startswith('[운영자 최신 지시 11/26] 손님이 찾았는데'))
        self.assertIn('buy_options 1~3번째', line)
        self.assertIn('공급 창당 최대 3품목, 기존 hard_limits 유지', line)   # caps unchanged

    def test_one_line_each(self):                                           # review 1 M3: no duplicate came back
        lines = operator_lines()
        self.assertEqual(sum(x.startswith('[운영자 최신 지시 11/26] 손님이 찾았는데') for x in lines), 1)
        self.assertEqual(sum('떨이]' in x for x in lines), 1)

    def test_food_candidates_note_matches(self):                            # review 1 I1: the tool says the same
        src = (HERE / 'tools_src' / 'food_candidates' / 'logic.py').read_text(encoding='utf-8')
        self.assertNotIn('첫 번째 buy_options SKU', src)
        self.assertIn('buy_options 1~3번째에서 손님이 쓴 것과 같은 식품', src)
        self.assertIn('셋 다 다르면 건너뜀', src)


if __name__ == '__main__':
    unittest.main()
