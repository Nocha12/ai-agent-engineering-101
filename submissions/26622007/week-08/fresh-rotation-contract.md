# Fresh order hash slot candidate

Source policy: `buyer_fresh_rotation.py`: ENABLED=False, START=0, END=0,
STUDY=fresh-uuid-slot-v1. No environment switch. Independent files under
`<market-dir>/buyer_fresh_rotation/<sha256([study,order])>/`.
Do not activate with B1, quote alignment, goods-first-one, extra-quote,
firm, first95 or cap95 windows. Source overlap guard excludes these live windows;
parent must also exclude any newly introduced study before activation.
No activation, market access or effect estimate was performed locally.

## Policy and population

Goods only. Reserve ITT before catalog/product selection, using full canonical
order UUID or genuine o-12hex key (UUID5 allocation, never reconstructed history).
SHA256(study:allocationUUID) chooses equally probable A/B. Finite samples need
not balance. Immutable assignment fixes retries. Old/terminal/resumed input is
excluded. Window is positive, at most 1800s; invocation must start in it.
No assignment on missing identity, future invocation, conflict, IO failure or OFF.

A keeps original full quote order. B swaps its first fresh shop with one
SHA256(order-key)-indexed shop in that same fit×rival partition. Exactly one swap;
same member selection is a no-op. Fitted and other rival partitions retain their
slots. Quota, input pool, selected_offer, price, stock/warranty checks and all
negotiation/payment/hold policies are unchanged. Only actual prefetch can apply
the swap. Later batches use the same complete reordered pool without extra quota.
Common eligibility means unequal SETS of first k-1 members. No membership
difference remains ITT/no-op. Unknown/oversized snapshot fails to baseline;
unknown is never a recent inquiry or evidence of unavailable stock.

## Integrated measurement contract

All files schema=1, bound to order, study, arm. assignment.json is ITT,
before product selection, including no-stock/hopeless/no-attempt outcomes.
decision.json is immutable proposal, not proof of dispatch. plan retains exact
full baseline shop objects, original fits, fit×rival partitions, rivals, k,
baseline/treatment full order and hashes, first memberships and common eligible.
The snapshot is frozen before worker mutation; no truncated proxy/history join.
Changing retry plan cannot authorize B. A failed or timed-out write cannot
authorize treatment even if its late immutable proposal appears on disk.

attempt-UUID.json is recorded ONLY after Thread.start succeeds: study, arm,
order, shop, SKU, thread_started_at and boundary=quote_thread_started, phase.
Its at field is journal creation time; thread_started_at is captured directly
after successful start. This proves thread admission, not RPC/ACK/paid. Missing
witness is unknown, flagged in orders.jsonl fresh_rotation.witness_failures.
start_failure records thread creation/start failure; attempted flag is reclaimed.
terminal-UUID.json carries state and passed_to_kit; handoff is not payment.
Killed processes/exception paths may lack terminal: missing remains unknown.
No acceptedquotes ordering may be used as dispatch order.

Reuse generic _io/_once/_bytes/_hash from stock helper only; B1 active rules,
availability logic, journal and study are untouched. Finite nonblocking slot
admission, 150ms IO deadline, 256KiB encoding limit, immutable atomic link.
Thread failure releases IO slots; late IO cannot change caller scheduling.

Collector is an external parent responsibility, not a new generic collector:
enumerate study assignment IDs for ITT, then join decision/attempt/terminal and
official payments+settled+refund by exact order and SKU. Distinguish proposal,
thread admission, ACK, paid, unknown; preserve paidFalse versus missing row.
Retain cohort start/prefix coverage, maturation boundary and source timestamps.
No timestamp may be inferred from order terminal or sorted acceptedquotes.
Report both ITT and common-eligible, no-op counts and missing witness/terminal;
official served-nothing/refund/value definitions apply. Historical 71/37 orders
remain unknown baseline and supply no new-policy effect estimate.

## Validation and rollback

Synthetic anonymized fixtures only; no new live snapshot is claimed. Runtime
will persist a complete prospective snapshot when separately activated.
Target seams: reserve, plan, decide, start_quotes. Tests cover deterministic
retry, OFF/END/future/invalid/conflict, membership/no-op, partition and quota,
IO exception/timeout/late publication and IO Thread.start slot recovery.
Parent performs independent review, changed-file security/gitleaks and host
full checks. Local targeted checks do not establish live safety or superiority.

Rollback: keep/set ENABLED=False, START=0, END=0 and release through parent gate.
For source rollback use git revert of this candidate's commits (preserve history).
Retain all evidence; never remove/reuse B1 or other study journals.
