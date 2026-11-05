import unittest
import scorecard

class DriftTest(unittest.TestCase):
    def test_drift_and_gap_use_same_public_board_scale(self):
        b={'shops':[{'name':scorecard.ME,'profit':1,'revenue':1,'stars_rank':1,'all':.7}],
           'buyers':[{'name':scorecard.ME,'surplus':1,'forum':1,'deals':1,'posts':1,'all':1}],
           'players':[{'player':'Nocha12','total':1.7},{'player':'leader','total':1.9}]}
        o=scorecard.objective(b)
        self.assertEqual(o['board_drift'], {'shops':.3,'buyers':0})
        self.assertEqual(o['board_players_gap'], .2)
        self.assertEqual(o['players'], 2)
        del b['players']
        self.assertIsNone(scorecard.objective(b)['board_players_gap'])


class BookTest(unittest.TestCase):
    def test_shop_all_ranks_book_when_the_board_has_it(self):
        me = {'name': scorecard.ME, 'profit': 1, 'book': 9, 'revenue': 5, 'stars_rank': 5, 'all': .75}
        other = {'name': 'x', 'profit': 5, 'book': 5, 'revenue': 9, 'stars_rank': 9, 'all': .25}
        o = scorecard.objective({'shops': [me, other], 'buyers': []})
        self.assertIn('book', o['shops']['parts'])
        self.assertEqual(o['shops']['all'], .5)
        self.assertEqual(o['board_drift']['shops'], -.25)
        r = scorecard.rivals([me, other])
        self.assertEqual((r['ranked_by'], r['my_profit_rank']), ('book', 1))
        self.assertEqual(scorecard.rivals([{k: v for k, v in other.items() if k != 'book'}])['ranked_by'], 'profit')
