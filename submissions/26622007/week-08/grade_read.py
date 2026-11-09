"""Default-off A/B G-read (11/10): the outside buyer's grade-preference opening read with its B.

11/10 06:20-07:58 market-kit.log: 31 talks open with "I want to buy <name> (<SKU>). I prefer grade C (refurbished) or B
(damaged box) with a warranty of at least 6 hours on the receipt, priced for its grade; grade A only if you have nothing
else. Please quote your cheapest unit that way." shop_stock.needs reads that as grades {A, C}: GRADES takes "grade C"
and "grade A", and the "or B (damaged box)" after the parenthesis is lost. So shop_stock.pick never offers a free
grade B unit there, even the one with the lowest floor: G8ec51935's B unit (u-7d82c4d0860b, floor 1509825, in stock from
06:42) was passed over three times for A units (floor 1706590); in two of them the buyer offered 1546976 and 1639776,
above the B floor, below the A one, and canceled. review/W-20261109-2040-AX셀러전략/report.md §10-10.

Treatment (arm B): for that exact sentence only, pick also allows grade B (shop_stock.pick add_grades), so the unit
with the lowest floor among A, B and C is offered, as for any buyer who names its grades. Quote, floor, warranty and every
guard after the pick are unchanged; the B unit is priced as a B (profit_price grade_break). Arm A: the original path.

Randomization unit: the task, by sha256(salt|study|version|task) against share, journaled (event "assigned") before the
pick; a failed write keeps the original path. "selected" records the unit picked; outcome rows (seller.remember) follow.
Window: start_at < stop_at and stop_at - start_at <= 20 h, else the original path. Switch (GRADE_READ_CONFIG overrides):
{study, salt, version, epoch, enabled, mode "ab"|"adopted", share, start_at, stop_at}.
Overlap: a talk moved to a B unit leaves the grade A only trials (floor_margin, a_open) and, for furniture, enters the
grade-break trial (GBT): run it after GBT's window or analyse them together (rows carry the unit and its grade).
Switch: .market/grade_read/config.json
"""
import hashlib
import os
from pathlib import Path
import re
import time

import shelf_jev_skip
import shop_stock

MAX_WINDOW = 20 * 3600
MAX_TEXT = 1000
SENTENCE = re.compile(r"I prefer grade C \(refurbished\) or B \(damaged box\) with a warranty of at least "
                      r"[0-9]{1,6}(?:\.[0-9]{1,4})? hours on the receipt, priced for its grade; grade A only if you have "
                      r"nothing else\.")
MORE_TRIALS = ("floor_margin", "a_open", "food_margin", "shelf_jev", "warranty_fact")


def extra_grades(text):
    """{"B"} when the opening is the exact sentence and the parser read it as {A, C}; else None."""
    if not isinstance(text, str) or len(text) > MAX_TEXT or any(c in text for c in "{}\""):
        return None
    if len(SENTENCE.findall(text)) != 1:
        return None
    if shop_stock.needs(text)["grades"] != {"A", "C"}:
        return None
    return {"B"}


class Trial(shelf_jev_skip.Trial):
    def __init__(self, root=None):
        super().__init__(root)
        self.root = self.root.parent / "grade_read"

    def _path(self):
        return Path(os.path.abspath(os.environ.get("GRADE_READ_CONFIG") or self.root / "config.json"))

    @staticmethod
    def _others(deal):
        out = shelf_jev_skip.Trial._others(deal)
        for k in MORE_TRIALS:
            v = deal.get(k)
            if isinstance(v, dict):
                out[k] = {f: v[f] for f in shelf_jev_skip.OTHER_FIELDS if isinstance(v.get(f), (str, int, float, bool))}
        return out

    def config(self, now=None):
        cfg = super().config(now)
        if cfg is None or cfg["stop_at"] - cfg["start_at"] > MAX_WINDOW:
            return None
        return cfg

    def assign(self, task, text, now=None):
        """(assignment, grades to add to the pick) or (None, None) for the original path; never raises."""
        try:
            extra = extra_grades(text)
            if extra is None:
                return None, None
            now = time.time() if now is None else now
            cfg = self.config(now)
            if cfg is None:
                return None, None
            a = dict(study=cfg["study"], version=cfg["version"], epoch=cfg["epoch"], mode=cfg["mode"],
                     salt_sha256=hashlib.sha256(cfg["salt"].encode()).hexdigest(), arm=self._arm(cfg, task),
                     assigned_at=now, randomization_unit="task")
            skus = sorted(shop_stock.SKU.findall(text))
            self._write(dict(event="assigned", ts=now, task=task, **a, added=sorted(extra),
                             sku=skus[0] if len(skus) == 1 else None,
                             input_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest()))
            return a, (extra if a["arm"] in ("B", "adopted") else None)
        except Exception:
            return None, None

    def selected(self, task, a, unit, miss, now=None):
        if not isinstance(a, dict) or "arm" not in a:
            return
        try:
            u = unit if isinstance(unit, dict) else {}
            self._write(dict(event="selected", ts=time.time() if now is None else now, task=task, study=a["study"],
                             version=a["version"], arm=a["arm"], mode=a["mode"], unit=u.get("unit"), sku=u.get("sku"),
                             grade=u.get("grade"), miss=miss if isinstance(miss, str) else None))
        except Exception:
            pass

    def event(self, task, deal, result, price=None, now=None):
        a = deal.get("grade_read") if isinstance(deal, dict) else None
        if not isinstance(a, dict) or "arm" not in a:
            return
        try:
            self._write(dict(event=result, ts=time.time() if now is None else now, task=task, study=a["study"],
                             version=a["version"], arm=a["arm"], mode=a["mode"], price=price, sku=deal.get("id"),
                             unit=deal.get("unit"), kind=deal.get("kind"), rounds=deal.get("rounds"),
                             others=self._others(deal)))
        except Exception:
            pass
