"""Snapshot exploratory measurements; no market actions."""
import json,sys,collections,statistics,datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import profit_cohorts as pc
END=1793973617
c=json.load(open('.market/catalog.json')); rr=list({r['seq']:r for r in pc.read_rows('.market/ledger_rows.jsonl') if r.get('ts',0)<=END}.values()); rows=[r for r in rr if r.get('k')=='settle' and END-21600<=r['ts']<=END]
for cat in ['가공/조리 해조류','조리/가공 견과류','소스/양념/조미료']:
 skus={k:v for k,v in c.items() if cat in str(v.get('category',''))}
 for sku,n in collections.Counter(r['sku'] for r in rows if r.get('sku') in skus).most_common(3):
  ss=[r for r in rows if r.get('sku')==sku];v=skus[sku];price=statistics.median(r['amount'] for r in ss);worst=v['list_price']*.75*1.5
  print(json.dumps({'kind':'candidate','sku':sku,'product':v,'paid_6h':n,'buyers':len({r['buyer'] for r in ss}),'median':price,'worst_cost':worst,'scenario_net':price-worst,'seqs':[ss[0]['seq'],ss[-1]['seq']]},ensure_ascii=False))
stock=pc.read_rows('.market/measure/stock.jsonl')
for sku in ['G1515d951','G6b9db161','G8ec51935']:
 for offset in [7200,3600]:
  lo=END-offset;hi=lo+3600;ss=sorted([r for r in stock if r['sku']==sku and r['ts']<=hi],key=lambda r:r['ts']);zero=0
  for i,r in enumerate(ss):
   end=min(hi,ss[i+1]['ts'] if i+1<len(ss) else hi)
   if r['qty']==0:zero+=max(0,end-max(lo,r['ts']))
  print(json.dumps({'kind':'stockout','sku':sku,'start':lo,'minutes':zero/60,'observations':sum(lo<=r['ts']<=hi for r in ss)}))
# Buyer latency and actually paid (not just signed) before/after quote changes.
paid={r.get('order') for r in pc.read_rows('.market/payments.jsonl') if r.get('paid')}
orders=pc.read_rows('.market/measure/orders.jsonl')
for tag,lo,hi in [('baseline',END-7200,END-3600),('recent',END-3600,END)]:
 ss=list({r['order']:r for r in orders if lo<=r.get('ts',0)<=hi}.values());yes=[r for r in ss if r['order'] in paid];sec=[r['seconds']['end'] for r in ss if r.get('seconds',{}).get('end') is not None]
 print(json.dumps({'kind':'buyer','window':tag,'orders':len(ss),'paid':len(yes),'median_seconds':statistics.median(sec) if sec else None,'mean_nominal_saved':statistics.mean(1-r['price']/r['list_price'] for r in yes if r.get('price') and r.get('list_price')) if yes else None,'note':'명목 정가할인, 실제 Saved와 다름'}))
