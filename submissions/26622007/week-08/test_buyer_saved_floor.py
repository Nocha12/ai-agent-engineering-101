"""buyer_saved_floor: the limit keeps my current Saved, not the fixed 0.30 (W-20261109-1638-AXPro주기)."""
import json
import os
import tempfile
import time
import unittest

import buyer_saved_floor as sf
import market_buyer as mb


def board(md, saved, ts=None):
    with open(os.path.join(md, "board_history.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": ts if ts is not None else time.time(), "buyers": {"nocha12": [saved, 0.89, 0.79]}}) + "\n")


def switch(md, **kw):
    with open(os.path.join(md, sf.SWITCH), "w", encoding="utf-8") as f:
        json.dump({"enabled": True, **kw}, f)


class Share(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.md = self.d.name

    def tearDown(self):
        self.d.cleanup()

    def test_off_without_switch(self):
        board(self.md, 0.3096)
        self.assertIsNone(sf.state(self.md, "o-1"))

    def test_off_by_env(self):
        board(self.md, 0.3096)
        switch(self.md)
        self.assertIsNone(sf.state(self.md, "o-1", env={"BUYER_SAVED_FLOOR": "0"}))

    def test_not_enabled_true_is_off(self):
        board(self.md, 0.3096)
        with open(os.path.join(self.md, sf.SWITCH), "w") as f:
            json.dump({"enabled": "true"}, f)
        self.assertIsNone(sf.state(self.md, "o-1"))

    def test_on_uses_board_saved(self):
        board(self.md, 0.3096)
        switch(self.md)
        st = sf.state(self.md, "o-1", env={})
        self.assertEqual(st["arm"], "B")
        self.assertAlmostEqual(st["share"], 0.3096)
        self.assertTrue(st["band_off"])

    def test_margin_and_bounds(self):
        board(self.md, 0.3096)
        switch(self.md, margin=0.002)
        self.assertAlmostEqual(sf.state(self.md, "o-1", env={})["share"], 0.3116)
        switch(self.md, margin=0.5)            # out of range: off (review I2)
        self.assertIsNone(sf.state(self.md, "o-1", env={}))
        board(self.md, 0.62)                   # a board Saved over MAX_SHARE is not trusted: off (review N6)
        switch(self.md)
        self.assertIsNone(sf.state(self.md, "o-1", env={}))

    def test_bad_config_is_off(self):        # review I2: a broken setting never turns the floor on
        board(self.md, 0.3096)
        for bad in ({"ab": None}, {"band": "Keep"}, {"margin": "0.01"}, {"kinds": None}, {"unknown": 1}, {"stop_at": 1}):
            switch(self.md, **bad)
            self.assertIsNone(sf.state(self.md, "o-1", env={}, kind="food"), bad)

    def test_malformed_board_is_off(self):   # review I1: never raises into the buyer run
        switch(self.md)
        for line in ({"ts": time.time(), "buyers": [1]}, {"ts": time.time(), "buyers": {"nocha12": "x"}}, [1, 2], "s"):
            with open(os.path.join(self.md, "board_history.jsonl"), "w") as f:
                f.write(json.dumps(line) + "\n")
            self.assertIsNone(sf.state(self.md, "o-1", env={}), line)
        with open(os.path.join(self.md, "board_history.jsonl"), "wb") as f:
            f.write(b"\xff\xfe{bad\n")
        self.assertIsNone(sf.state(self.md, "o-1", env={}))

    def test_never_under_base(self):
        board(self.md, 0.25)
        switch(self.md)
        self.assertEqual(sf.state(self.md, "o-1", env={})["share"], mb.SAVED_MIN_SHARE)

    def test_stale_board_is_off(self):
        board(self.md, 0.3096, ts=time.time() - 4 * 3600)
        switch(self.md)
        self.assertIsNone(sf.state(self.md, "o-1", env={}))

    def test_band_kept_when_asked(self):
        board(self.md, 0.3096)
        switch(self.md, band="keep")
        self.assertFalse(sf.state(self.md, "o-1", env={})["band_off"])

    def test_ab_split_is_by_order_hash(self):
        board(self.md, 0.3096)
        switch(self.md, ab={"salt": "sf-test", "share": 0.5})
        arms = [sf.state(self.md, f"o-{i}", env={})["arm"] for i in range(400)]
        self.assertTrue(150 < arms.count("B") < 250)
        self.assertEqual(arms, [sf.state(self.md, f"o-{i}", env={})["arm"] for i in range(400)])
        a = next(sf.state(self.md, f"o-{i}", env={}) for i in range(400) if arms[i] == "A")
        self.assertEqual(a["share"], mb.SAVED_MIN_SHARE)
        self.assertFalse(a["band_off"])

    def test_kinds_limits_the_floor(self):
        board(self.md, 0.3096)
        switch(self.md, kinds=["food"])
        self.assertEqual(sf.state(self.md, "o-1", env={}, kind="food")["arm"], "B")
        self.assertIsNone(sf.state(self.md, "o-1", env={}, kind="goods"))
        self.assertIsNone(sf.state(self.md, "o-1", env={}))          # kind unknown: off
        switch(self.md, kinds="food")                                 # not a list: off
        self.assertIsNone(sf.state(self.md, "o-1", env={}, kind="food"))

    def test_band_only_keeps_base_share(self):   # GPT Pro 1638 §2.4: the first A/B is the band off alone
        board(self.md, 0.3096)
        switch(self.md, floor=False)
        st = sf.state(self.md, "o-1", env={})
        self.assertEqual((st["arm"], st["share"], st["band_off"]), ("B", mb.SAVED_MIN_SHARE, True))
        switch(self.md, floor=False, band="keep")      # nothing would change: off
        self.assertIsNone(sf.state(self.md, "o-1", env={}))
        switch(self.md, floor="no")
        self.assertIsNone(sf.state(self.md, "o-1", env={}))

    def test_empty_order_id_is_off_under_ab(self):
        board(self.md, 0.3096)
        switch(self.md, ab={"salt": "s", "share": 0.5})
        self.assertIsNone(sf.state(self.md, None, env={}))
        self.assertIsNone(sf.state(self.md, "", env={}))

    def test_ab_bad_share_is_off(self):
        board(self.md, 0.3096)
        switch(self.md, ab={"salt": "x", "share": 2})
        self.assertIsNone(sf.state(self.md, "o-1", env={}))


class Learning(unittest.TestCase):
    """Arm B's trades and talks are left out of what both arms learn from (GPT Pro 1638 P1)."""

    def test_priced_leaves_out_arm_b(self):
        trades = [{"order": "1", "saved_floor": "B"}, {"order": "2", "saved_floor": "A"}, {"order": "3"},
                  {"order": "4", "saved_floor": None}]
        self.assertEqual([t["order"] for t in sf.priced(trades)], ["2", "3", "4"])
        self.assertEqual([t["order"] for t in mb.priced(trades)], ["2", "3", "4"])

    def test_rows_and_trade_arm(self):
        self.assertTrue(sf.floored({"saved_floor": {"arm": "B"}}))
        self.assertFalse(sf.floored({"saved_floor": {"arm": "A"}}))
        self.assertFalse(sf.floored({}))
        self.assertEqual(sf.trade_arm({"_saved_floor": {"arm": "B"}}), "B")
        self.assertIsNone(sf.trade_arm({}))

    def test_learners_skip_arm_b_rows(self):
        import buyer_rank_pg, buyer_shop_profile
        for mod in (buyer_rank_pg, buyer_shop_profile):
            src = open(mod.__file__, encoding="utf-8").read()
            self.assertIn("buyer_saved_floor.floored(r)", src, mod.__name__)
        src = open(mb.__file__, encoding="utf-8").read()
        self.assertIn('"saved_floor": buyer_saved_floor.trade_arm(order)', src)


class Cap(unittest.TestCase):
    def tearDown(self):
        mb.SAVED_SHARE = mb.SAVED_MIN_SHARE

    def test_saved_cap_default_follows_saved_share(self):
        self.assertEqual(mb.saved_cap({"list_price": 10000}, 0), 7000)
        mb.SAVED_SHARE = 0.31
        self.assertEqual(mb.saved_cap({"list_price": 10000}, 0), int(10000 * (1 - 0.31)))
        self.assertEqual(mb.saved_cap({"list_price": 10000}, 0, 0.30), 7000)   # an explicit share still wins

    def test_main_sets_and_logs_the_floor(self):
        src = open(mb.__file__, encoding="utf-8").read()
        i = src.index("def main():")
        self.assertIn("buyer_saved_floor.state(md", src[i:])
        self.assertIn('kind=order.get("kind")', src[i:])
        self.assertIn("SAVED_SHARE = ", src[i:])
        j = src.index("band_base, band_pct = accept_percent(", i)
        self.assertIn("band_off", src[j:j + 600])


if __name__ == "__main__":
    unittest.main()
