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
- `src/genshin_corpus/canonical/`: Phase 03 Batch 1 Canonical contracts,
  fingerprints, deterministic in-memory serialization, and no corpus storage
  or source-specific projector.

## Tests and fixtures

- `tests/collector/`: collector tests.
- `tests/parser/`: deterministic Parsed contract, OBC adapter, and run-pipeline
  tests.
- `tests/canonical/`: hand-built Canonical contract fixtures and focused tests.
- `tests/fixtures/`: small sanitized source samples used by automated tests.

## Data directories

- `data/raw/`: immutable local Raw evidence, including responses and auditable
  run metadata.
- `data/parsed/`: generated Parsed run records and manifests derived from Raw;
  ignored by Git.

The Canonical source package is limited to its authorized Batch 1 contract
foundation. `data/canonical/` is not established. Retrieval source and data
directories remain deferred to their own future phase contract.
