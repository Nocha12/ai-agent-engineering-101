"""Cycle measurements, prints JSONL; no operational writes or network."""
import datetime as dt, json, os, re, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import agent_tools, hard_limits, meta_harness, patch_notes, scorecard
from tools.auto_market_research.tool import search
from tools.demand_inventory.tool import summarize
from test_market_scan import CAT, ROWS
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'meta/evidence/astra-1106-101510'
def emit(kind,**kw):print(json.dumps(dict(kind=kind,**kw),ensure_ascii=False))
def main():
 rules=patch_notes.check(str(ROOT),page=Path(str(BASE)+'-rules.html').read_text(),record=False)
 emit('rules',data=rules,all_patches=patch_notes.parse(Path(str(BASE)+'-rules.html').read_text())['patches'])
 b=json.loads(Path(str(BASE)+'-board.json').read_text());obj=scorecard.objective(b)
 emit('board',objective=obj,race=scorecard.race(b,scorecard.history(str(ROOT))),book=next(r['book'] for r in b['shops'] if r['name']=='nocha12'))
 mismatches=[];counts={};old=scorecard.ME
 # Remove owner identity so objective's owner fallback cannot select our row for each rival.
 clean={k:[{key:value for key,value in r.items() if key!='owner'} for r in b[k]] for k in ['shops','buyers']}
 for k in clean:
  counts[k]=0
  for row in clean[k]:
   if row.get('all') is None:continue
   scorecard.ME=row['name'];got=scorecard.objective(clean)[k]['all'];counts[k]+=1
   if abs(got-row['all'])>.0005:mismatches.append([k,row['name'],got,row['all']])
 scorecard.ME=old;emit('all_rows',counts=counts,mismatches=mismatches)
 with tempfile.TemporaryDirectory() as d:
  md=Path(d);(md/'catalog.json').write_text(json.dumps(CAT));(md/'ledger_rows.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in ROWS))
  env={'PATH':os.environ['PATH'],'MARKET_DIR':d,'PYTHONPATH':str(ROOT)}
  raw=subprocess.check_output([sys.executable,str(BASE)+'-research-before.py'],input=json.dumps({'category':'furniture','include_purchases':True}),text=True,env=env)
  emit('research_fixture',before=json.loads(raw),after=search(CAT,ROWS,[{'ledger':1,'paid':True}],{'category':'furniture','include_purchases':True}))
 for name,args in [('auto_market_research',{'category':'침대/매트리스','limit':1,'include_purchases':True}),('demand_inventory',{'limit':3})]:
  emit('sandbox',tool=name,result=agent_tools.execute(name,args,str(ROOT/'.market')))
 p=json.loads((ROOT/'pricing.json').read_text());before=json.loads(subprocess.check_output(['git','show','HEAD:submissions/26622007/week-08/pricing.json'],text=True))
 emit('limits',diff=hard_limits.policy_diff(before,p),problems=hard_limits.check_policy_change(before,p),seller_unchanged=before['seller']==p['seller'])
 pack=json.loads((ROOT/'meta/evidence/astra-1106-101510.json').read_text())
 emit('stale_pack',old_players=pack['board']['objective']['players'],board_players=pack['board']['race']['total'],old_drift=pack['board']['objective']['board_drift'],comparable_to_fresh=meta_harness.score_comparable(pack,{'board':{'objective':obj},'rules':rules}))
 audit=[json.loads(l) for l in Path(str(BASE)+'-audit-v2.jsonl').read_text().splitlines()];scale=next(r['data'] for r in audit if r['kind']=='scale')
 targets=set(p['restock']['priority_skus']+p['restock']['high_price_proven_skus']);cuts=[]
 lines=(ROOT/'logs/market-kit.log').read_text().splitlines()
 for lo,hi in zip(scale['windows'][-2:],scale['windows'][-1:]+[1793927710]):
  hits=[]
  for i,line in enumerate(lines,1):
   if '[restock] guard:' not in line or '(stock cost)' not in line:continue
   try:t=dt.datetime.fromisoformat('2026-11-06T'+line[:8]+'+09:00').timestamp()
   except ValueError:continue
   m=re.search(r'guard: (\w+)',line)
   if lo<=t<hi and m and m[1] in targets:hits.append({'ref':f'logs/market-kit.log:{i}','line':line})
  cuts.append({'open':lo,'end':hi,'priority_stock_cost_cuts':len(hits),'sources':hits})
 emit('scale_gate',windows=cuts,turnover=scale,book_now=pack['board']['shops']['mine']['book'],note='No bought-two-windows-ago sample; expansion gates do not all pass; cap stays 15M.')
 # Recalculate last ten mature sales per restored SKU, including unit-linked refunds, from complete earlier audit cohort.
 emit('discovery',result=summarize(json.loads((ROOT/'.market/forum.json').read_text()),json.loads((ROOT/'.market/catalog.json').read_text()),json.loads((ROOT/'.market/inventory.json').read_text()),1793927710,limit=10))
if __name__=='__main__':main()
