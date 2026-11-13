# Current Phase

`docs/current-phase.md` is the sole current-state authority for active work,
authorization, executable defaults, adoption and the immediate next action.
Phase specifications own contracts; research notes own dated experiment facts.

## 0. Current work — S0 Reranking; Python 3.14 migration complete (2026-11-13)

**Latest operator decision:** pause Sparse use and fusion tuning; use S0
(BM25F + BGE-M3 Dense) to proceed to Clean-based Reranking. Preserve Sparse,
S1/S2 and P3/P4 evidence. This supersedes the adoption-pending checkpoint below;
it does not adopt an online Serving path or establish end-to-end quality.

**Authorized implementation:** the operator approved the revised
[Reranking/Python migration contract](phases/clean-reranking-v1.md): preserve
existing indexes and upstream data; prepare independent Python 3.14.5 runtimes,
an explicit old-index compatibility gate, fixed saved S0 candidates, BGE/GTE
relevance comparison, then structural processing and local score-gap-protected
MMR after model selection. Generation continues to own Evidence Assembly.
The [historical preparation report](research/clean-reranking-preparation-20261113.md)
records the initial software/fixture checkpoint. The model-free fixed input
export is complete: 70 questions / 6,963 candidates. New ordering, recovery,
validation and migration software is opt-in; model adoption remains undecided.
Both isolated Python 3.14.5 environments are installed; dependency imports,
GTE custom-class imports and finite fixture checks pass. The operator's real
BGE/GTE Preflight, four-group FP16 CUDA Smoke and independent Validate pass.
**Python migration and cleanup are complete:** matched r3 witnesses reproduce the
old index and agree across3.12/3.14 (Dense bitwise equal, Sparse exact values,
token IDs/rankings/kernel checks PASS). Active configs use3.14, the old-base-disabled
actual S0 query passes, four old virtual environments and the old shared base are
removed. By subsequent explicit authorization, global3.14 registration is repaired
and global3.12 registration retired with backups. E1 lock/code/models are retained.
The [migration report](research/python314-migration-20261113.md) owns exact versions,
finite evidence and cleanup; no Reranker quality/model selection is established.

**Execution boundary and immediate next action:** the operator now authorizes
Codex to start and inspect finite local checks expected within three to five
minutes. Longer full70Q remains operator-started, never polled by Codex. The next
step is the saved-S0 BGE/GTE full70Q Run, independent Validate and relevance report,
followed by operator model selection and only then structural/MMR ablation.
Existing Reranker Smoke remains valid; do not rerun retired3.12 commands or relax
tolerances. Implementation and validation performed no paid call, index rebuild
or Serving switch. The operator subsequently authorized committing and pushing
the reviewed code, configuration, tests and documentation; local runtime and
generated evidence remain excluded. Historical worktree source pins remain frozen records.

### Historical P4 completion checkpoint (before the S0 decision)

**Adopted: BAAI/bge-m3 as the sole Clean/Chunk V2.1 Dense model.**
The 20-question blind-review comparison found BGE-M3 and Jina v5 closely matched;
the operator explicitly selected BGE-M3 for its quality, permissive MIT license,
and compatibility with the planned Sparse-increment experiment. No further
Dense model comparison is required before continuing the pipeline.

The canonical selection record is config/index-selected-v1.json.
The selected Dense/lexical indexing artifacts are the completed BGE-M3 1024-dimensional Dense
index and Whoosh3 BM25F lexical index, sharing the validated 119,557 unique
Chunk inputs, 104,879 micro memberships and 78,658 macro memberships.

By explicit operator authorization, the unused Qwen3-0.6B and Jina v5 local
model weights and their new Dense candidate index directories were deleted.
The old Phase04 local Qwen3 Dense index was also deleted. The old online
Qwen 2048-dimensional Dense index was absent locally before this cleanup;
any independent external archives were not touched.
Raw, Clean, Chunk, source lineage, input export, BM25F, BGE-M3 model/index,
BGE reranker, original 70-question raw retrieval and blind-review records
were retained. Small retired Phase05 trial vector/mock index artifacts were also
removed; the surrounding research reports and source data remain. The exact deletion manifest is
.local/index-cleanup-20261110/cleanup-audit.json (local-only).

