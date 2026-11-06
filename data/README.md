# Data Directory

Clean V1 reconstruction is the active implementation work unit. Phase04 remains
the serving baseline and Phase05 semantic work is paused per
[`current-phase.md`](../docs/current-phase.md). Existing Raw,
Parsed, Canonical, original RU, BM25 and historical 2048d Dense artifacts retain
their responsibilities and must be preserved. Phase 05 run roots, requests,
failures/UNKNOWN ledgers, Cards and review exports below are retained historical
evidence, not pending V1 build tasks or sources of new call authorization.

`clean/<source>/<locale>/<clean_run_id>/` owns direct Raw-to-Clean records and
`metadata/manifest.json`. `records/` uses observation-scoped hashed filenames.
`build.lock` is an OS-managed single-writer lock. Partial manifests support
recovery, completed manifests bind the selected input snapshot and record hashes.
The bounded pilot and its `validation.json` live under this ignored root.
Clean source locators use paths relative to the selected immutable Raw run.
The [Clean contract](../docs/phases/clean-v1.md) owns the schema and reading API.

The completed local 1024d original-RU index at
`retrieval/p04-local-qwen3-1024-20261102-v1/` is the default serving index.
The builder
owns `build-plan.json`, `length-audit.json`, `shards/*/receipt.json`, per-shard
vectors/rows, `progress.json`, append-only `events.jsonl`, failure diagnostics,
`validation.json`, `report.json` and the final `metadata/manifest.json`.
Only the last manifest advertises a serving-loadable complete index; shards
remain for recovery. Kernel `build.lock` is retained and must not be deleted
to bypass a running worker. Partial work is not adopted. The supported launcher
runs in the operator's own PowerShell terminal, independent of Codex; keep that
terminal open. Empty `launch-logs/`/`launcher.json` in the smoke root are retained
unsuccessful host-detachment probes, not active workers or build completion.

`retrieval/p04-local-qwen3-preparation-20261102/` owns the read-only token census,
capped smoke selections and this work's test evidence. Separate
`retrieval/p04-local-qwen3-smoke-20261102-r1/` (512) and `-r2/` (1024) own bounded
real inference, process-death recovery and Dense/Hybrid/BGE loading evidence;
they are never production candidates. Historical 2048d artifacts, the 909-RU
pilot and Phase05 indexes are preserved. See the
[operator runbook](../docs/research/phase-04/p04-local-qwen3-migration-20261102.md).
Distinct `retrieval/p04-embedding-70q-*/` roots retain offline comparison
manifests, per-question Packets, traces and separate review bundles. Preserve
both index roots and the accepted 70Q runtime, query-vector and review inputs.

The Phase04 V1-default DeepSeek QA path uses a caller-selected campaign root
with `budget/` and `requests/<fingerprint>/` (plans, immutable raw attempts and
sealed results). Live calls reserve fingerprints only in the separate
`retrieval/p04-deepseek-request-ledger/`; they never consume Phase05 ledgers or
permits. Offline fixtures use isolated mock roots/ledgers outside historical
evidence. The separately authorized three-case live root is
`retrieval/p04-deepseek-live3-20261102-r1/`: frozen original Packets and controls
under `prepared/`, immutable shared calls under `execution/`, Generation results
under `outputs/`, and terminal audit under `closure/`. Its three sends are
consumed/closed; Q020's output-cap failure remains rejected and preserved.
Per-question backend results still persist their Packet/Generation outputs
under the existing selected output root, with a hash-bound raw-archive pointer.

Shared model calls retain route fingerprint ledgers and immutable original
reservations. New call roots contain `attempts/`, dispatch/retry records and a
sealed aggregate result; explicit recoveries reference original artifact hashes
and append ledger `recoveries/` records without changing old entries. Shared
batch directories reserve each attempt before dispatch and keep UNKNOWN token
allowances separate from observed usage. Ledger `.channel/` records provide
per-channel cooldown; `.locks/` files use OS process locks, not deletion for
recovery. Offline verification evidence:
`retrieval/p05-model-call-unification-20261030-r1/`; no paid rerun or new Card
production. These ignored archives still need an independent backup.

## Independent Card comparison preparation

