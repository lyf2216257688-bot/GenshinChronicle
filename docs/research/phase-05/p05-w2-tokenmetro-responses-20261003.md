# P05-W2 TokenMetro Responses transport (2026-10-03)

## Scope

This note records an opt-in transport variant for the existing
`phase05-w2-atomic-inventory-prompt-0.7` contract. The Prompt, source payloads,
schema, source-binding policy, provenance, validator, fail-closed behavior, and
Jizhi fallback rule remain unchanged. Chat Completions remains available as the
compatibility path. No production/default selector changed.

## Fixed two-unit gate

Manifest:
`D:/Batch4_review/p05-w2-atomic-inventory-manifest-responses-true-point1-6430-20261003-r1.json`
(`1e9e4391903e69a86eced1738fb8b15e1e6b54d32f4629f8471f9fdce14999bb`). The
units are the existing true `507825` point1 unit and ordinary control `6430`.
Source identity is
`45520b23490a1c66289476c71dbe55c38cd206167342ebeb5894721055c59677` and the
v0.7 Prompt identity is
`cb4b76eeb012c1f2afa43c60e688e8a30bf37f6404744f56e02b0532683562c0`.

Root:
`D:/Batch4_review/p05-w2-atomic-inventory-live-responses-true-point1-6430-20261003-r1`
(`62aa27b98e52a9023fa5d59b8ab14403b1bcfbe719ec7afb0792b8a091044814`). Both
units were TokenMetro primary accepted with HTTP 200, complete Responses
terminal events, zero fallback attempts, and zero retries. Atomic/schema/source
validation passed. `audit_sdk_route_integrity` passed with two accepted
attempts and zero provider/network calls during replay.

The source-anchored point1 review matched all 39 frozen claims. The output had
89 items and three duplicate quote items; the existing v0.7 comparison runs had
83/85 items and 4/0 duplicate quote items. Qualifier checks for unknown
identity/target, negative, inability, rhetorical, attribution, and condition
showed no obvious regression. Control `6430` retained conditional antecedents
and the tentative outcome (12 items versus the prior 11-item baseline).

## 200-unit attempt

Manifest:
`D:/Batch4_review/p05-w2-atomic-inventory-manifest-historical-200-20261003-v07-responses-r1.json`
(`41592c1ea5c106c8eda2b45c00af6c9546b55fa8b260043d21d370468fa38f6c`). It
preserves all 200 members of historical sample identity
`72e1ec582cfb98c06c4ff15d74c2abe5f50d8c7f7e53381d3d224b5d7434577a`, with
TokenMetro Responses primary and the original Jizhi Chat Completions fallback.

Root:
`D:/Batch4_review/p05-w2-atomic-inventory-live-historical-200-20261003-v07-responses-r1`.
The run issued exactly one TokenMetro Responses request for the first unit and
then stopped `blocked` after HTTP 200 and a complete stream. JSON parse and
authoritative schema validation passed, but source binding failed closed:
`semantic output item 0 source binding is not directly supported by map_desc`.
The unit's only source segment is image-only `map_desc`, so no fallback or
retry was eligible and no other 199 units were issued. Offline integrity audit
passed with 200 logical units, one attempt, zero accepted attempts, and zero
provider/network replay calls.

This blocker is a source-binding/input-kind issue already enforced by the
existing validator, not evidence against the Responses streaming transport.
Do not relax the binding contract or resume this root without a separately
authorized source-fidelity decision. `502434` remains outside this work unit.

## Provider-free media projection correction and resumed run (2026-10-04)

Corpus inspection found 9,920 `map_desc` source segments across the accepted
Canonical/RU build. Every observed value contained only image/media URLs and
UI metadata (`tab_name`, `layout_`, or `moduleName`); no observed `map_desc`
value contained text semantic fields. The projection now uses a narrow
recursive predicate: known media/UI keys are ignored, unknown keys and text
values remain provider-visible. Pure-media segments are recorded in the
omission ledger and sidecar with their original source/RU provenance and are
excluded from the provider payload. Mixed units retain their text segments;
`map_desc` is not globally excluded. The source-binding validator is unchanged.

Provider-free root:
`D:/Batch4_review/p05-w2-u1-provider-free-20261004-mapmedia-r3`.
It records 9,920 omitted source segments, 210,808 compilation units, and zero
provider/API/network calls. The revised historical 200 manifest is
`D:/Batch4_review/p05-w2-atomic-inventory-manifest-historical-200-20261004-mapmedia-r3.json`
(`4f33e4d68f7e8536cba318f9c0fa002f0bd3ad264bca8d07be89bc13ca8ec926`). It
retains all 200 historical logical members and marks 9 as provider-omitted.

Historically mislabeled `mapmedia-r1` run root (actual surface: Chat Completions):
`D:/Batch4_review/p05-w2-atomic-inventory-live-historical-200-20261004-mapmedia-r1`.
It issued three TokenMetro primary Chat Completions attempts before fail-closed
stop: two HTTP 200 complete streams accepted locally, and the third returned
HTTP 200 with no stream chunks or terminal event after the 900-second timeout
window. No retry, fallback, or later unit was issued. This is mechanical
evidence only. Offline route integrity replay passed with three attempts, two
accepted outputs, and zero provider/network calls.

## Route-surface identity correction and resumed 200-unit run (2026-10-04)

The `mapmedia-r1` run above is retained unchanged, but an identity audit found
that its persisted gate/atomic manifest encoded
`primary_route_config.api_surface=chat_completions`. The runner therefore
issued Chat Completions requests for that historical root; the surrounding
Responses label was not executable configuration. This correction does not
reinterpret the old attempts as Responses evidence. A new provider-free
Responses gate manifest (`cb31c3f5536104500d6d6c3a8dfa447485570fae7b729c615fdc1b91bf7cd266`)
and matching 200-unit manifest (`872c9ebf5a83ffb26c07d5bf9d5b1257bf0acaf54dedd2ead91fbb2bd0ea6294`)
now persist the surface in route/config identities and wire-request hashes.

The new root is
`D:/Batch4_review/p05-w2-atomic-inventory-live-historical-200-20261004-responses-r1`.
It issued 132 TokenMetro primary Responses requests: 131 complete streams passed
local validation; the next request reached `/v1/responses` and returned HTTP
503 `Service temporarily unavailable`, zero stream chunks, no usage, and no
provider request ID. The runner classified execution and billing as UNKNOWN,
stopped the batch, and issued no fallback or retry. Offline integrity/replay
passed for 132 attempts and 131 accepted outputs with zero replay
provider/network calls. The 200-unit batch is incomplete; fixed-sample semantic
review was not performed. The stopped root is immutable and must not be
continued by resending the indeterminate unit without a separate evidence-based
execution decision.

## TokenMetro continuation after upstream 503 (2026-10-04)

Following the upstream-fault diagnosis, a separate continuation kept the same
v0.7 Prompt, Responses route, source payloads, and remaining frozen members. It
included the previously blocked unit plus the 59 units not issued by the
parent root. The continuation root
`D:/Batch4_review/p05-w2-atomic-inventory-live-historical-200-20261004-responses-metro-continuation-r1`
issued 56 TokenMetro Responses requests: 55 complete local accepts, then one
HTTP 200 complete stream failed local validation with
`B v2 relation requires an explicit predicate`. Four units were not issued;
there was no retry or fallback. Offline integrity/replay passed for the 56
attempts and 55 accepted outputs with zero replay provider/network calls. This
is a correctness blocker, not semantic acceptance; no automatic repair or
further resend was performed.
