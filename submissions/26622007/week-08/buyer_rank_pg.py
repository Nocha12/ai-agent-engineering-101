"""Quotes ranked by the chance of a deal times the share of list it keeps (÷ seconds only with "per_time": true; owner 11/08, telegram 7959-7963,
7977; review/W-20261108-1655-AX가게별바이어/shop-profiles.md 5-6: replayed on 11/07 19:09 - 11/08 19:09, P x price took
+3% deals and +11-14% buyer ALL/h over the current order, ÷T within 1% of it).

Of the quotes within my limit (fits first, as before; the limits and guards are not touched here), best_quote keeps the
one with the most

    P(shop, kind) x g / T,   g = (1 - b)(1 - E[final] / list),   E[final] = ask x E[final / first ask](shop, kind)

(g = (1 - b)(1 - p) - q at a short warranty, buyer_short_warranty.expected), T = the quote seconds so far + the shop's
expected talk seconds + NEXT_ORDER_SECONDS. The share is of the final price against the list price (7961), never the cut
from the ask. P, E[final / first ask] and the talk seconds come from the last HOURS of measure/orders.jsonl, per shop and
kind, shrunk to the kind's pool: (k + SHRINK p) / (n + SHRINK). No talk seconds known: no ÷T.

The ÷T is only with "per_time": true in the switch; without it the key is P x g (main 11/08 19:22: the final replay,
shop-profiles.md 6, gave P x price +12% ALL/h and ÷T +9%).

No ask within my limit (no_fit): the order as before, or with "nofit": true in the switch (or "ab": the B half of the orders), of the asks within the talk band
(market_buyer.TALK_OVER_SHARE over the limit) the most P_nofit(shop, kind) x g(limit / list), where P_nofit is the deal
rate of the orders kept with that shop when no quote fitted, over the last NOFIT_HOURS (11/08 24h: 1,794 goods orders, mjforge kept 411 for 2 paid,
ddolcom 221 for 114, the cheapest ask decided; review/W-20261108-2241-AXPro주기, 대표님 8033 "잘 받아주는 곳으로").

Switch: .market/buyer_rank_pg.json {"enabled": true}; missing, unreadable or anything else is off, and off is the
current order exactly. BUYER_RANK_PG=0 in the environment is off too. Read at every order, so no restart either way.
"""
import fcntl
import hashlib
import json
import os
import time

SWITCH = "buyer_rank_pg.json"
CACHE = "buyer_rank_pg_cache.json"
HOURS = 24
NOFIT_HOURS = 3     # no-fit deal rates turned at 19:32 (short warranty on): ddolcom 112/198 before, 1/22 after
NOFIT_BAND = 0.20   # = market_buyer.TALK_OVER_SHARE: a kept ask over it ends with no talk, so it says nothing of the shop
SHRINK = 20.0
NEXT_ORDER_SECONDS = 20.0
REFRESH = 1800
TAIL_BYTES = 30_000_000
DONE = "TASK_STATE_COMPLETED"


def _num(x):
    return type(x) in (int, float) and x == x


