# Project Instructions

## Mission
Build a reliable, auditable research corpus for official Genshin Impact text.
The long-term pipeline is:

`Raw -> Parsed -> Canonical -> Retrieval/RAG -> Source-Bound Semantic Navigation`

Derived layers must remain strictly grounded in verified source evidence. High-level
semantic exploration is permitted only within approved, bounded phase contracts;
unconstrained global claim extraction, ungrounded knowledge graphs, and treating
model interpretations as canonical facts remain prohibited.

## Active scope

`docs/current-phase.md` is the sole current-state documentation authority for the active phase,
current authorization, production/default behavior, and immediate next action.
This file owns durable repository-wide engineering and layer-boundary
instructions rather than duplicating current project status.

Before changing implementation or documentation, read:

1. `docs/current-phase.md`
2. The specification for the current active phase, if it exists
3. Relevant notes under `docs/research/`
4. `docs/architecture-overview.md` when the task affects cross-layer boundaries

If a requested task conflicts with the active phase, stop and call out the conflict instead of silently expanding scope.

## Layer boundaries

- **Parsed**: source-specific, deterministic, and auditable. It preserves
  meaningful source structure, ordering, hierarchy, Raw traceability, and
  unsupported/unknown content.
- **Canonical**: establishes a stable research document representation within
  the approved contract. It must preserve traceability through Parsed to Raw,
  keep source evidence separate from semantic normalization, and must not
  silently invent identity, provenance, content role, dialogue semantics, or
  semantic equivalence.
- **Derived Retrieval / RAG**: Retrieval Units and Evidence Packets derive
  citation and evidence identity from Canonical lineage, not embedding chunk
  identity. Evidence Packets are provider-neutral, deterministic evidence
  interfaces; Generation consumes them without owning retrieval or assembly policy.
  RAG answers are query results and must not automatically become Canonical facts.
- **Semantic Navigation**: derived views (such as hierarchy navigation and
  event/relation graphs) remain non-authoritative structures grounded strictly
  in verified Retrieval Units and Canonical lineage. Model quotes or claims do
  not constitute provenance; navigation traces are audit data and are not
  Generation-visible input. Model outputs or navigation traces must never
  contaminate Canonical evidence.

## Hard constraints
Raw/Collector constraints below remain binding whenever Raw acquisition or Collector behavior is touched, regardless of active phase.

- Raw API responses are immutable evidence: do not rewrite, normalize, prune, or overwrite them.
- Locale must remain configurable and must not be hard-coded; current production locale/source are owned by `docs/current-phase.md`.
- Discover from official structures: channel tree -> channel listings -> content inventory -> detail responses.
- Do not brute-force candidate content identifiers (including historically observed `content_id` values).
- Treat the stable retrievable content key as evidence-driven: it must come from a verified contract promoted into the phase specification (such as the verified `content_id` contract); do not treat that as a permanent assumption about future API versions.
- Within one crawl run/content inventory, if the same verified stable content key belongs to multiple channels, fetch its detail once and preserve all channel memberships. This is run-level deduplication only: later crawl runs must be allowed to observe/fetch the item again so source changes can be detected.
- Preserve discovery responses as Raw data, not only final detail responses.
- Support resumability, bounded retry, explicit failure records, and an auditable run manifest.
- Do not bypass access controls, anti-bot protections, 403/429 responses, or other site protections.
- API contracts must be evidence-driven. Never invent endpoints, required headers, or schemas from assumptions.
- Never commit cookies, authorization material, or unredacted browser cURL containing secrets. Put sensitive local samples under `.local/`.
- Evidence Packet convenience must not cause corruption of structure, provenance,
  traceability, or other information required by an upstream contract. It may
  perform deterministic query-time ranking, finite-budget admission, and
  candidate truncation only when the operating-point policy records the
  disposition in an auditable form; silent evidence destruction and invented semantics are
  not permitted.

## Out-of-scope and separately authorized layers

Existing implementations and active phase experiments do not constitute authority to expand adjacent layers. Unless explicitly authorized by the active phase specification and approved in `docs/current-phase.md`, do **not** prematurely implement or freeze:

- Global entity or alias normalization across distinct sources or snapshots
- Vector databases, ANN serving engines, or external serving infrastructures
- Arbitrary model switches, unverified prompt/provider expansions, or unauthorized retrieval operating points
- Unconstrained global claim extraction or ungrounded knowledge graphs
- Complex full-stack serving UI beyond repository presentation tools
- Autonomous unconstrained web agents or unmonitored crawler runs
- Promoting AI-generated assertions, interpretations, or RAG answers to Canonical status

Source and upstream evidence remain preserved and auditable by their respective owners; each derived layer follows its explicit, fail-closed contract. Unsupported or non-indexable material may be skipped only through explicit audited handling. Never guess unsupported semantics or silently discard material whose contract requires accounting.

Future directories, abstractions, or layers must not be created merely because they appear in the long-term architecture overview.

## Documentation maintenance

- One fact has one primary documentation owner. `docs/current-phase.md` owns
  concise present state and serves as the Project Documentation Router; phase
  specifications own durable phase contracts; and research notes own scoped
  experiment and review facts.
- When a cohesive work unit changes current phase/status, production/default
  behavior, authorization/adoption, durable architecture, repository layout or
  package responsibility, or data-root responsibility, identify and update
  the affected owners in that same work unit. Do not edit unrelated documents
  merely for symmetry.
- Historical research results are not rewritten to match later outcomes. If a
  historical current-looking statement becomes misleading after supersession,
  qualify its historical checkpoint and current relation instead of erasing the
  past. `docs/current-phase.md` is a current-state record, not an append-only
  chronology.
- `docs/current-phase.md` is the sole current-state documentation authority;
  committed source and configuration define executable behavior, and tests
  verify that behavior and its contracts. If executable behavior and current
  documentation materially disagree, fail closed on the affected conclusion
  and surface the inconsistency.
- Maintain README, roadmap, architecture overview, project structure, and the
  data README only when their owned facts change. Change this file only for
  durable repository-wide rules; use ADRs only for costly or hard-to-reverse
  durable decisions. Repository documentation work does not itself authorize
  changes to external migration, history, or collaboration documents.

For relevant state-changing work, before declaring completion, search the
affected tracked documentation for stale unqualified references to `current`,
`next`, `immediate next`, `NOT AUTHORIZED`, `NOT STARTED`, accepted/current
selectors, production/default behavior, current provider/model, and package or
directory absence/implementation status. A historical statement may remain
only when its scope is clear enough that it cannot override current authority.

## Windows command guidance

- When a task requires commands for the user to run manually on Windows, provide PowerShell 7 (`pwsh`)-compatible commands by default. Use Windows PowerShell 5.1 only for an explicit legacy-compatibility requirement. This applies only to commands given to the user; Codex must use the shell available in its actual runtime and must not assume PowerShell 7 is installed there. Do not include usernames, absolute PowerShell installation paths, or other machine-specific paths in repository content.

## Engineering behavior
- Prefer small, reviewable changes with clear acceptance criteria.
- Separate observed facts from assumptions and historical leads.
- Fail loudly on unknown critical structures; do not silently discard data.
- Tests should use small checked-in fixtures, never depend on the full local Raw corpus.
- Generated datasets belong under `data/` and are not source code.
- Before adding dependencies or infrastructure, justify why the active phase and current evidence require them.
- Update the relevant documentation owners when their owned facts actually
  change, following the documentation-maintenance rule above.
