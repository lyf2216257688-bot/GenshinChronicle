# ADR-0001 — MiHoYo OBC content_id Stable Content Key Contract

Status: accepted  
Date: 2026-09-15  

## Context

In Phase 01 (Raw Collection), official wiki (`ys_obc`) content is acquired from official MiHoYo endpoints. We needed a deterministic, auditable, and evidence-grounded key to build content inventories, deduplicate runs, and fetch detail pages without brute-forcing identifiers or guessing API schemas.

Observations across representative channels (43, 25, 233, 129, 267, 81) showed that channel listing responses (`/common/blackboard/ys_obc/v1/home/content/list`) expose `content_id` on each item, and items may belong to multiple channels simultaneously.

## Decision

1. **Verified Stable Content Key**: Treat `content_id` (passed as `entry_page_id` in `/hoyowiki/genshin/wapi/entry_page`) as the verified stable retrievable key for official OBC detail payloads in this crawl pipeline version.
2. **Run-Level Deduplication with Multi-Channel Preservation**: Within one crawl run/content inventory, if the same verified `content_id` belongs to multiple channels, fetch its detail response exactly once and preserve all channel memberships in the inventory. Later crawl runs remain free to observe/fetch the item again so source changes can be detected.
3. **Strict Discovery Hierarchy**: Candidate identifiers must be discovered strictly via official structural hierarchy: `channel tree -> channel listing -> content inventory -> detail responses`. Brute-forcing candidate identifiers (including integer scanning or guessing historically observed IDs) is strictly prohibited.
4. **Evidence-Driven, Non-Permanent**: Treat `content_id` as verified by observed evidence for the current API version, not as an eternal assumption for future API versions.

## Consequences

- Prevents redundant API traffic and potential rate-limiting (429) while preserving complete channel membership provenance.
- Complies strictly with anti-bot and server access rules by avoiding blind ID brute-forcing.
- Forms the stable source-key lineage passed downstream from Raw to Parsed and Canonical layers.

## Evidence

- Verified and promoted in [`docs/phases/phase-01-raw-collection.md`](../phases/phase-01-raw-collection.md) (Sections 01B, 01C, 01D).
- Detailed empirical request/response captures recorded in [`docs/research/phase-01/mihoyo-obc-api-discovery.md`](../research/phase-01/mihoyo-obc-api-discovery.md).
- Enforced as repository-wide invariant in [`AGENTS.md`](../../AGENTS.md).
