"""Buyers this shop does not sell to (pricing.json seller.refuse_buyers), checked when a buyer completes an ACP checkout.

The A2A talk carries no buyer name; the first place the shop learns who pays is payment_data.order at
/checkout_sessions/{id}/complete. The market's public ledger has one "order" row per order naming its buyer, written
when the order is issued (before the buyer talks to any shop), so the order is looked up there. A refused buyer gets
the answer a sold-out unit gets (409, the session canceled, the unit back on sale) and nothing is sent to /api/settle.
Every other buyer, and any order the ledger lookup cannot place, goes on to settle as before.

The refused buyer, the time and the order go to .market/measure/refused.jsonl (record only, never read by a decision).
An empty list (or no "seller" key) turns this off without a restart: pricing.json is read on every checkout.

Used by the kit's AcpShop (scripts/market_kit.py loads this file from the shop folder) and by acp_shop.AcpCheckout.
"""
import importlib.util
import json
import os
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")
OUT_OF_STOCK = "Sorry, this item is currently out of stock. Thank you for your interest."
PAGE = 500              # rows /api/ledger returns per call
BACK = 4000             # rows to read back on a cold start or after a long gap: ~15 min at ~4 rows/s; orders last 10 min
MAX_ORDERS = 20000
EVERY = 10              # seconds between background ledger reads
WAIT = 5                # seconds a payment waits for a ledger read already running


def refused_buyers(path=None):
    """pricing.json seller.refuse_buyers, read now. [] when absent or unreadable."""
    try:
        with open(path or os.path.join(HERE, os.environ.get("SHOP_PRICING", "pricing.json")), encoding="utf-8") as f:
            names = ((json.load(f).get("seller") or {}).get("refuse_buyers")) or []
    except (OSError, ValueError, AttributeError):
        return []
    return [n for n in names if isinstance(n, str) and n] if isinstance(names, list) else []


def _get(path):
    with urllib.request.urlopen(MARKET.rstrip("/") + path, timeout=10) as r:
        return json.loads(r.read())


class OrderBuyers:
    """order id -> buyer from the ledger's "order" rows, read forward from the last row seen."""

    def __init__(self, get=_get):
        self.get = get
        self.seq = None
        self.buyers = {}
        self.lock = threading.Lock()

    def _read(self):
        hi = self.get("/api/ledger?after=-1")[-1]["seq"]
        if self.seq is None or hi - self.seq > BACK:
            self.seq = max(0, hi - BACK)          # after=-1 would mean "the last row only"
        while self.seq < hi:
            rows = self.get(f"/api/ledger?after={self.seq}")
            if not rows:
                break
            for e in rows:
                d = e.get("data") or {}
                if e.get("kind") == "order" and d.get("order") and d.get("buyer"):
                    self.buyers[d["order"]] = d["buyer"]
            self.seq = rows[-1]["seq"]
            if len(rows) < PAGE:
                break
        while len(self.buyers) > MAX_ORDERS:
            self.buyers.pop(next(iter(self.buyers)))

    def buyer(self, order):
        """The buyer of this order, or None when the ledger does not have it (or cannot be read in time).
        An order is issued minutes before its payment, so with warm() running it is almost always cached already."""
        if order in self.buyers:
            return self.buyers[order]
        if not self.lock.acquire(timeout=WAIT):  # a slow read elsewhere: do not hold the payment up
            return self.buyers.get(order)
        try:
            if order not in self.buyers:
                self._read()
            return self.buyers.get(order)
        finally:
            self.lock.release()

    def warm(self, on, log=print):
        """A daemon thread that keeps reading the ledger while on() is true (the refuse list is not empty)."""
        def loop():
            while True:
                try:
                    if on():
                        with self.lock:
                            self._read()
                except Exception as e:
                    log(f"[acp] refuse_buyers: ledger read failed ({type(e).__name__}: {e})")
                time.sleep(EVERY)
        threading.Thread(target=loop, daemon=True, name="acp-gate-ledger").start()


def _measure():
    spec = importlib.util.spec_from_file_location("week08_measure", os.path.join(HERE, "measure.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Gate:
    def __init__(self, orders=None, pricing=None, md=None, log=print, warm=False):
        self.orders = orders or OrderBuyers()
        self.pricing = pricing                  # tests pass a pricing.json path
        self.md = md
        self.log = log
        if warm:
            self.orders.warm(lambda: bool(refused_buyers(self.pricing)), log)

    def refusal(self, order, session=None):
        """The message to answer this checkout with instead of settling it, or None to settle as usual."""
        names = refused_buyers(self.pricing)
        if not names or not isinstance(order, str) or not order:
            return None
        try:
            buyer = self.orders.buyer(order)
        except Exception as e:                  # the ledger is down: never block a sale over it
            self.log(f"[acp] refuse_buyers: ledger lookup for {order} failed ({type(e).__name__}: {e}); settling")
            return None
        if buyer not in names:
            return None
        s = session or {}
        rec = {"buyer": buyer, "order": order, "session": s.get("id"), "task": s.get("task"), "unit": s.get("unit"),
               "item": (s.get("line_items") or [{}])[0].get("id"), "price": (s.get("totals") or [{}])[0].get("amount"),
               "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            _measure().write(self.md or os.environ.get("MARKET_DIR") or os.path.join(HERE, ".market"), "refused", rec)
        except Exception as e:
            self.log(f"[acp] refuse_buyers: could not record ({type(e).__name__}: {e})")
        self.log(f"[acp] REFUSED buyer {buyer} order {order} ({rec['item']} at {rec['price']}): answered out of stock")
        return OUT_OF_STOCK


_gate = None


def refusal(order, session=None, log=print):
    """Module-level entry for the kit: one Gate (and one ledger cache) per process."""
    global _gate
    if _gate is None:
        _gate = Gate(log=log, warm=True)
    return _gate.refusal(order, session)