`retrieval/p05-card-blind-prep-20261030-r1/` owns the five-source selection metadata,
sealed Canonical/RU snapshot, shared model-visible materials, execution code,
offline checks and separately sealed accidental diagnostic evidence. Authorized
`runs/gemini/` and `runs/deepseek/` retain raw invocation/response/usage and
source-bound document Cards. Generation results stay separate from preparation.
`runtime-revision/` appends explicit code migrations without resealing original
preparations; `recoveries/<provider>/<case>/` binds independently validated offline
results to original completed requests. Neither replaces raw failures or ledgers.
`isolated/<provider>/<case>/` accounts for proved completed, known-usage content
failures without accepting their Cards or permitting reissue; uncertain sends
remain blocking. Multiple code revisions preserve their predecessor seals.
`review-key.json` is private
control data, excluded from anonymous review exports and the model workspace.
`anonymous-review/` includes every selected document with paired, single-sided or
no-valid-Card dispositions. Only accepted Cards receive semantic/projection files;
failed and missing results receive no automatic semantic score. Provider names,
usage, timing, raw errors and the private assignment stay outside that package.
`deepseek-independent-dispatch.json` records a separately user-authorized,
five-case exception to UNKNOWN accounting; it never changes original usage or
permits reissue and is not a general production authorization.
The external Antigravity workspace receives only the sealed materials subtree;
the main corpus, historical model answers and review findings are not workspace roots.
Configured CLI access controls are not a claim of complete OS containment. Current
authorization and incident accounting are owned by `docs/current-phase.md`.

## Standalone offline Navi environment

`retrieval/p05-navi-offline-20261029-r1/` contains this work unit's protected-input
inventory, bounded validation runs, test logs and local verification records.
Each run separates sealed `prepared/` source snapshots and execution code,
create-only `jobs/` raw archives, isolated Mock ledger, Card checkpoints,
independent `indexes/ru` and `indexes/card`, completed output and append-only
human-review records. Unfinished attempts remain forensic data and are never
overwritten. The full input RU artifact is scanned/verified, but only explicitly
selected sources are materialized; vector budgets are separate and audited.

Review exports include source snapshots and raw request/response evidence with
checksums. They are not independent backups merely because they exist on the
same disk. All these data remain Git-ignored; committing code would not back
them up. Historical roots, the real v0.3 request ledger and 2048-dimensional
production indexes retain their existing responsibilities. See the
[environment report](../docs/research/phase-05/p05-navi-offline-environment-20261029.md).

`retrieval/p05-card-production-readiness-20261029-r1/` owns the separate
Card-only engineering checkpoint: initial/final Canonical census, historical
Card input comparison, append-only range/usage replay, bounded real-source
Mock builds, resource/cost scenarios and protected-input verification. It
contains no new paid response or full-corpus embedding. New Card builds keep
sealed `prepared/`, create-only raw `jobs/`, immutable per-source attempts under
`documents/`, completion pointers and batch summaries. A completed build means
mechanical completion, never human acceptance. New Card indexes are explicit
separate artifacts; historical RU/Card and production indexes stay intact.
See the [Card readiness report](../docs/research/phase-05/p05-card-production-readiness-20261029.md).

`data/` contains generated/local datasets, not source code.

`retrieval/p05-card-metro-discovery-20261029-r1/` retains secret-free model-catalog
discovery. `retrieval/p05-card-metro-live-20261029-r1/` owns the one authorized
Metro Card verification, strict protocol failure, independently known counters,
content-only unaccepted JSON and five-source stop records. Its separate persistent
ledger is `retrieval/p05-card-metro-request-ledger/`; the old jizhi ledger/results
remain unchanged. Valid JSON alone is not an ingested Card or human acceptance.
See the [Metro report](../docs/research/phase-05/p05-card-metro-switch-20261029.md).

## Raw evidence

`data/raw/` is the local home for collected Raw artifacts.

Raw principles:
- preserve received responses;
- do not silently normalize or prune Raw evidence;
- do not overwrite historical evidence merely to keep one "latest" copy;
- store enough metadata to reconstruct what was fetched and when;
- keep manifests/observations auditable.

The full dataset is intentionally ignored by Git.

Small sanitized samples needed for automated tests belong under `tests/fixtures/`, not here.

## Parsed outputs