def _switch(md, env=None):
    """The switch's dict when it is on, else None."""
    env = os.environ if env is None else env
    if str(env.get("BUYER_RANK_PG") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        with open(os.path.join(md, SWITCH), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError, TypeError):
        return None
    return d if isinstance(d, dict) and d.get("enabled") is True else None


def enabled(md, env=None):
    return _switch(md, env) is not None


def _first_ask(r):
    for q in r.get("quotes") or ():
        if isinstance(q, dict) and q.get("shop") == r.get("shop") and _num(q.get("ask")) and q["ask"] > 0:
            return q["ask"]
    asks = r.get("asks")
    return asks[0] if isinstance(asks, list) and asks and _num(asks[0]) and asks[0] > 0 else None


def stats_from_rows(rows, paid, now):
    """{"pool": {kind: {p, ratio, talk}}, "shops": {"shop|kind": {n, k, p, ratio, talk}}} from order lines: the orders
    with the shop kept (state set) in the last HOURS; NO_VALUE (kept, then no talk) is a failure of that shop (11/08
    19:43-20:03: ddolcom 0/12 by NO_VALUE kept its P .41 while they were left out). A deal: completed, a price, passed to the kit and not
    known unpaid. ratio: mean final price / first ask of the deals; talk: mean seconds end - quotes."""
    cell, pool, ncell, npool = {}, {}, {}, {}
    for r in rows:
        ts, shop, kind = r.get("ts"), r.get("shop"), r.get("kind")
        if not (_num(ts) and now - HOURS * 3600 <= ts <= now and isinstance(shop, str) and kind in ("goods", "food")):
            continue
        if r.get("state") is None:
            continue
        first = _first_ask(r)
        ok = (r.get("state") == DONE and _num(r.get("price")) and r.get("passed_to_kit") is True
              and paid.get(r.get("order")) is not False)
        s = r.get("seconds") or {}
        talk = s.get("end") - s.get("quotes") if _num(s.get("end")) and _num(s.get("quotes")) else None
        qs = [q for q in r.get("quotes") or () if isinstance(q, dict)]
        kept = next((q for q in qs if q.get("shop") == shop and _num(q.get("ask")) and _num(q.get("limit"))), None)
        # no ask within my limit, and the kept ask within the talk band (an ask over it never reaches a talk: 11/08 24h
        # 0 of 335 paid; independent review 23:0x)
        if qs and not any(q.get("fit") is True for q in qs) and ts >= now - NOFIT_HOURS * 3600 and kept is not None \
                and 0 < kept["limit"] and kept["ask"] <= kept["limit"] * (1 + NOFIT_BAND):
            for c in (ncell.setdefault((shop, kind), [0, 0]), npool.setdefault(kind, [0, 0])):
                c[0] += 1
                c[1] += ok
        for c in (cell.setdefault((shop, kind), [0, 0, 0.0, 0, 0.0, 0]), pool.setdefault(kind, [0, 0, 0.0, 0, 0.0, 0])):
            c[0] += 1
            c[1] += ok
            if ok and first:
                c[2] += r["price"] / first
                c[3] += 1
            if talk is not None and talk >= 0:
                c[4] += talk
                c[5] += 1
    pools = {k: {"p": c[1] / c[0], "ratio": c[2] / c[3] if c[3] else 1.0, "talk": c[4] / c[5] if c[5] else None}
             for k, c in pool.items()}
    shops = {}
    for (shop, kind), c in cell.items():
        pl = pools[kind]
        shops[f"{shop}|{kind}"] = {
            "n": c[0], "k": c[1],
            "p": (c[1] + SHRINK * pl["p"]) / (c[0] + SHRINK),
            "ratio": (c[2] + SHRINK * pl["ratio"]) / (c[3] + SHRINK),
            "talk": (c[4] + SHRINK * pl["talk"]) / (c[5] + SHRINK) if pl["talk"] is not None else None}
    npools = {k: {"p": c[1] / c[0]} for k, c in npool.items()}
    nshops = {f"{shop}|{kind}": {"n": c[0], "k": c[1], "p": (c[1] + SHRINK * npools[kind]["p"]) / (c[0] + SHRINK)}
              for (shop, kind), c in ncell.items()}
    return {"pool": pools, "shops": shops, "nofit": {"pool": npools, "shops": nshops}}


def _tail(path, nbytes=TAIL_BYTES):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - nbytes))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return
    for x in lines[1:] if size > nbytes else lines:
        try:
            r = json.loads(x)
        except ValueError:
            continue
        if isinstance(r, dict):
            yield r


def compute(md, now):
    paid = {p["order"]: p["paid"] for p in _tail(os.path.join(md, "payments.jsonl"), 4_000_000)
            if isinstance(p.get("order"), str) and isinstance(p.get("paid"), bool)}
    return stats_from_rows(_tail(os.path.join(md, "measure", "orders.jsonl")), paid, now)


