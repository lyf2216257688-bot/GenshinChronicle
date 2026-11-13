# Clean-based Reranking V1 and Python 3.14 migration

2026-11-13. The operator approved implementation of the revised Plan. Current
readiness and actual execution belong to `docs/current-phase.md`; this file owns
the contract. The operator subsequently authorized Codex to start and inspect
finite local validations expected to finish within three to five minutes, including
migration witnesses and diagnostic replay. Longer70Q remains operator-started in
an independent PowerShell 7 window; Codex does not start or poll that manual run.
No paid API, Serving change, index rebuild, commit or push is implied.

## Fixed inputs and independent comparison

Use the validated P4 saved S0/f1_max candidates with upstream structural cleanup
unchanged: 70 original questions and 6,963 question/candidate pairs. Prepare verifies
the P4 completion/validation, per-question receipts, input identity and text hashes,
then writes a create-only snapshot. `input_bundle_id + chunk_id` remains identity.
The complete `retrieval_text`, question and source mappings are identical for BGE
and GTE. Neither model sees review labels, answers or expanded Parent text.

Only BAAI/bge-reranker-v2-m3 and Alibaba-NLP/gte-multilingual-reranker-base are
allowed. Both use FP16 CUDA, batch one, eager attention and a dedicated regular-GIL
CPython 3.14.5 environment. Frozen Phase04 adapters and their pins are preserved.
The new adapter pins the existing weights and GTE custom-code hashes; README is
not a load dependency. GTE's local staging rewrites only the copied auto_map and
uses a new offline module cache. Downloaded model files are not rewritten.

Preflight audits every full query/passage pair with both actual tokenizers and
performs no model inference. Start with 2048 tokens; if needed, raise the common
budget to the longest actual pair, bounded by both model configurations. Real
Smoke checks the shortest/longest saved candidates with independent engineering
questions. Overlength or OOM is explicit failure: no truncation, skipped candidate,
CPU fallback or automatic reduction in depth. No complex windowing is implemented.
4GiB available RAM and 2GiB free VRAM are advice, not admission gates. The optional
running RAM floor is null until explicitly configured from observed Smoke results.

## Ordering, protection and downstream interface

The initial BGE/GTE comparison applies relevance only. Stable ties use original
recall order. Every request candidate must have exactly one finite scalar logit.
`ranked_candidates` returns the original candidate fields plus relevance rank,
raw logit, structural coverage/overlap, MMR trace and final rank. It is a
RankedCandidateSet, not an Evidence Packet. Recall discovery/fusion is unchanged.

After the operator chooses a model, Analyze reuses its saved scores. Compare pure
relevance, structural-only, structural+lambda .85 and structural+lambda .7.
Containment reuses the existing conservative source-range proof, including
observation, Parent, context, part qualifiers, conditions and aliases. A less
relevant wholly covered candidate is deferred, never deleted. Partial overlap,
different events in one Parent, cross-source similarities and unknown structure
are not merged or subjected to Parent quotas. Reverse containment does not prove
that a macro candidate lacks independent clues.

MMR is confined to original relevance Top30 non-deferred candidates. Relevance is
local logit min/max normalization, retaining relative score gaps. Protect Top3
representatives and candidates within 10% of the local total score span from the
highest score, including ties. These are initial configurable workpoints, not
calibrated correctness probabilities. Covered duplicates do not gain protection
again. All-equal logits keep the existing order. Other candidates maximize
`lambda*relevance - (1-lambda)*max(nonnegative cosine to selected candidates)`.
Use verified stored BGE-M3 vectors, with no encoder load or new vector generation.
Outside-pool candidates retain structural-stage order. Lambda one reproduces the
structural baseline. Missing or foreign vectors fail the MMR arm explicitly.

Generation alone owns Evidence Assembly: source/natural-Parent readback, final
evidence admission, deduplication, context budget and Packet construction. No
seventh workstream, query routing, Listwise, general LLM reranking or fine-tuning.

## Runtime migration without rebuilding indexes

