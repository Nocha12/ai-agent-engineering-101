"""Goods with a warranty shorter than 6 h, bought only under an expected-value cap (owner 11/08 17:55, telegram 7955
"일단 너가 찾은 수식으로 해"; review/W-20261108-1755-AX짧은보증수식/report.md).

The market scores a goods deal 0 when the unit breaks under the warranty and -price/list when it breaks outside it.
Breaks come within 0-6 h of the sale, evenly (about 10,000 market breaks, 11 after 6 h), so with a warranty of h hours a
unit that breaks (chance b) breaks outside it with q = b * max(0, 6 - h) / 6. A deal at p = price/list keeps in
expectation E = (1 - b)(1 - p) - q p, and it is bought only when E >= S, my current Saved (the board's, else 0.30):

    p_max(h) = (1 - b - S) / ((1 - b) + q)

At h = 6 that is 1 - S / (1 - b), the cap a 6 h goods deal already has (market_buyer.saved_cap). E.g. b = .072,
S = .2955: 6 h 68.2%, 3 h 65.6%, 0 h 63.3%.

A safety rail on top: the expected out-of-warranty loss of the short-warranty deals paid in the last hour, sum q * price,
stays under EXPOSURE_SHARE of the cash buffer (the switch file's cash_buffer, else hard_limits.STOP_BUYING_BELOW, the
buffer's floor). The accept band (plan A, 105%) does not apply to these deals: p_max(h) is the ceiling.

Switch: .market/buyer_short_warranty.json {"enabled": true, "cash_buffer": <won>}; missing, unreadable or anything
else is off, and off is the old behaviour exactly (6 h or no deal). BUYER_SHORT_WARRANTY=0 in the environment is off
too. Read at every order, so no restart either way.
"""
import json
import os
import time

import hard_limits

SWITCH = "buyer_short_warranty.json"
BREAK_HOURS = 6                 # a unit breaks within this many hours of the sale (hard_limits.BUYER_GOODS_WARRANTY_HOURS)
FALLBACK_SAVED = 0.30           # S when the board cannot be read (market_buyer.SAVED_MIN_SHARE)
FALLBACK_BREAK = 0.0837         # b when the ledger gives none: p90 of 2,089 goods SKUs (median .0718)
BOARD_MAX_AGE = 3 * 3600        # a board snapshot older than this is not my Saved now
BOARD_TAIL = 200000             # bytes of board_history.jsonl read for the last snapshot
EXPOSURE_SHARE = 0.02           # sum q * price of the last hour's short-warranty deals <= this share of the cash buffer
EXPOSURE_SECONDS = 3600
ORDERS_TAIL = 4000000           # bytes of measure/orders.jsonl read for that hour
ME = ("nocha12",)               # my buyer's name on the board, compared lowercased


def config(md, env=None):
    """The switch file as a dict when it is on, else None."""
    env = os.environ if env is None else env
    if str(env.get("BUYER_SHORT_WARRANTY") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        with open(os.path.join(md, SWITCH), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError, TypeError):
        return None
    return d if isinstance(d, dict) and d.get("enabled") is True else None


def number(v):
    return hard_limits.number(v)


def outside_chance(b, h):
    """q: the chance a unit breaks after a warranty of h hours."""
    h = h if number(h) and h > 0 else 0
    return b * max(0.0, BREAK_HOURS - h) / BREAK_HOURS


def p_max(h, b, S):
    """The highest price/list at which a deal with an h-hour warranty keeps S in expectation; 0 when none does."""
    b = min(max(b if number(b) else FALLBACK_BREAK, 0.0), 1.0)
    q = outside_chance(b, h)
    den = (1 - b) + q
    if den <= 0 or 1 - b - S <= 0:
        return 0.0
    return max(0.0, (1 - b - S) / den)


def expected(p, h, b):
    """E[x] = (1 - b)(1 - p) - q p: the share of list a deal at p keeps in expectation."""
    return (1 - b) * (1 - p) - outside_chance(b, h) * p


def cap(list_price, h, b, S):
    """The most I pay for a unit of list_price with an h-hour warranty (whole won, rounded down)."""
    if not number(list_price) or list_price <= 0:
        return 0
    return int(list_price * p_max(h, b, S))


def board_saved(md, now=None):
    """My Saved on the newest board snapshot (.market/board_history.jsonl, buyers: name -> [saved, forum, all]), or
    FALLBACK_SAVED when it is missing, older than BOARD_MAX_AGE or unreadable."""
    now = now or time.time()
    try:
        path = os.path.join(md, "board_history.jsonl")
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - BOARD_TAIL))
            lines = f.read().decode("utf-8", "replace").splitlines()
        for line in reversed(lines):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not number(d.get("ts")) or now - d["ts"] > BOARD_MAX_AGE:
                return FALLBACK_SAVED
            for name, row in (d.get("buyers") or {}).items():
                if str(name).lower() in ME and isinstance(row, list) and row and number(row[0]) and 0 < row[0] < 1:
                    return float(row[0])
            return FALLBACK_SAVED
    except OSError:
        pass
    return FALLBACK_SAVED


