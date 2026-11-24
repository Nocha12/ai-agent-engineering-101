from fractions import Fraction

import pytest

import price_ceiling as prices


@pytest.fixture(autouse=True)
def catalog(monkeypatch):
    monkeypatch.setattr(prices, '_signals', lambda sku, now: (8, 12, 0, 0, 0))
    shared = prices._demand_module()
    monkeypatch.setitem(shared, 'load_cache', lambda: {'as_of': 1, 'last_seq': 7})
    monkeypatch.setitem(shared, 'signals_from', lambda cache, sku, now: prices._signals(sku, now))
    monkeypatch.setattr(prices, 'DATA', {'items': {
        'water': ['water', 'food', 3000, 24, True, 1800, 1620, 3, 6],
        'lamp': ['lamp', 'goods', 10000, None, False, None, None, 0, 3]},
        'rates': {'food': [3, 6], 'goods': [0, 3]}, 'fallback': {'sell_list_multiplier': 0.60, 'cost_list_multiplier': 0.54},
        'sample': {'end': '2026-11-24T20:10:10+09:00'}})


def current_state(sku):
    return dict(sku=sku, complete=True, round=4, observed_at=prices.time.time(), last_seq=7,
                own_units=0, competitor_units=0, competitor_shops=0)


def test_food_single_and_split_lot_at_fee_boundary():
    args = dict(sku='water', cost=574, sell=1000, unsold=0, hold_hours=0)
    assert prices.quote(args)['max_buy_cost'] == 574
    assert prices.quote(args)['expected_profit'] == 0
    assert prices.quote(args)['min_sell_price'] == 999
    assert prices.quote(dict(args, sell=998))['expected_profit'] == -1
    assert prices.quote(dict(args, qty=3, batch_size=2))['expected_profit'] == 206


@pytest.mark.parametrize(('hours', 'profit'), [(0, 7187.12), (47, 6717.12), (48, 5816.48), (96, 3060.4), (144, 2580.4)])
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
    args = dict(sku='water', cost=750, sell=1800, hold_hours=5, qty=4, remaining_hours=10)
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
        assert prices.price_guard(action, good, read_buy_state=current_state)['expected_profit'] == 84
        with pytest.raises(ValueError):
            prices.price_guard(action, dict(good, cost=497), read_buy_state=current_state)
        with pytest.raises(ValueError):
            prices.price_guard(action, dict(good, sell=980), read_buy_state=current_state)
        with pytest.raises(ValueError):
            prices.price_guard(action, {'sku': 'water', 'verdict': 'buy'})


def test_guard_will_not_authorize_food_without_actual_shelf_life():
    with pytest.raises(ValueError, match='remaining_hours'):
        prices.price_guard('buy', dict(sku='water', qty=1, batch_size=1, sell=5000, cost=10, grade='A', hold_hours=2, round_hours=24, lot_dues=0))


def test_guard_never_treats_missing_dues_as_free():
    args = dict(sku='water', qty=1, batch_size=1, sell=1000, cost=490, unsold=0, grade='A', hold_hours=1, remaining_hours=12, round_hours=24)
    for action in ('buy', 'sell', 'price'):
        assert prices.price_guard(action, dict(args, lot_dues=0), read_buy_state=current_state)
        with pytest.raises(ValueError):
            prices.price_guard(action, args)
        with pytest.raises(ValueError):
            prices.price_guard(action, dict(args, lot_dues=10), read_buy_state=current_state)


def test_guard_does_not_allow_override_of_missing_deadline_or_zero_velocity(monkeypatch):
    args = dict(sku='water', qty=1, batch_size=1, sell=5000, cost=10, grade='A', hold_hours=2, remaining_hours=12, lot_dues=0, unsold=0)
    with pytest.raises(ValueError, match='round_hours'):
        prices.price_guard('buy', args)
    monkeypatch.setattr(prices, '_signals', lambda sku, now: (0, 0, 0, 0, 0))
    with pytest.raises(ValueError, match='skip'):
        prices.price_guard('buy', dict(args, round_hours=24), read_buy_state=current_state)


def test_guard_requires_full_lot_and_uses_available_time_with_probability_cap(monkeypatch):
    monkeypatch.setattr(prices, '_signals', lambda sku, now: (4, 12, 0, 0, 0))
    args = dict(sku='water', sell=1800, cost=500, grade='B', batch_size=4, hold_hours=2, remaining_hours=1, round_hours=2, lot_dues=0)
    with pytest.raises(ValueError, match='qty'):
        prices.price_guard('buy', args)
    assert prices.price_guard('buy', dict(args, qty=1, unsold=0), read_buy_state=current_state)['verdict'] == 'buy'


def test_recommendation_respects_integer_ceiling_with_fractional_cost():
    result = prices.quote(dict(sku='water', sell=1001, cost=496.5, unsold=0))
    assert (result['verdict'], result['recommended_qty']) == ('skip', 0)


