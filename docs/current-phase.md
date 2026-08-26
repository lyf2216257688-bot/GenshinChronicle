# Current Phase

## Active phase

**Phase 03 — Canonical Schema Design (planning only)**

## Objective

Design the Canonical-layer schema from accepted Phase 02 evidence. No Canonical
implementation is authorized by this status transition.

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

## Immediate next action

Phase 03 Canonical Schema Design / planning only. Do not begin Canonical
implementation until its design is reviewed and separately authorized.

## Phase transition boundary

Phase 02 is closed. Its Parsed contracts remain source-specific and
evolutionary; UNKNOWN or unsupported source structures must not be guessed or
silently discarded.

Canonical schema and normalization design are authorized for Phase 03 planning
only and remain provisional. Canonical implementation, persistence, and
materialization are not authorized.

The following remain outside the authorized Phase 03 planning scope:

- Canonical implementation, persistence, or materialization
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
`docs/phases/phase-02-parsed-schema.md`. Phase 03 planning must also follow
`docs/architecture-overview.md`, `AGENTS.md`, and the verified OBC evidence
already recorded in the repository.
