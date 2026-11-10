import sys,json,collections,datetime as dt
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import patch_notes,scorecard
BASE='meta/evidence/astra-1110-143630'
p=json.load(open(BASE+'.json'));end=dt.datetime.fromisoformat(p['time']).timestamp();start=1794244572
out=[]
parsed=patch_notes.parse(Path(BASE+'-rules.html').read_text())
out.append({'kind':'rules','latest':parsed['patches'][0]['id'],'patches':parsed['patches'],'mismatch':patch_notes.compare(parsed['scoring'],scorecard.OBJECTIVE),'pack_recent':p['rules']['recent']})
for name in ['.market/payments.jsonl','.market/decisions.jsonl']:
 counts=collections.Counter();sample=[];total=0
 for i,l in enumerate(open(name),1):
  r=json.loads(l);total+=1;t=r.get('ts',0)
  if not isinstance(t,(int,float)) or not start<=t<=end:continue
  if name.endswith('payments.jsonl'):
   counts['paid' if r.get('paid') else 'refused']+=1
   if not r.get('paid') and len(sample)<8:sample.append({'ref':f'{name}:{i}','reason':str(r.get('reason'))[:240]})
  else:
   counts[r.get('agent','unknown')]+=1
   if r.get('agent')=='restock':
    sample.append({'ref':f'{name}:{i}','reason':r.get('reason'),'final':r.get('final')})
 out.append({'kind':'own_records','file':name,'total_read':total,'window_counts':dict(counts),'samples':sample[-8:]})
Path(BASE+'-extra.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in out))
print([(x['kind'],x.get('window_counts')) for x in out])
