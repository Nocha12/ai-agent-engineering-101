import json, os, market_model, sys

def text(v):
    return str(v or "").lower()

def main():
    a = json.load(sys.stdin)
    md = os.environ["MARKET_DIR"]
    with open(os.path.join(md, "catalog.json"), encoding="utf-8") as f:
        catalog = json.load(f)
    if isinstance(catalog, dict):
        catalog = catalog.get("items", catalog.get("catalog", []))
    rows = market_model.load_rows(md)
    query = text(a.get("query"))
    category = text(a.get("category"))
    shop = text(a.get("shop"))
    limit = max(1, min(int(a.get("limit", 20)), 100))
    matches = []
    for item in catalog if isinstance(catalog, list) else []:
        if not query or any(query in text(v) for v in item.values()):
            matches.append(item)
    deals = []
    for row in rows:
        if category and category not in text(row.get("category")):
            continue
        if shop and shop not in text(row.get("shop")):
            continue
        deals.append(row)
    deals.reverse()
    print(json.dumps({"catalog": matches[:limit], "catalog_count": len(matches), "ledger": deals[:limit], "ledger_count": len(deals)}))

main()