"""A goods quote that names a 6 h or longer warranty is priced as one, not at the shop's 0 h history (B-W1,
W-20261109-1925-AXPro주기; review/W-20261109-1755-AXPro주기/buyer-candidates.md).

With buyer_short_warranty on, hours_for takes the least of what the quote said and the shop's history
(market_buyer.short_warranty_shops: the last goods receipt short is enough, taken as 0 h). warranty_hours_said reads a
quote that names 72 h as None ("leaves 6 h open"), so the history alone is left and a quote saying "grade A, market
certificate attached, 72 h warranty" was priced at p_max(0 h): 434 quotes in 426 orders in the 24 h to 11/09 19:30
asked at or under the 6 h cap (105% band) and were left over the 0 h one, 301 of those orders bought nothing
(ana/bw1_counterfactual.py). The same shops' goods sales in the ledger those 24 h: thisjonghoon 72 h 234, 6 h 127, none
55; yms979 72 h 2,967, none 222; to me only 72 h and 6 h.

Arm B: a quote whose text names only warranties of 6 h or more, and no "no warranty"/0 h/shorter one, is priced as a
6 h deal (hours_for None) whatever the history. The receipt check is not touched: a receipt under 6 h still goes to
market_buyer.warranty_short and buyer_short_warranty.receipt_check at its own hours, so a shop that says 72 h and ships
0 h is paid only under p_max(0 h), as now. Arm A, off and every other quote: as before.

Switch: .market/buyer_warranty_said.json {"enabled": true, "study": "...", "salt": "...", "share": 0.5,
"start_at": <epoch>, "stop_at": <epoch>}; on only in [start_at, stop_at), a window of at most MAX_WINDOW_HOURS; missing,
unreadable, a bad field or outside the window is off, and off is the old behaviour exactly. BUYER_WARRANTY_SAID=0 in the
environment is off too. Read once per order (a buyer run is one order), so no restart either way. Arms by
sha1(salt|order) < share, a hash axis of its own (not saved-floor's or goods-grade's salt).

On, it moves what other goods studies see: buyer_goods_grade.adjust runs only when hours_for is None, and the chosen
shop changes. Start it after the goods studies end. Every order with the switch on carries "warranty_said" in its
measure record (both arms, with the shops whose quote it would change), so the judgement counts both arms over the
same eligible orders. A deal in arm B also writes "deal" as the shop's last goods result in the memory both arms read,
which can clear the 0 h history for arm A too: a spill toward A, so the A/B difference is a lower bound.
"""
import hashlib
import json
import math
import os
import time
from pathlib import Path

import ask_read

SWITCH = "buyer_warranty_said.json"
MAX_WINDOW_HOURS = 12
VERSION = "bw1-1"


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _window(start, stop):
    return _num(start) and _num(stop) and 0 < start < stop <= start + MAX_WINDOW_HOURS * 3600


def _good(d):
    return (isinstance(d, dict) and d.get("enabled") is True
            and isinstance(d.get("study"), str) and d["study"] and isinstance(d.get("salt"), str) and d["salt"]
            and _num(d.get("share")) and 0 < d["share"] <= 1 and _window(d.get("start_at"), d.get("stop_at")))


def config(md, env=None, now=None):
    """{"study", "salt", "share", "start_at", "stop_at"} when the switch is on at now, else None."""
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    if str(env.get("BUYER_WARRANTY_SAID") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        d = json.loads((Path(md) / SWITCH).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not _good(d) or not d["start_at"] <= now < d["stop_at"]:
        return None
    return {k: d[k] for k in ("study", "salt", "share", "start_at", "stop_at")}


def arm(salt, order_id, share):
    h = int(hashlib.sha1(f"{salt}|{order_id}".encode()).hexdigest()[:8], 16) / 0x100000000
    return "B" if h < share else "A"


def state(md, order_id, env=None, now=None):
    """None when off; else {"study", "v", "arm", "start_at", "stop_at"}. Never raises into the buyer run."""
    try:
        c = config(md, env, now)
        if c is None or not order_id:
            return None
        return {"study": c["study"], "v": VERSION, "arm": arm(c["salt"], order_id, c["share"]),
                "start_at": c["start_at"], "stop_at": c["stop_at"]}
    except Exception:  # noqa: BLE001 - the switch never ends the run: off
        return None


def stated_ok(text):
    """True when the goods text names a warranty and every one it names is 6 h or more, with no "no warranty", 0 h or
    "as is" anywhere (ask_read's readings): "grade A, market certificate attached, 72 h warranty"."""
    t = ask_read.SAFE_RE.sub(ask_read._keep_stop, text or "")
    if ask_read.NO_WARRANTY.search(t) or ask_read.short_warranty_hours(text) is not None:
        return False
    hours = []
    for m in ask_read.WARRANTY_HOURS.finditer(t):
        n, u = (m["n"], m["u"]) if m["n"] else (m["n2"], m["u2"])
        v = ask_read.WORD_N[n.lower()] if n.lower() in ask_read.WORD_N else float(n)
        hours.append(v * 24 if u.lower().startswith("day") else v)
    return bool(hours) and min(hours) >= ask_read.GOODS_WARRANTY_HOURS


def applies(sw, quote):
    """Whether this quote is priced as a 6 h deal by arm B (buyer_short_warranty.hours_for)."""
    said = (sw or {}).get("said")
    q = quote or {}
    return isinstance(said, dict) and said.get("arm") == "B" and q.get("warranty_said_ok") is True \
        and q.get("warranty_h") is None


def record(sw, quotes):
    """The measure record for an order with the switch on: the arm and the shops whose 0 h history a quote naming 6 h
    or more would set aside (both arms), else None."""
    said = (sw or {}).get("said")
    if not isinstance(said, dict):
        return None
    hist = (sw or {}).get("hist") or {}
    eligible = sorted(name for name, q in (quotes or {}).items()
                      if isinstance(q, dict) and q.get("warranty_said_ok") is True and q.get("warranty_h") is None
                      and hist.get(name) is not None)
    return {**said, "eligible": eligible}


def switch_on(md, *, study, salt, start_at, hours, share=0.5):
    """Write the switch for [start_at, start_at + hours) atomically (0600); ValueError leaves the old file as it was."""
    d = {"enabled": True, "study": study, "salt": salt, "share": share, "start_at": start_at,
         "stop_at": start_at + hours * 3600 if _num(hours) and _num(start_at) else None}
    if not _good(d):
        raise ValueError("buyer_warranty_said: bad switch")
    path = Path(md) / SWITCH
    tmp = path.with_name(f"{SWITCH}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(d, f, sort_keys=True, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return d
