# parent probe audit-20261109-2155/floor-asof-probe.py (sha256 ed2a277af568e034), only the tool path changed to this folder
import json,sys
from pathlib import Path
p=Path(__file__).resolve().parent
sys.path.insert(0,str(p))
import floor_margin_verdict as v
import test_floor_margin_verdict as t

m,l,u=t.real()
missing=[r for r in l if r.get('data',{}).get('order')!='o-a7a58438eaeb']
asof=t.S2_TS+t.H6+1
a=v.classify(m,missing,u,study='fm-t',asof=asof,ledger_end=asof)
r=next(x for x in a['rows'] if x['task']==t.T1)
future=v.classify(m,l,u,study='fm-t',asof=t.S1_TS-1,ledger_end=asof)
f=next(x for x in future['rows'] if x['task']==t.T1)
facts={'missing_payment_after_3h':{'actual_status':r['status'],'expected_status':'unknown','excluded':v.excluded_why(r)},'asof_before_settlement':{'actual_status':f['status'],'future_settle_is_read':f['settle_ts'] is not None and f['settle_ts']>future['asof'],'settle_minus_asof':f['settle_ts']-future['asof'] if f['settle_ts'] is not None else None}}
print(json.dumps(facts))
assert r['status']=='unknown' and f['status']!='paid', 'remaining uncertainty/asof defects reproduced'
