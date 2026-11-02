# AI Model Interfaces and Provider Configuration

This document is the **single source of truth** for all external AI model providers,
models, endpoints, wire protocols, environment variables, credential loading rules,
and lifecycle states across the GenshinChronicle repository.

Code-level definitions in `src/genshin_corpus/` are authoritative. This document is
automatically checked against the codebase by `tests/retrieval/test_model_interfaces_consistency.py`
and `src/genshin_corpus/rag/model_interfaces_catalog.py`.

---

## Shared model-call execution (2026-10-30)

New API work defaults to `genshin_corpus.model_calls`. It schedules retry-free
SDK attempts, reuses raw-first transport and create-only evidence IO, and does
not alter prompts or content acceptance. A call root contains `call.json`, the
unchanged adapter `plan.json`, immutable `attempts/`, `dispatches/`, `decisions/`,
selected-artifact projections and a sealed result. Route fingerprint ledgers
are retained. A recovery root references the exact old plan, original bytes and
original ledger reservation; it never salts a fingerprint or removes a claim.

`Policy` defaults: maximum 3 attempts; only proved TCP/TLS pre-send failures
retry automatically; uncertain retries 0; JSON-generation retries 0; exponential
backoff from 1s capped at 30s; channel circuit after 3 consecutive channel
failures, cooldown 60s. Numeric/date `Retry-After` is honored within the cap.
These are operating points, not claims of provider capacity.

HTTP429/5xx/524, disconnection and uncertain interrupted calls may retry within
explicit `unknown_retries`, `cost_risk_authorization`, attempt and batch limits.
No generic assumption says 429/5xx proves no generation. Metro server-side
idempotency is **NOT_VERIFIED**; identical bodies/fingerprints do not prove
server deduplication. The generic seam can carry a stable `Idempotency-Key`
with a route-bound verified provider contract; neither current Metro adapter
enables it. Otherwise another attempt may incur another charge and consumes
risk allowance. Completed non-JSON can retry only with finite authorized
`format_retries`. Source/Schema failures and truncation are isolated by the shared transport layer, never
silently repaired/resampled. The new Card-only domain policy below may replace an eligible completed task with an explicitly recorded structural reorganization. Existing deterministic Card conversions remain
strictly source-bound. Successful retries never erase earlier UNKNOWN.

`Batch` reserves each attempt under an OS process lock. Unknown usage consumes
its conservative token allowance instead of zero. Independent tasks continue
while reservations fit. Reported usage and actual/unknown cost are independent
of protocol/content PASS. Allowances are operating reservations, not tokenizer
guarantees, provider enforcement or invoices; overruns can defer later sends.
Sealed attempts are reused on resume; partial attempts remain UNKNOWN. Channel
and budget deferrals are resumable. Execution stays serial; no database, queue
service or new dependency. Caller still owns live authorization and task
validation. SDK/transport retries stay disabled.

| Task | Integration |
|---|---|
| Metro Card Chat (`metro_chat.execute`) | Shared by default; same prompt/wire/source validation |
| Typed Responses Card / Link generation / review / attribution (`requests.execute`) | Shared by default; frozen Link semantics unchanged |
| New offline Navi, including rich text | Shared typed path by default; native runner explicit compatibility only |
| Card-only builds / new DeepSeek blind preparations | Shared budgets and independent-task continuation |
| Antigravity blind CLI | Shared outer budget/recovery/archive/circuit; internal HTTP attempts/retries/dedup/fees UNKNOWN |
| Completed original blind roots / scoped permits | Historical immutable identities; no implicit migration/additional authorization |
| Frozen native v0.3 / F2 / older semantic SDK / R1 and related research | Legacy, not migrated |
| Navi E2 `E2SDK` and historical research retry modes | Legacy, not migrated |
| Adopted Phase04 Generation / remote embedding / older SDK and urllib consumers | Legacy, production unchanged |

Card and new blind CLIs accept `--call-policy <json>`. It never substitutes for
`--execute-authorized`, exact preparation acknowledgment or campaign budgets.
Live Card builds require finite prepared `total_request_limit`. New preparations
snapshot both shared modules along with task code. Code drift needs a new
preparation or explicit origin-bound recovery, not hash substitution. Completed
historical blind campaigns and their exhausted authorization remain closed.
CLI invocation count is not API request count; unknown internal attempts are
reported separately from known API request observations. Its input-plus-output
allowance cannot guarantee the size of native Agent overhead.

Read-only diagnosis (PowerShell 7, no model calls):

```powershell
$env:PYTHONPATH = 'src'
python -m genshin_corpus.model_calls inspect --request data/retrieval/p05-card-blind-prep-20261030-r1/runs/deepseek/T01/request
```

