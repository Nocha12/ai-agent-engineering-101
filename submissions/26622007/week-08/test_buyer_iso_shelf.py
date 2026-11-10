"""Food expiry eligibility follows the existing checkout policy, not price reading."""
import unittest
from unittest import mock
import market_buyer as m


class Expiry(unittest.TestCase):
    def test_real_short_food_quote_is_refused_before_any_offer(self):
        # Actual 11/10 18:31 quote, later refused by checkout at 152.14 < 180.1 hours.
        text = ('Inventory condition: C. Expires at 2026-11-16T17:39:25+00:00. '
                'Confirm the certificate at checkout; condition can change while negotiating. Warranty: 0 hours.\n'
                '2026년 햅쌀 임금님표 이천쌀 4kg 상등급 소포장쌀 단일품종: asking 18550. Send your offer as a number.')
        order = {'kind': 'food', 'use_within_hours': 180.0}
        with mock.patch.object(m.time, 'time', return_value=1794303059.0):
            self.assertIsNotNone(m.quote_refused(text, order))

    @staticmethod
    def quote(expiry='2026-11-10T11:00:00+00:00', warranty=0):
        return (f'Inventory condition: A. Expires at {expiry}. Confirm the certificate at checkout; '
                f'condition can change while negotiating. Warranty: {warranty} hours.\nCounteroffer: 12000.')

    def test_checkout_boundary_and_timezone_offsets(self):
        # 19:00 KST -> expiry20:00 KST, exactly 0.9 + 0.1h is eligible.
        order = {'kind': 'food', 'use_within_hours': 0.9}
        for expiry in ('2026-11-10T11:00:00+00:00', '2026-11-10T20:00:00+09:00', '2026-11-10T11:00:00Z'):
            with self.subTest(expiry=expiry), mock.patch.object(m.time, 'time', return_value=1794304800.0):
                self.assertIsNone(m.quote_refused(self.quote(expiry), order))
            with mock.patch.object(m.time, 'time', return_value=1794304800.01):
                self.assertIsNotNone(m.quote_refused(self.quote(expiry), order))

    def test_ambiguous_truncated_or_unbound_dates_keep_the_existing_path(self):
        order = {'kind': 'food', 'use_within_hours': 48.0}
        good = self.quote()
        cases = [self.quote('2026-11-10T11:00:00'), self.quote('2026-02-30T11:00:00+00:00'),
                 self.quote('2026-11-10T11:00:00+99:00'), good[:120],
                 good + '\nExpires at 2026-12-10T11:00:00+00:00.', 'Example: ' + good]
        with mock.patch.object(m.time, 'time', return_value=1794304800.0):
            for text in cases:
                with self.subTest(text=text):
                    self.assertIsNone(m.quote_refused(text, order))

    def test_bad_clocks_do_not_reclassify_unknown_quotes(self):
        order = {'kind': 'food', 'use_within_hours': 48.0}
        for now in (True, '0', float('nan'), float('inf'), -float('inf'), 10**400):
            with self.subTest(now=now), mock.patch.object(m.time, 'time', return_value=now):
                self.assertIsNone(m.quote_refused(self.quote(), order))

    def test_existing_relative_hours_and_goods_guards_are_unchanged(self):
        with mock.patch.object(m.time, 'time', return_value=1794304800.0):
            self.assertIsNotNone(m.quote_refused('12 h of shelf life left', {'kind': 'food', 'use_within_hours': 48.0}))
            # Contradictory/updated freshness stays on the old conservative max-hours policy.
            self.assertIsNone(m.quote_refused(self.quote() + ' 72 h of shelf life left', {'kind': 'food', 'use_within_hours': 48.0}))
            self.assertIsNone(m.shelf_refused(self.quote(), {'kind': 'goods', 'use_within_hours': 48.0}))
            self.assertIsNotNone(m.quote_refused(self.quote(), {'kind': 'goods'}))
            self.assertIsNone(m.quote_refused(self.quote(warranty=6), {'kind': 'goods'}))
            self.assertIsNone(m.quote_refused(self.quote(), {'kind': 'food', 'use_within_hours': 0}))


if __name__ == '__main__':
    unittest.main()
