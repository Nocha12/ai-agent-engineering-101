# food_margin_verdict (analysis tool, not part of the week-08 product)

Read-only verdict tool for the seller food-margin A/B (study food-margin-2040, salt food-margin-2040-f1,
W-20261109-2040-AX셀러전략): intention to treat per food unit supplied in the window, outcome from the public ledger
(settle, expire, broken), cost from units_in (or, for the one unit of a supply with no units_in row, the supply total minus the others), SKU x grade cluster bootstrap
stratified B - A (bootstrap and t intervals) with pending units as 0 and as -cost and a bound for units left out, and the
shop-level food net per hour against the same hours a day before (with a bound for unknown-cost sales).
The rules are in the module docstring. It is never imported by the market kit.

Run the tests in this folder: `python3 -m unittest test_food_margin_verdict`.
Fixture: fixture/real_* are rows copied as they are from 11/09 (our food units, their ledger supply / settle /
result / expire rows, units_in rows and shop-memory talks); fixture/units.json names the units the tests use; fixture/real_units_in_rest.jsonl holds the real units_in rows of the
other units of those supplies (one unit of the 20-unit G200e39a3 supply has none: the real one-missing case).

Pass the ledger files from the day before start through asof (the tool warns when they do not cover it).

    python3 -I food_margin_verdict.py <seller_memory_r3.json> <units_in.jsonl> <ledger 11-08 11-09 11-10 jsonl> \
        --study food-margin-2040 --salt food-margin-2040-f1 \
        --start 1794232549.604962 --stop 1794275749.604962 --asof <epoch>
