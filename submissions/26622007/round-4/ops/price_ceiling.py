"""Round 4 cash break-even quotes (no network, wallet or operating state)."""

import argparse
import json
from pathlib import Path


from fractions import Fraction
from math import ceil, floor, isfinite


FEE_DIVISOR = 100
FIXED_FEE = 210
GAS = 206
STORAGE = Fraction(5, 1000)
GRADE_HOURS = 48
DEFECT = {'A': Fraction(3, 100), 'B': Fraction(12, 100), 'C': Fraction(35, 100)}
FOOD_LIFE = {'A': (Fraction(80, 100), 1), 'B': (Fraction(45, 100), Fraction(80, 100)),
             'C': (Fraction(15, 100), Fraction(45, 100))}
STRESS = Fraction(1, 10)
ALLOWED = {'sku', 'action', 'cost', 'sell', 'qty', 'batch_size', 'hold_hours', 'grade',
           'unsold', 'remaining_hours', 'lot_dues'}


def _number(value, label, upper, integer=False):
    if type(value) not in (int, float) or not 0 <= value <= upper or not isfinite(value):
        raise ValueError('invalid ' + label)
    if integer and type(value) is not int:
        raise ValueError('integer required: ' + label)
    return value if integer else Fraction(str(value))


def _scenario(kind, shelf, grade, hold, remaining, unsold, stress):
    if stress:
        unsold = min(1, unsold + STRESS)
    if kind == 'food':
        low, high = FOOD_LIFE[grade]
        life = remaining if remaining is not None else shelf * (low if stress else (low + high) / 2)
        return (1 - unsold if life > hold else 0), 0, 1
    aged = 'ABC'[min(2, 'ABC'.index(grade) + int(hold // GRADE_HOURS))]
    return 1 - unsold, DEFECT[aged], 1 + STORAGE * hold


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
    return ceiling if ceiling >= 0 else None, minimum, revenue(sell) - outlay


def _evaluate(args):
    if not isinstance(args, dict) or set(args) - ALLOWED:
        raise ValueError('unknown input')
    sku = args.get('sku')
    if not isinstance(sku, str) or sku not in DATA['items']:
        raise ValueError('unknown sku')
    name, kind, listed, shelf, essential, market_sell, market_cost, sales, supplied = DATA['items'][sku]
    fallback = DATA['fallback']
    sell_ratio = Fraction(str(fallback['sell_list_multiplier']))
    cost_ratio = Fraction(str(fallback['cost_list_multiplier']))
    sell = _number(args.get('sell', floor(market_sell) if market_sell is not None else floor(listed * sell_ratio)), 'sell', 10**12, True)
    cost = _number(args.get('cost', market_cost if market_cost is not None else ceil(listed * cost_ratio)), 'cost', 10**12)
    qty = _number(args.get('qty', 1), 'qty', 10**6, True)
    batch = _number(args.get('batch_size', 1), 'batch_size', 20, True)
    if not qty or not batch:
        raise ValueError('qty and batch_size must be positive')
    dues = _number(args.get('lot_dues', 0), 'lot_dues', 10**12, True)
    fixed = Fraction(((qty + batch - 1) // batch) * GAS + dues, qty)
    hold = _number(args.get('hold_hours', 1), 'hold_hours', 10**6)
    remaining = args.get('remaining_hours')
    if remaining is not None:
        if kind != 'food':
            raise ValueError('remaining_hours is only for food')
        remaining = _number(remaining, 'remaining_hours', shelf)
    grade = args.get('grade', 'B')
    action = args.get('action', 'sell' if 'cost' in args and 'sell' not in args else 'buy')
    if grade not in DEFECT or action not in ('buy', 'sell'):
        raise ValueError('invalid grade or action')
    observed_sales, observed_supply = DATA['rates'][kind]
    unsold = (_number(args['unsold'], 'unsold', 1) if 'unsold' in args else
              1 - min(Fraction(observed_sales, observed_supply), 1) if observed_supply else Fraction(1))
    base, stress = [_bounds(cost, sell, *_scenario(kind, shelf, grade, hold, remaining, unsold, mode), fixed)
                    for mode in (False, True)]
    maximum, minimum, profit = base
    tight_max, tight_min, tight_profit = stress
    ok = tight_profit >= 0 and tight_max is not None and tight_min is not None and cost <= tight_max and sell >= tight_min
    verdict = ('buy' if ok else 'skip') if action == 'buy' else ('sell_ok' if ok else 'too_low')
    reason = '보수 조건도 충족' if ok else '보수 손익분기 미달 또는 판매 불가'
    sell_source = '입력' if 'sell' in args else '실측' if sales else f'정가×{float(sell_ratio):.0%} 추정'
    cost_source = '입력' if 'cost' in args else '실측' if supplied else f'정가×{float(cost_ratio):.0%} 추정'
    sampled = DATA['sample']['end'][:16].replace('T', ' ')
    return dict(sku=sku, name=name, kind=kind, list_price=listed, sell=sell, sell_source=sell_source,
                cost=float(cost), cost_source=cost_source, unsold=float(unsold), ceiling=maximum,
                floor=minimum, profit=round(float(profit * qty), 4), risk=not ok, conservative_ceiling=tight_max,
                conservative_floor=tight_min, verdict=verdict, reason=reason + f' (판매가 {sell_source}, 원가 {cost_source}; 표본 {sampled} KST)')


def quote(args):
    """Compact advisory quote; verdict uses the stress case, profit is base-case per order."""
    row = _evaluate(args)
    return dict(max_buy_cost=row['ceiling'], min_sell_price=row['floor'], expected_profit=row['profit'],
                conservative_max_buy_cost=row['conservative_ceiling'], conservative_min_sell_price=row['conservative_floor'],
                verdict=row['verdict'], reason=row['reason'])


def price_guard(action, args):
    """Require actual prices (not a caller-provided verdict) before buy/sell/price actions."""
    if action not in ('buy', 'sell', 'price') or not isinstance(args, dict) or not {'sell', 'cost', 'grade', 'hold_hours'} <= args.keys():
        raise ValueError('guard needs buy/sell/price and explicit sell, cost, grade and hold_hours')
    item = DATA['items'].get(args.get('sku'))
    if item and item[1] == 'food' and args.get('remaining_hours') is None:
        raise ValueError('guard needs observed remaining_hours for food')
    result = quote(dict(args, action='buy' if action == 'buy' else 'sell'))
    if result['verdict'] not in ('buy', 'sell_ok'):
        raise ValueError(result['verdict'] + ': ' + result['reason'])
    return result


CACHE_PATH = Path(__file__).with_name('price_ceiling_data.json')
DATA = json.loads(CACHE_PATH.read_text(encoding='utf8'))


def build_rows(qty=1, batch_size=1, hold_hours=1, unsold=None):
    params = dict(qty=qty, batch_size=batch_size, hold_hours=hold_hours)
    if unsold is not None:
        params['unsold'] = unsold
    return [_evaluate(dict(params, sku=sku)) for sku, item in DATA['items'].items() if item[4] and item[1] == 'food']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--sku')
    mode.add_argument('--table', action='store_true')
    parser.add_argument('--json', action='store_true')
    for key in ('sell', 'qty', 'batch-size', 'lot-dues'):
        parser.add_argument('--' + key, type=int)
    for key in ('cost', 'hold-hours', 'remaining-hours', 'unsold'):
        parser.add_argument('--' + key, type=float)
    parser.add_argument('--grade', choices=list(DEFECT))
    parser.add_argument('--action', choices=['buy', 'sell'])
    args = vars(parser.parse_args())
    table, as_json = args.pop('table'), args.pop('json')
    params = {key: value for key, value in args.items() if value is not None}
    try:
        if not DATA['items']:
            raise ValueError('public price cache missing or empty')
        result = ([_evaluate(dict(params, sku=sku)) for sku, item in DATA['items'].items() if item[4] and item[1] == 'food']
                  if table else quote(params))
    except ValueError as exc:
        parser.error(str(exc))
    if as_json or not table:
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=None if as_json else 2))
    else:
        print('SKU\t품목\t정가\t판매가\t판매가 출처\t원가\t원가 출처\t매입상한\t최소판매가\t보수상한\t보수하한\t건당기대이익\t판정')
        for row in result:
            print(*(row[key] for key in ('sku', 'name', 'list_price', 'sell', 'sell_source', 'cost', 'cost_source', 'ceiling',
                                         'floor', 'conservative_ceiling', 'conservative_floor', 'profit', 'verdict')), sep='\t')


if __name__ == '__main__':
    main()
