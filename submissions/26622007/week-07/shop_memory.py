"""The shop's memory of its own negotiations and reviews, and the opening price it allows from them.

Memory file: {"negotiations": [{"time", "task", "item", "reserve", "open", "asks", "offers",
                                "result", "price"}]}
- In the market (SHOP_MARKET_NAME set, e.g. nocha12) it is memory/seller_memory.json, and the
  shop's reviews are read from the market's /api/shops every REVIEWS_TTL seconds.
- In the market test (no SHOP_MARKET_NAME) it is memory/seller_test_memory.json, without reviews.

Opening price: the harness proposes an opening ask; the code caps it at reserve * (1 + markup).
The markup comes from pricing.json tiers by review count and average stars, and steps down while
the recent conversion (deals / finished negotiations) is low. The reserve never moves.
"""
import json
import os
import threading
import time

import httpx

MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")
NAME = os.environ.get("SHOP_MARKET_NAME")
MEMORY = os.environ.get("SHOP_MEMORY") or ("memory/seller_memory.json" if NAME else "memory/seller_test_memory.json")
PRICING = os.environ.get("SHOP_PRICING", "pricing.json")
REVIEWS_TTL = 120
RECENT = 10

DEFAULT_PRICING = {
    "tiers": [{"min_reviews": 0, "min_stars": 0, "markup": 0.10},
              {"min_reviews": 40, "min_stars": 3.8, "markup": 0.20},
              {"min_reviews": 80, "min_stars": 4.0, "markup": 0.30}],
    "low_conversion": 0.6, "step_down": 0.05, "min_markup": 0.05,
}

_lock = threading.Lock()
_reviews = {"at": 0.0, "count": None, "stars": None}


def load_pricing():
    """Read every time, so the tiers can change without restarting the shop."""
    try:
        with open(PRICING, encoding="utf-8") as f:
            p = json.load(f)
        return {**DEFAULT_PRICING, **p}
    except (OSError, ValueError):
        return dict(DEFAULT_PRICING)


def load():
    try:
        with open(MEMORY, encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m.get("negotiations"), list) else {"negotiations": []}
    except (OSError, ValueError):
        return {"negotiations": []}


def record(entry):
    with _lock:
        m = load()
        m["negotiations"].append({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry})
        os.makedirs(os.path.dirname(MEMORY) or ".", exist_ok=True)
        tmp = MEMORY + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=1)
        os.replace(tmp, MEMORY)


def reviews():
    """(count, average stars) of this shop's reviews in the market, cached; (None, None) if unknown."""
    if not NAME:
        return None, None
    if time.time() - _reviews["at"] > REVIEWS_TTL:
        _reviews["at"] = time.time()   # also on failure, so a slow market is not asked on every turn
        try:
            shops = httpx.get(f"{MARKET}/api/shops", timeout=5).json()["shops"]
            me = next((s for s in shops if s.get("name") == NAME), None)
            if me is not None:
                stars = [r["stars"] for r in me.get("reviews") or [] if isinstance(r.get("stars"), (int, float))]
                board = httpx.get(f"{MARKET}/api/board", timeout=5).json()
                row = next((s for s in board.get("shops", []) if s.get("name") == NAME), {})
                _reviews["count"] = row.get("reviews", len(stars))
                _reviews["stars"] = row.get("stars") or (sum(stars) / len(stars) if stars else None)
        except (httpx.HTTPError, ValueError, KeyError):
            pass
    return _reviews["count"], _reviews["stars"]


def recent(item=None):
    done = [n for n in load()["negotiations"] if n.get("result") in ("deal", "rejected", "canceled")]
    last = done[-RECENT:]
    deals = [n for n in last if n["result"] == "deal"]
    same = [n["price"] for n in done if n["result"] == "deal" and n.get("item") == item][-5:] if item else []
    return {"finished": len(last), "deals": len(deals),
            "conversion": (len(deals) / len(last)) if last else None, "item_prices": same}


def markup():
    """(markup, why) for the opening cap."""
    p = load_pricing()
    count, stars = reviews()
    tier = p["tiers"][0]
    for t in p["tiers"]:
        if (count or 0) >= t.get("min_reviews", 0) and (stars or 0) >= t.get("min_stars", 0):
            tier = t
    m = tier["markup"]
    why = f"tier {tier.get('min_reviews', 0)}+ reviews ({count} reviews, {stars if stars is None else round(stars, 2)} stars)"
    r = recent()
    if r["conversion"] is not None and r["finished"] >= 5 and r["conversion"] < p["low_conversion"]:
        m = max(p["min_markup"], m - p["step_down"])
        why += f", conversion {r['deals']}/{r['finished']} low -> step down"
    return m, why


def note(item):
    """One line for the harness prompt: what this shop's own recent trades say."""
    r = recent(item)
    parts = []
    if r["item_prices"]:
        parts.append(f"your last {item} deals closed at {', '.join(str(round(p)) for p in r['item_prices'])}")
    if r["conversion"] is not None:
        parts.append(f"{r['deals']} of your last {r['finished']} negotiations ended in a deal")
    return "; ".join(parts)
