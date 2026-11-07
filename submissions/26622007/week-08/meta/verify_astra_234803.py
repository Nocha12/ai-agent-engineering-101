"""Follow-up measurements; preserves the first probe including its import failure."""
import json,os,sys,tempfile,subprocess,collections,datetime as dt
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import profit_cohorts
P='meta/evidence/astra-1107-234803';end=1794062884;start=1794019052
out=open(P+'-verified.jsonl','x')
def emit(kind,**kw):out.write(json.dumps(dict(kind=kind,**kw),ensure_ascii=False)+'\n');out.flush()
with tempfile.TemporaryDirectory() as d:
 p=Path(d);(p/'catalog.json').write_text(json.dumps({'G1':{'name':'매트리스','category':'가구','list_price':100}}));(p/'ledger_rows.jsonl').write_text(json.dumps({'k':'settle','seq':1,'ts':end-100,'sku':'G1','amount':90,'buyer':'buyer','shop':'shop'})+'\n');(p/'forum.json').write_text('[]')
 r=subprocess.run([sys.executable,'tools/auto_market_research_context/tool.py'],input='{"query":"매트리스"}',text=True,capture_output=True,env={**os.environ,'MARKET_DIR':d,'PYTHONPATH':os.getcwd()})
 emit('semantic',expected_paid=1,returncode=r.returncode,actual=json.loads(r.stdout) if r.returncode==0 else r.stderr)
rows=list({r['seq']:r for r in profit_cohorts.read_rows('.market/ledger_rows.jsonl') if r.get('ts',0)<=end}.values());cat=json.load(open('.market/catalog.json'))
old=[json.loads(l) for l in open('meta/evidence/astra-1107-113730-audit-v3.jsonl')];new=[json.loads(l) for l in open(P+'-audit.jsonl')]
for r in new:
 if r['kind']!='rival':continue
 urls=set(r['urls'])
 for x in old:
  if x['kind']=='rival' and x['name']==r['name']:urls.update(x['urls'])
 ss=[s for s in rows if s.get('k')=='settle' and s.get('shop') in urls and start<=s['ts']<=end]
 emit('rival',name=r['name'],urls=sorted(urls),paid=len(ss),buyers=len({s['buyer'] for s in ss}),revenue=sum(s['amount'] for s in ss),dear_revenue=sum(s['amount'] for s in ss if cat.get(s['sku'],{}).get('list_price',0)>=600000),top_buyers=collections.Counter(s['buyer'] for s in ss).most_common(3),top_skus=collections.Counter(s['sku'] for s in ss).most_common(4),note='직전 감사와 현재 shops의 URL 합집합; 중간 미확인 터널은 누락될 수 있는 하한. 경쟁원가 비공개.')
for sku in ['G2aefe28f','G40b26651']:
 ss=[s for s in rows if s.get('k')=='settle' and s.get('sku')==sku and start<=s['ts']<=end]
 emit('unserved_demand',sku=sku,catalog=cat.get(sku),paid=len(ss),buyers=collections.Counter(s['buyer'] for s in ss),prices=[min(s['amount'] for s in ss),max(s['amount'] for s in ss)] if ss else [],seqs=[s['seq'] for s in ss[:3]],note='포럼 #36857의 주문19/11회는 자기보고이며 정산 수와 다름. 수요 발견은 라이브 스냅샷, 효과 감사 cutoff와 분리.')
# Actual accepted commit boundary, equal-length pre/post sale windows with cutoff maturity.
for sku,sha in [('G2698c6f8','d1ba9c23'),('G3e0d6c67','d6ae5558'),('G19a70f63','7acdd73f')]:
 t=int(subprocess.check_output(['git','show','-s','--format=%ct',sha],text=True));span=end-t
 before=profit_cohorts.build('.',now=end,start=t-span)['skus'].get(sku,{})
 after=profit_cohorts.build('.',now=end,start=t)['skus'].get(sku,{})
 # Before must exclude post-change sales, so summarize restricted sale stream while preserving later breaks.
 filtered=[r for r in rows if r.get('k')!='settle' or r['ts']<t]
 b=profit_cohorts.summarize(filtered,json.load(open('.market/loss_state.json'))['mine'],json.load(open('memory/seller_memory_r3.json'))['negotiations'],[],end,t-span)['skus'].get(sku,{})
 emit('sku_change',sku=sku,commit=sha,start=t,hours=span/3600,before=b,after=after,note='동일 길이 판매구간. after의 미숙성은 성공 판정 제외; 사후 선택·품절 기회비용 미측정.')
