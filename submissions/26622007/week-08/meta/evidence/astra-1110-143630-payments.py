import json,collections
from pathlib import Path
start=1794244572;end=1794289003
rows={r['seq']:r for r in map(json.loads,open('.market/ledger_rows.jsonl')) if r.get('k')=='settle' and start<=r.get('ts',0)<=end}
paid=0;refused=collections.Counter();no_time=0
for r in map(json.loads,open('.market/payments.jsonl')):
 if r.get('paid') and r.get('ledger') in rows:paid+=1
 elif not r.get('paid'):refused[str(r.get('reason',''))[:160]]+=1;no_time+=1
r={'kind':'payments_join','paid_in_cutoff_by_ledger_seq':paid,'refused_without_timestamp_all_history':no_time,'refusal_reasons_all_history':refused.most_common(8),'limitation':'payments has no ts; refusal timing cannot be inferred from file order, use kit logs for timed refusals; previous empty window counts meant missing timestamps, not zero payments'}
Path('meta/evidence/astra-1110-143630-payments.jsonl').write_text(json.dumps(r,ensure_ascii=False)+'\n')
print(paid,no_time)
