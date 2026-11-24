import httpx

def run(args, kit):
    base=kit.market.rstrip('/')
    headers={'authorization':'Bearer '+kit.token('shop')}
    paths=['/api/inventory','/api/demand','/api/restock','/api/supply-prices','/api/chain']
    out={}
    with httpx.Client(timeout=12,follow_redirects=True) as c:
        for p in paths:
            try:
                r=c.get(base+p,headers=headers if p=='/api/inventory' else {})
                try: body=r.json()
                except Exception: body=r.text[:3000]
                out[p]={'status':r.status_code,'body':body}
            except Exception as e: out[p]={'error':str(e)}
    return out
