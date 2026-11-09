"""Fixed-cutoff local evidence, without messages or trading."""
import ast,collections,datetime as dt,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
C='astra-1109-133041'; p=json.load(open(f'meta/evidence/{C}.json')); end=dt.datetime.strptime(p['time'],'%Y-%m-%dT%H:%M:%S%z').timestamp()
def emit(kind,**kw):print(json.dumps(dict(kind=kind,**kw),ensure_ascii=False))
path=Path('tools/auto_market_brief/tool.py'); source=path.read_text() if path.exists() else json.load(open(f'meta/evidence/{C}-dropped-tool.json'))[str(path)]; tree=ast.parse(source); ns={}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='items'],type_ignores=[]),str(path),'exec'),ns)
catalog={'G1':{'name':'rice','category':'food'},'G2':{'name':'sofa','category':'goods'}}
rows=ns['items'](catalog); matches=[r for r in rows if 'rice' in json.dumps(r)]
emit('tool_fixture',tool=str(path),input=catalog,query='rice',limit=1,actual=matches,expected=[dict(catalog['G1'],sku='G1')],pass_filter=matches==[dict(catalog['G1'],sku='G1')])
windows=[json.loads(l) for l in open('.market/measure/supply_windows.jsonl')]
windows=sorted({w['opens_at']:w for w in windows if w.get('closes_at',end+1)<=end and w.get('ts',end+1)<=end}.values(),key=lambda w:w['opens_at'])[-2:]
from tools.supply_audit.tool import audit,read_rows
for sku in ['G8ec51935','G03d0199f','G24f5f979']:
 emit('supply',sku=sku,audit=audit(windows,read_rows('.market/measure/units_in.jsonl'),read_rows('.market/measure/units_out.jsonl'),end,sku),cohort=p['profit_cohorts']['skus'].get(sku,{}).get('mature_6h'))
for w in windows:
 cuts=[]
 for i,l in enumerate(open('logs/market-kit.log'),1):
  try:t=dt.datetime.fromisoformat('2026-11-09T'+l[:8]+'+09:00').timestamp()
  except ValueError:continue
  if w['opens_at']<=t<=w['closes_at'] and '[restock] guard:' in l:cuts.append((i,l.rstrip()))
 emit('guards',opens_at=w['opens_at'],closes_at=w['closes_at'],stock_cost=sum('(stock cost)' in l for _,l in cuts),sku_cap=sum('units per SKU' in l for _,l in cuts),cash=sum('cash kept' in l for _,l in cuts),samples=[{'ref':f'logs/market-kit.log:{i}','excerpt':l} for i,l in cuts[:3]])
for i,l in enumerate(open('.market/decisions.jsonl'),1):
 r=json.loads(l)
 if end-7200<=r.get('ts',0)<=end and r.get('agent')=='restock':emit('decision',ref=f'.market/decisions.jsonl:{i}',data=r)
# Independently check newest six-hour mature cohort, not lifetime missing costs.
import market_model
ledger=[json.loads(l) for l in open('.market/ledger_rows.jsonl')]; ledger=list({r['seq']:r for r in ledger if r.get('ts',0)<=end}.values())
mine=json.load(open('.market/loss_state.json'))['mine']; mem=json.load(open('memory/seller_memory_r3.json'))['negotiations']; costs={r['unit']:r['cost'] for r in mem if r.get('result')=='deal' and r.get('unit') and isinstance(r.get('cost'),(int,float))}
refund={s['seq']:s['amount'] for b,s in market_model.link_broken(ledger) if 0<=b['ts']-s['ts']<=(s.get('warranty') or 0)*3600}
groups=collections.defaultdict(list)
for r in ledger:
 if r.get('k')=='settle' and str(r['seq']) in mine and end-43200<=r['ts']<=end-21600:groups[r['sku']].append(r)
for sku,rs in groups.items():
 known=[r for r in rs if mine[str(r['seq'])].get('unit') in costs]
 emit('recent_mature',sku=sku,n=len(rs),missing=len(rs)-len(known),known_net=sum(r['amount']-costs[mine[str(r['seq'])]['unit']]-refund.get(r['seq'],0) for r in known),refunds=sum(refund.get(r['seq'],0) for r in rs),sale_seqs=[r['seq'] for r in rs])