**Serving status:** The existing Phase04 RAG runtime remains configured to
load the removed Qwen3 index/encoder; it is intentionally no longer a working
local fallback. Selecting BGE-M3 does not mean a new Clean-based online RAG
runtime was deployed. An opt-in candidate Retrieval runtime is now implemented;
integration with Reranking and Generation remains future work. The four-arm benchmark complete.json remains historical and its
old Run/Resume/Validate are not suitable after candidate removal.

**Completed prior authorization (2026-11-11): P0–P2 Clean-based Retrieval.**
The operator approved the revised [Retrieval V1 contract](phases/clean-retrieval-v1.md):
an opt-in S0 runtime over the existing selected indexes, replaceable F1/F0 fusion,
conservative structural candidate handling, and at most 128 real-source Sparse
trial rows with resource checks. **P0–P2 engineering validation is complete:**
the real selected-index S0 query matches original independent search; a reusable
two-question session passed; the 32-row Sparse trial passed official-kernel,
padding, exact-dot and recovery checks. The environment matrix covers 215 passing
test IDs. Exact observations and limits belong to the
[implementation report](research/clean-retrieval-p0p2-20261111.md).
These are finite engineering results, not relevance or end-to-end acceptance.
That work did not execute a full Sparse build, 70Q experiment, paid
API, Serving switch, Git commit or push. Sparse component reuse is
preferred; final S0/S1/S2 adoption remains the operator's decision. Later quality
evaluation uses automatic 70Q comparisons and representative source-text cases,
not mandatory blind review, complete relevance labeling or nDCG.

**Completed P3 foundation (2026-11-12).**
The operator ran full 119,557-row Sparse encoding in an independent PowerShell.
The [Sparse build contract](phases/sparse-build-v1.md) owns streaming SciPy shards,
shared micro/macro membership, resource protection, recovery and independent
Preflight/Run/Resume/Validate/Status entry points. The
[preparation report and operator commands](research/sparse-build-preparation-20261112.md)
own finite results and readiness. Preparation is complete: 32 real source vectors
match P0–P2 exactly, official kernel/streaming queries/recovery/source readback
pass, and the final source/model/code bindings are reverified. The environment
matrix covers 104 passing test IDs, including 17 new streaming/job fault tests.
The completed `data/retrieval/bge-m3-sparse-v1-20261112-ready/` manifest binds
468 shards, 10,015,176 nonzero weights and zero empty rows. Its independent
validation report is PASS and matches the full manifest hash
`c6897d8b1fc7289fcd26bf0c5d98cb35ab7f5ad8abfb31b0a0b8da8a599f937b`.
This is mechanical/source validation, not relevance or adoption.

**Completed preparation authorization (2026-11-12): P4 preparation and short tests.**
The operator requests S0/S1/S2 automatic comparison using the existing70 questions,
plus representative original-text cases and necessary F0/F1 controls. The
[P4 contract](phases/recall-experiment-v1.md) owns the experiment interface,
recovery, validation and reports. After the actual preflight stopped at about
1.1GiB available RAM before any inference, the operator requested running the
real preflight and one-question Smoke manually after closing the desktop app.
Codex completes fixture tests and preparation; no real model Smoke or70Q was run
in this work unit. The operator starts both Smoke and the complete70Q in an independent
PowerShell. Codex must not launch, keep alive, poll or monitor the manual run.
Final S0/S1/S2 adoption remains the operator's decision. No paid API, default
Serving switch, index rebuild, P5, or automatic Git commit/push is authorized.
Existing Raw/Clean/Chunk/Dense/BM25F/Sparse remain read-only. The prior local
commit authorization applied only to the completed P0–P3 work unit.
P4 fixture validation covers54 distinct passing tests across the isolated
runtimes; the actual entry points and explicit real-Smoke limitation are in the
[P4 preparation runbook](research/recall-experiment-preparation-20261112.md).

