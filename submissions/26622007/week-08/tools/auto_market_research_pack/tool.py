import json, os, market_model, sys

def rows(x):
    if isinstance(x, list): return x
    if isinstance(x, dict):
        for k in ('items','rows','products','posts','inventory'):
            if isinstance(x.get(k), list): return x[k]
        return [dict(v, _key=k) if isinstance(v, dict) else {'_key':k,'value':v} for k,v in x.items()]
    return []

def text(x):
    return json.dumps(x, ensure_ascii=False, default=str).lower()

def main(a):
    md=os.environ['MARKET_DIR']
    def read(name):
        with open(os.path.join(md,name), encoding='utf-8') as f: return json.load(f)
    q=str(a.get('query','')).strip().lower(); cat=str(a.get('category','')).strip().lower()
    n=max(1,min(100,int(a.get('limit',20))))
    catrows=rows(read('catalog.json'))
    def match(r):
        t=text(r)
        return (not q or q in t) and (not cat or cat in t)
    found=[r for r in catrows if match(r)][:n]
    skus={str(r.get('sku','')).lower() for r in found if isinstance(r,dict) and r.get('sku')}
    ledger=market_model.load_rows(md)
    deals=[]
    for r in reversed(ledger):
        if not isinstance(r,dict): continue
        t=text(r); sku=str(r.get('sku','')).lower()
        if (not q or q in t or sku in skus) and (not cat or cat in t) and (not skus or not sku or sku in skus): deals.append(r)
        if len(deals)>=n: break
    try:
        forum=rows(read('forum.json'))
        forum_available=True
    except FileNotFoundError:
        # Forum snapshots are optional, including in the sandbox self-test.
        forum=[]
        forum_available=False
    posts=[r for r in reversed(forum) if not q or q in text(r) or (cat and cat in text(r))][:n]
    inv=read('inventory.json'); invrows=rows(inv)
    stock=[r for r in invrows if not skus or (isinstance(r,dict) and str(r.get('sku','')).lower() in skus)][:n]
    out={'catalog_matches':found,'catalog_match_count':sum(1 for r in catrows if match(r)), 'ledger_matches_recent':deals,'forum_posts_recent':posts,'forum_snapshot_available':forum_available,'inventory_matches':stock}
    if a.get('include_purchases'):
        out['purchase_history_note']='Use my_purchases for authoritative personal purchase history.'
    print(json.dumps(out,ensure_ascii=False,default=str))

a=json.load(sys.stdin)
main(a)
