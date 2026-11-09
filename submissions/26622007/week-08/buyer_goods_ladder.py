"""Goods: open lower than 0.9 of the first ask, then climb back to it (review/W-20261109-1338-AXPro주기/report.md).

saved is half of buyer ALL and counts every deal by the share of list price it keeps, goods and food alike. In the last
24 h (11/09 13:4x, .market/measure/orders.jsonl joined to payments.jsonl paid) 162 of our 490 paid goods talks ended
with the shop taking our one 0.9 offer as it was: ddolcom 79/132, 25620027 36/57, yms979 30/33, yeonsong2 10/22. Had a
0.8 offer been taken there, each of those deals would have kept .0635 more of list price. buyer_food_ladder runs the
same ladder on food; this is the goods half, on its own switch and salt so the two studies are independent.

Arm B prices the talk with the ladder (0.80, 0.90). The limit, the accept band and oneshot.code_turn's rules stay as
they are, so the last rung is today's 0.9 and the worst case is today's price after one more turn. Arm A keeps (0.9,).
Both arms are recorded in orders.jsonl (an order outside the study records null). Arm B's talks are left out of the
shop learning both arms read (buyer_shop_profile, buyer_rank_pg: laddered), as buyer_goods_grade.lowered does, so arm
B's lower openings never move arm A's accept band or shop choice.

Eligible: a goods talk priced by the one-step default (steps == (0.9,)), resumed from a quote whose opening code
prices (code_priced: buyer_goods_first_one.first_decision, the goods opening rule), with a numeric ask, no opening
probe of its own (goods first-one, goods extra quote, quote alignment) and without the B3 hold rung (buyer_b3b1 acts
only after a single 0.9 offer). Everything else is untouched.

Switch: .market/buyer_goods_ladder.json {"enabled": true, "study": "<id>", "salt": "<salt>"}; missing, unreadable or
anything else is off, and off is the current talk exactly. BUYER_GOODS_LADDER=0 in the environment is off too. Read at
every order, so no restart either way. Arms: sha256(salt:order id), first byte odd is B.
"""
import hashlib
import json
import os

SWITCH = "buyer_goods_ladder.json"
BASE = (0.9,)
LADDER = (0.8, 0.9)


def config(md, env=None):
    """{"study", "salt"} with the switch on, else None."""
    env = os.environ if env is None else env
    if str(env.get("BUYER_GOODS_LADDER") or "").strip().lower() in ("0", "false", "no", "off"):
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


def decide(md, order, q, steps, resume, *, probing, code_priced, env=None):
    """(steps for this talk, record or None). Never raises: any failure is the current talk, unrecorded."""
    try:
        order = order or {}
        q = q or {}
        ask = q.get("ask")
        if (order.get("kind") != "goods" or steps != BASE or not resume or probing
                or not isinstance(ask, (int, float)) or isinstance(ask, bool)
                or order.get("_b3") is not None):
            return steps, None
        cfg = config(md, env)
        a = arm(cfg, order.get("order"))
        if a is None:
            return steps, None
        if not (code_priced() if callable(code_priced) else code_priced):   # read only with the study on
            return steps, None
        out = BASE if a == "A" else LADDER
        return out, {"study": cfg["study"], "arm": a, "steps": list(out)}
    except Exception:  # noqa: BLE001 - the study never changes an order it cannot read
        return steps, None


def laddered(row):
    """Whether an orders.jsonl row is an arm B talk of this study: the shop learning that both arms read leaves it out."""
    g = row.get("goods_ladder") if isinstance(row, dict) else None
    return isinstance(g, dict) and g.get("arm") == "B"