**Completed operator experiment and authorized offline review (2026-11-13).**
The operator completed the 70Q run at
`data/retrieval/recall-p4-70q-v1-20261112/`. Its independent Validate report is
PASS for scope=70q, questions=70, bound to complete SHA256
`16b73821c78bab9a53da3473232d6c6ae17b1c0da8d4179c129f5a51e4c5c815`.
This establishes mechanical/source binding and replay, not semantic acceptance.
The operator then authorized a lightweight question-by-question review of the
saved Top10/Top20 and F0/F1 controls, with no retrieval rerun, paid API,
index/Serving change, complex labeling, blind review or automatic commit/push.
The [quality review](research/recall-p4-70q-quality-review-20261113.md) records
S1 versus S0 as 7 improved / 36 comparable / 6 degraded / 21 uncertain, and
S2 as 3 / 45 / 1 / 21 under F1_max plus existing structural cleanup.
These are bounded non-blind reviewer judgments, not a gold relevance set or
global quality rates. Most confirmed gains concern ranking of existing evidence;
Sparse has not been shown to replace BM25F safely. If adding Sparse, the review
prefers S2 with F1_max; S0 remains the baseline/lower-cost choice. This is a
recommendation only: no S0/S1/S2 or fusion adoption, selector/default switch,
Reranking integration or Generation integration was performed. Final adoption
remained the operator's decision at this historical checkpoint; the later S0
choice is recorded at the start of this section.

---

## 1. Prior completed work unit (2026-11-09)

**Completed development work unit — standalone full-index experiment launcher.** The operator
authorized implementing Preflight / Run / Resume / Validate over formal Chunk
V2.1, three prepared local Dense models and Whoosh3 BM25F. Execution in this
work unit is limited to offline regressions and finite model/index trials.
The [launcher contract](phases/index-benchmark-v1.md) owns serial runtime
isolation, no-truncation audits, recoverable independent indexes, exact Dense
search, lexical operating points and standalone PowerShell commands. The
[implementation report](research/index-benchmark-launcher-20261109.md) owns
measured finite validation. Full-corpus encoding will be started separately by
the operator with the delivered launcher. Paid calls remain outside this work
unit. The operator authorized a local Git commit on 2026-11-10.
The finite environment matrix covered 181 passing test cases. All four launcher
modes passed over seven formal-source Chunk inputs, with four vector shards per
model and separate 6-member micro / 5-member macro coverage. Actual Resume
preserved 47 committed artifact hashes. These are bounded development results.

**Accepted foundation — new RAG indexing, work unit 1, unified input and field design.**
The operator authorized design, minimal opt-in engineering and offline validation
against completed Chunk V2.1. The [input contract](phases/indexing-input-v1.md)
owns shared Chunk identity, six lexical fields, unchanged retrieval_text Dense
input, micro/macro route membership, source readback and independent representation
bindings. Implementation and finite source validation are recorded in the
[work-unit report](research/indexing-input-v1-20261109.md).
The complete source survey passed. Two 16-record exports produced identical
718-row shared inputs with 650 micro and 548 macro memberships. Readback passed
for 15 populated Clean records, 474 natural parents and six original graphs.
145 offline regressions passed, including 17 new indexing tests. The launcher
now records explicit experimental analyzer/weight choices and three pinned
Dense candidate configurations. Retrieval-quality comparison and final model,
dictionary and field-weight choices remain follow-up work. Full-index execution
is outside this development turn, and serving migration requires its own decision.

**Completed input baseline.** Clean V1 has 16,437 records in
`data/clean/mihoyo_obc/zh-cn/clean-v1-full-20261106/`.
Chunk V2.1 full construction and engineering acceptance are complete in
`data/retrieval/clean-chunking-full-v2-1-20261108/`, with 119,557 unique balanced
candidates, 214,870 natural parents and 5,087 original dialogue groups under
`gc-story-admission-pilot-4`. Of those candidates, 63,980 belong to both routes,
40,899 only to micro and 14,678 only to macro. Logical route totals are 104,879
micro and 78,658 macro. 460 selected source records have no candidates. The
[V2.1 report](research/clean-chunking-v2-1-20261108.md) retains the finite repair
checkpoint and subsequent full-build/archive facts. Complete flat exports are
the indexing input. Shard restoration is unnecessary for reading these exports.

Balanced and parallel micro/macro remain the selected direction. Source literal
conditions, unknown provenance, exact occurrence aliases, CleanRef and full
natural-parent readback retain the [Chunk V2 contract](phases/chunking-v2.md).
The [Clean contract](phases/clean-v1.md) owns deterministic extraction and readers.
Whole-corpus independent Raw semantic/structural fidelity and retrieval/answer
quality retain their separate acceptance scopes.