Explicit programmatic recovery uses `metro_chat.execute(new_root, view, evidence,
strategy=original_strategy, legacy_origin=original_request_root, call_policy=policy,
call_batch=batch, live_authorized=True, api_key=...)`. Input reconstructs the
exact original plan; credentials stay in existing environment. Partial originals
remain partial and hashed, never resealed. Use a new output root and retain
UNKNOWN. This documentation does not authorize executing that recovery.

[Investigation, T01 offline verification and limits](research/phase-05/p05-model-call-unification-20261030.md).


### Card-only Metro Responses consumer (2026-10-29; historical checkpoint)

The subsequent [parameter study](research/phase-05/p05-metro-parameter-study-20261029.md)
keeps SDK serialization, vendor documentation, Metro public endpoint declarations
and actual Metro parameter enforcement separate. `metro_parameter_drafts.py`
exports offline-only DeepSeek none/low/nonstream/schema and Qwen Chat controls;
it has no dispatch entrypoint and changes no default model or production wire.
Neither native API documentation nor successful Mock serialization authorizes
an unsupported/untested Metro live operating point.
The subsequently authorized [D1 trial](research/phase-05/p05-metro-none-live-20261029.md)
did send reasoning.effort=none, but still returned visible reasoning and failed
ID/output-cap checks; it did not qualify this parameter as a reliable thinking
disable switch on the current route. Conditional non-stream D2 was not sent.

At the initial Metro checkpoint, `retrieval/navi_offline/card_build.py` live Card preparations defaulted to
`tokenmetro / deepseek-v4.1-flash`, configured `TOKENMETRO_API_KEY` and
`TOKENMETRO_BASE_URL`, `https://tokenmetro.com/v1/responses`, JSON-object mode,
8192 output cap. The authenticated model catalog lists the exact model ID and
Responses support. Route/surface/URL/wire identities and the dedicated
`data/retrieval/p05-card-metro-request-ledger/` preserve cross-run duplicate
protection separately from historical jizhi requests. No route fallback or retry.
This is a new explicit Card consumer, not reactivation of retired shared SDK or
a change to E2/v0.3/Phase04 policies. The first Metro response passed local Card
content checks but failed protocol ID/output-cap checks; further sends stopped.
Independent provider counters, stream and content outcomes are retained. Scope
and actual evidence: [Metro report](research/phase-05/p05-card-metro-switch-20261029.md).

Current Card CLI behavior is the structured-generation strategy documented below;
this historical Responses checkpoint does not override it.

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

**Scoped Navi v0.3-rc1 capability audit (2026-10-28, offline):** the installed
OpenAI SDK 3.19.2 forwards Responses `text.format` for both `json_object` and
`json_schema/strict=true` through offline MockTransport. Official
[Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs)
distinguishes JSON syntax mode from Schema adherence; it does not certify a
third-party proxy/model. The [historical Jizhi dual-route probe](research/phase-05/p05-navi-f2-dual-route-20261028.md)
accepted strict Schema parameters, but does not prove enforcement. Final F2
used JSON-object mode and retained JSON/Schema failures. Strict enforcement for
`jizhi/deepseek-v4.1-flash` remains **UNKNOWN**. The frozen candidate construction module prepares unsent
drafts only; both explicit formats require the same full local Schema/context
validation, with no automatic fallback, retry or live capability probe. See the
[candidate contract](research/phase-05/p05-navi-b-prompts-v03.md).

The separately prepared `retrieval/navi_v03_runner.py` now provides opt-in
single-request execution for that unchanged candidate, using explicit frozen
plan acknowledgment, the existing jizhi Responses route, SDK/transport retries
disabled and at most three independently proven pre-send attempts. Default
operation remains offline. A separately authorized three-case `json_object`
trial completed with HTTP 200, completed terminal events and full local content
replay for all three requests; this confirms only those bounded requests, not
general route reliability or semantic quality. Third-party enforcement of
`json_schema/strict=true` remains UNKNOWN, and no standing future-call
authorization follows from that trial. See the
[execution-readiness audit](research/phase-05/p05-navi-v03-run-readiness-20261028.md)
and [limited live acceptance report](research/phase-05/p05-navi-v03-limited-live-20261028.md).

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
- **E2 Retry Rule**: Bounded E2 application retry (at most 3 attempts on the exact same route and model) is permitted only for proven pre-generation TokenMetro TPM 429 errors. Uncertain execution is never retried. The separate v0.3 recorder uses only the scoped, archived pre-send non-transmission policy described above; it does not change E2 policy.

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

### Metro Card opt-in Chat check (2026-10-29; historical checkpoint)

