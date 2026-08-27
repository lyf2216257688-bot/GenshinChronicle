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

Phase 02 — Parsing is **CLOSED**. Phase 03 — Canonical Corpus implementation is
authorized, and the immediate next action is **Batch 1 — Canonical Contract
Foundation**.

Phase 01 Raw collection and the Phase 02 deterministic, source-specific Parsed
foundation are closed. The approved Phase 03 contract permits only the current
reviewed implementation batch; the OBC projector, Canonical materialization,
and Retrieval/RAG remain outside that batch.

Start here:

- `AGENTS.md` — repository-wide working rules for Codex
- `docs/current-phase.md` — current status and immediate next action
- `docs/phases/phase-01-raw-collection.md` — Phase 01 scope and acceptance criteria
- `docs/phases/phase-03-canonical-schema.md` — approved Canonical contract and gates
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
│   ├── phase-02-parsed-schema.md
│   └── phase-03-canonical-schema.md
├── research/
│   └── phase-01/
│       ├── mihoyo-obc-api-discovery.md
│       └── channel-tree-notes.md
└── decisions/
    └── README.md

src/
└── genshin_corpus/
    ├── collector/
    └── parser/

data/
├── README.md
└── raw/

tests/
├── collector/
├── parser/
└── fixtures/
    └── mihoyo_obc/
```

`canonical/` and `retrieval/` source packages remain deliberately absent.
Batch 1 authorization does not itself create future packages or data
directories. The `parser/` package contains the closed Phase 02 contracts,
source-specific OBC adapter, and Parsed run pipeline.

## Data and secrets

The full Raw corpus is local generated data and is ignored by Git. Small sanitized fixtures required for tests may be committed under `tests/fixtures/`.

Browser `Copy as cURL` output can contain cookies or other credentials. Keep unredacted captures under `.local/` (ignored by Git); only sanitized facts should be copied into tracked research notes.
