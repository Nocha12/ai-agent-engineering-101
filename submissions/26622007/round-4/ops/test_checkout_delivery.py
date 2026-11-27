"""Tests for ops/checkout_delivery.compose: every signed checkout must sit inside the first VISIBLE characters,
because the customer tool reads only agent_says = said[:2000] (Q00/agent-market house/customer.py).
Run: python3 -I ops/test_checkout_delivery.py  (from the round-4 folder)"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checkout_delivery as cd  # noqa: E402

NOW = 1_800_000_000.0


def jwt(i, n=865):                                  # longest seen in prod react.jsonl (11/27): 865
    head = 'eyJhbGciOiJFZERTQSJ9.'
    sig = '.' + 's' * 86
    return head + (f'p{i}' + 'x' * n)[:n - len(head) - len(sig)] + sig


def co(i, note=True):
    o = f'task-{i}' if i < 5 else f'226232fb-b05d-461a-86d2-83ef28a510f{i}-2'   # prod order ids: <uuid>-N
    return cd.Checkout(o, jwt(i), NOW - 10, (f'등급 근거 ({o}, u-abcdef{i}): ' + 'n' * 520) if note else '')


PROSE = '두부 1모 C등급 3,200원입니다. 기한은 약 20시간 남았습니다. ' * 40


class Compose(unittest.TestCase):
    def check_visible(self, text, cs):
        seen = cd.tokens(text[:cd.VISIBLE])
        for c in cs:
            self.assertIn(c.token, seen, c.order)
            self.assertIn(f'checkout_jwt ({c.order}): {c.token}', text[:cd.VISIBLE])

    def test_two_checkouts_raise_one_a_reply(self):             # operator 11/27: one checkout a reply
        with self.assertRaises(ValueError) as e:
            cd.compose(PROSE, [co(1), co(2)], set(), NOW)
        msg = str(e.exception)
        self.assertIn('1장만', msg)
        self.assertIn('checkout_cancel', msg)
        self.assertIn('input_required', msg)
        self.assertIn("['task-2']", msg)                   # names the order(s) to drop

    def test_one_checkout_token_first_then_prose_then_note(self):
        c = co(1)
        text, _ = cd.compose('두부 1모 3,200원입니다.', [c], set(), NOW)
        self.assertTrue(text.startswith(f'checkout_jwt (task-1): {c.token}'), text[:60])
        self.assertLess(text.index('두부 1모'), text.index('등급 근거'))
        self.assertLessEqual(len(text), cd.VISIBLE)        # short reply: the grade line is visible too

    def test_long_prose_note_dropped_before_prose_cut_below_limit(self):
        text, _ = cd.compose(PROSE * 3, [co(1)], set(), NOW)
        self.assertLessEqual(len(text), cd.REPLY_MAX)
        self.check_visible(text, [co(1)])

    def test_three_raise_and_name_both_extra(self):
        with self.assertRaises(ValueError) as e:
            cd.compose('x', [co(1), co(2), co(3)], set(), NOW)
        self.assertIn("['task-2', 'task-3']", str(e.exception))

    def test_cancel_down_to_one_works(self):
        cs = [co(1), co(2), co(3)]
        text, attached = cd.compose(PROSE, cs, {'task-2', 'task-3'}, NOW)
        self.assertEqual({c.order for c in attached}, {'task-1'})
        self.check_visible(text, cs[:1])
        self.assertNotIn(cs[1].token, text)
        self.assertNotIn(cs[2].token, text)
        self.assertIn('두부 1모', text)                    # prose still follows

    def test_one_line_longer_than_window_raises(self):
        c = cd.Checkout('task-1', jwt(1, 2100), NOW - 10, '')
        with self.assertRaises(ValueError) as e:
            cd.compose('x', [c], set(), NOW)
        self.assertIn('checkout_cancel', str(e.exception))

    def test_expired_or_cancelled_never_sent(self):
        old = cd.Checkout('task-9', jwt(9), NOW - cd.HOLD_SECONDS - 1, '')
        text, attached = cd.compose('hi ' + jwt(7), [old], set(), NOW)
        self.assertEqual(attached, ())
        self.assertEqual(cd.tokens(text), set())          # no token at all, not even one the model wrote

    def test_model_written_tokens_and_labels_stripped(self):
        c = co(1)
        text, _ = cd.compose(f'checkout_jwt (fake): {jwt(5)}\n결제서 드립니다', [c], set(), NOW)
        self.assertEqual(cd.tokens(text), {c.token})
        self.assertEqual(text.count('checkout_jwt'), 1)

    def test_prod_sized_checkout_fits(self):
        cs = [co(5)]
        text, attached = cd.compose(PROSE, cs, set(), NOW)
        self.assertEqual(len(attached), 1)
        self.check_visible(text, cs)

    def test_notes_dropped_prose_kept_when_both_do_not_fit(self):
        prose = '가' * (cd.REPLY_MAX - 1200)
        text, _ = cd.compose(prose, [co(1)], set(), NOW)
        self.assertNotIn('등급 근거', text)
        self.assertIn(prose[:500], text)
        self.assertLessEqual(len(text), cd.REPLY_MAX)

    def test_no_checkout_plain_reply(self):
        text, attached = cd.compose(PROSE * 3, [], set(), NOW)
        self.assertEqual(attached, ())
        self.assertEqual(len(text), cd.REPLY_MAX)


class GradeNote(unittest.TestCase):
    EV = {'unit': 'u-1867f653e4ad', 'claim': 'B', 'supplied_at': NOW - 3600, 'expires_at': NOW + 50 * 3600}

    def test_short_plain_no_null(self):                  # operator 11/27: was about 550 characters opening with null
        note = cd.grade_note('226232fb-b05d-461a-86d2-83ef28a510ff-2', self.EV, NOW)
        self.assertTrue(note.startswith('등급 근거: 시장 규칙상 식품 등급은 남은 소비기한입니다.'), note)
        self.assertNotIn('null', note)
        self.assertLessEqual(len(note), 150)
        self.assertIn('B등급', note)
        exp = cd.datetime.fromtimestamp(NOW + 50 * 3600, cd.KST).strftime('%m/%d %H:%M')
        self.assertIn(f'소비기한 {exp}까지(약 2일 남음)', note)

    def test_hours_under_two_days(self):
        ev = dict(self.EV, expires_at=NOW + 5.5 * 3600)
        self.assertIn('(약 5시간 남음)', cd.grade_note('task-1', ev, NOW))

    def test_bad_evidence_no_note(self):
        for ev in (None, dict(self.EV, claim='D'), dict(self.EV, unit='x'), dict(self.EV, expires_at=NOW - 7200 - 3600 * 99)):
            self.assertEqual(cd.grade_note('task-1', ev, NOW), '')
        self.assertEqual(cd.grade_note('bad order!', self.EV, NOW), '')

    def test_note_keeps_reply_inside_window(self):
        c = cd.Checkout('task-1', jwt(1), NOW - 10, cd.grade_note('task-1', self.EV, NOW))
        text, _ = cd.compose('두부 1모 3,200원입니다.', [c], set(), NOW)
        self.assertIn('등급 근거', text[:cd.VISIBLE])


if __name__ == '__main__':
    unittest.main()
