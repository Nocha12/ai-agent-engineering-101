"""Seller restart without losing talks (대표님 11/07 14:51): with the drain file naming this process, new talks are refused,
talks under way and deals waiting for their payment go on, and the status file says when none is left.
A restarted seller has another pid, so the same file does not stop it."""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import seller
from seller_drain import Drain
from shop_stock import HOLD_SECONDS, StockBook

INV = {"round": 3, "units": [{"unit": "u1", "sku": "Gaaaaaaaa", "name": "chair"}, {"unit": "u2", "sku": "Gbbbbbbbb", "name": "desk"}]}


def book():
    return StockBook(load=lambda: INV)


class SentinelTest(unittest.TestCase):
    def test_only_the_named_process_drains(self):
        with tempfile.TemporaryDirectory() as d:
            drain = Drain(root=d, pid=4242)
            self.assertFalse(drain.active())
            Path(d, "seller.drain").write_text("999\n")
            self.assertFalse(drain.active())            # the restarted seller: another pid
            Path(d, "seller.drain").write_text("4242\n")
            self.assertTrue(drain.active())
            Path(d, "seller.drain").write_text("not a pid")
            self.assertFalse(drain.active())


class CensusTest(unittest.TestCase):
    def test_talks_holds_and_payment_waits_keep_it_from_drained(self):
        stock = book()
        shop = SimpleNamespace(open={}, stock=stock)
        drain = Drain(root=tempfile.mkdtemp(), pid=1)
        self.assertTrue(drain.census(shop)["drained"])
        stock.hold("u1", "t1")
        shop.open["t1"] = {"unit": "u1"}
        c = drain.census(shop)
        self.assertEqual((c["open"], c["held"], c["drained"]), (1, 1, False))
        stock.agree("u1", {"task": "t1", "deal": {"id": "Gaaaaaaaa", "unit": "u1"}, "price": 10})
        del shop.open["t1"]
        c = drain.census(shop)
        self.assertEqual((c["open"], c["agreed"], c["drained"]), (0, 1, False))
        stock.mark_sold("u1")
        self.assertTrue(drain.census(shop)["drained"])

    def test_a_talk_whose_hold_ran_out_is_stale_not_waited_for(self):
        stock = book()
        stock.hold("u2", "t2")
        stock.held["u2"] = time.time() - HOLD_SECONDS - 1
        shop = SimpleNamespace(open={"t2": {"unit": "u2"}}, stock=stock)
        c = Drain(root=tempfile.mkdtemp(), pid=1).census(shop)
        self.assertEqual((c["open"], c["stale"], c["held"], c["drained"]), (0, 1, 0, True))

    def test_a_turn_being_answered_counts(self):
        drain = Drain(root=tempfile.mkdtemp(), pid=1)
        shop = SimpleNamespace(open={}, stock=book())
        with drain.turn():
            self.assertFalse(drain.census(shop)["drained"])
        self.assertTrue(drain.census(shop)["drained"])
        with self.assertRaises(RuntimeError):
            with drain.turn():
                raise RuntimeError
        self.assertEqual(drain.inflight, 0)

    def test_status_file_is_written_only_while_draining_and_flushes_first(self):
        with tempfile.TemporaryDirectory() as d:
            drain = Drain(root=d, pid=7)
            shop = SimpleNamespace(open={}, stock=book())
            flushed = []
            asyncio.run(drain.tick(shop, lambda: flushed.append(1)))
            self.assertFalse(Path(d, "seller_drain_status.json").exists())
            Path(d, "seller.drain").write_text("7")
            asyncio.run(drain.tick(shop, lambda: flushed.append(1)))
            s = json.loads(Path(d, "seller_drain_status.json").read_text())
            self.assertEqual((s["pid"], s["drained"], s["flushed"]), (7, True, True))
            self.assertEqual(flushed, [1])


