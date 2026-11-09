"""Default-OFF order-level A/B: arm B ranks kimauve's "... list price L. Send the ask or say ok to buy. Our ask: A" quote by A.

The quote_alignment study (11/07, buyer_quote_alignment) reads the same text but runs 30 minutes and marks both arms
"probing", which takes its orders out of the goods-ladder, first95 and cap95 trials; it stays as it is (off). This study:
- switch .market/buyer_our_ask.json {"enabled": true, "salt": "...", "start": epoch, "end": epoch}; anything else, or a
  window that is not finite, not started, over or longer than MAX_HOURS, is off;
- arm = sha256(salt:order)[0] % 2 (A legacy, B reads); every order started in the window is assigned (ITT);
- an arm B read ranks the quote and may end the order over TALK_OVER_SHARE; the talk's first turn, the harness note and
  every other arm stay as before (market_buyer ask_src "text_rank");
- the orders row keeps such a quote's ask out of the shared fields (ask None, fit False as legacy read it) and records it
  under "our_ask", so recent_asks, recent_fits, the food shares and buyer_rank_pg read the same rows in both arms.
W-20261109-2225-AXPro주기 (instruction 4, 11/10 01:47): 24 h of kit log had 1,313 such quotes, all read none; 261 within
my limit after the SKU and list-price check, 56 of them in orders that passed nothing to the kit (GPT Pro 01:39).
"""
import hashlib
import json
import math
import os
import re
import time

SWITCH = "buyer_our_ask.json"
MAX_HOURS = 13


def _switch(md):
    try:
        with open(os.path.join(md, SWITCH), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError, TypeError, RecursionError):
        return None
    if not isinstance(d, dict) or d.get("enabled") is not True:
        return None
    salt, start, end = d.get("salt"), d.get("start"), d.get("end")
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) < 1e12 and math.isfinite(v)  # 10**400: no OverflowError
    if not isinstance(salt, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", salt) or not num(start) or not num(end) \
            or not 0 < start < end <= start + MAX_HOURS * 3600:
        return None
    return {"salt": salt, "start": start, "end": end}


def assign(md, order, now=None):
    """{"arm", "salt", "start", "end", "applied": []} for an order started inside the window, else None."""
    s = _switch(md)
    now = time.time() if now is None else now
    oid = (order or {}).get("order")
    if s is None or not isinstance(oid, str) or not oid or not s["start"] <= now < s["end"]:
        return None
    arm = "AB"[hashlib.sha256((s["salt"] + ":" + oid).encode()).digest()[0] % 2]
    return dict(s, arm=arm, applied=[])


def reads(ctx, now=None):
    """Whether this order reads "Our ask" now: arm B, still inside its window."""
    now = time.time() if now is None else now
    return isinstance(ctx, dict) and ctx.get("arm") == "B" and ctx["start"] <= now < ctx["end"]


def measurement(ctx):
    return None if not isinstance(ctx, dict) else {"salt": ctx["salt"], "arm": ctx["arm"], "applied": list(ctx["applied"])}
