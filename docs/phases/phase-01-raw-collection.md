# Phase 01 — Raw Collection

## Goal

Create a **complete, repeatable, resumable, auditable** Raw dataset of the Chinese (`zh-cn`) API data required to discover and retrieve content from MiHoYo OBC (`mihoyo_obc`).

"Complete" in Phase 01 means complete official API JSON needed for content discovery and text/detail retrieval. It does **not** currently mean downloading every image, video, or audio binary. Asset URLs present in Raw JSON must remain preserved.

## Non-goals

Phase 01 does not:
- parse content into quests/books/weapons/etc.;
- clean or correct official wording;
- design the final Canonical schema;
- split passages;
- create embeddings or RAG;
- run AI analysis;
- build a knowledge graph.

## Collection invariants

1. Save received Raw responses without semantic rewriting or field deletion.
2. Never overwrite old Raw evidence merely because a later crawl exists.
3. `locale` is configurable; `zh-cn` is the current run target.
4. Discovery is driven by official structures, never by brute-force enumeration of candidate content identifiers (including historically observed `content_id` values).
5. The stable retrievable content key is not assumed in advance. Historical work suggests `content_id`, but the current API contract must be reverified in 01A–01C before implementation depends on it.
6. Within one crawl run/content inventory, fetch one detail payload per unique verified stable content key and preserve all observed channel memberships separately. This deduplication is scoped to that run; a later crawl run may fetch the same item again to detect source changes.
7. Preserve channel-tree and listing responses, not only final entry details.
8. Retries are bounded and failures remain visible.
9. Respect 403/429/access restrictions; do not bypass protections.
10. API behavior is verified from current captures before coding assumptions into the collector.
11. Every full run produces a manifest/coverage summary that can demonstrate per-channel listing/pagination completeness, not only global totals.

## Workflow

### 01A — Channel tree discovery

Current step.

Capture fresh evidence for:

```text
getChannelTree?app_sn=ys_obc
```

Collect:
- complete unchanged Response;
- `Copy as cURL` for request reconstruction.

Analyze and record at least:
- `channel_id`;
- parent relationship;
- name;
- depth;
- leaf/non-leaf behavior;
- which channels appear enumerable;
- unknown fields/structures.

Do not write the general collector yet.

### 01B — Representative channel listing samples

After the current channel tree is understood, select roughly 5–10 structurally representative channels rather than copying every channel manually.

For each selected channel, verify the actual listing request/response and record:
- request shape;
- pagination behavior, if any;
- listing schema;
- entry count signals;
- whether entries expose `content_id` or an equivalent stable key;
- duplicates / cross-channel membership behavior;
- anomalies.

Output: a verified Channel Inventory specification.

Historical endpoint patterns may be used only as leads; they must be reverified against fresh captures.

### 01C — Representative detail samples

For representative content types/channels, inspect 1–3 IDs each.

Verify whether detail retrieval is shared across types or differs. Record:
- endpoint and method;
- required query parameters;
- required request headers;
- top-level response schema;
- stable content identity fields;
- error behavior;
- notable differences between channels/types.

Do not infer that quests, weapons, books, characters, etc. share one schema until observed.

### 01D — Implement Collector v0.1

Only after 01A–01C provide enough current evidence.

Target flow:

```text
channel tree
    -> discover channels
    -> fetch channel listings
    -> build complete content inventory
    -> deduplicate verified stable content keys within this crawl run
    -> fetch each unique detail payload once for this crawl run
    -> persist Raw responses + observations
    -> generate manifest
```

The collector downloads and records. It does not interpret narrative content.

Required engineering behavior:
- resumable runs;
- idempotent/repeatable operation;
- bounded retry with backoff appropriate to observed behavior;
- explicit failure log/records;
- deterministic storage paths/naming;
- content hashes where useful for integrity/change detection;
- channel-to-content membership preservation;
- per-channel pagination/coverage accounting sufficient to prove why enumeration is complete;
- safe handling of partial runs;
- auditable timestamps and run metadata.

### 01E — Staged validation before full crawl

Scale progressively:

1. channel tree only -> inspect;
2. all channel listings only -> build/inspect inventory;
3. stratified/random ~20 detail payloads -> inspect Raw storage;
4. ~200 details -> inspect error rate, throttling, resume behavior;
5. full site crawl.

Do not jump directly to a full crawl if an earlier gate fails.

### 01F — Profile Raw before designing Parsed

After the full crawl, **do not immediately implement parsing**.

First produce corpus-level counts such as:
- channel count;
- listing-record count;
- unique content-ID count;
- detail successes/failures;
- duplicate channel memberships;
- unknown listing structures.

Before schema profiling, verify the full-run coverage report can account for every discovered enumerable channel, including pagination termination/completeness and any unresolved gaps.

Then statistically profile the actual detail corpus. Candidate measurements include:
- observed `component_id` values and frequencies;
- `template_layout` shapes;
- pages without `modules`;
- common field combinations;
- structural differences by channel;
- response-size distribution;
- module-count distribution;
- component-type combinations;
- rare/unknown structures.

Choose samples deliberately:
- representative sample of each observed structure;
- anomalies;
- largest pages;
- smallest pages;
- rare components.

Only then begin Phase 02 design.

## Full-crawl acceptance report

A successful Phase 01 full run must be able to report at minimum:

```text
source system
locale
crawl/run identifier
start/end timestamps
channel count
enumerable channels expected / completed / incomplete
per-channel listing-record counts
per-channel pagination pages/termination status
listing responses saved
listing records discovered
verified stable content-key field used for this run
unique content keys
unique detail responses expected
successful detail fetches
failed detail fetches
cross-channel duplicate memberships
unknown/unhandled discovery structures
channels with unresolved coverage gaps
HTTP/API error summary
resume/retry summary
manifest path/hash
```

## Security and local evidence

Browser cURL captures may contain cookies or credentials.

- Store raw local captures under `.local/`.
- Do not commit secrets.
- Research notes should contain only sanitized headers/parameters and verified facts.
- Never replace secret values with plausible fake values that could later be mistaken for real requirements; use `<REDACTED>` or `<REQUIRED_VALUE_FROM_BROWSER>`.
