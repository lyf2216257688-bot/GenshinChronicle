# Current Phase

`docs/current-phase.md` is the sole current-state documentation authority for the
active phase, current authorization, production/default behavior, and immediate next
action. Detailed historical narratives and experiment checkpoints are owned by their
respective research notes or historical registers (see [Documentation Router](#4-documentation-router));
this document maintains the concise present state, operational baselines, and boundaries only.

- **Current Checkpoint**: `main@e529bbb` (2026-10-12, `docs: establish project memory router v1`).
- **Working Tree Baseline**: Checkpoint `e529bbb` plus known user `.gitignore` delta (`/%SystemDrive%/`, `/codex-temp-locked/`, `/p01ea-test-temp/`, `/tmp*/`). Fresh Git status check required before any state-changing operations.

---

## 1. Active Phase & Work Unit

- **Active Phase**: **Phase 05 — Source-Bound Semantic Navigation** (Specification: [`docs/phases/phase-05-source-bound-semantic-navigation.md`](phases/phase-05-source-bound-semantic-navigation.md)).
- **Active Work Unit**: **Documentation Audit & Hygiene Remediation** (Resolving governance friction, reconciling phase documentation, and maintaining test/corpus integrity; Project Memory Router v1 closed at `e529bbb`).
- **Immediate Next Action**: Complete and verify documentation audit remediations (P0–P1 items, test suite 100% pass, and git hygiene).

### Paused Product Workstream (P05-Navi-E2)
- **Scope**: Single-case DeepSeek-driven source exploration from `3429:s7:u3`.
- **Status**: **PAUSED / PARTIAL / CHECKPOINTED** at `b5ffbb6`.
  - Prior E1: PASS / CLOSED at `main@4ff3887` (tools and source bindings reused).
  - Original E2 r1/r2: PARTIAL / STOPPED (pre-dispatch token projection stop; 2 calls, 13,048 tokens).
  - Minimal r2 continuation r3/r4: PARTIAL / EXECUTION UNKNOWN / STOPPED (3 calls added; cumulative 5 issued calls, 4 terminals, 1 unresolved call without terminal recorded strictly as `EXECUTION UNKNOWN` and **must NOT be reissued**; 19,273 known tokens + 1 UNKNOWN; 6 searches, 3 reads, 3 pages, 9,354 delivered Canonical characters).
  - Clues = 0; memory remains `unverified_hypothesis_not_fact`. No active Navi view, KG edge, or semantic acceptance.
  - Final provider-free affected tests passed 96/96 (E2 23 + E1 tools 25 + SDK 48).
  - Evidence: [`docs/research/phase-05/p05-navi-e2-continuation-20261012.md`](research/phase-05/p05-navi-e2-continuation-20261012.md).
  - Subsequent Phase E3: **NOT READY / NOT AUTHORIZED**.
  - No further provider/network calls authorized for E2 in this paused state.

### Parallel / Secondary Gates (Non-blocking)
- **Historical-200 6+6 Quality Review**: Prepared and frozen at `data/retrieval/p05-w2-atomic-quality-evidence-20261008-r2/index.json` (report: [`docs/research/phase-05/p05-w2-semantic-quality-review-v01-20261008.md`](research/phase-05/p05-w2-semantic-quality-review-v01-20261008.md)). Awaiting independent offline review.
- **Cheap-Channel Rate Control Canary**: Paused on provider capacity (report: [`docs/research/phase-05/p05-unique-historical-archive-20261012.md`](research/phase-05/p05-unique-historical-archive-20261012.md)).

---

## 2. Production Baseline & Invariants

The production corpus scope is `zh-cn` MiHoYo OBC. Upstream foundation layers are closed:
- **Phase 01 Raw Collection**: CLOSED / FROZEN.
- **Phase 02 Parsed Schema**: CLOSED / FROZEN.
- **Phase 03 Canonical Corpus**: CLOSED / FROZEN (16,437 Canonical records).
- **Phase 04 Block A Retrieval / Ranking**: **CLOSED / FROZEN / ADOPTED**:
  - **Retrieval Units (RU)**: 535,802 rows materialized from Canonical corpus.
  - **Lexical Retrieval**: Diagnostic C BM25 (`k1=1.2`, `b=0.75`, fields `record_title/section_name/speaker/retrieval_visible_text`, weights `2.0/1.25/1.5/1.0`, candidate depth 500).
  - **Dense Retrieval**: Qwen `qwen3.7-text-embedding` artifact (2048 dims, FP32/L2, read-only mmap).
  - **Reranking**: Local `BAAI/bge-reranker-v2-m3` default over 500-candidate Hybrid window; rank-only fusion `0.35 Hybrid / 0.65 rerank`, denominator 60, final Top 20. Fail-closed on missing local model snapshot or execution failure.
  - **Evidence Assembly**: Formal Deferred-Footprint-Charge default (`B0=12,000` context characters, `3,000` per block).
  - **Generation**: Bailian/Qwen `qwen3.7-plus-2026-05-26` snapshot (`enable_thinking=false`).
- **Phase 04 Block B**: Structural Actual Review Pass; production adoption BLOCKED; further recovery live run NOT AUTHORIZED.

---

## 3. Strict Authorization Boundary

- **Query Seam**: Arbitrary Dense/Hybrid queries use Qwen synchronous query seam (`role=query`, dimension 2048, dense-only). Fail-closed on missing credentials or transport errors.
- **Provider Calls**: Authorized ONLY when explicitly granted in the active work unit. No automatic retry or route fallback.
- **Audit Lineage**: Every provider request and raw response must be preserved immutably.
- **Corpus Integrity**: No silent rewriting, normalization, or pruning of Raw or Canonical evidence.
- **Scope Discipline**: No premature adoption of Vector DB, ANN, KG, AI Semantic Extraction, or UI expansion.

---

## 4. Documentation Router

One fact has one primary documentation owner. Consult the primary owners below rather than duplicating facts:

| Topic / Responsibility | Primary Owner | Description |
| :--- | :--- | :--- |
| **Working Rules & Invariants** | [`AGENTS.md`](../AGENTS.md) | Repository engineering rules, layer boundaries, and documentation maintenance discipline |
| **Architecture & Pipeline** | [`docs/architecture-overview.md`](architecture-overview.md) | High-level data pipeline (`Raw -> Parsed -> Canonical -> Retrieval/RAG -> Navi`) |
| **Phase Roadmap** | [`docs/roadmap.md`](roadmap.md) | Long-term phase directions and status |
| **Directory Responsibilities** | [`docs/project-structure.md`](project-structure.md) | Package and directory layout and ownership |
| **Data Roots & Artifacts** | [`data/README.md`](../data/README.md) | Rules and contracts for `data/raw`, `parsed`, `canonical`, and `retrieval` |
| **Architecture Decisions** | [`docs/decisions/README.md`](decisions/README.md) | ADR policy and durable engineering decisions |
| **Phase 01 Raw Collection** | [`docs/phases/phase-01-raw-collection.md`](phases/phase-01-raw-collection.md) | Discovery, crawl validation, and raw archive contract |
| **Phase 02 Parsed Schema** | [`docs/phases/phase-02-parsed-schema.md`](phases/phase-02-parsed-schema.md) | Source-specific parsed data representation and adapter contracts |
| **Phase 03 Canonical Corpus** | [`docs/phases/phase-03-canonical-schema.md`](phases/phase-03-canonical-schema.md) | Stable canonical document schema and 16,437-record production gate |
| **Phase 04 Retrieval / RAG** | [`docs/phases/phase-04-retrieval-evidence-assembly.md`](phases/phase-04-retrieval-evidence-assembly.md) | Phase 04 durable specification and Evidence Packet contract |
| **Phase 04 Checkpoint Index** | [`docs/research/phase-04/p04-rag-historical-checkpoint-register.md`](research/phase-04/p04-rag-historical-checkpoint-register.md) | Compact Phase 04 checkpoint register and evidence pointers |
| **Phase 04 Historical Archive** | [`docs/research/phase-04/p04-rag-historical-checkpoint-archive.md`](research/phase-04/p04-rag-historical-checkpoint-archive.md) | Historical narrative and Block B recovery lineage archive |
| **Phase 05 Navigation Spec** | [`docs/phases/phase-05-source-bound-semantic-navigation.md`](phases/phase-05-source-bound-semantic-navigation.md) | Phase 05 durable boundary, RU candidates, and semantic navigation contract |
| **Phase 05 Navi E1 (Tools)** | [`docs/research/phase-05/p05-navi-e1-tools-wiring-20261011.md`](research/phase-05/p05-navi-e1-tools-wiring-20261011.md) | Mechanical tool bindings, real-source fixture, and smoke test results |
| **Phase 05 Navi E2 (Run 1-2)** | [`docs/research/phase-05/p05-navi-e2-autonomous-exploration-20261012.md`](research/phase-05/p05-navi-e2-autonomous-exploration-20261012.md) | Initial autonomous exploration r1/r2 execution report |
| **Phase 05 Navi E2 (Continuation)** | [`docs/research/phase-05/p05-navi-e2-continuation-20261012.md`](research/phase-05/p05-navi-e2-continuation-20261012.md) | r3/r4 continuation report and terminal accounting |
| **Phase 05 Quality Review (6+6)** | [`docs/research/phase-05/p05-w2-semantic-quality-review-v01-20261008.md`](research/phase-05/p05-w2-semantic-quality-review-v01-20261008.md) | 12-sample atomic quality review evidence packet and index |
| **Phase 05 Unique Archive** | [`docs/research/phase-05/p05-unique-historical-archive-20261012.md`](research/phase-05/p05-unique-historical-archive-20261012.md) | 200-unit Responses live/continuation run and cheap canary archive |
