# Architecture Overview

## Purpose

Build a reliable official-text foundation. More ambitious semantic research
features are optional layers, not requirements of the foundational phases.

## Long-term pipeline

```text
SOURCE / RAW
    -> CLEAN
    -> CHUNKING
    -> INDEXING
    -> RETRIEVAL
    -> RERANKING
    -> GENERATION
```

The Clean V1 reconstruction implements the new foundation and its read interface.
The deployed Phase04 runtime continues to use the preserved Parsed/Canonical/RU
assets described below. Current adoption belongs to `docs/current-phase.md`.

### 1. Source / Raw
Answers: **What did we obtain, from which official source, and when?**

Architectural concepts include source system, source item, source version, raw
payload, crawl run, and crawl observation. These are concepts, not a request to
implement a database now.

Raw is evidence and reprocessing input. It is not the normal application query layer.

### Clean V1 reconstruction

Clean directly binds one source observation to immutable Raw detail and discovery
evidence. A record preserves ordered content blocks, source fields, tables,
dialogue groups and edges, scoped evidence-backed tags, relations and media.
Shared source locators support decoded JSON and HTML text ranges. Readable text
is an on-demand projection. Chunking owns content selection and representation.
The [Clean contract](phases/clean-v1.md) owns schema, readers, validation and
incremental-build behavior. Parsed/Canonical logic remains available for reuse
and historical citations.

### 2. Preserved Parsed baseline
Answers: **What deterministic structure can a source-specific adapter recover from Raw?**

Parsed schemas may remain source-specific. Unification is not required here.

Rules:
- deterministic extraction only;
- unknown important structures must be surfaced;
- parsing rules must be based on observed source schemas;
- do not pretend natural-language semantic judgments are deterministic parsing.

### 3. Preserved Canonical baseline
Answers: **What stable research document structure do we maintain independent of a source's page layout?**

The approved minimum hierarchy is `CanonicalRecord -> CanonicalSection ->
CanonicalUnit`, supported by `CanonicalObservation`, `ComponentContext`, and
`LineageLink` value objects. Source identity remains separate from the
observation-scoped Canonical record ID; Canonical semantic identity is not yet
established.

Canonical preserves source text and source values as represented by Parsed.
Representation may be normalized through explicit deterministic rules;
meaning, wording, provenance, and semantic equivalence must not be silently
corrected or inferred.

The architecture supports additional sources and locales while preserving
source-specific lineage. It establishes no cross-source semantic identity,
deduplication, or equivalence policy; any such behavior requires an explicit
contract.

### 4. Existing Derived Retrieval / RAG
Full-text and vector representations are rebuildable derivatives. Retrieval
and Evidence Assembly derive citation/evidence identity from Canonical lineage,
not embedding chunk identity. Candidate retrieval and final Evidence Assembly
remain distinct: top-k candidates are not automatically final RAG context.
Evidence Packets are provider-neutral, deterministic evidence interfaces;
Generation consumes them without owning retrieval or assembly policy.

The adopted retained-evidence combined Assembly preserves control-visible
independent evidence, audits same-occurrence aliases and display projections,
and admits whole roots using released budget. The original Formal entry point
provides explicit rollback. Current operating selectors belong to current-phase.

The local product path composes the existing retrieval/ranking and Assembly
interfaces through `rag/backend.py`; `generation/` owns provider adaptation and
`ui/` owns presentation. Provider and embedding selection must bind explicit,
compatible versioned configurations rather than alter evidence identity.
Query encoders and document indexes must share the same vector-space contract;
equal dimensions alone do not prove compatibility. Replacing embeddings builds
new derivatives over existing RUs and preserves an explicit rollback profile.

### 5. Phase 05 Semantic Navigation Derivatives

This section describes the retained optional layer. Development is paused in
favor of Phase 04 basic RAG V1 per `docs/current-phase.md`; none of its semantic
builds or acceptance gates is a prerequisite for local basic QA.
Phase 05 adds a rebuildable, source-bound semantic build lineage over
Canonical-first inputs and verified Retrieval Unit locators. It produces two
distinct non-authoritative views: a cross-record hierarchy for coarse-to-fine
navigation and an event/relation graph for bounded lateral traversal.
Rather than forcing both views to consume a monolithic model extraction layer,
Navi (narrative exploration across dispersed documents) and KG (conservative
local relations and dialogue branches) maintain distinct semantic responsibilities
and separate versioning while sharing official source identities. Source
binding and semantic acceptance are separate states; binding to an RU or
Canonical lineage does not prove semantic interpretation. Rejected,
unsupported, ambiguous, or unaccepted items remain auditable and inactive.

Phase 05 navigation may resolve candidates only through official Retrieval
Units. Retrieval owns the deterministic candidate/admission primitive feeding
the unchanged Formal Deferred Assembly; `rag/` owns online decisions and route
lineage. Online query-time navigation (P05-W1) is bounded, deterministic
traversal over accepted views and does not invoke unconstrained live LLM agent
loops. Multi-turn narrative exploration (E1/E2) operates as offline research
(controlled research decoupled from the online QA serving chain, not network
offline; remote model APIs are permitted under separate operator authorization)
under explicit budgets and human review. The final Evidence Packet remains the only citation
authority, and Generation does not consume semantic navigation traces.

RAG answers are query results. They must not automatically become Canonical facts.

## Cross-layer invariants

