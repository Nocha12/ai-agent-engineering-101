def test(run, kit):
    if kit.testing:
        out=run({},kit)
        assert '/api/catalog' in out and '/api/inventory' in out
