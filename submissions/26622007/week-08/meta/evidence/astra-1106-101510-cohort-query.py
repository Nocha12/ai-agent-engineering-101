import datetime as dt,json,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
import profit_cohorts as pc,market_model
end=1793927710;start=1793907582.902688
rr=list({r['seq']:r for r in pc.read_rows('.market/ledger_rows.jsonl') if r.get('ts',0)<=end}.values())
mine=pc.read_json('.market/loss_state.json',{})['mine'];mem=pc.read_json('memory/seller_memory_r3.json',{})['negotiations']
cost={r['unit']:r['cost'] for r in mem if r.get('result')=='deal' and r.get('unit') and isinstance(r.get('cost'),(int,float))}
refund={s['seq']:s['amount'] for b,s in market_model.link_broken(rr) if str(s['seq']) in mine and (not b.get('order') or b['order']==s.get('order')) and 0<=b['ts']-s['ts']<=s.get('warranty',0)*3600}
sales=[r for r in rr if r.get('k')=='settle' and str(r['seq']) in mine]
refs={r['seq']:f'.market/ledger_rows.jsonl:{i}' for i,r in enumerate(pc.read_rows('.market/ledger_rows.jsonl'),1)}
def summary(rs):
 return {'paid':len(rs),'known_net':sum(r['amount']-cost[mine[str(r['seq'])]['unit']]-refund.get(r['seq'],0) for r in rs if mine[str(r['seq'])]['unit'] in cost),'missing_cost':sum(mine[str(r['seq'])]['unit'] not in cost for r in rs),'refunds':sum(refund.get(r['seq'],0) for r in rs),'refs':[refs[r['seq']] for r in rs]}
for sku in ['G03d0199f','G00327e80','G05277ffe']:
 rs=sorted([r for r in sales if r['sku']==sku and r['ts']<=end-21600],key=lambda r:r['ts'])[-10:]
 print(json.dumps({'sku':sku,'last10_mature':summary(rs)},ensure_ascii=False))
pre=sorted([r for r in sales if r['sku']=='G6b9db161' and r.get('grade')=='A' and r['ts']<start and r['ts']<=end-21600],key=lambda r:r['ts'])[-20:]
post=[r for r in sales if r['sku']=='G6b9db161' and r.get('grade')=='A' and start<=r['ts']<=end]
print(json.dumps({'G6_A':{'before20':summary(pre),'after':summary(post),'after_mature':summary([r for r in post if r['ts']<=end-21600]),'first_pre_sale':pre[0]['ts'],'start':start,'cutoff':end,'note':'after net is provisional, same-grade inventory exposure still required at deadline'}},ensure_ascii=False))
# Grade A goods keep their grade. Exposure is union-of-time with >=1 observed A unit,
# not sum of unit-hours; first_tick cannot establish availability before it was observed.
inc={r['unit']:r for r in pc.read_rows('.market/measure/units_in.jsonl') if r.get('ts',end+1)<=end}
out={}
for r in pc.read_rows('.market/measure/units_out.jsonl'):
 if r.get('ts',end+1)<=end:out[r['unit']]=min(out.get(r['unit'],end),r['ts'])
for r in sales:
 u=mine[str(r['seq'])].get('unit')
 if u:out[u]=min(out.get(u,end),r['ts'])
def exposure(lo,hi):
 intervals=[]
 for u,r in inc.items():
  if r.get('sku')!='G6b9db161' or r.get('grade')!='A':continue
  a=max(lo,r['ts'] if r.get('first_tick') else r.get('supplied_at',r['ts']));b=min(hi,out.get(u,hi))
  if b>a:intervals.append((a,b))
 merged=[]
 for a,b in sorted(intervals):
  if merged and a<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],b)
  else:merged.append([a,b])
 return {'lo':lo,'hi':hi,'available_hours':sum(b-a for a,b in merged)/3600,'intervals':len(merged)}
before=exposure(pre[0]['ts'],start);after=exposure(start,end)
print(json.dumps({'kind':'G6_grade_A_exposure','before':before,'after':after,'before_known_net_per_available_hour':summary(pre)['known_net']/before['available_hours'] if before['available_hours'] else None,'after_provisional_net_per_available_hour':summary(post)['known_net']/after['available_hours'] if after['available_hours'] else None,'after_mature_paid':0,'basis':'union of incoming A intervals, ends at earliest paid/departure; before interval starts at earliest of the20 pre-sales, after ends fixed cutoff; provisional outcome is not a win'},ensure_ascii=False))
