"""Default-OFF, order-level 30 minute quote reader AB. No price policy changes.

Journal contains structured assignment/quote metadata only, never shop text.
Reservation precedes product selection; a missing journal always keeps legacy.
"""
import fcntl
import hashlib
import json
import math
import os
import re
import time

import ask_read

JOURNAL = "quote_alignment.jsonl"
DURATION = 1800


def parameters(env=None):
    env = os.environ if env is None else env
    if env.get("BUYER_QUOTE_ALIGN", "0") != "1":
        return None
    try:
        start = float(env.get("BUYER_QUOTE_ALIGN_START", ""))
    except (ValueError, TypeError):
        return None
    study = env.get("BUYER_QUOTE_ALIGN_STUDY", "")
    if not math.isfinite(start) or start <= 0 or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", study):
        return None
    return study, start, start + DURATION


def append(path, row):
    """Durable single append across quote threads/processes. Fail closed on IO error."""
    try:
        with open(path, "a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(json.dumps(row, ensure_ascii=True, allow_nan=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return True
    except (OSError, ValueError, TypeError):
        return False


def reserve(md, order, now=None, env=None):
    """Assign all starting orders, including those with no eligible quote/product."""
    p = parameters(env)
    now = time.time() if now is None else now
    oid = order.get("order")
    if not p or not isinstance(oid, str) or not oid or not p[1] <= now < p[2]:
        return None
    arm = "AB"[hashlib.sha256((p[0] + ":" + oid).encode()).digest()[0] % 2]
    row = {"event": "assignment", "study": p[0], "order": oid, "arm": arm,
           "at": now, "start": p[1], "end": p[2], "kind": order.get("kind"),
           "category": order.get("category")}
    path = os.path.join(md, JOURNAL)
    return dict(row, path=path) if append(path, row) else None


def read_quote(context, text, offer, legacy, raw, *, shop, open_, refused, late,
               receipt=False, now=None, env=None):
    """Additional read only when legacy found no number; journal both arms.

    Called only at quote publication, under quote_ctl's lock. Late, terminal,
    receipt and refused responses never receive a new quoted value.
    """
    p = parameters(env)
    now = time.time() if now is None else now
    if not context or not p or context.get("study") != p[0] or context.get("start") != p[1] or not p[1] <= now < p[2]:
        return legacy
    candidate = None
    reason = "legacy"
    if legacy is None and raw is None:
        reason = "not_open" if not open_ else "late" if late else "receipt" if receipt else "refused" if refused else None
        if reason is None:
            candidate, yours = ask_read.read(text)
            lp = offer.get("list_price")
            named = set(re.findall(r"\bG[0-9a-f]{8}\b", text or "", re.I))
            if (not isinstance(candidate, int) or isinstance(candidate, bool) or candidate <= 0 or yours
                    or not isinstance(lp, (int, float)) or isinstance(lp, bool) or not math.isfinite(lp) or lp <= 0
                    or not .05 * lp <= candidate <= lp
                    or named - {offer.get("sku")}):
                candidate, reason = None, "strict_or_bounds"
            else:
                reason = "opportunity"
    applied = candidate is not None and context.get("arm") == "B"
    row = {"event": "quote", "study": p[0], "order": context["order"], "arm": context["arm"],
           "at": now, "shop": shop, "sku": offer.get("sku"), "legacy": legacy,
           "candidate": candidate, "reason": reason, "applied": applied}
    if not append(context["path"], row):
        return legacy
    return candidate if applied else legacy
