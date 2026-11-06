# Project Structure

`src/genshin_corpus/clean/` owns the direct Raw-to-Clean adapter, shared origin
pool, HTML/table/dialogue projection, field-shape rules, resumable file builder,
selectors, source resolution and on-demand rendering. Its contract is
[Clean V1](phases/clean-v1.md). Existing serving packages below keep their
current responsibilities until explicit replacement adoption.

The active delivery path is the existing Phase 04 `retrieval/` -> `rag/backend.py`
-> `generation/` composition with the thin `ui/` presentation. Phase 05 NaviRAG,
Card, Link and KG development is paused per [`current-phase.md`](current-phase.md);
the packages and evidence described below remain preserved. Reusing the local
Qwen3 encoder/runtime for original-RU V1 migration does not make the paused
semantic pipelines a product dependency or require moving their packages.

Shared execution primitives: `src/genshin_corpus/model_calls.py` owns serial
model-call attempts, budgets, retry/resume and channel cooldown;
`src/genshin_corpus/evidence_io.py` owns reused create-only evidence IO.
`retrieval/navi_offline/common.py` re-exports that IO for compatibility.
Source/Schema/semantic validation remains in its existing domain modules.
Integration and legacy exceptions: `docs/model-interfaces.md`.

The opt-in `src/genshin_corpus/retrieval/navi_offline/` package owns the standalone
offline Navi source catalog, typed quotations, adaptive Cards, sharded RU/Card
indexes, raw-first request adapters, pipeline CLI and inactive human-review
records. It reuses existing projection, local embedding and frozen v0.3 modules;
it does not replace old experiment runners, production retrieval or `rag/`.
`embedding_worker.py` runs in the already-qualified local ML environment while
the CLI and SDK remain in the orchestration environment. `testing.py` provides
explicitly foreign MOCK vectors only. See the
[environment report](research/phase-05/p05-navi-offline-environment-20261029.md).

Within the same package, `cards_v2.py` owns attributed narrative reading,
section/document synthesis and the labeled story projection; `card_build.py`
owns Card-only batches, immutable source revisions, reuse and targeted updates;
`card_census.py` estimates whole-corpus work through the approved RU projector.
`anchors.py` owns versioned lossless sentence/paragraph position maps, program
extraction and inherited anchor identities; the legacy exact-quote locator remains.
These are specialized
offline functions, not a general agent framework or a new authoritative layer.
`response_records.py` preserves provider counters and independent strict
stream/content outcomes plus separated reasoning/final text. Card-only Metro
requests reuse the raw-first adapter with explicit route identity and ledger;
historical request paths and frozen v0.3 are not rewritten.
`metro_parameter_drafts.py` is an isolated offline wire/non-stream parsing
prototype for parameter studies, with no live dispatch or default adoption.
See the [Card readiness report](research/phase-05/p05-card-production-readiness-20261029.md).

This document describes directory responsibilities. Current project status and
the immediate next action are owned by `docs/current-phase.md`.

## Repository root

- `AGENTS.md`: repository-wide engineering and phase-boundary instructions.
- `README.md`: project orientation and high-level repository entry point.
- `docs/`: architecture, phase specifications, research evidence, AI model interfaces catalog, and decisions. `docs/model-interfaces.md` owns unified AI model interface documentation, provider endpoints, credential slots, and maintenance instructions.
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
- `src/genshin_corpus/clean/`: deterministic Clean contracts, direct OBC
  extraction, source locators, run storage and downstream reading interfaces.
