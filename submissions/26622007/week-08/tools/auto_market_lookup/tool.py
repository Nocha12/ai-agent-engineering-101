import json, os, sys
import market_model

def rows(x):
    if isinstance(x, list): return x
    if isinstance(x, dict):
        for k in ("items", "catalog", "rows", "products", "data"):
            if isinstance(x.get(k), list): return x[k]
        return list(x.values())
    return []

def text(x):
    return json.dumps(x, ensure_ascii=False, default=str).lower()

def main():
    a = json.load(sys.stdin)
    md = os.environ["MARKET_DIR"]
    with open(os.path.join(md, "catalog.json"), encoding="utf-8") as f:
        catalog = rows(json.load(f))
    ledger = market_model.load_rows(md)
    q = str(a.get("query", "")).lower()
    c = str(a.get("category", "")).lower()
    s = str(a.get("shop", "")).lower()
    lim = max(1, min(100, int(a.get("limit", 20))))
    products = [r for r in catalog if not q or q in text(r)]
    skus = {str(r.get("sku", "")).lower() for r in products if isinstance(r, dict)}
    deals = []
    for r in ledger:
        if not isinstance(r, dict): continue
        sku = str(r.get("sku", "")).lower()
        category_ok = not c or c in str(r.get("category", "")).lower()
        query_ok = not q or q in text(r) or (sku and sku in skus)
        shop_ok = not s or s in str(r.get("shop", "")).lower() or s in str(r.get("buyer", "")).lower()
        if category_ok and query_ok and shop_ok: deals.append(r)
    out = {"catalog": products[:lim], "ledger": deals[-lim:][::-1]}
    print(json.dumps(out, ensure_ascii=False, default=str))

main()