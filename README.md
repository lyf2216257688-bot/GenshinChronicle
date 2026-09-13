# Genshin Official Text Research Infrastructure

This repository is the engineering workspace for a research-oriented corpus of official Genshin Impact text.

The long-term direction is:

```text
Official sources
    -> Raw
    -> Parsed
    -> Canonical
    -> Retrieval / RAG
```

The project intentionally separates:

1. how an official source/API organizes data;
2. how game content is structurally represented;
3. how research or AI later interprets that content.

Those are different layers and must not be collapsed into one schema.

## Current status

Phases 01 — Raw Collection, 02 — Parsing, and 03 — Canonical Corpus are
**CLOSED**. Phase 04's first end-to-end RAG baseline is implemented,
materialized, and measured over the accepted 16,437-record OBC `zh-cn`
Canonical snapshot. It includes Retrieval Units, BM25, accepted Qwen Dense,
RRF
Hybrid, deterministic Evidence Assembly, Evidence Packets, and
provider-neutral Generation contracts.

Read `docs/current-phase.md` for the sole current production/default Dense,
historical challenger, reranker, attribution, authorization, and immediate-next
status.

Start here:

- `AGENTS.md` — repository-wide working rules for Codex
- `docs/current-phase.md` — current status and immediate next action
- `docs/phases/phase-04-first-end-to-end-rag-amendment-v1.md` — Phase 04 RAG v1 contract and historical plan
- `docs/research/phase-04/p04-rag-historical-checkpoint-register.md` — compact Phase 04 checkpoint index and evidence pointers
- `docs/architecture-overview.md` — long-term architecture, intentionally high-level
- `docs/roadmap.md` — phase-level direction and status

## Repository layout

The following is an abridged/key layout; consult `docs/project-structure.md`
for stable directory responsibilities.

```text
AGENTS.md
README.md

docs/
├── architecture-overview.md
├── current-phase.md
├── roadmap.md
├── phases/
│   ├── phase-01-raw-collection.md
│   ├── phase-02-parsed-schema.md
│   ├── phase-03-canonical-schema.md
│   └── phase-04-*.md
├── research/
│   ├── phase-01/
│   └── phase-04/
└── decisions/
    └── README.md

src/
└── genshin_corpus/
    ├── collector/
    ├── parser/
    ├── canonical/
    ├── retrieval/
    ├── generation/
    ├── rag/
    └── ui/

data/
├── README.md
└── raw/

tests/
├── collector/
├── parser/
├── canonical/
├── retrieval/
├── generation/
├── ui/
└── fixtures/
    └── mihoyo_obc/
```

The `canonical/` source package contains the completed Phase 03 contracts,
OBC structural projector, and Canonical run pipeline. `retrieval/` contains
the Phase 04 Retrieval Unit, candidate retrieval, Evidence Assembly,
materialization, comparison, and Dense challenger/comparison tooling.
`generation/`
contains provider-neutral Generation contracts and the scoped Bailian/Qwen
control path. `data/canonical/` and `data/retrieval/` are ignored local
generated-data roots; the latter is not a serving-system contract.

The local Streamlit frontend is an optional presentation layer over the
single-question RAG backend. Install `requirements-ui.txt`, expose `src` on
`PYTHONPATH`, and run:

```powershell
python -m pip install -r requirements-ui.txt
$env:PYTHONPATH = (Resolve-Path src)
python -m streamlit run src/genshin_corpus/ui/streamlit_app.py
```

The app reads provider credentials from the existing runtime environment only
when a question is submitted. `evidence_only` requires the Qwen query
embedding environment but does not inspect Generation credentials.

## Data and secrets

The full Raw corpus is local generated data and is ignored by Git. Small sanitized fixtures required for tests may be committed under `tests/fixtures/`.

Browser `Copy as cURL` output can contain cookies or other credentials. Keep unredacted captures under `.local/` (ignored by Git); only sanitized facts should be copied into tracked research notes.
