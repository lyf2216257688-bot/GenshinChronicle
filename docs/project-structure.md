# Project Structure

This document describes directory responsibilities. Current project status and
the immediate next action are owned by `docs/current-phase.md`.

## Repository root

- `AGENTS.md`: repository-wide engineering and phase-boundary instructions.
- `README.md`: project orientation and high-level repository entry point.
- `docs/`: architecture, phase specifications, research evidence, and decisions.
- `src/`: importable corpus implementation.
- `tests/`: deterministic automated tests and checked-in sanitized fixtures.
- `data/`: local and generated corpus data; it is not source code.

## Source packages

- `src/genshin_corpus/collector/`: source discovery, acquisition, Raw artifact
  storage, manifests, and collector-specific validation.
- `src/genshin_corpus/parser/`: Parsed-layer contracts, source/parsed
  fingerprints, identity helpers, classification containers, storage, and
  source-specific parser adapters.
- `src/genshin_corpus/parser/obc/`: MiHoYo OBC detail adapter and the local
  Raw-to-Parsed run entry point.
- `src/genshin_corpus/canonical/`: Phase 03 Canonical contracts,
  deterministic serialization, structural OBC projection, and the local
  Canonical run-storage/pipeline boundary.
- `src/genshin_corpus/retrieval/`: Phase 04 read-only corpus profiling and
  benchmark-contract validation; it contains no retrieval engine or index.

## Tests and fixtures

- `tests/collector/`: collector tests.
- `tests/parser/`: deterministic Parsed contract, OBC adapter, and run-pipeline
  tests.
- `tests/canonical/`: hand-built Canonical contract fixtures and focused tests.
- `tests/retrieval/`: Phase 04 profiler and benchmark-contract focused tests.
- `tests/fixtures/`: small sanitized source samples used by automated tests.

## Data directories

- `data/raw/`: immutable local Raw evidence, including responses and auditable
  run metadata.
- `data/parsed/`: generated Parsed run records and manifests derived from Raw;
  ignored by Git.

`data/canonical/` is the ignored local output root for immutable Canonical run
records and manifests. `data/retrieval/` is ignored and reserved for explicitly
authorized rebuildable Retrieval experiment artifacts; P04-W1 does not create
a production index or derived corpus.
