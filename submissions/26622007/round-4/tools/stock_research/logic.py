import httpx

def run(args, kit):
    base=kit.market.rstrip('/')
    h={'authorization':'Bearer '+kit.token('shop')}
    with httpx.Client(timeout=15,follow_redirects=True) as c:
        inv=c.get(base+'/api/inventory',headers=h).json()
        dem=c.get(base+'/api/demand').json()
        cat=c.get(base+'/api/catalog').json()
        prices=c.get(base+'/api/supply-prices').json()
    lim=max(1,min(60,int(args.get('limit',20))))
    demands=[]
    if isinstance(dem,list):
        for d in dem:
            demands.append({k:d.get(k) for k in ('category','item','kind','skus','median_price','shelf_hours','orders','rate','frequency','target_price') if k in d})
    elif isinstance(dem,dict): demands=dem
    targets=sorted(dem,key=lambda d:d.get('skus',0),reverse=True) if isinstance(dem,list) else []
    products=[]
    if isinstance(cat,list) and targets:
        for d in targets[:12]:
            matches=[x for x in cat if x.get('category')==d.get('category')]
            for x in matches[:3]:
                products.append({k:x.get(k) for k in ('sku','name','category','kind','list_price','shelf_hours')})
    elif isinstance(cat,list):
        products=[{k:x.get(k) for k in ('sku','name','category','kind','list_price','shelf_hours')} for x in cat[:lim]]
    return {'inventory':{k:inv.get(k) for k in ('round','balance','storage_due_with_next_lot','restock','surge','units')},'demand':demands,'products_sample':products[:lim],'supply_prices':prices}
