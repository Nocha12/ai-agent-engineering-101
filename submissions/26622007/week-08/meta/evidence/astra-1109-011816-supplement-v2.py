import json,collections,pathlib,datetime
P=pathlib.Path;prefix='meta/evidence/astra-1109-011816';pack=json.load(open(prefix+'.json'));now=datetime.datetime.fromisoformat(pack['time']).timestamp();start=1794108788
rows=[]; names={'https://ax-week07.treasuresky.xyz':'25620027'}
for l in open('.market/ledger_rows.jsonl'):
 r=json.loads(l)
 if r.get('ts',0)>now:continue
 rows.append(r)
 if r.get('seller') and r.get('shop'):names[r['shop']]={"YMS's Market":'yms979','Inextro Market':'inextro','Seochanit Shop':'seochanit'}.get(r['seller'],r['seller'])
cat=json.load(open('.market/catalog.json'));seen=set();ss=[]
for r in rows:
 if r.get('k')=='settle' and start<=r['ts']<=now and r['seq'] not in seen:seen.add(r['seq']);ss.append(r)
f=open(prefix+'-supplement-v2.jsonl','w')
def emit(**r):f.write(json.dumps(r,ensure_ascii=False)+'\n');f.flush()
emit(kind='correction',reason='초기 감사 73~77행의 경쟁점 0건은 이름 없는 settle의 URL 연결 누락이다. 아래 result행 seller→shop URL로 재연결한 집계로 대체한다.')
for name in ['yms979','inextro','moss-and-oak','25620027','seochanit']:
 own=[r for r in ss if names.get(r.get('shop'))==name or (name=='25620027' and r.get('shop')=='https://ax-week07.treasuresky.xyz')];groups=collections.defaultdict(list)
 for r in own:groups[r.get('sku')].append(r)
 top=[]
 for sku,rs in sorted(groups.items(),key=lambda x:-sum(r.get('amount',0) for r in x[1]))[:6]:
  top.append(dict(sku=sku,paid=len(rs),buyers=dict(collections.Counter(r.get('buyer') for r in rs).most_common(4)),revenue=sum(r.get('amount',0) for r in rs),price_min=min(r.get('amount',0) for r in rs),price_max=max(r.get('amount',0) for r in rs)))
 emit(kind='rival',name=name,paid=len(own),revenue=sum(r.get('amount',0) for r in own),dear_revenue=sum(r.get('amount',0) for r in own if cat.get(r.get('sku'),{}).get('list_price',0)>=600000),top=top,margin='원가 미공개; 실제 마진 미식별')
# Capture current switch without changing owner choices.
emit(kind='switch',path='.market/buyer_rank_pg.json',switch=json.load(open('.market/buyer_rank_pg.json')))
orders=[]
for l in open('.market/measure/orders.jsonl'):
 r=json.loads(l)
 if start<=r.get('ts',0)<=now:orders.append(r)
emit(kind='orders',n=len(orders),keys=list(orders[-1]) if orders else [],nofit_arm_mentions=sum('nofit_arm' in json.dumps(r) for r in orders))
for l in open('.market/measure/units_in.jsonl'):
 r=json.loads(l)
 if r.get('sku')=='Gef4d3036' and now-21600<=r.get('ts',0)<=now and r.get('cost',0)>r.get('list_price',1)*.9:
  sales=[s for s in ss if s.get('unit')==r.get('unit')];emit(kind='surge_unit',unit=r,sales=sales)
# Preserve stable guard excerpts instead of citing rotating logs.
counts=collections.Counter();examples=[]
for name in ['logs/market-kit.log.1','logs/market-kit.log']:
 with open(name,errors='replace') as h:
  for i,line in enumerate(h,1):
   if 'guard:' in line and any(x in line for x in ['stock cost','units per SKU','cash']):
    for k in ['stock cost','units per SKU','cash']:
     if k in line:counts[k]+=1
    if len(examples)<5:examples.append({'source':name+':'+str(i),'line':line.strip()})
emit(kind='guard_observed_logs',counts=dict(counts),examples=examples,basis='로그 보존 구간 전체; 실제 두 공급창 구속 수와 같지 않음')
print('supplement v2 ready')
