"""market_buyer reads the ledger rows (141 MB, 0.73 s) and the break stats (0.27 s) once per run, not at each use."""
import json
import threading
import unittest
from unittest import mock

import market_buyer
import market_model


class LedgerCache(unittest.TestCase):
    def setUp(self):
        market_buyer._LEDGER.clear()

    def test_rows_and_stats_are_read_once_per_run(self):
        with mock.patch.object(market_buyer.market_model, "load_rows", return_value=[{"seq": 1}]) as rows, \
                mock.patch.object(market_buyer.buyer_stats, "break_stats", return_value={"by_shop": {}}) as stats:
            self.assertEqual(market_buyer.ledger_rows("md"), [{"seq": 1}])
            self.assertEqual(market_buyer.ledger_rows("md"), [{"seq": 1}])
            self.assertEqual(market_buyer.ledger_breaks("md", {}), {"by_shop": {}})
            self.assertEqual(market_buyer.ledger_breaks("md", {}), {"by_shop": {}})
        self.assertEqual(rows.call_count, 1)
        self.assertEqual(stats.call_count, 1)

    def test_threads_at_once_read_it_once(self):
        gate = threading.Event()

        def slow(md):
            gate.wait(1)
            return []
        with mock.patch.object(market_buyer.market_model, "load_rows", side_effect=slow) as rows:
            ts = [threading.Thread(target=market_buyer.ledger_rows, args=("md",)) for _ in range(3)]
            for t in ts:
                t.start()
            gate.set()
            for t in ts:
                t.join()
        self.assertEqual(rows.call_count, 1)

    def test_warm_never_raises(self):
        with mock.patch.object(market_buyer.market_model, "load_rows", side_effect=OSError("gone")):
            t = market_buyer.warm_ledger("md")
            t.join()
        with mock.patch.object(market_buyer.market_model, "load_rows", return_value=[]) as rows:
            market_buyer.ledger_rows("md")      # a failed warm-up leaves the read to the first use
        self.assertEqual(rows.call_count, 1)


class CountingRows(list):
    def __iter__(self):
        self.scans = getattr(self, "scans", 0) + 1
        return super().__iter__()


class BuyerStatsTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(market_buyer._LEDGER)
        market_buyer._LEDGER.clear()
        self.addCleanup(lambda: (market_buyer._LEDGER.clear(), market_buyer._LEDGER.update(self.saved)))

    def test_all_sales_snapshot_is_classified_once_without_changing_link_priority(self):
        catalog = {"g": {"kind": "goods"}}
        rows = [
            {"k": "settle", "seq": 1, "ts": 10, "buyer": "b", "sku": "g", "order": "one", "grade": "A"},
            {"k": "settle", "seq": 2, "ts": 11, "buyer": "b", "sku": "g", "order": "two", "grade": "B"},
            {"k": "broken", "ts": 12, "buyer": "b", "sku": "g"},
            {"k": "broken", "ts": 13, "buyer": "b", "sku": "g", "order": "two"},
            {"k": "settle", "seq": 3, "ts": 0, "sku": "g", "grade": "C"},
        ]
        expected = market_model.break_stats(rows, catalog)
        counted = CountingRows(rows)
        with mock.patch.object(market_buyer.market_model, "load_rows", return_value=counted):
            actual = market_buyer.ledger_breaks("fixture-market", catalog)
            self.assertEqual(json.dumps(actual), json.dumps(expected))
            self.assertEqual(actual["sold"], 3)
            self.assertEqual(actual["broke"], 2)
            self.assertEqual(counted.scans, 1)
            self.assertIs(actual, market_buyer.ledger_breaks("fixture-market", {}))


if __name__ == "__main__":
    unittest.main()
