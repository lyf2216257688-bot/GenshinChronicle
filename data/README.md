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

## Retrieval experiment artifacts

`data/retrieval/` is an ignored local destination for explicitly authorized,
rebuildable Retrieval experiment artifacts. P04-W2 may store compact,
manifested run-level derived-document artifacts and offline lexical experiment
results; it does not create a production index, vector store, or serving
infrastructure.
