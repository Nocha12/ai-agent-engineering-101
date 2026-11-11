"""Finite first-stock allocation trial. Does not change prices or add a SKU.

Only the nominated unlisted 48-hour food with observed zero own recent sales
and positive reachable history qualifies. B reserves at most one held/waiting/new
unit. Every existing restock guard still runs after this reduction.
"""
import json
import math
import re
import time
from pathlib import Path

SKU = "Gb834bbb5"
SWITCH = "restock_first_stock.json"


def number(x):
    return type(x) in (int, float) and math.isfinite(x)


def config(state, inv, model, catalog, listed, now=None):
    try:
        now = time.time() if now is None else now
        d = json.loads((Path(state) / SWITCH).read_text(encoding="utf-8"))
        start, stop = d.get("start"), d.get("stop")
        window = inv.get("restock") or {}
        opens, closes = window.get("opens_at"), window.get("closes_at")
        m = (model or {}).get("skus", {}).get(SKU) or {}
        item = catalog.get(SKU) or {}
        if (d.get("enabled") is not True or not isinstance(d.get("study"), str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", d["study"])
                or not all(number(x) for x in (now, start, stop, opens, closes))
                or not 1800 <= stop - start <= 3600 or not start <= now < stop
                or not start <= opens < closes <= stop or closes - opens > 180
                or not opens - 50 <= now < closes
                or type(d.get("b_parity")) is not int or d["b_parity"] not in (0, 1)
                or not model or model.get("active") is not True
                or not number(m.get("mine_rate")) or m["mine_rate"] != 0
                or not number(m.get("deals")) or m["deals"] < 3
                or SKU in listed or item.get("kind") != "food" or item.get("shelf_hours") != 48):
            return None
        return {"study": d["study"], "opens_at": opens,
                "arm": "B" if int(opens // 1200) % 2 == d["b_parity"] else "A"}
    except Exception:  # no experiment read failure can block normal supply
        return None


def quantity(on, sku, asked, held_and_waiting):
    if on and on.get("arm") == "B" and sku == SKU:
        return min(asked, max(0, 1 - held_and_waiting))
    return asked
