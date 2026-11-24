import os,json,time,httpx
BASE='https://market.ourolabs.ai'; RPC='https://rpc-testnet.maroo.io'
def getj(c,url,headers=None):
 r=c.get(url,headers=headers); r.raise_for_status(); return r.json()
def post(c,url,obj,headers):
 r=c.post(url,json=obj,headers=headers); return r.status_code,r.json()
def records(obj):
 if isinstance(obj,list): return obj
 if isinstance(obj,dict):
  for k in ('items','catalog','products','skus','data'):
   if isinstance(obj.get(k),list): return obj[k]
 return []
def skuval(x):
 return x.get('sku') or x.get('id') or x.get('item')
def has_surge(obj,sku):
 if isinstance(obj,list):
  for x in obj:
   if x==sku: return True
   if isinstance(x,dict) and sku in (x.get('sku'),x.get('id')): return True
 elif isinstance(obj,dict):
  if sku in obj: return True
  for k in ('surge','skus','items','prices'):
   if k in obj and has_surge(obj[k],sku): return True
 return False
def rpc(method,params):
 r=httpx.post(RPC,json={'jsonrpc':'2.0','id':1,'method':method,'params':params},timeout=20); r.raise_for_status(); j=r.json()
 if j.get('error') or 'result' not in j: raise RuntimeError('RPC failed')
 return j['result']
def run(args,kit):
 sku=args.get('sku'); qty=args.get('qty')
 if not isinstance(sku,str) or not sku: return {'ok':False,'error':'sku required'}
 if not isinstance(qty,int) or qty<2 or qty>4: return {'ok':False,'error':'qty must be 2 to 4'}
 if kit.testing: return {'ok':False,'error':'testing mode: no purchase sent'}
 headers={'authorization':'Bearer '+kit.token('shop')}
 with httpx.Client(timeout=25) as c:
  inv=getj(c,BASE+'/api/inventory',headers)
  if not inv.get('restock',{}).get('open'): return {'ok':False,'error':'restock window closed','restock':inv.get('restock')}
  catalog=getj(c,BASE+'/api/catalog')
  entry=next((x for x in records(catalog) if skuval(x)==sku),None)
  if not entry: return {'ok':False,'error':'SKU not found in catalog'}
  kind=str(entry.get('kind','')).lower(); name=str(entry.get('name','')); category=str(entry.get('category',''))
  if kind!='food': return {'ok':False,'error':'food only'}
  allowed=('생수','물','우유','두유','쌀','계란','달걀','면','국수','누들','과일')
  if not any(k in name or k in category for k in allowed): return {'ok':False,'error':'not an approved staple-food category','name':name,'category':category}
  list_price=entry.get('list_price')
  if not isinstance(list_price,(int,float)) or list_price<=3000: return {'ok':False,'error':'list price must exceed 3000'}
  surge=getj(c,BASE+'/api/supply-prices')
  if has_surge(surge,sku): return {'ok':False,'error':'SKU is in surge list'}
  bal=inv.get('balance',0)
  if not isinstance(bal,(int,float)) or bal<500000: return {'ok':False,'error':'balance below safety floor'}
  status,quote=post(c,BASE+'/api/supply',{'sku':sku,'qty':qty},headers)
  if status not in (200,402): return {'ok':False,'status':status,'response':quote}
  total=quote.get('total')
  pay=quote.get('pay')
  if not isinstance(total,(int,float)) or not isinstance(pay,dict): return {'ok':False,'error':'unexpected quote format','response':quote}
  max_total=list_price*0.6*0.99*qty-210*qty-205
  if total>max_total: return {'ok':False,'error':'quote exceeds conservative break-even ceiling','total':total,'max_total':max_total,'units':quote.get('units')}
  if total>bal-1000000: return {'ok':False,'error':'would leave less than 1,000,000 credits cash','balance':bal,'total':total}
  if not quote.get('quote') or not pay.get('to') or not pay.get('wei') or not pay.get('data'): return {'ok':False,'error':'quote/payment fields incomplete'}
  wei=int(str(pay['wei']),0) if isinstance(pay['wei'],str) else int(pay['wei'])
  if wei<=0 or wei>500000*10**15: return {'ok':False,'error':'payment exceeds tool limit'}
  nonce=int(rpc('eth_getTransactionCount',[kit.address('shop'),'pending']),16)
  os.makedirs('logs',exist_ok=True)
  intent={'ts':int(time.time()),'purpose':'supply','order':str(quote['quote']),'to':pay['to'],'amount':total,'nonce':nonce}
  with open('logs/sends.jsonl','a',encoding='utf-8') as f: f.write(json.dumps(intent,separators=(',',':'))+'\n')
  try: tx=kit.pay(pay['to'],wei,pay['data'],role='shop')
  except Exception as e: return {'ok':False,'error':'payment outcome uncertain; verify nonce and chain before any retry','detail':str(e),'quote':quote.get('quote')}
  txid=tx if isinstance(tx,str) else str(tx)
  with open('logs/sends.jsonl','a',encoding='utf-8') as f: f.write(json.dumps({'ts':int(time.time()),'purpose':'supply','order':str(quote['quote']),'to':pay['to'],'amount':total,'tx_hash':txid},separators=(',',':'))+'\n')
  st,final=post(c,BASE+'/api/supply',{'quote':quote['quote'],'tx_hash':txid},headers)
  return {'ok':st<400,'status':st,'sku':sku,'name':name,'qty':qty,'total':total,'tx_hash':txid,'result':final}
