"""Per-commit buyer outcomes with payment time bounded by the review cutoff."""
import json,statistics,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import profit_cohorts as pc
end=1793973617
seqs={r['seq'] for r in pc.read_rows('.market/ledger_rows.jsonl') if r.get('ts',0)<=end and r.get('k')=='settle'}
paid={r.get('order') for r in pc.read_rows('.market/payments.jsonl') if r.get('paid') and r.get('ledger') in seqs}
orders=list({r['order']:r for r in pc.read_rows('.market/measure/orders.jsonl') if r.get('order')}.values())
def win(lo,hi):
 ss=[r for r in orders if lo<=r.get('ts',0)<hi]; yes=[r for r in ss if r['order'] in paid];seconds=[r['seconds']['end'] for r in ss if r.get('seconds',{}).get('end') is not None];saved=[1-r['price']/r['list_price'] for r in yes if r.get('price') and r.get('list_price')]
 return {'seconds':hi-lo,'orders':len(ss),'paid':len(yes),'median_seconds':statistics.median(seconds) if seconds else None,'nominal_saved':statistics.mean(saved) if saved else None,'note':'명목 할인, 실제 가치·파손 반영 Saved와 다름'}
for l in open('meta/evidence/astra-1106-230016-audit.jsonl'):
 r=json.loads(l)
 if r['kind']=='commit':
  t=r['ts'];print(json.dumps({'sha':r['sha'],'before':win(t-1800,t),'after':win(t,min(t+1800,end))},ensure_ascii=False))