Earlier V1/V2/recovery/pilot artifacts and claims are historical checkpoints,
routed through their dated research reports. Some historical local directories
were subsequently removed or archived. Current data-root and restoration
responsibilities belong to [data README](../data/README.md). The full V2.1 flat
artifacts are the source for this work unit. Long-term incremental build
infrastructure remains separate.

**Legacy Phase04 baseline (retired locally on 2026-11-10); Phase05 NaviRAG / Card / Link / KG remains paused.**

The operator has explicitly changed the phase priority. Reuse the existing
Phase 01-03 Raw -> Parsed -> Canonical foundation and Phase 04 RU/RAG modules.
Use DeepSeek as the V1 default final-answer provider. The original RU
full-corpus 1024d embedding build with `Qwen/Qwen3-Embedding-0.6B` is complete.
That paired query encoder/index was the earlier default but its local model and index have now been removed. The legacy Phase04 RAG runtime is not a working local serving path. The 70Q
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

## 2. Historical Phase04 executable baseline and verified capabilities

The table below records the retained Phase04 implementation checkpoint. Its Qwen
model/index paths have since been retired locally; references to defaults and
rollback here do not assert an operational local Serving path. Section 0 owns
current selection and authorization.

Source scope remains `mihoyo_obc` / `zh-cn`; locale remains configurable.

| Layer / capability | Verified baseline and V1 relation |
| --- | --- |
| Phase 01 Raw | CLOSED; immutable official discovery/detail evidence and audited collector. Reuse the accepted snapshot; no new crawl required. |
| Phase 02 Parsed | CLOSED; deterministic source-specific parser, Raw lineage and explicit unknowns. Its draft contract remains evolutionary, not permanently frozen. |
| Phase 03 Canonical | CLOSED; accepted 16,437-record structural corpus. Reuse it without semantic rewriting; closure does not establish semantic completeness. |
| Clean V1 | Direct Raw extraction, source locators, selectors and default 12-worker resumable builds. The local 16,437-record full run remains complete and record-hash checked. Whole-corpus Raw fidelity acceptance and serving adoption remain open. |
| Clean-based chunking | Opt-in V2.1 uses admission policy 4 with the V2 schema, parallel micro/macro candidates, balanced budgets, source-literal conditions, display-only ordering and CleanRef. Default 12-worker builds retain `--resume`. The 46-record V2.1 pilot has 3,663 candidates. The historical recovered V2 full output has 16,437 records / 122,527 candidates under policy 3. **V2.1 full build completed on 2026-11-08:** 16,437 records, 119,557 balanced candidates, 214,870 natural parents and 5,087 graphs under policy 4; on 2026-11-09 its seven flat exports passed SHA-256 verification. Per-record `_records` recovery shards were archived and removed from D: after independent verification, so any no-op resume or shard-level recheck requires restoring them first. This output has not been adopted for serving; Phase04 continues to use the original RU/index/Assembly and full-corpus retrieval quality acceptance remains open. |
| Clean-based indexing input | Opt-in `genshin_corpus.indexing` implements shared input projection, micro/macro route maps, six BM25F text fields, retrieval_text Dense input, hash-bound source readback and independent representation identities. The standalone benchmark adds three pinned local Dense encoders, exact vector-shard search and separate-route Whoosh3 with Han/Jieba channels. Experimental weights and the default Jieba dictionary are explicit configuration. Final quality choices remain follow-up work. |
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
corpus re-audit. At that2026-11-02 checkpoint the70Q runtime and review-only
JSONL files both existed with70 rows; the original temporary copies are absent
now. P4 binds the retained question-set.jsonl identified in its contract.
The initial priority audit's 181-test offline regression run covered Generation/measure,
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

