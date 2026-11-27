"""Sales text (W-20261127-0950-AX판매개선, 대표님 #13645): what the kit tells the model about near-expiry units and
asked-for buys, read from the exact sources the kit loads (ak.FORMAT addition, notes.md operator lines).
Run from ops/: PYTHONPATH=<a temp dir with pytest> ~/ax-round4-venv/bin/python -m pytest -q test_sales_text.py"""
import ast
import sys
import json
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
        self.assertIn('the date and time it expires', text)
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
                  'offer the one closest unit you hold', "a date and time from the tool's until as written",
                  "say its result's price and until exactly as written", 'not cut to the hour',
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
        self.assertEqual(r['now'], '11/27 09:40 KST')                # the minute it was read
        self.assertEqual(r['items'][0]['until'], '11/29 03시 KST')    # an older tool (no until): the hour, as before
        self.assertEqual(r['items'][1]['until'], '11/27 09시 KST')
        self.assertNotIn('until', r['items'][2])

    def test_tool_until_kept_to_the_minute(self):             # 11/27 0b2461b6: '18시까지' against a signed 18:27
        import agent_kit_records as rec
        out = {'ok': True, 'result': {'ok': True, 'sku': '두부', 'count': 3, 'items': [
            {'unit': 'u-1', 'hours_left': 7.4, 'until': '11/27 18:27 KST'},
            {'unit': 'u-2', 'hours_left': 2.0, 'until': 'soon'},          # not the tool's form: the hour from hours_left
            {'unit': 'u-3', 'until': None}]}}
        rec.with_until(out, 1795745219.0)                            # 11:06:59 KST, the 0b2461b6 look-up
        items = out['result']['items']
        self.assertEqual(items[0]['until'], '11/27 18:27 KST')
        self.assertNotIn('18시', json.dumps(items[0], ensure_ascii=False))
        self.assertEqual(items[1]['until'], '11/27 13시 KST')
        self.assertNotIn('until', items[2])
        self.assertEqual(out['result']['now'], '11/27 11:06 KST')

    @unittest.skipUnless(__import__('importlib.util').util.find_spec('httpx'), 'the tool imports httpx (market venv)')
    def test_tool_gives_until_from_expires_at(self):           # the forged tool's own until, the certificate's minute
        import importlib.util
        spec = importlib.util.spec_from_file_location('cii_logic', HERE / 'tools_src' / 'check_item_inventory' / 'logic.py')
        lg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lg)
        ea = 1795771679                                              # 11/27 18:27:59 KST
        self.assertEqual(lg.until({'expires_at': ea}), '11/27 18:27 KST')
        self.assertEqual(lg.until({'expires_at': float(ea)}), '11/27 18:27 KST')
        cert = {'unit': 'u-1', 'cert': {'unit': 'u-1', 'kind': 'food', 'grade': None, 'expires_at': ea}}
        self.assertEqual(lg.until({'unit': 'u-1', 'expires_at': ea + 3600, 'cert': cert}, ea - 3600), '11/27 18:27 KST')   # signed one first
        # review 2 N2: the signed expiry already past while the inventory date is not -> no until (not a past time)
        self.assertIsNone(lg.until({'unit': 'u-1', 'expires_at': ea + 3600, 'cert': cert}, ea))
        self.assertIsNone(lg.until({'unit': 'u-1', 'expires_at': ea + 3600, 'cert': cert}, ea + 60))
        other = {'cert': {'unit': 'u-9', 'expires_at': ea}}
        self.assertEqual(lg.until({'unit': 'u-1', 'expires_at': ea + 3600, 'cert': other}, ea + 60), '11/27 19:27 KST')  # not this unit's
        for u in ({}, {'expires_at': 0}, {'expires_at': True}, {'expires_at': '1795771679'}):
            self.assertIsNone(lg.until(u))
        import checkout_delivery as cd                               # the same minute the signed data line shows
        note = cd.grade_note('01daff51-0000-4000-8000-000000000000',
                             {'unit': 'u-f5a2635c268b', 'claim': 'C', 'supplied_at': ea - 86400, 'expires_at': ea}, ea - 7 * 3600)
        self.assertIn('소비기한 ' + lg.until({'expires_at': ea}) + '까지', note)


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


