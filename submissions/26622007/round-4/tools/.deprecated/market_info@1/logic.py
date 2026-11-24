import httpx

def run(args, kit):
    base = kit.market.rstrip('/')
    paths = ['/ROUND4.md', '/.well-known/agent-card.json', '/entities/orders/.well-known/agent-card.json', '/entities/clearing/.well-known/agent-card.json', '/entities/wholesale/.well-known/agent-card.json', '/entities/forum/.well-known/agent-card.json', '/entities/board/.well-known/agent-card.json', '/entities/bank/.well-known/agent-card.json', '/entities/model/.well-known/agent-card.json']
    out = {}
    with httpx.Client(timeout=12, follow_redirects=True) as c:
        for p in paths:
            try:
                r = c.get(base + p)
                out[p] = {'status': r.status_code, 'body': r.text[:18000]}
            except Exception as e:
                out[p] = {'error': str(e)}
    return out
