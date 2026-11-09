# food_margin_verdict (analysis tool, not part of the week-08 product)

Read-only verdict tool for the seller food-margin A/B (study food-margin-2040, salt food-margin-2040-f1,
W-20261109-2040-AX셀러전략): intention to treat per food unit supplied in the window, outcome from the public ledger
(settle, expire, broken), cost from units_in (or the supply row where that is exact), SKU x grade cluster bootstrap
B - A with pending units as 0 and as -cost, and the shop-level food net per hour against the same hours a day before.
The rules are in the module docstring. It is never imported by the market kit.

Run the tests in this folder: `python3 -m unittest test_food_margin_verdict`.
Fixture: fixture/real_* are rows copied as they are from 11/09 (our food units, their ledger supply / settle /
result / expire rows, units_in rows and shop-memory talks); fixture/units.json names the units the tests use.

    python3 -I food_margin_verdict.py <seller_memory_r3.json> <units_in.jsonl> <ledger 11-09 11-10 jsonl> \
        --study food-margin-2040 --salt food-margin-2040-f1 \
        --start 1794232549.604962 --stop 1794275749.604962 --asof <epoch>
