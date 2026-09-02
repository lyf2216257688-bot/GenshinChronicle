# Current Phase

## Active phase

**Phase 04 — Retrieval / Evidence Assembly architecture and design (P04-W7 Unit3B Frozen48 persisted production FINALIZE resume / resume-authority state)**

## Objective

Establish the evidence and benchmark foundation for later Retrieval / Evidence
Assembly experiments from accepted Raw, Parsed, and Canonical evidence, while
preserving traceability and keeping technology choices and production retrieval
implementation deferred until separately approved.

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
state created or modified by the preflight. Frozen48 semantic execution is
MECHANICALLY COMPLETE / PERSISTED: 48/48 terminal, observed 0 ACCEPT / 48
REJECT (`ANCHOR_NOT_VALID_POSITIVE` ×48). Independent semantic re-adjudication
was NOT PERFORMED; interpretation remains deferred. No authored query
attempts, quality judgments, restricted C1, or author feedback occurred. The
authoritative semantic prefix remains `review/semantic_ledger.jsonl`
(201316 bytes; SHA-256
`a88ec19bbbd5d92d76099505987ee4c32f6f1025880051a73fda8fb727e0b6d1`).
Production finalization has NOT executed; `final_ledger`, freeze candidates,
and Unit3 manifest remain NOT CREATED; `final24` persisted result is NONE.
The sanctioned semantic-facing FINALIZE integration repair is actual-gated and
checkpointed at `50a7afaafa2a7dd79fbdf8505d917f52800d4478`, the current
`w7_unit3.py` tooling/source-binding checkpoint. `225baa756d5870a4c5dc5144c717aec75fdf1b72`
remains the previous/pre-finalization-repair implementation identity. The old
contaminated first-production semantic executor remains RETIRED / MUST NOT
REUSE. The current engineering/preflight conversation is not reused for the
blind production semantic role.

The approved Phase 04 boundary is documented in
`docs/phases/phase-04-retrieval-evidence-assembly.md`.

## Immediate next action

The immediate next action is Production FINALIZE resume, NOT YET EXECUTED. It
is authorized only after this repository status synchronization is committed,
current `w7_unit3.py` verifies against tooling checkpoint
`50a7afaafa2a7dd79fbdf8505d917f52800d4478`, and the authoritative persisted
Unit3B prefix remains valid. The only authorized continuation is: validated
resume -> sanctioned `current_action == FINALIZE` -> `controller.finalize()`
-> STOP/report. Semantic rerun = NO; A-2 rerun/replacement = NO. The
deterministic expectation is selected semantic/control/WR/HN = `0/0/0/0`,
shortfall = `8/4/6/6`, no refill. `BENCHMARK_V0_4 = NOT YET AUTHORIZED`;
`RETRIEVAL_EVALUATION = NOT YET AUTHORIZED`; Dense-rescue-HN, Hybrid, RRF,
reranker, ANN, vector DB, embedding, and r02 remain NOT AUTHORIZED.

## Phase transition boundary

Phase 02 Parsed contracts remain source-specific and evolutionary; UNKNOWN or
unsupported source structures must not be guessed or silently discarded.
Phase 03 Canonical contracts preserve structural source evidence and lineage,
not semantic equivalence.

P04-W1 is a foundation implementation, not a retrieval engine. The later
Phase 04 work may evaluate retrieval-unit, passage, and chunk approaches;
lexical/BM25, dense/vector, and Hybrid alternatives; embeddings and possible
vector-store choices; reranking; deterministic and possible future LLM query
expansion; and Evidence Assembly/context expansion. No winner, production
infrastructure, or RAG behavior is authorized by P04-W1.

The following remain outside the authorized current Retrieval planning scope
and require their own reviewed authorization:

- semantic identity, alias/entity merge, Fact/Claim/Event models
- cross-Parsed-run or cross-snapshot semantic projection reuse
- Retrieval/RAG implementation and prompt orchestration
- AI semantic / claim extraction
- Knowledge graph / semantic layer
- UI

## Source of truth

Current stage and immediate next action: this file.

Phase 01 workflow and closure evidence: `docs/phases/phase-01-raw-collection.md` and relevant `docs/research/phase-01/` notes.

The closed Phase 02 draft specification is
`docs/phases/phase-02-parsed-schema.md`. The closed Phase 03 contract and
acceptance boundary are `docs/phases/phase-03-canonical-schema.md`.
Architecture/design work must also follow `docs/architecture-overview.md`,
`AGENTS.md`, and the verified OBC evidence already recorded in the repository.
