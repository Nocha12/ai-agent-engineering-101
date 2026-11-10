"""The buyer's market-reference line read by code instead of Jev (지시23, 11/10).

One buyer sends, word for word: "That is more than I want to pay. This item sold for <market price> on the market in
the last few hours. I can do <offer>." Two numbers, so fast_classify.bare_offer cannot take it and every such turn
called Jev (seller-jev). market-kit.log(.1) 11/10 08:48-13:59: 85 such turns, all this one shape; Jev read the "I can do"
number every time and the jevrule engine countered it (testdata/market_ref_cases.jsonl). They were 13 of the 16 Jev
calls in 12:25-12:55.

When the switch is on, seller_reader_trial.Reader answers that exact line with (offer, 1.0) and makes no Jev call. Every
check after the reader is unchanged (the offer must be written in the message, not one of our asks, under our ask, our
ask only with words of agreement, the SUSPECT screen, the price guards in seller.py), so the turn ends as it did when
Jev read the same number. Any other wording, an extra word or a second line still goes to Jev.

Switch: MARKET_DIR/market_ref_read.json {"enabled": true, "start_at": <epoch>, "stop_at": <epoch, optional>}, read at
every turn; absent, invalid, not a regular file, before start_at or at/after stop_at: off (Jev reads as before).
"""
import json
import math
import os
from pathlib import Path
import re
import stat
import time

N = r"([1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]*)"
LINE = re.compile(r"That is more than I want to pay\. This item sold for " + N +
                  r" on the market in the last few hours\. I can do " + N + r"\.")
MAX_TEXT = 500
MAX_CONFIG_BYTES = 4096
KEYS = {"enabled", "start_at", "stop_at"}


def offer(text):
    """The "I can do" number of the exact market-reference line, else None."""
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        return None
    m = LINE.fullmatch(text.strip())
    return int(m[2].replace(",", "")) if m else None


def _time(v):
    return type(v) in (int, float) and math.isfinite(v)


class Switch:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else Path(os.environ.get("MARKET_DIR") or Path(__file__).parent / ".market")

    def on(self, now=None):
        now = time.time() if now is None else now
        p = self.root / "market_ref_read.json"
        try:
            st = os.stat(p)
            if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_CONFIG_BYTES:
                return False
            c = json.loads(p.read_bytes().decode("utf-8"))
        except Exception:  # noqa: BLE001 - absent or unreadable: off
            return False
        if not isinstance(c, dict) or set(c) - KEYS or c.get("enabled") is not True or not _time(c.get("start_at")):
            return False
        if "stop_at" in c and not _time(c["stop_at"]):
            return False
        return c["start_at"] <= now and ("stop_at" not in c or now < c["stop_at"])


SWITCH = Switch()
