def test(run, kit):
    out = run({}, kit)
    assert '/ROUND4.md' in out
    assert out['/ROUND4.md'].get('status') == 200
