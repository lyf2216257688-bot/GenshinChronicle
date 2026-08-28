# Current Phase

## Active phase

**Phase 04 — Retrieval / Evidence Assembly architecture and design (P04-W3 complete; review pending)**

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
Failure Isolation** is complete and awaits human review. It added a small
evidence-grounded diagnostic benchmark and a stdlib-only unigram+bigram analyzer
matrix over unchanged r02 Retrieval artifacts. It did not rerun Canonical
projection or modify Raw, Parsed, Canonical, or profiler data.

The approved Phase 04 boundary is documented in
`docs/phases/phase-04-retrieval-evidence-assembly.md`.

## Immediate next action

Review P04-W3. Dense/Hybrid, routing/contamination, reranking, and Evidence
Assembly experiments require separately reviewed authorization.

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
