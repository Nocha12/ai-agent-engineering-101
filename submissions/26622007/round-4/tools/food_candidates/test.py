def test(run, kit):
    r=run({},kit)
    assert 'candidates' in r and 'balance' in r
    assert all(not x['surge'] and x['list_price']>3000 for x in r['candidates'])
