import json

def test(run, kit):
    out=run({},kit)
    assert out['written'] and out['name']=='노차네 구멍가게'
    with open('card.json',encoding='utf-8') as f:
        card=json.load(f)
    assert card['name']=='노차네 구멍가게'
    assert card['description']=='동네 구멍가게입니다. 생수·우유·쌀·계란·면·과일 같은 먹을거리를 조금씩 들여 두고, 사장님이 손님 안부를 기억합니다. 유통기한·등급·가격은 정직하게 말씀드립니다.'
    assert '가구점' not in card['description'] and 'Furniture' not in card['name']
