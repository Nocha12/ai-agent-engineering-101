import sys,json,datetime as dt,collections,re,gzip,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import profit_cohorts
from tools.supply_audit.tool import audit,read_rows
from tools.auto_forum_ledger_digest.tool import digest
p=json.load(open('meta/evidence/astra-1110-143630.json')); end=dt.datetime.fromisoformat(p['time']).timestamp()
def emit(kind,**kw):print(json.dumps(dict(kind=kind,**kw),ensure_ascii=False),flush=True)
measure=Path('.market/measure'); data=[read_rows(measure/(x+'.jsonl')) for x in ('supply_windows','units_in','units_out')]
windows=sorted({w['opens_at']:w for w in data[0] if w.get('closes_at',end+1)<=end and w.get('ts',end+1)<=end}.values(),key=lambda w:w['opens_at'])[-2:]
for w in windows:
 cuts=[]
 for path in ['logs/market-kit.log','logs/market-kit.log.1']:
  for i,l in enumerate(open(path),1):
   m=re.match(r'\[?(\d\d:\d\d:\d\d)\]? ',l)
   if not m:continue
   t=dt.datetime.fromisoformat('2026-11-10T'+m[1]+'+09:00').timestamp()
   if w['opens_at']-120<=t<=w['closes_at'] and 'guard:' in l:
    cuts.append((path,i,l.rstrip()))
 emit('guard',opens_at=w['opens_at'],stock_cost=sum('(stock cost)' in l for _,_,l in cuts),food_cost=sum('(food stock cost)' in l for _,_,l in cuts),sku_cuts=sum('units per SKU' in l for _,_,l in cuts),g2=[{'ref':f'{f}:{i}','excerpt':l} for f,i,l in cuts if 'G2c023a8c' in l][:10],note='모의 guard 반복 포함; 실제 감소 매입 아님')
