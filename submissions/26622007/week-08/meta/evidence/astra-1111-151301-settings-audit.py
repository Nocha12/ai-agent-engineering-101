import subprocess,json,pathlib,collections,datetime
P='meta/evidence/astra-1111-151301';p=json.load(open(P+'.json'));now=datetime.datetime.fromisoformat(p['time']).timestamp();start=datetime.datetime.fromisoformat('2026-11-11T02:57:00+09:00').timestamp()
with open(P+'-settings.jsonl','w') as f:
 for c in map(json.loads,open(P+'-commits.jsonl')):
  d=subprocess.check_output(['git','show','--format=','--no-ext-diff',c['commit'],'--','pricing.json','meta/terra.md','meta/strategy.md','restock.py','forum.py'],text=True)
  f.write(json.dumps({'commit':c['commit'][:8],'diff':d},ensure_ascii=False)+'\n')
with open(P+'-payments-decisions.jsonl','w') as f:
 for name in ['.market/payments.jsonl','.market/decisions.jsonl']:
  count=0;by=collections.Counter();first=None;last=None
  for ln,line in enumerate(open(name),1):
   try:r=json.loads(line)
   except:continue
   if start<=r.get('ts',0)<=now:
    count+=1;by[str(r.get('agent',r.get('status',r.get('ok','unknown'))))]+=1
    first=first or ln;last=ln
  f.write(json.dumps({'path':name,'records':count,'by':dict(by),'first_line':first,'last_line':last},ensure_ascii=False)+'\n')
print('settings, prompts and payments audited')
