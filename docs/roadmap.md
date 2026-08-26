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

Status: **planning only**

Design stable document/section/passage structures from accepted Phase 02
evidence before authorizing any Canonical implementation.

## Phase 04 — Retrieval / RAG

Status: **tentative**

Build lexical/vector retrieval over Canonical data and evaluate retrieval recall before adding semantic infrastructure.

## Later semantic layers

Entity resolution, Claims, Events, timelines, knowledge graphs, multilingual alignment, and other AI-semantic structures are not assumed requirements. Add only the smallest structure justified by real research failures after the corpus and RAG exist.
