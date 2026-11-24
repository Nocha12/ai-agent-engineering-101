def test(run, kit):
    assert run({},kit)['ok'] is False
    assert run({'order':' '},kit)['error']=='order is required'
    if kit.testing:
        out=run({'order':'test-order'},kit)
        assert out['ok'] is False and 'testing mode' in out['error']
