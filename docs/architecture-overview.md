# Architecture Overview

## Purpose

Build a reliable official-text foundation first. More ambitious semantic research features are optional future layers, not Phase 01 requirements.

## Long-term pipeline

```text
SOURCE / RAW
    -> PARSED
    -> CANONICAL
    -> DERIVED RETRIEVAL
    -> RAG query-time answers
```

### 1. Source / Raw
Answers: **What did we obtain, from which official source, and when?**

Likely concepts later include source system, source item, source version, raw payload, crawl run, and crawl observation. These are architectural concepts, not a request to implement a database now.

Raw is evidence and reprocessing input. It is not the normal application query layer.

### 2. Parsed
Answers: **What deterministic structure can a source-specific adapter recover from Raw?**

Parsed schemas may remain source-specific. Unification is not required here.

Rules:
- deterministic extraction only;
- unknown important structures must be surfaced;
- parsing rules must be based on observed source schemas;
- do not pretend natural-language semantic judgments are deterministic parsing.

### 3. Canonical
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

Current corpus policy: maintain one active Chinese Canonical corpus rather than duplicate Chinese passages for every source. The architecture must still allow future locales.

### 4. Derived Retrieval / RAG
Full-text and vector representations are derived artifacts. A later Retrieval
contract must derive citation/evidence identity from Canonical lineage rather
than from embedding chunk identity. Retrieval passage/chunk design remains
deferred.

RAG answers are query results. They must not automatically become Canonical facts.

## Cross-layer invariants

- Source/API organization != game-content organization != research interpretation.
- Raw evidence remains reproducible and auditable.
- Later artifacts must be traceable back to Raw.
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

## RAG-first product principle

GenshinChronicle's final product goal is high-quality RAG. Evidence Packets provide a free/manual path and an auditable evidence-output layer, but must not drive lossy upstream choices. Parsed, Canonical, and retrieval design must preserve or improve retrieval quality, recall, ranking, context quality, structural information, provenance, and traceability back to Raw. Evidence Packet convenience is never a reason to discard information needed by future RAG.

## Current implementation boundary

Phase 01 Raw collection, Phase 02 Parsed foundation, and Phase 03 Canonical
Schema are closed after their accepted corpus gates. The Phase 02 contract in
`docs/phases/phase-02-parsed-schema.md` remains a draft, evolutionary
source-specific contract. Phase 03 completed its approved Canonical contract,
structural OBC projection, deterministic storage, and the separately reviewed
16,437-record OBC `zh-cn` production gate. It establishes structural,
traceable Canonical evidence, not semantic completeness, final dialogue
semantics, cross-snapshot semantic identity/reuse, or Retrieval/RAG quality.

Retrieval / Evidence Assembly architecture and design is the next stage.
Retrieval schema design, technology selection, and implementation remain
deferred until separately approved under the RAG-first principle above.
