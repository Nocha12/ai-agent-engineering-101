import json, os, time

def run(args, kit):
    row = {
        "ts": int(time.time()),
        "customer": str(args.get("customer", "")),
        "item": str(args.get("item", "")),
        "offered_price": args.get("offered_price"),
        "grade_stock": str(args.get("grade_stock", "")),
        "wanted": str(args.get("wanted", "")),
        "last_message": str(args.get("last_message", "")),
        "reason": str(args.get("reason", "기타")),
    }
    os.makedirs("logs", exist_ok=True)
    with open("logs/lost_deals.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return {"ok": True, "recorded": True}
