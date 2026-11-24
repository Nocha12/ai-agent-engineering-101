import json, os, time

def test(run, kit):
    path = 'customers.test.json'
    try:
        try: os.remove(path)
        except FileNotFoundError: pass
        full = "A customer says it is 'Alice'"
        assert run({'op':'get','customer':full}, kit)['new'] is True
        assert run({'op':'update','customer':'Alice','fields':{'호칭':'Alice','선호 품목':['의자'],'고정 정보':{'사는 동네':'북촌','최근 산 품목':[{'name':'컵','grade':'A','shop':'다른 가게'}],'오늘 정보':'월요일·맑음','확인 시각':123}}}, kit)['ok']
        a = run({'op':'get','customer':full}, kit)
        assert a['방문 횟수'] == 1 and a['선호 품목 3개'] == ['의자']
        assert a['고정 정보']['사는 동네'] == '북촌' and a['고정 정보']['최근 산 품목'][0]['name'] == '컵'
        assert run({'op':'update','customer':full,'fields':{'고정 정보':{'사는 동네':'','최근 산 품목':[],'오늘 정보':'화요일·비','확인 시각':456}}}, kit)['ok']
        a = run({'op':'get','customer':'Alice'}, kit)
        assert a['고정 정보']['사는 동네'] == '북촌' and a['고정 정보']['최근 산 품목'][0]['name'] == '컵'
        assert a['고정 정보']['오늘 정보'] == '화요일·비' and a['고정 정보']['확인 시각'] == 456
        assert run({'op':'get','customer':'Bob'}, kit)['new'] is True
        assert run({'op':'update','customer':'Bob','fields':{'선호 품목':['탁자']}}, kit)['ok']
        b = run({'op':'get','customer':'Bob'}, kit)
        assert b['선호 품목 3개'] == ['탁자'] and a['선호 품목 3개'] == ['의자']
        with open(path, 'r', encoding='utf-8') as f: db = json.load(f)
        db['Alice']['마지막 방문 시각'] = time.time() - 1801
        with open(path, 'w', encoding='utf-8') as f: json.dump(db, f)
        assert run({'op':'get','customer':full}, kit)['방문 횟수'] == 2
        assert run({'op':'get','customer':'a person in your app'}, kit) == {'anonymous':True}
        assert run({'op':'get','customer':'Someone did not say who it is'}, kit) == {'anonymous':True}
        return True
    finally:
        try: os.remove(path)
        except FileNotFoundError: pass
