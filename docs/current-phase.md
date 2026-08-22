# Current Phase

## Active phase

**Phase 01 — Raw Collection**

## Objective

Collect the complete `mihoyo_obc` Chinese (`zh-cn`) Raw API corpus as a repeatable, resumable, auditable dataset.

## Current status

Repository initialization is complete.

Formal collector implementation has **not** started yet.

We are at **Phase 01A — Channel tree discovery**.

## Immediate next action

Obtain a fresh browser capture for:

```text
getChannelTree?app_sn=ys_obc
```

Required evidence:

1. the complete Response, unchanged;
2. the request's full `Copy as cURL` capture, kept locally until secrets are reviewed/redacted.

Use the real response to determine the current channel tree and choose representative channels for listing-API sampling.

## Gate before collector implementation

Do not build the general collector until we have verified from current evidence:

- channel tree structure;
- representative channel listing and pagination/termination behavior;
- the current stable retrievable content key (historical `content_id` must be reverified);
- representative detail endpoint behavior across multiple content types;
- which request headers/parameters are genuinely required.

## Explicitly paused

- Parsed schema design
- Canonical field/schema design
- Passage implementation
- Retrieval/RAG
- Embeddings
- AI semantic analysis

## Source of truth

Detailed Phase 01 workflow: `docs/phases/phase-01-raw-collection.md`.
