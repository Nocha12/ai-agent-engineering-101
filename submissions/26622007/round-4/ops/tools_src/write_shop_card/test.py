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
  assert out['written'] and out['clearance']=='떨이·기한 임박: 계란 20구 C 4,190(~'+time.strftime('%H:%M',time.localtime(now+2*3600))+')크레딧', out
  assert d.startswith('동네 구멍가게입니다.') and d.index('떨이·기한 임박')<d.index('지금 재고') and d.endswith('계란 20구 B8,000크레딧'), d
  assert '생수' not in d.split('지금 재고')[0].split('먹을거리를')[1], d
  assert run({'_inventory':cl,'_now':now},kit)['clearance']
  assert run({'_inventory':{'units':units},'stock':stock},kit)['clearance'] is None
 finally:
  if os.path.exists('card.test.json'): os.remove('card.test.json')
 return 'ok'
