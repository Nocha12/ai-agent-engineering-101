import json, os, sys
import market_model

a=json.load(sys.stdin)
limit=max(1,min(100,int(a.get('limit',20))))
md=os.environ['MARKET_DIR']
def read_json(name, default):
    try:
        with open(os.path.join(md,name),encoding='utf-8') as f: return json.load(f)
    except (OSError,ValueError): return default
forum=read_json('forum.json',[])
if isinstance(forum,dict):
    forum=forum.get('posts',forum.get('items',[]))
if not isinstance(forum,list): forum=[]
rows=market_model.load_rows(md)
if not isinstance(rows,list): rows=[]
def newest(items):
    def stamp(x):
        if isinstance(x,dict):
            for k in ('timestamp','ts','created_at','time','created'):
                v=x.get(k)
                if isinstance(v,(int,float)): return v
        return -1
    return sorted(items,key=stamp,reverse=True)[:limit]
print(json.dumps({'forum_posts':newest(forum),'ledger_rows':newest(rows)},default=str))