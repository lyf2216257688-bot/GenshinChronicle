# P05-W2 bounded-navigation Prompt v5 (2026-10-01)

## Boundary

This work unit adds an opt-in, provider-free Prompt/task revision. It does
not call a provider, rebuild a semantic root, modify the compiler/input,
authoritative output schema, source-binding policy, production/default
selector, or active Navi/KG eligibility. v3 and v4 artifacts remain immutable
historical evidence.

## Contract

The revision is `phase05-w2-b-json-object-0.5` with Prompt version
`phase05-w2-b-json-object-prompt-0.5`.

- Experiment identity: `d0e325441e724af31eb7e3f20671a31f7cac8eaf1e66ff2ab45670ae0645a622`
- Prompt identity: `e28273ded5d649e59e2d3143ce1549d490d8c540b581998d2dad9ae4fdda27c3`
- Request contract identity: `b802b77d802178525077af5ccd8250bbf88dc70b9e9bf27f5bba96e423993f2d`
- Output schema identity: `9848b1e07c09b64156497dddc7a1a244f6dd9d7c3d5455c8c0dd0fae380cf312`
- Frozen preflight identity: `8601fb2e665fd5f1bc1b7a9ef35a871269e3ea00f0b9a23654656a0bb3838da0`
- Frozen semantic build identity: `45520b23490a1c66289476c71dbe55c38cd206167342ebeb5894721055c59677`

The task scans every stage but emits only independently navigable claims. It
adds explicit instructions for navigation-worthiness, decorative/poetic
boundaries, repeated-proposition merging, mention minimality, relation
minimality, and uncertainty versus polarity. The inherited v4 instruction
that only unsupported or genuinely ambiguous material may be omitted is
replaced in v5 with a narrower rule: navigation-neutral decorative material may
be omitted or merged, while unsupported or ambiguous material still requires
an explicit disposition. This removes the conflicting omission boundary without
changing the schema or source-binding policy. The existing v3 stage splitting,
minimal source binding, local-reference checks, coverage accounting, and
fail-closed replay remain in force.

## Frozen review fixture

`p05-w2-bounded-navigation-prompt-v5-review-fixture-20261001.json` anchors the
true `507825` point1 source/input and v3/v4 outputs, plus ordinary control
`6430`. It records the required point1 claims, optional decorative material,
duplicate and modality/polarity rules, and excludes `502434` from quality
pass/fail because its map-topic binding remains UNKNOWN. The fixture is a
review checklist, not semantic acceptance.

## Candidate live-gate boundary

The fixture records four candidate units without selecting after seeing results:
true `507825` point1, `507825` point2, the first `2606` dated-email stage, and
ordinary control `6430`. It binds each compilation-unit ID, semantic-input
identity, payload hash, segment IDs, and an existing baseline artifact where one exists. The corrected true-point1
v4 output is reusable historical evidence. No v4 output exists for the other
three candidates, so a full v5-v4 paired comparison across the sample is
UNKNOWN; point2 and the dated email use targeted v3 outputs, while `6430` uses
the accepted Prompt v2 control output as regression context only. `502434` remains excluded because its
map-topic source-binding outcome is UNKNOWN.

The four-unit v5 gate manifest is now frozen at
`docs/research/phase-05/p05-w2-v5-live-gate-manifest-20261001.json`, identity
`724aad586e2e0a21e2353fbcbc6c2dc4bae8cd1b210ec642e25154e5c60e794a`. It binds
the v5 Prompt/config to the four immutable payload and segment identities and
uses the currently proven route-pair operating point: DeepSeek V4.1 Flash,
TokenMetro primary, Jizhi fallback, streaming, `max_tokens=300000`,
`timeout_seconds=900`, no automatic retry, and empty generation parameters.
The SDK CLI accepts this route pair only with explicit v5 selection and the
manifest; the legacy v2/8601 path and historical v3/v4 operating points remain
unchanged. This records the pre-live manifest checkpoint; the subsequent live
result is recorded below.

## Validation boundary

The v5 CLI mapping and zero-network preflight passed with zero formal attempts,
provider calls, and network calls; offline replay was deterministic. Direct
contract assertions, fixture artifact/hash checks, `py_compile`, and the
combined focused suites `test_semantic_prompt_v2` plus
`test_semantic_b_revision` passed `19/19`; the wiring-focused SDK suite passes
`63/63`. The fresh CLI preflight is recorded at
`D:/Batch4_review/p05-w2-v5-gate-preflight-20261001-r2`; it reports zero formal
attempts, provider calls, and network calls. An earlier run was blocked by
Windows `WinError 5` in temporary-directory fixtures; the repeat under the
current writable environment passed, so no ACL work is part of this unit.

## Authorized live gate result

The fixed four-unit gate completed at
`D:/Batch4_review/p05-w2-v5-live-gate-20261001-r4` using the manifest above,
DeepSeek V4.1 Flash, TokenMetro primary, Jizhi fallback, streaming,
`max_tokens=300000`, `timeout_seconds=900`, and zero automatic retries. All
four units were accepted on TokenMetro primary with HTTP 200, complete streams,
zero fallback attempts, and zero retry attempts. `audit_sdk_route_integrity`
passed with four accepted attempts; the audit itself made zero provider and
network calls. The earlier `r1`, `r2`, and `r3` roots remain immutable
interrupted mechanical evidence.

The semantic result is **NOT PASS**. True `507825` point1 fell from v4's 119
items to 14. Redundancy and poetic expansion were substantially reduced, and
the explicit prohibition was marked negative while the unknown commander was
marked uncertain. However, the output omitted many frozen required claims,
including the sleep/awake state, rising clouds, mournful sound, unfamiliar
sound, grief and suffering, memory/name state, warning seal, tears, mourning
target, and multiple inner emotional states. This is a recall failure against
the frozen checklist, not a mechanical execution failure.

Point2 produced 52 items versus its historical v3 21-item output, leaving
possible over-extraction unresolved because no v4 paired baseline exists. The
dated email produced 21 items and retained uncertainty for the possible arrival
time. The ordinary `6430` control retained conditional, tentative, and uncertain
boundaries and accounted for its image-only segment. These controls provide
useful bounded evidence but do not offset the point1 failure. V5 remains
opt-in; semantic acceptance, production/default adoption, and Full Semantic
Build remain blocked. At that checkpoint, no further live retry or sample
expansion was authorized by this result. The subsequent separately authorized
architecture research is recorded in
`p05-w2-inventory-navigation-architecture-20261001.md`; it preserves this v5
failure and all historical artifacts.
