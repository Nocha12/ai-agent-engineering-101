import unittest
from tools.demand_inventory.tool import summarize


class DemandInventoryTest(unittest.TestCase):
    def test_only_recent_exact_buyer_requests_and_live_units(self):
        p = {'id': 1, 'ts': 100, 'role': 'buyer', 'text': '소스 구합니다. 예산은 19만 원대'}
        cat = {'G1': {'category': '식품>소스', 'list_price': 100}, 'G2': {'category': '식품>소스세트'}}
        inv = {'units': [{'sku': 'G1', 'expires_at': 200}, {'sku': 'G1', 'expires_at': 90}, {'sku': 'G2'}]}
        r = summarize([p, dict(p, role='shop'), dict(p, ts=-99999), dict(p, ts=101)], cat, inv, 100)
        self.assertEqual(len(r['requests']), 1)
        self.assertEqual(r['requests'][0]['catalog_matches'], 1)
        self.assertEqual(r['requests'][0]['own_live_units'], 1)
        self.assertIn('unverified', r['requests'][0]['caution'])

    def test_context_is_opt_in_and_contains_no_purchase_instruction(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock
        import restock
        for enabled in (False, True):
            with tempfile.TemporaryDirectory() as md:
                Path(md, 'forum.json').write_text(json.dumps([{'id': 1, 'ts': 100, 'role': 'buyer', 'text': '소스 구합니다'}]))
                seen = {}
                def decide(goal, schema, state, agent, given):
                    seen.update(given)
                    return {'buy': [], 'why': 'measurement only'}
                cfg = {**restock.DEFAULTS, 'round_ends_at': None, 'forum_demand_context': enabled}
                with mock.patch.object(restock, 'plan', return_value={'buy': []}), \
                     mock.patch.object(restock.decision_memory, 'outcomes', return_value={}), \
                     mock.patch.object(restock.agent_memory, 'learn_from_outcomes'), \
                     mock.patch.object(restock.agent_memory, 'select', return_value=''), \
                     mock.patch.object(restock.agent_memory, 'daily'):
                    restock.agent_turn(md, {'balance': 5000000, 'units': []}, {}, [], cfg, None, decide)
                self.assertEqual('c3' in seen, enabled)
                if enabled:
                    self.assertEqual(json.loads(seen['c3'])['requests'], [])