An isolated `navi_offline/metro_chat.py` adapter archives raw nonstream Chat before SDK parsing,
uses the existing Metro persistent ledger with endpoint-bound identity. Its raw transport never retries; shared scheduling now owns bounded attempts. At this historical checkpoint it never retried.
It does not change Card defaults. One authorized DeepSeek/Chat/disabled-thinking request
showed no visible reasoning but failed token arithmetic and Card Schema; batch continuation
was blocked at that checkpoint. Superseded by the bounded Card strategy below. See [actual evidence](research/phase-05/p05-metro-chat-disabled-live-20261029.md).

### Card structured-generation strategy (2026-10-29; historical checkpoint)

At that checkpoint Card CLI preparation defaulted to `deepseek-card-structured-1`: Metro
`https://tokenmetro.com/v1/chat/completions`, `deepseek-v4.1-flash`, stream=false,
max_tokens=12288, json_object, thinking.type=disabled via top-level extra-body forwarding.
System Prompt carries the complete Card Schema and typed examples; deterministic aliases
reduce wire IDs without changing provenance. The existing TOKENMETRO credential slot is reused.
`qwen-card-structured-1` remains an explicit experimental candidate (Qwen3.8 Flash,
json_schema/enable_thinking=false); its live probe HTTP524 is not a success or free-call proof.
At that checkpoint Python prepare retained legacy defaults; explicit legacy-responses
selects the old Card execution path. E2 and frozen Link protocol rules are unaffected.

Only this new Card adapter permits recorded token arithmetic warnings and the observed
DeepSeek v4.1/v4-1 returned spelling alias; it still rejects foreign models, truncation,
missing counters, over-cap output and source/content violations. Native upstream identity
and actual fees remain UNKNOWN. See [real batch, recovery and semantic limitations](research/phase-05/p05-card-reliable-generation-20261029.md).

### 2026-10-31 Phase05 Card A/C qualification

**Historical qualification checkpoint; now superseded by the versioned runtime integration below.** Machine-readable profile: `config/phase05-card-api-profile-20261031.json`. Original sealed A/C outputs: `data/retrieval/p05-card-api-ac-20261031-r1/`.

- Selected NEW Card profile: TokenMetro `deepseek-v4.1-flash`, Chat Completions, nonstream, `response_format={"type":"json_object"}`, `thinking.type=disabled`, 32,000 output cap, full local Schema and source/quotation checks. Native upstream identity unverified.
- A: Responses `text.format=json_schema`, T01, 32k, thinking none: HTTP 400, `This response_format type is unavailable now`; provider usage UNKNOWN. Do not select Responses JSON Schema at current route.
- C: Chat Low requested, T05, 32k: HTTP 200; mechanical/source PASS; 7,279 input + 16,008 output = 23,287 tokens, versus disabled 19,831. Response had neither reasoning_content nor reasoning-token breakdown, so effective Low is unverified. No clear semantic gain; remain disabled by default.
- Shared model_calls remains required for retry, recovery, budgets, persistent raw records, channel fault control, UNKNOWN accounting. Unsupported HTTP 400 is not retried. Sent/unknown requests require bounded cost-risk authorization for resends.
- Long-document B/truncation strategy was deferred at qualification; the new versioned structural implementation below supersedes that pending work. Cards remain INACTIVE/NOT_REVIEWED.
- **Historical migration note:** qualification changed the JSON decision only. Runtime now implements the separate 32k strategy below; the 12,288 legacy strategy and sealed archives are retained.

Official references: https://api-docs.deepseek.com/api/create-chat-completion/ , https://api-docs.deepseek.com/api/create-response/ , https://api-docs.deepseek.com/guides/thinking_mode/ .

### 2026-10-31 final Chat strict Function Calling A/C extension

Isolated paid T01 probe: Chat Completions + forced `submit_navi_card` function (`strict:true`), thinking disabled, nonstream, 32k. On the **TokenMetro `/v1/chat/completions` route**, HTTP 200 with exactly one function call, `finish_reason=tool_calls`, and **full local source/Schema/quote PASS**. Tokens: input 2,956, output 13,839, total 16,795, fee UNKNOWN. Disabled-thinking JSON Object T01 reference total: 11,291; both passed. Note DeepSeek official strict tool calling requires a Beta endpoint, while this was through Metro; this successful output does not independently prove provider-side strict-enforcement guarantees.

**No change to chosen default:** Chat Completions, JSON Object, thinking disabled, new-run 32k profile. Strict Function Calling is a *compatible alternate* only: around 49% higher observed total tokens on T01, no clear semantic gain, and a tool-call adapter would be required. Keep independent local Schema and exact-source checks. See `docs/research/phase-05/p05-chat-strict-function-20261031.md` and original raw artifacts at `data/retrieval/p05-card-strict-tools-20261031-r1/`. The separate versioned 32k implementation below preserves legacy strategies and archives.

