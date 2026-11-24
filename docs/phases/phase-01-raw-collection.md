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

### Versioned Collector contract

New runs use `gc-raw-2`. Before any HTTP dispatch, the Collector records the canonical collection root, source, locale, run ID, base URL, app identifier, request contract, request headers, listing parameter names and digest, channel set, detail flag and detail limit. Reordering or repeating the same channel identifiers yields the same channel set. Operational timeout and retry settings may vary between invocations. A mismatched identity requires a new run. Each mutable run has a process lock outside its archive directory.

Each HTTP attempt has its own receipt and, when a body is available, byte-for-byte response file. Keep the failures and unaccepted observations as audit evidence. Only a verified request binding, HTTP 200, integer `retcode=0` and the expected OBC container qualify an observation for acceptance. The verified discovery response has `data.list` channel nodes. Listing roots must match the requested channel. Details must have a matching `data.page.id`. Leave content component interpretation to downstream. An unhandled pagination signal or a missing requested channel blocks discovery completion. The Collector freezes inventory, memberships and the ordered detail selection after all targeted listings pass.

Raw bodies, receipts and accepted references are created atomically at independent, non-replacing paths. An interruption before reference publication can recover the earliest fully verified attempt. A saved but unaccepted body with missing or invalid metadata stays at its original location. A later request uses a new attempt. An accepted reference remains fixed. Known reference loss or any accepted-path, receipt or body integrity failure blocks collection for the run.

| Artifact | State and recovery |
| --- | --- |
| `metadata/checkpoint.json` | Replaceable `partial` report. Recovery uses durable identity, attempt receipts, accepted references and frozen discovery. |
| `metadata/inventory.json`, `metadata/discovery.json` | Write-once discovery and selection. The inventory SHA-256 is carried into the final manifest. |
| `metadata/finalizing.json` | Durable prepared manifest and SHA-256. Resume can finish publication from the same prepared bytes after verifying the evidence. |
| `metadata/manifest.json` | Final `complete` or `scoped_complete` manifest. Valid reopen under the original collection identity uses zero HTTP requests and writes. Its `paths` entries point to accepted observations relative to the archive root. |

The collection identity governs writes and resume. Archive readers use the final manifest, its digest, the inventory and response locators at the supplied archive root. A backup or moved copy can be read independently of the original collection path, process locks and local access state. Old unversioned runs remain available under their historical read contract. Collection against an old or unidentifiable directory requires a fresh run ID.

#### Source access restrictions

All outgoing requests for `mihoyo_obc` share the per-user source state and dispatch lock, including different request kinds, run IDs, locales, base URL settings and output roots. The state is under `%LOCALAPPDATA%/GenshinChronicle/raw-collector` on Windows and the user state directory under `genshinchronicle/raw-collector` on POSIX. Each HTTP attempt records its dispatch before sending and its response status when received. The Collector preserves unexpected redirect responses as failures under the reviewed request contract.

- **403** freezes new source dispatches across process restarts. A dispatch interrupted before recording the HTTP outcome also blocks new dispatches for review. After independently confirming access is permitted, use the standalone `--acknowledge-access EVENT_ID --reason "verified restoration reason"` operation. It saves a receipt and performs zero HTTP requests.
- **429** writes the source's earliest next dispatch time as UTC epoch seconds, honoring `Retry-After` and bounded backoff with a minimum delay of one second. A wait longer than the available retry budget ends the invocation as `partial`. Restarts and other runs respect the saved deadline. The acknowledgment operation leaves the cooldown in force. Transport and 5xx retries stay bounded.

#### Completion and exit codes

| Status | Meaning | Exit code |
| --- | --- | --- |
| `complete` | All enumerable channels and unique details for this run and locale were collected under the verified OBC request contract. The run requested unfiltered discovery and uncapped details, has nonempty inventory and has no unresolved gaps. Coverage applies to the observed API JSON within this run. | `0` |
| `scoped_complete` | The requested discovery, filtered or sampled scope succeeded. A finite limit remains scoped even when it covers all discovered details. | `0` |
| `partial` | A requested operation has an unresolved failure, coverage gap or access restriction. The resumable report is the checkpoint. | `1` |
| Rejected | Invalid arguments, identity conflict, integrity fault, unavailable run/source lock or local storage failure. | `2` |

The CLI defaults to discovery. Supply `--details` to fetch details and optionally use `--channel-id` and `--detail-limit`. Its JSON summary includes the requested scope, full-coverage eligibility, unresolved failures, counts and the final manifest or checkpoint path. The response receipts preserve historical failures after recovery. A complete run attests to the reviewed source and the enumerable API scope of that observation, with media and upstream material outside the observed scope remaining outside the completeness claim.

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

P01-EA evidence (2026-08-24): browser inspection of channels 25 and 130 showed exactly one `content/list` request per channel, no pagination query parameters, and no appended request after scrolling to the bottom. A controlled run completed 200/200 detail fetches with 0 failures. The historical same-run probe took about 1.7 seconds with zero new requests. That probe established cache reuse at the time. The versioned contract above defines the current publication and resume behavior. The Collector records `single_response_verified` under the reviewed listing contract and reports `partial` if it encounters a recognized pagination signal. The response to live 429/5xx remains unknown. Retry and access restriction handling have offline coverage.

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
