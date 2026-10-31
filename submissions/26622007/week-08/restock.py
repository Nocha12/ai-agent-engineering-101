"""The kit's restock hook (Round 3, every 60 s): prints {"buy": [{"sku", "qty"}]}, and "bankrupt": true when stuck.

    python restock.py --state .market

The first stock, before the ledger says what sells: every category gets a few units of the SKU nearest its median
price, since an order's acceptable SKUs lie around a reference price. Food only with a long shelf life (no storage
fee and it outlasts the round), goods only cheap ones (a tied-up sofa pays storage). A sold unit is bought again.
pricing.json "restock" holds the numbers. No harness: this is arithmetic.
"""
import argparse
import collections
import json
import os
import random
import sys
import time

WORST_COST = 0.75      # a unit's cost is at most this share of its list price (A 55-75%)
DEFAULTS = {"keep_cash": 1500000, "target_per_category": 2, "min_shelf_hours": 720, "goods_max_price": 50000,
            "max_lines": 10}


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def plan(inv, catalog, demand, cfg):
    """The hook's answer for this state."""
    balance, units = inv.get("balance") or 0, inv.get("units") or []
    if balance < inv.get("bankrupt_line", 0) and not units and (inv.get("bankruptcies_left") or 0) > 0:
        return {"bankrupt": True, "buy": []}
    now = time.time()
    have = collections.Counter(u.get("category") for u in units
                               if u.get("grade") != "expired" and not (u.get("expires_at") and u["expires_at"] <= now))
    skus = collections.defaultdict(list)
    for sku, x in catalog.items():
        if x.get("kind") == "food":
            if (x.get("shelf_hours") or 0) >= cfg["min_shelf_hours"]:
                skus[x["category"]].append((sku, x))
        elif x.get("list_price", float("inf")) <= cfg["goods_max_price"]:
            skus[x["category"]].append((sku, x))
    rows = [d for d in demand if skus.get(d["category"]) and have[d["category"]] < cfg["target_per_category"]]
    random.shuffle(rows)
    rows.sort(key=lambda d: have[d["category"]])       # the emptiest categories first
    spare, buy = balance - cfg["keep_cash"], []
    for d in rows[:cfg["max_lines"]]:
        sku, x = min(skus[d["category"]], key=lambda s: abs(s[1]["list_price"] - d.get("median_price", 0)))
        qty = cfg["target_per_category"] - have[d["category"]]
        cost = x["list_price"] * WORST_COST * qty
        if cost <= spare:
            buy.append({"sku": sku, "qty": qty})
            spare -= cost
    return {"buy": buy}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    args = ap.parse_args()
    inv = load(os.path.join(args.state, "inventory.json"), {})
    cfg = {**DEFAULTS, **load("pricing.json", {}).get("restock", {})}
    out = plan(inv, load(os.path.join(args.state, "catalog.json"), {}), load(os.path.join(args.state, "demand.json"), []), cfg)
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
