"""Read-only semantic, decision, cohort and rules probes at the evidence cutoff."""
import json, os, subprocess, sys, tempfile, datetime as dt, importlib.util, gzip, collections, re
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import patch_notes, profit_cohorts
P='meta/evidence/astra-1107-234803'
p=json.load(open(P+'.json'));end=dt.datetime.strptime(p['time'],'%Y-%m-%dT%H:%M:%S%z').timestamp();start=1794019052
out=open(P+'-probes.jsonl','x')
def emit(kind,**kw):out.write(json.dumps(dict(kind=kind,**kw),ensure_ascii=False)+'\n');out.flush()
emit('rules',parsed=patch_notes.parse(Path(P+'-rules.html').read_text()),check=patch_notes.check(page=Path(P+'-rules.html').read_text(),record=False))
name='auto_market_research_context'
with tempfile.TemporaryDirectory() as d:
 m=Path(d);(m/'catalog.json').write_text(json.dumps({'G1':{'name':'매트리스','category':'가구','list_price':100}}));(m/'ledger_rows.jsonl').write_text(json.dumps({'k':'settle','seq':1,'ts':end-100,'sku':'G1','amount':90,'buyer':'buyer','shop':'shop'})+'\n');(m/'forum.json').write_text('[]')
 r=subprocess.run([sys.executable,'tools/'+name+'/tool.py'],input='{"query":"매트리스"}',text=True,capture_output=True,env={**os.environ,'MARKET_DIR':d})
 emit('semantic',tool=name,expected_paid=1,actual=json.loads(r.stdout) if r.returncode==0 else r.stderr)
for i,l in enumerate(open('.market/decisions.jsonl'),1):
 r=json.loads(l)
 if r.get('agent')=='restock' and start<=r.get('ts',0)<=end:
  text=r.get('final',{}).get('why','');calls=r.get('calls',[])
  emit('decision',ref=f'.market/decisions.jsonl:{i}',ts=r['ts'],why=text,reason=r.get('reason'),calls=calls,unit_id_in_why=bool(re.search(r'u-[a-z0-9]+',text)),both_tools={'supply_audit','cohort_profit'}<=set(c.get('tool') for c in calls))
for sku,when in [('G2698c6f8','2026-11-07T16:13:42+09:00'),('G3e0d6c67','2026-11-07T17:26:02+09:00'),('G19a70f63','2026-11-07T19:49:52+09:00'),('G47788edd','2026-11-07T21:02:16+09:00')]:
 ts=dt.datetime.fromisoformat(when).timestamp();allc=profit_cohorts.build('.',now=end,start=ts)
 emit('post_change_cohort',sku=sku,start=ts,result=allc['skus'].get(sku),note='사이클 시작 기준; 실제 적용은 이보다 늦으므로 경계 판매를 추가 분리해야 한다. 거절된 G477은 처리군 아님.')
# Read every listed log and decode compressed logs; count content instead of claiming a hash is inspection.
for path in p['files']:
 if not path.startswith('logs/') or not Path(path).is_file():continue
 op=gzip.open if path.endswith('.gz') else open
 counts=collections.Counter();examples=[]
 try:
  with op(path,'rt',errors='replace') as f:
   for i,l in enumerate(f,1):
    counts['lines']+=1
    for term in ('settled','paid','no stock','Traceback','guard:','stock cost','units per SKU','cash kept'):
     if term in l:counts[term]+=1
    if 'guard:' in l and len(examples)<2:examples.append({'ref':f'{path}:{i}','excerpt':l.strip()})
  emit('log_content',path=path,counts=dict(counts),examples=examples)
 except OSError as e:emit('log_error',path=path,error=str(e))
