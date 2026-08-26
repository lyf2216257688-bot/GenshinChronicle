# Current Phase

## Active phase

**Phase 01 — Raw Collection (CLOSED)**

## Objective

Collect the complete `mihoyo_obc` Chinese (`zh-cn`) Raw API corpus as a repeatable, resumable, auditable dataset.

## Current status

Repository initialization, Collector v0.1, discovery/contract verification (01A–01C), offline acceptance, and the scoped Collector security gate are complete; `GC-COLLECTOR-001` and `GC-COLLECTOR-002` are closed with 0 remaining findings. P01-EA was completed and checkpointed at `0c5617d`.

P01-EB full crawl (`run_id=p01eb_full_20260824`) is complete and locally auditable: 96/96 listing responses, 32,916 listing records, 16,437 unique `content_id` values, and 16,437/16,437 successful detail responses, with 0 final unresolved failures. Archive/hash/inventory audit and same-run recovery passed. OBC full-corpus profiling is complete. Current production scope is `zh-cn` + OBC only. These results are run-level and contract-bounded; `manifest complete` is not a claim of absolute semantic completeness of the upstream server.

## Immediate next action

Phase 02 Parsed Schema Design

## Phase 02 boundary

Phase 02 Parsed Schema Design has not started. No Parsed schema design or implementation is authorized by this status document.

Future phase boundaries remain:

- Parsed schema design
- Canonical field/schema design
- Passage implementation
- Retrieval/RAG
- Embeddings
- AI semantic analysis

## Source of truth

Detailed Phase 01 workflow: `docs/phases/phase-01-raw-collection.md`.
