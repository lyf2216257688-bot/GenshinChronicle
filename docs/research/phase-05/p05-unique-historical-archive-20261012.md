# Phase 05 Unique Historical Checkpoint Archive (2026-10-12)

This document preserves historical Phase 05 run records and execution lineages
that were previously recorded directly in `docs/current-phase.md` and do not have
a standalone research note owner. This is not current authority; present state
and immediate next actions are owned solely by `docs/current-phase.md`.

---

## 1. 2026-10-04 TokenMetro Responses Historical-200 Live Runs & Lineage Audit

The TokenMetro DeepSeek Responses transport was implemented as an opt-in route variant
(Chat Completions retained for compatibility; Jizhi fallback rule unchanged).

### Historical Attempt Roots & Execution Lineage

1. **Initial Responses-Primary 200-Unit Attempt**:
   - The first attempt stopped on the first pure-media `map_desc` unit because the existing
     validator correctly rejected an unsupported source binding.
   - Corpus inspection showed the 9,920 observed `map_desc` segments contain only media/UI
     metadata; a narrow provider-free projection correction omitted those segments from LLM
     payloads while preserving source/RU provenance and accounting.
   - The revised manifest retains all 200 historical members and marks 9 provider-omitted units.
   - The prior `mapmedia-r1` manifest persisted `api_surface=chat_completions`; its label did
     not match its actual Chat Completions route. That root remains immutable historical evidence:
     `D:/Batch4_review/p05-w2-atomic-inventory-live-historical-200-20261004-mapmedia-r1`.

2. **Responses-Bound Run (`responses-r1`)**:
   - Manifest: `D:/Batch4_review/p05-w2-atomic-inventory-manifest-historical-200-20261004-responses-r1.json` (identity `872c9ebf5a83ffb26c07d5bf9d5b1257bf0acaf54dedd2ead91fbb2bd0ea6294`).
   - Root: `D:/Batch4_review/p05-w2-atomic-inventory-live-historical-200-20261004-responses-r1`.
   - Issued 132 TokenMetro `/v1/responses` attempts: 131 complete local accepts, then an HTTP 503
     `Service temporarily unavailable` with zero chunks, no usage, and no provider request ID.
   - Execution and billing remain `UNKNOWN`; runner stopped without fallback or retry.
   - Offline route integrity/replay passed for the 131 accepted outputs with zero replay provider/network calls.
   - This run is incomplete mechanical evidence, not semantic-quality acceptance.

3. **Responses Continuation (`responses-metro-continuation-r1`)**:
   - Authorized for the blocked unit plus the 59 remaining units.
   - Root: `D:/Batch4_review/p05-w2-atomic-inventory-live-historical-200-20261004-responses-metro-continuation-r1`.
   - Completed 55/60 local accepts, then stopped on an HTTP 200 complete stream whose local
     validation failed (`B v2 relation requires an explicit predicate`). Four continuation units
     were not issued; no retry or fallback was used.

4. **Four Unissued Frozen Members**:
   - Run across two separate immutable TokenMetro Responses roots.
   - First root stopped at an indeterminate provider execution before issuing remaining members;
     offline replay audit passed.
   - After a backend-channel change, the second root completed all 4 primary attempts with no
     fallback or retry: 1 output passed atomic source-binding review, and 3 were rejected because
     their `source_quote` values were not exact substrings of the bound source payload.

5. **Unified Historical-200 Audit Results**:
   - Pure-media omissions: 9.
   - Accepted logical members: 187.
   - Correctness-rejected logical members: 4.
   - Lineage and hashes: `D:/Batch4_review/p05-w2-atomic-inventory-audit-20261004-r1/lineage-audit.json`.
   - These failures keep v0.7 inventory validation open; compact navigation of that historical
     inventory plus official RU backcheck remains unauthorized.

---

## 2. 2026-10-07 Semantic-Batch Cheap-Channel Rate-Control Canary

- **Commit**: `45a268a` (`add semantic batch rate control`).
- **Canary Scope**: A fresh 2-unit cheap-channel canary at
  `D:/Batch4_review/p05-semantic-batch-cheap-rate-canary-20261007-r1`.
- **Execution & Outcome**:
  - Issued exactly one TokenMetro request and stopped fail-closed.
  - Returned HTTP 429 `RateLimitExceeded.EndpointRPMExceeded`, with zero stream chunks,
    `UNKNOWN` usage, and no generation evidence.
  - Therefore ineligible for the narrow TPM-only automatic retry.
- **Lineage Accounting**:
  - Final lineage state: one `execution_unknown` plus one `pending_never_issued`.
  - Audit authority is complete.
  - Cheap-channel rate-control live validation is **PARTIAL / paused on current provider capacity**.
  - Do not resume, rerun, or reuse this immutable root.
