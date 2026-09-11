# Project Instructions

## Mission
Build a reliable, auditable research corpus for official Genshin Impact text.
The long-term pipeline is:

`Raw -> Parsed -> Canonical -> Retrieval/RAG`

AI Semantic / Knowledge layers are explicitly deferred until real corpus data and RAG results justify them.

## Active scope

`docs/current-phase.md` is the sole source of truth for the active phase,
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

Parsed remains source-specific, deterministic, and auditable. It preserves
meaningful source structure, ordering, hierarchy, Raw traceability, and
unsupported/unknown content.

Canonical may establish a stable research document representation only within
the approved current-phase contract. It must preserve traceability through
Parsed to Raw, keep source evidence separate from semantic normalization, and
must not silently invent identity, provenance, content role, dialogue
semantics, or semantic equivalence.

## Hard constraints
Raw/Collector constraints below remain binding whenever Raw acquisition or Collector behavior is touched, even though Phase 01 is closed.

- Raw API responses are immutable evidence: do not rewrite, normalize, prune, or overwrite them.
- `zh-cn` is the current target, but locale must remain a configuration value rather than a permanent single-language assumption.
- Discover from official structures: channel tree -> channel listings -> content inventory -> detail responses.
- Do not brute-force candidate content identifiers (including historically observed `content_id` values).
- Treat the stable retrievable content key as evidence-driven: it must come from a contract verified and promoted into the current Phase specification. Phase 01 currently uses the verified `content_id` contract; do not treat that as a permanent assumption about future API versions.
- Within one crawl run/content inventory, if the same verified stable content key belongs to multiple channels, fetch its detail once and preserve all channel memberships. This is run-level deduplication only: later crawl runs must be allowed to observe/fetch the item again so source changes can be detected.
- Preserve discovery responses as Raw data, not only final detail responses.
- Support resumability, bounded retry, explicit failure records, and an auditable run manifest.
- Do not bypass access controls, anti-bot protections, 403/429 responses, or other site protections.
- API contracts must be evidence-driven. Never invent endpoints, required headers, or schemas from assumptions.
- Never commit cookies, authorization material, or unredacted browser cURL containing secrets. Put sensitive local samples under `.local/`.
- Evidence Packet convenience must not cause loss of structure, provenance, traceability, recall, ranking, context quality, or other information needed for final RAG quality.

## Deferred and separately authorized layers

Phase 04 Retrieval, Evidence Assembly, and provider-neutral Generation
contracts exist. Do not treat that implementation as authority to freeze or
expand adjacent layers. Unless the current phase explicitly authorizes them,
do **not** prematurely implement or freeze:

- Canonical entity or alias normalization
- a production Dense model switch or new embedding operating point
- vector databases, ANN, or rerankers
- Retrieval/RAG reruns, optimization, or prompt/provider expansion
- AI semantic / claim extraction
- knowledge graph / semantic layer
- UI

Unknown or unsupported structures must be preserved rather than guessed or
silently discarded in every derived layer.

Future directories or abstractions should not be created merely because they appear in the long-term architecture.

## Documentation maintenance

- One fact has one primary documentation owner. `docs/current-phase.md` owns
  concise present state; phase specifications own durable phase contracts; and
  research notes own scoped experiment and review facts.
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

## Engineering behavior
- Prefer small, reviewable changes with clear acceptance criteria.
- Separate observed facts from assumptions and historical leads.
- Fail loudly on unknown critical structures; do not silently discard data.
- Tests should use small checked-in fixtures, never depend on the full local Raw corpus.
- Generated datasets belong under `data/` and are not source code.
- Before adding dependencies or infrastructure, justify why the active phase and current evidence require them.
- Update the relevant documentation owners when their owned facts actually
  change, following the documentation-maintenance rule above.
