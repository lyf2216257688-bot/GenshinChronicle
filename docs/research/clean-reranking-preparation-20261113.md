# Clean-based Reranking / Python 3.14 preparation

2026-11-13. **Historical preparation and r2 diagnostic checkpoints.** The subsequent
[completed migration report](python314-migration-20261113.md) supersedes the
migration status, execution boundary and old Baseline commands below. This file
preserves the original preparation observations; it does not override current-phase.

## Authorized split and assets

At the initial checkpoint, Codex prepared environments, model-free input export, implementation and fixture
tests. The operator starts all real model Preflight/Smoke, Baseline/Candidate and
70Q independently; Codex must not launch, keep alive or monitor those manual runs.
No upstream data/index rebuild, paid API, Serving switch or Git operation.

The input is the existing validated P4 S0/f1_max + cleanup result. The create-only
`data/retrieval/rerank-v1-input-20261113/` contains 70 questions / 6,963 candidates,
original text, Chunk/source/recall information and P4 receipt bindings. Export is
model-free and does not rerun retrieval. Existing BGE/GTE files and Dense vectors
are reused. Dependency packages and environment inventory live under
`.local/runtime-py314/`; no downloaded model source is edited.

## Historical initial PowerShell 7 commands (superseded)

From the repository root, after closing memory-heavy apps as appropriate. Run
each command in order; on failure preserve the output and return the error before
continuing dependent steps:

```powershell
# Historical r2 request only; this old-runtime mode is now retired.
./scripts/retrieval/run-python314-migration.ps1 -Mode Baseline
```

At this checkpoint default migration outputs ended in `-20261113-r2`; the first failed attempt was
preserved. Return `data/retrieval/python314-baseline-20261113-r2/baseline-index-check.json`
and the terminal result before proceeding. This uses the same 32 documents and
two queries, same single-item forwards and unchanged rtol=1e-5/atol=1e-6. It may
still fail: the purpose of this retry is to retain the exact discrepancy, not
weaken the acceptance gate. Do not repeat the already valid Reranker Preflight/
Smoke/Validate or start full70Q at this blocked checkpoint.

Only after a successful Baseline and review of its output, continue migration:

```powershell
./scripts/retrieval/run-python314-migration.ps1 -Mode Candidate
./scripts/retrieval/run-python314-migration.ps1 -Mode Compare
```

Return the migration Compare report and Reranker Smoke validation before cleanup.
Do not delete or disable the old interpreter before capturing the baseline.
Each output is create-only. If a preparation/Smoke is interrupted, retain that
attempt; report the failure and choose a coherent new config/output pair rather
than overwriting it. Changes to code/config/assets require matching new preflight.
The default resource figures are advice, never hard admission conditions.

Once both validations pass, Activate can switch active config paths after backing
them up; it does not clean environments or update historical manifests:

```powershell
./scripts/retrieval/run-python314-migration.ps1 -Mode Activate
```

The complete 70Q remains operator-started. It needs the matching independent
Smoke validation. Resume reuses committed question/model groups:

```powershell
./scripts/retrieval/run-clean-reranking.ps1 -Mode Run
# Only after an interrupted run:
./scripts/retrieval/run-clean-reranking.ps1 -Mode Resume
./scripts/retrieval/run-clean-reranking.ps1 -Mode Validate
./scripts/retrieval/run-clean-reranking.ps1 -Mode Report
```

Select the model after reviewing the comparison, then use exactly one selected
model for the structural/MMR ablation, without new model inference:

```powershell
# Replace bge with gte if that is the operator's selection.
./scripts/retrieval/run-clean-reranking.ps1 -Mode Analyze -Model bge
```

Report/Analyze create fresh reports under the run's reports folder. JSON traces
reference the frozen inputs and preserve every candidate and model/runtime binding.
S0/BGE/GTE have explicit Top5/10/20 views and complete ordering references.
Markdown pages provide Top20 original text and source-unknown status; fragment
rankings are diagnostics. Per-question telemetry separates model load, tokenization,
forward and result-file serialization/write time (the latter excludes receipt
writing); cold model-load GPU peaks are separate from scoring peaks.
The report does not automatically classify semantic improvements or select a winner.

## Compatibility and cleanup limits

Python 3.14 wheels exist for the pinned Torch/cu128 and numerical packages. That
does not establish model loading, CUDA kernels, GTE custom-code compatibility or
numeric equivalence. Old reranker tokenizers .15.2 cannot be transferred unchanged;
the new .22.2/Transformers 4.57.3 profile is isolated from historical adapters.

The Dense/Sparse space IDs include Python versions. They are not rewritten.
Finite witness comparison and a sealed compatibility record are required before
new-runtime query admission. All other identity checks remain in force. Failed
compatibility retains the old runtimes and original indexes; no automatic rebuild.

