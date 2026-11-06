"""Fixed-cutoff audit; operational sources are read-only, stdout is a new evidence artifact."""
import collections, datetime as dt, gzip, hashlib, json, subprocess, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import profit_cohorts as pc, market_model, scale_measure
ROOT=Path(__file__).resolve().parents[1]
START=dt.datetime.fromisoformat('2026-11-05T21:49:05+09:00').timestamp()
END=dt.datetime.fromisoformat('2026-11-06T10:15:10+09:00').timestamp()
def emit(kind,**kw):print(json.dumps(dict(kind=kind,**kw),ensure_ascii=False))
def read(p):return pc.read_json(ROOT/p,{})
def rows(p):return pc.read_rows(ROOT/p)
def main():
 pack=read('meta/evidence/astra-1106-101510.json')
 logs=[]
 for p in pack['files']:
  if p.startswith('logs/') and (ROOT/p).is_file():
   b=(ROOT/p).read_bytes(); content=gzip.decompress(b) if p.endswith('.gz') else b
   emit('log_read',path=p,bytes=len(b),lines=len(content.splitlines()),sha256=hashlib.sha256(b).hexdigest())
   if 'market-kit' in p or '/kit-parts/' in p:logs.extend((p,i,l) for i,l in enumerate(content.decode(errors='replace').splitlines(),1) if '[restock] guard:' in l and 'stock cost' in l)
 rr=list({r['seq']:r for r in rows('.market/ledger_rows.jsonl') if r.get('ts',0)<=END}.values())
 mine=read('.market/loss_state.json')['mine'];mem=read('memory/seller_memory_r3.json')['negotiations']
 costs={r['unit']:r['cost'] for r in mem if r.get('result')=='deal' and r.get('unit') and isinstance(r.get('cost'),(int,float))}
 refs={r['seq']:f'.market/ledger_rows.jsonl:{i}' for i,r in enumerate(rows('.market/ledger_rows.jsonl'),1)}
 own=[r for r in rr if r.get('k')=='settle' and str(r['seq']) in mine]
 refunds={s['seq']:s['amount'] for b,s in market_model.link_broken(rr) if str(s['seq']) in mine and 0<=b['ts']-s['ts']<=s.get('warranty',0)*3600 and (not b.get('order') or b['order']==s.get('order'))}
 def win(lo,hi):
  ss=[r for r in own if lo<=r['ts']<hi];known=[r for r in ss if mine[str(r['seq'])].get('unit') in costs];mature=[r for r in known if r['ts']<=END-21600]
  return dict(paid=len(ss),revenue=sum(r['amount'] for r in ss),net_after_linked_refunds=sum(r['amount']-costs[mine[str(r['seq'])]['unit']]-refunds.get(r['seq'],0) for r in known),mature_paid=len(mature),mature_net=sum(r['amount']-costs[mine[str(r['seq'])]['unit']]-refunds.get(r['seq'],0) for r in mature),missing_cost=len(ss)-len(known),open_warranty=sum(r['ts']>END-21600 for r in ss),market_paid=sum(r.get('k')=='settle' and lo<=r['ts']<hi for r in rr),refs=[refs[r['seq']] for r in ss[:1]+ss[-1:]])
 emit('cycle',start=START,end=END,before=win(START-(END-START),START),after=win(START,END),net_basis='paid minus known unit cost minus order-linked warranty refunds; storage excluded; open warranties provisional')
 git=subprocess.check_output(['git','log','--reverse','--since=2026-11-05T21:49:05+09:00','--format=%h%x09%ct%x09%s','--','.'],text=True)
 for l in git.splitlines():
  sha,t,title=l.split('\t',2);t=int(t)
  if t>END:continue
  files=subprocess.check_output(['git','show','--format=','--name-only',sha,'--','.'],text=True).splitlines()
  emit('commit',sha=sha,ts=t,title=title,files=files,before=win(t-1800,t),after=win(t,min(t+1800,END)))
 for i,g in enumerate(rows('meta/grades.jsonl'),1):
  t=dt.datetime.fromisoformat(g['time']).timestamp()
  if not START<=t<=END:continue
  emit('review',cycle=g['cycle'],status=g['status'],source=f'meta/grades.jsonl:{i}',check=g.get('strategy_check'),gap=g.get('evidence_gap'),before=win(t-3600,t),after=win(t,min(t+3600,END)))
 for label,start in [('review',START),('price_trial',1793907582.902688),('scale_start',dt.datetime.fromisoformat('2026-11-05T11:56:19+09:00').timestamp())]:
  c=pc.summarize(rr,mine,mem,rows('memory/agent/losses.jsonl'),END,start)
  for sku in read('pricing.json')['restock']['sku_target_units']:emit('cohort',label=label,sku=sku,data=c['skus'].get(sku))
 emit('scale',data=scale_measure.build(str(ROOT),now=END))
 for h in range(12):emit('hour',start=END-(12-h)*3600,**win(END-(12-h)*3600,END-(11-h)*3600))
 names={r['shop']:r['seller'] for r in rr if r.get('seller') and r.get('shop')}
 for rival in pack['board']['rivals']['top']:
  urls={u for u,n in names.items() if rival['name'].lower() in n.lower()}
  ss=[r for r in rr if r.get('k')=='settle' and (r.get('shop') in urls or r.get('seller','').lower()==rival['name'].lower()) and START<=r['ts']<=END]
  emit('rival',name=rival['name'],mapped=bool(urls),paid=len(ss) if urls else None,buyers=len({r.get('buyer') for r in ss}) if urls else None,revenue=sum(r['amount'] for r in ss),top=[dict(sku=k,paid=v,min=min(r['amount'] for r in ss if r['sku']==k),max=max(r['amount'] for r in ss if r['sku']==k)) for k,v in collections.Counter(r['sku'] for r in ss).most_common(5)],margin=None,refs=[refs[r['seq']] for r in ss[:2]])
 tc=rows('.market/tool_calls.jsonl')
 for p in sorted((ROOT/'tools').glob('*/manifest.json')):
  calls=[r for r in tc if r.get('tool')==p.parent.name and START<=r.get('ts',0)<=END]
  emit('tool',name=p.parent.name,calls=len(calls),failed=sum(not r.get('ok') for r in calls),manifest=read(str(p.relative_to(ROOT))))
 for p,i,l in logs[-30:]:emit('cap_cut',source=f'{p}:{i}',text=l)
 forum=read('.market/forum.json');posts=forum if isinstance(forum,list) else forum.get('posts',[])
 emit('forum_summary',shape=type(forum).__name__,posts=len(posts),demand_posts=sum('구합니다' in str(r) for r in posts))
 for r in posts:
  if '구합니다' in str(r):emit('forum_demand',post=r)
 orders=rows('.market/measure/orders.jsonl');paid={r.get('order') for r in rows('.market/payments.jsonl') if r.get('paid')}
 for label,lo,hi in [('before_saved',1793922334-5400,1793922334),('after_saved',1793922334,END)]:
  oo=[r for r in orders if lo<=r.get('ts',0)<hi];pp=[r for r in oo if r.get('order') in paid]
  emit('buyer',label=label,orders=len(oo),paid=len(pp),food_paid=sum(r.get('kind')=='food' for r in pp),quoted=sum(bool(r.get('quotes')) for r in oo),no_warranty=sum(bool(r.get('no_warranty')) for r in oo),nominal_saved_sum=sum(1-r['price']/r['list_price'] for r in pp if r.get('price') is not None and r.get('list_price')),note='nominal list share; not official saved after break/food valuation; different traffic mix')
 emit('previous_review',status='canary_failed',source='meta/changes.jsonl:42',graded_changes=len(rows('meta/changes.jsonl')[41]['answer'].get('grades',[])),note='Previous grades survived in immutable changes ledger; reverted code/doc changes are graded again here.')
if __name__=='__main__':main()
