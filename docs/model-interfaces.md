# AI Model Interfaces and Provider Configuration

This document is the **single source of truth** for all external AI model providers,
models, endpoints, wire protocols, environment variables, credential loading rules,
and lifecycle states across the GenshinChronicle repository.

Code-level definitions in `src/genshin_corpus/` are authoritative. This document is
automatically checked against the codebase by `tests/retrieval/test_model_interfaces_consistency.py`
and `src/genshin_corpus/rag/model_interfaces_catalog.py`.

---

## 1. Interface Lifecycle Tiers

Interfaces are strictly partitioned into four operational tiers. Never treat historical
or experimental configurations as current production defaults.

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ Tier 1: Phase 04 Production QA Serving Chain (Default / Adopted)                │
│   • Generation: Alibaba Cloud Bailian (qwen3.7-plus-2026-05-26 via Chat Comp)    │
│   • Query Seam: DashScope Text Embedding (qwen3.7-text-embedding 2048d)          │
│   • Rerank: Local BAAI/bge-reranker-v2-m3 (Local weight inference; 0 remote API) │
└──────────────────────────────────────┬───────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼───────────────────────────────────────────┐
│ Tier 2: Phase 05 Navi E2 Autonomous Exploration Routes (Responses API Only)      │
│   • Primary story exploration route: gpt-6-luna / tokenmetro                     │
│   • Authorized candidate routes: glm-5.3-flash, qwen3.8-flash, deepseek-v4.1    │
│   • Dedicated credential slot isolation per route prefix; strict fail-closed     │
└──────────────────────────────────────┬───────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼───────────────────────────────────────────┐
│ Tier 3: Phase 05 Shared Semantic SDK Routes (Retired from Live Dispatch)         │
│   • tokenmetro / jizhi shared profiles                                           │
│   • Live execution prohibited; retained for offline replay and MockTransport     │
└──────────────────────────────────────┬───────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼───────────────────────────────────────────┐
│ Tier 4: Deprecated & Disallowed Routes (Permanently Prohibited)                  │
│   • Old TokenMetro GLM route (503 error; removed from E2_ROUTES)                 │
│   • E2 Chat Completions surface (prohibited; E2 requires Responses API)          │
│   • Cross-provider runtime fallback (prohibited under fail-closed audit rules)   │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Canonical Model Interface Directory

### Opt-in local embedding deployment (outside production serving)

`genshin_corpus.retrieval.local_qwen3_embedding` loads official
`Qwen/Qwen3-Embedding-0.6B`, revision
`97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, entirely from local files.
It has no provider endpoint, credential slot, remote fallback or production
selector. The first contract fixes 1024 dimensions, FP32/L2 output, 2048-token
right truncation, left padding and last-token pooling. Query text uses the
official `Instruct: ...\nQuery:...` template; documents use exact RU text.
CUDA FP16 and CPU FP32 have different identities and require distinct indexes.
The pinned file manifest, repeatability tolerance and measured validation are
owned by [the deployment report](research/phase-05/p05-local-qwen3-embedding-20261025.md).
All production tiers below retain their existing defaults.

### 2.1 Tier 1: Phase 04 Production QA Serving Chain

| Route Name | Logical Model | Model ID / Snapshot | Provider | Protocol Surface | Endpoint / Base URL | API Key Env | Status | Applicable Modules |
|---|---|---|---|---|---|---|---|---|
| `bailian_generation` | `qwen3.7-plus` | `qwen3.7-plus-2026-05-26` | Alibaba Cloud Bailian Model Studio | `chat_completions` (`/compatible-mode/v1/chat/completions`) | `BAILIAN_BASE_URL` (`https://{workspace}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`) | `DASHSCOPE_API_KEY` | **production_adopted** | `genshin_corpus.generation.generation`, `genshin_corpus.ui.runtime` |
| `dashscope_query_embedding` | `qwen3.7-text-embedding` | `qwen3.7-text-embedding` | Alibaba Cloud DashScope | `native_http_post` (`/api/v1/services/embeddings/text-embedding/text-embedding`) | `DASHSCOPE_QWEN_EMBEDDING_ENDPOINT` (`https://{workspace}.{region}.maas.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding`) | `DASHSCOPE_API_KEY` | **production_adopted** | `genshin_corpus.retrieval.qwen_embedding`, `genshin_corpus.retrieval.production_materialization`, `genshin_corpus.ui.runtime` |
| `bge_reranker` | `bge-reranker-v2-m3` | `BAAI/bge-reranker-v2-m3` | Local Model Snapshot | `local_inference` | `GENSHIN_BGE_RERANKER_MODEL_ROOT` (local filesystem path) | None (0 remote API calls) | **production_adopted** | `genshin_corpus.retrieval.bge_reranker_v2_m3`, `genshin_corpus.ui.runtime` |

