"""The limit keeps my current Saved, not the fixed 0.30 (W-20261109-1638-AXPro주기, review/W-20261109-1638-AXPro주기).

Saved is the sum over deals of surplus / list, divided by deals + 100 (agent.md 228). A deal that keeps r of its list
moves Saved by (r - S) / (deals + 101): it lowers Saved whenever r < S, my Saved now. market_buyer.saved_cap kept a
fixed SAVED_MIN_SHARE of 0.30 and the accept band (plan A) takes up to 105% of that limit, so deals at 0.69-0.73 of
list were bought while S was 0.3096: 361 of my 1,841 deals in the 24 h to 11/09 16:41 kept r < S (goods 216, food 145),
and leaving the 288 whose price alone was over (1 - S) of list would have put Saved at .3105 instead of .3096
(ana/saved_floor.py), where ddolcom .3097 and 25512088 .3099 sit just above (one place = .0089 of buyer ALL).
The short-warranty path (buyer_short_warranty.p_max) already holds deals to S; this does the same for the 6 h path.

On, an order's share is max(SAVED_MIN_SHARE, S + margin), at most MAX_SHARE, S the board's (board_history.jsonl,
not older than buyer_short_warranty.BOARD_MAX_AGE); with band "cap" (the default) the accept band is not used, so
nothing over the limit is taken. A board that cannot be read leaves the order as before.

Switch: .market/buyer_saved_floor.json {"enabled": true, "floor": true|false, "margin": 0.0, "band": "cap"|"keep",
"ab": {"salt": "...", "share": 0.5}, "kinds": ["food", "goods"]}; missing, unreadable or anything else is off, and off is the old behaviour
exactly. With "ab", only the orders whose sha1(salt|order) falls under share are arm B (the floor); arm A is the
old behaviour and is still returned so the judgement can count both. BUYER_SAVED_FLOOR=0 in the environment is off
too. "floor": false keeps SAVED_MIN_SHARE and only turns the band off (GPT Pro 1638 §2.4: the first A/B). Arm B's
trades and orders rows are left out of the price, rank and shop-profile learning both arms read. "kinds" (a list) limits it to those order kinds, so it can start on food when the food study ends and on goods
when the goods studies end; an order of another or unknown kind is off. Read at every order (a buyer run is one
order), so no restart either way. On, it moves what other buyer studies see (goods-grade's limit_a, quote fit, the
shop profile's band records, food talks without the band): start it on a kind only after that kind's studies end, and
every order it touched carries "saved_floor" in its measure record (review I3).

Each record names the study (the ab salt), VERSION, the board snapshot it read (board_ts) and the floor flag, and the
early exit with no shop in stock writes it too, so the judgement recounts both arms by sha1(salt|order) over every order
(GPT Pro 1638 P2). With the floor on (arm B, "floor" true), a receipt for another SKU than the one asked is held to
that SKU's break chance, not b = 0 (recheck; market_buyer.receipt_chance). The band-only first A/B ("floor": false)
keeps b = 0 there, so arm B differs from arm A by the band alone.
"""
import hashlib
import json
import os
import time

import buyer_short_warranty

