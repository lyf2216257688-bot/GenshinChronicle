# Current Phase

`docs/current-phase.md` is the sole current-state documentation authority for the
active phase, current authorization, production/default behavior, and immediate next
action. Detailed historical narratives and experiment checkpoints are owned by their
respective research notes or historical registers (see [Documentation Router](#4-documentation-router));
this document maintains the concise present state, operational baselines, and boundaries only.

- **Current Checkpoint**: main@f685a1c (2026-10-19), Byakuyakoku continuous understanding source mapping remediation and live run.
- **Working Tree Baseline**: Operator authorized local commit for Phase 05 Byakuyakoku interactive navigation sample, Method A/B experience comparison, and architectural synthesis; no push authorized.

---

## 1. Active Phase & Work Unit

- **Active Phase**: **Phase 05 — Source-Bound Semantic Navigation** (Specification: [`docs/phases/phase-05-source-bound-semantic-navigation.md`](phases/phase-05-source-bound-semantic-navigation.md)).
- **Active Work Unit**: **Phase 05 Byakuyakoku Interactive Navigation Prototype & Architectural Synthesis — COMPLETE** (2026-10-20, operator-authorized offline work unit):
  - Reconciled 16 hardcoded component-to-RU mappings in [`byakuyakoku_continuity_experiment.py`](../src/genshin_corpus/retrieval/byakuyakoku_continuity_experiment.py): corrected 12 historical conflict hashes to authoritative production RU IDs matching `byakuyakoku_samples.py` and the 535,802-unit production manifest; updated unit test assertions in [`test_byakuyakoku_continuity_experiment.py`](../tests/retrieval/test_byakuyakoku_continuity_experiment.py) (all 22 repository unit tests PASS offline).
  - Built a standalone, self-contained interactive navigation prototype ([`byakuyakoku_navi_interactive.html`](research/phase-05/byakuyakoku_navi_interactive.html)) verifying 5-theme switching, cross-document evidence linking, Method A/B stance contrast, and fail-closed `section_only` source degradation.
  - Documented authentic sample sheets for Method A ([p05-byakuyakoku-navi-sample-method-a.md](research/phase-05/p05-byakuyakoku-navi-sample-method-a.md)) and Method B ([p05-byakuyakoku-navi-sample-method-b.md](research/phase-05/p05-byakuyakoku-navi-sample-method-b.md)), accompanied by a structured evaluation report ([p05-byakuyakoku-navi-comparison-report-20261019.md](research/phase-05/p05-byakuyakoku-navi-comparison-report-20261019.md)).
  - Produced in-depth architectural synthesis ([p05-byakuyakoku-navi-architectural-synthesis-20261019.md](research/phase-05/p05-byakuyakoku-navi-architectural-synthesis-20261019.md)) analyzing OBC data characteristics, Autonomous Exploration A vs Continuous Understanding B mechanisms, scalability limits, and proposed a two-tier (macro-backbone + micro-probe) complementary navigation architecture.
  - Consumed zero paid API calls; preserved all historical run records intact.
- **Immediate Next Action**: Operator review of the interactive prototype and architectural synthesis, deciding on Phase 05 design adoption or next navigation milestone; no live exploration requests or paid API calls are authorized without explicit operator approval. No push authorized.

### Autonomous Story Exploration Checkpoint (Run 3: `gpt-6-luna` 2026-10-16)

- Immutable run root: `data/retrieval/p05-navi-e2-gpt6luna-20261016-r1` (run identity: `7bca0ea7d50355024c85da3456dd46920c182fe5f2d311897c5171713814d689`).
- Detailed findings and evaluation in the [GPT-6-Luna E2 Story Exploration Report (Run 3)](research/phase-05/p05-navi-e2-gpt6luna-story-exploration-r3-20261016.md).
- Model & route: `gpt-6-luna` / `tokenmetro` via Responses API (`https://tokenmetro.com/v1/responses`).
- Starting anchor verified: `3429:s7:u3` (RU `d9fed651...`, Ema dialogue in 《龙蛇藏归辑录》).
- Autonomous trajectory: Model executed 1 complete decision round (Seed Read + 3 searches: 《白夜国水文考》, 蛇神到来/迁居历史, 海渊生活/民间故事); hit 14 candidate RUs across 6 pages; strictly qualified Ema's statements as NPC dialogue insufficient to prove history.
- Round 01 planned reading: 4 target RUs (`ffdaf...` 水文考卷三, `b87a...` 常世国龙蛇传小说, `54d64...` 珊瑚宫重要事件, `bff4...` 白夜国重要事件).
- Historical termination: Round 01 returned completed commentary and final_answer messages; local cross-message concatenation polluted the decision input, and strict JSON validation rejected it (`decision_complete=false`). The system stopped fail-closed with `ExplorationError` (`status=partial`), without retry/fallback. Later read-only checks confirmed each message matches its terminal text and final_answer is valid JSON; historical artifacts/status remain frozen.
- Cumulative resource usage: 2 issued calls (both RESOLVED, 0 UNKNOWN), 0 retries, 0 fallbacks; **9,692 known total tokens** (8,503 input, 1,189 output, 56 reasoning); 3,089 Canonical body characters; 29,313 tool response characters; actual cost/currency UNKNOWN.
- Historical run audit checkpoint: **70/70 checks PASS** (`offline-audit-summary.json`), **920/920 repository unit tests PASS**, zero credential leaks. Repair validation is recorded in the active work unit above. E2 remains **PARTIAL**, E3 **NOT READY / NOT AUTHORIZED**.

### Autonomous Story Exploration Checkpoint (Run 2: `gpt-6-luna` 2026-10-15)

- Immutable run root: `data/retrieval/p05-navi-e2-gpt6luna-20261015-r2` (run identity: `7bca0ea7d50355024c85da3456dd46920c182fe5f2d311897c5171713814d689`).
- Detailed findings and evaluation in the [GPT-6-Luna E2 Story Exploration Report (Run 2)](research/phase-05/p05-navi-e2-gpt6luna-story-exploration-r2-20261015.md).
- Model & route: `gpt-6-luna` / `tokenmetro` via Responses API (`https://tokenmetro.com/v1/responses`).
- Starting anchor verified: `3429:s7:u3` (RU `d9fed651...`, Ema dialogue in 《龙蛇藏归辑录》).
- Fix verification: Model 100% adopted formal `unit_id` references for all 6 autonomous reads, completely eliminating the r1 coordinate ambiguity.
- Autonomous trajectory: Model executed 4 complete decision rounds; autonomously read 7 units across 5 independent pages:
  - `3429` (龙蛇藏归辑录 - 剧情对话, RU `d9fed...`)
  - `3470` (白夜国馆藏 - 《白夜国地理水文考》第三卷 RU `ffdaf...` 及图书描述 RU `834db...`)
  - `508883` (白夜国 - 势力/神明 RU `ed35...` 及重要事件/改造文化 RU `6be0...`)
  - `3918` (乌帽子 - NPC对话 RU `4e18...`)
  - `3205` (露子【巫女】 - NPC对话 RU `3ea8...`)
- Rigorous source qualification: Model differentiated NPC testimony (Ema, Eboshi, Tsuyuko), in-game geographical treatise (《白夜国地理水文考》), editorial wiki compilation (`508883`), and noted the thematic conflict between Sangonomiya's policy of institutional forgetting vs. Eboshi's preservation of truth; strictly maintained `unverified_hypothesis_not_fact` memory status.
- Termination: Round 04 dispatch encountered `APIConnectionError` (`EXECUTION UNKNOWN`, `complete=false`); system safely stopped fail-closed without retry or fallback.
- Cumulative resource usage: 5 issued calls (4 RESOLVED, 1 UNKNOWN), 0 retries, 0 fallbacks; **24,886 known total tokens** (22,682 input, 2,204 output, 94 reasoning) + 1 UNKNOWN; 17,656 Canonical body characters; 72,364 tool response characters; actual cost/currency UNKNOWN.
- Offline audit: **143/143 checks PASS** (`offline-audit-summary.json`), **915/915 repository unit tests PASS**, zero credential leaks. E2 remains PARTIAL.

### Autonomous Story Exploration Checkpoint (Run 1: `gpt-6-luna` 2026-10-15)

- Immutable run root: `data/retrieval/p05-navi-e2-gpt6luna-20261015-r1` (run identity: `02144d3952c666f3c4f15e89edb393ccd53e6888c4819a9e3c71b189dcb17555`).
- Detailed findings in [GPT-6-Luna E2 Story Exploration Report (Run 1)](research/phase-05/p05-navi-e2-gpt6luna-story-exploration-20261015.md).
- Status: PARTIAL / STOPPED (stopped fail-closed at round 2 action execution due to hit coordinate ambiguity; 14,882 tokens, 107/107 audit PASS). Frozen.

### Four-model protocol checkpoint (2026-10-15 follow-up CLOSED)

- Original frozen root: `data/retrieval/p05-navi-e2-responses-four-model-20261015-r1` (initial 2 PASS / 2 BLOCKED). New root: `data/retrieval/p05-navi-e2-responses-followup-20261015-r2`; detailed results in the [E2 follow-up report](research/phase-05/p05-navi-e2-autonomous-exploration-20261012.md#2026-10-15-jizhi-glm-and-deepseek-follow-up).
- `gpt-6-luna` / `tokenmetro`: authentication / Responses / E2 four-action gates PASS (r1); returned model `gpt-6-luna`; cumulative 1 request, 770 known tokens.
- `qwen3.8-flash` / `tokenmetro_qwen`: all three gates PASS (r1); returned model `qwen3.8-flash`; independent runtime credential; cumulative 1 request, 1,297 known tokens.
- `glm-5.3-flash` / **`jizhi_glm`**: all three gates PASS (new r2 independent credential, `NAVI_GLM_API_KEY` / `NAVI_GLM_BASE_URL`, `https://jizhiapi.site/v1/responses`); returned model `glm-5.3-flash`; new 1 request, 807 tokens; cumulative 2 requests (historical TokenMetro 503 usage remains UNKNOWN). Historical TokenMetro GLM selection is no longer executable for new E2 runs.
- `deepseek-v4.1-flash` / `jizhi`: all three gates PASS (new r2 operator-authorized diagnostic); returned model `deepseek-v4.1-flash`; new 1 request, 854 tokens; cumulative 2 requests (historical HTTP 524 usage remains UNKNOWN).
- Both new streams ended `response.completed` / `status=completed`; visible decision output accepted, no provider reasoning text/token breakdown supplied (NOT_PROVIDED / UNKNOWN). No formal corpus tools executed.
- Follow-up authorization allowed only 1 new request per selected model / 2 total; both are consumed. Combined: **6/12 issued** (GPT/Qwen 1/3 each; GLM/DeepSeek 2/3 each), 0 retries/fallbacks/Chat Completions/tool executions; **3,728 known tokens + 2 historical UNKNOWN requests**; actual cost/currency UNKNOWN.
- Offline follow-up: **292 tests PASS**, **222 integrity checks PASS**; original campaign's 72 inventoried files and historical E2 r1-r4 110 files unchanged. Four-model protocol first stage is COMPLETE; synthetic action checks do not change formal E2 PARTIAL or establish research/semantic PASS. No commit/push.

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
- **Provider Calls**: Authorized ONLY when explicitly granted in the active work unit. For new E2 execution, explicit model AND matching route are required; no default or fallback. SDK retries are disabled. Application retry is bounded to at most 3 attempts on the same route/model, only for the exact proven pre-generation TokenMetro TPM 429 rejection; uncertain execution is never retried. Historical experiment policies remain frozen.
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
| **Phase 05 Navi E2 (Run 1-2)** | [`docs/research/phase-05/p05-navi-e2-autonomous-exploration-20261012.md`](research/phase-05/p05-navi-e2-autonomous-exploration-20261012.md) | Historical r1/r2 exploration plus 2026-10-15 four-model Responses protocol follow-up |
| **Phase 05 Navi E2 (GPT-6-Luna Run 1)** | [`docs/research/phase-05/p05-navi-e2-gpt6luna-story-exploration-20261015.md`](research/phase-05/p05-navi-e2-gpt6luna-story-exploration-20261015.md) | Single-case autonomous story exploration report using `gpt-6-luna` (Run 1, coordinate stop) |
| **Phase 05 Navi E2 (GPT-6-Luna Run 2)** | [`docs/research/phase-05/p05-navi-e2-gpt6luna-story-exploration-r2-20261015.md`](research/phase-05/p05-navi-e2-gpt6luna-story-exploration-r2-20261015.md) | Single-case autonomous story exploration report using `gpt-6-luna` (Run 2, unit_id fix, 7 reads / 5 pages, network stop) |
| **Phase 05 Navi E2 (GPT-6-Luna Run 3)** | [`docs/research/phase-05/p05-navi-e2-gpt6luna-story-exploration-r3-20261016.md`](research/phase-05/p05-navi-e2-gpt6luna-story-exploration-r3-20261016.md) | Single-case autonomous story exploration report using `gpt-6-luna` (Run 3, historical aggregate-parse stop and subsequent offline multi-message repair) |
| **Phase 05 Quality Review (6+6)** | [`docs/research/phase-05/p05-w2-semantic-quality-review-v01-20261008.md`](research/phase-05/p05-w2-semantic-quality-review-v01-20261008.md) | 12-sample atomic quality review evidence packet and index |
| **Phase 05 Unique Archive** | [`docs/research/phase-05/p05-unique-historical-archive-20261012.md`](research/phase-05/p05-unique-historical-archive-20261012.md) | 200-unit Responses live/continuation run and cheap canary archive |
| **AI Model Interfaces** | [`docs/model-interfaces.md`](model-interfaces.md) | Central AI provider catalog, request protocols, credential discovery, and maintenance checks |
| **Phase 05 Navi Sample A** | [`docs/research/phase-05/p05-byakuyakoku-navi-sample-method-a.md`](research/phase-05/p05-byakuyakoku-navi-sample-method-a.md) | Method A (E2 autonomous search) authentic Byakuyakoku narrative sample sheet |
| **Phase 05 Navi Sample B** | [`docs/research/phase-05/p05-byakuyakoku-navi-sample-method-b.md`](research/phase-05/p05-byakuyakoku-navi-sample-method-b.md) | Method B (7-step continuous lore understanding) authentic Byakuyakoku sample sheet |
| **Phase 05 Navi Comparison** | [`docs/research/phase-05/p05-byakuyakoku-navi-comparison-report-20261019.md`](research/phase-05/p05-byakuyakoku-navi-comparison-report-20261019.md) | Structured evaluation report comparing Method A and Method B on narrative, evidence, and reader experience |
| **Phase 05 Navi Architecture** | [`docs/research/phase-05/p05-byakuyakoku-navi-architectural-synthesis-20261019.md`](research/phase-05/p05-byakuyakoku-navi-architectural-synthesis-20261019.md) | Synthesis analyzing OBC data structure, exploration vs continuity, scalability limits, and two-tier architecture |
| **Phase 05 Navi Prototype** | [`docs/research/phase-05/byakuyakoku_navi_interactive.html`](research/phase-05/byakuyakoku_navi_interactive.html) | Standalone interactive prototype HTML widget for Byakuyakoku navigation exploration |
