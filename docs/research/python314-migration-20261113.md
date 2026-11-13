# Python 3.14.5 migration and finite Reranker readiness

2026-11-13. **Migration, entrypoint switch and approved Python 3.12 cleanup are
complete.** This report owns measured migration/cleanup facts. Current authorization
and the next action remain with [current-phase](../current-phase.md); the durable
contract is [Clean Reranking V1](../phases/clean-reranking-v1.md).

## Authorization and execution boundary

The operator subsequently authorized Codex to run and inspect local validations
expected within three to five minutes, then explicitly requested that global
Python use 3.14 and that3.12 be removed completely. That instruction authorized
retiring the discovered global 3.12 registration, superseding the original
system-Python exclusion for this specific interpreter. Other projects and
unrelated installations were not removed. Longer full70Q remains an independent
operator-started job; no paid API, index rebuild, Git commit/push or Serving change.

## Environments and actual verification

All active project environments use **Windows AMD64 regular-GIL CPython 3.14.5**.
Isolation is preserved; Torch was not added to the global Python installation.

| Runtime | Verified dependencies |
| --- | --- |
| `.local/runtime-py314/dense-sparse` | Torch 2.11.0+cu128, Transformers 5.1.0, tokenizers 0.22.2, NumPy 2.5.3, SciPy 1.18.1, safetensors 0.8.0, psutil 7.1.3 |
| `.local/runtime-py314/reranker` | Torch 2.11.0+cu128, Transformers 4.57.3, tokenizers 0.22.2, NumPy 2.5.3, safetensors 0.8.0, psutil 7.1.3 |
| Existing `.local/rag-index-prep-20261109/bm25f-runtime` | Whoosh3 3.54.0, jieba 0.42.1; retained Python 3.14.5 and its existing system-package visibility |

Both new runtimes pass dependency imports and pip check again after old-environment
removal, with no old .pth/site-packages path observed. Actual CUDA FP16 model
execution passed for BGE-M3 Dense/Sparse and both Rerankers. BGE-M3 uses the
existing SDPA path; the Rerankers use eager attention, batch one. GTE's unchanged
local custom code loads successfully. FlagEmbedding 1.4.2 remains a pinned official
Sparse kernel plus the existing thin adapter; full training-package import is
not claimed, and no unrelated training dependencies were installed.

The same verified local CPython 3.14 Torch/cu128 wheel was reused in both new
environments. Its hash, package freezes and import identities remain under
`.local/runtime-py314/` as recorded by the historical preparation report.

## Baseline mismatch: cause and correction

The original3.12 single-item Baseline failed against the stored Dense documents.
The preserved r2 diagnostic quantifies 18/32 failed rows, maximum absolute error
`0.0010811388492584229`, and minimum cosine `0.9999599838279329`. High cosine did
not waive the fixed elementwise tolerance. All six originally single-item source
rows were bitwise equal; all 18 failures belonged to originally paired batches.

The bounded replay reconstructed the original shard-local ordering, batches and
padding from hash-checked source artifacts and unchanged encoder code. There was
no historical OOM-driven batch-size ambiguity. It replayed 24 two-item batches
(48 documents:26 anchors plus22 original companions) and reused the six already
bitwise-equal single-item anchors. **All 32 then became bitwise equal to the stored
index, maximum error 0.** The replay itself explicitly admits no migration.

The corrected r3 migration compares the same 32 document anchors and two existing
engineering queries in both environments. Document Dense forwards now reproduce
the original index's exact batch order and companions in BOTH runtimes; query
Dense and all Sparse forwards stay single-item. Companions are shape context,
not additional quality examples. Source/config/code identities and token lengths
are checked. No index data, model parameters, document text or tolerance changed.

| r3 finite check | Actual result |
| --- | --- |
| 3.12 document baseline vs stored Dense | 32/32 bitwise equal, maximum absolute error 0 |
| 3.12 vs3.14 token IDs/projection |PASS on32 documents +2 queries |
| 3.12 vs3.14 Dense |All34 bitwise equal, maximum absolute error 0 |
| 3.12 vs3.14 Sparse support/values |Exact dictionary/value equality on all 34; fixed allclose check also PASS |
| Official Sparse kernel on the corresponding hidden states |PASS in both runtimes |
| Two engineering queries, Dense/Sparse micro/macro Top10 |Exact ranking agreement |

Fixed `rtol=1e-5, atol=1e-6` was never relaxed. This remains a finite engineering
compatibility conclusion, not corpus-wide semantic or answer-quality acceptance.

Evidence roots, all preserved:

- `data/retrieval/python314-baseline-20261113-r2/`: original failed numeric witness.
- `data/retrieval/python314-baseline-review-20261113-r2/`: full32-row read-only review.
- `data/retrieval/python314-baseline-replay-20261113/`: diagnostic replay, explicitly not a certificate.
- `data/retrieval/python314-baseline-20261113-r3/` and `python314-candidate-20261113-r3/`: completed matched witnesses.
- `data/retrieval/python314-compatibility-20261113-r3/`: sealed PASS certificate.

