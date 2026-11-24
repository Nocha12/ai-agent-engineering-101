import json, os, time

def test(run, kit):
    path = 'customers.test.json'
    try:
        try: os.remove(path)
        except FileNotFoundError: pass
        full = "A customer says it is 'Alice'"
        assert run({'op':'get','customer':full}, kit) == {'new':True}
        assert run({'op':'update','customer':'Alice','fields':{'호칭':'Alice','선호 품목':['의자']}}, kit)['ok']
        a = run({'op':'get','customer':full}, kit)
        assert a['방문 횟수'] == 1 and a['선호 품목 3개'] == ['의자']
        assert run({'op':'get','customer':'Bob'}, kit) == {'new':True}
        assert run({'op':'update','customer':'Bob','fields':{'선호 품목':['탁자']}}, kit)['ok']
        b = run({'op':'get','customer':'Bob'}, kit)
        assert b['선호 품목 3개'] == ['탁자'] and a['선호 품목 3개'] == ['의자']
        with open(path, 'r', encoding='utf-8') as f: db = json.load(f)
        db['Alice']['마지막 방문 시각'] = time.time() - 1801
        with open(path, 'w', encoding='utf-8') as f: json.dump(db, f)
        assert run({'op':'get','customer':'Alice'}, kit)['방문 횟수'] == 2
        assert run({'op':'get','customer':'a person in your app'}, kit) == {'anonymous':True}
        assert run({'op':'get','customer':'Someone did not say who it is'}, kit) == {'anonymous':True}
        return True
    finally:
        try: os.remove(path)
        except FileNotFoundError: pass
