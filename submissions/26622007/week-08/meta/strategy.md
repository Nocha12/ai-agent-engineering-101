# Current strategy

Terra reads this every hour and picks its change from the levers below. Astra rewrites it every twelve hours after
comparing two or three directions. Terra rewrites it only with a strategy shift (two cycles of evidence, see
meta/terra.md step 2). A person or the overnight GPT Pro review may also set it; the newest entry in History wins.
Rewrite in place; keep this file short.

## Strategy: lean profit stock (GPT Pro profit review, review/W-20261103-2120/gpt-pro-answer1.md)

- Product mix: only SKUs the ledger shows selling, at most 4 units per SKU and 2 of goods listed above 100,000 (the
  proven mattresses, so they are not out of stock between supply windows); food kept small (food stock cost cap) because
  it expires.
- Positioning: honest grades with the market certificate, the ledger's honest sales as proof, a short warranty on goods
  whose break risk allows it.
- Buyers reach the shop through: the card (built from current stock), shop posts on the forum, replies to "구합니다" posts.
- Negotiation: first ask P0 and floor F from profit_price.py, concessions [.6, .4, .2], refuse deals whose break risk is
  too high for a warranty.
- Turnover: eligible stock first, then first-expiring first; the stock cost cap counts stock held + lines on their way +
  new lines.
- Why: the opening (three windows) lost money on stock that did not sell; a small stock of proven sellers protects the
  balance while the shop learns which SKUs pay.

## Levers Terra pulls first

1. Which SKUs are stocked (restock.wanted: expected net per unit against sales while in stock).
2. Prices: P0 and floor F inputs in pricing.json, inside their bounds.
3. Stock cost cap, widened only on the conditions written in meta/terra.md.
4. Card and forum posts that bring buyers to SKUs in stock.

## Shift conditions (Terra shifts only when one holds two cycles in a row, numbers quoted)

- The shops ahead in profit (board.rivals) earn it with many more deals on SKUs or categories we do not stock, and our
  own paid deals per hour stay flat: shift towards stocking their best-selling SKUs at a margin our floor allows.
- Our visits rise but paid deals do not: the gap is negotiation or positioning, not stock; shift the levers there.
- Losses (expired, break_refund, grade_drop, storage) exceed the hour's contribution: shift to a narrower, faster mix.

## Experiment

None running yet; Astra's next cycle designs the first one.

## Baseline (board, 2026-11-03 22:50 KST)

Profit rank 12 of 80 (87,988). First: dae-yangkim 4,079,126.

## History

- 2026-11-03 22:40 set from the GPT Pro profit review (person). Strategy check added to Terra, strategy choice to Astra.
- 2026-11-04 02:3x kept: repeat food + mattresses/bedding, now 2 units of each proven mattress (overnight GPT Pro review,
  review/ax-overnight-20261103/0200). Measure in the next hour: second units sold before the next window, stock-out
  minutes of the three mattresses, refunds after 05:04 (the first mattress sale's warranty end).
