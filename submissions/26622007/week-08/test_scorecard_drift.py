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
