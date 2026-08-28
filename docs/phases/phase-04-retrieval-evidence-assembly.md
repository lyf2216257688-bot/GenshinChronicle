# Phase 04 — Retrieval / Evidence Assembly

Status: **ACTIVE — P04-W1 Corpus Profiler + Retrieval Benchmark Foundation complete; human review pending**

## Authorization and boundary

Phase 04 starts from the accepted immutable `mihoyo_obc` / `zh-cn` Canonical
run. P04-W1 is authorized to add a read-only corpus profiler, a Retrieval
benchmark contract, a small evidence-grounded seed benchmark, and the minimum
supporting documentation/tests.

P04-W1 does not authorize a production retrieval engine, BM25, dense or Hybrid
selection, embeddings, vector stores, reranking, query expansion, RAG
orchestration, Entity/Fact/Event models, a knowledge graph, or changes to Raw,
Parsed, or Canonical contracts.

## Approved architectural boundaries

- Canonical is immutable evidence; Retrieval artifacts are rebuildable,
  versioned derivatives and citations resolve through Canonical lineage.
- `CanonicalUnit` is a useful naked-leaf experiment baseline, not the universal
  retrieval document. Representation experiments must retain relevant parent
  structure, ordering, context, and lineage where evidence requires it.
- No fixed retrieval-representation taxonomy is approved. Structured,
  rich-text, and dialogue projections are overlapping evidence views, not three
  automatically independent prose corpora.
- Retrieval candidate selection and Evidence Assembly are separate boundaries;
  retrieved top-k candidates are not automatically final RAG context.
- Text equality never defines evidence identity. Query-local presentation
  suppression may be investigated only if every source occurrence remains
  resolvable with its lineage and citation evidence.
- Wrong-role retrieval is a benchmark correctness concern. UNKNOWN
  provenance/content-role stays UNKNOWN; no fixed role/importance taxonomy or
  `component_id -> priority` contract is introduced.
- No BM25/Dense/Hybrid/embedding/vector database/reranker winner is frozen.
- No Phase 03 Retrieval integration blocker is currently known. Observation
  local IDs, heterogeneous values, incomplete roles, and dialogue uncertainty
  are Retrieval concerns to handle in rebuildable derivatives.

## P04-W1 deliverables and acceptance

The profiler must stream one accepted Canonical manifest in manifest order,
verify each record path and SHA-256, and emit bounded aggregate observations
only. It must not rerun projection or create a second corpus.

The benchmark contract must support a product-weighted main track and
diagnostic slices, evidence locations at existing Canonical/lineage scopes,
relevance, sufficient evidence sets, optional alternatives, and query-specific
assembly metadata. It must not invent semantic or cross-snapshot identities.

The seed set is intentionally small and evidence-grounded. It validates schema
and resolver behavior; it is not a representative quality benchmark or a
technology-selection result.
