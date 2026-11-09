"""Goods: price a unit by its grade's chance of breaking, not its SKU's (review/W-20261109-1033-AX취약점루프/weakness-02-buyer.md §5).

saved_cap and value_limit take market_model.break_chance, one rate per SKU over every grade sold. The grade is most of
the risk (market 48 h to 11/09 07:44: grade C broke 31.7%, B 11.3%, A 2.8%), so a grade C unit passes with an A-like
cap: our last 12 h grade C goods kept .242 of list on average against .356 for every deal, at a .407 discount, because
41% broke and a broken unit is refunded and keeps 0.

Arm B takes max(SKU chance, market_model.unit_break_chance(stats, sku, grade)) when the chosen shop's quote names one
grade, so saved_cap's list (1 - 0.30 / (1 - b)) asks a deeper discount of a grade B or C unit (the short warranty cap
reads the grade the same way: "grade C", "Grade C", one grade only). The chance never goes down, so a grade A unit is
priced as today. Arm A keeps the SKU chance. Both arms are recorded in orders.jsonl ("goods_grade"), with the chance B
uses, so the eligible set is the same in both. Only the limit moves: what is bought, the shops asked and the choice
among them stay as they are, and a lower limit can only mean fewer or cheaper deals.

Eligible: a round 3 goods order with an id, the chosen shop's quote naming one grade, a SKU chance (enough goods sold),
and no short warranty expected (buyer_short_warranty: its own cap with the grade's chance). Everything else is
untouched and unrecorded. Disjoint from buyer_food_ladder and buyer_food_shelf (food only); buyer_quote_deadline is on
for all goods (both arms). The goods no-fit A/B (buyer_rank_pg, nofit-ab-goods-fixed-0025) ended 11/09 13:29 with its
switch set to nofit false, which leaves rk["nofit"] false on every order, so it is no condition here.

Switch: .market/buyer_goods_grade.json {"enabled": true, "study": "<id>", "salt": "<salt>"}; missing, unreadable or
anything else is off, and off is the current limit exactly. BUYER_GOODS_GRADE=0 in the environment is off too. Read at
every order, so no restart either way. Arms: sha256(salt:order id), first byte odd is B.
"""
import hashlib
import json
import os
import re

import market_model

SWITCH = "buyer_goods_grade.json"
GRADE = re.compile(r"(?i)\bgrade ([abc])\b")      # market_buyer.short_cap's reading of a quote


def config(md, env=None):
    """{"study", "salt"} with the switch on, else None."""
    env = os.environ if env is None else env
    if str(env.get("BUYER_GOODS_GRADE") or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        with open(os.path.join(md, SWITCH), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("enabled") is not True:
        return None
    study, salt = d.get("study"), d.get("salt")
    if not (isinstance(study, str) and study and isinstance(salt, str) and salt):
        return None
    return {"study": study, "salt": salt}


def arm(cfg, order_id):
    """"A" or "B" for an order id under the study's salt; None without a study or an id."""
    if not cfg or not isinstance(order_id, str) or not order_id:
        return None
    return "B" if hashlib.sha256(f"{cfg['salt']}:{order_id}".encode("utf-8", "surrogatepass")).digest()[0] % 2 else "A"


def grade(text):
    """The one grade a quote names ("grade C"), else None (none named, or more than one)."""
    found = {g.upper() for g in GRADE.findall(text or "")}
    return found.pop() if len(found) == 1 else None


def _p(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x <= 1


def adjust(md, order, q, stats, sku, chance, sw_h, env=None):
    """(chance for this order's limit, record or None). Never raises: any failure is the SKU chance, unrecorded."""
    try:
        order = order or {}
        if order.get("kind") != "goods" or sw_h is not None or not stats or not _p(chance):
            return chance, None
        g = grade((q or {}).get("text"))
        if g is None:
            return chance, None
        cfg = config(md, env)
        a = arm(cfg, order.get("order"))
        if a is None:
            return chance, None
        unit = market_model.unit_break_chance(stats, sku, g)
        if not _p(unit):
            return chance, None
        b = max(chance, unit)
        return (b if a == "B" else chance), {"study": cfg["study"], "arm": a, "grade": g, "sku_chance": round(chance, 4),
                                             "grade_chance": round(unit, 4), "raised": unit > chance}
    except Exception:  # noqa: BLE001 - the study never changes an order it cannot read
        return chance, None
