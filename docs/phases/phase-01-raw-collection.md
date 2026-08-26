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
5. The stable retrievable content key is evidence-driven. For the current reviewed capture, `content_id` is verified and promoted as the Phase 01 contract; future API versions must be reverified rather than assumed to preserve it.
6. Within one crawl run/content inventory, fetch one detail payload per unique verified stable content key and preserve all observed channel memberships separately. This deduplication is scoped to that run; a later crawl run may fetch the same item again to detect source changes.
7. Preserve channel-tree and listing responses, not only final entry details.
8. Retries are bounded and failures remain visible.
9. Respect 403/429/access restrictions; do not bypass protections.
10. API behavior is verified from current captures before coding assumptions into the collector.
11. Every full run produces a manifest/coverage summary that can demonstrate per-channel listing/pagination completeness, not only global totals.

## Workflow

### 01A — Channel tree discovery (complete)

Completed against the reviewed current capture. The promoted source contract uses `GET /common/blackboard/ys_obc/v1/home/map?app_sn=ys_obc`. Observed structures include shortcut, hidden, and special-function nodes; depth is not limited to 2, and an embedded map `list` is not a complete inventory.

The reviewed capture used:

```text
GET /common/blackboard/ys_obc/v1/home/map?app_sn=ys_obc
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

Do not treat the observed counts or node shapes as permanent API constants.

### 01B — Representative channel listing samples (complete)

Completed for representative channels 43, 25, 233, 129, 267, and 81. The promoted listing contract is `GET /common/blackboard/ys_obc/v1/home/content/list?app_sn=ys_obc&channel_id=<channel_id>`; detailed evidence remains in the research note.

For each selected channel, verify the actual listing request/response and record:
- request shape;
- pagination behavior, if any;
- listing schema;
- entry count signals;
- whether entries expose `content_id` or an equivalent stable key;
- duplicates / cross-channel membership behavior;
- anomalies.

Output: a verified Channel Inventory specification.

Detailed request/response evidence is maintained in `docs/research/phase-01/mihoyo-obc-api-discovery.md`.

### 01C — Representative detail samples (complete)

Completed with character 501157, quest 509653, and video 509109. The promoted detail contract is `GET /hoyowiki/genshin/wapi/entry_page?app_sn=ys_obc&entry_page_id=<content_id>&lang=zh-cn`. `content_id` is the currently verified directory/detail key; one key may have multiple channel memberships, which must all be preserved while deduplicating detail fetches within one run.

The reviewed samples confirm the endpoint across these content types. Keep schema differences evidence-driven. Record:
- endpoint and method;
- required query parameters;
- required request headers;
- top-level response schema;
- stable content identity fields;
- error behavior;
- notable differences between channels/types.

Do not infer that quests, weapons, books, characters, etc. share one schema until observed.

### 01D — Implement Collector v0.1 (complete)

01A–01C satisfy the evidence gate. Implement only the Raw collector described below; the browser-observed `x-rpc-wiki_app: genshin` header remains UNKNOWN and is not an implementation prerequisite.

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

## 01D implementation acceptance

Collector fixtures/tests and manifest support belong to 01D implementation and acceptance.

### 01E — Staged validation before full crawl

Live staged validation runs in this order:

1. map + a small set of representative lists;
2. at most ~20 details;
3. inspect Raw, inventory, memberships, manifest, and failures;
4. review;
5. all relevant enumerable channel listings / full inventory;
6. ~200 details;
7. resume/rate-limit/failure validation;
8. full crawl.

Do not treat the map's embedded `list` as the complete inventory. Do not proceed past a failed review gate.

P01-EA evidence (2026-08-24): browser inspection of channels 25 and 130 showed exactly one `content/list` request per channel, no pagination query parameters, and no appended request after scrolling to the bottom. A controlled run completed 200/200 detail fetches with 0 failures. Re-running the same run ID completed in about 1.7 seconds with zero retry attempts, demonstrating listing/detail resume skips for already valid saved responses. This is current frontend evidence, not a permanent API guarantee. The collector records `single_response_verified` for this observed contract and stops with an explicit partial manifest if a future response exposes a recognized pagination control field in the response envelope or listing container. Live 429/5xx behavior remains UNKNOWN; bounded offline retry behavior is covered by tests.

### 01F — Profile Raw before designing Parsed (complete)

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

P01-EB completed the full OBC detail-corpus profiling on the saved run. The profiling gate is satisfied; it does not itself define a Parsed schema.

### Phase 01 closure (2026-08-26)

P01-EB (`run_id=p01eb_full_20260824`) is complete for the current production scope (`zh-cn` + OBC only): 96/96 listing responses, 32,916 listing records, 16,437 unique `content_id` values, 16,437/16,437 successful detail fetches, and 0 final unresolved failures. Archive/hash/inventory audit and same-run recovery passed. P01-EA was checkpointed at `0c5617d` and is not rerun. Phase 01 is **CLOSED**. This records crawl and contract coverage for the run, not absolute semantic completeness of the upstream server. AGD, multilingual, OBC↔AGD alignment, client unpacking, and MiHoYoBinData branches remain retired from active production.

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
