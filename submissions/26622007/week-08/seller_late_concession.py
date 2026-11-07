"""Isolated late-round experiment. Opening/warranty/reserve are never changed.
Trial config/SQLite/trace live below MARKET_DIR/late_concession; no old cohort writes.
Adoption/rollback flags affect NEW tasks only. Existing assignments remain sticky.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import time
from seller_concession_trial import Trial

class LateConcession(Trial):
    def __init__(self, root=None):
        base=Path(root) if root is not None else Path(os.environ.get("MARKET_DIR") or Path(__file__).parent/".market")
        super().__init__(base/"late_concession")

    def quote(self, task, deal, now=None):
        if "late_concession" in deal or deal.get("rounds",0):
            return
        # Historical opening trials must remain isolated even if replayed.
        if (deal.get("concession_trial") or {}).get("id"):
            deal["late_concession"]={"arm":"A","id":None}
            return
        now=time.time() if now is None else now
        if (self.root/"adopted").exists() and not (self.root/"disabled").exists():
            try:
                self.root.mkdir(parents=True,exist_ok=True)
                with closing(sqlite3.connect(self.root/"seller_concession_trial.sqlite3",timeout=10)) as db,db:
                    db.execute("CREATE TABLE IF NOT EXISTS assignments (task TEXT PRIMARY KEY, data TEXT NOT NULL)")
                    db.execute("BEGIN IMMEDIATE")
                    row=db.execute("SELECT data FROM assignments WHERE task=?",(task,)).fetchone()
                    if row:
                        a=json.loads(row[0])
                    else:
                        a=dict(arm="B",id="adopted-v1",stratum="adopted",enrolled_at=now,
                               unit=deal.get("unit"),sku=deal.get("id"),kind=deal.get("kind"),
                               cost=deal.get("cost"),reserve=deal["reserve"],open=deal["open"],
                               policy_sha=deal.get("reader_trial_policy_sha"))
                        db.execute("INSERT INTO assignments VALUES (?,?)",(task,json.dumps(a,allow_nan=False)))
            except Exception:
                self.invalid("adoption_assignment")
                a={"arm":"A","id":None}
        else:
            a=self.assign(task,deal,now)
        if a.get("id") and any(a.get(k)!=deal.get(v) for k,v in
                (("unit","unit"),("sku","id"),("reserve","reserve"),("open","open"),("cost","cost"))):
            a=dict(a,contaminated=True)
            self.invalid("snapshot_mismatch")
        deal["late_concession"]=a
        self.event(task,deal,"quote",deal["ask"],now)

    def arm(self, assignment):
        # Never retrospectively change a current negotiation after enrollment.
        return "A" if assignment.get("contaminated") else assignment.get("arm","A")

    def floor(self, deal, baseline):
        a=deal.get("late_concession") or {}
        if a.get("id") and self.arm(a)=="B" and deal.get("rounds",0)>=3:
            return max(deal["reserve"],min(deal["ask"],round(deal["reserve"]+(baseline-deal["reserve"])*.9)))
        return baseline

    def event(self, task, deal, event, price=None, now=None, **fields):
        clone=dict(deal,concession_trial=deal.get("late_concession") or {})
        super().event(task,clone,event,price,now,**fields)