`genshin_corpus.model_calls` owns shared model-call attempts, request budgets,
retry decisions, interrupted-call recovery and per-channel cooldown. The small
`evidence_io` module reuses the existing create-only/hash/seal contract formerly
owned by the offline package. Adapters retain raw HTTP/CLI evidence and separate
reasoning, usage, protocol and task-content validation. The caller owns source
selection, semantic contracts and live authorization. No call output acquires
Canonical authority. Current migration coverage and legacy exceptions are listed
in `docs/model-interfaces.md`; adopted Phase04 and frozen runners are unchanged.

- Source/API organization != game-content organization != research interpretation.
- Raw evidence remains reproducible and auditable.
- Derived artifacts must be traceable back to Raw.
- Unknown structures are recorded, not silently dropped.
- Deterministic work belongs in code; semantic interpretation should not be disguised as deterministic parsing.
- Long-term extensibility is preserved without implementing future layers prematurely.

Phase 02 Parsed schema and parser contracts remain source-specific,
deterministic, and evolutionary. Parsed records carry explicit Raw references,
source positions, fingerprints, parser versions, and unsupported/anomaly
status; they must not silently discard unknown structure or take on
Canonical/Retrieval responsibilities. Canonical lineage must identify the
exact Parsed observation and must not fabricate RawRefs for values derived from
Parsed-run dependencies.

## RAG-first product principle & engineering behavior

GenshinChronicle's final product goal is high-quality RAG. Evidence Packets
provide a free/manual path and an auditable evidence-output layer, but must not
irreversibly destroy upstream structural, provenance, or audit evidence merely
for Packet convenience. Deterministic query-time ranking, Top-K, finite-budget
admission, and audited omission are permitted operating-point behavior when
their disposition is explicit and auditable; they are not claims of improved
recall, ranking, or context quality.

The repository follows these durable engineering and evaluation principles:
- **Build → Measure → Optimize**: Construct an end-to-end runnable baseline first, collect real empirical evidence, identify the actual bottleneck, and optimize. Avoid expanding evaluation, benchmark, or governance machinery ahead of a functioning system.
- **Evaluator as Decision Tool, Not a Product**: Benchmark suites and automated evaluators exist solely to guide specific engineering decisions. When evaluator complexity approaches or exceeds the system under test, conduct a cost/value review. RAG failures prioritize minimal explainable root-cause attribution (`data` / `Retrieval` / `Assembly` / `Generation` / `question-source`) before authorizing optimizations.
- **Fixed Invariants vs. Parameterized Operating Points**: Correctness invariants (provenance, fail-closed handling, immutability, deterministic ordering, official citation lineage) are non-negotiable. Operational parameters (Top-K thresholds, candidate depths, context budgets, fusion constants, provider/model choices) are versioned operating points, not permanent architectural winners.

## Current implementation boundary

Phase 01 Raw collection, Phase 02 Parsed foundation, and Phase 03 Canonical
Schema are closed after their accepted corpus gates. The Phase 02 contract in
`docs/phases/phase-02-parsed-schema.md` remains a draft, evolutionary
source-specific contract. Phase 03 completed its approved Canonical contract,
structural OBC projection, deterministic storage, and the separately reviewed
16,437-record OBC `zh-cn` production gate. It establishes structural,
traceable Canonical evidence, not semantic completeness, final dialogue
semantics, cross-snapshot semantic identity/reuse, or Retrieval/RAG quality.

Phase 04 implements the first end-to-end RAG baseline: versioned Retrieval
Units, deterministic BM25/local Dense/RRF candidate retrieval, deterministic
Evidence Assembly, Evidence Packets, and provider-neutral Generation
contracts. Its current production/default configuration and authorization are
owned by `docs/current-phase.md`; this architecture document does not select a
permanent model, retrieval winner, provider, reranker, or serving
infrastructure.

The current delivery direction uses the operator-selected DeepSeek final-answer
and local Qwen3-Embedding-0.6B/1024d retrieval defaults. Those are reversible
operating choices. A transactional streaming builder publishes a
complete index only after source/row/vector verification; the local query
worker exits before BGE uses the GPU. Explicit paired profiles preserve the
old 2048d rollback. The CLI and thin Streamlit UI share backend profile
selection. The detailed delivery/rollback contract is
in [Phase 04](phases/phase-04-retrieval-evidence-assembly.md#basic-rag-v1-delivery-contract).

The paused Phase 05 implementation does not alter the accepted Phase 04 Block A
retrieval/ranking behavior, Evidence Packet authority, or Generation-visible
request contract.
Its provider-free first work unit proves only semantic-build/view and bounded
navigation mechanics; it does not prove semantic extraction quality or answer
improvement.

The standalone offline Navi environment owns adaptive source-bound Card
hierarchy, separate local RU/Card vector indexes and candidate-to-original-text
generation/review. These are inactive research derivatives, separate from the
accepted online paths and from KG. Its pinned embedding worker reuses the
qualified ML runtime through local process pipes; generation SDK dependencies
stay in the orchestration runtime. Current authorization and measured readiness
remain with `docs/current-phase.md`.

The Card-only build path can prepare a whole-corpus snapshot, process adaptive
reading and source-aware section/document synthesis in bounded batches, and
build a new local Card index with exact-window reuse. Card observations,
interpretations and hypotheses remain distinct derived content. If that research
is resumed, Card-led Link work follows Card qualification; Canonical/RU retain
original-evidence authority. The optional attribution sidecar is a diagnostic, not formal factual
review or release authority. None of these paths selects a production default.
