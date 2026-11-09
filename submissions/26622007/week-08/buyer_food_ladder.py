"""Food: open lower than 0.9 of the first ask, then climb back to it (review/W-20261109-1033-AX취약점루프/weakness-02-buyer.md).

saved is half of buyer ALL. Our food deals keep less of list price than the buyers above us at the same shop and grade:
11/09 07:44 cut, last 12 h, a food deal kept .356 of list against dragonash .400, sergiobuilds .385, 11im .379, and
food grade C at olstjr .482 against choo-chu .604, at psyoung82 .300 against dragonash .550. 112 of our 442 grade C
food talks ended with the shop taking our one 0.9 offer as it was (olstjr, totato2203-r3, psyoung82 every time,
yms979 96%): room below 0.9 that the one-step talk (oneshot.STEPS) never asks for.

Arm B prices the talk with the ladder (0.80, 0.90), or (0.70, 0.80, 0.90) when the quote names grade C. The limit,
the accept band and oneshot.code_turn's rules stay as they are, so the last rung is today's 0.9 and the worst case is
today's price after one or two more turns. Arm A keeps (0.9,). Both arms are recorded in orders.jsonl.

Eligible: a food talk priced by the one-step default (steps == (0.9,)), resumed from a quote with a numeric ask whose
opening code prices (code_priced, as the opening probes require), with no opening probe of its own (one-won, opening
discount, one-won default, food quote yield), without the B3 hold rung (buyer_b3b1: it acts only after a single 0.9
offer, so it would fire in arm A and never in B), and not an order the food shelf study (buyer_food_shelf) changes by
arm (would_drop not empty), so the studies touch disjoint talks. Everything else is untouched and unrecorded.
The grade is read from "grade C" / "grade=C" / "Grade C"; a quote that names it without the word (": C (close to its
date)") gets the two-rung ladder.

Switch: .market/buyer_food_ladder.json {"enabled": true, "study": "<id>", "salt": "<salt>"}; missing, unreadable or
anything else is off, and off is the current talk exactly. BUYER_FOOD_LADDER=0 in the environment is off too. Read at
every order, so no restart either way. Arms: sha256(salt:order id), first byte odd is B.
"""
import hashlib
import json
import os
import re

SWITCH = "buyer_food_ladder.json"
BASE = (0.9,)
LADDER = (0.8, 0.9)
LADDER_C = (0.7, 0.8, 0.9)
GRADE = re.compile(r"\bgrade\s*[=:]?\s*([ABC])(?![\w/])", re.I)


def config(md, env=None):
    """{"study", "salt"} with the switch on, else None."""
    env = os.environ if env is None else env
    if str(env.get("BUYER_FOOD_LADDER") or "").strip().lower() in ("0", "false", "no", "off"):
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


def decide(md, order, q, steps, resume, *, probing, code_priced, env=None):
    """(steps for this talk, record or None). Never raises: any failure is the current talk, unrecorded."""
    try:
        order = order or {}
        q = q or {}
        ask = q.get("ask")
        fsh = order.get("_food_shelf")
        if (order.get("kind") != "food" or steps != BASE or not resume or probing or not code_priced
                or not isinstance(ask, (int, float)) or isinstance(ask, bool)
                or order.get("_one_won_default") or order.get("_food_quote_yield") or order.get("_b3") is not None
                or (isinstance(fsh, dict) and fsh.get("would_drop"))):
            return steps, None
        cfg = config(md, env)
        a = arm(cfg, order.get("order"))
        if a is None:
            return steps, None
        g = grade(q.get("text"))
        out = BASE if a == "A" else LADDER_C if g == "C" else LADDER
        return out, {"study": cfg["study"], "arm": a, "grade": g, "steps": list(out)}
    except Exception:  # noqa: BLE001 - the study never changes an order it cannot read
        return steps, None
