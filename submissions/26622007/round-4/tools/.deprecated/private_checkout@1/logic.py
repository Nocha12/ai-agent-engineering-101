import os, json, time, httpx, hashlib, base64
BASE='https://market.ourolabs.ai'
def run(args,kit):
    op=args.get('op')
    if op not in ('create','settle'): return {'ok':False,'error':'op must be create or settle'}
    sku=args.get('sku'); price=args.get('price'); task=args.get('task'); unit=args.get('unit')
    if not isinstance(sku,str) or not sku or not isinstance(price,int) or price<=0 or not isinstance(task,str) or not task or not isinstance(unit,str) or not unit: return {'ok':False,'error':'sku, positive integer price, task and unit are required'}
    grade=args.get('grade')
    if grade not in ('A','B','C'): return {'ok':False,'error':'grade must be current inventory grade A, B, or C'}
    warranty=args.get('warranty_hours',0)
    if not isinstance(warranty,int) or warranty<0 or warranty>72: return {'ok':False,'error':'warranty_hours must be between 0 and 72'}
    payload={'id':'checkout','shop':kit.name,'shop_url':kit.shop_url,'item':sku,'price':price,'task':task,'currency':'credits','iat':int(time.time()),'unit':unit,'claim':grade,'warranty_hours':warranty,'private':True}
    if args.get('cert') is not None: payload['cert']=args['cert']
    jwt=kit.sign_jwt(payload,'shop')
    digest=base64.urlsafe_b64encode(hashlib.sha256(jwt.encode()).digest()).decode().rstrip('=')
    if op=='create': return {'ok':True,'checkout_jwt':jwt,'checkout_hash':digest,'payload':payload}
    order=args.get('order'); cm=args.get('checkout_mandate'); pm=args.get('payment_mandate')
    if not isinstance(order,str) or not order or not isinstance(cm,dict) or not isinstance(pm,dict): return {'ok':False,'error':'order and buyer-signed checkout_mandate/payment_mandate are required'}
    if kit.testing: return {'ok':False,'error':'testing mode: settlement not sent','checkout_jwt':jwt}
    try:
        r=httpx.post(BASE+'/api/settle',json={'order':order,'checkout_jwt':jwt,'checkout_mandate':cm,'payment_mandate':pm},headers={'authorization':'Bearer '+kit.token('shop')},timeout=25)
        data=r.json()
    except Exception as e: return {'ok':False,'error':'settlement outcome uncertain; verify before retrying','detail':str(e)}
    if r.status_code>=400 or not data.get('settled'): return {'ok':False,'status':r.status_code,'response':data}
    rec=data.get('mandate_receipt') or {}
    safe={k:rec.get(k) for k in ('order','amount','shop_fee','tx_hash','warranty_hours')}; safe['ts']=int(time.time())
    os.makedirs('logs',exist_ok=True)
    with open('logs/sales.jsonl','a',encoding='utf-8') as f: f.write(json.dumps(safe,separators=(',',':'))+'\n')
    return {'ok':True,'settled':True,'receipt':safe,'response':data}
