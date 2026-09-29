# P05-W2 Targeted v3 Provider-Free Preflight (2026-09-28)

This note records the provider-free preparation boundary before the later
authorized live run. The live outcome is recorded separately in
`p05-w2-targeted-v3-live-20260928-r1.md`.

## Scope and selection

This is a fixed, small follow-up to the immutable 200-unit root
`data/retrieval/p05-w2-scoped-semantic-20260927-r1`. It does not rerun that
root, execute live validation, or assess semantic quality. Parent review
evidence and the current compiler output determine the selected units:

| Source record | v3 units | Reason |
| --- | ---: | --- |
| `507825` 古老的字迹【安瓦蒂尼尔湖】 | 3 | All three explicit dialogue point stages; inspect important independent events, cross-stage context, and duplicate/incorrect relations. |
| `2606` 托马 | 5 | All five dated `主题：` email stages; the prior output touched each heading but compressed separate developments. Check event coverage and speaker/date continuity. |
| `502434` 残破的出勤记录【阿陀河谷】 | 1 | Complete record with image-only `map_desc` beside textual dialogue events; check that textual items use minimal direct support and map-only support stays with location/topic items. |
| `6572` 敌人索引 | 1 | The short `岩` segment at the same Canonical ordinal as the prior accepted sparse control; detect invention or regression. |
| `6430` 奇异的甘露封印…【教程】 | 1 | Prior accepted multi-segment ordinary control with events/relations/modality; check ordinary context and binding behavior. |

The initial five-record/five-unit idea was expanded only where the compiler
made the old parent units non-equivalent: three point stages and five email
stages are separately compiled. The final fixed set has 11 units, 17 supplied
segments, and 27,447 serialized input bytes. Each selected segment has at least
one RU binding. All selected payloads are non-partial and non-oversized. The
five-record filtered compiler output also contains four unselected oversized
units; they are not claimed ready.

## Frozen input and identities

- Input root: `data/retrieval/p05-w2-targeted-v3-20260928-r1/input`.
- Authoritative preflight root: `data/retrieval/p05-w2-targeted-v3-20260928-r1/preflight-final`.
- Targeted semantic build identity: `561bf9b6dffdeaeaa394ee924db509d0747a9f808511fe947007aa311f92100b`.
- Parent semantic build identity: `45520b23490a1c66289476c71dbe55c38cd206167342ebeb5894721055c59677`.
- RU build identity: `49b48ee746716add0248fed388d10bd522a930efb582a0f5e827f66681ed8998`.
- v3 prompt identity: `2c332577f91edb50806a4f4c2cf663b720932c4833ac0e6023e1050c74370a2d`.
- Route-pair request-parameter identity: `38a73966aaeaa27d726139dfb5d5b9ff04d1e23bf9287becb8e82871f47fbc4b`.
- Payload-set identity: `4afc097ddbd72a0359966fff9aab449599130e9f76b1d84915a9cd8457826f28`.
- Preflight identity: `02b1b33cc742edf0575c08782d048290627562a70b2837c1df9e2f9beb338827`.

The v3 JSON-object experiment contract remains an opt-in historical-preflight
template. This targeted bundle uses its v3 prompt with the committed streaming
SDK route-pair request shape; the two request-contract identities are distinct
and are not interchangeable. The historical `frozen_preflight_units` and
`run_channel` entry points reject this new identity/count. The committed
`run_sdk_route_pair()` accepts explicitly supplied units and a source identity;
future execution must first verify the frozen preflight/artifact hashes and
pass the exact units in `request_ordinal` order with this targeted semantic
build identity. Its existing immutable attempt, resume, fallback, and audit
boundaries then apply. At this preparation boundary, live execution had not
yet been authorized.

## Provider-free result and next boundary

Preflight construction and an independent local readback PASS: all six artifact
hashes, 11 payload/input identities, 11 request identities, 11 wire identities,
RU bindings, and complete `3/3` point and `5/5` email stage coverage verify.
Compiler and preflight provider/network calls are zero. The earlier
`preflight/` and `preflight-v2/` subdirectories are superseded preparation
drafts; only `preflight-final/` is authoritative.

A separately authorized live validation would plan 11 TokenMetro primary
requests. Only exact persisted pre-generation HTTP 403
`content_policy_violation` can trigger at most one Jizhi fallback per unit:
zero fallback requests are planned, 11 are the conditional ceiling, and 22
is the combined hard ceiling. There is no automatic retry or sample expansion.
Provider revision, token usage, charge, semantic improvement, and output
quality were UNKNOWN at this preparation boundary. The later live run is
reported separately and does not turn this preflight into semantic acceptance.
Neither the targeted input PASS nor the prior mechanical 200-unit PASS implies
semantic acceptance, full-corpus readiness, or production adoption.
