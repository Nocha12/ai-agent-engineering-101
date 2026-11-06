"""market_buyer reads the ledger rows (141 MB, 0.73 s) and the break stats (0.27 s) once per run, not at each use."""
import threading
import unittest
from unittest import mock

import market_buyer


class LedgerCache(unittest.TestCase):
    def setUp(self):
        market_buyer._LEDGER.clear()

    def test_rows_and_stats_are_read_once_per_run(self):
        with mock.patch.object(market_buyer.market_model, "load_rows", return_value=[{"seq": 1}]) as rows, \
                mock.patch.object(market_buyer.market_model, "break_stats", return_value={"by_shop": {}}) as stats:
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


if __name__ == "__main__":
    unittest.main()
