def test(run, kit):
    out=run({'target':'orders'},kit)
    assert out['target']=='orders' and out['status']==200
    out=run({'target':'nonsense'},kit)
    assert out.get('error')=='unknown target'
