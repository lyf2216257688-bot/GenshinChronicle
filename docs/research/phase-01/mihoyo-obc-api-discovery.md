# MiHoYo OBC API Discovery — Phase 01

## Purpose

Record **observed evidence** needed to implement the Raw collector. Keep verified facts separate from historical leads and assumptions.

## Current investigation

Status: **01A–01C complete; 01D ready**

Reviewed current contracts:

```text
GET /common/blackboard/ys_obc/v1/home/map?app_sn=ys_obc

GET /common/blackboard/ys_obc/v1/home/content/list?app_sn=ys_obc&channel_id=<channel_id>

GET /hoyowiki/genshin/wapi/entry_page?app_sn=ys_obc&entry_page_id=<content_id>&lang=zh-cn
```

The reviewed capture and sanitized request records are the evidence basis for the facts below. Do not paste secrets or unredacted cURL here.

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
| Channel tree endpoint | VERIFIED | Current reviewed capture uses `/common/blackboard/ys_obc/v1/home/map?app_sn=ys_obc`. |
| Ordinary channel listing endpoint | VERIFIED | Verified across representative channels 43, 25, 233, 129, 267, and 81. |
| Detail endpoint | VERIFIED | `/hoyowiki/genshin/wapi/entry_page` verified for character 501157, quest 509653, and video 509109. |
| Stable directory/detail key | VERIFIED | `content_id` is the current verified listing identity and detail request key. |
| Cross-channel membership | VERIFIED | One `content_id` may belong to multiple channels; deduplicate details within a run while preserving all memberships. |
| `x-rpc-wiki_app: genshin` requirement | UNKNOWN | Observed in browser requests; server-side necessity is not proven. |
| Listing/detail schemas, pagination termination, errors/limits | UNKNOWN | Keep unresolved until implementation smoke/staged validation. |

## Historical leads and unresolved contract details

Earlier work suggested patterns resembling:
- channel listing requests parameterized by `channel_id`;
- detail requests using an `entry_page` endpoint and a content/page identifier;
- additional wiki-specific request headers.

The endpoint and key patterns above are now verified for the current reviewed capture; remaining schema, pagination, error, and header behavior stays unresolved until implementation validation.

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

1. Is there one listing schema or multiple listing schemas?
2. How is pagination represented, and what observable condition proves a channel listing is exhausted?
3. Which headers are truly required versus browser noise?
4. What throttling/error behavior is observed?
5. Are there discovery structures that are not channel -> list -> detail?

## Promotion rule

A finding may move from this research note into the Phase 01 implementation specification only after it is supported by current captured evidence.