SWITCH = "buyer_saved_floor.json"
BASE_SHARE = 0.30          # market_buyer.SAVED_MIN_SHARE
MAX_SHARE = 0.40           # a board S above this is not trusted: off
MAX_MARGIN = 0.05
KEYS = {"enabled", "floor", "margin", "band", "ab", "kinds", "by", "since", "why", "source"}   # no start/stop: not enforced, so refused
VERSION = "sf-2"           # sf-2: records carry study/v/board_ts/floor; the floor rechecks a shipped SKU's b


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def config(md, env=None):
    """The switch file as a dict when it is on and every setting in it is good, else None (review I2: a broken
    setting never turns the floor on)."""
    env = os.environ if env is None else env
    if str(env.get("BUYER_SAVED_FLOOR") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        with open(os.path.join(md, SWITCH), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(d, dict) or d.get("enabled") is not True or set(d) - KEYS:
        return None
    if "margin" in d and not (_number(d["margin"]) and 0 <= d["margin"] <= MAX_MARGIN):
        return None
    if "band" in d and d["band"] not in ("cap", "keep"):
        return None
    if "floor" in d and not isinstance(d["floor"], bool):
        return None
    if d.get("floor") is False and d.get("band", "cap") == "keep":     # neither the share nor the band: nothing
        return None
    if "ab" in d:
        ab = d["ab"]
        if not isinstance(ab, dict) or not isinstance(ab.get("salt"), str) or not ab["salt"] \
                or not _number(ab.get("share")) or not 0 < ab["share"] <= 1:
            return None
    if "kinds" in d and not (isinstance(d["kinds"], list) and d["kinds"]
                             and all(k in ("food", "goods") for k in d["kinds"])):
        return None
    return d


def board_saved(md, now=None):
    """My Saved on the newest board snapshot, or None when it is missing, stale, over MAX_SHARE or unreadable."""
    row = board_row(md, now)
    return row[0] if row else None


def board_row(md, now=None):
    """(my Saved, the snapshot's ts) on the newest board snapshot, or None as board_saved."""
    now = now or time.time()
    try:
        with open(os.path.join(md, "board_history.jsonl"), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - buyer_short_warranty.BOARD_TAIL))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict) or not _number(d.get("ts")) \
                or not -300 <= now - d["ts"] <= buyer_short_warranty.BOARD_MAX_AGE \
                or not isinstance(d.get("buyers"), dict):
            return None
        for name, row in d["buyers"].items():
            if str(name).lower() in buyer_short_warranty.ME and isinstance(row, list) and row \
                    and _number(row[0]) and 0 < row[0] <= MAX_SHARE:
                return float(row[0]), float(d["ts"])
        return None
    return None


def arm(salt, order_id, share):
    h = int(hashlib.sha1(f"{salt}|{order_id}".encode()).hexdigest()[:8], 16) / 0x100000000
    return "B" if h < share else "A"


def state(md, order_id, now=None, env=None, base=BASE_SHARE, kind=None):
    """None when off; else {"arm", "share", "band_off", "board_saved", "board_ts", "floor", "study", "v"}: arm A keeps
    base and the band. Never raises into the buyer run (review I1): anything unexpected is off."""
    try:
        c = config(md, env)
        if c is None or ("kinds" in c and kind not in c["kinds"]):
            return None
        row = board_row(md, now)
        if row is None:
            return None
        S, ts = row
        floor = c.get("floor", True)
        meta = {"board_saved": S, "board_ts": ts, "floor": floor,
                "study": c["ab"]["salt"] if "ab" in c else None, "v": VERSION}
        if "ab" in c:
            if not isinstance(order_id, str) or not order_id:
                return None
            if arm(c["ab"]["salt"], order_id, c["ab"]["share"]) == "A":
                return {"arm": "A", "share": base, "band_off": False, **meta}
        share = min(MAX_SHARE, max(base, S + c.get("margin", 0))) if floor else base
        return {"arm": "B", "share": share, "band_off": c.get("band", "cap") == "cap", **meta}
    except Exception:   # noqa: BLE001 - the floor is optional; the order goes on as before
        return None


def recheck(st):
    """Whether a receipt for another SKU is held to that SKU's break chance: arm B with the floor on only. The band-only
    A/B (floor false), arm A and records without the flag keep b = 0 as before."""
    return isinstance(st, dict) and st.get("arm") == "B" and st.get("floor") is True


def trade_arm(order):
    """The arm market_buyer writes into the order's trade: "A", "B" or None (not in the study)."""
    g = (order or {}).get("_saved_floor")
    return g.get("arm") if isinstance(g, dict) else None


def floored(row):
    """Whether an orders.jsonl row is an arm B talk: the shop learning both arms read leaves it out."""
    g = row.get("saved_floor") if isinstance(row, dict) else None
    return isinstance(g, dict) and g.get("arm") == "B"


def priced(trades):
    """The trades whose prices both arms may learn from: without arm B's (every trade carries its arm from the start)."""
    return [t for t in trades if t.get("saved_floor") != "B"]
