# Phase 04 — Retrieval / Evidence Assembly

Status: **ACTIVE — durable Phase 04 architecture boundary and historical
P04-W1 contract; current work-unit status is owned by
`docs/current-phase.md`**

> Historical checkpoint / current relation: the P04-W1 authorization and its
> non-authorization statements below apply to that completed work unit. They
> do not override the later implemented post-W7 first end-to-end RAG path.
> Current production/default behavior and authorization are solely owned by
> `docs/current-phase.md`.

## Authorization and boundary

Phase 04 starts from the accepted immutable `mihoyo_obc` / `zh-cn` Canonical
run. The completed P04-W1 work unit was authorized to add a read-only corpus
profiler, a Retrieval benchmark contract, a small evidence-grounded seed
benchmark, and the minimum supporting documentation/tests.

P04-W1 did not authorize a production retrieval engine, BM25, Dense or Hybrid
selection, embeddings, vector stores, reranking, query expansion, RAG
orchestration, Entity/Fact/Event models, a knowledge graph, or changes to Raw,
Parsed, or Canonical contracts.

W6 separately completed a bounded Dense family-isolation pilot on the fixed
`contextualized_leaf` arm and frozen benchmark-v0.3. Its result is historical
experiment evidence, not a permanent Dense winner or global technology ban.
Later production/default and challenger decisions are recorded only in
`docs/current-phase.md`.

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
- No BM25/Dense/Hybrid/embedding/vector database/reranker winner is frozen as
  a durable architecture decision. Current production baselines are operating
  choices, not permanent winners.
- W6 Dense evidence is closed for that pilot only; its follow-up boundaries do
  not supersede post-W7 Phase 04 authority.
- At the completed P04-W1 checkpoint, no Phase 03 Retrieval integration blocker
  was known. Observation-local IDs, heterogeneous values, incomplete roles, and
  dialogue uncertainty were Retrieval concerns to handle in rebuildable
  derivatives.

## Historical P04-W1 deliverables and acceptance

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