- `src/genshin_corpus/retrieval/`: Phase 04 Retrieval Units and Phase 05
  source-bound semantic-build/view primitives; lexical, local Dense, and
  deterministic candidate retrieval; reranking adapters/primitives
  and rank fusion; combined retained-evidence Assembly with explicit Formal rollback; and rebuildable local
  materialization, comparison, parity, and bounded research tooling.
  `navi_exploration.py` owns the local E1 search/source-read tools and explicit
  review-to-topic projection. `navi_reading.py` owns the opt-in candidate V
  release catalog and source-layout body partitioning over verified artifacts.
  `navi_f1_prepare.py` owns provider-free fresh-sample deterministic selection,
  exact Canonical/RU packet binding, arm-input integrity and frozen-evidence replay;
  it separates mechanical PASS from sample-independence dispositions and has no
  model/embedding/publication seam.
  `navi_f1_freshness.py` owns bounded full-rich-text exact/near-duplicate screening
  with source locators; it does not classify semantic truth or certify story-family
  independence. F1 source roles and family dispositions remain explicit agent review.
  `navi_f2_preflight.py` owns isolated provider-free request construction, frozen
  native/rc1 response validation, non-streaming wire/envelope replay, raw-first
  create-only archival and parameterized budget arithmetic. It has no credential,
  network client, sending loop or production adoption seam.
  `navi_f2_c1.py` adds an isolated offline historical Responses stream projection,
  unchanged-input streaming draft renderer, empirical usage proxies and budget
  gate. It does not certify current provider capabilities or authorize dispatch.
  `navi_f2_live_probe.py` is a retired/no-send entrypoint; its two prior attempts
  remain historical evidence. `navi_f2_sdk_transport.py` owns the isolated F2
  SDK/httpx single-case stream adapter, raw-body/event separation, explicit proxy
  and fingerprint/resume checks. It does not start a campaign or authorize probes.
  `navi_f2_execute.py` executes the 48 frozen base slots in order under explicit F2
  authority and stops on transport/stream failures; original outputs remain inactive.
  `navi_f2_instruction_diagnostic.py` isolates two user-authorized Responses input/
  output-cap diagnostics and audits raw/SDK events and item identities separately.
  `navi_f2_json_mode_preflight.py` and `navi_f2_json_mode_30k_preflight.py` prepare
  separate unsent JSON-object plans. `navi_f2_json_mode_execute_v2.py` owns the
  isolated 30k plan's guarded executor and archived-content replay; its existence
  does not authorize dispatch or establish a completed batch.
  `navi_f2_continue_46.py` is the separately authorized continuation for frozen
  slots 3–48; it verifies earlier runs, permits bounded proven pre-header retries
  within one run, and never auto-resumes an existing root.
  `navi_g1_s1_audit.py` owns provider-free G1 evidence-packet preparation,
  validation/freezing of separately agent-authored judgments, historical
  comparison and input integrity. `navi_v02_contract.py` checks rc1 offline
  proposition provenance, reading receipts and original-text retention after
  schema validation. Both have no semantic classifier, provider or release seam.
  `navi_prompt_candidate.py` owns the separate v0.3-rc1 offline candidate:
  mode-specific model schemas, unsent Responses drafts, deterministic disposition/
  Pair summaries and the prospective story-admission policy. It reuses complete
  rc1 local invariants internally and has no dispatch, repair, publication or
  default-runner adoption seam. Its small checked-in F2 regression fixture records
  agent-authored judgments, not new model output or human semantic gold.
  separate `navi_v03_runner.py` owns opt-in single-request execution preparation,
  Canonical/RU-bound source snapshots, a persistent fingerprint reservation,
  raw-first SDK archival, strict full-stream/content replay and at most three
  independently proven pre-send attempts. Default CLI operation is offline;
  it never sends via an old F2 Prompt renderer or automatically resumes a run.
  The existence of an experiment runner does not authorize its execution, and an
  adapter's existence does not establish the current default. It owns no UI
  lifecycle or Generation provider contract, is not a serving system, and does
  not establish a permanent technology winner.
