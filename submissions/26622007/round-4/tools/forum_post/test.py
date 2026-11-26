def test(run, kit):
    out = run({"text": "동네 식품 안내"}, kit)
    assert out.get('ok') is True
    assert out['post']['text'] == '동네 식품 안내'
    assert run({"text": ""}, kit).get('ok') is False