`data/parsed/` contains generated, Git-ignored Parsed runs produced from local
Raw runs. A Parsed run stores immutable record files plus an auditable manifest
that records its input Raw run, dependencies, fingerprints, statuses, and
diagnostics. Parsed output is derived data, not source code and not a
replacement for Raw evidence.

## Canonical outputs

`data/canonical/` contains local, immutable Canonical run records and manifests
derived from accepted Parsed observations. Canonical data remains the evidence
source for later Retrieval derivatives and must not be rewritten by Phase 04
tools.

## Retrieval and RAG artifacts

`data/retrieval/` is an ignored local destination for authorized, rebuildable
Phase 04 and Phase 05 derivatives: Retrieval Unit builds, lexical/Dense indexes
and row mappings, source-bound semantic builds and distinct hierarchy/graph
views, deterministic candidate and Evidence Packet materialization, and their
manifests, ledgers, and provenance. Phase 05 semantic artifacts are navigation
derivatives, not evidence authority; final citation authority remains the
Evidence Packet resolved to official Retrieval Units and Canonical lineage.
The root can hold the local production materialization baseline, but is not a
serving-system, vector-database, or permanent source-of-truth contract.

Candidate V offline research roots contain a release catalog and metadata-join
audit, input/implementation fingerprints, immutable session manifests and chained
reading/understanding checkpoints, and bounded replay results. Coverage receipts
record delivery separately from interpretation; they are not formal citations
or accepted semantic views. New runs use new directories, leaving historical
A/B roots immutable.

R1 Navi discovery comparison roots preserve frozen 128-document research
scopes, evaluator-only reference proposals, independent source profiles,
provider attempts/raw responses, derived experimental vectors and rankings,
dual-source judgments, stability checks and cost accounting. Partial model
cohorts remain separate from the complete-model cohort; execution-unknown paid
requests are quarantined. Model review is not human semantic acceptance, and
full raw-corpus retrieval does not imply full-corpus semantic production.

The separate `p05-navi-r1-qa-20261025-r*` roots preserve source-pair discovery
ledgers, legacy dispositions, hash-verified Canonical/RU snippets, blind review
deliveries, raw model/stream outcomes, independent reconciliation projections,
method comparisons and pending human review materials. Partial Qwen and later
operator-requested jizhi DeepSeek cohorts remain distinct; the uniform DeepSeek
review owns the new candidate comparison. Exact quote binding, model semantics
and human acceptance are separately recorded. No QA artifact changes R1 inputs,
Canonical evidence, production retrieval or active semantic views.

G1 offline navigation roots (`p05-navi-g1-*`) keep standalone versioned Cards
and Links, real-section/unit inventories, batch reading receipts, cached
source-only semantic queries, original-text Hybrid candidates, extracted exact
Canonical/RU evidence, A/B/C/D/U recommendations, model/self/targeted reviews,
and request/failure/hash ledgers. Reused legacy versions and new generations
are counted separately. Earlier ten-case QA packets are calibration only.
All G1 results remain inactive and human acceptance remains explicit; no
artifact is a Canonical fact or independently accepted history witness.

The separate G1-S1 roots (`p05-navi-g1-s1-*`) retain score-withheld evidence
packets, exact snippet/RU checks, agent-authored and frozen proposition reviews,
complete 48-pair historical comparisons, separate supplemental cases, reverse
self-audits and static prompt/schema checks. They bind the unchanged G1 input
inventory and do not regenerate Cards/Links or alter human/release states.
Agent review is explicitly not human gold; rubric migration is not model accuracy.

The independent `p05-navi-v02-finalization-*` roots preserve pre-edit document
snapshots, unchanged G1/S1 inventories, conservative fresh-source exclusions,
synthetic static-contract probes and verification records. They are preparation
evidence, not a newly selected validation cohort or new model-generated Links.

The E2 four-model Responses protocol evidence uses a separate immutable campaign
root, `data/retrieval/p05-navi-e2-responses-four-model-20261015-r1/`. Its
authorization, per-model probe configs, issued attempts, raw error/SSE evidence,
terminal usage, action validation, and offline audit are synthetic protocol
evidence, not formal autonomous research or Canonical material. Runtime API
credentials never belong in this root. The independently authorized follow-up
root `data/retrieval/p05-navi-e2-responses-followup-20261015-r2/` binds the frozen
original campaign manifest and counts its issued requests cumulatively. It holds
new Jizhi GLM/DeepSeek probes, not resume/replay or a resolution of earlier UNKNOWN
requests. Historical E2 r1-r4 and original protocol roots stay immutable.

