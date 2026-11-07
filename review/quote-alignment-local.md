# Quote alignment local candidate

Owner: local candidate run a60255eac65c, 2026-11-07 KST. Base origin/week-08
af229f3cd22e03fcfb1d31de1403c5edea21ebe6. Own branch ax/quote-align-local-1107;
parent resume worktree remains untouched. Checked worktree list, bounded task ledger
(lines 779–789 relevant entries), process names/PIDs only, and work-state rev21.
Other owners retain operations and other strategies; this run owns quote alignment,
its tests, offline measurement and this explicitly requested review artifact only.
The review/ path is explicitly authorized by the brief; product edits stay inside
submissions/26622007/week-08. No push, runtime, config, env, secrets, restart or AB run.

## Root cause before edits

Root cause: buyer.py ASK_IN_TEXT/text_ask accepts asking, not Our ask. In
market_buyer.py:672–687 text_ask_checked immediately drops a missing legacy read;
quote:740–751 stores ask=None. oneshot.py:69–76 requires positive quoted on the
first text turn before reading strict ask_read. This is a safe fallback caused by
reader coverage mismatch, not a strict-parser defect.

Evidence: audit-1628-root-cause.md, log 166112/166120, stored quote null,
8510 strict read and quoted=None versus quoted=8510 pure reproduction. Its
subsequent 7680 ask still exceeded limit7447; saving one harness call does not prove
a deal or Players improvement. fallback-1432-root-cause.md establishes a different
late quote path: selected response arrived after decision, resume absent, safe
unknownquoted first-turn gate. That path must remain unchanged.

16:53 separate evidence: audit-1655.md/json log190746/190764–765, stored quote null,
Our ask142800, limit117599, subsequent lowest133520, canceled. Local reproduction
will test the same observed grammar (grade B, 506h, list168000). This demonstrates
a sufficient current-code mechanism, not the unrecorded historical inner branch;
contemporaneous process locals/source snapshot are unavailable here.

Approved TDD seams: buyer quote handling through quote() with network/time/file
boundaries faked, and offline read-only journal measurement through its public CLI.
One slice at a time. No live market/model execution.

Environment: requirements.txt pinned a2a-sdk1.2.2/httpx0.28.1/cryptography50.0.2/
uvicorn0.54.0; fresh /tmp/ax-quote-candidate-venv, Python3.15.1, no borrowed
node_modules/submodules needed. Dependencies installed there. Week-08 README and
check_week08.py absent in this base; use complete unittest discovery, syntax and
diff checks, with exact commands/receipts reported after final commit.

Relevant incident lessons: operate only in own worktree; receipt price is not paid
settlement; join payments and settled and retain unresolved outcomes. Skills read:
investigate, mp-tdd and gstack-review (full specified files), work-review, delegation
and coding-skills. No telemetry/upgrades/config writes. Independent review belongs
to parent and is pending; self review cannot replace it.

## Candidate contract and parent runbook (not executed)

Single variable: only if the original quote has no metadata ask and legacy
buyer.text_ask itself returns None, arm B can use ask_read.read. A runs legacy
unchanged while journaling shadow opportunities. Do not rescue a legacy number
rejected by its bounds/SKU guard. Require positive integer, no previous-offer
numbers, existing 5%..100% list bounds, no foreign SKU, no quote refusal,
INPUT_REQUIRED, no receipt artifacts, and no late/closed publication. The
quote_ctl lock serializes late checking and publication. Metadata precedence,
first-turn quoted equality, later-turn fallback, 6h receipt/warranty and price
limits remain unchanged. Changed source consumers use existing ask_src=text.
The additional read can alter ranking, selected SKU/shop and early quote completion;
it is not merely a harness optimization. No price-policy constants were edited.

