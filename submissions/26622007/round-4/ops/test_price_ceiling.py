import pytest

import price_ceiling as prices


@pytest.fixture(autouse=True)
def catalog(monkeypatch):
    monkeypatch.setattr(prices, '_signals', lambda sku, now: (8, 12, 0, 0, 0))
    monkeypatch.setattr(prices, 'DATA', {'items': {
        'water': ['water', 'food', 3000, 24, True, 1800, 1620, 3, 6],
        'lamp': ['lamp', 'goods', 10000, None, False, None, None, 0, 3]},
        'rates': {'food': [3, 6], 'goods': [0, 3]}, 'fallback': {'sell_list_multiplier': 0.60, 'cost_list_multiplier': 0.54},
        'sample': {'end': '2026-11-24T20:10:10+09:00'}})


def test_food_single_and_split_lot_at_fee_boundary():
    args = dict(sku='water', cost=574, sell=1000, unsold=0, hold_hours=0)
    assert prices.quote(args)['max_buy_cost'] == 574
    assert prices.quote(args)['expected_profit'] == 0
    assert prices.quote(args)['min_sell_price'] == 999
    assert prices.quote(dict(args, sell=998))['expected_profit'] == -1
    assert prices.quote(dict(args, qty=3, batch_size=2))['expected_profit'] == 206


@pytest.mark.parametrize(('hours', 'profit'), [(0, 7177.82), (47, 6707.82), (48, 5779.28), (96, 2951.9), (144, 2471.9)])
def test_goods_aging_and_storage(hours, profit):
    result = prices.quote(dict(sku='lamp', sell=10000, cost=2000, grade='A', hold_hours=hours, unsold=0))
    assert result['expected_profit'] == pytest.approx(profit)


def test_food_expiration_and_total_loss():
    args = dict(sku='water', sell=1000, cost=574, unsold=0, remaining_hours=2)
    assert prices.quote(dict(args, hold_hours=1.99))['expected_profit'] == 0
    for lost in [dict(args, hold_hours=2), dict(args, unsold=1)]:
        result = prices.quote(lost)
        assert (result['max_buy_cost'], result['min_sell_price'], result['expected_profit']) == (None, None, -780)
        assert result['verdict'] == 'skip'


def test_sale_below_server_fee_never_settles():
    result = prices.quote(dict(sku='water', sell=211, cost=0, unsold=0))
    assert result['expected_profit'] == -206
    assert result['max_buy_cost'] is None


def test_extra_lot_charges_apply_once_and_fractional_ceiling_rounds_down():
    args = dict(sku='water', sell=1000, cost=574, qty=2, batch_size=2, lot_dues=11, unsold=0)
    assert prices.quote(args)['max_buy_cost'] == 671
    assert prices.quote(dict(args, cost=671))['expected_profit'] == 1
    assert prices.quote(dict(args, cost=672))['expected_profit'] == -1


def test_default_table_flags_three_thousand_credit_staple():
    row, = prices.build_rows()
    assert row['risk'] is True and row['profit'] < 0
    assert row['unsold'] == 0


def test_velocity_zero_and_uncontested_demand(monkeypatch):
    args = dict(sku='water', cost=800, sell=1800, hold_hours=3, qty=2)
    assert prices.quote(args)['verdict'] == 'buy'
    monkeypatch.setattr(prices, '_signals', lambda sku, now: (0, 0, 50, 5, 0))
    result = prices.quote(args)
    assert (result['verdict'], result['recommended_qty']) == ('skip', 0)


def test_expiry_and_deadline_reduce_feasible_lot():
    args = dict(sku='water', cost=1050, sell=1800, hold_hours=5, qty=4, remaining_hours=10)
    assert prices.quote(args)['recommended_qty'] == 4
    for limit in ({'remaining_hours': 0.4}, {'round_hours': 0.4}):
        reduced = prices.quote(dict(args, **limit))
        assert reduced['verdict'] == 'skip' and reduced['recommended_qty'] == 1
    assert prices.quote(dict(args, round_hours=0))['recommended_qty'] == 0


def test_prior_stock_and_competitors_do_not_inflate_sales(monkeypatch):
    args = dict(sku='water', cost=500, sell=1800, qty=1, hold_hours=1)
    monkeypatch.setattr(prices, '_signals', lambda sku, now: (2, 1, 0, 0, 0))
    alone = prices._evaluate(args)
    monkeypatch.setattr(prices, '_signals', lambda sku, now: (2, 1, 9, 2, 0))
    contested = prices._evaluate(args)
    assert 0 < 1 - contested['unsold'] < 2 / 3
    assert contested['unsold'] > alone['unsold']
    monkeypatch.setattr(prices, '_signals', lambda sku, now: (2, 1, 9, 2, 2))
    assert prices.quote(args)['verdict'] == 'skip'


