# Current Phase

## Active phase

**Phase 04 - First End-to-End RAG design / planning (post-W7 direction reset; implementation not yet authorized)**

## Objective

Build the first real, runnable end-to-end RAG baseline on top of the accepted
Raw -> Parsed -> Canonical foundation, then use real human-authored questions
to measure the whole system and optimize the actual bottleneck.

The current baseline direction is:

```text
Canonical
-> Retrieval Units
-> BM25 + local Dense + simple Hybrid
-> deterministic Evidence Assembly
-> provider-neutral Evidence Packet
-> GenerationProvider
-> Answer + Citations
```

This is a **first runnable baseline**, not a final architecture freeze. Models,
retrieval parameters, context-expansion settings, fusion details, storage,
GenerationProviders, and later optimization layers may change when actual
evaluation evidence justifies the change.

## Current status

Repository initialization, Collector v0.1, discovery/contract verification (01A–01C), offline acceptance, and the scoped Collector security gate are complete; `GC-COLLECTOR-001` and `GC-COLLECTOR-002` are closed with 0 remaining findings. P01-EA was completed and checkpointed at `0c5617d`.

P01-EB full crawl (`run_id=p01eb_full_20260824`) is complete and locally auditable: 96/96 listing responses, 32,916 listing records, 16,437 unique `content_id` values, and 16,437/16,437 successful detail responses, with 0 final unresolved failures. Archive/hash/inventory audit and same-run recovery passed. OBC full-corpus profiling is complete. Current production scope is `zh-cn` + OBC only. These results are run-level and contract-bounded; `manifest complete` is not a claim of absolute semantic completeness of the upstream server.

Phase 02 Parsed Schema / Parser Foundation is **CLOSED** at checkpoint
`5c2d131` (`phase02: add incremental parsed acceptance pipeline`). Batch 4
added the executable OBC Raw-to-Parsed run pipeline, immutable Parsed record
storage, Raw dependency accounting, versioned reuse/reparse decisions, and
deterministic corpus-wide acceptance. The accepted P01-EB evidence accounted
for 16,437/16,437 input details, with 0 `blocked_integrity` records. An
independent same-run reuse check produced 16,437 reused records and 0
reparsed records; full local regression passed 53/53 tests.

This detail-level Parsed accounting is an auditable local result, not a claim
of upstream semantic completeness or that every nested component has a
complete semantic handler. The Parsed schema remains a design draft rather
than a frozen contract.

Phase 03 Canonical Schema is **CLOSED / PASS**. Its approved architecture was
completed through Batch 1 (`0f7cff4`), Batch 2 (`e34468e`), Batch 3
(`0fc609b`), Batch 4 (`f287bef`), and Batch 5A (`4726d8a`). The separate
Batch 5B production gate projected the accepted
`phase02-batch4-p01eb-full-20260824-closure-v2` Parsed run to
`phase03-batch5b-p01eb-full-20260824`: its manifest is `complete`, with
16,437 input and accounted records, 0 input-integrity failures, 0 reuse, and
16,437 reprojected Canonical records. Streaming audit verified all 16,437
record paths, hashes, JSON objects, record IDs, and Parsed identity keys.

Phase 01 Raw Collection, Phase 02 Parsed Schema / Parser Foundation, and
Phase 03 Canonical Schema are **CLOSED**. Phase 03 establishes a full OBC
`zh-cn` structural Canonical production projection; it does not establish
semantic completeness, complete unsupported-structure understanding, final
dialogue/speaker semantics, cross-snapshot identity, or Retrieval/RAG quality.

## Active work unit

