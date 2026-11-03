# Current strategy

Terra reads this every hour and picks its change from the levers below. Astra rewrites it every twelve hours after
comparing two or three directions. Terra rewrites it only with a strategy shift (two cycles of evidence, see
meta/terra.md step 2). A person or the overnight GPT Pro review may also set it; the newest entry in History wins, and
their commits since Terra's last cycle (`git log`) say what moved and when to undo it.
Rewrite in place; keep this file short.

## Strategy: broad profit stock + proven mattresses (person and overnight GPT Pro review, 11/04 00:03-06:31)

- Product mix, two parts:
  - Breadth: restock.wanted ranks lines by profit per hour per won of stock, so the stock cost cap buys many SKUs in
    many categories (buyers who order a category see only shops stocking it). max_stock_cost 4,000,000 and
    keep_cash 4,500,000 (both hard_limits bounds; 21.9M of warranties were open at 05:00), food 450,000, at most 4
    units per SKU, max_sku_share 0.2.
  - Proven mattresses: the SKUs that sell out within minutes of each supply window carry the profit (11/04 07:20
    ledger: G1515d951 39 sales, 3,413,663 gross, 2,019,861 after 4 refunds; G6b9db161 26 sales, 2,078,215 after 1).
    restock.high_price_proven_skus (G1515d951, G6b9db161) up to high_price_proven_max_units 4, each capped by
    restock.sku_target_units (G1515d951 4, G6b9db161 2, G75f11c19 1); these and restock.priority_skus go first in the
    buy batch. Other goods above 100,000 only from those lists (high_price_listed_only), 2 units at most. Slow goods
    and SKUs whose extra unit stayed unsold twice are in restock.skip_skus (G0f415407, G9c02e68e, pillows G054ff120);
    G6b9db161 is skipped until its 2 held units sell at the capped asks.
- Positioning: honest grades with the market certificate, the ledger's honest sales as proof, a 6 h warranty on goods
  whose break risk allows it.
- Buyers reach the shop through: the card (built from current stock), shop posts on the forum, replies to "구합니다" posts.
- Negotiation: reserve = floor F and first ask P0 (profit_price.py, b = 15% priced into the floor), concessions
  [.6, .4, .2]; price.scarce_asks fixes G1515d951's first ask at 380,911; price.ask_caps caps the first ask of slow
  SKUs by grade.
- Buying: lines go out only within 50 s of a supply window opening (opening.WINDOW_LEAD), or the kit drops them.
- Why: the mattresses made most of the gross profit and sat out of stock between windows; breadth brings the visits
  the leaders have (07:01: our 1,060 visits against dae-yangkim's 2,115).

## Rules Terra applies (numbers quoted, one step per cycle)

- Break rate, per SKU: narrow a proven mattress (sku_target_units down one, then off the proven list) when its breaks
  within warranty exceed 15% of its sales older than 1 h (every break so far came 0.04-0.84 h after the sale) over at
  least 20 sales, or its net per sale after refunds over its last 10 sales is below 0. Narrow all mattresses to 1 unit
  when mattress breaks overall exceed 15%. One or two breaks in an hour are not a signal on their own: at ~10 sales an
  hour a 10% rate gives that often (11/04 03:08 and 03:34 on G1515d951, still +51,791 per sale at 07:20).
- Unsold: take a SKU's target down one when an extra unit is still unsold at the next window twice.
- scarce_asks: drop a SKU from it when its paid prices fall below those before the fixed ask, or its units stop selling
  before the next window.
- Widening: the stock and cash caps are at their bounds; widen by adding SKUs (restock.wanted, target units) only
  where matured (6 h past) warranty data shows net per sale above 0.

## Levers Terra pulls first

1. Which SKUs are stocked and how many (restock.wanted, sku_target_units, skip_skus): net per sale after refunds
   against sales while in stock.
2. Prices: P0 and floor F inputs, scarce_asks and ask_caps in pricing.json, inside their bounds.
3. Card and forum posts that bring buyers to SKUs in stock.

## Shift conditions (Terra shifts only when one holds two cycles in a row, numbers quoted)

- The shops ahead in profit (board.rivals) earn it with many more deals on SKUs or categories we do not stock, and our
  own paid deals per hour stay flat: shift towards stocking their best-selling SKUs at a margin our floor allows.
- Our visits rise but paid deals do not: the gap is negotiation or positioning, not stock; shift the levers there.
- Losses (expired, break_refund, grade_drop, storage) exceed the hour's contribution: shift to a narrower, faster mix.

## Experiment

None running yet; Astra's next cycle designs the first one.

## Baseline (board, 2026-11-04 07:01 KST)

Profit rank 7 (7,180,093, 492 deals, 1,060 visits). First: dae-yangkim 19,808,798 (407 deals, 2,115 visits).

## History

- 2026-11-03 22:40 set from the GPT Pro profit review (person): lean profit stock. Strategy check added to Terra.
- 2026-11-04 00:03-02:3x widened (person): stock cap by profit per won for breadth, 2 units of each proven mattress.
- 2026-11-04 03:3x-06:31 proven mattresses (person, overnight GPT Pro review mw0y87qc, review/ax-overnight-20261103
  0300-0600): G1515d951 to 4, G6b9db161 to 2 and skipped until sold, G0f415407/G9c02e68e off, slow goods stopped,
  keep_cash 4,500,000, first-ask caps, lines sent within 50 s of a window.
- 2026-11-04 07:4x G1515d951 kept at 4 on data (person's review W-20261104-0715-AXTerra): 39 sales, 4 breaks (10.3%,
  under the 15% priced in the floor; market 4.2%), net +2,019,861 after 1,393,802 refunds. The 03:58 Terra cut was
  refused by a test environment fault, not by its change. Watch: 2 of its 4 grade-B sales broke (A: 2 of 32).
