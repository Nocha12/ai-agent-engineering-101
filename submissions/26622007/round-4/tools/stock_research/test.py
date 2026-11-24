def test(run, kit):
    out=run({'limit':5},kit)
    assert 'inventory' in out and 'demand' in out
