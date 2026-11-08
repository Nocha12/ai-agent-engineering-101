import json, os, sys, market_model

def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default

def rows(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for k in ("items", "rows", "catalog", "posts"):
            if isinstance(value.get(k), list):
                return value[k]
        return list(value.values())
    return []

def main():
    a = json.load(sys.stdin)
    md = os.environ["MARKET_DIR"]
    limit = max(1, min(100, int(a.get("limit", 20))))
    category = str(a.get("category", "")).casefold()
    query = str(a.get("query", "")).casefold()
    catalog = rows(read_json(os.path.join(md, "catalog.json"), []))
    ledger = market_model.load_rows(md)
    forum = rows(read_json(os.path.join(md, "forum.json"), []))
    def matches(row):
        text = json.dumps(row, ensure_ascii=False, default=str).casefold()
        return (not category or category in text) and (not query or query in text)
    cm = [x for x in catalog if matches(x)]
    lm = [x for x in ledger if matches(x)]
    fm = [x for x in forum if not query or query in json.dumps(x, ensure_ascii=False, default=str).casefold()]
    print(json.dumps({"catalog_count": len(cm), "catalog": cm[:limit], "ledger_count": len(lm), "ledger": lm[:limit], "forum": fm[-limit:]}, ensure_ascii=False, default=str))

main()