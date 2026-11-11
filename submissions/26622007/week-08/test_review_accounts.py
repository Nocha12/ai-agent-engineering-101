import json
import tempfile
import unittest
from pathlib import Path
import agent_tools
from tools.review_accounts.tool import summarize


class ReviewAccountsTest(unittest.TestCase):
    def test_paid_identity_verified_and_name_weighting(self):
        sales = [dict(k='settle', seq=i, order=str(i), buyer=b, sku='G')
                 for i, b in [(1, 'a'), (2, 'a'), (3, 'b'), (4, 'other')]]
        reviews = [dict(seq=10+i, order=str(i), buyer=b, stars=s, verified=True)
                   for i, b, s in [(1, 'a', 1), (2, 'a', 3), (3, 'b', 5), (4, 'other', 1)]]
        reviews += [reviews[0], dict(reviews[0], seq=20, verified=False),
                    dict(reviews[0], seq=21, buyer='fake'), dict(reviews[0], seq=22, stars=float('nan'))]
        out = summarize(reviews, sales, {'1': {}, '2': {}, '3': {}})
        self.assertEqual(out['linked_verified_reviews'], 3)
        self.assertEqual(out['name_mean_proxy'], 3.5)
        self.assertEqual(out['reviewers'][0]['examples'][0]['paid_seq'], 1)
        self.assertEqual(out['skipped'], {'unverified': 1, 'unlinked': 2, 'invalid': 1})
        self.assertIn('NOT GitHub account', out['basis'])

    def test_missing_is_not_zero_and_sandbox(self):
        self.assertFalse(summarize(None, [], {})['available'])
        with tempfile.TemporaryDirectory() as root:
            md = Path(root) / '.market'
            md.mkdir()
            result = agent_tools.execute('review_accounts', {}, str(md))
            self.assertNotIn('error', result)
            self.assertFalse(result['available'])

    def test_delivered_summary_keeps_basis(self):
        sales = [dict(k='settle', seq=i, order=str(i), buyer=str(i), sku='G') for i in range(30)]
        reviews = [dict(seq=100+i, order=str(i), buyer=str(i), stars=2, verified=True) for i in range(30)]
        result = summarize(reviews, sales, {str(i): {} for i in range(30)}, 100)
        delivered = json.loads(agent_tools.shrink(result))
        self.assertEqual(delivered['linked_verified_reviews'], 30)
        self.assertIn('NOT GitHub account', delivered['basis'])
        self.assertEqual(len(delivered['reviewers']), 10)
