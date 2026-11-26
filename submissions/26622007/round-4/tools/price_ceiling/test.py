def test(run, kit):
    result = run({'sku': 'G00b8acf0', 'sell': 1000, 'cost': 574, 'unsold': 0, 'hold_hours': 0}, kit)
    assert result['max_buy_cost'] == 574 and result['min_sell_price'] == 999
    assert result['expected_profit'] == 0 and result['verdict'] == 'skip'
