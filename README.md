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

Active work is **Phase 02 — Parsing**.

Phase 01 Raw collection is closed. The current goal is to build a deterministic,
source-specific Parsed layer over the completed Raw corpus while preserving
structure, provenance, and Raw traceability. Canonical and Retrieval/RAG work
remain outside the current phase.

Start here:

- `AGENTS.md` — repository-wide working rules for Codex
- `docs/current-phase.md` — current status and immediate next action
- `docs/phases/phase-01-raw-collection.md` — Phase 01 scope and acceptance criteria
- `docs/architecture-overview.md` — long-term architecture, intentionally high-level
- `docs/roadmap.md` — tentative future phases

## Repository layout

```text
AGENTS.md
README.md

docs/
├── architecture-overview.md
├── current-phase.md
├── roadmap.md
├── phases/
│   ├── phase-01-raw-collection.md
│   └── phase-02-parsed-schema.md
├── research/
│   └── phase-01/
│       ├── mihoyo-obc-api-discovery.md
│       └── channel-tree-notes.md
└── decisions/
    └── README.md

src/
└── genshin_corpus/
    └── collector/

data/
├── README.md
└── raw/

tests/
├── collector/
└── fixtures/
    └── mihoyo_obc/
```

`canonical/` and `retrieval/` source packages remain deliberately absent.
The `parser/` package is introduced in Phase 02 and currently contains only
the contract foundation; source-specific handlers are added incrementally.

## Data and secrets

The full Raw corpus is local generated data and is ignored by Git. Small sanitized fixtures required for tests may be committed under `tests/fixtures/`.

Browser `Copy as cURL` output can contain cookies or other credentials. Keep unredacted captures under `.local/` (ignored by Git); only sanitized facts should be copied into tracked research notes.
