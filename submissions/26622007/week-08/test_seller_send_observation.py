"""Public Seller.execute send seam; synthetic URL keys, isolated stock and RPC."""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from a2a.types import Message
import seller
import shop_memory
from shop_stock import StockBook


class SendObservation(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for patch in (mock.patch.dict(os.environ, MARKET_DIR=str(self.root / 'market')),
                      mock.patch.object(shop_memory, 'MEMORY', str(self.root / 'memory.json')),
                      mock.patch.object(seller, 'load_shop_key', return_value=None),
                      mock.patch.object(seller, 'log')):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(seller.flush_memory)
        self.task = 'https://fixture.invalid/tasks/12345678-aaaa'
        self.unit = 'https://fixture.invalid/units/12345678-aaaa'
        self.deal = dict(id='Gb2392fa9', unit=self.unit, name='fixture item', kind='food',
                         reserve=8042, ask=13295, open=13706, rounds=1, warranty=None,
                         history=[('you asked', 13295)])
        self.inventory = [dict(unit=self.unit, sku='Gb2392fa9', grade='A')]
        self.book = StockBook(load=lambda: dict(round=3, units=self.inventory))
        self.shop = seller.Seller(None, 'fixture', stock=self.book)
        self.calls = []

    def rows(self):
        path = self.root / 'market/measure/seller_send.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def run_turn(self, *, task=None, unit=None, fail=None, error=None, probe=None):
        task, unit = task or self.task, unit or self.unit
        deal = copy.deepcopy(self.deal)
        deal['unit'] = unit
        self.inventory[:] = [dict(unit=unit, sku=deal['id'], grade='A')]
        self.shop.open[task] = deal
        async def sent(phase, *args, **kwargs):
            self.calls.append(phase)
            self.assertEqual(self.book.agreed[unit]['task'], task)
            if probe:
                probe(phase)
            if phase == fail:
                raise error
        up = SimpleNamespace(new_agent_message=lambda **kw: kw,
                             add_artifact=lambda **kw: sent('artifact', **kw),
                             complete=lambda *args: sent('complete', *args),
                             reject=mock.AsyncMock(), failed=mock.AsyncMock())
        ctx = SimpleNamespace(current_task=SimpleNamespace(id=task, context_id='fixture-context'),
                              message=Message(role='ROLE_USER', message_id='fixture-message',
                                              parts=[seller.new_text_part('13295')]))
        with mock.patch.object(seller, 'TaskUpdater', return_value=up):
            result = asyncio.run(self.shop.execute(ctx, None))
        self.assertFalse(up.reject.called)
        self.assertFalse(up.failed.called)
        seller.flush_memory()
        return result

    def test_public_send_records_before_and_returned_for_both_awaits(self):
        def probe(phase):
            self.assertEqual(self.rows()[-1]['phase'], phase + '_before')
        self.assertIsNone(self.run_turn(probe=probe))
        self.assertEqual(self.calls, ['artifact', 'complete'])
        self.assertEqual([r['phase'] for r in self.rows()],
                         ['artifact_before', 'artifact_returned', 'complete_before', 'complete_returned'])
        self.assertNotIn(self.task, self.shop.open)
        self.assertEqual(json.loads((self.root / 'memory.json').read_text())['negotiations'][-1]['result'], 'agreed')


if __name__ == '__main__':
    unittest.main()
