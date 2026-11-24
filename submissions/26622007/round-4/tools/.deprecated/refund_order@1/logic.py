import os, json, time, hashlib
import httpx

BASE='https://market.ourolabs.ai'
RPC='https://rpc-testnet.maroo.io'

def _json(resp):
    resp.raise_for_status()
    return resp.json()

def _rows(obj):
    if isinstance(obj,list): return obj
    if isinstance(obj,dict):
        for k in ('ledger','rows','entries','items'):
            if isinstance(obj.get(k),list): return obj[k]
    return []

def _order(row):
    d=row.get('data') or {}
    return row.get('order') or d.get('order') or row.get('data',{}).get('order')

def _shop(row):
    d=row.get('data') or {}
    return row.get('shop') or d.get('shop')

def _rpc(method, params):
    r=httpx.post(RPC,json={'jsonrpc':'2.0','id':1,'method':method,'params':params},timeout=20)
    r.raise_for_status()
    obj=r.json()
    if obj.get('error') or 'result' not in obj: raise RuntimeError('rpc unavailable')
    return obj['result']

def _read(path):
    try:
        with open(path,encoding='utf-8') as f: return [json.loads(x) for x in f if x.strip()]
    except FileNotFoundError: return []

def run(args, kit):
    order=str(args.get('order','')).strip()
    if not order: return {'ok':False,'error':'order is required'}
    if kit.testing: return {'ok':False,'error':'testing mode: no refund sent'}
    headers={'authorization':'Bearer '+kit.token('shop')}
    with httpx.Client(timeout=20) as c:
        ledger=_json(c.get(BASE+'/api/ledger',params={'after':-500}))
        rows=_rows(ledger)
        rel=[r for r in rows if _order(r)==order]
        settles=[r for r in rel if r.get('kind')=='settle' and (_shop(r)==kit.shop_url or str(_shop(r) or '').rstrip('/').lower()==str(kit.shop_url).rstrip('/').lower())]
        broken=[r for r in rel if r.get('kind')=='broken' and (_shop(r)==kit.shop_url or str(_shop(r) or '').rstrip('/').lower()==str(kit.shop_url).rstrip('/').lower())]
        if not settles: return {'ok':False,'error':'우리 가게 판매로 확인되지 않았습니다'}
        if not broken: return {'ok':False,'error':'장부에 해당 주문의 고장 기록이 없습니다'}
        sealed=_json(c.get(BASE+'/api/ledger/sealed',params={'order':order},headers=headers))
        if isinstance(sealed,dict) and isinstance(sealed.get('data'),dict): sealed=sealed['data']
        if isinstance(sealed,dict) and isinstance(sealed.get('sealed'),dict): sealed=sealed['sealed']
        amount=sealed.get('amount') if isinstance(sealed,dict) else None
        tx_hash=sealed.get('tx_hash') if isinstance(sealed,dict) else None
        warranty=sealed.get('warranty_hours') if isinstance(sealed,dict) else None
        if not isinstance(amount,int) or amount<=0 or not tx_hash or not isinstance(warranty,(int,float)) or warranty<=0 or warranty>72:
            return {'ok':False,'error':'봉인 장부의 환불 조건을 확인할 수 없습니다'}
        settle=settles[-1]
        d=settle.get('data') or {}
        stamp=settle.get('ts',d.get('ts',settle.get('timestamp',d.get('timestamp'))))
        if stamp is None:
            return {'ok':False,'error':'판매 시각을 장부에서 확인할 수 없습니다'}
        stamp=float(stamp)
        if stamp>100000000000: stamp/=1000
        elapsed=(time.time()-stamp)/3600
        if elapsed<0 or elapsed>warranty:
            return {'ok':False,'error':'보증 기간이 지났거나 판매 시각이 유효하지 않습니다'}
        refunds=_read('logs/refunds.jsonl')
        sends=_read('logs/sends.jsonl')
        if any(x.get('order')==order and x.get('purpose')=='refund' for x in refunds+sends):
            return {'ok':False,'error':'이 주문에 이미 환불 송금 기록이 있어 중복 송금을 막았습니다'}
        tx=_rpc('eth_getTransactionByHash',[tx_hash])
        if not isinstance(tx,dict) or not tx.get('from'):
            return {'ok':False,'error':'결제 트랜잭션 발신 주소를 확인할 수 없습니다'}
        to=tx['from']
        if len(to)!=42 or not to.startswith('0x'):
            return {'ok':False,'error':'결제 발신 주소 형식이 올바르지 않습니다'}
        nonce=int(_rpc('eth_getTransactionCount',[kit.address('shop'),'pending']),16)
        os.makedirs('logs',exist_ok=True)
        record={'ts':int(time.time()),'purpose':'refund','order':order,'to':to,'amount':amount,'nonce':nonce}
        with open('logs/sends.jsonl','a',encoding='utf-8') as f:
            f.write(json.dumps(record,separators=(',',':'))+'\n')
        receipt=kit.pay(to,amount*10**15,'0x',role='shop')
        txid=receipt if isinstance(receipt,str) else str(receipt)
        done={'ts':int(time.time()),'purpose':'refund','order':order,'to':to,'amount':amount,'tx_hash':txid}
        with open('logs/sends.jsonl','a',encoding='utf-8') as f:
            f.write(json.dumps(done,separators=(',',':'))+'\n')
        with open('logs/refunds.jsonl','a',encoding='utf-8') as f:
            f.write(json.dumps(done,separators=(',',':'))+'\n')
        return {'ok':True,'order':order,'amount':amount,'tx_hash':txid}
