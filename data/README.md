# Data Directory

`data/` contains generated/local datasets, not source code.

## Phase 01

`data/raw/` is the intended local home for collected Raw artifacts once the collector exists.

Raw principles:
- preserve received responses;
- do not silently normalize or prune Raw evidence;
- do not overwrite historical evidence merely to keep one "latest" copy;
- store enough metadata to reconstruct what was fetched and when;
- keep manifests/observations auditable.

The full dataset is intentionally ignored by Git.

Small sanitized samples needed for automated tests belong under `tests/fixtures/`, not here.

## Future directories

`data/parsed/`, `data/canonical/`, and retrieval artifacts should be introduced only when those phases begin. They are not part of the current implementation scope.
