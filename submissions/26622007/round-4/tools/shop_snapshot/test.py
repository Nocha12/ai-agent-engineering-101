def test(run, kit):
    out=run({},kit)
    assert '/api/inventory' in out and '/api/demand' in out
