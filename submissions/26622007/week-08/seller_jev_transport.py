"""Default-off isolated HTTP connection reuse trial. No reader/price policy change."""
import atexit
import time
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

    def reader(self,deal):
        a=deal.get("jev_transport") or {}
        if a.get("id") and self.arm(a)=="B":
            return lambda text:fast_classify.jev_offer(text,post=self.pool.post)
        return fast_classify.jev_offer

    def event(self,task,deal,event,price=None,now=None,**fields):
        super().event(task,dict(deal,opening_cue=deal.get("jev_transport") or {}),event,price,now,**fields)
