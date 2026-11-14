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
The retained Phase04 implementation uses the preserved Parsed/Canonical/RU
contracts below; its retired local Qwen assets are not an operational fallback.
Current adoption belongs to `docs/current-phase.md`.

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

### Clean-based chunking

An opt-in derivative consumes versioned Clean records, applies scoped story
admission and adapts source structures/graphs into a shared packing core.
The operator-selected direction searches micro and macro candidates in parallel,
with balanced as the default chunking profile. Candidates each carry retrieval-visible text, CleanRef coverage,
natural-parent identity and local source scopes. Complete source parents remain
readable independently of admission. Exact repeated voice representations keep
all occurrence references. Source provenance and commentary memberships remain
explicit, including unknown authority. The [V2 design](phases/chunking-v2.md)
owns provisional formats and operating points. Quotable body, necessary source
context and structural metadata have separate retrieval responsibilities.
Source conditions retain their literal evidence and unknown status, and sequence
numbers express display order. Later indexing, retrieval,
reranking and Evidence Assembly own their respective adoption and fusion policies.

### Clean-based indexing input

The opt-in `indexing/` package projects completed V2.1 artifacts into a shared
Chunk-keyed input table. Micro/macro routes contain membership and display-order
mappings. Both lexical and Dense representations retain the original Chunk,
Parent, Clean observation and Raw lineage. Six lexical text fields separate
source title, headings, field labels, body, delivered context and literal
conditions. Dense consumes unchanged retrieval_text. Byte/hash locators keep
complete Chunk, Parent and graph structures available for readback.
Representation identities bind the selected input bundle, granularity and
explicit analyzer or vector-space configuration. The
[input contract](phases/indexing-input-v1.md) owns these interfaces.
The opt-in [index experiment launcher](phases/index-benchmark-v1.md) adds isolated
serial local encoders, transactional shared Dense vector shards and separate
macro/micro Whoosh3 BM25F indexes. Exact cosine search filters shared vectors by
route. Lexical statistics are computed independently per route and physical
field/analyzer channel. Completion follows source/mapping/artifact validation.
Retrieval ordering and final Packet assembly retain their respective layer owners.

### Clean-based Retrieval

The opt-in `retrieval/clean_based/` package discovers candidates over the selected
indexes with shared Chunk identity. Local runtime workers preserve independent
query/index bindings; one Dense scan supplies both route lists. Replaceable F0/F1
fusion and conservative source-range organization emit auditable candidates,
not Packets. Full parent readback and final context budgets belong to Generation.
`indexing/sparse_trial.py` reuses SciPy CSR for a bounded, separately bound Sparse
trial; it is not a full-corpus build or Serving engine. Its
[contract](phases/clean-retrieval-v1.md) owns interfaces and current experimental
parameters; current-phase owns authorization and measured acceptance.
The separate `indexing/sparse_full.py` / `sparse_job.py` path adds bounded,
shared-encoding CSR shards, exact streaming search and standalone manual build,
resume and independent validation under [Sparse build V1](phases/sparse-build-v1.md).
It retains the trial's space contract and does not integrate S1/S2 into Serving.
The separate [P4 experiment](phases/recall-experiment-v1.md) captures all three
channels once per question and reuses them across opt-in S0/S1/S2 and fusion
controls. Its recoverable checkpoints and source-text diagnostics do not add
Reranking, Generation assembly, query rewriting or a new Serving selector.

### Clean-based Reranking and runtime migration

The opt-in Clean-based Reranking path consumes
fixed CandidateSets and emits complete RankedCandidateSets. Dedicated local
Cross-Encoders score relevance; conservative source coverage and optional local
MMR record priority adjustments without deleting candidates. Generation retains
source/Parent readback and Packet assembly. The separate `rerank_handoff.py`
interface applies the selected policy to exact bound scores and exports full,
self-contained RankedCandidateSets with source/recall fields and adjustment
records. `config/reranking-selected-v1.json` owns the executable selection;
the original comparison config and reports remain frozen. Handoff validation
and explicit fallback do not invoke new inference or integrate Serving.
Python runtime migration is admitted
only by an explicit sealed compatibility record against immutable index identity;
it does not rewrite representation metadata. The
[Reranking contract](phases/clean-reranking-v1.md) owns these interfaces.

