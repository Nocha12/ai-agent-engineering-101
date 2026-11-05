"""Fixed-cutoff read-only audit; stdout saved once, never rewrites operational logs."""
import collections, datetime as dt, gzip, hashlib, json, subprocess, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import profit_cohorts as pc, market_model, strategy_screen
ROOT=Path(__file__).resolve().parents[1]
START=dt.datetime.fromisoformat('2026-11-05T09:38:17+09:00').timestamp()
END=dt.datetime.fromisoformat('2026-11-05T21:49:05+09:00').timestamp()
def emit(kind,**x): print(json.dumps(dict(kind=kind,**x),ensure_ascii=False))
def read(p): return pc.read_json(ROOT/p,{})
def rows(p): return pc.read_rows(ROOT/p)
def main():
 pack=read('meta/evidence/astra-1105-214905.json')
 for p in pack['files']:
  if p.startswith('logs/') and (ROOT/p).is_file():
   b=(ROOT/p).read_bytes(); content=gzip.decompress(b) if p.endswith('.gz') else b
   emit('log_read',path=p,bytes=len(b),lines=len(content.splitlines()),sha256=hashlib.sha256(b).hexdigest())
 rr=list({r['seq']:r for r in rows('.market/ledger_rows.jsonl') if r.get('ts',0)<=END}.values())
 mine=read('.market/loss_state.json')['mine']; mem=read('memory/seller_memory_r3.json')['negotiations']
 costs={r['unit']:r['cost'] for r in mem if r.get('result')=='deal' and r.get('unit') and isinstance(r.get('cost'),(int,float))}
 refs={r['seq']:f'.market/ledger_rows.jsonl:{i}' for i,r in enumerate(rows('.market/ledger_rows.jsonl'),1)}
 own=[r for r in rr if r.get('k')=='settle' and str(r['seq']) in mine]
 refunds={s['seq']:s['amount'] for b,s in market_model.link_broken(rr) if str(s['seq']) in mine and 0<=b['ts']-s['ts']<=s.get('warranty',0)*3600 and (not b.get('order') or b['order']==s.get('order'))}
 def win(lo,hi):
  ss=[r for r in own if lo<=r['ts']<hi]; known=[r for r in ss if mine[str(r['seq'])].get('unit') in costs]
  return dict(paid=len(ss),revenue=sum(r['amount'] for r in ss),net_after_refunds=sum(r['amount']-costs[mine[str(r['seq'])]['unit']]-refunds.get(r['seq'],0) for r in known),missing_cost=len(ss)-len(known),open_warranty=sum(r['ts']>END-21600 for r in ss),market_paid=sum(r.get('k')=='settle' and lo<=r['ts']<hi for r in rr),refs=[refs[r['seq']] for r in ss[:1]+ss[-1:]])
 emit('cycle',start=START,end=END,**win(START,END))
 git=subprocess.check_output(['git','log','--reverse','--since=2026-11-05T09:38:17+09:00','--format=%h%x09%ct%x09%s','--','.'],text=True)
 for l in git.splitlines():
  sha,t,title=l.split('\t',2); t=int(t)
  if t>END:continue
  files=subprocess.check_output(['git','show','--format=','--name-only',sha,'--','.'],text=True).splitlines()
  emit('commit',sha=sha,ts=t,title=title,files=files,before=win(t-1800,t),after=win(t,min(t+1800,END)))
 for i,g in enumerate(rows('meta/grades.jsonl'),1):
  if not g.get('cycle','').startswith('terra-'):continue
  t=dt.datetime.fromisoformat(g['time']).timestamp()
  if t<START:continue
  emit('terra',cycle=g['cycle'],status=g['status'],source=f'meta/grades.jsonl:{i}',check=g.get('strategy_check'),gap=g.get('evidence_gap'),before=win(t-3600,t),after=win(t,min(t+3600,END)))
 for sku in ['G8ec51935','Gb5d0dde7','G03d0199f','G05277ffe','G00327e80','G1465c678','Ge62449c9','G6b9db161','G75f11c19']:
  emit('cohort',sku=sku,data=pack['profit_cohorts']['skus'].get(sku))
 for p in sorted((ROOT/'tools').glob('*/manifest.json')):
  calls=[r for r in rows('.market/tool_calls.jsonl') if r.get('tool')==p.parent.name and START<=r.get('ts',0)<=END]
  emit('tool',name=p.parent.name,calls=len(calls),failed=sum(not r.get('ok') for r in calls),selftest=read(str(p.relative_to(ROOT))).get('selftest'))
 names={r['shop']:r['seller'] for r in rr if r.get('seller') and r.get('shop')}
 for rival in pack['board']['rivals']['top']:
  urls={u for u,n in names.items() if rival['name'].lower()==n.lower()}
  ss=[r for r in rr if r.get('k')=='settle' and r.get('shop') in urls and START<=r['ts']<=END]
  emit('rival',name=rival['name'],mapped=bool(urls),paid=len(ss) if urls else None,buyers=len({r.get('buyer') for r in ss}) if urls else None,top=[dict(sku=k,paid=v,min=min(r['amount'] for r in ss if r['sku']==k),max=max(r['amount'] for r in ss if r['sku']==k)) for k,v in collections.Counter(r['sku'] for r in ss).most_common(5)],margin=None)
 forum=read('.market/forum.json')
 for r in forum if isinstance(forum,list) else []:
  if r.get('ts',0)<=END and ('구합니다' in r.get('text','') or r.get('author','').lower() in ['nocha12','inextro','dae-yangkim']):emit('forum',post=r)
if __name__=='__main__':main()