Default flag: BUYER_QUOTE_ALIGN is absent/0 => no assignment or extra reading.
Parent-only future parameters: BUYER_QUOTE_ALIGN=1,
BUYER_QUOTE_ALIGN_START=<fresh finite epoch>,
BUYER_QUOTE_ALIGN_STUDY=<unique ASCII letters/digits/underscore/hyphen, <=64>.
END is exactly START+1800, [START,END); no default start/study and no auto-restart.
Order arm is SHA256(study+":"+order) first-byte parity, 50/50 nominal, not forced
balance. All Round3 starting orders are assigned before catalog filtering and
product/shop selection, including no-stock/no-opportunity/error outcomes. The
append-only journal is <market-dir>/quote_alignment.jsonl, protected by flock,
flush and fsync. Journal errors keep legacy behavior. No text, credentials, URLs,
receipts or environment values enter the new journal. Do not edit old logs.

Before starting, parent must complete independent review, validate exact deployed
sources and fresh test receipts, stop overlapping first1/extra-quote/opening trials,
preserve 11im food-only default and existing seller strategy, verify no simultaneous
pricing/restart changes, and acquire its operational gate. This candidate does
not turn off existing experiments or change their files; the base contains the
expired goods-first1 pilot and must not be mistaken for a new adopted default.
Current candidate needs no runtime action. Enabling requires parent authorization.

Kill switch: parent sets BUYER_QUOTE_ALIGN=0 through its authorized mechanism.
read_quote checks it again for in-flight quotes; after END no additional quote
is applied, even for an already assigned order. Already published quotes/talks
continue under existing guards; switching off cannot undo earlier selection.
Code rollback: revert candidate feature commits on the integration branch in
reverse order after parent review, preserving unrelated commits and journals.
No reset/force push. Default OFF alone is sufficient to disable the added read.

Measure at END+300 or later, using explicit read-only snapshots and fresh output:

```sh
python tools/quote_alignment_measure.py --start START --end END --asof ASOF \
  --study STUDY --journal SNAPSHOT/quote_alignment.jsonl \
  --orders SNAPSHOT/orders.jsonl --payments SNAPSHOT/payments.jsonl \
  --settled SNAPSHOT/settled.jsonl -o NEW_REPORT.json
```

Report both arms by reservation/start ITT, not completed-time cohort or only
selected opportunities. Metrics: all assignments; missing outcomes/payment,
observed paid+settled (not receipt); opportunity quote/order and selected-opportunity
order counts; intended applied quote/order counts; harness/code turns; missing
turns; end time coverage; selected shop/SKU, actual settled SKU and mismatch;
selection changes relative to first choice; warning/settlement-over-limit counts.
No stock and canceled outcomes stay in ITT. Missing order/turns suppress aggregate
turn-per-ITT/time-per-ITT claims instead of imputing zero. Identical duplicate
records are counted; conflicting records or malformed input return exit2 with
audit, never a success verdict. Untimed records are explicit snapshot observations;
asof enforcement cannot be claimed for those. Naive source times use Asia/Seoul.
Full snapshot bytes/hashes recorded; reads are sequential, not globally atomic.
Inputs cannot be overwritten, nor can an existing output be replaced.

An opportunity is a successful strict *quote-stage* read, not proven LLM savings.
Journal applied=true means intended quote publication after successful append,
not selected trade, code first offer or completed settlement. An IO/fsync failure
can leave a journal line without publication; cross-check final orders' quotes
and harness turns before claiming exposure. q.text's existing 300-char clipping
can still make the subsequent strict first-turn check fall back safely. Report
those differences. This candidate does not join all LLM retries/tag events;
harness turns are not identical to model attempts. There is no mature score,
refund/6h outcome or official Players calculation in this tool.

Decision: check integrity, followup/missingness and overlap first. Evaluate whole
ITT product-selection/economic/time outcomes alongside fallback opportunities.
Sparse opportunity can justify no adoption; it does not establish safety or
performance equivalence. Do not equate saved LLM turns with Players improvement.
The 16:53 local fixture is grammar/state reproduction only, not the original
process's unrecorded inner-branch trace. The later 16:58 fallback is uninvestigated
here and is not assigned the same cause. Operational AB/score impact unverified.

## Verification and self review