The registered locked E1 worktree still has an environment referring to the main
3.12 base (only pip was observed). Environment migration/retirement review is
authorized, but no worktree unlock, code/data edit or E1 model restart follows.
Static inventory and absence of an observed Python process do not establish that
no caller exists. Cleanup remains pending until caller/process/scheduler review,
successful switch and old-path-disabled validation are complete.

## Initial delivery observations (historical, before switch/cleanup)

- Saved S0 export: completed, 70 questions / 6,963 candidates.
- Ordering, offline BGE/GTE adapters, recovery/validation/reporting, structural/MMR
  replay and migration witness/compatibility/activation entrypoints are implemented.
- Two isolated Python 3.14.5 environments are installed and their dependency-only
  checks pass. This is a staged migration, not a successful runtime/index migration.
- Codex has not run real-model jobs. The operator completed Reranker Preflight/
  Smoke/Validate; old Baseline failed and Candidate/Compare could not complete.
  The new Dense runtime compatibility and full70Q remain unverified.
- Activation and Python 3.12 cleanup: NOT PERFORMED; validation gates remain open.

### Installed environments and compatibility evidence

Both new environments use Windows AMD64 regular-GIL CPython 3.14.5. The downloaded
`torch-2.11.0+cu128-cp314-cp314-win_amd64.whl` was verified as 2,770,971,557 bytes,
SHA256 `d6c21797ff75271b4fbdd905e2d703be4ecea5ea5bbdde4d1c201e9c71bc411d`, then
installed from the same local file into both environments. The earlier interrupted
network installation was not accepted as a complete installation.

| Environment | Verified versions and status |
| --- | --- |
| `.local/runtime-py314/dense-sparse` | Python 3.14.5; Torch 2.11.0+cu128; Transformers 5.1.0; tokenizers 0.22.2; NumPy 2.5.3; SciPy 1.18.1; safetensors 0.8.0; psutil 7.1.3. Imports and pip check PASS. |
| `.local/runtime-py314/reranker` | Python 3.14.5; Torch 2.11.0+cu128; Transformers 4.57.3; tokenizers 0.22.2; NumPy 2.5.3; safetensors 0.8.0; psutil 7.1.3. Imports, pinned runtime identity and pip check PASS. |
| Existing `.local/rag-index-prep-20261109/bm25f-runtime` | Retained Python 3.14.5; Whoosh3 3.54.0; jieba 0.42.1. Still permits system packages; observed import paths contain Python 3.14 user/system locations, no 3.12 location. Fixture index/query checks pass. |

The new Dense/Sparse and Reranker paths contain their own site-packages and the
existing 3.14 base, with no old `.pth`/3.12 environment reference in observed
`sys.path`. Their full installed dependency freezes and refreshed package/.pth/
caller/worktree inventory are local evidence; this does not replace the later
old-path-disabled and no-consumer checks.

- Initial dependency checks report Torch CUDA build 12.8, passing CPU fixtures
  and no initialized CUDA context. The subsequent operator Reranker Smoke verifies
  model loading and FP16 CUDA eager execution for both models, as measured below.
  New-runtime BGE-M3 SDPA/numerical compatibility is still unverified; no observed
  OOM does not establish a universal resource limit or test every OOM path.
- BGE `XLMRobertaForSequenceClassification` and GTE's original `NewConfig` /
  `NewForSequenceClassification` import under Transformers 4.57.3. No downloaded
  source was modified; no xformers/FlashAttention was installed. The subsequent
  operator Smoke also passes local AutoClass loading, pairing and scalar-logit
  scoring on the recorded samples; it is not a quality verdict.
- The pinned FlagEmbedding 1.4.2 official Sparse kernel compiles and passes a tiny
  CPU fixture for repeated-token max aggregation and special-token exclusion.
  This is not full FlagEmbedding import, CUDA/real-hidden-state parity, or old/new
  index compatibility. No unrelated training dependencies were added.

Evidence under `.local/runtime-py314/`:
`verified-wheel.json`, `dense-imports.json`, `reranker-imports.json`,
`dense-pip-check.log`, `reranker-pip-check.log`, `dense-freeze.txt`,
`reranker-freeze.txt`, `bm25f-identity.json`, `inventory-ready-20261113.json`.

### Finite regression results

| Runtime/check | Actual result |
| --- | --- |
| Base Python 3.14.5: new ordering/migration/compatibility, Retrieval/P4, historical BGE/GTE/reranking fixtures | 99 tests passed; later changes were rechecked with the affected 24-test subset in the new Dense environment, all passed. |
| New Dense/Sparse Python 3.14.5: indexing, Sparse trial/full tiny fixtures | 61 tests run, 52 passed and 9 lexical-dependency skips. CPU pooling and process recovery pass. |
| Existing BM25F Python 3.14.5: indexing and Clean Retrieval fixtures | 53 tests run, 51 passed and 2 Torch skips. This covers the separate lexical fixture path. |
| Both new PowerShell launchers | Parser checks PASS. |

