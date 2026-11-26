import httpx

def run(args, kit):
    params = {"limit": max(1, min(100, int(args.get("limit", 100))))}
    if args.get("since") is not None:
        params["since"] = args["since"]
    r = httpx.get(kit.market.rstrip("/") + "/api/forum", params=params, timeout=12)
    r.raise_for_status()
    data = r.json()
    return {"ok": True, "posts": data if isinstance(data, list) else data.get("posts", data)}
