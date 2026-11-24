"""Round 4 cash break-even quotes (no network, wallet or operating state)."""

import argparse
import json
import time
from functools import lru_cache
from pathlib import Path
from runpy import run_path


from fractions import Fraction
from math import ceil, expm1, floor, isfinite


FEE_DIVISOR = 100
MAX_PRICE = 10**12
FIXED_FEE = 210
GAS = 206
STORAGE = Fraction(5, 1000)
GRADE_HOURS = 48
DEFECT = {'A': Fraction(3, 100), 'B': Fraction(12, 100), 'C': Fraction(35, 100)}
FOOD_LIFE = {'A': (Fraction(80, 100), 1), 'B': (Fraction(45, 100), Fraction(80, 100)),
             'C': (Fraction(15, 100), Fraction(45, 100))}
STRESS = Fraction(1, 10)
PRIOR_SALES, PRIOR_HOURS = Fraction(1, 4), 1
SUGGEST_LIMIT = 4
DEFAULT_HOLD = 12
ALLOWED = {'sku', 'action', 'cost', 'sell', 'qty', 'batch_size', 'hold_hours', 'grade',
           'unsold', 'remaining_hours', 'round_hours', 'lot_dues'}


def _number(value, label, upper, integer=False):
    if type(value) not in (int, float) or not 0 <= value <= upper or not isfinite(value):
        raise ValueError('invalid ' + label)
    if integer and type(value) is not int:
        raise ValueError('integer required: ' + label)
    return value if integer else Fraction(str(value))


@lru_cache(maxsize=1)
def _demand_module():
    return run_path(str(Path(__file__).with_name('price_demand.py')))


def _signals(sku, now):
    return _demand_module()['signals'](sku, now)