**P04-W1 — Corpus Profiler + Retrieval Benchmark Foundation** is CLOSED / PASS
at `3cf9694`. **P04-W2 — Derived Retrieval Representation + Lexical Baseline**
is CLOSED at `7d8c510`. **P04-W3 — Diagnostic Benchmark Expansion + Lexical
Failure Isolation** is CLOSED at `34069c2`. **P04-W4 — Diagnostic Evidence
Expansion + Retrieval-Family Decision Gate** is CLOSED / PASS at `2f292f3`.
Its final decision is **D — mixed / UNKNOWN**: it added a bounded
evidence-grounded diagnostic benchmark and a fixed A/B analyzer matrix over
unchanged r02 Retrieval artifacts, without rerunning Canonical projection or
modifying Raw, Parsed, Canonical, or profiler data.

**P04-W5 — Benchmark Reliability & Coverage Expansion** is CLOSED / PASS at
`8ba49d1`. It froze `benchmark-v0.3` before its single A/B matrix run,
preserved all 12 v0.2 anchors, expanded the diagnostic evidence with explicit
Canonical/r02 coverage eligibility, and kept all Retrieval representations,
analyzers, scorer, and Canonical data unchanged. Its measurement evidence is
`docs/research/phase-04/p04-w5-benchmark-reliability.md`: structured overall
improved 5/5 from A to B, but W5-new structured is only 2/2 for the same new
entity and is not cross-entity replication; dialogue and paraphrase remain
mixed; natural HN did not form top-10 contamination. It does not authorize an
analyzer follow-up, representation redesign, Dense pilot, routing/down-rank,
or another Retrieval technology path.