Experiment-specific, review-only, provider, or challenger evidence that is
kept under `.local/` remains outside this data-root contract and must not be
promoted to tracked data merely because it informs a later decision.

### Navi F1 candidate evidence freeze (2026-10-26)

`retrieval/p05-navi-f1-20261026-r1/` owns the independent candidate pool, exclusions,
24-pair/48-source selection, exact Canonical/RU snapshots, shared source inputs,
reviewer-only original candidates/probes and file-hash seal. Final independence
status is PARTIAL (19 provisional, four blocked, one family review); this is not
a complete 24-pair F2 input or a model experiment. Earlier checkpoints are retained;
`independence-dispositions-final.json` and `f2-readiness.json` own final dispositions.
`retrieval/p05-navi-f1-replay-20261026-r1/` owns the separate offline reconstruction
comparison and post-seal verification, without writing back to the sealed r1 root.
See [F1 report](../docs/research/phase-05/p05-navi-f1-freeze-20261026.md). This historical
freeze authorized no project model/Embedding inference, human acceptance or production
release; current authority belongs to `docs/current-phase.md`.

### Navi F1-R2 repaired sample freeze (2026-10-26)

`retrieval/p05-navi-f1-20261026-r2/` owns the expanded finite pool, R1-to-R2
differences, full-source freshness evidence, 24-pair/48-source exact inputs,
4+4 reviewer candidate registry and file-hash seal. User-confirmed seed is
`navi-v02-fresh-1`. No historical-calibration block is known under bounded agent
review; same-character references and within-F1 content dependence are disclosed.
`retrieval/p05-navi-f1-replay-20261026-r2/` owns the separate 24-packet replay
and post-seal checks. R1 and its PARTIAL findings are preserved. At this historical
checkpoint F2 was not authorized; current authority belongs to `docs/current-phase.md`.
See [R2 report](../docs/research/phase-05/p05-navi-f1-r2-freeze-20261026.md).

### F2 offline preflight (2026-10-26)

`retrieval/p05-navi-f2-preflight-20261026-r1/` owns 48 static base requests from
sealed F1-R2, prompt/native validator/Schema snapshots, byte/token-proxy and
price templates, optional-review slots, evaluation/failed-attempt protocols,
synthetic-only replay evidence and the preflight seal. At that historical checkpoint
readiness was BLOCKED on runtime/token/context/budget/review settings and authorization;
current execution authority is owned by `docs/current-phase.md`.
Synthetic examples and statuses are not actual F2 outputs or performance. No model
or Embedding calls are authorized by this artifact. See [Preflight report](../docs/research/phase-05/p05-navi-f2-preflight-20261026.md).

### F2-C1 runtime and budget preparation (2026-10-27)

`retrieval/p05-navi-f2-c1-20261027-r1/` owns 48 unchanged-input streaming drafts,
119 historical G1 stream replays, empirical token proxies, replaceable budget
scenarios, the runtime draft, tests and a separate seal. Model/route selection is
user-confirmed as jizhi / deepseek-v4.1-flash; at that historical checkpoint provider
capabilities and F2 dispatch were unverified and unauthorized. No historical inputs
are replaced. See [C1 report](../docs/research/phase-05/p05-navi-f2-c1-20261027.md).

### F2 live probe (2026-10-27)

`retrieval/p05-navi-f2-live-20261027-r1/` preserves the authorized neutral probe,
its HTTP 403 access-denied response, the 48 base slots unsent at that checkpoint and
integrity records.
See the [blocked F2 report](../docs/research/phase-05/p05-navi-f2-live-20261027.md).
`retrieval/p05-navi-f2-live-20261027-r2/` preserves the subsequent user-requested
probe with an unresolved URLError and UNKNOWN execution state; see the
[retry record](../docs/research/phase-05/p05-navi-f2-retry-20261027.md).
`retrieval/p05-navi-f2-transport-audit-20261027-r1/` contains the subsequent
provider-free client and proxy comparison; see the
[transport audit](../docs/research/phase-05/p05-navi-f2-transport-audit-20261027.md).