def _scenario(kind, shelf, grade, hold, remaining, round_hours, unsold, demand, qty, action, stress):
    horizon = min(hold, round_hours) if round_hours is not None else hold
    defect, carrying = 0, 1
    if kind == 'food':
        low, high = FOOD_LIFE[grade]
        life = remaining if remaining is not None else shelf * (low if stress else (low + high) / 2)
        horizon = min(horizon, life)
    else:
        aged = 'ABC'[min(2, 'ABC'.index(grade) + int(hold // GRADE_HOURS))]
        defect, carrying = DEFECT[aged], 1 + STORAGE * hold
    if demand is None:
        sold = 1
    else:
        raw, count, units, shops, own = demand
        raw = Fraction(str(raw))
        speed = (count + PRIOR_SALES) / (count / raw + PRIOR_HOURS) if raw > 0 and count else 0
        market = speed * horizon
        shared = market / (shops + 1)
        capacity = shared / 2 if stress else max(shared, market - units)
        per_unit = max(0, capacity - (own if action == 'buy' else 0)) / qty
        sold = Fraction(str(-expm1(-float(per_unit)))) if stress else min(1, per_unit)
    if unsold is not None:
        conditional = max(0, 1 - unsold - (STRESS if stress else 0))
        possible = life > (0 if demand is not None else hold) if kind == 'food' else True
        sold = min(sold, conditional) if possible and (round_hours is None or round_hours > 0) else 0
    return sold, defect, carrying


def _bounds(cost, sell, sold, defect, carrying, fixed):
    def revenue(price):
        fee = price // FEE_DIVISOR + FIXED_FEE
        return sold * (price - fee - defect * (price + GAS)) if price >= fee else 0

    ceiling = floor((revenue(sell) - fixed) / carrying)
    outlay = cost * carrying + fixed
    minimum = None
    if sold:
        slope = 1 - Fraction(1, FEE_DIVISOR) - defect
        budget = outlay / sold + FIXED_FEE + defect * GAS
        minimum = max(0, floor((budget - 1) / slope))
        while revenue(minimum) < outlay:
            minimum += 1
        if minimum > MAX_PRICE:
            minimum = None
    return ceiling if ceiling >= 0 else None, minimum, revenue(sell) - outlay


def _evaluate(args, *, guard=False, now=None):
    if not isinstance(args, dict) or set(args) - ALLOWED:
        raise ValueError('unknown input')
    sku = args.get('sku')
    if not isinstance(sku, str) or sku not in DATA['items']:
        raise ValueError('unknown sku')
    name, kind, listed, shelf, essential, market_sell, market_cost, sales, supplied = DATA['items'][sku]
    fallback = DATA['fallback']
    sell_ratio = Fraction(str(fallback['sell_list_multiplier']))
    cost_ratio = Fraction(str(fallback['cost_list_multiplier']))
    sell = _number(args.get('sell', floor(market_sell) if market_sell is not None else floor(listed * sell_ratio)), 'sell', MAX_PRICE, True)
    cost = _number(args.get('cost', market_cost if market_cost is not None else ceil(listed * cost_ratio)), 'cost', MAX_PRICE)
    qty = _number(args.get('qty', 1), 'qty', 10**6, True)
    batch = _number(args.get('batch_size', 1), 'batch_size', 20, True)
    if not qty or not batch:
        raise ValueError('qty and batch_size must be positive')
    dues = _number(args.get('lot_dues', 0), 'lot_dues', 10**12, True)
    hold = _number(args.get('hold_hours', DEFAULT_HOLD), 'hold_hours', 10**6)
    round_hours = _number(args['round_hours'], 'round_hours', 10**6) if 'round_hours' in args else None
    remaining = args.get('remaining_hours')
    if remaining is not None:
        if kind != 'food':
            raise ValueError('remaining_hours is only for food')
        remaining = _number(remaining, 'remaining_hours', shelf)
    grade = args.get('grade', 'B')
    action = args.get('action', 'sell' if 'cost' in args and 'sell' not in args else 'buy')
    if grade not in DEFECT or action not in ('buy', 'sell'):
        raise ValueError('invalid grade or action')
    unsold = _number(args['unsold'], 'unsold', 1) if 'unsold' in args else None
    demand = None
    unavailable = False
    if unsold is None or guard:
        try:
            demand = _signals(sku, time.time() if now is None else now)
        except (ValueError, OSError, KeyError):
            unavailable, demand = True, (0, 0, 0, 0, 0)

    def scenario(quantity, stress):
        return _scenario(kind, shelf, grade, hold, remaining, round_hours, unsold, demand, quantity, action, stress)

    def bounds(quantity, stress):
        fixed = Fraction(((quantity + batch - 1) // batch) * GAS + dues, quantity)
        return _bounds(cost, sell, *scenario(quantity, stress), fixed)

    base, stress = bounds(qty, False), bounds(qty, True)
    maximum, minimum, profit = base
    tight_max, tight_min, tight_profit = stress
    def clears(limits):
        maximum, minimum, profit = limits
        return profit >= 0 and maximum is not None and minimum is not None and cost <= maximum and sell >= minimum

    ok = clears(stress)
    verdict = ('buy' if ok else 'skip') if action == 'buy' else ('sell_ok' if ok else 'too_low')
    candidates = []
    for quantity in range(1, min(qty, SUGGEST_LIMIT) + 1):
        limits = bounds(quantity, True)
        if clears(limits):
            candidates.append((limits[2] * quantity, -quantity))
    recommended = -max(candidates)[1] if candidates else 0
    reason = '보수 조건도 충족' if ok else '보수 손익분기 미달 또는 판매 불가'
    if unavailable:
        reason = '공유 수요 자료 누락·만료·오류: 갱신 전 거래 금지'
    elif demand is not None and not demand[0]:
        reason = '최근 해당 SKU의 유효 판매 속도 0'
    elif not ok and recommended:
        reason += f'; {recommended}개로 재조회'
    sell_source = '입력' if 'sell' in args else '실측' if sales else f'정가×{float(sell_ratio):.0%} 추정'
    cost_source = '입력' if 'cost' in args else '실측' if supplied else f'정가×{float(cost_ratio):.0%} 추정'
    sampled = DATA['sample']['end'][:16].replace('T', ' ')
    return dict(sku=sku, name=name, kind=kind, list_price=listed, sell=sell, sell_source=sell_source,
                cost=float(cost), cost_source=cost_source, unsold=float(1 - scenario(qty, False)[0]), ceiling=maximum,
                recommended_qty=recommended, conservative_profit=float(tight_profit * qty),
                floor=minimum, profit=round(float(profit * qty), 4), risk=not ok, conservative_ceiling=tight_max,
                conservative_floor=tight_min, verdict=verdict, reason=reason + f' (판매가 {sell_source}, 원가 {cost_source}; 표본 {sampled} KST)')


def quote(args, *, guard=False):
    """Compact advisory quote; verdict uses the stress case, profit is base-case per order."""
    row = _evaluate(args, guard=guard)
    return dict(max_buy_cost=row['ceiling'], min_sell_price=row['floor'], expected_profit=row['profit'],
                conservative_max_buy_cost=row['conservative_ceiling'], conservative_min_sell_price=row['conservative_floor'],
                recommended_qty=row['recommended_qty'], verdict=row['verdict'], reason=row['reason'])


def price_guard(action, args):
    """Require actual prices (not a caller-provided verdict) before buy/sell/price actions."""
    required = {'sell', 'cost', 'qty', 'batch_size', 'grade', 'hold_hours', 'round_hours', 'lot_dues'}
    if action not in ('buy', 'sell', 'price') or not isinstance(args, dict) or not required <= args.keys():
        raise ValueError('guard needs buy/sell/price and explicit ' + ', '.join(sorted(required)))
    item = DATA['items'].get(args.get('sku'))
    if item and item[1] == 'food' and args.get('remaining_hours') is None:
        raise ValueError('guard needs observed remaining_hours for food')
    result = quote(dict(args, action='buy' if action == 'buy' else 'sell'), guard=True)
    if result['verdict'] not in ('buy', 'sell_ok'):
        raise ValueError(result['verdict'] + ': ' + result['reason'])
    return result


CACHE_PATH = Path(__file__).with_name('price_ceiling_data.json')
DATA = json.loads(CACHE_PATH.read_text(encoding='utf8'))
if DATA.get('schema_version') != 2:
    raise ValueError('rebuild the public median cost cache before use')


def build_rows(qty=1, batch_size=1, hold_hours=DEFAULT_HOLD, unsold=None):
    params = dict(qty=qty, batch_size=batch_size, hold_hours=hold_hours)
    if unsold is not None:
        params['unsold'] = unsold
    return [_evaluate(dict(params, sku=sku)) for sku, item in DATA['items'].items() if item[4] and item[1] == 'food']


def ranked_rows(params, now=None):
    now = time.time() if now is None else now
    params = dict({'qty': SUGGEST_LIMIT, 'batch_size': SUGGEST_LIMIT}, **params)
    rows = []
    for sku, item in DATA['items'].items():
        if item[5] is None or item[6] is None:
            continue
        args = dict(params, sku=sku, action='buy')
        suggested = _evaluate(args, now=now)['recommended_qty']
        rows.append(_evaluate(dict(args, qty=suggested or 1), now=now))
    return sorted(rows, key=lambda row: (row['risk'], -row['conservative_profit'], row['sku']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--sku')
    mode.add_argument('--table', action='store_true')
    mode.add_argument('--top', type=int, help='rank SKUs with both observed prices by conservative lot profit')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--snapshot', action='store_true', help='replay --top only at the shared observation time; not a live quote')
    for key in ('sell', 'qty', 'batch-size', 'lot-dues'):
        parser.add_argument('--' + key, type=int)
    for key in ('cost', 'hold-hours', 'remaining-hours', 'round-hours', 'unsold'):
        parser.add_argument('--' + key, type=float)
    parser.add_argument('--grade', choices=list(DEFECT))
    parser.add_argument('--action', choices=['buy', 'sell'])
    args = vars(parser.parse_args())
    table, as_json, top, replay = args.pop('table'), args.pop('json'), args.pop('top'), args.pop('snapshot')
    params = {key: value for key, value in args.items() if value is not None}
    try:
        if not DATA['items']:
            raise ValueError('public price cache missing or empty')
        if replay and top is None:
            raise ValueError('--snapshot requires --top')
        if top is not None:
            if top < 1 or set(params) - {'qty', 'batch_size', 'hold_hours', 'round_hours', 'grade', 'lot_dues'}:
                raise ValueError('--top requires positive N and observed, model-based prices')
            result = ranked_rows(params, _demand_module()['market_as_of']() if replay else None)[:top]
            table = True
        else:
            result = ([_evaluate(dict(params, sku=sku)) for sku, item in DATA['items'].items() if item[4] and item[1] == 'food']
                      if table else quote(params))
    except ValueError as exc:
        parser.error(str(exc))
    if as_json or not table:
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=None if as_json else 2))
    else:
        print('SKU\t품목\t정가\t판매가\t판매가 출처\t원가\t원가 출처\t매입상한\t최소판매가\t보수상한\t보수하한\t건당기대이익\t권장수량\t판정')
        for row in result:
            print(*(row[key] for key in ('sku', 'name', 'list_price', 'sell', 'sell_source', 'cost', 'cost_source', 'ceiling',
                                         'floor', 'conservative_ceiling', 'conservative_floor', 'profit', 'recommended_qty', 'verdict')), sep='\t')


if __name__ == '__main__':
    main()
