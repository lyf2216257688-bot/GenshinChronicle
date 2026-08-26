# Project Instructions

## Mission
Build a reliable, auditable research corpus for official Genshin Impact text.
The long-term pipeline is:

`Raw -> Parsed -> Canonical -> Retrieval/RAG`

AI Semantic / Knowledge layers are explicitly deferred until real corpus data and RAG results justify them.

## Active scope
The active phase is **Phase 01 — Raw Collection**.
Before changing code, read:

1. `docs/current-phase.md`
2. `docs/phases/phase-01-raw-collection.md`
3. Any relevant notes under `docs/research/phase-01/`

If a requested task conflicts with the active phase, stop and call out the conflict instead of silently expanding scope.

## Phase 01 objective
Collect the complete Chinese (`zh-cn`) raw API corpus exposed by MiHoYo OBC (`mihoyo_obc`) in a way that is repeatable, resumable, and auditable.

The collector must be source-oriented. Do not assume every entry is a quest or design the Raw layer around quest-specific fields.

## Hard constraints
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

## Explicit non-goals for Phase 01
Do **not** implement or design in detail:
- Parsed schema
- Canonical database/schema
- text cleanup or correction
- Passage segmentation
- embeddings
- Retrieval/RAG
- AI analysis
- knowledge graph / semantic layer
- quest/weapon/book/etc. content parsers

Future directories or abstractions should not be created merely because they appear in the long-term architecture.

## Engineering behavior
- Prefer small, reviewable changes with clear acceptance criteria.
- Separate observed facts from assumptions and historical leads.
- Fail loudly on unknown critical structures; do not silently discard data.
- Tests should use small checked-in fixtures, never depend on the full local Raw corpus.
- Generated datasets belong under `data/` and are not source code.
- Before adding dependencies or infrastructure, justify why Phase 01 needs them.
- Update `docs/current-phase.md` only when project status actually changes.
