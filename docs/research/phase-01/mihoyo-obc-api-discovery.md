# MiHoYo OBC API Discovery — Phase 01

## Purpose

Record **observed evidence** needed to implement the Raw collector. Keep verified facts separate from historical leads and assumptions.

## Current investigation

Status: **01A — channel tree discovery**

Target request:

```text
getChannelTree?app_sn=ys_obc
```

Needed from a fresh browser session:
- complete Response;
- complete `Copy as cURL` kept locally for analysis;
- capture date/time;
- visible request URL/method/status.

## Evidence discipline

Use these labels:

- **VERIFIED** — directly observed in a current capture.
- **HISTORICAL LEAD** — seen in earlier work; must be reverified.
- **ASSUMPTION** — not supported yet; must not enter collector code.
- **UNKNOWN** — explicitly unresolved.

## Current facts

| Item | Status | Evidence / note |
|---|---|---|
| Source system identifier | VERIFIED | `mihoyo_obc` is the project source label. |
| Current target locale | VERIFIED | `zh-cn`. |
| Channel-tree query contains `app_sn=ys_obc` | HISTORICAL LEAD | Reverify with fresh capture before implementation. |
| Listing endpoint/schema | UNKNOWN | Must be sampled after channel-tree analysis. |
| Detail endpoint/schema across content types | UNKNOWN | Must be sampled across representative channels. |
| Required headers | UNKNOWN | Derive from current browser requests; do not guess. |

## Historical leads to reverify

Earlier work suggested patterns resembling:
- channel listing requests parameterized by `channel_id`;
- detail requests using an `entry_page` endpoint and a content/page identifier;
- additional wiki-specific request headers.

These are not current contracts until verified again.

## Sanitized request record template

```text
Observed at:
Method:
URL:
Status:
Query parameters:
Required headers:
  <header-name>: <REDACTED or non-secret value>
Cookies required?:
Response content type:
Notes:
```

## Open questions

1. What is the full current channel hierarchy?
2. Which nodes are directly enumerable?
3. Is there one listing schema or multiple listing schemas?
4. How is pagination represented, and what observable condition proves a channel listing is exhausted?
5. What stable key identifies a retrievable content item? Is the historical `content_id` still the correct current key?
6. Can one content item belong to multiple channels, and can cross-channel duplicates be deduplicated within one crawl run without losing membership evidence?
7. Is detail retrieval common across content types?
8. Which headers are truly required versus browser noise?
9. What throttling/error behavior is observed?
10. Are there discovery structures that are not channel -> list -> detail?

## Promotion rule

A finding may move from this research note into the Phase 01 implementation specification only after it is supported by current captured evidence.
