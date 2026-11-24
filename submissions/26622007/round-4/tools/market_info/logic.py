import httpx

def run(args, kit):
    target = args.get('target', '문서')
    paths = {'문서':'/ROUND4.md', 'orders':'/entities/orders/.well-known/agent-card.json', 'clearing':'/entities/clearing/.well-known/agent-card.json', 'wholesale':'/entities/wholesale/.well-known/agent-card.json', 'forum':'/entities/forum/.well-known/agent-card.json', 'board':'/entities/board/.well-known/agent-card.json', 'bank':'/entities/bank/.well-known/agent-card.json', 'model':'/entities/model/.well-known/agent-card.json', 'directory':'/.well-known/agent-card.json'}
    if target not in paths: return {'error':'unknown target', 'targets':list(paths)}
    with httpx.Client(timeout=15, follow_redirects=True) as c:
        r=c.get(kit.market.rstrip('/')+paths[target])
        return {'target':target,'status':r.status_code,'body':r.text}
