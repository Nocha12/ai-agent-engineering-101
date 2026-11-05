"""The market's patch notes (https://market.ourolabs.ai/rules), read at every meta-harness cycle.

The rules change during Round 3: Patch 3.2 (11/04) added surge cost and renamed profit to cash, Patch 3.3 (11/05) scored
shops by book instead of cash. The scorecard still ranked cash for hours after 3.3, because nothing told Terra and Astra
that the score had changed. check() parses the page's patch list and its scoring bars, remembers when each patch was first
seen (.market/patch_notes.json), and compares the published weights with scorecard.OBJECTIVE, so the pack says:
- new: patches first seen since the previous check (this cycle's news), recent: those first seen in the last day;
- scoring: the published parts and weights; objective_matches false and mismatch when scorecard.OBJECTIVE disagrees.
No model call. A page that cannot be read or parsed gives {"error": ...} and leaves the record alone.

    python patch_notes.py          # prints what a cycle would see (and records it)
"""
import html
import json
import os
import re
import time

import httpx

RULES = "https://market.ourolabs.ai/rules"
STATE = os.path.join(".market", "patch_notes.json")
RECENT_HOURS = 24
KINDS = {"Shop ALL": "shops", "Buyer ALL": "buyers"}


def text(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse(page):
    """{"patches": [{"id", "date", "title", "changes": [{"tag", "text"}]}], "scoring": {"shops": [[part, weight]], ...}}."""
    patches = []
    for art in re.findall(r'<article class="patch[^"]*">(.*?)</article>', page, re.S):
        head = dict(re.findall(r'<span class="(v|d|t)">(.*?)</span>', art.split("</header>")[0], re.S))
        changes = [{"tag": text(tag), "text": text(body)}
                   for tag, body in re.findall(r'<li><span class="tag">(.*?)</span>(.*?)</li>', art, re.S)]
        if head.get("v"):
            patches.append({"id": text(head["v"]), "date": text(head.get("d", "")), "title": text(head.get("t", "")),
                            "changes": changes})
    scoring = {}
    for name, parts in re.findall(r'<div class="bar"><span>(.*?)</span><div class="parts">(.*?)</div></div>', page, re.S):
        kind = KINDS.get(text(name))
        if not kind:
            continue
        got = []
        for width, label in re.findall(r'<span class="p\d+" style="width:(\d+(?:\.\d+)?)%">(.*?)</span>', parts, re.S):
            words = text(label).split()
            if words:
                got.append([words[0].lower(), round(float(width) / 100, 3)])
        scoring[kind] = got
    return {"patches": patches, "scoring": scoring}


def compare(scoring, objective):
    """Where the published weights and scorecard.OBJECTIVE disagree: [str], empty when they agree."""
    out = []
    for kind, parts in objective.items():
        published = scoring.get(kind)
        if not published:
            out.append(f"{kind}: no published weights found")
            continue
        mine = [(names, w) for names, w in parts]
        if len(published) != len(mine):
            out.append(f"{kind}: published {published}, scorecard {[(n[0], w) for n, w in mine]}")
            continue
        for (part, w), (names, mw) in zip(published, mine):
            if part not in names or abs(w - mw) > 1e-6:
                out.append(f"{kind}: published {part} {w}, scorecard {names[0]} {mw}")
    return out


def check(root=".", page=None, now=None, objective=None):
    """What this cycle should know about the rules. Never raises."""
    now = time.time() if now is None else now
    try:
        if page is None:
            page = httpx.get(RULES, timeout=20).text
        got = parse(page)
        if not got["patches"] or not got["scoring"]:
            return {"error": "the rules page had no patch list or scoring bars (layout changed?)", "url": RULES}
        if objective is None:
            import scorecard
            objective = scorecard.OBJECTIVE
        path = os.path.join(root, STATE)
        try:
            with open(path, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        seen, last = st.get("first_seen") or {}, st.get("checked_at") or 0
        for p in got["patches"]:
            seen.setdefault(p["id"], now)
        mismatch = compare(got["scoring"], objective)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"first_seen": seen, "checked_at": now, "scoring": got["scoring"]}, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
        first = lambda p: seen[p["id"]]
        return {"url": RULES, "latest": got["patches"][0]["id"],
                "new": [p for p in got["patches"] if last and first(p) > last],
                "recent": [p for p in got["patches"] if now - first(p) < RECENT_HOURS * 3600],
                "first_check": not last, "scoring": got["scoring"],
                "objective_matches": not mismatch, "mismatch": mismatch}
    except Exception as e:  # noqa: BLE001 - the rules page never stops a cycle
        return {"error": f"{type(e).__name__}: {e}", "url": RULES}


if __name__ == "__main__":
    print(json.dumps(check(os.path.dirname(os.path.abspath(__file__))), ensure_ascii=False, indent=1))