#### Tier 1 Invariants & Parameters:
- **Generation Policy**: `EXACT_SNAPSHOT_POLICY` requires explicit dated snapshot `qwen3.7-plus-2026-05-26`. Undated aliases (e.g. `qwen-plus`) fail closed before dispatch.
- **Generation Parameters**: `temperature=0.0`, `max_output_tokens=1024`, `enable_thinking=False`.
- **Generation Retry**: Bounded to at most 3 attempts; retry is allowed only for explicit 429 status pairs in `_BAILIAN_RETRYABLE_ERROR_PAIRS` (e.g. `(429, "Throttling")`, `(429, "LimitRequests")`).
- **Embedding Seam**: `role=query`, dimension 2048, FP32, L2 normalized. Requires `DASHSCOPE_QWEN_WORKSPACE` and `DASHSCOPE_QWEN_REGION` (default: `cn-beijing`).
- **Reranking Default**: Local `BAAI/bge-reranker-v2-m3` over the Top-500 candidate window; rank-only fusion `0.35 Hybrid / 0.65 rerank`, denominator 60, final Top 20. Remote Qwen reranker (`qwen_rerank.py`) is a historical challenger and is not part of default serving.

---

### 2.2 Tier 2: Phase 05 Navi E2 Autonomous Exploration Routes