### Clean-based Generation workbench

The opt-in [Generation contract](phases/clean-generation-v1.md) connects new
versioned research queries to the selected local retrieval/ranking primitives.
Isolated Job-owned workers exit between Dense and BGE GPU use. Generation's
verified source handles and immutable read receipts retain Clean/Raw lineage;
deterministic whole-structure admission emits provider-neutral Packets with
explicit duplicate, unread, failed-source and budget dispositions.
`rag/` owns a local SQLite/ LangGraph Mock persistence skeleton and finite
workbench; the session bridge delegates attempts and retries to the unchanged
shared caller. Checkpoint progress and immutable dispatch/accounting evidence
have separate authority and are reconciled on resume. New Generation Chat
modules decode native tools and JSON/SSE under exact-route capability manifests;
a separately approved finite synthetic probe archives raw bytes, usage and tool
receipts. Only complete locally validated tool batches can execute. G4 adds an
independent Streamlit frontend and background local executor over a
fixed chain, with same-chat durable history, typed corrections and extractive
context indexes. Citation addresses include turn and Packet identity; source-bound
Parent receipts remain viewable. Restart displays saved work and requires explicit
continuation. No cross-chat Store, cloud tracing, adopted default frontend or
automatic live dispatch is introduced. Mock is the entry default. Explicit finite
G4 live mode reuses S0/BGE, source Assembly and G3's unchanged sender/shared caller;
all chats share its expiring campaign budget. Literal user context can accompany
follow-up queries; this fixed chain adds no autonomous research controller.
This independent workbench does not migrate the retained Phase04 backend/UI.

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
Full-text and vector representations are rebuildable derivatives. In the
retained Phase 04 path, Retrieval and Evidence Assembly derive citation/evidence
identity from Canonical lineage, not embedding chunk identity. Clean-based
derivatives bind identity to Clean observations and Raw lineage under their
approved contract. Candidate retrieval and final Evidence Assembly remain
distinct: top-k candidates are not automatically final RAG context.
Evidence Packets remain provider-neutral, deterministic evidence interfaces.
In the Clean-based target architecture, the Generation workstream owns the
candidate-to-Packet assembly policy after Retrieval and Reranking: source/parent
readback, duplicate handling, context-budget admission and final Packet
composition. Retrieval owns candidate discovery; Reranking owns candidate
ordering. Generation must not take over either upstream responsibility. This
is a workstream boundary, not a requirement to move the legacy Phase 04
implementation into `generation/`.

The retained Phase 04 serving path continues to use the adopted
retained-evidence combined Assembly. It preserves control-visible independent
evidence, audits same-occurrence aliases and display projections, and admits
whole roots using released budget. The original Formal entry point provides
explicit rollback. This describes the existing legacy runtime; the Clean-based
target ownership above does not retroactively move or change this
implementation. Current operating selectors belong to current-phase.

In the retained Phase 04 runtime, the local product path composes the
existing retrieval/ranking and Assembly interfaces through `rag/backend.py`;
`generation/` owns provider adaptation and `ui/` owns presentation. Provider and embedding selection must bind explicit,
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

The retained Phase04 CLI/backend/UI still describe their historical Qwen paired
profiles, but the local weights/indexes were retired; they must not be presented
as working rollback. The selected Clean-based indexes have an independent opt-in
Retrieval path and do not imply end-to-end RAG adoption. The historical delivery
contract remains in [Phase 04](phases/phase-04-retrieval-evidence-assembly.md#basic-rag-v1-delivery-contract).

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