class IssuedTerms(unittest.TestCase):
    ORDER = '01daff51-0000-4000-8000-000000000000'

    def test_clearance_price_is_the_signed_one(self):          # 11/27 01daff51: '4,720' said, 4,710 signed
        import agent_kit_records as rec
        ea, now = 1795764420, 1795745977.0   # expiry 16:27, issued 11:19:37 KST
        got = rec.issued_terms({'price': 4710, 'iat': int(now)}, {'expires_at': ea}, now)
        self.assertEqual(got['price'], 4710)
        self.assertEqual(got['until'], '11/27 16:27 KST')
        self.assertIn('가격 4,710크레딧', got['reply_note'])
        self.assertIn('소비기한 11/27 16:27까지', got['reply_note'])
        self.assertNotIn('4,720', got['reply_note'])
        self.assertNotIn('16시', got['reply_note'])

    def test_until_matches_signed_data_line(self):            # 11/27 0b2461b6: '18시까지' against 18:27
        import agent_kit_records as rec
        import checkout_delivery as cd
        ev = {'unit': 'u-f5a2635c268b', 'claim': 'C', 'supplied_at': 1795700000, 'expires_at': 1795771679}
        now = 1795745239.0                                           # 11:07:19 KST
        got = rec.issued_terms({'price': 2680}, ev, now)
        self.assertEqual(got['until'], '11/27 18:27 KST')
        self.assertIn('소비기한 ' + got['until'] + '까지', cd.grade_note(self.ORDER, ev, now))
        self.assertIn('2,680크레딧', got['reply_note'])

    def test_nothing_checkable_adds_nothing(self):
        import agent_kit_records as rec
        now = 1795740000.0
        self.assertEqual(rec.issued_terms(None, None, now), {})
        self.assertEqual(rec.issued_terms({'price': '4710'}, {'expires_at': 'x'}, now), {})
        self.assertEqual(rec.issued_terms({'price': True}, {'expires_at': now - 1}, now), {})   # past expiry: no until
        only = rec.issued_terms({'price': 5000}, None, now)
        self.assertEqual(set(only), {'price', 'reply_note'})
        self.assertNotIn('소비기한', only['reply_note'])


