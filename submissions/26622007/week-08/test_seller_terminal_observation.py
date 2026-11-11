"""Public paid/unpaid callback contract; no market or model calls."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import seller
from shop_stock import StockBook


class TerminalObservation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, MARKET_DIR=str(self.root / '.market'))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.cwd)
        self.shop = seller.Seller(None, 'fixture', stock=StockBook(load=lambda: {'round': 3, 'units': []}))
        self.info = {'task': 'fixture-task-l18', 'price': 13295, 'deal': {
            'id': 'Gef4d3036', 'unit': 'fixture-unit-l18', 'name': 'fixture item',
            'cost': 8020, 'reserve': 8042, 'open': 13706, 'arm': None,
            'warranty': None, 'history': [('you asked', 13295), ('you asked', 13295),
                                        ('buyer offered', 10), ('buyer offered', 13295)]}}

    def tearDown(self):
        seller.flush_memory()

    def rows(self):
        return [json.loads(x) for x in (self.root / '.market/measure/seller_terminal.jsonl').read_text().splitlines()]

    def test_unpaid_callback_adds_joinable_reason_without_changing_memory(self):
        original = copy.deepcopy(self.info)
        self.shop.unpaid(self.info, 'expired before payment')
        seller.flush_memory()
        row, = self.rows()
        memory = json.loads((self.root / 'memory/seller_memory_r3.json').read_text())['negotiations'][-1]
        self.assertEqual(row['task_sha256'], hashlib.sha256(memory['task'].encode()).hexdigest())
        self.assertEqual(row['unit_sha256'], hashlib.sha256(memory['unit'].encode()).hexdigest())
        self.assertEqual((row['sku'], row['local_outcome'], row['reason_enum'], row['price']),
                         ('Gef4d3036', 'unpaid', 'expired', 13295))
        self.assertEqual(memory['result'], 'unpaid')
        self.assertEqual(self.info, original)


if __name__ == '__main__':
    unittest.main()
