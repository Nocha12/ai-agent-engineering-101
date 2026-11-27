def test(run,kit):
 import json,os
 units=[]; stock={}
 for i in range(22):
  sku='S'+str(i); units.append({'sku':sku,'name':'식품상품이름'+str(i),'grade':'A','cost':1000}); stock[sku]={'A':2000}
 try:
  out=run({'_inventory':{'units':units},'stock':stock},kit)
  card=json.load(open('card.test.json',encoding='utf-8'))
  assert out['written'] and out['shown']==22 and out['left_out']==0
  assert '외' not in card['description'] and '식품상품' in card['description']
  assert not run({'_inventory':{'units':units},'stock':{'S0':{'A':100}}},kit)['written']
 finally:
  if os.path.exists('card.test.json'): os.remove('card.test.json')

 # 떨이 11/26 19:5x: units on clearance right after the fixed description, before the stock line; water never
 import time
 now=1_800_000_000
 egg={'sku':'EG','name':'[한] 계란 20구','kind':'food','category':'식품>계란/유제품>계란','list_price':12900}
 cl={'units':[dict(egg,grade='C',cost=5000,expires_at=now+2*3600),dict(egg,grade='B',cost=6000,expires_at=now+47*3600),{'sku':'G00b8acf0','name':'몽베스트 생수 2Lx6병','kind':'food','grade':'C','cost':2500,'expires_at':now+3600}]}
 try:
  out=run({'_inventory':cl,'_now':now,'stock':{'EG':{'B':8000}}},kit)
  d=json.load(open('card.test.json',encoding='utf-8'))['description']
  assert out['written'] and out['clearance']=='떨이·기한 임박: 계란20구 C 4,190(~'+time.strftime('%H:%M',time.localtime(now+2*3600))+')크레딧', out
  assert d.startswith('동네 구멍가게입니다.') and d.index('떨이·기한 임박')<d.index('지금 재고') and d.endswith('계란20구 B8,000크레딧'), d
  assert '생수' not in d.split('지금 재고')[0].split('먹을거리를')[1], d
  assert run({'_inventory':cl,'_now':now},kit)['clearance']
  assert run({'_inventory':{'units':units},'stock':stock},kit)['clearance'] is None
 finally:
  if os.path.exists('card.test.json'): os.remove('card.test.json')
 def alike(d):
  parts=[x.rsplit(' ',1)[0] for x in d.split('지금 재고')[-1].split(': ',1)[-1].split(' / ')]
  return len(parts)!=len(set(parts))
 # review r4: an undated unit (expires_at 0) and an SKU with nothing on sale keep their names, never the SKU id
 inv={'units':[{'sku':'G0000cccc','name':'계란 20구 대란','grade':'A','cost':1000,'expires_at':0},
               {'sku':'GOLD1234','name':'계란 20구','grade':'B','cost':1000,'expires_at':now-60},
               {'sku':'GNEW5678','name':'계란 20구','grade':'B','cost':1000,'expires_at':now+86400}]}
 try:
  out=run({'_inventory':inv,'_now':now,'stock':{'G0000cccc':{'A':3000},'GOLD1234':{'B':3000}}},kit)
  d=json.load(open('card.test.json',encoding='utf-8'))['description']
  assert out['written'] and 'G0000cccc' not in d and 'GOLD1234' not in d and '대란계란20구 A3,000' in d, d
  assert not alike(d), d
  inv={'units':inv['units'][:2]}                     # r5 I1: one on sale, one not, nothing else: still two names
  out=run({'_inventory':inv,'_now':now,'stock':{'G0000cccc':{'A':3000},'GOLD1234':{'B':3000}}},kit)
  d=json.load(open('card.test.json',encoding='utf-8'))['description']
  assert out['written'] and not alike(d) and 'GOLD1234' not in d, d
 finally:
  if os.path.exists('card.test.json'): os.remove('card.test.json')
 # cash-out 11/27 (review I4): the card takes a cash-out price for a grade with a unit of 48 h or less left, not under it
 gm={'sku':'GM','name':'서울우유 멸균우유 190ml 24팩','kind':'food','cost':12235}
 inv={'units':[dict(gm,unit='c1',grade='C',expires_at=now+29*3600),dict(gm,unit='b1',grade='B',expires_at=now+200*3600)]}
 os.makedirs('logs',exist_ok=True)
 with open('logs/quotes.test.json','w',encoding='utf-8') as f:
  json.dump({'GM|C':{'price':11787,'ts':now,'by':'cashout','floor':11787},'GM|B':{'price':11787,'ts':now,'by':'cashout','floor':11787}},f)
 try:
  out=run({'_inventory':inv,'_now':now,'stock':{'GM':{'C':11787}}},kit)
  assert out['written'] and 'C11,787' in out['stock'], out
  assert not run({'_inventory':inv,'_now':now,'stock':{'GM':{'C':11786}}},kit)['written']
  assert not run({'_inventory':inv,'_now':now,'stock':{'GM':{'B':11787}}},kit)['written']   # B: only a 200 h unit
 finally:
  for f in ('card.test.json','logs/quotes.test.json'):
   if os.path.exists(f): os.remove(f)
 return 'ok'