class PersistenceTest(unittest.TestCase):
    def test_failed_memory_write_blocks_flush(self):
        s = seller.Seller(catalog=None, name='test', stock=book())
        deal = dict(name='chair', id='Gaaaaaaaa', reserve=1, history=[])
        with mock.patch.object(seller, 'MEMORY_FAILURES', []), \
                mock.patch.object(seller.CONCESSION_TRIAL, 'event'), \
                mock.patch.object(seller.shop_memory, 'record', side_effect=OSError('disk')), \
                mock.patch.object(seller, 'log'):
            s._remember_now('t', deal, 'rejected')
            with self.assertRaises(RuntimeError):
                seller.flush_memory(strict=True)


class QuietContractTest(unittest.TestCase):
    def test_stale_and_wrong_pid_and_old_status_cannot_authorize_restart(self):
        from seller_drain_wait import quiet
        c = dict(pid=7, at=101, quiet=True, flushed=True,
                 inflight=0, open=0, held=0, agreed=0, late=0, stale=0)
        self.assertTrue(quiet(c, 7, 100, 102))
        self.assertFalse(quiet(c, 8, 100, 102))
        self.assertFalse(quiet(c, 7, 102, 103))
        self.assertFalse(quiet(c, 7, 100, 112))
        self.assertFalse(quiet(dict(c, stale=1), 7, 100, 102))


class FlushRaceTest(unittest.TestCase):
    def test_rechecks_census_after_flush_yields(self):
        async def check():
            with tempfile.TemporaryDirectory() as d:
                drain = Drain(root=d, pid=7)
                Path(d, 'seller.drain').write_text('7')
                shop = SimpleNamespace(open={}, stock=book())
                def flush():
                    shop.open['during-flush'] = {'unit': None}
                c = await drain.tick(shop, flush)
                self.assertFalse(c['quiet'])
                self.assertFalse(c['drained'])
        asyncio.run(check())


class FakeUpdater:
    calls = []

    def __init__(self, **kw):
        pass

    def new_agent_message(self, parts, metadata=None):
        return parts

    async def reject(self, msg):
        FakeUpdater.calls.append("reject")

    async def failed(self, msg):
        FakeUpdater.calls.append("failed")


class Passed(Exception):
    pass


class AdmissionTest(unittest.TestCase):
    def run_turn(self, shop, task_id, active):
        FakeUpdater.calls = []
        ctx = SimpleNamespace(current_task=SimpleNamespace(id=task_id, context_id="c"), message=None)
        queue = SimpleNamespace(enqueue_event=mock.AsyncMock())
        drain = Drain(root=tempfile.mkdtemp(), pid=1)
        with mock.patch.object(seller, "TaskUpdater", FakeUpdater), \
                mock.patch.object(seller, "new_text_part", lambda t: t), \
                mock.patch.object(seller, "get_message_text", lambda m: "I want a chair"), \
                mock.patch.object(seller, "log"), mock.patch.object(seller, "DRAIN", drain), \
                mock.patch.object(drain, "active", return_value=active), \
                mock.patch.object(seller.CONCESSION_TRIAL, "restore", side_effect=Passed):
            try:
                asyncio.run(shop.execute(ctx, queue))
            except Passed:
                return "continued", drain
        return FakeUpdater.calls, drain

    def shop(self):
        s = seller.Seller(catalog=None, name="t", stock=book())
        s.open_from_stock = mock.AsyncMock()
        return s

    def test_a_new_talk_is_refused_while_draining(self):
        s = self.shop()
        calls, drain = self.run_turn(s, "new", active=True)
        self.assertEqual(calls, ["reject"])
        s.open_from_stock.assert_not_called()
        self.assertEqual(drain.inflight, 0)

    def test_a_talk_under_way_goes_on_while_draining(self):
        s = self.shop()
        s.open["t1"] = {"unit": "u1", "id": "Gaaaaaaaa", "rounds": 0}
        calls, drain = self.run_turn(s, "t1", active=True)
        self.assertEqual(calls, "continued")
        self.assertEqual(drain.inflight, 0)

    def test_new_talks_open_when_not_draining(self):
        s = self.shop()
        calls, _ = self.run_turn(s, "new", active=False)
        self.assertEqual(calls, [])
        s.open_from_stock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