**P04-W6 — Dense family-isolation pilot** is **CLOSED / PASS**. The fixed
`contextualized_leaf` pilot completed with 242,965 documents using the pinned
`BAAI/bge-small-zh-v1.5` snapshot; embedding and frozen benchmark-v0.3
evaluation artifacts passed provenance and integrity review. Dense showed
credible complementary signal, but paraphrase-family repeated rescue was not
established, Dense is not a standalone winner, and the Retrieval winner
remains **UNKNOWN**. Hybrid, ANN/vector DB, reranking, routing/down-rank,
larger models, query rewrite, and representation changes are not authorized.
**P04-W7 blind leaf-only evidence expansion**: the legacy benchmark sanitizer
and Unit 2 structural runner, performance repair, and one production Unit 2
run are CLOSED / PASS. The Unit 3 execution contract is APPROVED / FROZEN.
Unit 3A-1 pre-exposure implementation and its technical-lead actual code gate
are ACCEPTED / PASS. Unit 3A-2 committed production mechanical extraction is
ACCEPTED / FROZEN. Unit 3B-0 pre-exposure production persistence tooling and
its technical-lead actual source/test/docs gate are PASS at checkpoint
`1126a8d`; post-checkpoint generator binding is PASS. Its proven scope is
persistence and write/action ordering only: **write-order correctness !=
read/exposure-order correctness**. Historically, the first ZERO-EXPOSURE
runtime preflight was PASS, semantic exposure OCCURRED, and the contaminated
first production semantic execution WAS BLOCKED at that incident point;
scientific judgments were 0/48 at that incident point. Query quality checking
was NOT EXECUTED; `QUERY_AUTHORING = NOT EXECUTED`; `REAL_C1_CHECK = NOT
EXECUTED`; finalization was NOT EXECUTED;
`BENCHMARK_V0_4 = NOT AUTHORIZED`; and `RETRIEVAL_EVALUATION = NOT AUTHORIZED`.
A-2 remains VALID / FROZEN. The contaminated semantic executor is RETIRED from
further Unit 3 semantic-review and query-author roles. Incident classification
is FINAL C — `SAFE_HELPER_EXISTS_BUT_NOT_EXCLUSIVE_OR_STATE_BOUND`.
The previous accepted exposure/materiality implementation checkpoint is
`225baa7` (pre-finalization-repair source identity); it is not the current
working-source implementation or binding. The minimum persisted
material-quality retry representation/wiring and mechanically separate
production-shaped quality-authority wiring were checkpointed there. The
pre-finalization-repair application-level exclusive/state-bound candidate-body
exposure implementation was checkpointed there as well. The approved
post-incident requirement is a trusted blind executor plus an application-level
mechanical fail-closed gate; OS/filesystem/shell isolation is not required or
claimed for W7. Post-incident exposure authority is owned by
`docs/phases/phase-04-w7-unit3b-exposure-amendment-v1.md`; material-quality retry
authority is owned by
`docs/phases/phase-04-w7-unit3b-material-quality-retry-addendum-v1.md`. The
exposure amendment is effective/frozen at its accepted authority checkpoint;
the material-quality addendum is effective/frozen at checkpoint `46fb3aa`; the
classification gap is closed by that repository-effective authority. The
readiness rule is: candidate N full lifecycle terminal -> candidate N+1 body
may be exposed. No surviving state attributable to the failed semantic
execution was found within the committed source-observable Unit3B runtime
footprint; this does not include or invalidate the pre-existing A-2
accepted/frozen manifest and pack. The independent source/test audit was
PARTIAL at audit time because authoritative N→N+1 exposure-readiness semantics
were then absent; its source findings remain valid. The exposure amendment is
repository-effective / FROZEN; the previously missing
N→N+1 exposure-readiness semantics are now authoritative. The repaired-state
ZERO-EXPOSURE preflight is PASS / CLOSED, with ZERO candidate-body,
future-candidate-body, and real-overlap exposure and ZERO production runtime
state created or modified by the preflight. P04-W7 Unit3B Frozen48 production
execution is PASS / CLOSED: 48/48 semantic states are mechanically complete /
persisted; observed result is 0 ACCEPT / 48 REJECT
(`ANCHOR_NOT_VALID_POSITIVE` ×48). Independent semantic re-adjudication was
NOT PERFORMED and scientific interpretation is DEFERRED. No authored query
attempts, quality judgments, restricted C1, or author feedback occurred.
Production finalization is COMPLETE / PERSISTED / ACTUAL-GATED. Its terminal
manifest is
`data/retrieval/p04-w7-unit3-contextualized-leaf-r01/metadata/unit3_manifest.json`
(3018 bytes; SHA-256
`512eb2fafa44c05c8b7d1d218fc163d051177b2208f89e355e83bf25bb3dcf73`).
Filesystem existence, byte-count, and SHA identities were independently
actual-matched; row-count and accounting values are terminal-manifest /
authoritative-finalizer derived and consistent with the actual-gated
deterministic finalization contract. The terminal manifest is the primary
owner of detailed child artifact identities and accounting. Selected
semantic/control/WR/HN = `0/0/0/0`; quota shortfall = `8/4/6/6`; all queues
are `SHORTFALL / EVIDENCE_INSUFFICIENT`; no refill. Terminal-manifest row
counts are final ledger 48 and freeze candidates 0. `benchmark_v0_4 =
NOT_CREATED` and `retrieval_evaluation = NOT_EXECUTED`; neither is
automatically authorized by this closure. Engineering blocker = NONE. The
sanctioned semantic-facing FINALIZE integration repair is actual-gated and
checkpointed at `50a7afaafa2a7dd79fbdf8505d917f52800d4478`, the current
`w7_unit3.py` tooling/source-binding checkpoint. `225baa756d5870a4c5dc5144c717aec75fdf1b72`
remains the previous/pre-finalization-repair implementation identity. The old
contaminated first-production semantic executor remains RETIRED / MUST NOT
REUSE.

The approved Phase 04 boundary is documented in
`docs/phases/phase-04-retrieval-evidence-assembly.md`.

## Post-W7 direction update

P04-W7 Frozen48 production / finalization / outcome closure remains **PASS /
CLOSED**. Its observed result was 0 ACCEPT / 48 REJECT; it remains a valid
negative result for the frozen W7 candidate-construction design and does not
establish a Retrieval-family winner.

The user subsequently stopped the Frozen48 D1/body-failure-taxonomy path and
also stopped the separately sent Semantic-First Benchmark Design Plan.
Neither is a pending task and neither should be resumed or resent.

