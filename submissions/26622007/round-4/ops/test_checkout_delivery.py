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

    def test_two_checkouts_with_long_prose_both_visible(self):
        cs = [co(1), co(2)]
        text, attached = cd.compose(PROSE, cs, set(), NOW)
        self.assertEqual({c.order for c in attached}, {'task-1', 'task-2'})
        self.check_visible(text, cs)
        self.assertLessEqual(len(text), cd.REPLY_MAX)
        self.assertIn('두부 1모', text)                    # prose still follows

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

    def test_too_many_for_visible_window_raises_with_how_many_fit(self):
        with self.assertRaises(ValueError) as e:
            cd.compose('x', [co(1), co(2), co(3)], set(), NOW)
        msg = str(e.exception)
        self.assertIn('2,000', msg)
        self.assertIn('2장', msg)                           # two of these fit
        self.assertIn('checkout_cancel', msg)

    def test_cancel_down_to_fit_works(self):
        cs = [co(1), co(2), co(3)]
        text, attached = cd.compose(PROSE, cs, {'task-3'}, NOW)
        self.assertEqual({c.order for c in attached}, {'task-1', 'task-2'})
        self.check_visible(text, cs[:2])
        self.assertNotIn(cs[2].token, text)

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

    def test_two_prod_sized_checkouts_fit(self):
        cs = [co(5), co(6)]
        text, attached = cd.compose(PROSE, cs, set(), NOW)
        self.assertEqual(len(attached), 2)
        self.check_visible(text, cs)

    def test_no_checkout_plain_reply(self):
        text, attached = cd.compose(PROSE * 3, [], set(), NOW)
        self.assertEqual(attached, ())
        self.assertEqual(len(text), cd.REPLY_MAX)


if __name__ == '__main__':
    unittest.main()
