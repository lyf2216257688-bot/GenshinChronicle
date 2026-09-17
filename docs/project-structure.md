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
- `src/genshin_corpus/retrieval/`: Phase 04 Retrieval Units; lexical, local
  Dense, and deterministic candidate retrieval; reranking adapters/primitives
  and rank fusion; Formal Deferred Evidence Assembly; and rebuildable local
  materialization, comparison, parity, and bounded research tooling. The
  existence of an experiment runner does not authorize its execution, and an
  adapter's existence does not establish the current default. It owns no UI
  lifecycle or Generation provider contract, is not a serving system, and does
  not establish a permanent technology winner.
- `src/genshin_corpus/generation/`: provider-neutral Generation contracts,
  generation-visible Evidence Packet projection, result/citation-integrity
  persistence, and explicitly scoped provider adapters. It consumes an
  Evidence Packet but does not own Retrieval or Evidence Assembly policy.
- `src/genshin_corpus/rag/`: the small provider-neutral single-question RAG
  orchestration boundary. It owns prepared-state reuse and per-question
  composition across Retrieval, optional reranking, Formal Deferred Assembly,
  Evidence Packet handling, optional Generation, citation validation, and
  structured audit/persistence results. It does not redefine the policies or
  provider contracts owned by `retrieval/` and `generation/`.
- `src/genshin_corpus/rag/config.py`: stable production RAG artifact-path
  configuration, separate from M1/M2 measurement runners and without
  Retrieval, Assembly, model, or reranker policy.
- `src/genshin_corpus/ui/`: thin local Streamlit presentation and submit-time
  wiring over the `rag/` backend boundary. It owns no Retrieval, Assembly,
  Generation, citation, or persistence logic.

## Tests and fixtures

- `tests/collector/`: collector tests.
- `tests/parser/`: deterministic Parsed contract, OBC adapter, and run-pipeline
  tests.
- `tests/canonical/`: hand-built Canonical contract fixtures and focused tests.
- `tests/retrieval/`: focused Phase 04 profiler, retrieval, Evidence Assembly,
  materialization, comparison, and Qwen-tooling tests.
- `tests/generation/`: focused offline Generation contract and injected
  transport-adapter tests; no test calls a remote Generation API.
- `tests/ui/`: provider-free lifecycle, configuration-gating, path, and
  bounded-result tests for the Streamlit presentation layer.
- `tests/fixtures/`: small sanitized source samples used by automated tests.

## Data directories

- `data/raw/`: immutable local Raw evidence, including responses and auditable
  run metadata.
- `data/parsed/`: generated Parsed run records and manifests derived from Raw;
  ignored by Git.

`data/canonical/` is the ignored local output root for immutable Canonical run
records and manifests. `data/retrieval/` is the ignored local root for
authorized rebuildable Retrieval/RAG derivatives and materialization outputs;
its detailed responsibilities are owned by `data/README.md`.
