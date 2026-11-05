import unittest
import json
import tempfile
import time
from pathlib import Path
import agent_tools
from tools.forum_demand.tool import search


class ForumDemandTest(unittest.TestCase):
    def test_sandbox_read_only_contract(self):
        with tempfile.TemporaryDirectory() as d:
            md = Path(d) / '.market'
            md.mkdir()
            (md / 'forum.json').write_text(json.dumps([{'id': 1, 'role': 'buyer',
                'ts': time.time(), 'text': '러그 구합니다'}]))
            (md / 'catalog.json').write_text(json.dumps({'G': {'category': '홈>러그'}}))
            (md / 'inventory.json').write_text('{"units": []}')
            (md / 'ledger_rows.jsonl').write_text('')
            result = agent_tools.execute('forum_demand', {'limit': 1}, str(md))
            self.assertNotIn('error', result)
            self.assertEqual(result['leads'][0]['candidates'][0]['sku'], 'G')
            self.assertFalse((md / 'outbox.jsonl').exists())

    def test_requests_are_not_orders_and_paid_rows_deduplicate(self):
        posts = [{'id': 1, 'role': 'buyer', 'ts': 50, 'text': '러그 구합니다', 'score': 0},
                 {'id': 2, 'role': 'shop', 'ts': 50, 'text': '러그 구합니다'},
                 {'id': 3, 'role': 'buyer', 'ts': 200, 'text': '러그 구합니다'}]
        catalog = {'G': {'category': '홈>러그', 'name': 'carpet'},
                   'F': {'category': '식품>김치', 'name': 'food'}}
        row = {'k': 'settle', 'ts': 50, 'seq': 1, 'sku': 'G', 'honest': True}
        result = search(posts, catalog, {'units': [{'sku': 'G'}]},
                        [row, row, dict(row, k='result', seq=2), dict(row, honest=False, seq=3)], 100)
        self.assertEqual(len(result['leads']), 1)
        self.assertEqual(result['leads'][0]['candidates'][0]['paid_6h'], 1)
        self.assertEqual(result['leads'][0]['candidates'][0]['own_stock'], 1)
        self.assertNotIn('buy', result)

    def test_unmatched_request_is_visible_without_fabricated_candidate(self):
        result = search([{'id': 1, 'role': 'buyer', 'ts': 50, 'text': '생선 구합니다'}], {}, {}, [], 100)
        self.assertEqual(result['leads'][0]['candidates'], [])