class GradeAnswer(unittest.TestCase):
    """check-0340 후보 1 (11/28 00:25~03:40): asked whether the certificate shows the grade, the model said three
    different things: b6fd1f23 'the signed certificate shows the grade' (false, food cert grade is empty by design),
    9f705afc 'cannot confirm it with this checkout', 05f2657d cancelled its own checkout for lack of grade proof; all
    unpaid. b2b628e8/707973ea said the signed grade line + expiry on the certificate and were paid. One answer now."""

    def cert(self, **kw):
        return {'kind': 'food', 'unit': 'u-000000000001', 'grade': None, 'supplied_at': 1795700000,
                'expires_at': 1795900000, **kw}

    def test_format_says_one_answer(self):
        text = kit_format()
        self.assertIn("'X등급(가게 서명)'", text)
        self.assertIn('the grade the shop signs into that checkout', text)
        self.assertIn('leaves the food grade empty by design and states the expiry', text)
        self.assertIn('Never say the grade can be read in the certificate', text)
        self.assertIn('never say you cannot confirm the grade', text)
        self.assertIn('never cancel a checkout because the certificate has no grade', text)
        self.assertIn('the missing certificate grade is never a reason to cancel', text)          # r1 M1
        self.assertNotIn("cancel only on the customer's refusal", text)
        self.assertIn('Only when the customer says a grade certified by the market', text)        # r1 I1 (notes 21/27)
        self.assertIn('ask if the shop-signed grade is fine', text)
        self.assertIn("the issue result's reply_note says which of these this checkout has", text)   # r1 M2
        # the two sources of the split answers are gone
        self.assertNotIn('소비기한은 시장 서명 인증서에 있습니다', text)
        self.assertNotIn('independently certified grade, explain it is unavailable', text)
        self.assertNotIn('lists only the item, signed price and expiry', text)   # stale since 8b8de83b

    def test_note_names_the_signed_grade_and_the_empty_cert(self):
        import agent_kit_records as rec
        note = rec.food_reply_note('B', self.cert(), '01/15 18:00 KST')
        self.assertIn("'B등급(가게 서명)'", note)
        self.assertIn('가게가 이 결제서에 서명한 등급', note)
        self.assertIn('시장 인증서는 설계상 식품 등급(grade)을 비워 두고 소비기한(01/15 18:00까지)을 적습니다', note)
        self.assertIn('가격·소비기한과 가게가 서명한 등급 줄이 자동으로 붙습니다', note)
        for bad in ('인증서에서 등급을 확인할 수 있다', '확인해 드릴 수 없다', '결제서를 취소하지 않습니다'):
            self.assertIn(bad, note)            # named as what not to say / do
        self.assertIn('그와 다른 가격·등급을 쓰지 않습니다', note)
        self.assertIn('시장이 인증한 등급이 꼭 있어야 산다고 했으면 위 사실을 한 문장으로 말하고', note)   # r1 I1

    def test_no_expiry_no_expiry_claim(self):              # review 0010 M3: no until -> no 소비기한 promised
        import agent_kit_records as rec
        for cert in (self.cert(), {}):                     # past expiry / another unit's certificate
            note = rec.food_reply_note('A', cert, None)
            self.assertNotIn('소비기한', note, note)
            self.assertIn("'A등급(가게 서명)'", note)
            self.assertIn('가격과 가게가 서명한 등급 줄이 자동으로 붙습니다', note)
            self.assertIn('시장 인증서는 설계상 식품 등급(grade)을 비워 둡니다', note)

    def test_no_claim_no_grade_line(self):
        import agent_kit_records as rec
        for claim in (None, 'D', '', 3):
            note = rec.food_reply_note(claim, self.cert(), '01/15 18:00 KST')
            self.assertNotIn('등급(가게 서명)', note, note)
            self.assertIn('가격·소비기한 줄이 자동으로 붙습니다', note)
            self.assertIn('답장에 등급을 쓰지 않습니다', note)
            self.assertIn('손님이 등급을 물으면 이렇게 답합니다: 이 결제서에는 가게가 서명한 등급이 없고, ', note)   # r1 M4
            self.assertIn('시장 인증서는 설계상 식품 등급(grade)을 비워 두고 소비기한(01/15 18:00까지)을 적습니다', note)

    def test_note_matches_the_composed_lines(self):        # r1 M3: the note says what compose really puts in front
        import base64
        import agent_kit_records as rec
        import checkout_delivery as cd
        now, unit = 1_800_000_000.0, 'u-e1fa9c84e002'
        cases = 0
        for claim in ('B', None, 'Z'):
            for cert_unit in (unit, 'u-000000000000'):            # this unit's certificate / another unit's
                for ends in (now + 3600, now - 60):               # expiry ahead / already past
                    cert = {'kind': 'food', 'unit': cert_unit, 'grade': None, 'expires_at': ends}
                    payload = {'unit': unit, 'price': 4441, 'cert': {'cert': cert}}
                    if claim is not None:
                        payload['claim'] = claim
                    enc = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
                    c = cd.Checkout('task-1', f'eyJhbGciOiJFZERTQSJ9.{enc}.c2ln', now - 10)
                    text, _ = cd.compose('', [c], set(), now)
                    no_grade = cd._ungraded_food(payload)
                    note = rec.food_reply_note(payload.get('claim'), no_grade,
                                               rec.issued_terms(payload, no_grade, now - 10).get('until'))
                    with self.subTest(claim=claim, cert_unit=cert_unit, ends=ends):
                        self.assertEqual('등급(가게 서명)' in text, "등급(가게 서명)'" in note, (text, note))
                        self.assertEqual('소비기한 ' in text, '소비기한(' in note, (text, note))   # the signed term vs the note (CERT_GRADE_LINE says 소비기한이)
                        self.assertEqual('소비기한 ' in text, '가격·소비기한' in note, (text, note))
                        if claim == 'B':
                            self.assertEqual(text.count('B등급(가게 서명)'), 1)
                            self.assertEqual(note.count('B등급(가게 서명)'), 1)
                    cases += 1
        self.assertEqual(cases, 12)


if __name__ == '__main__':
    unittest.main()