def test_conservative_share_does_not_promise_one_sale_at_one_expected_arrival(monkeypatch):
    monkeypatch.setattr(prices, '_signals', lambda sku, now: (3.25, 3, 0, 0, 0))
    result = prices.quote(dict(sku='water', sell=1800, cost=1000, hold_hours=1))
    assert result['expected_profit'] > 0 and result['verdict'] == 'skip'


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


def test_guard_and_ranking_never_override_peer_zero_evidence_limit(monkeypatch):
    monkeypatch.setattr(prices, '_signals', lambda sku, now: (2, 1, 0, 0, 0))
    args = dict(sku='water', qty=1, batch_size=1, sell=5000, cost=100, grade='A', hold_hours=12, remaining_hours=15, round_hours=12, lot_dues=0)
    assert prices.quote(args)['recommended_qty'] == 0
    with pytest.raises(ValueError, match='한도 0개'):
        prices.price_guard('buy', args, read_buy_state=current_state)


def test_live_own_stock_pending_and_new_ledger_head_block_buy():
    args = dict(sku='water', qty=1, batch_size=1, sell=5000, cost=10, grade='A', hold_hours=1, round_hours=12, remaining_hours=12, lot_dues=0)
    assert prices.price_guard('buy', args, read_buy_state=current_state)['verdict'] == 'buy'
    with pytest.raises(ValueError):
        prices.price_guard('buy', args)
    for changed in (dict(own_units=10), dict(complete=False), dict(last_seq=8), dict(observed_at=1), dict(competitor_units=1)):
        with pytest.raises(ValueError):
            prices.price_guard('buy', args, read_buy_state=lambda sku: dict(current_state(sku), **changed))


def test_floor_is_reachable_within_the_input_domain():
    assert prices.quote(dict(sku='lamp', cost=10**12, grade='B', unsold=0))['min_sell_price'] is None


def test_minimum_is_first_profitable_price_not_a_monotonic_guarantee(monkeypatch):
    args = dict(sku='lamp', cost=152, grade='C', hold_hours=0, unsold=0, action='sell')
    # net refund: 0.65 * (880 - 218) - 0.35 * 206 = 358.2 >= 152 + 206; net receipt never falls as price rises
    assert prices.quote(dict(args, sell=880))['min_sell_price'] == 880
    assert prices.quote(dict(args, sell=880))['expected_profit'] == 0.2
    assert prices.quote(dict(args, sell=879))['expected_profit'] == -0.45
    assert prices.quote(dict(args, sell=900))['expected_profit'] == prices.quote(dict(args, sell=899))['expected_profit']
    monkeypatch.setattr(prices, 'REFUND_BASIS', 'gross')
    assert prices.quote(dict(args, sell=999))['min_sell_price'] == 999
    assert prices.quote(dict(args, sell=999))['expected_profit'] == 0.25
    assert prices.quote(dict(args, sell=1000))['expected_profit'] == -0.1


def test_refund_is_shop_net_receipt():
    assert prices.REFUND_BASIS == 'net'
    assert prices.refund_amount(10000) == 10000 - (100 + 210)
    assert prices.refund_amount(999) == 999 - 219


@pytest.mark.parametrize('basis', ['net', 'gross'])
@pytest.mark.parametrize(('defect', 'carrying', 'cost', 'fixed', 'sold'), [
    (Fraction(3, 100), Fraction(1), 152, 206, Fraction(1)),        # goods grade A
    (Fraction(12, 100), Fraction(106, 100), 2000, 206, Fraction(1, 2)),  # goods grade B, 12h storage
    (Fraction(35, 100), Fraction(1), 152, 206, Fraction(1)),       # goods grade C
    (Fraction(35, 100), Fraction(124, 100), 7777, Fraction(422, 3), Fraction(3, 4)),
    (0, 1, 574, 206, Fraction(1)),                                 # food
    (0, 1, 10**6, Fraction(206 + 11, 2), Fraction(2, 3))])          # food, lot dues
def test_floor_is_smallest_break_even_price(monkeypatch, basis, defect, carrying, cost, fixed, sold):
    monkeypatch.setattr(prices, 'REFUND_BASIS', basis)
    minimum = prices._bounds(cost, 0, sold, defect, carrying, fixed)[1]
    profit = lambda price: prices._bounds(cost, price, sold, defect, carrying, fixed)[2]
    assert minimum is not None and profit(minimum - 1) < 0 <= profit(minimum)
    assert all(profit(price) < 0 for price in range(max(0, minimum - 300), minimum))


@pytest.mark.parametrize('inputs', [dict(unsold=float('nan')), dict(unsold=1.01), dict(qty=True),
                                    dict(batch_size=21), dict(hold_hours=-1), dict(sell=1.2), dict(grade='X')])
def test_invalid_input_rejects(inputs):
    with pytest.raises(ValueError):
        prices.quote(dict(sku='water', **inputs))
