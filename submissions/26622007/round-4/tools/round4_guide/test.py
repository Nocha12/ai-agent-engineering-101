def test(run, kit):
    out=run({'keyword':'checkout'},kit)
    assert out['status']==200 and out['found']
