import json, os, re, sys
import market_model

a=json.load(sys.stdin); md=os.environ['MARKET_DIR']
def read(n, default):
 try:
  with open(os.path.join(md,n)) as f: return json.load(f)
 except Exception: return default
def rows(x):
 if isinstance(x,list): return x
 if isinstance(x,dict):
  for k in ('items','rows','posts','catalog','inventory'):
   if isinstance(x.get(k),list): return x[k]
  return list(x.values())
 return []
def text(x): return json.dumps(x,ensure_ascii=False).lower()
cat=rows(read('catalog.json',[])); inv=read('inventory.json',{})
ledger=market_model.load_rows(md)
if not isinstance(ledger,list): ledger=rows(ledger)
q=str(a.get('query','')).lower(); c=str(a.get('category','')).lower(); lim=max(1,min(100,int(a.get('limit',20))))
def match(x):
 t=text(x); return (not q or q in t) and (not c or c in str(x.get('category','')).lower() or c in t)
cm=[x for x in cat if isinstance(x,dict) and match(x)]
seen=set(); deals=[]
for r in ledger:
 if not isinstance(r,dict) or not match(r): continue
 if not any(z in text(r) for z in ('settle','paid','complete')): continue
 key=str(r.get('deal_id',r.get('order_id',r.get('id',text(r)))))
 if key not in seen: seen.add(key); deals.append(r)
deals.sort(key=lambda x: x.get('timestamp',x.get('created_at',0)),reverse=True)
identity=' '.join(str(inv.get(k,'')) for k in ('shop','shop_name','agent','name')).strip().lower() if isinstance(inv,dict) else ''
purchases=[]
if a.get('include_purchases') and identity:
 purchases=[r for r in deals if identity in str(r.get('buyer','')).lower()]
forum=rows(read('forum.json',[]))
posts=[x for x in forum if isinstance(x,dict) and (not q or q in text(x)) and (not c or c in text(x))]
posts.sort(key=lambda x:x.get('timestamp',x.get('created_at',0)),reverse=True)
print(json.dumps({'catalog_count':len(cm),'catalog':cm[:lim],'deal_count':len(deals),'deals':deals[:lim],'purchases':purchases[:lim] if a.get('include_purchases') else None,'forum_count':len(posts),'forum':posts[:lim]},ensure_ascii=False))