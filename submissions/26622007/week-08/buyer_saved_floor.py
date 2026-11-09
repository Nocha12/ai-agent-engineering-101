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

Switch: .market/buyer_saved_floor.json {"enabled": true, "margin": 0.0, "band": "cap"|"keep",
"ab": {"salt": "...", "share": 0.5}, "kinds": ["food", "goods"]}; missing, unreadable or anything else is off, and off is the old behaviour
exactly. With "ab", only the orders whose sha1(salt|order) falls under share are arm B (the floor); arm A is the
old behaviour and is still returned so the judgement can count both. BUYER_SAVED_FLOOR=0 in the environment is off
too. "kinds" (a list) limits it to those order kinds, so it can start on food when the food study ends and on goods
when the goods studies end; an order of another or unknown kind is off. Read at every order (a buyer run is one
order), so no restart either way.
"""
import hashlib
import json
import os
import time

import buyer_short_warranty

SWITCH = "buyer_saved_floor.json"
BASE_SHARE = 0.30          # market_buyer.SAVED_MIN_SHARE
MAX_SHARE = 0.40           # a board S above this is not trusted as a floor
MAX_MARGIN = 0.05


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def config(md, env=None):
    env = os.environ if env is None else env
    if str(env.get("BUYER_SAVED_FLOOR") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        with open(os.path.join(md, SWITCH), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError, TypeError):
        return None
    return d if isinstance(d, dict) and d.get("enabled") is True else None


def board_saved(md, now=None):
    """My Saved on the newest board snapshot, or None when it is missing, stale or unreadable."""
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
                or not -300 <= now - d["ts"] <= buyer_short_warranty.BOARD_MAX_AGE:
            return None
        for name, row in (d.get("buyers") or {}).items():
            if str(name).lower() in buyer_short_warranty.ME and isinstance(row, list) and row \
                    and _number(row[0]) and 0 < row[0] < 1:
                return float(row[0])
        return None
    return None


def arm(salt, order_id, share):
    h = int(hashlib.sha1(f"{salt}|{order_id}".encode()).hexdigest()[:8], 16) / 0x100000000
    return "B" if h < share else "A"


def state(md, order_id, now=None, env=None, base=BASE_SHARE, kind=None):
    """None when off; else {"arm", "share", "band_off", "board_saved"}: arm A keeps base and the band."""
    c = config(md, env)
    if c is None:
        return None
    if "kinds" in c and (not isinstance(c["kinds"], list) or kind not in c["kinds"]):
        return None
    S = board_saved(md, now)
    if S is None:
        return None
    ab = c.get("ab")
    if ab is not None:
        if not isinstance(ab, dict) or not isinstance(ab.get("salt"), str) or not ab["salt"] \
                or not _number(ab.get("share")) or not 0 < ab["share"] <= 1:
            return None
        a = arm(ab["salt"], order_id, ab["share"])
    else:
        a = "B"
    if a == "A":
        return {"arm": "A", "share": base, "band_off": False, "board_saved": S}
    m = c.get("margin", 0)
    m = m if _number(m) and 0 <= m <= MAX_MARGIN else 0
    return {"arm": "B", "share": min(MAX_SHARE, max(base, S + m)), "band_off": c.get("band", "cap") != "keep",
            "board_saved": S}