### F2 SDK communication repair (2026-10-27)

`retrieval/p05-navi-f2-sdk-repair-20261027-r1/` owns the original urllib-code snapshot,
48 full-adapter synthetic replays, relevant fixture logs, historical protection checks
and one unauthenticated homepage GET response. Synthetic usage is not model usage;
homepage200 does not establish authenticated Responses/30K capability. See the
[SDK repair report](../docs/research/phase-05/p05-navi-f2-sdk-repair-20261027.md).

### F2 first formal request (2026-10-28)

`retrieval/p05-navi-f2-experiment-20261027-r1/` owns the authorized first
frozen base request, raw stream, SDK events, stop record, 48-slot ledger and
offline observation. One request was attempted; 47 remain unsent, and strict
stream validation failed. See the [first-request report](../docs/research/phase-05/p05-navi-f2-first-request-20261028.md).

### F2 instructions and output-cap diagnostic (2026-10-28)

`retrieval/p05-navi-f2-instructions-diagnostic-20261028-r1/` owns two separately
authorized neutral diagnostic requests, raw responses, SDK events and identity/
instruction checks. These calls do not consume formal F2 slots. See the
[diagnostic report](../docs/research/phase-05/p05-navi-f2-instructions-diagnostic-20261028.md).

`retrieval/p05-navi-f2-metro-diagnostic-20261028-r1/` holds the separately
authorized one-call TokenMetro comparison, HTTP 401 response and isolated audit;
see the [TokenMetro report](../docs/research/phase-05/p05-navi-f2-tokenmetro-diagnostic-20261028.md).

### F2 JSON-object recovery and 30k preparation (2026-10-28)

The independent `p05-navi-f2-legacy-json-mode-20261028-r1/`,
`p05-navi-f2-rc1-json-mode-20261028-r1/` and
`p05-navi-f2-rc1-json-mode-30k-20261028-r1/` roots preserve the matched-pair
diagnostic requests, raw responses, SDK events and observations. The separate
`p05-navi-f2-json-mode-prepared-20261028-r1/` and
`p05-navi-f2-json-mode-prepared-30k-20261028-r1/` roots contain unsent plans;
they do not authorize dispatch. The original F2 failure and remaining slots
stay separate. See the [recovery](../docs/research/phase-05/p05-navi-f2-json-mode-recovery-20261028.md)
and [30k runner](../docs/research/phase-05/p05-navi-f2-json-mode-30k-and-runner-20261028.md) reports.

These generated files remain Git-ignored. A per-file SHA-256
[inventory](../docs/research/phase-05/p05-navi-f2-evidence-inventory-20261028.json)
records the F1/F2/v0.2 local evidence copied from worktree `5d8c` to this main
checkout; it is not a backup of file contents. Both copies are local to the same
machine, so retain an independent backup before removing either checkout or data root.

### F2 30k partial execution (2026-10-28)

`retrieval/p05-navi-f2-json-mode-execution-20261028-r1/` contains the new
48-slot run configuration, two attempted slots and stop record. The separate
`retrieval/p05-navi-f2-json-mode-execution-audit-20261028-r1/` inventories the
run files and all 48 dispositions after offline replay. It does not replace raw
evidence. The run stopped at the rc1 Schema failure with 46 slots unsent; see
the [execution report](../docs/research/phase-05/p05-navi-f2-formal-30k-partial-20261028.md).

The independent `retrieval/p05-navi-f2-json-mode-continuation-46-20261028-r1/`
records the separately authorized slot 3–48 continuation and its slot 3 pre-send
TLS stop. `retrieval/p05-navi-f2-json-mode-continuation-audit-20261028-r1/`
combines dispositions read-only over the unchanged 48-slot plan; it does not
replace either raw run. See the [continuation report](../docs/research/phase-05/p05-navi-f2-continuation-46-20261028.md).

`retrieval/p05-navi-f2-json-mode-continuation-retry-20261028-r1/` holds the
subsequently authorized slots 3–48, each in an independent attempt directory.
`retrieval/p05-navi-f2-48-final-audit-20261028-r1/` and corrected r2 hold the
read-only 48-slot replay and accounting; r2 corrects the slot 2 attempt number.
See the [execution report](../docs/research/phase-05/p05-navi-f2-48-execution-20261028.md).

