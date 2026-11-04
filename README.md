# Genshin Official Text Research Infrastructure

This repository is the engineering workspace for a research-oriented corpus of official Genshin Impact text.

The long-term direction is:

```text
Official sources
    -> Raw
    -> Parsed
    -> Canonical
    -> Retrieval / RAG
    -> Source-Bound Semantic Navigation
```

The project intentionally separates:

1. how an official source/API organizes data;
2. how game content is structurally represented;
3. how research or AI later interprets that content.

Those are different layers and must not be collapsed into one schema.

## Current status

Phases 01 — Raw Collection, 02 — Parsing, and 03 — Canonical Corpus are
**CLOSED**. **Phase 04 basic RAG V1 is the active development priority**
(2026-11-02), extending the previously adopted Block A over the accepted
16,437-record OBC `zh-cn` Canonical snapshot. It includes
Retrieval Units, BM25, local Qwen3 Dense, RRF Hybrid, deterministic Evidence
Assembly, Evidence Packets, and provider-neutral Generation contracts.
Phase 05 — NaviRAG/Card/Link/KG is **PAUSED**, with code, research and model
evidence preserved. Future semantic work depends on measured basic-RAG needs.

V1 uses the operator-selected DeepSeek default through `GenerationProvider`,
with explicit Bailian/Qwen rollback. Its limited live gate accepted two cases
and rejected one for an unresolved output-cap mismatch; no broad QA acceptance
or new paid authorization follows. The operator selected the completed local
`Qwen3-Embedding-0.6B` / 1024d query/index pair as the default. The historical
online-Qwen 2048d pair remains an explicit rollback and comparison option.
BM25, Dense/Hybrid, BGE and
Evidence Packets are retained. The existing backend and Streamlit UI are reused.

Read `docs/current-phase.md` for the sole current production/default Dense,
historical challenger, reranker, attribution, authorization, and immediate-next
status.

Start here:

- `AGENTS.md` — repository-wide working rules for Codex
- `docs/current-phase.md` — current status and immediate next action
- [Phase 04 V1 delivery contract](docs/phases/phase-04-retrieval-evidence-assembly.md#basic-rag-v1-delivery-contract) — ordered work, migration/rollback and acceptance gates
- `docs/phases/phase-04-first-end-to-end-rag-amendment-v1.md` — Phase 04 RAG v1 contract and historical plan
- `docs/research/phase-04/p04-rag-historical-checkpoint-register.md` — compact Phase 04 checkpoint index and evidence pointers
- `docs/architecture-overview.md` — long-term architecture, intentionally high-level
- `docs/roadmap.md` — phase-level direction and status
- `docs/phases/phase-05-source-bound-semantic-navigation.md` — paused Phase 05 contracts and preserved research boundaries
- [Offline Navi environment and CLI](docs/research/phase-05/p05-navi-offline-environment-20261029.md) — preserved inactive research workflow; not a V1 prerequisite

## Repository layout

The following is an abridged/key layout; consult `docs/project-structure.md`
for stable directory responsibilities.

```text
AGENTS.md
README.md

docs/
├── architecture-overview.md
├── current-phase.md
├── model-interfaces.md
├── roadmap.md
├── phases/
│   ├── phase-01-raw-collection.md
│   ├── phase-02-parsed-schema.md
│   ├── phase-03-canonical-schema.md
│   ├── phase-04-*.md
│   └── phase-05-source-bound-semantic-navigation.md
├── research/
│   ├── phase-01/
│   ├── phase-03/
│   ├── phase-04/
│   └── phase-05/
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
├── raw/
├── parsed/
├── canonical/
└── retrieval/

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
contains provider-neutral Generation contracts, V1-default DeepSeek and the
scoped Bailian/Qwen control path. `data/canonical/` and `data/retrieval/` are ignored local
generated-data roots; the latter is not a serving-system contract.

The local Streamlit frontend is an optional presentation layer over the
single-question RAG backend. It shares the **local Qwen3/1024d default**
and explicit online rollback with the CLI. The default local-BGE path uses
the validated Python 3.12 runtime at
`.\.local\gte-reranker-runtime\Scripts\python.exe`, which
contains Streamlit `1.63.0`. Expose `src` on `PYTHONPATH`, provide the pinned
local BGE snapshot, leave the reranker selector unset, and run:

```powershell
$runtime = '.\.local\gte-reranker-runtime\Scripts\python.exe'
$env:PYTHONPATH = (Resolve-Path src)
$env:GENSHIN_BGE_RERANKER_MODEL_ROOT = (Resolve-Path '.\.local\models\bge-reranker-v2-m3\953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e')
Remove-Item Env:GENSHIN_RERANKER_BACKEND -ErrorAction SilentlyContinue
Remove-Item Env:GENSHIN_EMBEDDING_PROFILE,Env:GENSHIN_LOCAL_DENSE_MANIFEST -ErrorAction SilentlyContinue
& $runtime -m streamlit run src/genshin_corpus/ui/streamlit_app.py
```

With the same setup, run a single retrieval-only query from PowerShell with
`& $runtime -m genshin_corpus.rag '前任大贤者阿扎尔出身于哪个学派？'`.
The CLI prints the result and artifact paths. Its `--generate-answer` flag
enables the configured Generation path.

Default `evidence_only` uses local Embedding and BGE without provider credentials.
Set `GENSHIN_EMBEDDING_PROFILE=online_qwen_2048` for explicit rollback. New
questions on that profile use the configured online query service and can
incur charges. The [70Q comparison](docs/research/phase-04/p04-embedding-70q-20261103.md)
reuses accepted 2048d query vectors offline. The online
Qwen reranker is not part of the default path; select it explicitly with
`GENSHIN_RERANKER_BACKEND=online_qwen` when its credentials are configured.
For the complete AI model catalog, provider endpoints, credential environment
variables, and protocol specifications, see `docs/model-interfaces.md`.

Final-answer selection uses `GENSHIN_GENERATION_BACKEND=deepseek` (also the
unset default) or explicit `bailian`; `evidence_only` reads neither Generation profile.
The DeepSeek path uses the same question/Packet renderer and citation checker,
not Cards or Links. It requires a new explicitly authorized finite campaign;
configuration, raw archives, replay and closed live-test limits are
documented in [model interfaces](docs/model-interfaces.md#phase-04-v1-default-deepseek-final-qa).
Returning to `bailian` preserves the original control without changing indexes.

## Data and secrets

The full Raw corpus is local generated data and is ignored by Git. Small sanitized fixtures required for tests may be committed under `tests/fixtures/`.

Browser `Copy as cURL` output can contain cookies or other credentials. Keep unredacted captures under `.local/` (ignored by Git); only sanitized facts should be copied into tracked research notes.

### Evidence Assembly selector

The backend, CLI and Streamlit default to the adopted `combined` Assembly,
which reuses proved visible aliases, conservative HTML display projection and
whole-RU refill. To select the original Formal Deferred Assembly in PowerShell 7,
set the shared runtime selector before launching the CLI or Streamlit.

```powershell
$env:GENSHIN_ASSEMBLY_BACKEND = 'formal'
```

Use `combined` or remove this variable to restore the default. Direct backend
callers select `SingleQuestionBackendConfig(assembly_backend="formal")` for
rollback. The frozen Embedding 70Q comparison runner explicitly pins Formal.
The [Assembly report](docs/research/phase-04/p04-assembly-retention-70q-20261104.md)
records evaluation limits and the operator's adoption.
