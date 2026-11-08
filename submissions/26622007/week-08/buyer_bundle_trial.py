"""Source-OFF goods bundle: one extra bounded counter and payment-aware scheduling.

The observed .952 final/first-ask ratio is not a baseline parameter. Baseline
oneshot accepts a counter within its value limit. This treatment instead offers
.92 of the first ask once after .90, then accepts only within that lower cap.
Unparsed turns retain the existing harness path.
"""

ENABLED = False
START = 0
END = 0
STUDY = 'goods-counter-payment-bundle'


def counter(baseline, ask, history, limit, steps):
    if (steps != (.9,) or not isinstance(baseline, dict) or
            baseline.get('action') != 'offer' or type(ask) is not int or
            not 0 < ask <= 999999999999 or type(limit) is not int or limit <= 0):
        return baseline
    asks = [price for who, price in history if who == 'shop asked']
    offers = [price for who, price in history if who == 'you offered']
    if not asks or not offers or any(type(p) is not int or p <= 0 for p in asks + offers):
        return baseline  # the original first .90 offer is unchanged
    cap = min(limit, asks[0] * 92 // 100)
    if ask <= cap:
        return {**baseline, 'price': min(ask, limit)}
    if len(offers) == 1 and offers[-1] < cap:
        return {**baseline, 'price': cap}
    return {**baseline, 'action': 'walk_away', 'price': 0}
