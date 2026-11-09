"""Default-off A/B S-shelf (11/10): the exact food shelf-life question answered with the quoted unit's real remaining
hours instead of the harness.

11/09 23:53 - 11/10 01:48 market-kit.log: 56 seller turns were exactly shelf_life_reply.QUESTION. For the 54 whose unit
is known (shelf_jev_skip events.jsonl), units_in expires_at left at least the hours asked every time; still the harness
answered "I can't confirm the remaining shelf life" (or "unable to verify", "not sure") in 27 of 56. Talks with that
answer were canceled 13 of 27, with another answer 7 of 29 (Fisher p .094, observational: price and "final" wording are
mixed in). review/W-20261109-2040-AX셀러전략/report.md §10-4.

Treatment (arm B): shelf_life_reply.factual_answer's line ("This unit has X hours of shelf life remaining at this
quote. My price: <ask>."), a counter at the current ask; no Jev and no harness for that turn. It answers only when the
unit is food, known, has a finite expiry and its remaining life (rounded down) is at least the hours asked; otherwise
both arms take the original path. Arm A: the original path, unchanged. Every price guard after the turn is unchanged.

Randomization unit: the task, by sha256(salt|study|version|task) against share; the arm is fixed at the first eligible
turn and journaled (event "turn") before a B answer is given, so the analysis is ITT over tasks that reached an eligible
turn; a failed write keeps the original path. Outcome rows (seller.remember: deal, unpaid, canceled, ...) for every
assigned task.

R1 (shelf_jev_skip) protection: while R1's configuration is active this module never answers and never writes, so the
two are never on together (R1's B leaves Jev out of the same turn that S-shelf B would answer by code; with both on,
R1's contrast would exist only inside S-shelf A).

Window: start_at < stop_at and stop_at - start_at <= 20 h; outside it, or absent/invalid/disabled configuration: the
original path. Talks assigned earlier keep their arm for later eligible turns only inside the window, and still get
outcome rows. Switch: MARKET_DIR/shelf_fact/config.json (SHELF_FACT_CONFIG overrides), the shelf_jev_skip format
{study, salt, version, epoch, enabled, mode "ab", share, start_at, stop_at}; mode "adopted" answers every eligible turn.
"""
import hashlib
import os
from pathlib import Path
import time

import shelf_jev_skip
from shelf_life_reply import factual_answer

MAX_WINDOW = 20 * 3600


class Trial(shelf_jev_skip.Trial):
    def __init__(self, root=None, r1=None):
        super().__init__(root)
        self.root = self.root.parent / "shelf_fact"
        self.r1 = r1 if r1 is not None else shelf_jev_skip.Trial(root)

    def _path(self):
        return Path(os.path.abspath(os.environ.get("SHELF_FACT_CONFIG") or self.root / "config.json"))

    @staticmethod
    def _others(deal):
        out = shelf_jev_skip.Trial._others(deal)
        v = deal.get("shelf_jev")
        if isinstance(v, dict):
            out["shelf_jev"] = {f: v[f] for f in shelf_jev_skip.OTHER_FIELDS if isinstance(v.get(f), (str, int, float, bool))}
        return out

    def config(self, now=None):
        cfg = super().config(now)
        if cfg is None or cfg["stop_at"] - cfg["start_at"] > MAX_WINDOW:
            return None
        return cfg

    def answer(self, task, text, deal, now=None):
        """The B line for this turn, or None (the original path); never raises."""
        try:
            now = time.time() if now is None else now
            if not shelf_jev_skip.QUESTION.fullmatch(text.strip() if isinstance(text, str) else ""):
                return None
            cfg = self.config(now)
            if cfg is None or self.r1.config(now) is not None:
                return None
            fact = factual_answer(text, deal, now)
            if fact is None:
                return None
            a = deal.get("shelf_fact")
            if not (isinstance(a, dict) and a.get("study") == cfg["study"] and a.get("version") == cfg["version"]
                    and a.get("mode") == cfg["mode"]):
                a = dict(study=cfg["study"], version=cfg["version"], epoch=cfg["epoch"], mode=cfg["mode"],
                         arm=self._arm(cfg, task), assigned_at=now, randomization_unit="task")
            treated = a["arm"] in ("B", "adopted")
            self._write(dict(event="turn", ts=now, task=task, study=a["study"], version=a["version"],
                             epoch=a["epoch"], mode=a["mode"], arm=a["arm"], assigned_at=a["assigned_at"],
                             randomization_unit="task", answered=treated, remaining_hours=fact["hours"],
                             required_hours=fact["required"], expires_at=deal.get("expires_at"),
                             round=deal.get("rounds"), ask=deal.get("ask"), reserve=deal.get("reserve"),
                             sku=deal.get("id"), unit=deal.get("unit"), kind=deal.get("kind"),
                             input_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                             answer_sha256=hashlib.sha256(fact["message"].encode("utf-8")).hexdigest() if treated else None,
                             others=self._others(deal)))
            deal["shelf_fact"] = a
            return fact["message"] if treated else None
        except Exception:
            return None

    def event(self, task, deal, result, price=None, now=None):
        a = deal.get("shelf_fact") or {}
        if not a:
            return
        try:
            self._write(dict(event=result, ts=time.time() if now is None else now, task=task, study=a["study"],
                             version=a["version"], arm=a["arm"], mode=a["mode"], price=price, sku=deal.get("id"),
                             unit=deal.get("unit"), kind=deal.get("kind"), rounds=deal.get("rounds"),
                             others=self._others(deal)))
        except Exception:
            pass
