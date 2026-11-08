# Fresh order hash slot candidate

Source policy: `buyer_fresh_rotation.py`: ENABLED=False,
START=1794075900 (2026-11-08 03:25 KST), END=1794077700 (03:55 KST),
STUDY=fresh-uuid-slot-20261108-0325. This retained past window was not a
local activation; it is not reused or extended here. No experiment environment
switch. Independent files under
`<market-dir>/buyer_fresh_rotation/<sha256([study,order])>/`.
Do not activate with B1, quote alignment, goods-first-one, extra-quote,
firm, first95 or cap95 windows. Source overlap guard excludes these live windows;
parent must also exclude any newly introduced study before activation.
No activation, market access or effect estimate was performed locally.

## Policy and population

Goods with the actual rule chooser only. Main resolves `choose_by_rule()` once
per invocation (nonempty BUYER_CHOOSE_ENGINE overrides pricing.json; only rule
selects the rule engine) and passes the same boolean to admission, first dispatch,
chooser, ACP retry and downstream preference. It is never loaded from persisted
order/context fields. Direct dispatch/chooser calls resolve the current engine.
Reserve/decide require explicit `rule_engine is True`; missing or nonboolean
evidence fails closed. Nonrule invocations cannot receive a fresh allocation or
apply B from an existing persisted context. Existing agent enough/hurry/ACP and
actual-quote behavior remain the baseline; the rule selection separation below
does not claim agent selection is invariant to quotes.

Reserve ITT before catalog/product selection, using full canonical
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
the swap. The full quotable pool stays canonical for later batches; rule first
selection uses baseline `selection_asked`, while actual `asked` comes from started
`threads` and controls top-up, enough, duplicate prevention and cancellation.
For otherwise equal downstream candidates, the final tie-break uses canonical
preference, not arrival or treatment order. The single-variable hypothesis is
limited to rule runs; quote availability, response timing, final deal and economic
outcomes are not asserted invariant. No extra quote quota is added.
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

Rollback: retain ENABLED=False and the existing past window; any later release
or future window requires the parent's separate procedure.
For source rollback use git revert of this candidate's commits (preserve history).
Retain all evidence; never remove/reuse B1 or other study journals.
# Opt-in verified runtime image (local preparation only)

The supported executable subset is **rule selection with a prefetch context**.
Snapshot bootstrap policy decorates verified buyer definitions before execution:
actual engine resolution must return exactly True before main order handling;
`choose_racing` requires that rule decision and a non-None prefetch context.
Unsupported `choose`, agent_loop.decide and agent_tools discovery/selftest/boxed/
execute/run terminate the entire process with exit 78 before their bodies run.
This also covers the hurry first step and daemon-thread fallback. Failure is not
a HarnessError or a recoverable fallback to negotiation. Rule selection itself,
selection_asked/actual asked/threads and the ordinary non-snapshot source remain
unchanged. Agent and absent-prefetch paths are explicitly unsupported here.

Nested live tool manifests and subprocess tool.py are not in the verified image.
The previous cwd-prefix change restored discovery from 0 to 17, but that was
mutable live fallback, not immutable tool support. This snapshot now rejects
those entry points rather than silently consuming them. A full agent image would
need verified manifests, tool code/dependencies and a verified child launcher;
this implementation does not claim that support. Credential, command,
interpreter/third-party and ancestor trust contracts remain unverified.

`tools.fresh_snapshot` builds a new artifact from pinned Git objects: all top-level
non-test Python files and `pricing.json`. The fresh helper's code and four scalar
settings determine its versioned module name. The candidate entry imports that
literal name; the retained-helper rollback entry, which has no fresh references,
is kept byte-identical. Source flags and past windows are unchanged. Synthetic
settings overrides are artifact-only and require all four scalars and a full
1800-second window. No future operating window is selected by this tool.

The generated command embeds the bootstrap and expected artifact SHA256. With a
trusted interpreter, it verifies one bounded regular-file read, holds those bytes
in memory, and loads local modules and declared resources from that same image.
Late imports and helper settings cannot switch to a later filesystem version.
Existing runtime data paths remain in the original working directory. This is
not an OS sandbox: interpreter, installed dependencies, command/bootstrap, runtime
data and process memory require their existing trust controls. Direct OS-level
file access outside the declared loader/read interfaces is not intercepted.

Publication creates a new artifact name without replacing any destination. It
does not install into a checkout, update HEAD/index, stop writers, or switch an
already-running buyer kit. Retain both candidate and rollback artifacts; selecting
the previous trusted command is the image rollback, while already-running readers
retain their own verified bytes. Do not use the helper-deleting rollback
`7313949`; the retained-helper source rollback is `6a23d40`.

The existing kit `run_buyer` command interface accepts this command, as covered
by an isolated actual-buyer/no-shops test. `cmd_join` loads `agent.json` once;
there is no established live reload/admission API here. Connecting an existing
kit to a trusted command and excluding modification of that trust anchor remains
an OPEN operating contract. The unrestricted meta model child is not confined
by its working directory. A directory's mode, serial checks, flock or an
`exclusive_writer=True` assertion cannot establish this boundary. Legacy mutable
entry installation still has the noncooperating-writer race and must not be
described as closed by these opt-in tests. No operating transition is authorized
or performed by this preparation.
# Full buyer release preparation

`tools/fresh_release.py` prepares a separate publisher-owned source tree for the
complete baseline buyer, including `tools/<name>/*.py` and each manifest. It
keeps the versioned fresh helper/code/settings binding from `fresh_snapshot`,
but does not apply the older rule-prefetch-only AST guards. Thus a baseline
singleton, missing prefetch, hurry first step or agent decision can execute the
pinned baseline implementation instead of exiting 78. Non-rule experiment
admission/dispatch remains denied by the actual buyer/helper code.

The trusted command contains its bootstrap, full manifest digest, publisher UID,
absolute interpreter and release path. It verifies the complete read-only tree
before entry. Existing or incomplete publication destinations are never reused;
failed publication can leave an inaccessible incomplete directory, and must use
a new destination on retry. This is a publisher/consumer ownership contract,
not filesystem compare-and-swap or a defense against a publisher mutating its
own release. Retain every command generation and its complete tree for held
readers and rollback.

The physical tree must retain the repository layout under a dedicated managed
root (`repo/submissions/26622007/week-08`), because the existing tool sandbox
derives its repository read boundary from that layout. The interpreter, standard
library and installed packages must also be publisher managed and accessible to
the actual consumer. A manifest source hash alone does not authenticate them.
Verify ownership and consumer read/execute/no-write access for the tree and its
ancestors before use. Source packaging alone is not an operating receipt.

The opt-in `AGENT_RELEASE_LIVE_DIR` set by this command lets the existing
`agent_tools` sandbox keep the original runtime data cwd/read boundary while
its code, manifests, tool paths and `PYTHONPATH` use the release. Ordinary
execution without this variable preserves the previous cwd. The buyer cwd stays
live; the existing credential fallback location and live agent notebook path
are preserved without copying keys into the release. Live JSON/ledger/notebook
data remain mutable inputs, not immutable executable code.

The existing kit still captures `agent.json` once, and the existing supervisor
still passes its own source directory to official `join --dir`. This addition
does not implement or prove a persistent managed selector, actual consumer
access, kit transition, full first-choice replay, real A/B or economic results.
Those conditions require separate review and operating evidence. Do not deploy
the old guarded command as a replacement for all-order baseline availability.