### New Card-only 32k runtime (2026-10-31)

Both Python Card-only preparation entrypoints and the Card CLI default to
`deepseek-card-evidence-32k-20261031`: TokenMetro DeepSeek v4.1 Flash,
Chat Completions, stream=false, max_tokens=32000, JSON Object, thinking.type=disabled.
All read and synthesis calls use this cap. Full local Schema, source aliases,
program-extracted verbatim quotations, attribution/modality instructions and semantic review boundaries remain.
Read timeout is 900s, connect 15s; SDK/HTTP retries remain zero. Prompt hash and
versioned reading/reorganization policies are bound to preparation.

8,000 body characters and 100,000 serialized request characters are input safety
limits, not semantic split rules. `--context-token-limit` optionally binds an actual
operator-confirmed context limit; a conservative UTF8 byte proxy plus 32,000 output
reservation checks it. This is not a native tokenizer/context attestation.

Network retries use shared Policy/Batch unchanged. New Cards require format_retries=0.
One completed, usage-accounted JSON truncation or obvious final-answer planning leak
may create one structural replacement decision, with multiple new reading/synthesis
calls charged to the same campaign. No recursive content reorganization, UNKNOWN
resend, Schema/source retry, model fallback or output-cap escalation is implicit.

Required children must all succeed before document synthesis or completion. Formal
semantic Cards and temporary reading/reduction nodes have separate roles; temporary
nodes stay archived and are excluded from the default semantic index. Frozen runs
resume in their hash-verified prepared interpreter snapshot, never by rewriting old
wire/ledger/code identities. Full-corpus paid generation remains separately authorized.
The previous `deepseek-card-structured-32k-20261031` strategy retains its copied-quote
Schema and remains explicitly selectable; its archived jobs resume with their original code.

The new `card-evidence-selection-1` wire contract supplies lossless sentence/paragraph
segment IDs inside delivered evidence. Reading outputs choose evidence/start/end segment IDs;
synthesis outputs choose verified inherited anchor IDs. Models never output quote text or
character offsets. Programs extract continuous ranges into the existing Card v2 anchors;
disjoint evidence stays as multiple anchors. Position maps and both read/synthesis Schema
hashes are bound to preparation and raw-first jobs. Unknown IDs, invalid ranges and source
violations are isolated without fuzzy search, rebinding or legacy quote-text repair.
Reference validation is mechanical; semantic quality remains NOT_VALIDATED.

Card index export independently selects completed, validated sources and seals a
`card-selection.json` report of included/skipped sources. New completions require a new
index identity/directory, with existing exact-window reuse; zero eligible Cards causes no
encoder load. Historical Cards and indexes are not rewritten.
See [current implementation and offline evidence](research/phase-05/p05-card-evidence-selection-20261031.md)
and [structural integration/census checkpoint](research/phase-05/p05-card-integration-32k-20261031.md).

### Fixed-66 bounded Card repair (2026-11-01)

The initial stage of the now-closed fixed66 campaign explicitly selected `deepseek-card-bounded-evidence-20261101`,
TokenMetro Chat Completions, exact model `deepseek-v4.1-flash-1017`, JSON Object,
stream=false, thinking.type=disabled and max_tokens=32000. Legacy preparation defaults
and frozen strategy identities are retained; this is a scoped new wire contract.
Provider-reported completion_tokens exceeded32000 on archived requests; local
validation keeps that request failed. Output shape and staged reduction control
new generation length; they do not certify server enforcement or native tokenization.
All semantic source bindings derive from exact selected anchors. Completed HTTP200
content failures with known usage can receive one bounded structural replacement;
UNKNOWN/uncertain billing is isolated and not blindly resent. Windows of8 sends and
disjoint source workers still use shared Policy/Batch and raw-first adapters.
Actual charges and native upstream attestation remain UNKNOWN. See the
[repair report](research/phase-05/p05-card-live66-repair-20261101.md).

The same fixed66 repair later introduced explicit
`deepseek-card-single-segment-20261101` (contract4), preserving the exact1017 model,
Chat/nonstream/disabled-thinking/JSON Object/max_tokens32000 wire settings. Reading
selects atomic segment IDs instead of paired endpoints; inherited synthesis anchor
choices and strict rejection remain. This bounds representable reference geometry,
not provider token accounting or semantic truth. A single specifically authorized
replacement of a post-send UNKNOWN scope succeeded; this is recorded authority for
that one scope, not a general UNKNOWN retry policy. The operator then stopped new
dispatches after repeated channel failures; this closed work unit has no standing
authorization to resume remaining sources. Actual fees remain UNKNOWN.
