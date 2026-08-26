# Current Phase

## Active phase

**Phase 02 — Parsing**

## Objective

Build an evidence-driven, source-specific, deterministic and auditable Parsed layer over the completed `mihoyo_obc` Chinese (`zh-cn`) Raw corpus while preserving Raw traceability, meaningful source structure, and unsupported/unknown content.

## Current status

Repository initialization, Collector v0.1, discovery/contract verification (01A–01C), offline acceptance, and the scoped Collector security gate are complete; `GC-COLLECTOR-001` and `GC-COLLECTOR-002` are closed with 0 remaining findings. P01-EA was completed and checkpointed at `0c5617d`.

P01-EB full crawl (`run_id=p01eb_full_20260824`) is complete and locally auditable: 96/96 listing responses, 32,916 listing records, 16,437 unique `content_id` values, and 16,437/16,437 successful detail responses, with 0 final unresolved failures. Archive/hash/inventory audit and same-run recovery passed. OBC full-corpus profiling is complete. Current production scope is `zh-cn` + OBC only. These results are run-level and contract-bounded; `manifest complete` is not a claim of absolute semantic completeness of the upstream server.

Phase 02 Parsed Schema / Parser Architecture planning and all four implementation batches are complete. Batch 4 added the executable OBC Raw-to-Parsed run pipeline, immutable Parsed record storage, Raw dependency accounting, versioned reuse/reparse decisions, and deterministic corpus-wide acceptance. The local P01-EB acceptance run accounted for 16,437/16,437 completed Raw details with 0 `blocked_integrity` records. This is an auditable local parsing result, not a claim of absolute upstream semantic completeness. The schema remains a design draft rather than a frozen contract.

## Immediate next action

Conduct Phase 02 acceptance/review of the completed Parsed foundation before any decision about a later phase.

## Phase 02 boundary

Phase 02 is active. Parsed schema and parser architecture may now be designed and implemented incrementally from verified OBC Raw evidence.

The current schema is not frozen. UNKNOWN or unsupported source structures must not be guessed or silently discarded.

The following remain outside the current phase:

- Canonical entity/schema normalization
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

The Phase 02 draft specification is `docs/phases/phase-02-parsed-schema.md`. Phase 02 work must also follow `docs/architecture-overview.md`, `AGENTS.md`, and the verified OBC evidence already recorded in the repository.
