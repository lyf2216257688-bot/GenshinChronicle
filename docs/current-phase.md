# Current Phase

`docs/current-phase.md` is the sole current-state authority for active work,
authorization, executable defaults, adoption and the immediate next action.
Phase specifications own contracts; research notes own dated experiment facts.

## 1. Active priority and work unit (2026-11-06)

**Active implementation work unit — Clean V1 (2026-11-06).** The operator
approved the Raw -> Clean technical plan for offline implementation and a bounded
representative pilot. This work owns the direct Raw-to-Clean schema, structure
and tag extraction, source locators, resumable file builds and downstream readers.
The [Clean contract](phases/clean-v1.md) owns the durable interface and acceptance
requirements. Full-corpus materialization, serving adoption, index rebuilds and
legacy asset disposition remain subsequent work units. The existing Phase04
runtime described below is the executable production baseline. Phase05 stays paused.

The Clean V1 foundation and bounded pilot are implemented. 40 new regressions
and 108 existing parser/Canonical/Collector/shared-lock regressions pass. The
30-record pilot covers all 30 observed component types and accounts for 193
components and 6,907 component source values. Independent checks retain 4,439
meaningful HTML text-node occurrences, 49 tables and 5 dialogue groups with
39 nodes and 41 edges. The [implementation record](research/clean-v1-implementation-20261106.md)
owns the pilot paths, measurements, limitations and source evidence.

**Serving baseline: Phase 04 basic RAG V1. PAUSED: Phase 05 NaviRAG / Card / Link / KG.**

The operator has explicitly changed the phase priority. Reuse the existing
Phase 01-03 Raw -> Parsed -> Canonical foundation and Phase 04 RU/RAG modules.
Use DeepSeek as the V1 default final-answer provider. The original RU
full-corpus 1024d embedding build with `Qwen/Qwen3-Embedding-0.6B` is complete.
The operator selected that paired query encoder/index as the default. The 70Q
comparison measures quality and guides later improvements independently of adoption.
The V1 goal remains a stable local question -> official evidence -> answer
-> traceable citation workflow.
The existing backend and Streamlit presentation are the initial delivery path.
No complex UI or public cloud deployment is required.

The operator has now selected **DeepSeek as the V1 Generation default**; unset
`GENSHIN_GENERATION_BACKEND` selects DeepSeek, and `bailian` is explicit rollback.
This is an operator choice, not a regrading of the closed three-case live gate:
Q014/Q003 accepted, Q020 rejected for reported output above the request cap.
That issue remains open and does not negate the completed Embedding build.
No new paid calls are authorized. `generation/deepseek.py` retains shared finite budgets,
raw archives, replay and citation checks. Missing authorization/configuration
fails closed; selecting a default never grants dispatch permission.