- `src/genshin_corpus/generation/`: provider-neutral Generation contracts,
  generation-visible Evidence Packet projection, result/citation-integrity
  persistence, and explicitly scoped provider adapters. It consumes an
  Evidence Packet but does not own Retrieval or Evidence Assembly policy.
  `deepseek.py` is the V1 default TokenMetro adapter; Bailian/Qwen remains explicit rollback. It implements
  final QA using the shared model-call layer, raw-first HTTP archives and the
  neutral renderer/citation checks. The existing runtime factory selects the
  provider explicitly; Retrieval/Assembly do not own provider selection.
- `src/genshin_corpus/rag/`: the small provider-neutral single-question RAG
  orchestration boundary. It owns prepared-state reuse and per-question
  composition across Retrieval, optional reranking, selectable combined/Formal Assembly,
  Evidence Packet handling, optional Generation, citation validation, and
  structured audit/persistence results. It also owns the opt-in provider-free
  bounded adaptive Evidence kernel and assessment contracts, plus the narrow
  opt-in targeted-gate assessor adapter/parser and review persistence seam;
  these remain evaluation mechanics only and are not the production/default
  path. Phase 05 navigation orchestration and route lineage live here; the
  E1 recorded exploration/checkpoints and the opt-in real-source mechanical
  smoke also live here; E1 contains no model transport or production Agent.
  `navi_e2_experiment.py` is the opt-in single-case model exploration runner;
  it reuses E1 tools and SDK evidence helpers without creating active views.
  `navi_v_experiment.py` owns provider-free candidate V scheduling, delivery
  checkpoints, coverage and strict continuous-understanding updates;
  `navi_v_research.py` is its bounded real-source recorded-decision replay entry;
  `navi_v_live_research.py` owns opt-in live research loop execution;
  `navi_v_expanded_research.py` owns multi-task progression and cross-task understanding runner.
  `navi_r1_experiment.py` owns the isolated, opt-in R1 profile/discovery comparison,
  immutable paid-attempt accounting and research-only analysis; it creates no
  active semantic view and does not change production retrieval or generation.
  `navi_r1_qa.py` owns the isolated follow-up candidate ledger, Canonical/RU
  snippet verification, method-blind source-ID review and offline quote/semantic
  review projections. It preserves R1 artifacts and creates no production view.
  `navi_g1_experiment.py` owns the opt-in G1 offline Card/query/Hybrid/Link
  loop, legacy reuse adapters, source-disjoint bounded validation, conservative
  grade recommendations and targeted model checks. It writes inactive research
  artifacts only; it is not an online RAG selector or full-corpus scheduler.
  `navi_e2_routes.py` owns explicit E2 model/route/credential-slot mappings;
  `model_interfaces_catalog.py` owns the programmatic catalog and consistency
  checks for active and historical AI model interfaces;
  `navi_e2_protocol_probe.py` owns bounded synthetic Responses/action canaries
  and their campaign request ceilings, without invoking corpus tools. Archived
  retrieval SDK execution paths are restricted to offline MockTransport;
  historical request construction and read-only replay remain available. The
  package does not redefine semantic candidate/admission or Assembly policies
  owned by `retrieval/`, and it does not redefine provider contracts owned by
  `generation/`.
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
- `tests/clean/`: direct extraction, source round trips, selectors and build
  recovery tests using bounded checked-in component excerpts.
- `tests/retrieval/`: focused Phase 04 profiler, retrieval, Evidence Assembly,
  materialization, comparison, and Qwen-tooling tests.
- `tests/generation/`: focused offline Generation contract and injected
  transport-adapter tests; no test calls a remote Generation API.
- `tests/ui/`: provider-free lifecycle, configuration-gating, path, and
  bounded-result tests for the Streamlit presentation layer.
- `tests/fixtures/`: small sanitized source samples used by automated tests.

## Data directories