New isolated environments use existing Python 3.14.5. Retain selected package
versions where CPython 3.14 builds exist; never import cp312 extensions or use an
old site-packages .pth. Dense/Sparse keeps Transformers 5.1.0; the first Reranker
candidate is Transformers 4.57.3/tokenizers .22.2. Both pin Torch 2.11.0+cu128.
The BM25F environment already uses 3.14.5 and is retained. No training extras or
new CUDA toolchain are required. FlagEmbedding validation retains its fixed
official wheel kernel and existing thin adapter; full-package import was not a
previously accepted requirement.

Existing Dense/Sparse identities include Python/packages and stay immutable.
Operator-run Baseline and Candidate capture 32 existing source samples and two
engineering queries, exact token IDs, Dense/Sparse values, official Sparse-kernel
checks and Top10 route rankings. Document Dense validation uses the original
index's shard-local batches, order and padding companions in BOTH environments;
queries and Sparse validation remain single-item. This corrects the demonstrated
single-versus-batched comparison mismatch without changing production encoding
or the fixed 32 anchors. Only the original partners needed for the same shapes
are read, with max_batch=2 and no historical OOM ambiguity. Baseline additionally
checks its document vectors against stored Dense rows. Compare uses fixed
rtol=1e-5/atol=1e-6, equal Sparse support and exact diagnostic rankings; do not
relax tolerances after failure. A sealed certificate binds both witnesses, code,
source manifest digests and actual target contracts. The initial admission allows
only the Python 3.12.10 -> 3.14.5 difference with other representation fields and
the recorded package versions unchanged. Finite compatibility is not a global
semantic guarantee.

Persist encoded Dense/Sparse values, exact token IDs and runtime/source bindings
before testing the old baseline against stored rows. That check records per-row
absolute error, failing component counts and tolerance ratios, and preserves the
stored comparison vectors. Failure produces no completed witness or compatibility
admission. An incomplete baseline blocks Candidate before creating its output.
Use a new coherent output set for a retry; never overwrite the failed attempt.

`runtime_compatibility.index_space` admits only the specific validated target to
the original index space. It never overwrites encoder identity or index metadata.
The S0 worker uses this opt-in mapping; no certificate means fail closed. Historical
P3/P4 jobs retain their frozen contracts and cannot silently Resume with changed
code or configs. Sparse assets and S1/S2 results remain preserved, not adopted.

Activate requires both the Dense/Sparse certificate and independently validated
matching Reranker Smoke. It checks the installed target encoder, backs up configs,
then switches active Dense and operator configs. It performs no deletion. Old
environments and python312-base remain until all active callers, .pth/PYTHONPATH,
processes, scheduled entrypoints and registered worktrees are checked. The locked
E1 environment is included only in environment review; its lock/code/data remain.
Any unresolved consumer or compatibility failure blocks cleanup, not preservation.
The operator later authorized global Python3.14 adoption and removal of the
specific project-owned3.12 global registration. Back up those registration keys,
validate the existing3.14 interpreter/launcher first, and remove only the verified
old registration/base after its consumers are cleared. This does not authorize
removing unrelated Python installations or other projects.

## Recovery and finite acceptance

Use new output roots outside source/index/model roots. Question/model attempts
are immutable, result first and completion receipt last. Resume ignores incomplete
attempts, reuses committed groups and rejects corruption/duplicates/code drift.
Workers are serial and owned by Windows Job Objects. A complete matching real
Smoke and independent Validate are prerequisites for Run/Resume. Validate checks
all identities, scores and token audits without inference. Reports retain all
candidate references and original-text Top20 pages; small JSON views reference
the frozen input instead of copying provenance repeatedly.

Review is lightweight, question by question. Existing literal witness fragments
may be located only within matching observation/Parent/conditions and are labeled
as fragment diagnostics, not whole-Chunk equivalence or gold relevance. Report
cold load separately from scoring, per-question median/P95, GPU allocated/reserved
peaks and process memory observations. Mechanical PASS remains NOT_REVIEWED for
semantics. The operator selects the base model and whether MMR is worth keeping;
no incremental gain permits the simpler no-MMR outcome.
