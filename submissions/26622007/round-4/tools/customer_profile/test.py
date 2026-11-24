import httpx

def test(run, kit):
    ledger = httpx.get(kit.market.rstrip('/') + '/api/ledger?after=-500', timeout=12).json()
    rows = ledger if isinstance(ledger, list) else ledger.get('ledger', ledger.get('rows', []))
    buyer = next(row['data']['buyer'] for row in rows if isinstance(row, dict) and row.get('kind') == 'settle' and isinstance(row.get('data'), dict) and row['data'].get('buyer'))
    result = run({'customer': buyer}, kit)
    assert result.get('found') is True
    assert isinstance(result.get('최근 산 품목'), list) and len(result['최근 산 품목']) <= 3
    assert all(set(item) == {'name','grade','shop'} for item in result['최근 산 품목'])
    assert result['최근 산 품목']
    return True