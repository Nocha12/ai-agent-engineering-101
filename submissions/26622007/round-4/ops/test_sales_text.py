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


class Replies(unittest.TestCase):
    def test_format_reply_rules(self):
        text = kit_format()
        for s in ('say what you can do first', 'Never write null, 하한가, 하한, 떨이가, floor or clearance',
                  'nor 안내가 meaning a price', 'say it in one sentence and add what you can offer instead',
                  'never make up a fact', 'look the item up with {sku} before you give its expiry',
                  'offer the one closest unit you hold', "a date and hour from the tool's until",
                  'once a conversation and only when it is the same item', '계란 20구 is 20 eggs',
                  'not when they refused substitutes'):
            self.assertIn(s, text)

    def test_goal_greeting_only_same_item(self):
        import json
        g = json.loads((HERE.parent / 'agent.json').read_text(encoding='utf-8'))['goal']
        self.assertNotIn('첫마디에 반드시 지난 구매나 지난번 찾던 물건', g)
        self.assertIn('이번에 찾는 물건과 같은 품목일 때만', g)
        self.assertIn('가장 가까운 1개를 이름·등급·기한·가격으로 함께 권한다', g)

    def test_ask_contact_text(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('rs_logic', HERE / 'restock_shoutout' / 'logic.py')
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.assertTrue(m.ASK_CONTACT.startswith('지금은 이 물건이 없어요. 들어오면 먼저 알려 드릴게요.'))
        src = (HERE / 'restock_shoutout' / 'logic.py').read_text(encoding='utf-8')
        import re
        says = re.findall(r"(?:say=|'say': )f?'([^']*)'", src) + [m.ASK_CONTACT]
        self.assertGreaterEqual(len(says), 6)
        for say in says:                                     # review s3 I1/M7: no restock promise, one voice
            for s in ('들여놓', '입고 때', '연락드릴게요', '재고가 없어요'):
                self.assertNotIn(s, say)
        self.assertIn('포럼에서 쓰시는 이름', m.ASK_CONTACT)        # a market contact, as op contact accepts
        self.assertNotIn('연락처(', m.ASK_CONTACT)

    def test_tool_notes_carry_no_banned_words(self):          # review s3 I3: tool text the model reads each call
        src = (HERE / 'tools_src' / 'check_item_inventory' / 'logic.py').read_text(encoding='utf-8')
        import re
        notes = (re.findall(r"CLEAR_NOTE = \('(.*?)'\)", src, re.S) + re.findall(r"'note(?:_price)?': '([^']*)'", src)
                 + re.findall(r"(?:set_note = |\['quote_kept_note'\] = )'([^']*)'", src))
        self.assertGreaterEqual(len(notes), 5)
        for n in notes:
            for w in ('떨이가', '안내가', '하한가', 'null'):
                self.assertNotIn(w, n)


class Until(unittest.TestCase):
    NOW = 1795740000.0                                   # 2026-11-27 09:40 KST

    def test_items_get_date(self):
        import agent_kit_records as rec
        out = {'ok': True, 'result': {'ok': True, 'sku': '쌀', 'count': 2, 'items': [
            {'unit': 'u-1', 'hours_left': 41.5}, {'unit': 'u-2', 'hours_left': 0.2}, {'unit': 'u-3'}]}}
        rec.with_until(out, self.NOW)
        r = out['result']
        self.assertEqual(r['now'], '11/27 09시 KST')
        self.assertEqual(r['items'][0]['until'], '11/29 03시 KST')
        self.assertEqual(r['items'][1]['until'], '11/27 09시 KST')
        self.assertNotIn('until', r['items'][2])

    def test_twenty_items_stay_short(self):                   # review s3 M4: per-item answer grows ~20 chars a unit
        import json
        import agent_kit_records as rec
        out = {'ok': True, 'result': {'ok': True, 'items': [{'unit': f'u-{i:012x}', 'hours_left': 30.5} for i in range(20)]}}
        before = len(json.dumps(out, ensure_ascii=False))
        rec.with_until(out, self.NOW)
        self.assertLess(len(json.dumps(out, ensure_ascii=False)) - before, 20 * 30 + 40)

    def test_whole_shop_and_odd_shapes_untouched(self):
        import copy
        import agent_kit_records as rec
        shop = {'ok': True, 'result': {'ok': True, 'skus': [{'sku': 'G1', 'grades': {'C': {'hours_left': 1}}}]}}
        for out in (shop, {'ok': False, 'error': 'x'}, None, 'text', {'ok': True, 'result': {'items': [1, None]}}):
            before = copy.deepcopy(out)
            got = rec.with_until(out, self.NOW)
            if isinstance(before, dict) and isinstance(before.get('result'), dict) and 'items' in before['result']:
                self.assertEqual(got['result']['items'], [1, None])
                self.assertIn('now', got['result'])            # an items list always gets 'now' (docstring says so)
            else:
                self.assertEqual(got, before)


if __name__ == '__main__':
    unittest.main()
