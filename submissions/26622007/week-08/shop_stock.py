"""Round 3: the shop's stock, read from the .market/inventory.json the kit keeps (every 10 s and after each supply).

The shop runs on Render with --deployed: the kit in the same service refreshes the file but serves no checkout, so
the seller (which unit to promise) and acp_shop (which unit to ship) share one StockBook in this process.
The file can be 10 s old, so the book also remembers what it has promised and sold since.
"""
import json
import math
import os
import re
import time
from pathlib import Path

ECON_ROUND = 3
HOLD_SECONDS = 300      # a talk silent this long frees its unit (each message renews it); 900 s hid stock for most of a window
PAY_SECONDS = 120       # after the deal: the unit waits this long for its payment (paid deals settle within seconds), then is on sale again
PAY_MARGIN_HOURS = 0.1  # food must outlast the buyer's need by this much (time to pay), GPT Pro §3
SKU = re.compile(r"\bG[0-9a-f]{8}\b")
HOURS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h\b|hrs?\b|hours?\b|시간)", re.I)
SHELF_WORDS = re.compile(r"shelf|last|expir|fresh|after payment|유통|소비|남", re.I)
GRADES = re.compile(r"grades?\s*(?:of\s*|:\s*)?([ABC](?:\s*(?:,|/|or|and)\s*[ABC])*)\b|\b([ABC])\s*(?:등급|grade\b)")


def needs(text):
    """What the buyer's words ask of a unit: {"skus", "hours", "grades", "cert"}. SKUs named are the only acceptable ones;
    hours is the shelf life asked for (food), grades the grades allowed. Words about a warranty's hours are not shelf life."""
    t = text or ""
    hours = [float(m.group(1)) for m in HOURS.finditer(t)
             if SHELF_WORDS.search(t[max(0, m.start() - 40):m.end() + 25])
             and not re.search(r"warrant|guarantee|보증", t[max(0, m.start() - 25):m.end() + 12], re.I)]
    grades = set()
    for m in GRADES.finditer(t):
        grades |= set(re.findall(r"[ABC]", m.group(1) or m.group(2)))
    return {"skus": set(SKU.findall(t)), "hours": max(hours) if hours else None, "grades": grades or None,
            "cert": bool(re.search(r"certif|cert\s*=\s*true|인증서", t, re.I))}


def unfit(u, need, now=None):
    """Why this unit cannot serve this buyer, or None: SKU, shelf life left >= need + PAY_MARGIN_HOURS, grade, certificate."""
    if need["skus"] and u["sku"] not in need["skus"]:
        return f"{u['sku']} is not the SKU asked for"
    if need["hours"] is not None and u.get("expires_at"):
        left = (u["expires_at"] - (now or time.time())) / 3600
        if left < need["hours"] + PAY_MARGIN_HOURS:
            return f"{left:.1f} h of shelf life left, {need['hours']:g} h asked"
    if need["grades"] and u.get("grade") not in need["grades"]:
        return f"grade {u.get('grade')}, {'/'.join(sorted(need['grades']))} asked"
    if need["cert"] and not u.get("cert"):
        return "no certificate"
    return None


def market_dir():
    return Path(os.environ.get("MARKET_DIR", ".market"))


def load_inventory():
    try:
        inv = json.loads((market_dir() / "inventory.json").read_text(encoding="utf-8"))
        return inv if isinstance(inv, dict) else {}
    except (OSError, ValueError):
        return {}


def last_segment(category):
    return (category or "").split(">")[-1].lower()


def expired(u, now=None):
    return u.get("grade") == "expired" or bool(u.get("expires_at") and u["expires_at"] <= (now or time.time()))


MARKDOWN = {"clear_hours": 24, "min_floor_share": 0.1, "stale_days": 3, "stale_per_day": 0.1, "end_hours": 48,
            "below_cost": ["food_expiry", "stale_goods", "round_end"]}


def markdown(u, cfg=None, ends_at=None, now=None):
    """(lowest price for this unit, share of cost it keeps, why). Selling below cost beats what waiting brings: food past
    its date is lost at cost, and stock left when the round ends counts as a loss. The numbers are starting values in
    pricing.json "markdown"; Round 3's own data has to judge them. "below_cost" lists the reasons allowed to go under
    cost (food_expiry, stale_goods, round_end): the shop starts with food near its date only."""
    cfg, now = {**MARKDOWN, **(cfg or {})}, now or time.time()
    allowed = set(cfg["below_cost"])
    cost, factors = math.ceil(u["cost"]), [(1.0, "")]
    supplied = u.get("supplied_at") or now
    if u.get("expires_at") and "food_expiry" in allowed:
        left, life = (u["expires_at"] - now) / 3600, (u["expires_at"] - supplied) / 3600
        clear = min(cfg["clear_hours"], max(life, 1) * 0.5)
        if left < clear:
            factors.append((max(0.0, left / clear), f"{left:.1f} h of shelf life left"))
    elif not u.get("expires_at") and "stale_goods" in allowed:
        over = (now - supplied) / 86400 - cfg["stale_days"]
        if over > 0:
            factors.append((max(0.0, 1 - over * cfg["stale_per_day"]), f"{over + cfg['stale_days']:.1f} days in stock"))
    if ends_at and "round_end" in allowed:
        left = (ends_at - now) / 3600
        if left < cfg["end_hours"]:
            factors.append((max(0.0, left / cfg["end_hours"]), f"{left:.1f} h to the end of the round"))
    keep, why = min(factors, key=lambda f: f[0])
    least = min(cost, math.ceil((u.get("list_price") or 0) * cfg["min_floor_share"]))
    return max(least, round(cost * keep)), keep, why


