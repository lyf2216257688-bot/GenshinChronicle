# Genshin Official Text Research Infrastructure

This repository is the engineering workspace for a research-oriented corpus of official Genshin Impact text.

The long-term direction is:

```text
Official sources
    -> Raw
    -> Clean
    -> Chunking
    -> Indexing
    -> Retrieval
    -> Reranking
    -> Generation
```

The project intentionally separates:

1. how an official source/API organizes data;
2. how game content is structurally represented;
3. how research or AI later interprets that content.

Source evidence, structural organization and semantic interpretation retain
distinct responsibilities throughout this pipeline.

## Current status

Phases 01 — Raw Collection, 02 — Parsing, and 03 — Canonical Corpus are
**CLOSED**. Clean V1 and Chunk V2.1 full artifacts provide the new-chain input
baseline. **Indexing work unit 1 owns unified input and field design**
(2026-11-09), specified in the [input contract](docs/phases/indexing-input-v1.md).
Current authorization and adoption belong to [current-phase](docs/current-phase.md).
The standalone [index experiment launcher](docs/phases/index-benchmark-v1.md)
provides serial local Dense/BM25F construction, recovery and independent search.
The selected BGE-M3/BM25F artifacts are reused by the opt-in
[Clean Retrieval P0–P2](docs/phases/clean-retrieval-v1.md) work. Its candidate
interface and bounded Sparse trial do not switch Serving or establish RAG quality.
The [P3 Sparse runbook](docs/research/sparse-build-preparation-20261112.md) provides
separate Preflight/Run/Resume/Status/Validate commands for the operator's manual
full build; it does not automatically start encoding or change Serving.
The [P4 recall experiment](docs/phases/recall-experiment-v1.md) reuses the completed
indexes for operator-run S0/S1/S2 comparisons, resumable raw hits and source-text
reports. Preflight and a separate one-question Smoke precede manual70Q Run.
The [Python 3.14 migration report](docs/research/python314-migration-20261113.md)
records validated active runtimes, the explicit immutable-index compatibility
gate and completed old-runtime cleanup. Clean Reranking completed the saved70Q
BGE/GTE comparison and structural/MMR review. The selected ordering is BGE +
conservative structural processing, MMR off. Its complete candidate handoff and
explicit relevance-only fallback are described in the
[P4 handoff report](docs/research/clean-reranking-p4-handoff-20261113.md).
This is not online RAG adoption or Generation integration.
The direct Raw adapter, evidence locators, resumable builder and readers remain
specified in the [Clean contract](docs/phases/clean-v1.md).
The retained Phase04 implementation extends the historical adopted Block A over the accepted
16,437-record OBC `zh-cn` Canonical snapshot. It includes
Retrieval Units, BM25, local Qwen3 Dense, RRF Hybrid, deterministic Evidence
Assembly, Evidence Packets, and provider-neutral Generation contracts.
Phase 05 — NaviRAG/Card/Link/KG is **PAUSED**, with code, research and model
evidence preserved. Future semantic work depends on measured basic-RAG needs.

V1 uses the operator-selected DeepSeek default through `GenerationProvider`,
with explicit Bailian/Qwen rollback. Its limited live gate accepted two cases
and rejected one for an unresolved output-cap mismatch; no broad QA acceptance
or new paid authorization follows. The old Qwen query/index selectors still
exist in code, but their local assets were retired. They are not working local
rollback. Backend/Streamlit adoption of the Clean-based pipeline remains separate.

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
│   ├── clean-v1.md
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
    ├── clean/
    ├── retrieval/
    ├── generation/
    ├── rag/
    └── ui/

data/
├── README.md
├── raw/
├── parsed/
├── canonical/
├── clean/
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

## Opt-in Clean candidate retrieval

`python -m genshin_corpus.retrieval.clean_based` is the independent candidate
entry point. The [P0–P2 report](docs/research/clean-retrieval-p0p2-20261111.md)
provides single-query usage, finite source-text results and measured limitations.
It does not invoke Generation or switch the retained backend/Streamlit selectors.

## Historical Phase04 CLI/Streamlit checkpoint

The instructions below describe the retained implementation before local Qwen
asset retirement. They are not run instructions for the current workspace and
do not provide working local rollback. Use current-phase for adoption status.
The later Python migration removed the old3.12 environments named in these
historical examples; do not recreate them to restart retired tasks.

The retained Streamlit frontend is an optional presentation layer over the
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
