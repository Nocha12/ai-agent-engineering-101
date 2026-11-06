import json
import os
import tempfile
import unittest

import forum
import rival_vote as rv

BOARD = {"players": [
    {"player": "buzllightyear", "total": 1.448, "buyer": {"name": "buzllightyear"}},
    {"player": "MJforge", "total": 1.435, "buyer": {"name": "mjforge"}},
    {"player": "Nocha12", "total": 1.435, "buyer": {"name": "nocha12"}},
    {"player": "olstjr", "total": 1.377, "buyer": {"name": "olstjr"}},
]}
POSTS = [{"id": i, "author": "mjforge", "role": "buyer"} for i in (10, 11, 12, 13)] + \
        [{"id": 20, "author": "olstjr", "role": "buyer"}]


def sent(post, value=1, fee=0):
    return {"sent": {"vote": post, "value": value, "as": "buyer"}, "status": 200,
            "answer": json.dumps({"ok": True, "fee": fee, "next_dislike_fee": 0})}


class RivalVote(unittest.TestCase):
    def test_rivals_only_within_band(self):
        self.assertEqual(rv.rivals(BOARD), ["mjforge"])
        self.assertEqual(rv.rivals(BOARD, band=0.02), ["mjforge", "buzllightyear"])

    def test_flips_my_likes_with_enough_dislikes(self):
        authors = {"1": "mjforge", "2": "mjforge"}       # two posts I liked earlier
        lines, _ = rv.plan(["mjforge"], POSTS, authors, [sent(1), sent(2)], {}, 0, 1000)
        self.assertEqual([x["vote"] for x in lines], [13, 12, 11])
        self.assertTrue(all(x["value"] == -1 for x in lines))

    def test_never_pays(self):
        lines, why = rv.plan(["mjforge"], POSTS, {}, [], {}, 20000, 1000)
        self.assertEqual(lines, [])
        lines, _ = rv.plan(["mjforge"], POSTS, {}, [], {}, None, 1000)
        self.assertEqual(lines, [])
        lines, why = rv.plan(["mjforge"], POSTS, {}, [sent(99, -1, fee=20000)], {}, 0, 1000)
        self.assertEqual(lines, [])
        self.assertIn("charged", why)

    def test_hourly_and_total_caps(self):
        lines, _ = rv.plan(["mjforge"], POSTS, {}, [], {"opened": [900, 950, 990]}, 0, 1000)
        self.assertEqual(lines, [])
        lines, _ = rv.plan(["mjforge"], POSTS, {}, [], {"opened": [900 - 3600]}, 0, 1000)
        self.assertEqual(len(lines), 1)
        full = {"disliked": {f"b{i}": 0 for i in range(rv.MAX_STANCES)}}
        self.assertEqual(rv.plan(["mjforge"], POSTS, {}, [], full, 0, 1000)[0], [])

    def test_waits_without_enough_posts(self):
        authors = {str(i): "mjforge" for i in range(1, 6)}
        lines, _ = rv.plan(["mjforge"], POSTS, authors, [sent(i) for i in range(1, 6)], {}, 0, 1000)
        self.assertEqual(lines, [])

    def test_maybe_run_writes_once_and_forum_stops_liking(self):
        with tempfile.TemporaryDirectory() as md:
            json.dump(POSTS, open(os.path.join(md, "forum.json"), "w"))
            json.dump({"next_dislike_fee": 0}, open(os.path.join(md, "inventory.json"), "w"))
            lines = rv.maybe_run(md, fetch=lambda: BOARD, now=10000)
            self.assertEqual([x["vote"] for x in lines], [13])
            self.assertIsNone(rv.maybe_run(md, fetch=lambda: BOARD, now=10060))      # not due
            self.assertIsNone(rv.maybe_run(md, fetch=lambda: BOARD, now=10000 + 700))  # already disliked
            with open(os.path.join(md, "outbox.jsonl")) as f:
                out = [json.loads(x) for x in f]
            self.assertEqual(out, [{"vote": 13, "value": -1, "as": "buyer"}])
            self.assertEqual(rv.avoid(md), {"mjforge"})
            cfg = {"max_chars": 600, "max_votes": 10}
            final = {"post": "", "refs": [], "thread": "", "votes": [{"post": "10", "value": 1}, {"post": "20", "value": 1}]}
            got, _ = forum.clean(final, POSTS, set(), set(), cfg, avoid=rv.avoid(md))
            self.assertEqual(got, [{"vote": 20, "value": 1, "as": "buyer"}])

    def test_a_like_waiting_in_the_outbox_counts(self):
        lines, _ = rv.plan(["mjforge"], POSTS, {"10": "mjforge"}, [], {}, 0, 1000, queued=[{"vote": 10, "value": 1, "as": "buyer"}])
        self.assertEqual([x["vote"] for x in lines], [13, 12])

    def test_already_disliked_sends_nothing(self):
        state = {}
        lines, _ = rv.plan(["mjforge"], POSTS, {"1": "mjforge"}, [sent(1, -1)], state, 0, 1000)
        self.assertEqual(lines, [])
        self.assertIn("mjforge", state["disliked"])

    def test_vote_room_per_hour(self):
        authors = {"1": "mjforge", "2": "mjforge"}
        lines, _ = rv.plan(["mjforge"], POSTS, authors, [sent(1), sent(2)], {"votes": [[990, 1]]}, 0, 1000)
        self.assertEqual(lines, [])        # 3 votes needed, 2 left this hour

    def test_refused_dislike_is_released(self):
        state = {"disliked": {"mjforge": 1}}
        refused = {"sent": {"vote": 13, "value": -1, "as": "buyer"}, "status": 402, "answer": "{}"}
        rv.recheck(state, {"13": "mjforge"}, [refused], [])
        self.assertEqual(state["disliked"], {})
        state = {"disliked": {"mjforge": 1}}
        rv.recheck(state, {"13": "mjforge"}, [sent(13, -1)], [])
        self.assertIn("mjforge", state["disliked"])

    def test_avoid_survives_a_bad_state_file(self):
        with tempfile.TemporaryDirectory() as md:
            with open(os.path.join(md, rv.STATE), "w") as f:
                json.dump([1, 2], f)
            self.assertEqual(rv.avoid(md), set())
            self.assertIsNone(rv.maybe_run(md, fetch=lambda: {"players": []}, now=10000))

    def test_board_failure_is_quiet(self):
        with tempfile.TemporaryDirectory() as md:
            def boom():
                raise ValueError("bad json")
            self.assertIsNone(rv.maybe_run(md, fetch=boom, now=10000))


if __name__ == "__main__":
    unittest.main()
