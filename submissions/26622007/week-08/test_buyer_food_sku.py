import unittest
from unittest.mock import patch
import buyer_food_sku as f

class FoodSku(unittest.TestCase):
    def test_paid_prior_policy(self):
        offers = [dict(sku="a", list_price=100), dict(sku="b", list_price=200)]
        rows = [dict(order="old1", shop="url", sku="b", price=120, time=990,
                     use_within_hours=10), dict(order="old2", shop="url", sku="b",
                     price=100, time=980, use_within_hours=10)]
        orders = [dict(order=x["order"], kind="food", use_within_hours=10,
                       ts=995, seconds=dict(end=20), sku="b", shop="name",
                       passed_to_kit=True) for x in rows]
        pays = [dict(order=x["order"], paid=True) for x in rows]
        h = f.evidence(pays, rows, orders, 1000, 5)
        self.assertEqual(f.candidate(offers, offers[0], "url", h, lambda o: 140)["sku"], "b")
        self.assertEqual(f.evidence([dict(order="old1",paid=False)], rows, orders,1000,5), {})
        rows[0]["time"] = 1000
        h = f.evidence(pays, rows, orders,1000,5)
        self.assertIsNone(f.candidate(offers, offers[0], "url", h, lambda o:140))

class Paths(unittest.TestCase):
    def test_canonical_paths_and_fixed_memory_hint(self):
        import market_buyer as m
        a = dict(sku="a", name="a", list_price=100, qty=2)
        b = dict(sku="b", name="b", list_price=200, qty=2)
        s = dict(name="s", url="url", usable=[a,b],
                 _food_sku=dict(url="url", selected="b"))
        self.assertEqual(m.selected_offer(s,150)["sku"], "b")
        asks = dict(a=[100]*10,b=[1]*10)
        self.assertFalse(m.hopeless([s],150,asks,{}))
        with patch.object(m,"quote") as quote:
            threads = m.start_quotes([s],dict(kind="food"),150,lambda sku:0,{},m.quote_ctl())
            for thread in threads.values():
                thread.join()
        self.assertEqual(quote.call_args.args[2], "b")
        self.assertEqual(s["_offer"]["sku"], "b")
        s["_food_sku"] = None
        self.assertEqual(m.selected_offer(s,150)["sku"],"a")

    def test_default_and_scope_no_history_io(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d, patch.object(f,"read_rows",side_effect=AssertionError):
            self.assertIsNone(f.reserve(d,"none",dict(kind="food",order="o",_buyer_started_at=10),[],
                                       100,None,None,10))
            with patch.object(f,"ENABLED",True), patch.object(f,"START",1), patch.object(f,"END",1801):
                self.assertIsNone(f.reserve(d,"none",dict(kind="goods",order="o",_buyer_started_at=10),[],
                                           100,None,None,10))

    def test_reject_bad_paid_price_and_unknown_time(self):
        for price in (True,float("nan"),10**100,-1,"5"):
            self.assertFalse(f.integer(price))
        self.assertFalse(f.number(True))
        self.assertFalse(f.number(float("nan")))
        self.assertEqual(f.evidence([],[],[],True,0),{})

    def test_snapshot_reused_without_history_read(self):
        import tempfile, os, json
        import market_buyer as m
        order=dict(kind="food",order="new",_buyer_started_at=1000,use_within_hours=5)
        s=dict(name="s",url="url",usable=[dict(sku="a",list_price=100),dict(sku="b",list_price=200)])
        with tempfile.TemporaryDirectory() as d, patch.object(f,"ENABLED",True), patch.object(f,"START",900), patch.object(f,"END",2700):
            os.mkdir(os.path.join(d,"measure"))
            for path in ("payments.jsonl","settled.jsonl","measure/orders.jsonl"):
                with open(os.path.join(d,path),"w") as out:
                    out.write("")
            first=f.reserve(d,os.path.join(d,"settled.jsonl"),order,[s],150,m.pick_offer,lambda o:140,1000)
            with patch.object(f,"read_rows",side_effect=AssertionError):
                again=f.reserve(d,"unread",order,[s],150,m.pick_offer,lambda o:140,1001)
            self.assertEqual(first,again)
            self.assertIsNone(first["arm"])

if __name__ == "__main__":
    unittest.main()