Follow the current validation gate in section 0 and the
[Reranking/migration runbook](research/clean-reranking-preparation-20261113.md).
The completed P4 [70Q quality review](research/recall-p4-70q-quality-review-20261113.md)
does not require another retrieval run; the subsequent S0 decision is recorded
in section 0. Earlier P4 Preflight/Smoke/70Q instructions remain recovery/history
references, not unfinished P4 work. Do not run the historical four-arm launcher
against removed Qwen/Jina assets. The selected indexes are reused read-only. Generation owns
Clean-based candidate-to-Packet assembly after Retrieval and Reranking; this work
does not implement that downstream policy or establish retrieval/answer quality.
The following dated material records retained legacy serving evidence and gaps.

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
| Clean-based Reranking and Python 3.14 migration contract | [`Reranking V1`](phases/clean-reranking-v1.md) |
| Historical Reranking preparation and initial diagnostic checkpoints | [`Preparation report`](research/clean-reranking-preparation-20261113.md) |
| Completed Python migration, compatibility measurements, global entrypoints and cleanup | [`Migration report`](research/python314-migration-20261113.md) |
| Architecture and boundaries | [`Architecture overview`](architecture-overview.md) |
| Phase priorities and milestones | [`Roadmap`](roadmap.md) |
| Directory/package responsibilities | [`Project structure`](project-structure.md) |
| Data roots and artifact responsibilities | [`Data README`](../data/README.md) |
| Durable decisions / ADR policy | [`Decisions`](decisions/README.md) |
| Raw acquisition contract | [`Phase 01`](phases/phase-01-raw-collection.md) |
| Direct Raw-to-Clean schema, builds and read interfaces | [`Clean V1`](phases/clean-v1.md) |
| Clean implementation and bounded pilot evidence | [`Implementation record`](research/clean-v1-implementation-20261106.md) |
| Full Clean structure census and chunking discussion candidates | [`Chunking investigation`](research/clean-chunking-full-survey-20261106.md) |
| Current Clean-based V2 chunking, source conditions, retrieval fields and display semantics | [`Chunking V2`](phases/chunking-v2.md) |
| Unified Clean-based indexing input, field projections, analyzers and representation bindings | [`Indexing input V1`](phases/indexing-input-v1.md) |
| Standalone serial full-index experiment, commands, encoding, recovery and independent search | [`Index benchmark V1`](phases/index-benchmark-v1.md) |
| Clean-based Retrieval P0–P2, fusion, candidate interface and bounded Sparse trial | [`Retrieval V1`](phases/clean-retrieval-v1.md) |
| Retrieval P0–P2 measured results, component reuse, resources and finite limits | [`Implementation report`](research/clean-retrieval-p0p2-20261111.md) |
| P3 streaming Sparse build, recovery, resource and validation contract | [`Sparse build V1`](phases/sparse-build-v1.md) |
| P3 preparation, finite test evidence and standalone PowerShell commands | [`P3 runbook`](research/sparse-build-preparation-20261112.md) |
| P4 S0/S1/S2 comparison, recovery and report contract | [`P4 experiment`](phases/recall-experiment-v1.md) |
| P4 preparation tests, real-Smoke limitation and manual commands | [`P4 runbook`](research/recall-experiment-preparation-20261112.md) |
| Completed P4 validation, bounded 70Q quality judgments and selection recommendation | [`P4 quality review`](research/recall-p4-70q-quality-review-20261113.md) |
| Launcher finite validation, environment reuse and measured limitations | [`Launcher report`](research/index-benchmark-launcher-20261109.md) |
| Indexing work unit 1 source survey, finite export and readback evidence | [`Work-unit report`](research/indexing-input-v1-20261109.md) |
| Historical V1 chunking interface and operating points | [`Chunking V1`](phases/chunking-pilot-v1.md) |
| V2 35-record result, before/after text and 7,759-node classification | [`V2 result`](research/clean-chunking-v2-20261107.md) |
| V2 full-build matcher repair, two-record recovery, mixed bindings and complete artifact acceptance | [`V2 recovery`](research/chunking-v2-recovery-20261107.md) |
| V2.1 finite admission repair, 46-record text comparisons, duplicate audit and validation | [`V2.1 result`](research/clean-chunking-v2-1-20261108.md) |
| Initial chunking comparison and 19 quality cases (historical r7 checkpoint) | [`Chunking pilot result`](research/clean-chunking-pilot-20261107.md) |
| Historical three-rule refinement, 12-record rerun and ten before/after cases | [`Chunking refinement`](research/clean-chunking-refinement-20261107.md) |
| Clean/Chunk default 12-worker builds, recovery, resource evidence and manual commands | [`Build performance`](research/build-performance-20261107.md) |
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
