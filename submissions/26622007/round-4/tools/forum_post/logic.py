import httpx

def run(args, kit):
    text = args.get('text')
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        return {"ok": False, "error": "text는 1~2000자여야 합니다"}
    if kit.testing:
        return {"ok": True, "post": {"id": 0, "text": text}}
    body = {"text": text}
    if args.get('thread') is not None:
        body['thread'] = args['thread']
    r = httpx.post(kit.market.rstrip('/') + '/api/forum', json=body,
                   headers={"authorization": "Bearer " + kit.token('shop')}, timeout=12)
    try:
        data = r.json()
    except Exception:
        data = {"body": r.text[:500]}
    return {"ok": r.is_success, "status": r.status_code, "result": data}
