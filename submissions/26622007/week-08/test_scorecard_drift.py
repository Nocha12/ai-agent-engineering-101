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
        self.assertEqual((r['ranked_by'], r['my_rank']), ('book', 1))
        self.assertEqual(scorecard.rivals([{k: v for k, v in other.items() if k != 'book'}])['ranked_by'], 'profit')


class RaceTest(unittest.TestCase):
    BOARD = {'players': [{'player': 'lead', 'total': 1.9}, {'player': 'up', 'total': 1.5}, {'player': 'Nocha12', 'total': 1.27}]}

    def test_rank_gaps_and_growth_of_me_the_leader_and_the_player_above(self):
        past = [{'ts': 0, 'players': {'lead': 1.8, 'up': 1.45, 'Nocha12': 1.3}},
                {'ts': 30000, 'players': {'lead': 1.85}}]
        r = scorecard.race(self.BOARD, past, now=0 + 6 * 3600 + 60)
        self.assertEqual((r['rank'], r['above'], r['gap_to_above'], r['gap_to_leader']), (3, 'up', .23, .63))
        self.assertEqual(r['growth']['6h'], {'me': -.03, 'leader': .1, 'above': .05, 'hours': 6.0})
        self.assertIsNone(r['growth']['12h'])

    def test_the_percentile_is_not_rounded_before_the_weights(self):
        rows = [{'name': str(i), 'book': i, 'revenue': i, 'stars_rank': i, 'all': 0} for i in range(7)]
        rows[3]['name'] = scorecard.ME
        o = scorecard.objective({'shops': rows, 'buyers': []})
        self.assertEqual(o['shops']['all'], .5)
        self.assertTrue(o['shops']['ranked'])

    def test_an_unscored_row_is_estimated_but_marked(self):
        me = {'name': scorecard.ME, 'book': 1, 'revenue': 1, 'stars_rank': 1}
        o = scorecard.objective({'shops': [me, {'name': 'x', 'book': 2, 'revenue': 2, 'stars_rank': 2, 'all': .9}], 'buyers': []})
        self.assertFalse(o['shops']['ranked'])


class AlertTest(unittest.TestCase):
    def test_cap_surge_forum_and_drift_raise_alerts(self):
        import json, os, tempfile, time
        now = time.time()
        with tempfile.TemporaryDirectory() as md:
            os.makedirs(os.path.join(md, 'measure'))
            with open(os.path.join(md, 'llm_calls.jsonl'), 'w') as f:
                f.writelines(json.dumps({'ts': now - 10, 'tag': 'seller-harness'}) + '\n' for _ in range(301))
            with open(os.path.join(md, 'measure', 'llm_cap.jsonl'), 'w') as f:
                f.write(json.dumps({'ts': now - 5, 'tag': 'buyer-agent'}) + '\n')
            json.dump({'G1': {'list_price': 1000}}, open(os.path.join(md, 'catalog.json'), 'w'))
            json.dump({'units': [{'sku': 'G1', 'cost': 950, 'supplied_at': now - 60},
                                 {'sku': 'G1', 'cost': 700, 'supplied_at': now - 60}]}, open(os.path.join(md, 'inventory.json'), 'w'))
            b = {'objective': {'buyers': {'parts': {'forum': {'value': .55}}}, 'board_drift': {'shops': .002, 'buyers': 0}},
                 'forum_6h_ago': .58}
            got = [a.split(':')[0] for a in scorecard.alerts(md, b, now=now)]
            self.assertEqual(got, ['llm_calls', 'llm_cap', 'surge', 'forum', 'board_drift'])
            b['forum_6h_ago'] = .555
            b['objective']['board_drift'] = {}
            os.remove(os.path.join(md, 'llm_calls.jsonl'))
            self.assertEqual([a.split(':')[0] for a in scorecard.alerts(md, b, now=now)], ['llm_cap', 'surge'])


class JsonlTest(unittest.TestCase):
    def test_a_cut_line_is_skipped_not_the_whole_file(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'h.jsonl')
            with open(p, 'w') as f:
                f.write('{"ts": 1}\n{"ts": 2, "pla{"ts": 3}\n{"ts": 4}\n')
            self.assertEqual([r['ts'] for r in scorecard.jsonl(p)], [1, 4])
