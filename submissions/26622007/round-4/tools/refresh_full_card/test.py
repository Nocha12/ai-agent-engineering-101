def test(run,kit):
 import json,os
 inv={'units':[{'sku':'A1','name':'우유 24팩','grade':'B','cost':1000},{'sku':'A1','name':'우유 24팩','grade':'B','cost':1100},{'sku':'B2','name':'쌀 5kg','grade':'C','cost':2000}]}
 try:
  out=run({'_inventory':inv},kit)
  card=json.load(open('card.test.json',encoding='utf-8'))
  assert out['written'] and out['shown']==2 and out['left_out']==0
  assert '우유24팩 B' in card['description'] and '쌀5kg C' in card['description']
  assert card['name']=='노차네 구멍가게'
  assert card['description'].startswith('동네 구멍가게입니다. 생수·우유·쌀·계란·면·과일 같은 먹을거리를 조금씩 들여 두고, 사장님이 손님 안부를 기억합니다. 유통기한·등급·가격은 정직하게 말씀드립니다.')
 finally:
  if os.path.exists('card.test.json'): os.remove('card.test.json')

 # 떨이 11/26 19:5x: units on clearance come first, right after the fixed description
 import time
 now=1_800_000_000
 egg={'sku':'EG','name':'[한] 계란 20구','kind':'food','category':'식품>계란/유제품>계란','list_price':12900}
 cl={'units':[dict(egg,grade='C',cost=5000,expires_at=now+2*3600),dict(egg,grade='C',cost=5000,expires_at=now+6*3600),dict(egg,grade='A',cost=7000,expires_at=now+47*3600),{'sku':'G00b8acf0','name':'몽베스트 생수 2Lx6병','kind':'food','grade':'C','cost':2500,'expires_at':now+3600}]}
 try:
  out=run({'_inventory':cl,'_now':now},kit)
  card=json.load(open('card.test.json',encoding='utf-8'))
  assert out['clearance']=='떨이·기한 임박: 계란20구 C 4,190(~'+time.strftime('%H:%M',time.localtime(now+2*3600))+')크레딧', out
  d=card['description']
  assert d.index('떨이·기한 임박')<d.index('지금 재고') and '생수' not in d.split('지금 재고')[0].split('먹을거리를')[1], d
  out=run({'_inventory':inv},kit)
  assert out['clearance'] is None and '떨이' not in json.load(open('card.test.json',encoding='utf-8'))['description']
 finally:
  if os.path.exists('card.test.json'): os.remove('card.test.json')
 def alike(d):
  parts=[x.rsplit(' ',1)[0] for x in d.split('지금 재고')[-1].split(': ',1)[-1].split(' / ')]
  return len(parts)!=len(set(parts))
 # review r4: an undated unit (expires_at 0) and an SKU with nothing on sale keep their names, never the SKU id
 inv={'units':[{'sku':'G0000cccc','name':'계란 20구 대란','grade':'A','cost':1000,'expires_at':0},
               {'sku':'GOLD1234','name':'계란 20구','grade':'B','cost':1000,'expires_at':now-60}]}
 try:
  out=run({'_inventory':inv,'_now':now},kit)
  d=json.load(open('card.test.json',encoding='utf-8'))['description']
  assert out['written'] and 'G0000cccc' not in d and 'GOLD1234' not in d, d
  assert not alike(d), d                         # r5 I1: never two alike
 finally:
  if os.path.exists('card.test.json'): os.remove('card.test.json')
 return 'ok'
