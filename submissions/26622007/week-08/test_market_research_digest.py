import json
import tempfile
import unittest
from pathlib import Path

import agent_tools
from tools.auto_market_research_digest.tool import digest


class DigestTest(unittest.TestCase):
    def test_sandbox_sku_intersection_and_paid_dedup(self):
        cat = {'G1': {'name': 'bed', 'category': 'furniture'},
               'G2': {'name': 'bed', 'category': 'bedding'}}
        rows = [{'k': 'settle', 'seq': 1, 'ts': 1, 'sku': 'G1'},
                {'k': 'settle', 'seq': 1, 'ts': 1, 'sku': 'G1'},
                {'k': 'result', 'seq': 2, 'sku': 'G1'},
                {'k': 'settle', 'seq': 3, 'sku': 'G2'}]
        posts = [{'ts': 3, 'text': 'G1 available'}, {'ts': 4, 'text': 'G2 available'}]
        with tempfile.TemporaryDirectory() as d:
            md = Path(d) / '.market'; md.mkdir()
            (md / 'catalog.json').write_text(json.dumps(cat))
            (md / 'ledger_rows.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            (md / 'forum.json').write_text(json.dumps(posts))
            result = agent_tools.execute('auto_market_research_digest', {'query': 'bed', 'category': 'furniture'}, str(md))
        self.assertNotIn('error', result)
        self.assertEqual([p['sku'] for p in result['catalog']], ['G1'])
        self.assertEqual([r['seq'] for r in result['recent_paid_deals']], [1])
        self.assertEqual([p['text'] for p in result['recent_forum_posts']], ['G1 available'])
        self.assertEqual(digest(cat, rows, posts, {'query': 'missing'})['paid_deal_count'], 0)
