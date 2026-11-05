"""Historical scale gates and buyer outcomes at the review cutoff; read-only."""
import datetime as dt,json,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import profit_cohorts as pc,scale_measure
END=dt.datetime.fromisoformat('2026-11-05T21:49:05+09:00').timestamp()
rows=pc.read_rows
win=rows('.market/measure/supply_windows.jsonl');inc=rows('.market/measure/units_in.jsonl');out=rows('.market/measure/units_out.jsonl');ledger=rows('.market/ledger_rows.jsonl');mine=pc.read_json('.market/loss_state.json',{})['mine']
for r in rows('meta/grades.jsonl'):
 if not r.get('cycle','').startswith('terra-1105-') or r.get('time','')<'2026-11-05T09:38':continue
 f=Path('meta/evidence')/(r['cycle']+'.json')
 if not f.exists():continue
 p=json.loads(f.read_text());t=dt.datetime.fromisoformat(p['time']).timestamp()
 targets={'G8ec51935':4,'G05277ffe':3,'Gb5d0dde7':2,'G03d0199f':1 if t>=1793871994 else 2,'G00327e80':2}
 cohort=p.get('profit_cohorts',{}).get('skus',{})
 print(json.dumps({'cycle':r['cycle'],'pack':str(f),'cutoff':t,'maturity':{s:cohort.get(s,{}).get('mature_6h') for s in targets},'units':scale_measure.summarize(win,inc,out,ledger,mine,targets,t),'rules_match':p.get('rules',{}).get('objective_matches')},ensure_ascii=False))
orders=rows('.market/measure/orders.jsonl');paid={r.get('order') for r in rows('.market/payments.jsonl') if r.get('paid')}
for sha in ['c6292b5','42c174b','a69b853']:
 t=int(subprocess.check_output(['git','show','-s','--format=%ct',sha],text=True))
 data={}
 for label,lo,hi in [('before',t-3600,t),('after',t,min(t+3600,END))]:
  ss=list({r['order']:r for r in orders if lo<=r.get('ts',0)<hi}.values());yes=[r for r in ss if r['order'] in paid]
  data[label]={'seconds':hi-lo,'orders':len(ss),'paid':len(yes),'paid_rate':len(yes)/len(ss) if ss else None,'no_warranty':sum(bool(r.get('no_warranty')) for r in ss),'skipped_short_warranty':sum(bool(r.get('skipped_short_warranty')) for r in ss),'nominal_saving':sum((r.get('list_price') or 0)-(r.get('price') or 0) for r in yes),'basis':'nominal saving is not matured surplus; cutoff order cohorts, payments read now'}
 print(json.dumps({'commit':sha,'buyer_windows':data},ensure_ascii=False))
