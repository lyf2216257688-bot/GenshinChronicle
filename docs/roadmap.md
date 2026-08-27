# Roadmap

This roadmap describes direction, not a commitment to pre-design later phases.

## Phase 00 — Repository initialization

Status: **complete**

Deliverables:
- repository instructions;
- architecture overview;
- active-phase specification;
- data/Git safety boundaries;
- empty implementation/test skeleton for the collector.

## Phase 01 — Raw Collection

Status: **complete / CLOSED**

Goal: obtain the complete, auditable `mihoyo_obc` `zh-cn` Raw corpus.

High-level sequence:

```text
API discovery
-> channel inventory
-> detail-endpoint verification
-> collector v0.1
-> staged crawl validation
-> full Raw crawl
-> manifest / coverage report
-> Raw corpus profiling and stratified sampling
```

## Phase 02 — Parsing

Status: **complete / CLOSED**

The source-specific Parsed foundation closed with auditable OBC Raw-to-Parsed
acceptance. Parsed outputs remain source-specific, provisional, and
evolutionary; closure does not freeze their draft schema or unknown handlers.

## Phase 03 — Canonical Corpus

Status: **complete / CLOSED**

The approved `CanonicalRecord -> CanonicalSection -> CanonicalUnit` contract,
structural OBC projector, dialogue/structured-observation preservation,
immutable run storage, representative acceptance, and the separate 16,437
record production gate are complete. This establishes an auditable OBC `zh-cn`
structural Canonical corpus, not semantic completeness or cross-snapshot
semantic identity.

## Phase 04 — Retrieval / RAG

Status: **architecture/design next; implementation tentative**

Design Retrieval / Evidence Assembly from the accepted Canonical evidence.
BM25, vector, hybrid retrieval, chunking, embeddings, reranking, and other
technology choices remain unselected; implementation requires later approval.

## Later semantic layers

Entity resolution, Claims, Events, timelines, knowledge graphs, multilingual alignment, and other AI-semantic structures are not assumed requirements. Add only the smallest structure justified by real research failures after the corpus and RAG exist.
