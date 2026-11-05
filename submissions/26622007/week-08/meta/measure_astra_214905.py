"""Cutoff measurements. Output is a new immutable audit, no operational writes."""
import collections, datetime as dt, json, re, statistics, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import profit_cohorts as pc, market_model, patch_notes, scorecard, meta_harness
from tools.forum_demand.tool import search
END=dt.datetime.fromisoformat('2026-11-05T21:49:05+09:00').timestamp()
START=dt.datetime.fromisoformat('2026-11-05T09:38:17+09:00').timestamp()
def emit(kind, **x):print(json.dumps(dict(kind=kind,**x),ensure_ascii=False))
def read(p):return pc.read_json(p,{})
def rows(p):return pc.read_rows(p)
rr=list({r['seq']:r for r in rows('.market/ledger_rows.jsonl') if r.get('ts',0)<=END}.values())
cat=read('.market/catalog.json');mine=read('.market/loss_state.json')['mine']
mem=read('memory/seller_memory_r3.json')['negotiations']
coh=pc.summarize(rr,mine,mem,rows('memory/agent/losses.jsonl'),END,START)
for s in ['G8ec51935','G03d0199f','G05277ffe','Gb5d0dde7','G00327e80','Ge62449c9','G1465c678']:
 emit('post_review_cohort',sku=s,data=coh['skus'].get(s))
names={r['shop']:r['seller'] for r in rr if r.get('shop') and r.get('seller')}
for label,needle in [('inextro','inextro'),('Kiro729','kiro everyday')]:
 urls={u for u,n in names.items() if needle in n.lower()}
 ss=[r for r in rr if r.get('k')=='settle' and r.get('shop') in urls and START<=r['ts']<=END]
 top=[]
 for s,n in collections.Counter(r['sku'] for r in ss).most_common(5):
  trades=[r for r in ss if r['sku']==s];top.append(dict(sku=s,paid=n,buyers=len({r['buyer'] for r in trades}),revenue=sum(r['amount'] for r in trades),min=min(r['amount'] for r in trades),max=max(r['amount'] for r in trades)))
 emit('rival_alias',name=label,aliases=[n for u,n in names.items() if u in urls],paid=len(ss),buyers=len({r['buyer'] for r in ss}),top=top,margin=None,note='seller card alias substring; no public rival cost')
posts={}
for row in rows('.market/outbox.sent.jsonl'):
 if row.get('status')!=200 or not row.get('sent',{}).get('post'):continue
 try: ans=json.loads(row.get('answer','{}'))
 except (ValueError,TypeError):continue
 if ans.get('post'):posts[ans['post']]=row['sent']['post']
follows=[r for r in rows('.market/measure/forum_followup.jsonl') if START<=r.get('ts',0)<=END and r.get('role')=='buyer']
for at in ['10m','1h']:
 for label,token in [('trend','[가격 동향]'),('purchase','[구매 기록]')]:
  selected=list({r['post']:r for r in follows if r.get('at')==at and token in posts.get(r['post'],'')}.values())
  scores=[r['score'] for r in selected if r.get('listed') and isinstance(r.get('score'),(int,float))]
  emit('forum_same_age',age=at,kind_of_post=label,n=len(selected),listed=len(scores),median=statistics.median(scores) if scores else None)
leads=search(read('.market/forum.json'),cat,read('.market/inventory.json'),rr,END)
emit('demand_leads',**leads)
# Current kit log starts today 13:54; earlier guard intervals are explicitly unavailable here.
base=dt.datetime.fromisoformat('2026-11-05T00:00:00+09:00').timestamp()
kit=[]
for i,line in enumerate(Path('logs/market-kit.log').read_text(errors='replace').splitlines(),1):
 m=re.match(r'(\d\d):(\d\d):(\d\d)',line)
 if not m:continue
 h,mi,se=map(int,m.groups());t=base+h*3600+mi*60+se
 if START<=t<=END:kit.append((t,i,line))
windows=json.loads(Path('meta/evidence/astra-1105-214905-scale.jsonl').read_text())
ws=windows['windows']
for a,b in zip(ws[-2:],[ws[-1],END]):
 cuts=[(i,l) for t,i,l in kit if a<=t<b and '(stock cost)' in l and 'guard:' in l]
 counts=collections.Counter(re.search(r'guard: (\w+)',l).group(1) for i,l in cuts)
 emit('guard_window',start=a,end=b,cuts=len(cuts),priority_cuts={s:counts[s] for s in read('pricing.json')['restock']['sku_target_units']},refs=[f'logs/market-kit.log:{i}' for i,l in cuts[:2]],examples=[l for i,l in cuts[:2]])
for lo,hi in [(16,17),(18,19),(20,21)]:
 ss=[(i,l) for t,i,l in kit if base+lo*3600<=t<base+hi*3600]
 emit('kit_hour',hour=lo,supply=sum('[supply]' in l for i,l in ss),refused429=sum('[supply]' in l and '429' in l for i,l in ss),stock_cost_cuts=sum('(stock cost)' in l for i,l in ss),late_lines=sum('late_lines' in l for i,l in ss))
p=read('meta/evidence/astra-1105-214905.json');old=read('meta/evidence/astra-1105-093817.json')
emit('objective',published=patch_notes.parse(Path('meta/evidence/astra-1105-214905-rules.html').read_text())['scoring'],mismatch=patch_notes.compare(patch_notes.parse(Path('meta/evidence/astra-1105-214905-rules.html').read_text())['scoring'],scorecard.OBJECTIVE),before_board_players=old['board']['players']['mine']['total'],after_board_players=p['board']['players']['mine']['total'],old_formula=old['board']['objective']['players'],mixed_baseline_detected=meta_harness.objective_changed(old,p),book_before=old['board']['shops']['mine']['book'],book_after=p['board']['shops']['mine']['book'])
for c in rows('meta/changes.jsonl'):
 if c.get('time','')<'2026-11-05T09:38':continue
 emit('change_record',cycle=c['cycle'],status=c.get('status'),settings=c.get('settings'),problems=c.get('problems'),hypothesis=c.get('answer',{}).get('hypothesis'))
# Source files and line numbers remain available; operating logs are never rewritten.
