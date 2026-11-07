# Roadmap

This roadmap describes direction, not a commitment to pre-design later phases.

## Clean V1 reconstruction

The approved reconstruction target uses Raw -> Clean -> Chunking -> Indexing ->
Retrieval -> Reranking -> Generation. The Clean work unit owns direct extraction,
structure and tag preservation, source resolution, file builds and a bounded
representative pilot. Its contract is [Clean V1](phases/clean-v1.md).
The following finite [V2 chunking pilot](phases/chunking-v2.md) explores scoped
admission, shared structure-aware packing, independently searchable micro/macro
representations and natural-parent readback on actual Clean inputs.
Existing Phase04 serving assets remain the executable baseline while replacement
layers obtain their own implementation and adoption decisions. Current work-unit
status is owned by [current-phase](current-phase.md).

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

Status: **SERVING BASELINE — basic RAG V1; existing Block A remains adopted**

The first end-to-end baseline now includes rebuildable Retrieval Units,
BM25/local Dense/RRF Hybrid candidate retrieval, deterministic Evidence
Assembly, Evidence Packets, and provider-neutral Generation contracts.
Production/default configuration, challenger status, and authorization remain
intentionally outside this phase-level roadmap and are owned by
`docs/current-phase.md`. The first implementation does not freeze a permanent
retrieval winner, Dense model, reranker, vector database, or Generation
provider.

The 2026-11-02 priority change reused Phases 01-03 and the existing RU/index,
Assembly, Generation and local presentation modules. Serving-baseline milestones are
staged so that real failures can be attributed; an unresolved Generation issue
does not block independently authorized offline Embedding preparation:

1. **DeepSeek QA:** reuse the implemented provider/shared accounting and the
   operator-selected V1 default; retain explicit Qwen rollback. The closed
   fixed-Packet live evidence and open cap issue stay separate from adoption
   and future paid authorization; current facts belong to current-phase.
2. **Local original-RU embeddings:** use the completed Qwen3-Embedding-0.6B /
   1024d index and matching query encoder by default. Keep the historical 2048d
   pair available as explicit rollback. Use the repeatable 70Q Packet
   comparison to identify retrieval and Assembly weaknesses after adoption.
3. **Local V1 acceptance:** reuse the backend/Streamlit entry point for repeated
   real questions, traceable citations, restart/resource/error checks and
   measured latency/usage. Use existing 70Q/story materials plus actual questions;
   use the existing 70Q comparison and record residual quality limits before
   release.

Detailed acceptance and migration requirements belong to the
[Phase 04 delivery contract](phases/phase-04-retrieval-evidence-assembly.md#basic-rag-v1-delivery-contract).
Current progress and the immediate next action remain in `docs/current-phase.md`.
These model/dimension choices are stage operating choices, not permanent
architecture restrictions. Basic V1 does not depend on Phase 05 completion.

## Phase 05 — Source-Bound Semantic Navigation

Status: **PAUSED — resume only from demonstrated basic-RAG needs**

Phase 05 provides a versioned source-bound semantic build lineage, distinct
hierarchy and event/relation graph views, and bounded provider-neutral
navigation over official RU candidates. It preserves Phase 04 Block A,
Evidence Packet authority, and the Generation-visible contract. P05-W1 proves
only mechanical contracts and does not establish semantic extraction quality,
corpus-scale view quality, answer improvement, or production/default adoption.

NaviRAG, Card generation/repair, Link improvements and KG construction are not
the current next steps. Preserve all implementations, historical results,
failed attempts and inactive artifacts. The pause is a priority decision,
not a declaration that these capabilities failed or are permanently rejected.
After V1 use exposes a concrete navigation/semantic bottleneck, select the
smallest useful capability, bounded cost and source-based acceptance scope
before separately authorizing resumption.

## Later semantic layers

Global entity resolution, Claims, timelines, multilingual alignment, and other
AI-semantic structures beyond the bounded P05 contract remain conditional. Add
only the smallest structure justified by later semantic/live evidence.
