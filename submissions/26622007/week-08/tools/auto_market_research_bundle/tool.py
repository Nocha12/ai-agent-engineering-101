import json, os, sys
import market_model

def main():
    a = json.load(sys.stdin)
    md = os.environ["MARKET_DIR"]
    def read(name, default):
        try:
            with open(os.path.join(md, name), encoding="utf-8") as f: return json.load(f)
        except (OSError, ValueError): return default
    catalog = read("catalog.json", [])
    if isinstance(catalog, dict): catalog = catalog.get("items", catalog.get("catalog", []))
    q = str(a.get("query", "")).lower(); cat = str(a.get("category", "")).lower()
    lim = max(1, min(100, int(a.get("limit", 20))))
    def get(x, *keys):
        return next((x[k] for k in keys if isinstance(x, dict) and k in x), "")
    def match(x):
        return (not q or q in " ".join(str(get(x,k)) for k in ("sku","name","category","kind")).lower()) and (not cat or cat in str(get(x,"category")).lower())
    items = [x for x in catalog if match(x)]
    sku_cats = {str(get(x,"sku")): str(get(x,"category")) for x in items}
    rows = market_model.load_rows(md)
    seen=set(); deals=[]
    for r in rows:
        if str(get(r,"status","type")).lower() not in ("paid","settled","settle","sale"): continue
        sku=str(get(r,"sku")); category=sku_cats.get(sku, str(get(r,"category")))
        if sku not in sku_cats or (cat and cat not in category.lower()): continue
        if q and q not in (sku+" "+category).lower(): continue
        shop=str(get(r,"shop","seller"))
        if a.get("shop") and str(a["shop"]).lower() not in shop.lower(): continue
        key=(sku, str(get(r,"buyer")), shop, str(get(r,"timestamp","time")), str(get(r,"price")))
        if key in seen: continue
        seen.add(key); deals.append(r)
    deals.sort(key=lambda r: get(r,"timestamp","time"), reverse=True)
    out={"catalog_count":len(items),"catalog":items[:lim],"deal_count":len(deals),"deals":deals[:lim]}
    if a.get("include_purchases"):
        buys=[]
        for r in rows:
            buyer=str(get(r,"buyer")); shop=str(get(r,"shop","seller"))
            if a.get("shop") and str(a["shop"]).lower() not in shop.lower(): continue
            if str(get(r,"status","type")).lower() in ("paid","settled","settle","sale") and buyer: buys.append(r)
        out["purchases_count"]=len(buys); out["purchases"]=buys[:lim]
    print(json.dumps(out, ensure_ascii=False))

main()