Original incident condition: live quote Our ask8510, legacy quote null, first
harness fallback; separate 142800 observation lacks inner trace. Test condition:
immutable af229f3 quote() plus fake HTTP/send boundaries, and real candidate
quote()/oneshot with temp journals and fake clock; no live network/model replay.
Baseline public quote assertion8510 fails with actual None. Initial module/CLI
absence REDs also recorded; behavioral base RED is the stronger root-cause proof.
Treatment8510 and separate142800 grammar pass. Adversarial/ambiguity/mixed-number,
receipt, SKU/bounds, late/no-firstquoted, metadata/legacy precedence and kill switch
tests pass; offline fixture checks ITT missing outcomes, false-paid receipt,
payment after asof, actual SKU mismatch, corrupt/conflicting journals and input
immutability. Local first offer can change (8510:6200 observed harness versus7447
code); identical seller response/settlement cannot be assumed.

Receipts are in review/receipts/2026-11-07/W-20261106-1950-AX바이어점수 under the
central review root. RED labels: quote-align-red (172647), quote-measure-red
(172858), quote-align-baseline-red (173122), quote-measure-selection-red (173202).
Early GREEN labels are intermediate only. Final clean-HEAD full/targeted/syntax/
diff receipts and check are recorded in /tmp/ax-quote-candidate-result.md after
the final commit; intermediate dirty/during-edit full runs are not final evidence.

PR type: application. Base coverage: explicit local origin/week-08 af229f3;
no fetch or outside service review. Scope check: 5 requested candidate files only.
Self review read full diff and downstream ask_src consumers (best_quote, record,
opening/oneshot/first1 gates); extractions unnecessary, reused ask_read and stdlib.
Gate inventory: 2 listed,1 eligible/read,0 unread; RH-16 marks the entirely new
offline parser. Its bad rows are counted and audit exits2; unknown timestamps are
counted/disclosed. No existing test/threshold/assertion was removed or relaxed.
Gate artifact/log: /tmp/ax-quote-gate.log and /tmp/ax-quote-gstack-state.
Security self check: no new external calls, shell execution, credential logging,
config changes or raw shop text journal; read-only measurement rejects overwrites.
No known outstanding code defect in self pass; independent assessment pending.

Pre-Landing Review: INCOMPLETE (parent-owned independent review pending).
Gate edits: 2 listed,1 read,0 findings; no existing gate relaxation.
Formal gstack specialist/native adversarial dispatch was not run: user explicitly
assigned independent review to parent and prohibited Claude. Self adversarial
checks and tests do not replace that gate. No telemetry/upgrades/brain sync.
Latest instructions checked: 24 at 17:30 KST (22/23/24 incorporated). Parent owns
broader actual AB/adoption objective; this local handoff does not close that work.

DEBUG REPORT
Symptom: legacy quote drops Our ask8510; separate142800 grammar observation.
Root cause: legacy asking-only quote reader versus strict first-turn quoted gate.
Fix: default-OFF bounded quote-only strict additional read with order ITT journal.
Evidence: af229f3 public quote behavioral RED; candidate quote and offline CLI GREEN.
Regression test: test_quote_alignment.py (quote boundary + offline CLI).
Status: DONE_WITH_CONCERNS local candidate; independent review/live AB pending parent.

리뷰 결과: 위험도 저(기본 OFF 로컬 후보) · 확인된 미해결 코드 지적 0건 · 독립 리뷰 부모 대기
검증: RED→GREEN·최종 clean HEAD 전체/구문/diff 영수증은 결과 파일 참조; 운영 AB·점수 효과 미검증
보안: 자체 검토 통과, 독립 보안 검토 미실행; 비밀/env/config/운영 변경 없음
정정 대조: goods 첫1원 비채택·11im 유지·0원 유효성 미확인·안전 fallback 보존·142800 내부 추정 한계 반영
되돌리는 법: 부모 승인 경로에서 BUYER_QUOTE_ALIGN=0; 필요시 후보 feature 커밋 역순 revert, 원장 보존
