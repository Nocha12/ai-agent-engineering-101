import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class MyStateInventoryTest(unittest.TestCase):
    def test_sku_totals_are_complete_even_with_small_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            inventory = {"units": [
                {"sku": "A", "category": "goods", "cost": 10},
                {"sku": "A", "category": "goods", "cost": 20},
                {"sku": "B", "category": "food", "cost": 5},
            ]}
            Path(directory, "inventory.json").write_text(json.dumps(inventory))
            run = subprocess.run(
                [sys.executable, "tools/my_state/tool.py"], input='{"limit": 1}',
                text=True, capture_output=True, check=True,
                env={**os.environ, "MARKET_DIR": directory},
            )
        result = json.loads(run.stdout)
        self.assertEqual(result["by_sku"], {"A": {"units": 2, "cost": 30}, "B": {"units": 1, "cost": 5}})
        self.assertEqual(result["stock_cost"], 35)
