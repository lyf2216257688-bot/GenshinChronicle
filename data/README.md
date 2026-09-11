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
Phase 04 derivatives: Retrieval Unit builds, lexical/Dense indexes and row
mappings, deterministic candidate and Evidence Packet materialization, and
their manifests, ledgers, and provenance. It can hold the local production
materialization baseline, but is not a serving-system, vector-database, or
permanent source-of-truth contract.

Experiment-specific, review-only, provider, or challenger evidence that is
kept under `.local/` remains outside this data-root contract and must not be
promoted to tracked data merely because it informs a later decision.
