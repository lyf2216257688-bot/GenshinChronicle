# Current Phase

## Active phase

**Phase 01D — Collector v0.1**

## Objective

Collect the complete `mihoyo_obc` Chinese (`zh-cn`) Raw API corpus as a repeatable, resumable, auditable dataset.

## Current status

Repository initialization is complete. Discovery and contract verification phases 01A–01C are complete.

Formal Collector v0.1 implementation has **not** started yet.

## Immediate next action

制定并审阅 Collector v0.1 implementation plan。

## Gate before collector implementation

The 01A–01C evidence gate is satisfied. Collector implementation may proceed against the promoted Phase 01 source contract. The `x-rpc-wiki_app: genshin` header remains an UNKNOWN requirement and may be checked by a minimal smoke test; it does not block 01D.

Verified gates:

- channel tree structure;
- representative channel listing endpoint behavior (pagination/termination remains UNKNOWN);
- the current stable retrievable content key (`content_id` is verified for the current Phase contract);
- representative detail endpoint behavior across multiple content types;
- observed request parameters; required-header behavior remains UNKNOWN, including whether `x-rpc-wiki_app: genshin` is required.

## Explicitly paused

- Parsed schema design
- Canonical field/schema design
- Passage implementation
- Retrieval/RAG
- Embeddings
- AI semantic analysis

## Source of truth

Detailed Phase 01 workflow: `docs/phases/phase-01-raw-collection.md`.
