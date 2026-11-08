"""Cutoff cohorts, supply identities, and source-line checks for this review."""
import datetime as dt, json, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import market_model, profit_cohorts, patch_notes
from tools.supply_audit.tool import audit, read_rows
p=json.load(open('meta/evidence/astra-1108-123308.json'))
end=dt.datetime.fromisoformat(p['time']).timestamp()
rows=[r for r in profit_cohorts.read_rows('.market/ledger_rows.jsonl') if r.get('ts',0)<=end]
mine=json.load(open('.market/loss_state.json'))['mine']
mem=json.load(open('memory/seller_memory_r3.json'))['negotiations']
costs={n['unit']:n['cost'] for n in mem if n.get('result')=='deal' and n.get('unit') and isinstance(n.get('cost'),(int,float))}
orders={r.get('order') for r in rows if r.get('k')=='settle'}
eligible=[r for r in rows if r.get('k')!='broken' or not r.get('order') or r['order'] in orders]
refunds={sale['seq']:sale['amount'] for broken,sale in market_model.link_broken(eligible)
         if str(sale['seq']) in mine and 0<=broken['ts']-sale['ts']<=(sale.get('warranty') or 0)*3600}

def emit(**r): print(json.dumps(r,ensure_ascii=False))
for sku,stamp in [('G2698c6f8','2026-11-07T16:16:52+09:00'),('G3e0d6c67','2026-11-07T17:29:14+09:00'),('G19a70f63','2026-11-07T19:53:05+09:00')]:
 t=dt.datetime.fromisoformat(stamp).timestamp()
 # Same sale-age maturity cutoff; 'before' limited to six hours, after excludes still-open sales.
 for label,lo,hi in [('before',t-21600,t),('after',t,end-21600)]:
  sales=[r for r in rows if r.get('k')=='settle' and r.get('sku')==sku and str(r['seq']) in mine and lo<=r.get('ts',0)<hi]
  known=[r for r in sales if mine[str(r['seq'])].get('unit') in costs]
  net=sum(r['amount']-costs[mine[str(r['seq'])]['unit']]-refunds.get(r['seq'],0) for r in known)
  c=dict(paid=len(sales),missing_cost=len(sales)-len(known),revenue=sum(r['amount'] for r in sales),refunds=sum(refunds.get(r['seq'],0) for r in sales),known_net=net,net_per_sale=net/len(sales) if sales and len(known)==len(sales) else None)
  emit(kind='sku_cohort',sku=sku,side=label,start=lo,end=hi,hours=(hi-lo)/3600,**c)
windows,incoming,outgoing=[read_rows('.market/measure/'+n+'.jsonl') for n in ('supply_windows','units_in','units_out')]
for sku in ['G2698c6f8','G3e0d6c67','G19a70f63','G1515d951','G147ae4e7','G6b0fd5bc','G00327e80']:
 emit(kind='supply',sku=sku,result=audit(windows,incoming,outgoing,end,sku),mature=p['profit_cohorts']['skus'].get(sku,{}).get('mature_6h'))
emit(kind='rules',result=patch_notes.check(page=Path('meta/evidence/astra-1108-123308-rules.html').read_text(),record=False))
