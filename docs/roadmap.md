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

Status: **active**

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

Status: **tentative / blocked by Phase 01 evidence**

Do not predefine detailed parsers before profiling the actual Raw corpus. Parsed outputs may be source-specific.

## Phase 03 — Canonical Corpus

Status: **tentative / blocked by Phase 02 evidence**

Create stable document/section/passage structures only after source schemas and mapping rules are validated.

## Phase 04 — Retrieval / RAG

Status: **tentative**

Build lexical/vector retrieval over Canonical data and evaluate retrieval recall before adding semantic infrastructure.

## Later semantic layers

Entity resolution, Claims, Events, timelines, knowledge graphs, multilingual alignment, and other AI-semantic structures are not assumed requirements. Add only the smallest structure justified by real research failures after the corpus and RAG exist.
