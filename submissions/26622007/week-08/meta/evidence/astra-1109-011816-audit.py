import json, subprocess, hashlib, collections, datetime, pathlib, sys
sys.path.insert(0,str(pathlib.Path.cwd()))
import market_model
from tools.supply_audit.tool import audit,read_rows
P=pathlib.Path; prefix='meta/evidence/astra-1109-011816'
pack=json.load(open(prefix+'.json')); now=datetime.datetime.fromisoformat(pack['time']).timestamp(); start=datetime.datetime.fromisoformat('2026-11-08T12:33:08+09:00').timestamp()
def read(f):
 out=[]
 for l in open(f):
  try:out.append(json.loads(l))
  except ValueError:pass
 return out
rows={r['seq']:r for r in read('.market/ledger_rows.jsonl') if r.get('seq') and r.get('ts',0)<=now}; rows=list(rows.values())
mine=json.load(open('.market/loss_state.json'))['mine']; neg=json.load(open('memory/seller_memory_r3.json'))['negotiations']; costs={n['unit']:n['cost'] for n in neg if n.get('result')=='deal' and n.get('unit') and isinstance(n.get('cost'),(int,float))}
refund={}
for b,s in market_model.link_broken(rows):
 if b.get('ts',0)-s.get('ts',0)<=3600*(s.get('warranty') or 0):refund[s['seq']]=s.get('amount',0)
settles=[r for r in rows if r.get('k')=='settle']; own=[r for r in settles if str(r['seq']) in mine]
def metrics(a,b):
 ss=[r for r in own if a<=r['ts']<b]; mature=[r for r in ss if r['ts']<=now-21600]; known=[r for r in mature if mine[str(r['seq'])].get('unit') in costs]
 return dict(shop_paid=len(ss),revenue=sum(r.get('amount',0) for r in ss),buyer_paid=sum(a<=r['ts']<b and r.get('buyer')=='nocha12' for r in settles),mature=len(mature),missing_cost=len(mature)-len(known),known_mature_net=sum(r.get('amount',0)-costs[mine[str(r['seq'])]['unit']]-refund.get(r['seq'],0) for r in known),market_paid=sum(a<=r['ts']<b for r in settles))
auditfile=open(prefix+'-audit.jsonl','w')
def emit(kind,**data):
 auditfile.write(json.dumps(dict(kind=kind,**data),ensure_ascii=False)+'\n');auditfile.flush()
emit('interval',start=start,end=now,metrics=metrics(start,now),race=pack['board']['race'])
commits=[]
raw=subprocess.check_output(['git','log','--since=2026-11-08 12:33:08 +0900','--format=%H%x09%ct','--','.'],text=True)
for line in raw.splitlines():
 sha,ts=line.split('\t'); ts=int(ts)
 body=subprocess.check_output(['git','show','-s','--format=%B',sha],text=True).strip()
 files=subprocess.check_output(['git','diff-tree','--no-commit-id','--name-only','-r',sha,'--','.'],text=True).splitlines()
 r=dict(commit=sha[:8],time=ts,message=body,files=files,before=metrics(ts-1800,ts),after=metrics(ts,min(now,ts+1800)),after_hours=max(0,min(now-ts,1800))/3600)
 commits.append(r);emit('commit',**r)
for name in ['nocha12','yms979','inextro','moss-and-oak','25620027','seochanit']:
 ss=[r for r in settles if start<=r['ts']<=now and (r.get('seller')==name or (name=='nocha12' and str(r['seq']) in mine))]
 sku=collections.defaultdict(lambda:dict(paid=0,revenue=0,buyers=set(),prices=[]))
 for r in ss:
  v=sku[r.get('sku')];v['paid']+=1;v['revenue']+=r.get('amount',0);v['buyers'].add(r.get('buyer'));v['prices'].append(r.get('amount',0))
 out=[]
 for k,v in sorted(sku.items(),key=lambda kv:-kv[1]['revenue'])[:8]:out.append(dict(sku=k,paid=v['paid'],revenue=v['revenue'],buyers=len(v['buyers']),price_min=min(v['prices']),price_max=max(v['prices'])))
 emit('rival',name=name,paid=len(ss),revenue=sum(r.get('amount',0) for r in ss),top=out,margin='원가 미공개; 마진 미식별')
win,inc,out=[read_rows('.market/measure/'+n+'.jsonl') for n in ['supply_windows','units_in','units_out']]
cfg=json.load(open('pricing.json'))
for sku,target in cfg['restock'].get('sku_target_units',{}).items():
 emit('supply',target=target,result=audit(win,inc,out,now,sku),cohort=pack['profit_cohorts']['skus'].get(sku,{}).get('mature_6h'))
for f in ['.market/decisions.jsonl','.market/tool_calls.jsonl']:
 rs=read(f);recent=[r for r in rs if start<=r.get('ts',0)<=now]
 if 'decisions' in f:
  for r in recent:
   if r.get('agent') in ('restock','forum'):emit('decision',source=f,row=r)
 else:
  for tool in sorted(set(r.get('tool','') for r in rs)):
   a=[r for r in recent if r.get('tool')==tool];emit('tool',tool=tool,calls=len(a),failures=sum(not r.get('ok') for r in a),available=tool in pack['shop']['tools_status']['available'])
forum=json.load(open('.market/forum.json')); posts=forum if isinstance(forum,list) else forum.get('posts',[])
for p in posts:
 if any(w in p.get('text','') for w in ['구합니다','구매','찾습니다']):emit('demand_post',post=p)
# Read every pack-listed log, retaining inventory and content hashes, never dumping secret-bearing raw text.
with open(prefix+'-sources.jsonl','w') as f:
 for name in pack['files']:
  if not name.startswith('logs/'):continue
  p=P(name)
  if p.is_file():
   h=hashlib.sha256();lines=0
   with p.open('rb') as src:
    for l in src:h.update(l);lines+=1
   f.write(json.dumps(dict(path=name,lines=lines,sha256=h.hexdigest()))+'\n')
json.dump(commits,open(prefix+'-commits.json','w'),ensure_ascii=False,indent=2)
print('commits',len(commits),'metrics',metrics(start,now))
