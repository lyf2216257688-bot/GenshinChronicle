# Architecture Overview

GenshinChronicle builds an auditable corpus of official text and uses it to answer research questions with traceable evidence. [Current Phase](current-phase.md) records engineering adoption, authorization and release status. This document defines ownership and data flow.

## Primary evidence and the Clean-based path

```text
Official source → Raw → Clean → Chunking → Indexing
                                            ↓
                  Answer ← Evidence Packet ← Reranking ← Retrieval
                               Generation
```

| Layer | Ownership and exit interface |
| --- | --- |
| Raw | Preserve source discovery, memberships, official API responses, run manifests and explicit failure evidence. Stable source keys and locale come from a verified, versioned acquisition contract. |
| Clean | Derive directly from immutable Raw observations. Preserve source wording, meaningful values, order, structure, relationships, evidence-backed source tags and explicit unknowns. A versioned locator maps Clean evidence back to Raw. |
| Chunking | Own scoped admission, micro/macro candidates, natural parents and display/search projections. Keep literal conditions and structural ambiguity visible. Chunk identity and display order do not establish source identity or story chronology. |
| Indexing | Produce a common input and versioned field, analyzer and representation bindings. Lexical, Dense and Sparse representations retain the identity and source locator of each indexed row. |
| Retrieval | Discover query-local candidates from explicit, bounded channel searches. Record raw hits, versioned fusion, ranks and original evidence identities. |
| Reranking | Order complete candidate sets and bind scores to the exact model, runtime and input. Approved structural processing can defer covered candidates while preserving the original candidates and source signals. |
| Generation | Assemble a provider-neutral Evidence Packet from the ordered set. Read source/parent context, prove duplicate handling, and audit each finite-budget admission, truncation or exclusion. Use the assembled Packet for validation, citation and generation. |

**Evidence authority.** Raw is the immutable observation. Clean structures the observation, while chunking and indexes add query-oriented views. Evidence Packets may include excerpts or omit audited candidates. They leave upstream evidence and recoverable source context available. Answer text and navigation outputs remain query derivatives.

**Query ownership.** Generation can explicitly request a new upstream query under its bounded contract. Retrieval continues to own discovery, Reranking continues to own ordering, and Assembly operates on their outputs. The model consumes the assembled Packet; generated claims require support from the cited evidence in that Packet.

The [local chat client](phases/local-chat-v1.md) consumes published turn, source and progress views through a versioned product API. The product adapter owns chat identities, drafts, local credential storage, per-turn grant selection, the durable job queue, conversation admission and feedback. The existing Generation engine owns research decisions, model calls, publication and evidence binding.

## Preserved interfaces

- [Parsed](phases/phase-02-parsed-schema.md) maintains source-specific deterministic structure, Raw references, fingerprints, parser versions and explicit unsupported states. [Canonical](phases/phase-03-canonical-schema.md) keeps stable structural records, exact Parsed lineage, and the difference between actual RawRefs and dependencies introduced in projection.
- [Legacy Phase04](phases/phase-04-retrieval-evidence-assembly.md) derives RU and Packet identities from Canonical lineage. Its retained Embedding/RRF/Reranking/Assembly placement and explicit combined/Formal selection belong to that interface. The newer Assembly ownership applies to the Clean-based path.
- [Phase05 semantic navigation](phases/phase-05-source-bound-semantic-navigation.md) keeps hierarchy and event/relation views separate. Membership and provenance derive from verified RU/Canonical evidence. Model quotes, inferred relations and navigation traces have their own audit scope. They never acquire Canonical authority or enter a Packet as navigation traces.

## Cross-layer contracts

- Use versioned manifests, stable identities, source locators, hashes and create-only outputs. Unknown critical structures, missing records or failed bindings stop the affected operation. Explicit omissions retain the reason.
- A [versioned corpus snapshot](phases/versioned-corpus-incremental.md) binds the current Raw observation and the effective Raw observation used for evidence. It records the source-value equivalence needed to reuse earlier evidence. Computation reuse binds the relevant inputs and execution contract independently of source and release row identities.
- `model_calls` owns attempts, retry decisions, budgets, cooldown and interrupted-call recovery. Task adapters keep raw evidence and validate protocol, usage and content. The caller owns semantic policy and live authorization. Frozen legacy exceptions are recorded in the [model interfaces catalog](model-interfaces.md).
- Parameters such as candidate depth, Top-K, fusion weights, model revisions and context budgets are operating points. They are evaluated with bounded comparisons. Provenance, immutability, explainable disposition and independent source readback govern every operating point.
- Establish a runnable chain, measure failures against source evidence, and scope changes to the diagnosed layer. Engineering integrity, semantic quality and adoption have separate acceptance gates. Evaluators serve specific decisions and stay proportional to the system under test.

See the [workbench](rag-workbench.md) for actual implemented entry points and the [research index](research/README.md) for experiment details.
