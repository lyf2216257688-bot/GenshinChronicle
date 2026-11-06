# Phase 04 — Retrieval / Evidence Assembly

Status: **Phase 04 basic RAG V1 is the retained serving delivery contract.
The previously adopted Block A remains the executable baseline while V1 changes
are implemented and validated. Current work-unit status, authorization and
adoption are owned by [`docs/current-phase.md`](../current-phase.md).**

The separately approved [Clean V1 reconstruction](clean-v1.md) owns direct
Raw extraction and its replacement-foundation readers. This document continues
to own the deployed Canonical/RU serving path and its acceptance requirements.

> Historical checkpoint / current relation: the P04-W1 authorization and its
> non-authorization statements below apply to that completed work unit. They
> do not override the later implemented post-W7 first end-to-end RAG path.
> Current production/default behavior and authorization are solely owned by
> `docs/current-phase.md`.

The [V1 delivery contract](#basic-rag-v1-delivery-contract) below extends the
existing implementation. The historical P04-W1 exclusions are not V1 exclusions.

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
- Benchmark and evaluation tooling follows the repository-level principles in
  [`docs/architecture-overview.md`](../architecture-overview.md) (evaluators serve
  as decision tools, not standalone products; measure before optimizing).
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

## Basic RAG V1 delivery contract

### Goal and dependency boundary

Deliver one stable local workflow: question -> official-source retrieval ->
BGE reranking -> deterministic Evidence Packet -> generated answer -> traceable
original-text citations. Reuse the accepted Raw, Parsed, Canonical and RU
snapshot; do not rebuild the foundation to accommodate a model change.
Phase 05 NaviRAG/Card/Link/KG is paused and is not a V1 dependency or release
gate. Its code, research, failures, model-call archives and inactive outputs
remain preserved. Any future resumption must address a measured basic-RAG
bottleneck under a separately bounded scope.

Retain BM25, Dense/RRF Hybrid, local BGE, versioned Evidence Assembly,
provider-neutral Generation and the original citation resolver.
The existing library/backend and thin Streamlit presentation are the first
delivery vehicle; add only the usability needed for reproducible local use.
A new UI framework, public cloud service, vector database, ANN engine, semantic
precomputation and a new large benchmark system are not V1 prerequisites.

### Ordered work and implementation seams

The operator may authorize independent offline migration preparation while a
Generation issue remains open. A default selector choice does not regrade a
failed live test or grant additional paid calls; current choices and permits
remain owned by current-phase.

1. **Validate DeepSeek answer generation.** Keep the accepted retrieval,
   reranker and Packet fixed for the first comparison. Implement a small
   `GenerationProvider` adapter and explicit provider selection through the
   existing backend/UI factory. Reuse `GenerationRequest`, `GenerationResult`
   and citation checks; retain Bailian/Qwen as an explicit control/rollback.
   `BailianControlConfig` remains the Qwen control, not a generic model selector;
   use a separate adapter selected by the same UI/runtime factory. Existing
   Phase 05 DeepSeek transports provide protocol evidence, not final-QA quality
   acceptance. Implementation/adoption status belongs to current-phase. Freeze
   the exact route/model, supported request parameters, timeout and finite
   request/token limits for the first live work unit; do not copy Card JSON
   schemas, 32k caps or model-specific retry policies into final QA by default.
   New/materially changed API execution uses `genshin_corpus.model_calls`
   with raw-first archives, task validation and bounded recovery. Keep the
   adopted legacy path unchanged until explicitly migrated. UNKNOWN does not
   authorize resending; no silent cross-provider fallback.
2. **Migrate original RU embeddings to local Qwen3-Embedding-0.6B, 1024d.**
   Reuse the pinned snapshot, encoder, identity-bound `EmbeddingBatch`,
   small-index guards and qualified isolated runtime. The existing 909-RU
   deployment pilot is not a full-corpus index or acceptance. Generalize
   `rag/backend.py` preparation/query injection and `rag/config.py` artifact
   selection: the original path requires the accepted online-Qwen manifest and
   2048d queries. Keep that validator as the old-profile guard; introduce
   explicit compatible profiles instead of weakening validation globally.
   The generic Dense loader already checks dimensions from manifests, but
   dimension equality alone is insufficient for vector-space compatibility.
3. **Accept the complete local workflow using real questions.** Keep the
   historical retrieval pair as a control. Use the same questions, RU snapshot,
   BM25, Hybrid, reranker and Assembly settings to compare retrieval
   independently of Generation. Diagnose actual data, retrieval,
   Assembly, Generation or question/source-coverage failures before tuning.
   Do not reopen Phase 05 simply because a question remains difficult.

### Full-index migration and rollback requirements

- Freeze the source RU manifest/hash/build and row-order mapping; account for
  every one of the accepted 535,802 RUs without changing RU IDs, text, source
  locators or upstream artifacts. Build a fresh derivative root. Preserve the
  old 2048d manifest, vectors, query contract and selectors for comparison and
  explicit rollback; never overwrite them with the new model's vectors.
- Bind model revision and file hashes, query instruction, document projection,
  pooling, normalization, dimension, compute dtype/device and runtime to both
  query and index identities. The qualified CUDA FP16 and CPU FP32 contracts
  are distinct; a device change requires a compatible index/profile, not an
  automatic fallback. Never mix online 2048d queries with local 1024d documents.
- Replace the pilot's all-at-once encoding/writing with bounded batches or
  shards, persisted progress and failure accounting. Resume only verified
  completed work of the same build/encoding contract. Interrupted or incomplete
  artifacts cannot become the serving index; publish/select only after full
  row/hash/dimension/finiteness/L2 and RU-lineage checks pass.
- Audit token lengths before choosing full-corpus encoding. The pilot uses a
  2048-token limit and records right truncation; its 909 RUs were untruncated.
  Measure full-corpus coverage before encoding. If any RU exceeds the limit, define and
  evaluate an explicit window/projection policy retaining original RU identity,
  exact ranges and audited coverage. Windows are retrieval derivatives, never
  new citation authorities. Silent prefix truncation is not full coverage.
- Plan disk/workspace and recovery headroom. A single 535,802 x 1024 FP32
  matrix alone is about 2.04 GiB; row mappings, shards, models and retained old
  artifacts are additional. Pilot throughput cannot establish full-build time.
- Preserve the separate Embedding and BGE environments. Reuse/generalize the
  existing process-worker seam where useful; measure model load/unload,
  interprocess overhead, RAM/VRAM peaks and repeated queries. The pilot tested
  Embedding then BGE separately, not simultaneous residency. Choose a measured
  lifecycle policy before adopting the local serving path.
- Switch query encoder and index together via one explicit validated profile.
  Run the same acceptance questions on the old profile after a rollback drill.
  Missing local resources or incompatible identities fail closed, with an
  actionable error; users may explicitly choose the preserved control profile.

### Practical evaluation and release gates

The operator adopted the existing 2026-11-04 retained-Packet combined Assembly
as the shared backend/CLI/Streamlit default. It preserves independent control-visible evidence,
suppresses only proved visible same-occurrence aliases with equal display
labels, audits conservative structured HTML display edits against original RU
ranges, and refills only whole omitted Top20 roots under the existing gates.
Original RU text and lineage remain in the Packet. The explicit `formal`
selector uses the original Formal Deferred Assembly. Current selectors and
adoption are owned by current-phase. The bounded contract and results are in the
[Assembly experiment](../research/phase-04/p04-assembly-retention-70q-20261104.md).

Reuse `.local/p04-rag-m2/questions.runtime.jsonl` (70 historical questions),
its separate review-only sidecar, existing story QA/review materials and a
small selection of actual user questions. Select and record a bounded scope
before execution; a targeted subset does not claim all-70 or corpus-wide
quality. Reference answers and prior judgments remain review-only and never
enter Retrieval, Packets, Generation prompts or runtime request identity.

For each selected question, preserve the question/config/profile identities,
candidate windows, selected Packet, answer, citation result, failures,
stage/total timing and available usage/resource observations. Existing backend
results already expose candidates, Packet/Generation persistence and stage
telemetry; extend only the missing measurements. Report:

- **Answer and evidence separately:** PASS / PARTIAL / FAIL with a short
  source-based reason. Check factual accuracy, completeness, attribution,
  uncertainty and whether the cited original text supports each key claim.
  Citation-ID validation is structural; `semantic_faithfulness=not_evaluated`
  must never be presented as semantic PASS.
- **Recall:** verify relevant original evidence at candidate depth and in the
  final Packet for diagnosed cases; use Hit/Recall at fixed depths where a
  reviewed target is available, with explicit denominators. Report losses
  after reranking/Assembly separately. Avoid inventing exhaustive gold labels.
- **Usability and cost:** cold preparation and warm query latency, per-stage
  times, request counts/tokens, build throughput/time/disk, peak RAM/VRAM and
  failures. Actual monetary cost remains UNKNOWN unless independently known.
  Set practical machine-specific latency/resource budgets before the measured
  run, rather than declaring success from pilot speed or guessed targets.

V1 release requires a documented local launch with reproducible prerequisites,
repeated successful question/answer/citation inspection and restart/reload,
working `evidence_only`, explicit failures on missing resources, and the
rollback drill. The local interface must make source titles/locators and the
original Packet inspectable from answer citations; the current UI's evidence
text display and persisted full Packet provide a starting point, not proof of
completed citation-browsing usability. All chosen cases must have terminal
accounting; no unresolved
index/lineage mismatch, missing citation target, silent evidence loss or
unaccounted model attempt is acceptable. The reviewed acceptance cases must
have no unresolved blocking factual or citation-support defect; difficult or
unsupported questions must expose the evidence limit. Record residual PARTIAL/
FAIL cases and compare old/new quality and performance. The operator can
select a validated, identity-matched serving profile independently of this
comparison. Human/product acceptance
and an explicit adoption record in `docs/current-phase.md` are required before
calling V1 released. Offline tests alone do not establish answer quality.

### Invariants versus operating choices

Immutable source evidence, exact upstream lineage, provider-neutral Packet
boundaries, no semantic contamination, audited omissions and fail-closed
identity validation remain engineering constraints. DeepSeek-first generation,
Qwen3-0.6B/1024d, BGE, candidate depths, fusion weights, context/output budgets
and local interaction form are versioned stage choices, not permanent vendor,
dimension or architecture restrictions. Their implementation/adoption status
belongs only to `docs/current-phase.md`; this contract defines acceptance, not
proof that any pending migration has already passed.
