# Current Phase

## Active phase

**Phase 03 — Canonical Corpus (implementation authorized)**

## Objective

Implement the approved Canonical-layer contract from accepted Phase 02
evidence in small, independently reviewed batches while preserving Parsed and
Raw traceability and keeping Retrieval/RAG deferred.

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

The Phase 03 Canonical architecture plan has passed review. The approved
contract is recorded in `docs/phases/phase-03-canonical-schema.md`.

Phase 03 Batch 1 — Canonical Contract Foundation is **CLOSED / PASS** at
checkpoint `0f7cff4` (`phase03: establish canonical contract foundation`).

## Immediate next action

Phase 03 Batch 2 — Structural OBC Projector.

## Phase transition boundary

Phase 02 is closed. Its Parsed contracts remain source-specific and
evolutionary; UNKNOWN or unsupported source structures must not be guessed or
silently discarded.

Phase 03 implementation is authorized. Batch 1 is closed / pass at `0f7cff4`.
The current authorization is limited to Batch 2, Structural OBC Projector.
Canonical corpus materialization, run storage/full-corpus projection, semantic
normalization or merge, and cross-snapshot identity/reuse are not authorized
in Batch 2.

The following remain outside the authorized current batch. Later Phase 03
batches require their own reviewed authorization:

- Canonical persistence, materialization, or corpus-wide projection
- semantic identity, alias/entity merge, Fact/Claim/Event models
- cross-Parsed-run or cross-snapshot semantic projection reuse
- Retrieval-oriented passage/chunk design
- BM25 / vector / Hybrid retrieval
- Embeddings and vector databases
- Reranking and query expansion
- Retrieval/RAG implementation and prompt orchestration
- AI semantic / claim extraction
- Knowledge graph / semantic layer
- UI

## Source of truth

Current stage and immediate next action: this file.

Phase 01 workflow and closure evidence: `docs/phases/phase-01-raw-collection.md` and relevant `docs/research/phase-01/` notes.

The closed Phase 02 draft specification is
`docs/phases/phase-02-parsed-schema.md`. The approved Phase 03 contract is
`docs/phases/phase-03-canonical-schema.md`. Implementation must also follow
`docs/architecture-overview.md`, `AGENTS.md`, and the verified OBC evidence
already recorded in the repository.
