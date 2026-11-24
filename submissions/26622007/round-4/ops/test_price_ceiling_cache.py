import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


CLI = Path(__file__).with_name('price_ceiling_cache.py')
FIRST_HASH = '63363a384a5fc9f697413597cfd90874bbeaccf8fa69da9c96ceaf9fc442995b'


class CacheCLI(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / 'result.json'
        self.catalog = [dict(sku='rice', name='밥', category='식품>시리얼/곡물/콩류>곡물',
                             kind='food', list_price=100, shelf_hours=24),
                        dict(sku='pen', name='펜', category='문구>펜', kind='goods',
                             list_price=30, shelf_hours=None)]
        self.events = []
        self.add('round', {}, 1794929865.983, 0)
        self.add('join', {'shop': 'NEVER-EXPORT'}, 1795514415.932)
        self.add('supply', dict(sku='rice', qty=1, total=12, units=['a']), 1795514416)
        self.add('supply', dict(sku='rice', qty=3, total=108, units=['b', 'c', 'd'], storage=5), 1795514417)
        self.add('settle', dict(sku='rice', unit='a', amount=51), 1795514418)
        self.add('settle', dict(sku='rice', unit='b', amount=60), 1795514419)
        self.add('settle', dict(sku='rice', unit='c', amount=999), 1795518700)

    def add(self, kind, data, stamp, round_id=4):
        seq = len(self.events) + 1
        self.events.append(dict(kind=kind, data=data, ts=stamp, round=round_id, seq=seq,
                                hash=FIRST_HASH if seq == 1 else format(seq, '064x'),
                                prev=self.events[-1]['hash'] if self.events else '0' * 64))

    def generate(self, *extra):
        for name, value in [('catalog', self.catalog), ('ledger', self.events)]:
            (self.root / (name + '.json')).write_text(json.dumps(value), encoding='utf-8')
        return subprocess.run([sys.executable, '-I', str(CLI), '--data-dir', str(self.root),
                               '--output', str(self.output), *extra], capture_output=True, text=True)

    def test_latest_observations_and_aggregates_only(self):
        result = self.generate()
        self.assertEqual(result.returncode, 0, result.stderr)
        raw = self.output.read_text(encoding='utf-8')
        self.assertNotIn('NEVER-EXPORT', raw)
        cache = json.loads(raw)
        self.assertEqual(cache['items']['rice'], ['밥', 'food', 100, 24, True, 60, 36, 3, 4])
        self.assertEqual(cache['items']['pen'], ['펜', 'goods', 30, None, False, None, None, 0, 0])
        self.assertEqual(cache['rates'], {'food': [3, 4], 'goods': [0, 0]})
        self.assertGreater(cache['sample']['observed_minutes'], 70)

    def test_rollback_and_failure_preserve_last_good_bytes(self):
        self.assertEqual(self.generate().returncode, 0)
        first = self.output.read_bytes()
        self.catalog[0]['list_price'] = 200
        self.assertEqual(self.generate().returncode, 0)
        second = self.output.read_bytes()
        self.assertNotEqual(first, second)
        self.assertEqual(self.generate().returncode, 0)
        backup = self.root / 'result.json.bak'
        self.assertEqual(backup.read_bytes(), first)
        self.events[2]['data']['qty'] = False
        self.assertNotEqual(self.generate().returncode, 0)
        self.assertEqual(self.output.read_bytes(), second)
        self.assertEqual(backup.read_bytes(), first)

    def test_rejects_partial_or_duplicate_ledger(self):
        self.events.pop(2)
        self.assertNotEqual(self.generate().returncode, 0)
        self.assertFalse(self.output.exists())

    def test_rejects_shorter_observation_when_refreshing(self):
        self.assertEqual(self.generate().returncode, 0)
        first = self.output.read_bytes()
        self.events.pop()
        self.assertNotEqual(self.generate().returncode, 0)
        self.assertEqual(self.output.read_bytes(), first)

    def test_rejects_symlinked_input(self):
        (self.root / 'ledger.json').symlink_to(self.root / 'elsewhere.json')
        self.assertNotEqual(self.generate().returncode, 0)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
