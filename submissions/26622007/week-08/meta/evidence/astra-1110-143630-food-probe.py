import json,collections
from pathlib import Path
end=1794289003;start=end-43200
catalog=json.load(open('.market/catalog.json'));stock=json.load(open('.market/inventory.json')).get('units',[])
skus={k:v for k,v in catalog.items() if v.get('kind')=='food' and any(w in str(v.get('category','')) for w in ['찌개','탕류','장류','곡물'])}
counts=collections.Counter()
seen=set()
for r in map(json.loads,open('.market/ledger_rows.jsonl')):
 if r.get('k')=='settle' and r.get('sku') in skus and start<=r.get('ts',0)<=end and r['seq'] not in seen:
  seen.add(r['seq']);counts[r['sku']]+=1
own=[{'sku':u['sku'],'unit':u.get('unit'),'remaining_hours':round((u['expires_at']-end)/3600,2),'cost':u.get('cost'),'list_price':skus[u['sku']].get('list_price'),'paid_12h':counts[u['sku']]} for u in stock if u.get('sku') in skus and isinstance(u.get('expires_at'),(int,float)) and u['expires_at']>=end+72*3600 and skus[u['sku']].get('list_price',1e20)<=99999 and counts[u['sku']]>0]
r={'kind':'food_demand_probe','catalog_candidates':len(skus),'paid_skus_12h':len(counts),'own_shelf_72h_under_100k_with_paid':own,'basis':'category substring and public settled SKU match; budget 9만원대 interpreted <100000; current inventory observed after pack, not cutoff inventory; no purchase or message; list is not an accepted offer'}
Path('meta/evidence/astra-1110-143630-food-probe.jsonl').write_text(json.dumps(r,ensure_ascii=False)+'\n')
print(len(skus),len(counts),len(own))
