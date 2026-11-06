import copy
import unittest
import meta_harness
import scorecard


class ComparabilityTest(unittest.TestCase):
    def card(self):
        return {'rules': {'latest': 'Patch 3.4', 'objective_matches': True},
                'board': {'objective': {'shops': {'parts': {'book': {'weight': .5}}},
                                        'buyers': {'parts': {'saved': {'weight': .5}}},
                                        'board_drift': {'shops': 0, 'buyers': 0}}}}

    def test_stale_formula_and_changed_patch_are_not_comparable(self):
        a = self.card(); b = copy.deepcopy(a)
        self.assertTrue(meta_harness.score_comparable(a, b))
        b['board']['objective']['buyers']['parts'] = {'surplus': {'weight': .5}}
        self.assertFalse(meta_harness.score_comparable(a, b))
        b = self.card(); b['board']['objective']['board_drift']['buyers'] = .11
        self.assertFalse(meta_harness.score_comparable(a, b))
        b = self.card(); b['rules']['latest'] = 'Patch 3.3'
        self.assertFalse(meta_harness.score_comparable(a, b))
        self.assertFalse(meta_harness.score_comparable({}, {}))

    def test_snapshot_signature_makes_growth_comparable(self):
        b = {'players': [{'player': 'Nocha12', 'total': 1}]}
        p = scorecard.snapshot(b); p['ts'] = 1
        self.assertTrue(scorecard.race(b, [p], now=21602)['growth']['6h']['comparable'])
        p['scoring_signature']['patch'] = '3.3'
        self.assertFalse(scorecard.race(b, [p], now=21602)['growth']['6h']['comparable'])
