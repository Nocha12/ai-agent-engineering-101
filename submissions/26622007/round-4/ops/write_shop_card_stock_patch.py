import sys
d=sys.argv[1]
t=open(d+"/logic.py",encoding="utf-8").read()
old="""    card={"""
new="""    desc='동네 구멍가게입니다. 생수·우유·쌀·계란·면·과일 같은 먹을거리를 조금씩 들여 두고, 사장님이 손님 안부를 기억합니다. 유통기한·등급·가격은 정직하게 말씀드립니다.'
    stock=' '.join(str(args.get('stock') or '').split())[:200]
    if stock:
        desc=desc+' 지금 재고(등급별 가격): '+stock
    card={"""
assert t.count(old)==1; t=t.replace(old,new,1)
old2="""      'description':'동네 구멍가게입니다. 생수·우유·쌀·계란·면·과일 같은 먹을거리를 조금씩 들여 두고, 사장님이 손님 안부를 기억합니다. 유통기한·등급·가격은 정직하게 말씀드립니다.',"""
assert t.count(old2)==1; t=t.replace(old2,"      'description':desc,")
open(d+"/logic.py","w",encoding="utf-8").write(t)
s=open(d+"/test.py",encoding="utf-8").read().rstrip("\n")
s+="""
    out=run({'stock':'계란 20구 C 7,200 · B 10,100 / 쌀 20kg B 61,000'},kit)
    with open('card.json',encoding='utf-8') as f:
        card=json.load(f)
    assert card['description'].endswith('지금 재고(등급별 가격): 계란 20구 C 7,200 · B 10,100 / 쌀 20kg B 61,000')
    assert 'Furniture' not in card['description'] and '가구점' not in card['description']
    run({},kit)
"""
open(d+"/test.py","w",encoding="utf-8").write(s)
p=open(d+"/permission.yml",encoding="utf-8").read()
assert "args 없음." in p
p=p.replace("args 없음.","args {stock?}: 지금 재고 품목·등급별 가격 한 줄(200자 이내)을 설명 끝에 붙입니다. 재고가 바뀔 때만 다시 씁니다.")
open(d+"/permission.yml","w",encoding="utf-8").write(p)
import types,os
os.makedirs(d+"/run",exist_ok=True); os.chdir(d+"/run")
ns={}; exec(open("../logic.py").read(),ns); ts={}; exec(open("../test.py").read(),ts)
k=types.SimpleNamespace(shop_url="https://x.example/a2a",testing=True)
print("TEST", ts["test"](ns["run"],k))
