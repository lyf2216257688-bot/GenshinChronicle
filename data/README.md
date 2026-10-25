# Data Directory

`data/` contains generated/local datasets, not source code.

## Raw evidence

`data/raw/` is the local home for collected Raw artifacts.

Raw principles:
- preserve received responses;
- do not silently normalize or prune Raw evidence;
- do not overwrite historical evidence merely to keep one "latest" copy;
- store enough metadata to reconstruct what was fetched and when;
- keep manifests/observations auditable.

The full dataset is intentionally ignored by Git.

Small sanitized samples needed for automated tests belong under `tests/fixtures/`, not here.

## Parsed outputs

`data/parsed/` contains generated, Git-ignored Parsed runs produced from local
Raw runs. A Parsed run stores immutable record files plus an auditable manifest
that records its input Raw run, dependencies, fingerprints, statuses, and
diagnostics. Parsed output is derived data, not source code and not a
replacement for Raw evidence.

## Canonical outputs

`data/canonical/` contains local, immutable Canonical run records and manifests
derived from accepted Parsed observations. Canonical data remains the evidence
source for later Retrieval derivatives and must not be rewritten by Phase 04
tools.

## Retrieval and RAG artifacts

`data/retrieval/` is an ignored local destination for authorized, rebuildable
Phase 04 and Phase 05 derivatives: Retrieval Unit builds, lexical/Dense indexes
and row mappings, source-bound semantic builds and distinct hierarchy/graph
views, deterministic candidate and Evidence Packet materialization, and their
manifests, ledgers, and provenance. Phase 05 semantic artifacts are navigation
derivatives, not evidence authority; final citation authority remains the
Evidence Packet resolved to official Retrieval Units and Canonical lineage.
The root can hold the local production materialization baseline, but is not a
serving-system, vector-database, or permanent source-of-truth contract.

Candidate V offline research roots contain a release catalog and metadata-join
audit, input/implementation fingerprints, immutable session manifests and chained
reading/understanding checkpoints, and bounded replay results. Coverage receipts
record delivery separately from interpretation; they are not formal citations
or accepted semantic views. New runs use new directories, leaving historical
A/B roots immutable.

R1 Navi discovery comparison roots preserve frozen 128-document research
scopes, evaluator-only reference proposals, independent source profiles,
provider attempts/raw responses, derived experimental vectors and rankings,
dual-source judgments, stability checks and cost accounting. Partial model
cohorts remain separate from the complete-model cohort; execution-unknown paid
requests are quarantined. Model review is not human semantic acceptance, and
full raw-corpus retrieval does not imply full-corpus semantic production.

The separate `p05-navi-r1-qa-20261025-r*` roots preserve source-pair discovery
ledgers, legacy dispositions, hash-verified Canonical/RU snippets, blind review
deliveries, raw model/stream outcomes, independent reconciliation projections,
method comparisons and pending human review materials. Partial Qwen and later
operator-requested jizhi DeepSeek cohorts remain distinct; the uniform DeepSeek
review owns the new candidate comparison. Exact quote binding, model semantics
and human acceptance are separately recorded. No QA artifact changes R1 inputs,
Canonical evidence, production retrieval or active semantic views.

The E2 four-model Responses protocol evidence uses a separate immutable campaign
root, `data/retrieval/p05-navi-e2-responses-four-model-20261015-r1/`. Its
authorization, per-model probe configs, issued attempts, raw error/SSE evidence,
terminal usage, action validation, and offline audit are synthetic protocol
evidence, not formal autonomous research or Canonical material. Runtime API
credentials never belong in this root. The independently authorized follow-up
root `data/retrieval/p05-navi-e2-responses-followup-20261015-r2/` binds the frozen
original campaign manifest and counts its issued requests cumulatively. It holds
new Jizhi GLM/DeepSeek probes, not resume/replay or a resolution of earlier UNKNOWN
requests. Historical E2 r1-r4 and original protocol roots stay immutable.

Experiment-specific, review-only, provider, or challenger evidence that is
kept under `.local/` remains outside this data-root contract and must not be
promoted to tracked data merely because it informs a later decision.