`retrieval/p05-navi-prompt-final-20261028-r1/` owns the separate final v0.3-rc1
offline candidate freeze: protected-input hashes, exported mode schemas and
prompts, unsent request drafts, representative-case results, source-binding
checks and verification records. It does not replace any F1/F2 raw run or
historical judgment, and contains no new paid response or released Navi Link.
The small portable regression fixture under `tests/fixtures/retrieval/` contains
only six selected F2 source pairs with traceable snippets and agent-authored
contract cases; controlled erroneous assertions are explicitly distinguished
from historical model errors. Current scope belongs to `docs/current-phase.md`.

`retrieval/p05-navi-v03-run-readiness-20261028-r1/` owns the separate v0.3 runner
readiness audit, prepared source-bound requests, explicitly offline Mock
checkpoint archives/ledger entries, Schema checks, test records and historical-integrity
checks. No provider response or live execution is claimed. Future authorized
v0.3 calls use the persistent `retrieval/p05-navi-v03-request-ledger/` fingerprint
reservations across output directories; existing/pending reservations cannot
be automatically resumed or removed. Plans and attempt/run outputs are
create-only and remain inactive. This does not change older evidence roots.

`retrieval/p05-navi-v03-limited-live-20261028-r1/` owns the separately authorized
three-case real acceptance: exact source-bound plans, persistent request-ledger
reservations, pre-send TLS retry evidence, raw response bytes, SDK event streams,
full local replays and `evaluation.json`. It records three completed responses
and 15,941 known tokens; actual fee and strict provider Schema enforcement remain
UNKNOWN. It does not alter F2 history or publish/release any Navi. See the
[limited live acceptance report](../docs/research/phase-05/p05-navi-v03-limited-live-20261028.md).

## Fixed-32k Card integration evidence

`retrieval/p05-card-integration-32k-20261031-r1/` owns offline integration checks,
source-plan/historical-response replay, final structural census and protected hashes.
In new builds `reorganizations/` seals one replacement decision per failed task;
`reorganization-results/` separately seals its successful resolution (never inside
an already sealed decision). Original failed jobs and successful temporary nodes
remain traceable. `documents/*/accepted-build.json` exists only after all required
reading/synthesis succeeds; temporary nodes are not default semantic index owners.

## Card evidence-selection and source export checks

`retrieval/p05-card-evidence-selection-20261031-r1/` owns offline historical replay,
new-contract preparation, MOCK-only index verification and protected archive hashes.
It contains no newly generated semantic Cards or real embedding result. Its controlled
reference selections are test inputs, not model effectiveness or semantic review evidence.

New `deepseek-card-evidence-32k-20261031` preparations seal
`prepared/quote-locations.json`; raw-first job plans bind their actual delivered/inherited
position maps, including replacement tasks. This is a locator over existing verified
RU evidence, not a second evidence authority. New Card index roots seal
`card-selection.json` with completed-source identities, counts and every skipped source
and reason. An empty export contains `export-result.json` without vector shards.
Neither export nor replay edits source Cards, historical failures or request ledgers.

`retrieval/p05-card-live66-1017-repair-v3-20261101-r1/` owns the fixed66 Card repair
continuation: campaign/prepared binds the unchanged source selection, prior runtime
and pilot/shard preparation identities; pilot and shard directories own immutable
raw requests/responses, exact source maps, Cards and shared finite call reservations.
Reuse-origin records link copied historical successes without recharging them.
Failed responses and prior run roots are retained. Full66 status counts complete
source documents separately from requests; it does not grant semantic acceptance.
No Link/KG/Embedding or new production index is owned by this data root.

Within that repair root, `full66_status.json` preserves the initial four-worker
checkpoint (51/66); `combined_status.json` records the later drained54/66 state;
`closure-report/status.json` is the sealed closure census including physical-attempt
accounting, exact66 statuses, required-read progress, audits and uncertainty.
`continuation-*-v4/`, canary and `authorized-unknown-once-v4/` preserve later partial
work and originals. `operator-stop/` and `operator-stop-result/` record closure of new
dispatches without cancelling sent requests. `final-cards/` holds only54 actually
complete normalized review sources, not66; it owns no new API reservations. Original
failed roots and budgets remain unchanged. All outputs are inactive/not reviewed.
