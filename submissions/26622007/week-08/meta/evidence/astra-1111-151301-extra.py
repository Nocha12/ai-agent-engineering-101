import json,datetime,collections,pathlib,sys,gzip,hashlib
sys.path.insert(0,str(pathlib.Path.cwd()))
import strategy_screen,patch_notes,scorecard
from tools.review_accounts.tool import summarize
P='meta/evidence/astra-1111-151301'
p=json.load(open(P+'.json'));now=datetime.datetime.fromisoformat(p['time']).timestamp()
def write(s,rs):
 with open(P+s,'w') as f:
  for r in rs:f.write(json.dumps(r,ensure_ascii=False)+'\n')
rows=[];all_rows=[];seen=set();url_names=collections.defaultdict(set)
shops=json.load(open(P+'-public-shops.json'))['shops']
for s in shops:url_names[s['url']].add(s['name'])
for old in pathlib.Path('meta/evidence').glob('*public-shops.json'):
 try:
  for s in json.load(open(old)).get('shops',[]):url_names[s['url']].add(s['name'])
 except (ValueError,AttributeError):pass
for line in open('.market/ledger_rows.jsonl'):
 try:r=json.loads(line)
 except:continue
 if r.get('ts',0)>now:continue
 if r.get('k')=='settle' and r.get('seq') not in seen:
  seen.add(r.get('seq'));all_rows.append(r)
  if r.get('ts',0)>=now-43200:rows.append(r)
cat=json.load(open('.market/catalog.json'));inv=json.load(open('.market/inventory.json'));cfg=json.load(open('pricing.json'))
# Use pack snapshot surge, not a later inventory's factor, where available.
inv['surge']=[{'sku':r['sku'],'factor':r['surge']} for r in p['market_opportunities']['candidates']]
write('-screen.jsonl',strategy_screen.screen(all_rows,cat,inv,cfg,now,limit=20)['candidates'])
mine=json.load(open('.market/loss_state.json'))['mine'];reviews=[]
for ln,line in enumerate(open('.market/ledger_reviews.jsonl'),1):
 r=json.loads(line)
 if r.get('ts',0)<=now:reviews.append(r)
a=summarize(reviews,all_rows,mine)
write('-reviews.jsonl',[a])
write('-rules.jsonl',[patch_notes.parse(open(P+'-rules.html').read()),{'mismatch':patch_notes.compare(patch_notes.parse(open(P+'-rules.html').read())['scoring'],scorecard.OBJECTIVE)}])
names=['yms979','inextro','25620027','moss-and-oak','seochanit']
rs=[]
for name in names:
 rr=[r for r in rows if (r.get('seller') or '').lower()==name.lower() or name in url_names[r.get('shop')]]
 g=collections.defaultdict(list)
 for r in rr:g[r.get('sku')].append(r)
 rs.append(dict(shop=name,paid=len(rr),revenue=sum(r['amount'] for r in rr),dear_revenue=sum(r['amount'] for r in rr if cat.get(r.get('sku'),{}).get('list_price',0)>=600000),buyers=len({r.get('buyer') for r in rr}),margin=None,coverage='name or historical public URL; incomplete aliases remain a lower bound',top=[dict(sku=k,paid=len(v),buyers=len({r.get('buyer') for r in v}),revenue=sum(r['amount'] for r in v),prices=[min(r['amount'] for r in v),max(r['amount'] for r in v)]) for k,v in sorted(g.items(),key=lambda kv:-sum(r['amount'] for r in kv[1]))[:6]]))
write('-rivals-v3.jsonl',rs)
metrics=[]
for i,line in enumerate(open(P+'-commits.jsonl'),1):
 c=json.loads(line);t=c['ts']; before=[r for r in rows if t-1800<=r['ts']<t];after=[r for r in rows if t<=r['ts']<t+1800]
 metrics.append(dict(commit=c['commit'][:8],market_paid_before=len(before),market_paid_after=len(after),note='12h cache window lower bound; oldest commit before-window can be clipped'))
write('-market-metrics.jsonl',metrics)
# Read compressed logs as text too; earlier hash audit retained raw compression bytes.
audit=[]
for name in p['files']:
 if not name.startswith('logs/') or not name.endswith('.gz'):continue
 counts=collections.Counter();n=0
 with gzip.open(name,'rt',errors='replace') as f:
  for n,line in enumerate(f,1):
   for term in ['stock cost','units per SKU','no stock','Traceback','settled','not paying']:
    if term in line:counts[term]+=1
 audit.append(dict(path=name,decoded_lines=n,hits=dict(counts)))
write('-compressed-log-audit.jsonl',audit)
print(json.dumps({'reviews':a,'rivals':[{k:r[k] for k in ('shop','paid','revenue')} for r in rs]},ensure_ascii=False))
