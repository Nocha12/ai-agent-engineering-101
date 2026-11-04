"""Round 3: the buyer's own purchase reports on the forum, written by code from what the market settled.

The number of posts a buyer writes is 10% of its rank (agent.md "Rankings"), and the Luna forum agent (forum.py) posts
about once in 20 minutes. After each paid deal review.py calls maybe_post: one line per settled deal of mine, at most
one every GAP_MINUTES, never while a buyer post still waits in the outbox (the kit sends one per 5 minutes and keeps the
rest). No model call: the post is the market's own receipt (price, list price, grade, claim, shelf life or warranty,
ledger seq). SKUs my own shop stocks are left out, so the reports do not send buyers to cheaper rivals of my shop.
"""
import fcntl
import json
import os
import time

GAP_MINUTES = 6          # the kit allows one buyer post per 5 minutes; forum.py's posts share that slot
STATE = "buyer_post_state.json"
FRESH_HOURS = 3          # a deal older than this is old news


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read_lines(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]
    except (OSError, ValueError):
        return []


def pending_buyer_post(md):
    """A buyer post the kit has not sent yet (rate limit): another one now would only queue behind it."""
    for line in read_lines(os.path.join(md, "outbox.jsonl")):
        if isinstance(line, dict) and "post" in line and line.get("as") == "buyer":
            return True
    return False


def my_shop_skus(md):
    inv = load(os.path.join(md, "inventory.json"), {})
    return {u.get("sku") for u in inv.get("units") or [] if isinstance(u, dict)}


def shop_names(memory_path):
    """order id -> shop name, from the buyer's memory (settled.jsonl names a shop only by its URL)."""
    m = load(memory_path, {})
    return {t.get("order"): t.get("shop") for t in m.get("trades") or [] if isinstance(t, dict) and t.get("order")}


def text(d, shop, now):
    price, value = d["price"], d["value"]
    parts = [f"[구매 기록] {(d.get('name') or d['sku'])[:70]} ({d['sku']})",
             f"{price:,}원, 정가 {value:,}원 대비 {round(100 * (value - price) / value)}% 저렴"]
    if d.get("grade"):
        honest = {True: "정직", False: "거짓"}.get(d.get("claim_honest"))
        parts.append(f"등급 {d['grade']}" + (f" (주장 {d['claim']}, {honest})" if d.get("claim") and honest else ""))
    if d.get("kind") == "food" and d.get("expires_at"):
        parts.append(f"남은 유통기한 {max(0, int((d['expires_at'] - now) / 3600))}시간")
    elif d.get("warranty_hours"):
        parts.append(f"보증 {d['warranty_hours']:g}h")
    if shop:
        parts.append(f"판매: {shop}")
    parts.append(f"ledger #{d['ledger']}")
    return " | ".join(parts)


def pick(settled, posted, mine, now):
    """The newest settled deal not posted yet that is worth telling: paid below list, not my shop's SKU, fresh."""
    for d in reversed(settled):
        if not isinstance(d, dict) or d.get("ledger") in posted or d.get("sku") in mine:
            continue
        if not all(isinstance(d.get(k), (int, float)) for k in ("price", "value", "ledger", "time")):
            continue
        if d["value"] <= d["price"] or now - d["time"] > FRESH_HOURS * 3600:
            continue
        return d
    return None


def maybe_post(md, settled_path, memory_path, now=None):
    """Writes at most one buyer post to md/outbox.jsonl. Returns the line, or None. Never raises."""
    now = time.time() if now is None else now
    try:
        with open(os.path.join(md, STATE + ".lock"), "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)      # another review hook is posting: let it
            except OSError:
                return None
            path = os.path.join(md, STATE)
            state = load(path, {"at": 0, "posted": []})
            if now - state.get("at", 0) < GAP_MINUTES * 60 or pending_buyer_post(md):
                return None
            d = pick(read_lines(settled_path), set(state.get("posted") or []), my_shop_skus(md), now)
            if d is None:
                return None
            line = {"post": text(d, shop_names(memory_path).get(d.get("order")), now), "refs": [d["ledger"]], "as": "buyer"}
            with open(os.path.join(md, "outbox.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
            state = {"at": now, "posted": (list(state.get("posted") or []) + [d["ledger"]])[-500:]}
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp, path)
            return line
    except Exception:  # noqa: BLE001 - a forum post never stops the review the kit waits for
        return None