All E2 routes are defined in [`src/genshin_corpus/rag/navi_e2_routes.py`](file:///d:/GenshinChronicle/src/genshin_corpus/rag/navi_e2_routes.py) under `E2_ROUTES`.
E2 requires the **OpenAI Responses API** streaming SSE surface (`api_surface='responses'`, endpoint path `/responses`).

The separately authorized G1 offline research runner reuses the existing
`deepseek-v4.1-flash` / `jizhi` Responses transport for source Cards, B dual-source
propositions/self-checks and bounded targeted checks. It reuses the configured
Qwen query embedding seam for new cached queries. G1 creates no new provider,
credential slot, endpoint, production default or automatic fallback. Its
authorization and bounded request/token safeguards are owned by current-phase
and the G1 research note; R1/R1-QA historical cohorts remain immutable.

| Logical Model (`model`) | Route Key (`route`) | Provider | Protocol Surface | Confirmed Base URL | API Key Env | Base URL Env | Surface Env | Current Lifecycle Status |
|---|---|---|---|---|---|---|---|---|
| `gpt-6-luna` | `tokenmetro` | TokenMetro | `responses` | `https://tokenmetro.com/v1` | `TOKENMETRO_API_KEY` | `TOKENMETRO_BASE_URL` | `TOKENMETRO_API_SURFACE` | **e2_active_exploration** (Primary story exploration route; Run 1–3) |
| `glm-5.3-flash` | `jizhi_glm` | Jizhi | `responses` | `https://jizhiapi.site/v1` | `NAVI_GLM_API_KEY` | `NAVI_GLM_BASE_URL` | `NAVI_GLM_API_SURFACE` | **e2_authorized_candidate** (New 2026-10-15 independent slot; probe passed; awaiting run auth) |
| `qwen3.8-flash` | `tokenmetro_qwen` | TokenMetro | `responses` | `https://tokenmetro.com/v1` | `NAVI_QWEN_API_KEY` | `NAVI_QWEN_BASE_URL` | `NAVI_QWEN_API_SURFACE` | **e2_authorized_candidate** (Independent slot; probe passed; awaiting run auth) |
| `deepseek-v4.1-flash` | `jizhi` | Jizhi | `responses` | `https://jizhiapi.site/v1` | `JIZHI_API_KEY` | `JIZHI_BASE_URL` | `JIZHI_API_SURFACE` | **e2_authorized_candidate** (Independent slot; probe passed; awaiting run auth) |

#### Tier 2 Invariants & Parameters:
- **Credential Slot Isolation**: Each model and route pair binds to a dedicated uppercase prefix (e.g., `TOKENMETRO_*`, `NAVI_GLM_*`, `NAVI_QWEN_*`, `JIZHI_*`). Even when sharing a physical host (e.g. `tokenmetro.com`), credential slots are strictly segregated.
- **Base URL Verification**: `e2_route()` strictly validates the configured Base URL against the confirmed URL. Mismatched or non-HTTPS URLs refuse credential dispatch.
- **Decision Text Parsing**: Uses `_ResponsesDecisionText` to check line/item/content identities, select the unique `item.phase=final_answer` message, isolate `commentary` messages, and fail closed on corrupt, ambiguous, or multi-answer payloads.
- **Fail-Closed Policy**: Any stream error, identity conflict, or schema rejection sets `decision_complete=false` and stops execution immediately. Automatic retry, model switching, or fallback is strictly prohibited.
- **Retry Rule**: Bounded application retry (at most 3 attempts on the exact same route and model) is permitted only for proven pre-generation TokenMetro TPM 429 errors. Uncertain execution is never retried.

---

### R1 isolated discovery comparison (2026-10-25)

The separately authorized 2026-10-25 R1 discovery comparison is an isolated
research consumer of the existing Bailian workspace Chat Completion transport
and Qwen embedding seam. Its uniform generation model is `qwen3.8-max` and
its separate reference/quality reviewer is `qwen3.7-plus-2026-05-26`, with
research-specific output limits and traced persistent connections. It does
not enable the retired shared semantic SDK or alter E2 Responses-only rules
or production Generation settings. Protocols, partial model cohorts and
uncertain-call quarantine are owned by the R1 research note and run artifacts.

R1-QA is a separately authorized candidate-review consumer. After the operator
reported Aliyun quota exhaustion, its partial Qwen cohort stopped. Subsequent
review uses the existing `jizhi` / `deepseek-v4.1-flash` native Responses route
and `JIZHI_API_KEY` / `JIZHI_BASE_URL` credential slots. The opt-in QA runner
reuses SDK stream evidence with retries disabled, verifies exact streamed and
terminal text, and keeps its narrow empty-metadata/JSON-envelope reconciliation
as separate artifacts. It does not relax production/E2 parser policies or
alter Phase04 Generation. UNKNOWN requests remain quarantined across QA cohorts.

### 2.3 Tier 3: Phase 05 Shared Semantic SDK Routes (Retired from Live Dispatch)

Defined in [`src/genshin_corpus/retrieval/semantic_tokenmetro_profile.py`](file:///d:/GenshinChronicle/src/genshin_corpus/retrieval/semantic_tokenmetro_profile.py) and consumed by [`semantic_sdk_runner.py`](file:///d:/GenshinChronicle/src/genshin_corpus/retrieval/semantic_sdk_runner.py).

| Route Key (`route`) | Provider | Model ID Mapping (`TOKENMETRO_MODEL_IDS`) | Base URL | API Key Env | Base URL Env | Current Lifecycle Status |
|---|---|---|---|---|---|---|
| `tokenmetro` | TokenMetro | `deepseek-v4.1-flash`, `gpt-6-luna`, `glm-5.3-flash`, `qwen3.8-flash` | `https://tokenmetro.com/v1` | `TOKENMETRO_API_KEY` | `TOKENMETRO_BASE_URL` | **retired_offline_only** |
| `jizhi` | Jizhi | (Same model alias mappings) | `https://jizhiapi.site/v1` | `JIZHI_API_KEY` | `JIZHI_BASE_URL` | **retired_offline_only** |

#### Tier 3 Status & Invariants:
- **Retired from Live Execution**: `semantic_sdk_runner.py` explicitly enforces that live dispatch is retired. Live invocation without mock transport raises `ValueError`.
- **Offline / Test Use Only**: Preserved for deterministic offline unit tests (using `httpx.MockTransport`), historical replay validation, and regression suites.
- **Updated Decision Contract**: Integrates `_ResponsesDecisionText` to maintain compatibility with multi-message responses while preserving fail-closed rejection without triggering fallback.

---

### 2.4 Tier 4: Deprecated & Disallowed Historical Routes

| Route Identifier | Model / Scope | Former Provider | Former Surface | Deprecation Rationale & Enforcement |
|---|---|---|---|---|
| `('glm-5.3-flash', 'tokenmetro')` | `glm-5.3-flash` | TokenMetro | `responses` | **REMOVED from `E2_ROUTES` on 2026-10-15**. Upstream produced HTTP 503 errors and unresolved UNKNOWN usage. Replaced by `('glm-5.3-flash', 'jizhi_glm')` on Jizhi. Re-enabling or retrying this route is strictly prohibited. |
| `e2_chat_completions` | All E2 Models | TokenMetro / Jizhi | `chat_completions` | **DISALLOWED since 2026-10-15**. E2 exploration migrated permanently to Responses API streaming SSE. `e2_route()` strictly rejects `api_surface != 'responses'`. |
| `cross_provider_fallback` | All Phase 05 | Any | Any | **PROHIBITED**. Automatic route failover (e.g., TokenMetro -> Jizhi upon failure) violates fail-closed audit boundaries. Failures must halt and preserve raw evidence. |

---

## 3. Local Machine Credential Architecture Investigation

This section details how Codex, Antigravity, and developer shells obtain API credentials on the local host. In compliance with repository security rules, only source types, locations, and loading mechanisms are described; secret values are never printed, stored, or migrated.

### 3.1 Credential Source Matrix

| Service / Provider | Credential Variables | Source Type | Storage Location | Loading Mechanism |
|---|---|---|---|---|
| **Alibaba Cloud Bailian / DashScope** | `DASHSCOPE_API_KEY`<br>`BAILIAN_BASE_URL`<br>`DASHSCOPE_QWEN_EMBEDDING_ENDPOINT`<br>`DASHSCOPE_QWEN_WORKSPACE`<br>`DASHSCOPE_QWEN_REGION` | **Windows User Environment Variables** | Registry: `HKCU\Environment` | Injected into all user-spawned shells (`pwsh`, `cmd`, Python). Accessible via `os.environ.get("DASHSCOPE_API_KEY")`. |
| **TokenMetro** | `TOKENMETRO_API_KEY`<br>`TOKENMETRO_BASE_URL` | **Codex Private Environment File** & **CC Switch** | File: `C:\Users\Lyf22\.codex\.env`<br>SQLite: `C:\Users\Lyf22\.cc-switch\cc-switch.db` | Loaded by Codex into task execution processes. Managed graphically on desktop via CC Switch (`.cc-switch/cc-switch.db`). |
| **Jizhi (极智)** | `JIZHI_API_KEY`<br>`JIZHI_BASE_URL` | **Codex Private Environment File** & **CC Switch** | File: `C:\Users\Lyf22\.codex\.env`<br>SQLite: `C:\Users\Lyf22\.cc-switch\cc-switch.db` | Loaded by Codex into task execution processes. Backed up in CC Switch database. |
| **Navi E2 Isolated Slots** | `NAVI_GLM_API_KEY`<br>`NAVI_GLM_BASE_URL`<br>`NAVI_QWEN_API_KEY`<br>`NAVI_QWEN_BASE_URL` | **Interactive Memory Injection (Process Stdin)** | Non-persistent process memory | Injected via non-echoing interactive standard input for single-run execution (per `p05-navi-e2-autonomous-exploration-20261012.md#106,145`). Deliberately omitted from disk files, configs, and Git to prevent credential sprawl. |
| **Antigravity IDE / Agent Runtime** | (Native Google Gemini authentication) | IDE Built-in Configuration | `C:\Users\Lyf22\.gemini\antigravity\` | Uses native Google DeepMind / Gemini agent authentication for assistant chat. PowerShell command sessions inherit Windows User Environment variables (`DASHSCOPE_*`, `BAILIAN_*`), but do not automatically read `~/.codex/.env` unless exported. |

### 3.2 Security and Non-Migration Principles
- **No Forced Centralization**: Do not attempt to merge or migrate Codex's private `.env` or non-persistent interactive slots into Windows system variables or repository files. The existing separation between DashScope (user-level system config), TokenMetro/Jizhi (Codex private config), and isolated Navi slots (interactive memory) preserves intentional security boundaries.
- **Audit Logging Redaction**: The codebase enforces `sensitive()` checks and `_write_once()` filters. Any attempt by a provider to echo configured secret values aborts execution immediately with `ExplorationError('credential echo')`.
- **Zero-Secret Repositories**: All credential values remain outside git-tracked paths. Sample env files are strictly named `.env.example`.

---

## 4. Long-Term Automated Maintenance Mechanism

To prevent documentation rot and eliminate manual update oversights, interface documentation consistency is enforced programmatically.

### 4.1 Programmatic Source of Truth
The module [`src/genshin_corpus/rag/model_interfaces_catalog.py`](file:///d:/GenshinChronicle/src/genshin_corpus/rag/model_interfaces_catalog.py) directly imports:
- `E2_ROUTES` from `navi_e2_routes.py`
- `BailianControlConfig` and `BASELINE_QWEN_MODEL_ID` from `generation.py`
- `DashScopeQwenEmbeddingConfig` from `qwen_embedding.py`
- `ROUTE_PROFILES` and `TOKENMETRO_MODEL_IDS` from `semantic_tokenmetro_profile.py`

Whenever a developer adds, modifies, or removes a model, route, endpoint, or environment variable in code, the catalog immediately reflects the change.

### 4.2 Automated Consistency Test
The unit test [`tests/retrieval/test_model_interfaces_consistency.py`](file:///d:/GenshinChronicle/tests/retrieval/test_model_interfaces_consistency.py) runs as part of the standard test suite (`python -m unittest`). It verifies:
1. Every active model, route key, provider, base URL, and credential environment variable in code exists in `docs/model-interfaces.md`.
2. Deprecated routes are documented under the deprecated section and excluded from active tables.
3. The Responses API protocol requirement and credential isolation rules are documented.
4. The Documentation Router in `docs/current-phase.md` links to `docs/model-interfaces.md`.

### 4.3 CLI Commands for Developers
```powershell
# 1. Run consistency check against documentation
python -m genshin_corpus.rag.model_interfaces_catalog --check

# 2. Print canonical Markdown tables generated directly from code
python -m genshin_corpus.rag.model_interfaces_catalog --markdown

# 3. Run consistency unit test
python -m unittest tests.retrieval.test_model_interfaces_consistency
```