**2026-11-03 observed update:** The operator has completed the full local
1024d original-RU build. Independent inspection of the final report, validation,
manifest, source binding and vector artifacts found 535,802 / 535,802 rows,
dimension 1024, 524 committed shards and `validation.status=PASS`.
The full-build task is closed; do not blindly rerun it.
The local 1024d pair is the executable retrieval default. Select
`GENSHIN_EMBEDDING_PROFILE=online_qwen_2048` to use the retained online pair.
The backend, CLI and Streamlit share this configuration and fail closed if
index and query contracts do not match.
V1 is not released. The detailed
[Phase 04 V1 contract](phases/phase-04-retrieval-evidence-assembly.md#basic-rag-v1-delivery-contract)
owns implementation sequence, migration/rollback and release gates.

Phase 05 is preserved, not abandoned or regraded. Do not continue Card repair or
full-corpus generation, Link improvement, KG construction, new semantic model
experiments or online Navi integration as the next work. Resume only when real
V1 use identifies a specific bottleneck and a bounded scope is recorded here.
Previous research plans and spent/closed call allowances do not authorize work
under the new priority.

## 2. Executable baseline and verified capabilities

Source scope remains `mihoyo_obc` / `zh-cn`; locale remains configurable.

| Layer / capability | Verified baseline and V1 relation |
| --- | --- |
| Phase 01 Raw | CLOSED; immutable official discovery/detail evidence and audited collector. Reuse the accepted snapshot; no new crawl required. |
| Phase 02 Parsed | CLOSED; deterministic source-specific parser, Raw lineage and explicit unknowns. Its draft contract remains evolutionary, not permanently frozen. |
| Phase 03 Canonical | CLOSED; accepted 16,437-record structural corpus. Reuse it without semantic rewriting; closure does not establish semantic completeness. |
| Clean V1 | Implemented direct Raw extraction, source locators, selectors, resumable builds and a verified 30-record pilot. Serving adoption belongs to a later work unit. |
| Retrieval Units | 535,802 original RUs with Canonical/Parsed/Raw lineage; 32,955 explicit skips, zero reported failures in the accepted RU manifest. |
| BM25 | Adopted Diagnostic C field-aware scorer, `k1=1.2`, `b=0.75`, fields title/section/speaker/text, weights `2.0/1.25/1.5/1.0`; candidate depth 500. |
| Dense / Hybrid | Default local Qwen3 535,802 x 1024 FP32/L2, read-only mmap. Historical `qwen3.7-text-embedding` 535,802 x 2048 remains explicit rollback with its matched query seam. Deterministic RRF Hybrid is retained. |
| Reranking | Default local `BAAI/bge-reranker-v2-m3` over Hybrid Top500; rank-only fusion `0.35 Hybrid / 0.65 rerank`, denominator 60, final Top20. Online Qwen and off are explicit selectors, with no automatic fallback. |
| Assembly / citation | Adopted retained-evidence combined (proved aliases + conservative HTML display + existing refill); explicit formal rollback. Context budget 12,000 characters / 3,000 per block. Provider-neutral Evidence Packets retain original-source lineage. Citation validation is structural; semantic faithfulness remains `not_evaluated`. |
| Generation | V1 default `deepseek`: TokenMetro Chat `deepseek-v4.1-flash-1017`, neutral messages, 1024 requested cap, disabled thinking, temperature 0, one shared attempt and finite authorized budget. Closed live gate 2 accepted / 1 output-cap rejection; cap issue unresolved. Explicit `bailian` retains `qwen3.7-plus-2026-05-26` control. No new paid call permission. |
| Local Embedding | Pinned `Qwen/Qwen3-Embedding-0.6B` / 1024d full original-RU index is **complete and artifact-validated** (535,802 rows; 524 shards). Default `local_qwen3_1024` uses `data/retrieval/p04-local-qwen3-1024-20261102-v1/metadata/manifest.json` and the paired isolated query encoder. Semantic acceptance is separate. |
| Local question flow | `rag/backend.py` provides reusable prepared state, candidate retrieval, reranking, Packet/Generation output, citation validation and persisted traces. `python -m genshin_corpus.rag` and Streamlit share the runtime selectors. |
| Block B / Phase 05 | Experimental adaptive/semantic gates and inactive Navi derivatives remain separate. Block B production adoption stays blocked; neither it nor Phase 05 is required for V1. |

**Historical asset inspection (2026-11-02):** The prior profile selected RU data under
`data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/ru/`,
field-aware BM25 under `.local/p04-block-a-closure-20260914-r2/field-aware-lexical/`,
and Dense under `.local/p04-qwen-full-batch-beijing-20260909/final/dense/`.
All three manifests and referenced artifact sizes/SHA-256 passed read-only
verification on 2026-11-02; the online Dense manifest matches its pinned
accepted binding. The full Qwen3 installation manifest/files also passed
verification; its pilot index at
`.local/embedding-tests/qwen3-0.6b-20261025-r1/dense/` has 909 rows and passed
artifact checks. The separate Embedding/BGE runtime executables and BGE weights
exist. This is artifact integrity, not a new model-inference or full upstream
corpus re-audit. The 70Q runtime and review-only JSONL files both exist with
70 rows. The initial priority audit's 181-test offline regression run covered Generation/measure,
backend, RU/Assembly/retrieval, local embedding guards, BGE/ranking, UI runtime
and model-interface documentation. After the DeepSeek adapter/selector work,
193 affected offline tests passed in the existing BGE/UI runtime and 20 shared
model-call regressions passed in the existing SDK-capable Python runtime
(213 unique tests total). The 10 new DeepSeek tests forbid socket connections
and cover fixture HTTP, archived replay/UNKNOWN, budgets, validation and
backend Packet/answer persistence. No dependencies or model runtimes were upgraded.

**Migration preparation (historical, 2026-11-02):** 280 distinct offline
regressions passed across Generation, backend/UI, model profiles, RU/Assembly,
Dense/Hybrid, rerankers and the resumable builder. Capped 512/1,024-RU trials,
process-death resume and small-index local Query -> Dense/Hybrid -> BGE passed.
The preflight's "no full inference" statement described the state **before**
the operator's 2026-11-03 full run, not the current state.

**Full build acceptance (observed, 2026-11-03):** The operator ran
`scripts/retrieval/build-local-embedding.ps1 -Mode Run` and the index at
`data/retrieval/p04-local-qwen3-1024-20261102-v1/` completed.
`report.json` reports status `complete`, 535,802 / 535,802 original RUs,
approximately 4h54m active runtime; `validation.json` reports `PASS`
for hashes, source identity/order, 1,024 dimensions, finite values,
normalization and zero long-text truncation. Independent artifact inspection
loaded the `(535802, 1024)` FP32 matrix and matched SHA-256 hashes of
the final vectors, row mapping and source RU manifest. This establishes
**artifact completeness, not retrieval relevance or V1 release quality**.

**Local Dense comparative observation (2026-11-03):** The original Phase04
70 questions were encoded locally (70/70, no truncation) and compared against
frozen old 2048d Qwen Dense Top20 results. Mean Top20 overlap was 8.99/20;
Q003/Q007/Q057/Q060 had zero Top20 overlap. Overlap is not answer correctness
or a fixed Hybrid/BGE/Packet relevance verdict. Details:
`.local/p04-embedding-direct-eval-20261103/dense_70q_comparison.json`.
The accepted old 2048d index, 70Q review data and query vectors remain
valuable comparison/rollback artifacts.

## 3. Immediate next action and remaining V1 gaps

The Clean implementation work unit is complete at its bounded-pilot boundary.
The next reconstruction work is to review the Clean contract and representative
outputs for the chunking branch. Whole-corpus materialization and the coordinated
source/chunk/index/citation adoption require their own work unit. The following
dated material describes retained serving-baseline gaps and evidence.

**Latest work (2026-11-02; paid gate CLOSED):** user authorization was exactly
3 requests / 150,000 conservative tokens / 1024 requested output cap / zero
retries. Q014 and Q003 returned accepted answers with source-based Codex review
PASS for those cases; Q020 reported 5,384 output tokens despite actual wire cap
1024 and was rejected as `OUTPUT_LIMIT_EXCEEDED`. Three HTTP200, exact returned
model `deepseek-v4.1-flash-1017`, zero retries/unknown usage. Provider reports:
23,157 input + 6,884 output = 30,041 tokens; actual RMB UNKNOWN. The full
three-case gate did not pass. No human/product acceptance was established.
The later operator default choice above does not change those historical facts.
Frozen input/47-RU verification, actual-wire/raw archives, shared ledger,
control preservation and secret-scan audits PASS. Details and claim limits:
[three-case live report](research/phase-04/p04-deepseek-live3-20261102.md).

**Adopted serving work unit (2026-11-04):** the operator explicitly adopted the existing
`phase04-retained-evidence-1` **combined** Assembly as the backend/CLI/Streamlit
default. `GENSHIN_ASSEMBLY_BACKEND=formal` selects the original Formal
Deferred-Footprint-Charge as explicit rollback. The shared runtime rejects
unknown selectors before model construction. Direct backend callers can set
`SingleQuestionBackendConfig(assembly_backend="formal")`.

The adopted implementation reuses the completed 140-control/420-challenger
Assembly replay over frozen 70Q inputs. Original RU, indexes and citation lineage
remain the evidence owners. Existing whole-root refill and 12,000/3,000 character
budgets apply. Q020 local key evidence is restored by combined; old-profile
Q037's direct-dialogue gap and speculative refill content remain known limits.
The operator's adoption does not regrade the pending full-Packet human review
or establish DeepSeek answer quality. This work authorizes regression tests,
related documentation and a scoped local Git commit. Full 70Q retrieval and paid
API dispatch are outside this work unit. See the
[Assembly experiment and adoption](research/phase-04/p04-assembly-retention-70q-20261104.md).

1. **70Q retrieval baseline recorded:** the
   [70Q comparison](research/phase-04/p04-embedding-70q-20261103.md) completed
   70/70 per profile with the same BM25, RRF, BGE and Evidence Assembly settings.
   Codex reviewed the Packets and recorded 53 equivalent, 1 local better, 3
   old better, 9 both insufficient and 4 pending. Human acceptance remains open.
   Use the retained traces to diagnose the identified recall and Assembly
   failures and repeat the same comparison for any subsequent tuning.
2. **V1 acceptance remains open:** assess final answer accuracy and citation
   support independently from retrieval and deterministic checks. Measure
   cold/warm latency, RAM/VRAM, restart and rollback behavior, and usability
   in the existing backend/Streamlit interface. DeepSeek output-cap reporting
   remains unresolved; any new paid live probe needs separate authorization.
3. **Authorized storage cleanup (2026-11-04):** the Card repair-v3 and Navi R1-QA
   Phase05 roots were archived separately to F: and removed from D: only after
   complete path, byte-count and per-file SHA-256 verification. Preserve both
   indexes, all 70Q materials and comparison results. The old F: Navi scattered
   copy remains because it contains six files absent from the D: source snapshot
   and verified archive; do not remove it until those files are reconciled. See
   [the storage cleanup closeout](research/phase-04/p04-storage-cleanup-closure-20261104.md).

The DeepSeek output-cap/reporting problem remains a separate open Generation
issue. Do not raise caps, retry paid calls or block offline Embedding preparation
on it. Further live probes need a new finite authorization; full query-path
answer quality and latency remain unmeasured by the fixed-Packet paid run.

No component rewrite, new large Benchmark framework, global semantic layer or
new upstream corpus is required by this plan. DeepSeek, 1024d, model revisions,
Top-K, fusion and budgets are stage operating choices. Immutable evidence,
lineage, auditability and provider-neutral boundaries remain invariants.

**Serving authorization record (2026-11-04):** the authorized three paid DeepSeek requests are complete
and their allowance is consumed/closed. No further dispatch is authorized by
that gate. This work permits local code/tests, the operator-requested paired
retrieval default switch, offline 70Q evaluation and the explicitly selected
retained-Packet Assembly ablations. The operator has now adopted combined and authorized a scoped local commit. No online
Embedding/Qwen/Generation calls, full-corpus rebuild, merge or push.
Preserve immutable attempts and UNKNOWN evidence;
new/materially changed API execution must use the shared call layer. Historical
Phase05 permits remain unrelated to Phase04 execution.

**Historical Git inspection baseline (2026-11-02):** `main@7b6454d` was clean before that work. Other
local worktrees exist and were not modified, merged or removed by this work.
No branch consolidation or remote-state claim follows from this inspection.

## 4. Documentation Router

| Topic / responsibility | Primary owner |
| --- | --- |
| Working rules and evidence/layer invariants | [`AGENTS.md`](../AGENTS.md) |
| Architecture and boundaries | [`Architecture overview`](architecture-overview.md) |
| Phase priorities and milestones | [`Roadmap`](roadmap.md) |
| Directory/package responsibilities | [`Project structure`](project-structure.md) |
| Data roots and artifact responsibilities | [`Data README`](../data/README.md) |
| Durable decisions / ADR policy | [`Decisions`](decisions/README.md) |
| Raw acquisition contract | [`Phase 01`](phases/phase-01-raw-collection.md) |
| Direct Raw-to-Clean schema, builds and read interfaces | [`Clean V1`](phases/clean-v1.md) |
| Clean implementation and bounded pilot evidence | [`Implementation record`](research/clean-v1-implementation-20261106.md) |
| Source-specific parsing contract | [`Phase 02`](phases/phase-02-parsed-schema.md) |
| Canonical evidence contract | [`Phase 03`](phases/phase-03-canonical-schema.md) |
| Basic RAG V1 contract, migration and acceptance | [`Phase 04`](phases/phase-04-retrieval-evidence-assembly.md#basic-rag-v1-delivery-contract) |
| Original RAG plan (historical, not a restart checklist) | [`First end-to-end amendment`](phases/phase-04-first-end-to-end-rag-amendment-v1.md) |
| Phase 04 completed checkpoints and evidence | [`Checkpoint register`](research/phase-04/p04-rag-historical-checkpoint-register.md), [`historical archive`](research/phase-04/p04-rag-historical-checkpoint-archive.md) |
| Limited DeepSeek final-QA live evidence | [Three-case report](research/phase-04/p04-deepseek-live3-20261102.md) |
| AI protocols, configuration and shared-call migration coverage | [`Model interfaces`](model-interfaces.md) |
| Pinned local Qwen3 deployment and 909-RU pilot limits | [`Deployment report`](research/phase-05/p05-local-qwen3-embedding-20261025.md) (reused engineering asset, historically filed in Phase 05) |
| Local original-RU full-build program, operator commands, recovery and smoke measurements | [`Migration preparation`](research/phase-04/p04-local-qwen3-migration-20261102.md) |
| Offline paired retrieval quality and review procedure | [`70Q Evidence Packet comparison`](research/phase-04/p04-embedding-70q-20261103.md) |
| Retained-Packet Assembly offline ablations and review | [`Assembly experiment`](research/phase-04/p04-assembly-retention-70q-20261104.md) |
| Paused semantic-navigation contracts and research pointers | [`Phase 05`](phases/phase-05-source-bound-semantic-navigation.md) |

### Preserved Phase 05 checkpoints (lookup only)

Historical next-work recommendations are deferred by this priority change.
Original research reports, raw runs, call ledgers and judgments are unchanged
except explicit supersession annotations where needed.

The former detailed Phase 05 status narration and router are also preserved
in Git at `7b6454d:docs/current-phase.md`; this current record replaces their
priority/next-action wording, not the underlying historical evidence.

Key historical owners:

- [Fixed-66 closure](research/phase-05/p05-card-live66-repair-20261101.md):
  54/66 complete sources; 12 incomplete; all inactive/not reviewed/not validated.
  No automatic completion or retry is implied.
- [Latest Card offline change](research/phase-05/p05-card-semantic-improvement-20261102.md)
  and [closed live comparison](research/phase-05/p05-card-semantic-live6-20261102.md):
  three requests sent; first-batch semantic gate failed and second batch unsent.
  This priority decision does not alter those results or the 54 existing Cards.
- [Offline Card/Link environment](research/phase-05/p05-navi-offline-environment-20261029.md),
  [Card readiness](research/phase-05/p05-card-production-readiness-20261029.md),
  [shared call integration](research/phase-05/p05-model-call-unification-20261030.md):
  preserved code and bounded research, not V1 dependencies or production adoption.
- [R1](research/phase-05/p05-navi-r1-discovery-20261025.md),
  [R1-QA](research/phase-05/p05-navi-r1-qa-20261025.md),
  [G1](research/phase-05/p05-navi-g1-offline-20261025.md),
  [F2](research/phase-05/p05-navi-f2-48-execution-20261028.md),
  [v0.3 limited acceptance](research/phase-05/p05-navi-v03-limited-live-20261028.md)
  and [E2](research/phase-05/p05-navi-e2-autonomous-exploration-20261012.md):
  original scoped outcomes/limits remain historical; no resumption authorized.