Current project priority is now **First End-to-End RAG**. The governing design
direction is:

`docs/phases/phase-04-first-end-to-end-rag-amendment-v1.md`

Historical authorization statements inside the W1-W7 status paragraphs above
remain valid as historical snapshots of those work units, but they do **not**
override this later current-state direction. In particular, old statements
that Hybrid, RRF, embedding, or downstream RAG work were "NOT AUTHORIZED" are
not current permanent technology bans.

That amendment records the agreed first runnable baseline direction. It does
not authorize implementation by itself and it must not be interpreted as a
permanent rejection of later reranking, ANN/vector DB, different embeddings,
different Hybrid methods, additional GenerationProviders, or other
evidence-justified improvements.

Current pending Codex task: **NONE**.

## Immediate next action

Create a docs-only checkpoint containing
`docs/current-phase.md` and
`docs/phases/phase-04-first-end-to-end-rag-amendment-v1.md`.

After that checkpoint, the next engineering task is a **new Plan-only Codex
task**. Codex must inspect the live Canonical contracts, fixtures, and reusable
Retrieval assets and answer:

> How should the current real Canonical data be transformed into Retrieval
> Units, and how should context be restored with the simplest deterministic
> Evidence Assembly design?

The Plan must distinguish deterministic invariants from tunable configuration.
Context window / neighbor expansion / Top-K / evidence-budget choices should
have bounded, reproducible defaults but must not be hard-coded as permanent
architecture constants. The Plan should end with **exactly one first
implementation work unit**.

No First End-to-End RAG implementation is authorized before that Plan is
reviewed and explicitly approved.

## Phase transition boundary

Phase 01 Raw Collection, Phase 02 Parsed Schema / Parser Foundation, and Phase
03 Canonical Schema remain **CLOSED**. P04-W1-W7 accepted/closed work remains
historical evidence and reusable tooling where applicable.

Phase 02 Parsed contracts remain source-specific and evolutionary; UNKNOWN or
unsupported source structures must not be guessed or silently discarded.
Phase 03 Canonical contracts preserve structural source evidence and lineage,
not semantic equivalence.

The new First End-to-End RAG direction builds on the current real Canonical
contract; it must not redesign Canonical merely for Retrieval convenience.

The current amendment authorizes **design / planning direction only**.
Implementation requires a separately reviewed Plan and explicit execution
authorization.

First-version exclusions in the amendment are temporary scope controls, not
permanent technology bans. In particular, Vector DB / ANN, reranking, more
complex Hybrid methods, query routing/decomposition, agentic retrieval,
knowledge graphs, different embeddings, and additional GenerationProviders
may be reconsidered later if end-to-end evidence shows they address a real
bottleneck.

The planned first GenerationProvider is OpenAI. The long-term provider list is
intentionally not frozen; later providers may be added or removed according
to actual product needs.

Knowledge-graph / semantic-identity layers and production UI remain outside
the first runnable RAG baseline unless separately authorized.

## Source of truth

Current stage and immediate next action: this file.

Current First End-to-End RAG design direction:
`docs/phases/phase-04-first-end-to-end-rag-amendment-v1.md`.

The general Phase 04 boundary remains documented in
`docs/phases/phase-04-retrieval-evidence-assembly.md`; W7-specific amendments
and addenda remain authoritative for the historical W7 work they governed,
unless a later document explicitly supersedes a current-state decision.

Phase 01 workflow and closure evidence:
`docs/phases/phase-01-raw-collection.md` and relevant
`docs/research/phase-01/` notes.

Closed Phase 02 and Phase 03 authority:
`docs/phases/phase-02-parsed-schema.md` and
`docs/phases/phase-03-canonical-schema.md`.

Architecture/design work must also follow `docs/architecture-overview.md`,
`AGENTS.md`, actual repository code/tests, and verified OBC evidence.
