
def test(run, kit):
    out = run({'top': 3}, kit)
    assert isinstance(out, dict) and isinstance(out['items'], list) and 'as_of' in out
    for item in out['items']:
        assert item['tx'] >= 1 and item['buy_qty'] >= 0 and item['price'][0] <= item['price'][1] <= item['price'][2]
    try:
        run({'wallet': 'x'}, kit)
    except ValueError:
        pass
    else:
        raise AssertionError('unknown args must be refused')
