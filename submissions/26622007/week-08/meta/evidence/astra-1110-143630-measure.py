import sys,json,datetime as dt,collections,re,gzip,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import profit_cohorts
from tools.supply_audit.tool import audit,read_rows
from tools.auto_forum_ledger_digest.tool import digest
p=json.load(open('meta/evidence/astra-1110-143630.json')); end=dt.datetime.fromisoformat(p['time']).timestamp()
def emit(kind,**kw):print(json.dumps(dict(kind=kind,**kw),ensure_ascii=False),flush=True)
c=profit_cohorts.build('.',now=end,start=dt.datetime.fromisoformat('2026-11-10T04:30:00+09:00').timestamp())
emit('g2_post_change',cohort=c['skus'].get('G2c023a8c'))
measure=Path('.market/measure'); data=[read_rows(measure/(x+'.jsonl')) for x in ('supply_windows','units_in','units_out')]
for sku in ['G2c023a8c','G03d0199f','G6b9db161','G24f5f979','G8ec51935']:
 emit('supply',**audit(*data,end,sku))
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
 emit('guard',opens_at=w['opens_at'],stock_cost=sum('stock cost' in l for _,_,l in cuts),sku_cuts=sum('units per SKU' in l for _,_,l in cuts),g2=[{'ref':f'{f}:{i}','excerpt':l} for f,i,l in cuts if 'G2c023a8c' in l][:10],note='모의 guard 반복 포함; 실제 감소 매입 아님')
forum=json.load(open('.market/forum.json')); rows=profit_cohorts.read_rows('.market/ledger_rows.jsonl'); rows=[r for r in rows if r.get('ts',0)<=end]
forum=[f for f in forum if f.get('ts',0)<=end]
out=digest(forum,rows,20)
emit('digest_probe',posts=len(out['forum_posts']),paid_recent=len(out['ledger_rows']),reference_status=dict(collections.Counter(x['status'] for x in out['reference_evidence'])),samples=out['reference_evidence'][:3])
emit('forum_demand',posts=[{k:f.get(k) for k in ('id','ts','text','refs','score')} for f in forum if '구합니다' in f.get('text','')])
# Read every listed log, including compressed historic logs, without changing originals.
for name in p['files']:
 if not name.startswith('logs/') or not Path(name).is_file():continue
 raw=Path(name).read_bytes(); content=gzip.decompress(raw) if name.endswith('.gz') else raw
 emit('log_scan',path=name,sha256=hashlib.sha256(raw).hexdigest(),lines=content.count(b'\n'),guard_lines=content.count(b'guard:'),tracebacks=content.count(b'Traceback'))