def _read_cache(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None, None
    if not isinstance(d, dict) or not _num(d.get("at")) or not isinstance(d.get("stats"), dict):
        return None, None
    return d["at"], d["stats"]


def stats(md, now=None):
    """The cached stats, computed again from the log when older than REFRESH (one buyer run at a time; the others go on
    with the cached ones, or None)."""
    now = time.time() if now is None else now
    path = os.path.join(md, CACHE)
    fresh = lambda at: at is not None and 0 <= now - at < REFRESH
    at, st = _read_cache(path)
    if fresh(at):
        return st
    try:
        with open(path + ".lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return st
            at, st = _read_cache(path)
            if fresh(at):
                return st
            try:
                st = compute(md, now)
            except Exception:   # noqa: BLE001 - a broken log is no ranking until the next REFRESH
                st = {"pool": {}, "shops": {}}
            tmp = f"{path}.{os.getpid()}.tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"at": now, "stats": st}, f, sort_keys=True)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
    except OSError:
        pass
    return st


def state(md, order, env=None, now=None):
    """{"stats": ...} kept on the order when the switch is on and stats can be read; else None (the current order)."""
    try:
        sw = _switch(md, env)
        if sw is None:
            return None
        st = stats(md, now)
    except Exception:   # noqa: BLE001 - the ranking never stops an order
        return None
    if not isinstance(st, dict):
        return None
    arm = nofit_arm(sw, order)
    return {"stats": st, "per_time": sw.get("per_time") is True, "nofit": sw.get("nofit") is True or arm == "B",
            **({"nofit_arm": arm} if arm else {})}


def nofit_arm(sw, order):
    """"nofit": "ab" in the switch: half the orders (B, by the order id) get the no-fit pick, the other half (A) the order
    before, at the same hours (GPT Pro 11/08 23:0x §2.9: a field test in the one buyer kit, 9-12 h, same-time arms; its
    replay gave +0.02 to +0.57 paid/h, ours +2.1 to +2.7 halved). Anything else: None."""
    oid = (order or {}).get("order")
    if sw.get("nofit") != "ab" or not isinstance(oid, str) or not oid:
        return None
    return "B" if int(hashlib.sha256(oid.encode("utf-8", "surrogatepass")).hexdigest(), 16) % 2 else "A"


def cell(rk, shop, kind):
    """(P, E[final / first ask], talk seconds or None) for shop and kind: its shrunk cell, else the kind's pool, else
    (None, 1.0, None)."""
    st = (rk or {}).get("stats") or {}
    c = (st.get("shops") or {}).get(f"{shop}|{kind}") or (st.get("pool") or {}).get(kind) or {}
    p, ratio, talk = c.get("p"), c.get("ratio"), c.get("talk")
    return (p if _num(p) and 0 <= p <= 1 else None,
            ratio if _num(ratio) and ratio > 0 else 1.0,
            talk if _num(talk) and talk >= 0 else None)


def nofit_p(rk, shop, kind):
    """P(deal | no ask within my limit) for shop and kind: its shrunk cell, else the kind's no-fit pool, else None."""
    nf = ((rk or {}).get("stats") or {}).get("nofit") or {}
    c = (nf.get("shops") or {}).get(f"{shop}|{kind}") or (nf.get("pool") or {}).get(kind) or {}
    p = c.get("p")
    return p if _num(p) and 0 <= p <= 1 else None


def score(rk, shop, kind, ask, list_price, g_of, quote_seconds, penalty=0.0):
    """P x (g - penalty) / T for one quote, or None when it cannot be scored (no P, no price). g_of(p): the share of list
    a deal at p = price / list keeps in expectation (the caller's, with the break chance and any short warranty)."""
    if not (_num(ask) and _num(list_price) and list_price > 0):
        return None
    P, ratio, talk = cell(rk, shop, kind)
    if P is None:
        return None
    g = g_of(ask * ratio / list_price) - penalty
    if g <= 0:          # a deal that keeps nothing: ranked by g alone (P and T would flip it), under any that keeps some
        return g
    if talk is None or (rk or {}).get("per_time") is not True:
        return P * g
    return P * g / (max(0.0, quote_seconds or 0.0) + talk + NEXT_ORDER_SECONDS)