def test_action_guard_requires_both_actual_quotes_and_uses_stress_bounds():
    good = dict(sku='water', qty=1, batch_size=1, sell=1000, cost=490, unsold=0, grade='A', hold_hours=1, remaining_hours=12, round_hours=24, lot_dues=0)
    for action in ('buy', 'sell', 'price'):
        assert prices.price_guard(action, good)['expected_profit'] == 84
        with pytest.raises(ValueError):
            prices.price_guard(action, dict(good, cost=497))
        with pytest.raises(ValueError):
            prices.price_guard(action, dict(good, sell=980))
        with pytest.raises(ValueError):
            prices.price_guard(action, {'sku': 'water', 'verdict': 'buy'})


def test_guard_will_not_authorize_food_without_actual_shelf_life():
    with pytest.raises(ValueError, match='remaining_hours'):
        prices.price_guard('buy', dict(sku='water', qty=1, batch_size=1, sell=5000, cost=10, grade='A', hold_hours=2, round_hours=24, lot_dues=0))


def test_guard_never_treats_missing_dues_as_free():
    args = dict(sku='water', qty=1, batch_size=1, sell=1000, cost=490, unsold=0, grade='A', hold_hours=1, remaining_hours=12, round_hours=24)
    for action in ('buy', 'sell', 'price'):
        assert prices.price_guard(action, dict(args, lot_dues=0))
        with pytest.raises(ValueError):
            prices.price_guard(action, args)
        with pytest.raises(ValueError):
            prices.price_guard(action, dict(args, lot_dues=10))


def test_guard_does_not_allow_override_of_missing_deadline_or_zero_velocity(monkeypatch):
    args = dict(sku='water', qty=1, batch_size=1, sell=5000, cost=10, grade='A', hold_hours=2, remaining_hours=12, lot_dues=0, unsold=0)
    with pytest.raises(ValueError, match='round_hours'):
        prices.price_guard('buy', args)
    monkeypatch.setattr(prices, '_signals', lambda sku, now: (0, 0, 0, 0, 0))
    with pytest.raises(ValueError, match='skip'):
        prices.price_guard('buy', dict(args, round_hours=24))


def test_guard_requires_full_lot_and_uses_available_time_with_probability_cap(monkeypatch):
    monkeypatch.setattr(prices, '_signals', lambda sku, now: (4, 12, 0, 0, 0))
    args = dict(sku='water', sell=1800, cost=500, grade='B', batch_size=4, hold_hours=2, remaining_hours=1, round_hours=2, lot_dues=0)
    with pytest.raises(ValueError, match='qty'):
        prices.price_guard('buy', args)
    assert prices.price_guard('buy', dict(args, qty=1, unsold=0))['verdict'] == 'buy'


def test_recommendation_respects_integer_ceiling_with_fractional_cost(monkeypatch):
    monkeypatch.setattr(prices, '_signals', lambda sku, now: (0.9503, 12, 0, 0, 0))
    result = prices.quote(dict(sku='water', sell=1800, cost=500.5, hold_hours=1))
    assert (result['verdict'], result['recommended_qty']) == ('skip', 0)


def test_competitor_depletion_only_credited_in_base_case(monkeypatch):
    args = dict(sku='water', sell=1800, cost=500, qty=5, hold_hours=1)
    monkeypatch.setattr(prices, '_signals', lambda sku, now: (10, 12, 1, 1, 0))
    low = prices.quote(args)
    monkeypatch.setattr(prices, '_signals', lambda sku, now: (10, 12, 100, 1, 0))
    high = prices.quote(args)
    assert low['max_buy_cost'] > high['max_buy_cost']
    assert low['conservative_max_buy_cost'] == high['conservative_max_buy_cost']


def test_missing_or_stale_signals_explained_separately(monkeypatch):
    def stale(sku, now):
        raise ValueError('stale snapshot')
    monkeypatch.setattr(prices, '_signals', stale)
    result = prices.quote(dict(sku='water', sell=5000, cost=10))
    assert result['verdict'] == 'skip' and result['recommended_qty'] == 0 and '갱신' in result['reason']


def test_floor_is_reachable_within_the_input_domain():
    assert prices.quote(dict(sku='lamp', cost=10**12, grade='B', unsold=0))['min_sell_price'] is None


def test_minimum_is_first_profitable_price_not_a_monotonic_guarantee():
    args = dict(sku='lamp', cost=152, grade='C', hold_hours=0, unsold=0, action='sell')
    assert prices.quote(dict(args, sell=999))['min_sell_price'] == 999
    assert prices.quote(dict(args, sell=999))['expected_profit'] == 0.25
    assert prices.quote(dict(args, sell=1000))['expected_profit'] == -0.1


@pytest.mark.parametrize('inputs', [dict(unsold=float('nan')), dict(unsold=1.01), dict(qty=True),
                                    dict(batch_size=21), dict(hold_hours=-1), dict(sell=1.2), dict(grade='X')])
def test_invalid_input_rejects(inputs):
    with pytest.raises(ValueError):
        prices.quote(dict(sku='water', **inputs))