class StockBook:
    """A unit is free, promised to a talk (held, renewed by each message), agreed and waiting for its payment (held for
    PAY_SECONDS), or sold. Payment shows as the unit leaving inventory.json (the kit's checkout) or mark_sold (ours):
    on_paid(info) is called then, on_unpaid(info, why) when the wait runs out or the checkout is cancelled."""

    def __init__(self, load=load_inventory):
        self.load = load
        self.held = {}      # unit -> time promised (renewed); a hold lasts HOLD_SECONDS, or PAY_SECONDS once agreed
        self.owner = {}     # unit -> task that holds it
        self.agreed = {}    # unit -> {"task", "at", "expires_at", ...} deals waiting for their payment
        self.sold = set()
        self.on_paid = self.on_unpaid = None

    def economy(self):
        """True from Round 3 on. Before that the shop keeps selling from catalog.json."""
        return self.load().get("round", 0) >= ECON_ROUND

    def grade(self, unit):
        """The unit's grade now (a food unit's grade drops while it waits), or None if it is gone."""
        return next((u.get("grade") for u in self.load().get("units", []) if u["unit"] == unit), None)

    def settle(self, inv_units=None, now=None):
        """Agreed deals: paid when the unit left the stock (not by expiring), unpaid when the wait ran out."""
        if not self.agreed:
            return
        now = now or time.time()
        present = {u["unit"] for u in (inv_units if inv_units is not None else self.load().get("units", []))}
        for unit, info in list(self.agreed.items()):
            if unit not in present:
                del self.agreed[unit]
                self._drop(unit)
                if info.get("expires_at") and info["expires_at"] <= now:
                    self._call(self.on_unpaid, info, "expired before payment")
                else:
                    self.sold.add(unit)
                    self._call(self.on_paid, info)
            elif now - self.held.get(unit, info["at"]) >= PAY_SECONDS:     # a checkout being paid renews the hold
                del self.agreed[unit]
                self._drop(unit)
                self._call(self.on_unpaid, info, f"not paid within {PAY_SECONDS} s")

    @staticmethod
    def _call(fn, *args):
        if fn is not None:
            fn(*args)

    def _drop(self, unit):
        self.held.pop(unit, None)
        self.owner.pop(unit, None)

    def units(self):
        """Units free to promise: in stock, not expired, not promised, not sold since the file was written."""
        now = time.time()
        inv = self.load().get("units", [])
        self.settle(inv, now)
        for unit, t in list(self.held.items()):
            if now - t >= (PAY_SECONDS if unit in self.agreed else HOLD_SECONDS):
                self._drop(unit)
        return [u for u in inv if not expired(u, now) and u["unit"] not in self.held and u["unit"] not in self.sold]

    def pick(self, text, now=None):
        """(unit, None) for what the buyer asked (SKU first, then name, then category): among the units that fit the buyer's
        needs (needs()/unfit()), the one that expires first. (None, why) when none does."""
        want, need = text.lower(), needs(text)
        units = self.units()
        for matches in (lambda u: u["sku"].lower() in want,
                        lambda u: (u.get("name") or "\0").lower() in want,
                        lambda u: (last_segment(u.get("category")) or "\0") in want):
            fit = [u for u in units if matches(u)]
            if not fit:
                continue
            ok = [u for u in fit if unfit(u, need, now) is None]
            if ok:
                return min(ok, key=lambda u: (u.get("expires_at") or float("inf"), u.get("supplied_at") or 0)), None
            return None, unfit(fit[0], need, now)
        return None, "not in stock"

    def find(self, text):
        return self.pick(text)[0]

    def hold(self, unit, task=None):
        self.held[unit] = time.time()
        if task is not None:
            self.owner[unit] = task

    def holder(self, unit):
        """The task holding this unit now, None when it is free (a run-out hold counts as free)."""
        self.units()
        return self.owner.get(unit) if unit in self.held else None

    def agree(self, unit, info):
        """The talk closed a deal on this unit: keep it for the payment."""
        self.held[unit] = time.time()
        self.agreed[unit] = {"at": time.time(), **info}
        if info.get("task") is not None:
            self.owner[unit] = info["task"]

    def release(self, unit):
        info = self.agreed.pop(unit, None)
        self._drop(unit)
        if info is not None:
            self._call(self.on_unpaid, info, "checkout cancelled or refused")

    def mark_sold(self, unit):
        info = self.agreed.pop(unit, None)
        self._drop(unit)
        self.sold.add(unit)
        if info is not None:
            self._call(self.on_paid, info)

    def ship(self, receipt, acceptable=None):
        """(unit dict, None) to ship for a receipt, or (None, reason). Same rule as the kit's AcpShop.pick_unit:
        the seller's unit if it still fits, else the first-expiring unit of a fitting SKU."""
        want = receipt.get("item")
        fits = lambda u: (u["sku"] in acceptable if acceptable else
                          u["sku"] == want or last_segment(u.get("category")) == str(want).lower())
        promised = receipt.get("unit")
        if promised:
            now = time.time()
            u = next((u for u in self.load().get("units", []) if u["unit"] == promised), None)
            if u and not expired(u, now) and promised not in self.sold and fits(u):
                return u, None
            return None, f"unit {promised} is not in stock for this order"
        fit = [u for u in self.units() if fits(u)]
        if not fit:
            return None, f"no {want} in stock"
        return min(fit, key=lambda u: (u.get("expires_at") or float("inf"), u.get("supplied_at") or 0)), None