These overlapping counts are not added together. Logs are `fixture-tests.log`,
`dense-migration-rerank-tests.log`, `dense-index-fixtures.log` and
`lexical-fixtures.log` under the same local evidence directory. Fixture logs that
say `engineering-smoke` use mocked workers and temporary data, not real models.

The first new-venv index fixture run exposed a Windows redirector issue in the
hard-process-exit test: killing only the launcher could leave its interpreter
holding the temporary lock. The fixture now uses the existing Windows Job Object
process-tree helper already used by production workers. The isolated regression
and the 61-test index suite then pass; index algorithms and actual indexes were
not changed.

### Historical r2 retained environments (superseded)

At that checkpoint no old environment or shared base had been deleted. The retained set was `.local/embedding-runtime`,
`.local/rag-index-prep-20261109/candidate-runtime`, `.local/gte-reranker-runtime`,
`.local/python312-base` and the locked E1 worktree environment. Reasons: the old
Dense baseline is still needed, active configs remain unchanged, historical
callers retain their frozen contracts, and no-consumer/process/scheduler plus
old-path-disabled checks are not complete. The E1 lock, code and data were not
changed. No Raw/Clean/Chunk or existing index was rewritten or rebuilt.

At this historical r2 checkpoint, the next gate was the diagnostic Baseline retry;
the later corrected migration and cleanup are recorded in the completed report.
The old3.12 Baseline/Replay entrypoints are now retired and must not be rerun.
Model selection, full70Q review, incremental MMR adoption and final cleanup remain
outstanding.

### Operator-run checkpoint and diagnostic correction

Read-only review confirms the operator's Reranker validation is PASS for all four
model/sample groups, bound to complete SHA256
`4e3f3d1308989d1127cd8888914868587508ba206645839285a81a1b985b904c` and
plan binding `e61867fba7cd5bb3382332d21375cabbacb98b2033ba0bcb6a33bd0896b4c52f`.
Both actual tokenizers audited all 6,963 pairs: 25–1,150 tokens, so the common
2048 workpoint suffices for this frozen input. Smoke uses the shortest/longest
selected passages with an independent question: 26 and 1,129 tokens, not every
original query/passage pairing.

| Observed Smoke measurement | BGE | GTE |
| --- | --- | --- |
| Model load time | 1.560 s | 0.520 s |
| Longer sample scoring time, including tokenization/transfer | 0.161 s | 0.070 s |
| Maximum recorded scoring GPU allocated / reserved | 1.197 / 1.223 GiB | 0.674 / 0.695 GiB |
| OS process lifetime peak working set | 4.023 GiB | 1.558 GiB |

These are one finite engineering Smoke, not stable throughput statistics,
minimum free-resource admission thresholds or model quality/selection evidence.
No new hard RAM/VRAM gate was set. The original Reranker code/config/plan were
left unchanged and their validation still matches after the migration-only fix.

The old Python 3.12 Baseline completed its 34 forward samples, then failed before
saving vectors at `Baseline does not reproduce stored Dense rows`. Therefore the
first attempt cannot establish the failed-row count, error magnitude or root
cause; Candidate/Compare failures are consequential missing-baseline failures,
not measured Python 3.14 numerical failures. Both failed output directories are
empty and were preserved.

Source-artifact inspection found the original encoder code unchanged and the
original index operating point max_batch=2 / padded_token_budget=2048. Of the
same 32 samples, 26 would be in two-item batches under that source plan; the
migration Baseline uses single-item forwards. Batch/padding shape is a plausible
cause, not an established explanation or permission to relax tolerance. The
model-free audit is `.local/runtime-py314/baseline-shape-audit-20261113.json`.

The migration capture now writes `capture-plan.json`, `dense.npy`, `sparse.json`
and `encoded.json` before the stored-index comparison; failure also retains
`stored-dense.npy` and `baseline-index-check.json` with per-row errors and failing
component counts. The same NumPy isclose predicate and fixed tolerances remain.
Failure produces no completed witness, query admission or certificate. Candidate
checks for a completed Baseline before creating its output. Compare requires a
passing stored-index diagnostic at the same tolerances. Default migration paths
were moved together to a fresh `-r2` attempt; no failed attempt is overwritten.

All 13 focused migration/compatibility tests pass under both Python 3.12.10 and
the new Python 3.14.5 Dense environment, including a mocked capture that retains
encoded values and refuses completion/search after a failed baseline. Logs:
`migration-diagnostics-tests.log` and `migration-diagnostics-tests-py312.log`.
These are fixtures, not another model run or a compatibility PASS.
