# Current Phase

## Active phase

**Phase 01E — Staged Validation**

## Objective

Collect the complete `mihoyo_obc` Chinese (`zh-cn`) Raw API corpus as a repeatable, resumable, auditable dataset.

## Current status

Repository initialization and Collector v0.1 implementation are complete. Discovery and contract verification phases 01A–01C are complete. Phase 01D offline functional acceptance and the scoped Collector security gate are complete: `GC-COLLECTOR-001` and `GC-COLLECTOR-002` are closed, with 0 remaining findings. P01-EA staged live validation is complete: browser checks verified single-response listings for channels 25 and 130, the full-listing run completed with 200 successful detail fetches, and same-run resume skipped saved responses.

## Immediate next action

完成 P01-EA review 后，决定是否进入下一次受控 live crawl；不得将当前单响应证据外推为永久 API 保证，也不得在未解决 coverage 证据前进入 Parsed/Canonical。

## Gate before collector implementation

The 01A–01C evidence gate is satisfied. Collector implementation may proceed against the promoted Phase 01 source contract. The `x-rpc-wiki_app: genshin` header remains an UNKNOWN requirement and may be checked by a minimal smoke test; it does not block 01D.

Verified gates:

- channel tree structure;
- representative channel listing endpoint behavior (current browser evidence shows one request without pagination parameters for channels 25 and 130; future pagination signals are a hard stop);
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
