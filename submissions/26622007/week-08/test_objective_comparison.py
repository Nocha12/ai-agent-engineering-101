import unittest
from meta_harness import objective_changed


class ObjectiveComparisonTest(unittest.TestCase):
    def card(self, field, weight=0.5):
        return {'board': {'objective': {'shops': {'parts': {field: {'weight': weight}}}}}}

    def test_delayed_repair_is_not_comparable(self):
        self.assertTrue(objective_changed(self.card('profit'), self.card('book')))
        self.assertTrue(objective_changed(self.card('book'), self.card('book', 0.6)))

    def test_same_formula_and_missing_history(self):
        self.assertFalse(objective_changed(self.card('book'), self.card('book')))
        self.assertFalse(objective_changed({}, self.card('book')))
