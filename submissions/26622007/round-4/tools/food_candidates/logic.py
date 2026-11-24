import httpx

def run(args, kit):
    base=kit.market.rstrip('/')
    h={'authorization':'Bearer '+kit.token('shop')}
    with httpx.Client(timeout=12, follow_redirects=True) as c:
        cat=c.get(base+'/api/catalog').json()
        prices=c.get(base+'/api/supply-prices').json()
        inv=c.get(base+'/api/inventory',headers=h).json()
        dem=c.get(base+'/api/demand').json()
    surge=set()
    if isinstance(prices,dict):
        rows=prices.get('surge',[])
        if isinstance(rows,list): surge={str(x.get('sku')) for x in rows if isinstance(x,dict)}
    wanted=('생수','우유','두유','쌀','계란','국수','면','과일')
    out=[]
    if isinstance(cat,list):
        for x in cat:
            if not isinstance(x,dict) or x.get('kind')!='food': continue
            text=str(x.get('name',''))+' '+str(x.get('category',''))
            if not any(w in text for w in wanted): continue
            p=x.get('list_price')
            if not isinstance(p,(int,float)) or p<=3000: continue
            out.append({'sku':x.get('sku'),'name':x.get('name'),'category':x.get('category'),'list_price':p,'shelf_hours':x.get('shelf_hours'),'surge':str(x.get('sku')) in surge})
    out.sort(key=lambda x:(x['surge'], -x['list_price']))
    return {'balance':inv.get('balance'),'restock':inv.get('restock'),'surge_skus':sorted(surge),'candidates':out[:80]}
