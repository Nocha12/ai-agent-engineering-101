"""Default-off isolated HTTP connection reuse trial. No reader/price policy change."""
import atexit
import math
import re
import time
import httpx
import fast_classify
from jev_connection_pool import JevConnectionPool
from seller_opening_cue import OpeningCue

class JevTransport(OpeningCue):
    def __init__(self,root=None):
        super().__init__(root)
        self.root=self.root.parent/"jev_transport"
        self.pool=JevConnectionPool()
        atexit.register(self.pool.close)

    def quote(self,task,deal,now=None):
        if "jev_transport" in deal or deal.get("rounds",0):return
        if any((deal.get(k) or {}).get("id") for k in ("reader_trial","concession_trial")):
            deal["jev_transport"]={"id":None,"arm":"A"};return
        now=time.time() if now is None else now
        a=self._assign(task,deal,now)
        if a.get("id") and any(a.get(k)!=v for k,v in self._snapshot(deal).items()):
            a=dict(a,contaminated=True);self.invalid("jev_transport_snapshot")
        deal["jev_transport"]=a
        self.event(task,deal,"quote",deal["ask"],now)

    @staticmethod
    def trial_config(trial_id, start, end, seed):
        """Build a finite trial config; never write or extend an existing epoch."""
        if not isinstance(trial_id,str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}",trial_id):
            raise ValueError("invalid trial id")
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (start,end)):
            raise ValueError("finite timestamps required")
        if not 0 < end-start <= 1800 or type(seed) is not int:
            raise ValueError("invalid trial window or seed")
        return dict(enabled=True,id=trial_id,start=start,end=end,seed=seed,stop=False)

    def _treatment_active(self,deal):
        # ITT enrollment stays immutable; only actual transport exposure expires.
        try:
            a=deal.get("jev_transport") or {}
            if not a.get("id") or self.arm(a)!="B" or (self.root/"disabled").exists():
                return False
            c=self.config()
            now=time.time()
            return bool(c and not c.get("stop") and c.get("id")==a["id"]
                        and math.isfinite(now) and c["start"] <= now < c["end"])
        except Exception:
            return False

    def reader(self,deal):
        def post(*args,**kwargs):
            # Recheck at dispatch too: classification may outlive reader creation.
            send=self.pool.post if self._treatment_active(deal) else httpx.post
            return send(*args,**kwargs)
        def read(text):
            if self._treatment_active(deal):
                return fast_classify.jev_offer(text,post=post)
            return fast_classify.jev_offer(text)
        return read

    def event(self,task,deal,event,price=None,now=None,**fields):
        super().event(task,dict(deal,opening_cue=deal.get("jev_transport") or {}),event,price,now,**fields)