def state(md, order, now=None, env=None):
    """For a goods order with the switch on: {"S", "buffer", "hist": {shop: hours}}, kept on the order; else None."""
    if (order or {}).get("kind") != "goods":
        return None
    cfg = config(md, env)
    if cfg is None:
        return None
    buf = cfg.get("cash_buffer")
    return {"S": board_saved(md, now), "buffer": buf if number(buf) and buf > 0 else hard_limits.STOP_BUYING_BELOW,
            "hist": {}}


def hours_for(sw, shop, quote=None):
    """The warranty hours I expect from shop on this order, when under BREAK_HOURS: the least of what its quote said and
    what its recent receipts showed (market_buyer.short_warranty_shops, taken as 0 h). None: a 6 h one, as usual."""
    if not sw:
        return None
    hs = [h for h in ((quote or {}).get("warranty_h"), (sw.get("hist") or {}).get(shop)) if number(h)]
    h = min(hs) if hs else None
    return h if h is not None and h < BREAK_HOURS else None


def exposure(md, now=None):
    """sum q * price of the short-warranty deals passed to the kit in the last EXPOSURE_SECONDS (measure/orders.jsonl)."""
    now = now or time.time()
    total = 0.0
    try:
        with open(os.path.join(md, "measure", "orders.jsonl"), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - ORDERS_TAIL))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return 0.0
    for line in lines:
        if '"short_warranty_buy"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        s = d.get("short_warranty_buy")
        if not isinstance(s, dict) or not d.get("passed_to_kit") or not number(d.get("ts")) or now - d["ts"] > EXPOSURE_SECONDS:
            continue
        if number(s.get("loss")):
            total += s["loss"]
    return total


def receipt_check(sw, md, rec, list_price, b, now=None):
    """(ok, record, why) for a goods receipt whose warranty is short: ok when p <= p_max(h) and the hour's exposure
    leaves room for it. record goes on the order line (short_warranty_buy) either way."""
    h = rec.get("warranty_hours") if number(rec.get("warranty_hours")) and rec.get("warranty_hours") > 0 else 0
    price = rec.get("price")
    b = b if number(b) and 0 <= b < 1 else FALLBACK_BREAK
    S = sw["S"]
    pm = p_max(h, b, S)
    if not number(price) or not number(list_price) or list_price <= 0:
        return False, None, "no price or list price to check a short warranty against"
    p = price / list_price
    loss = outside_chance(b, h) * price
    rec_ = {"h": h, "b": round(b, 4), "S": round(S, 4), "p_max": round(pm, 4), "p": round(p, 4),
            "loss": round(loss, 1)}
    if p > pm:
        return False, dict(rec_, ok=False), f"warranty {h:g} h: price {p:.1%} of list is over p_max {pm:.1%}"
    room = EXPOSURE_SHARE * sw["buffer"]
    used = exposure(md, now)
    rec_["exposure"] = round(used, 1)
    if used + loss > room:
        return False, dict(rec_, ok=False), f"warranty {h:g} h: the hour's expected loss {used + loss:.0f} would pass {room:.0f}"
    return True, dict(rec_, ok=True), None
