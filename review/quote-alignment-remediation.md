# Quote alignment remediation handoff
Owner 4c84784c, 2026-11-07; sole quote-align worktree, initial clean HEAD 47f1512.
Parent owns independent re-review, integration, activation and actual 30 minute AB.
Latest directive 24 checked. Prior aa6701/2125558f/6dcb8953 ledger completed; child list empty.
Owned paths: buyer_quote_alignment*, market_buyer quote/write metadata only,
tools/quote_alignment_measure.py, test_quote_alignment*, this handoff. No operation changes.

## DEBUG REPORT
Symptom: journal flock/fsync blocks decision lock; post-IO expiry/OFF ignored;
empty turn map produces false zero harness.
Root cause: synchronous durable IO nested in quote_ctl lock; validation happens only
before IO; all(empty) with zero defaults treats absence as observed zero.
Fix: bounded (150ms) daemon append outside decision lock, max 8 outstanding workers.
Reservation bounded too. Worker has no publication capability. Recheck current
source/environment study/window after IO; under decision lock recheck closed and
actual clock. Keep legacy when timed out/closed/OFF/changed study/expired.
Evidence: original independent review/probes reproduced original 3 defects.
Regression test: test_quote_alignment_remediation covers indefinite append,
actual flock, process exit with stranded writer, closed lock interleaving,
post-IO expiry/OFF/study, source bootstrap, sparse turns.
Status: locally implemented; independent review/live AB pending.

## Audit / ITT
A timed-out IO worker may finish writing later; it cannot publish a price.
Quote journal rows are proposals with applied=false, intended_applied and an
orders.quote_alignment_applied confirmation marker. Actual publication is added
under decision lock and serialized in final orders record. Measurement joins
that record for new-schema applied counts; preexisting historical rows retain
legacy applied semantics. Missing order is not confirmed publication. Assignment
rows remain ITT even when reservation later times out/falls back. If process exit
loses a pending assignment, missing started-order reconciliation is mandatory;
do not claim a complete randomized cohort from journal alone. A process killed
during write can leave a partial line: malformed input invalidates audit.
No timeout kills an OS fsync; daemons ensure it cannot hold process exit.
Initial directory-entry power-loss durability remains unverified.

Writer buyer.talk initializes sparse turns and increments take/fast/code/harness.
Nonempty known counters, finite nonnegative integers, accept absent branches as0.
Empty/unknown-key/invalid maps remain missing, not savings.

## Source-only bootstrap and rollback (NOT executed live)
buyer_quote_alignment_policy.py is a fixed local source file, read as bounded
AST literal assignments, never executed. No dynamic path from an order.
Default SOURCE_CONTROL=False, ENABLED=False, START=0, STUDY empty: no activation
without existing explicit valid environment policy. To activate without restarting
kit, parent prepares/reviews an atomic source-policy replacement:
SOURCE_CONTROL=True, ENABLED=True, START=<fresh future epoch>, STUDY=<new ASCII id>.
Each order's market_buyer imports/reserves using the file; existing quote readers
reread it after IO. No environment reload or shell export claim.
Rollback: atomic source-policy replacement SOURCE_CONTROL=True, ENABLED=False
retaining START/STUDY, preserving journals. Restoring SOURCE_CONTROL=False alone
is not a guaranteed kill if inherited environment is enabled.
Source OFF is sampled at reservation and post-IO validation. Quotes validated
before OFF can already be published/negotiated; it does not undo offers, receipts,
payments or stop other strategies. A file change after the last sample is not
synchronously serialized with quote publication; END/closed are rechecked under
the decision lock. Parent must describe this sampled kill contract accurately.
No CLI activation helper or guessed deploy command. Parent uses its reviewed
atomic source deployment mechanism, fresh start, seller no-overlap and gates.

## Integration / unchanged policies
Do not fast-forward candidate over production 2dc9b0b blindly. Reconcile latest
production first in authorized worktree; preserve restock sku_target_units=2
and metadata/seller changes. Only quote-owned files may deploy. Do not overwrite
dirty AGENTS/log or seller files. No push/restart/env/config reads/AB here.
11im food first1, positive-price/cap/6h, unknownquoted fallback and extra3s False
untouched. Goods-first1 not newly adopted. No zero-price trial.
16:53 sufficient grammar mechanism != historic inner branch; 16:58 root cause
not equated. No economic/Players effect asserted.

## Review scope
Applied gstack-investigate (root cause/interleaving), mp-tdd (authorized public
quote/measurement seams, RED/GREEN), gstack-review (self-review of callers,
bounds, audit schema), work-review. This is NOT an independent review nor a full
gstack multi-review contract. Security static self-review: no network/credential
or shell-execution addition, fixed source path, literal-only policy, no text in
journal. Independent code/security re-review remains parent's required gate.
Original conditions: injected indefinite journal IO, post-IO OFF/END, missing turns.
Tests: fake network/order fixtures plus real local flock and process exit. Live
market timing, actual runtime source rollout, power loss and AB effect unverified.

리뷰 결과: 위험도 중 · 기존 Important3 로컬 보완 · 독립 재리뷰 미실시
검증: 최종 HEAD 영수증은 중앙 quote-align-remediation.md 참조; live AB 미검증
보안: 자체 정적점검 완료; 독립 보안리뷰 부모 후속
정정 대조: 가격/cap/6h/11im/3s/unknownquoted 유지; 비식품첫1원 신규채택 없음
되돌리는 법: source authoritative OFF; 운영 후보 미적용. 코드 역패치도 원장 보존