Original manifests, space_ids and historical3.12 facts remain unchanged. Query
admission uses the explicit bound compatibility gate; runtime identity still
truthfully records3.14.5. No index rebuilding or new document encoding was used
to replace existing index rows.

## Switch and independent operation

Activation backed up and switched the active `dense_python` in
`config/clean-retrieval-v1.json` plus the interpreter paths in Sparse/P4 operator
configs. This does not resume their frozen historical runs. Reranking already
uses the new separate3.14 runtime.

With the shared old 3.12 base temporarily unavailable, a complete actual S0 query
returned 100 candidates and passed both Dense and BM25F independent-search checks.
Output: `data/retrieval/python314-s0-switch-smoke-20261113/`. The question, inputs,
result hashes and final receipt were independently inspected. This verifies the
active launcher path without a3.12 fallback, not relevance or Generation adoption.

## Cleanup and global Python

Before deletion, process and scheduled-task inspection found no old-project-runtime
consumer. Process/user/machine PYTHONPATH and PYTHONHOME checks found no old
references. Registered worktree environments and IDE settings were reviewed;
the locked E1 environment contained only pip and no active launch reference was
found. Its lock, source and adjacent model/data assets remain intact.

Deleted, in the approved order:

1. `.local/embedding-runtime`.
2. `.local/gte-reranker-runtime`.
3. `.local/rag-index-prep-20261109/candidate-runtime`.
4. The registered E1 worktree's `.local/e1/venv` only.
5. `.local/python312-base`, last, after rechecking remaining environment references.

The first final-base cleanup correctly stopped because the user-level global
Python 3.12 launcher registration still pointed to that base. After explicit
operator authorization, both 3.12 and3.14 registry trees were exported as backups.
Inspection also found the 3.14 registration pointing at a stale drive location.
Only its Python registration path values were repaired to the existing verified
3.14.5 installation, then validated before removing the project-owned3.12 key.
No global package reinstall or unrelated Python removal was performed.

Final host checks: `python --version` and `py --version` both return
`Python 3.14.5`; `py -0p` lists only 3.14. The known project/worktree environment
scan has zero remaining 3.12 environments. PATH had no old 3.12 entry to remove.
Historical source pins, manifests and audit reports intentionally retain3.12
text; they are not live runtime dependencies.

Retired launchers now refuse clearly through the local retirement marker:
old Baseline/Replay modes, the removed-Qwen build, and the historical four-arm
benchmark. Frozen Python model adapters keep their original identity checks.
The retained Phase04 UI/backend references removed historical assets and is not
an operational fallback. This work does not restore those tasks or move them
into the new active Reranking path.

Cleanup/registration evidence under `.local/runtime-py314/` includes
`consumer-audit-20261113.json`, `environment-variable-audit.json`,
`old-base-disabled-check.json`, `virtualenv-cleanup-20261113.json`,
`shared-base-cleanup-20261113.json`, `global-python-registration-20261113.json`,
`python312-registration-backup.reg`, `python314-registration-backup.reg`,
`retired-entrypoints.json` and `final-migration-report.json`.

## Tests, Reranker readiness and remaining work

Final relevant fixtures: **106 passed** in the new 3.14 Dense runtime. The 16 focused
migration/compatibility tests also passed in both old 3.12 and new 3.14 before
cleanup. Separate index fixtures previously ran 61 cases (52 passes/9 lexical
skips); the BM25F environment ran 53 (51 passes/2 Torch skips). These overlapping
counts are not summed. New/retirement PowerShell launchers parse; old Baseline
refuses before starting a retired interpreter. Post-cleanup imports and both
pip checks pass. Fixture logs that mention Smoke contain mock workers.

The operator's actual BGE/GTE Preflight/Smoke/Validate remains valid after all
migration changes: all 6,963 frozen pairs were audited,25–1,150 tokens, common
budget 2048. Four actual Smoke groups pass (independent-question pair lengths 26
and 1,129). These verify local loading/scalar logits and finite resources, not a
quality winner. No extra Reranker inference was needed for the migration fix.

At the migration-closure checkpoint, the full70Q BGE/GTE comparison had **not run**.
The operator subsequently completed it; the later
[P2 comparison report](clean-reranking-p2-comparison-20261113.md) owns those results
and the model-decision pause. The commands below preserve the earlier handoff;
do not restart the completed comparison. It comprises 6,963 pairs per model
and about 2.47 million input tokens per model, beyond the short validation scope.
In the independently operated long-run window:

```powershell
./scripts/retrieval/run-clean-reranking.ps1 -Mode Run
# After successful completion:
./scripts/retrieval/run-clean-reranking.ps1 -Mode Validate
./scripts/retrieval/run-clean-reranking.ps1 -Mode Report
```

Use Resume only after an interrupted Run. After report review and explicit model
selection, Analyze reuses scores for structure/MMR. Evidence Assembly remains
Generation's responsibility; no seventh branch, Packet construction or semantic
adoption is introduced. Raw/Clean/Chunk and existing indexes remain unchanged.
