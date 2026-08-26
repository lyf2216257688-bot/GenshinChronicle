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

Tentative concepts include DomainObject, ContentCollection, Document, DocumentRevision, Section, Passage, and compact choice structures.

Canonical text should preserve official wording. Representation may be normalized; meaning or wording must not be silently corrected.

Current corpus policy: maintain one active Chinese Canonical corpus rather than duplicate Chinese passages for every source. The architecture must still allow future locales.

### 4. Derived Retrieval / RAG
Full-text and vector representations are derived artifacts. Citation/evidence identity should remain tied to stable Canonical passages rather than to embedding chunks.

RAG answers are query results. They must not automatically become Canonical facts.

## Cross-layer invariants

- Source/API organization != game-content organization != research interpretation.
- Raw evidence remains reproducible and auditable.
- Later artifacts must be traceable back to Raw.
- Unknown structures are recorded, not silently dropped.
- Deterministic work belongs in code; semantic interpretation should not be disguised as deterministic parsing.
- Long-term extensibility is preserved without implementing future layers prematurely.

## RAG-first product principle

GenshinChronicle's final product goal is high-quality RAG. Evidence Packets provide a free/manual path and an auditable evidence-output layer, but must not drive lossy upstream choices. Parsed, Canonical, and retrieval design must preserve or improve retrieval quality, recall, ranking, context quality, structural information, provenance, and traceability back to Raw. Evidence Packet convenience is never a reason to discard information needed by future RAG.

## Current implementation boundary

Phase 01 Raw collection is closed after the completed P01-EB corpus and profiling evidence. Detailed Parsed/Canonical/Retrieval schemas remain provisional until designed in Phase 02, governed by the RAG-first principle above.