The default local Qwen3 deployment uses persistent ignored resources in the
primary checkout: `.local/models/qwen3-embedding-0.6b/<revision>/`,
`.local/embedding-runtime/`, `.local/embedding-dependency-cache/` and
`.local/embedding-tests/`. The last directory owns this separately authorized
small verification index, measurements and installation evidence; it does not
replace the production `data/retrieval/` ownership or any historical index.
`retrieval/local_qwen3_embedding.py` owns the offline encoder/index binding;
`local_qwen3_build.py` adds bounded source streaming, transactional shard receipts,
OS locking, verified resume, token audit and final validation/publication.
`local_qwen3_query.py` runs the matching encoder in a short-lived isolated process
which exits before BGE. `scripts/retrieval/build-local-embedding.ps1` owns the
manual foreground Windows launcher; `rag/config.py` owns paired profile
selection and the preserved 2048d rollback. `rag/__main__.py` is the shared-runtime
single-question CLI. `rag/embedding_70q.py` runs the offline Packet comparison.
`local_qwen3_validation.py` retains
the historical bounded deployment CLI. The
environment lock is `requirements-local-qwen3-embedding.txt`.

- `data/raw/`: immutable local Raw evidence, including responses and auditable
  run metadata.
- `data/parsed/`: generated Parsed run records and manifests derived from Raw;
  ignored by Git.
- `data/clean/`: generated versioned Clean records, run manifests and bounded
  pilot evidence; ignored by Git.

`data/canonical/` is the ignored local output root for immutable Canonical run
records and manifests. `data/retrieval/` is the ignored local root for
authorized rebuildable Retrieval/RAG derivatives, Phase 05 semantic build/view
artifacts, and materialization outputs; its detailed responsibilities are owned
by `data/README.md`.

Card generation: `navi_offline/card_generation.py` owns versioned typed Chat prompts,
wire aliases and logged deterministic conversions; `metro_chat.py` owns raw-first
nonstream transport and independent protocol/content/accounting records. `card_build.py`
uses those strategies for bounded Card-only batches and separately sealed raw recovery.
New evidence-selection wire Schemas materialize into the existing Card v2 fields;
Card build also owns completed-source index selection and explicit skip reports.
`card_structure.py` owns versioned structure-aware reading and semantic scope identities;
`card_runtime.py` isolates hash-verified prepared runtimes for old-task resume.
These modules do not own Link standards or Canonical evidence.

`navi_offline/card_blind.py` owns opt-in source-identical Card comparison preparation,
fresh Antigravity CLI invocation, reuse of the existing Metro transport/ledger,
immutable per-source results, append-only code revisions/offline recovery, and
private anonymous-review assignment with explicit partial-failure cohorts. Named,
bounded independent dispatch in the historical blind root used a separately recorded authorization. New preparations use shared independent-task budgets; uncertain retries still require explicit cost-risk authorization. UNKNOWN usage and failed requests remain preserved. Its default
execution path is a provider-free preview. It does not own Card semantic standards,
provider production defaults, or OS-level isolation guarantees.

`navi_offline/card_contract.py` owns the explicit bounded exact-anchor Card wire
Schema and program-derived source bindings. `card_recovery.py` owns hash-bound
cross-version success imports and reachable-reading completion checks.
`scripts/retrieval/phase05_card66_repair.py` orchestrates only the fixed66 selection
with disjoint finite source shards; it uses shared model_calls through card_build
and introduces no transport or retry bypass.

`navi_offline/card_normalize.py` owns metadata-only review consolidation and actual
per-job generation contract annotations; source semantics and historical artifacts
remain unchanged. `scripts/retrieval/phase05_card66_continue.py` is the sealed finite
contract4 handoff runner; its existing implementation is not permission to restart
the operator-stopped experiment. Current source counts and the next authorized action
remain owned by current-phase and the scoped repair report.

`navi_offline/card_semantics.py` owns the opt-in evidence-judgment prompt,
source-context-first synthesis input, narrow audited narrative admission and
selected-range delivery summaries. It does not adjudicate semantic truth,
rewrite historical Cards or assign authoritative source provenance.
