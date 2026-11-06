import datetime as dt,gzip,json,re,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
import profit_cohorts as pc,scale_measure
END=1793927710
rows=pc.read_rows('.market/ledger_rows.jsonl');incoming=pc.read_rows('.market/measure/units_in.jsonl');outgoing=pc.read_rows('.market/measure/units_out.jsonl');windows=pc.read_rows('.market/measure/supply_windows.jsonl');mine=pc.read_json('.market/loss_state.json',{})['mine']
logrows=[]
paths=[('logs/kit-parts/market-kit-1105-215837-fa68fed7b7324560.log.gz','2026-11-05'),('logs/kit-parts/market-kit-1106-045016-0c4fec34c3cd41f8.log.gz','2026-11-06'),('logs/market-kit.log','2026-11-06')]
for p,date in paths:
 data=gzip.decompress(Path(p).read_bytes()).decode(errors='replace') if p.endswith('.gz') else Path(p).read_text()
 day=dt.datetime.fromisoformat(date+'T00:00:00+09:00');last=86400
 for i,line in reversed(list(enumerate(data.splitlines(),1))):
  m=re.match(r'(\d\d):(\d\d):(\d\d)',line)
  if not m:continue
  clock=sum(int(n)*w for n,w in zip(m.groups(),[3600,60,1]))
  if clock>last:day-=dt.timedelta(days=1)
  last=clock;t=day.timestamp()+clock
  if 'guard:' in line and '(stock cost)' in line:logrows.append((t,p,i,line))
for g in pc.read_rows('meta/grades.jsonl')[42:]:
 t=dt.datetime.fromisoformat(g['time']).timestamp()
 if t>END:continue
 # Targets at that review from its pack if available; use original policy targets for the gate, and expose it.
 targets={'G8ec51935':4,'G05277ffe':3,'Gb5d0dde7':2,'G03d0199f':1,'G00327e80':1 if t>=1793902629 else 2}
 s=scale_measure.summarize(windows,incoming,outgoing,rows,mine,targets,t);opens=s.get('windows',[])
 cuts=[]
 for lo,hi in zip(opens[-2:],opens[-1:]+[t]):
  hits=[(p,i,l) for ts,p,i,l in logrows if lo<=ts<hi and any(sku in l for sku in targets)]
  cuts.append({'open':lo,'cuts':len(hits),'examples':hits[:2]})
 passed=any(v['paid_fraction'] is not None and v['paid_fraction']>.5 and not v['extra_retained'] for v in s.get('skus',{}).values())
 print(json.dumps({'cycle':g['cycle'],'cap_windows':cuts,'turnover_pass_any':passed,'turnover':s,'all_three_proven':False if not all(x['cuts']>0 for x in cuts) or not passed else None,'note':'when first/second gate fails no expansion; matured profit and 6h book require separate check if both pass'},ensure_ascii=False))
