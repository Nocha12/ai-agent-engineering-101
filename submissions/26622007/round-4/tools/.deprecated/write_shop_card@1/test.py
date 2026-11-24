def test(run, kit):
    out=run({},kit)
    assert out['written'] and out['name']=='Nocha Furniture'
