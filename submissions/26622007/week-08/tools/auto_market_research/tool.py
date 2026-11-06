import json, os, sys
import market_model

def main():
    a = json.load(sys.stdin)
    md = os.environ["MARKET_DIR"]
    limit = max(1, min(100, int(a.get("limit", 20))))
    category = str(a.get("category", "")).lower()
    query = str(a.get("query", "")).lower()
    shop = str(a.get("shop", "")).lower()
    def read(name, default):
        try:
            with open(os.path.join(md, name), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default
    catalog = read("catalog.json", [])
    if isinstance(catalog, dict):
        catalog = catalog.get("items", catalog.get("catalog", []))
    def val(x, *keys):
        return next((x[k] for k in keys if isinstance(x, dict) and k in x), "")
    matches = [x for x in catalog if category in str(val(x,"category")).lower() and query in (str(val(x,"sku")).lower()+" "+str(val(x,"name")).lower()+" "+str(val(x,"category")).lower())]
    skus = {str(val(x,"sku")) for x in matches}
    rows = market_model.load_rows(md)
    deals = [r for r in rows if str(val(r,"sku")) in skus and (not shop or shop in str(val(r,"shop","seller")).lower())]
    deals.sort(key=lambda r: val(r,"timestamp","time","created_at"), reverse=True)
    out = {"catalog_count":len(matches),"catalog":matches[:limit],"paid_deal_count":len(deals),"paid_deals":deals[:limit]}
    if a.get("include_purchases"):
        purchases = read("ledger_rows.jsonl", [])
        if isinstance(purchases, list):
            out["personal_history"] = [r for r in purchases if (not shop or shop in str(val(r,"shop","seller")).lower())][:limit]
        else:
            out["personal_history"] = []
    print(json.dumps(out, default=str))

main()