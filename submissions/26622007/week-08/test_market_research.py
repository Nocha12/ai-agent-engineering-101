import json
import tempfile
import unittest
from pathlib import Path
import agent_tools
from tools.auto_market_research.tool import search
from test_market_scan import CAT, ROWS


class ResearchTest(unittest.TestCase):
    def test_keyed_catalog_and_paid_personal_provenance(self):
        payments = [{'ledger': 1, 'paid': True}, {'ledger': 3, 'paid': False}]
        got = search(CAT, ROWS + [ROWS[0]], payments,
                     {'query': 'bed', 'category': 'furniture', 'include_purchases': True})
        self.assertEqual(got['catalog_count'], 1)
        self.assertEqual(got['catalog'][0]['sku'], 'G1')
        self.assertEqual([r['seq'] for r in got['paid_deals']], [1])
        self.assertEqual([r['seq'] for r in got['personal_history']], [1])
        self.assertEqual(search(CAT, ROWS, payments, {'shop': 'shop-b', 'include_purchases': True})['personal_history'], [])
        self.assertEqual(search(CAT, ROWS, payments, {'query': 'missing'})['paid_deals'], [])

    def test_real_sandbox_with_jsonl_payments(self):
        with tempfile.TemporaryDirectory() as d:
            md = Path(d) / '.market'; md.mkdir()
            (md / 'catalog.json').write_text(json.dumps(CAT))
            (md / 'ledger_rows.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in ROWS))
            (md / 'payments.jsonl').write_text('{broken\n' + json.dumps({'ledger': 1, 'paid': True})+'\n')
            got = agent_tools.execute('auto_market_research', {'category': 'furniture', 'include_purchases': True}, str(md))
            self.assertNotIn('error', got)
            self.assertEqual(got['personal_history_count'], 1)
            self.assertEqual(got['paid_deal_count'], 1)
