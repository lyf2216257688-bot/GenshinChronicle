# P05-W2 DeepSeek bounded production-route pilot r1 (2026-09-27)

## Scope and fixed operating point

One invocation of production `run_sdk_route_pair` ran three real, RU-bound
Semantic Compilation Units from the accepted frozen preflight
`data/retrieval/p05-w2-live-preflight-20260923-r2` (preflight identity
`8601fb2e665fd5f1bc1b7a9ef35a871269e3ea00f0b9a23654656a0bb3838da0`; semantic
build identity
`45520b23490a1c66289476c71dbe55c38cd206167342ebeb5894721055c59677`). The
cohort was selected before execution from small, non-ordinal21 units with
different input roles:

| DeepSeek ordinal | Compilation unit | Role | Segments | Input bytes |
| ---: | --- | --- | ---: | ---: |
| 0 | `795c7d1c7502f2ac454d614be84a0ba683d6a73dbc70150de8953db3f8be81eb` | high-risk dialogue branch / omitted subject | 1 | 1,042 |
| 2 | `34493832d4e89251697f4b8485283165e1d646df347eecee47235f84121735f2` | high-risk identity role / knowledge belief | 1 | 771 |
| 14 | `a54cedd8d96e76284f5f07e77d78c96c1aec08887234ee52ea4a64bc51656ca4` | matched control | 2 | 1,178 |

All selected payload hashes, semantic input identities, source segment IDs,
request identities, and wire request identities were frozen by the zero-network
preflight. Prompt v2 identity remained
`25ebc483c5dfcfe9b3dc8ddd01001f75e98ff4c2b5327aa789a3af84a068003e`; output
schema identity remained
`9848b1e07c09b64156497dddc7a1a244f6dd9d7c3d5455c8c0dd0fae380cf312`. The
model was `deepseek-v4.1-flash`, using TokenMetro primary and Jizhi fallback,
OpenAI SDK 3.19.2 / httpx 0.28.1, Chat Completions, `stream=true`,
`max_tokens=16384`, 300-second timeout, and no automatic retry. The maximum was
six provider attempts (three units, at most one exact-policy fallback each).
No 403 was manufactured or sought.

## Provider outcomes

All three units completed on TokenMetro primary with HTTP 200,
`finish_reason=stop`, complete streams, and `accepted_for_local_contract`.
Strict JSON/schema validation and binding to the frozen input segments passed
for each unit. No transport, parsing, schema, or source-binding failure was
observed; there were zero indeterminate units.

| Ordinal | Route | Latency ms | First chunk ms | First visible ms | Input / output / reasoning tokens | Visible chars |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 0 | TokenMetro primary | 12,398.308 | 2,281.103 | 11,597.979 | 1,484 / 2,284 / 2,071 | 591 |
| 2 | TokenMetro primary | 60,462.024 | 2,834.465 | 54,539.244 | 1,353 / 11,342 / 10,340 | 2,674 |
| 14 | TokenMetro primary | 59,013.510 | 2,180.350 | 55,763.726 | 1,531 / 9,618 / 9,055 | 1,629 |

Across the three attempts, reported usage totals were 4,368 input, 23,244
output, and 21,466 reasoning tokens; one attempt reported 896 cached tokens.
Reported credit was `0` on each response. Currency and billable status were
not supplied and remain `UNKNOWN`; the reported credit is not treated as a
verified monetary cost.

Real automatic fallback was **UNOBSERVED** in this cohort: TokenMetro returned
no policy 403, so `tokenmetro_policy_403=0`, `jizhi_fallback_issued=0`, and
`accepted_fallback=0`. The runner correctly kept all three accepted units on
the primary route. This is neither a fallback failure nor evidence that the
historical policy trigger has been removed.

## Accounting and offline audit

The immutable root is
`data/retrieval/p05-w2-deepseek-bounded-pilot-20260927-r1`, run identity
`4e1b49dcab125a7bda23751ff25bbeb50064f44420670a055ad443910b536b1d`, with
terminal summary identity
`343ec7958f4bb81827d7f2558a4ffac7165e8ade9fb36279ee45e73dd1cbbc96`.
Accounting records three logical units, three total/current-invocation
attempts, three accepted primary units, zero policy 403s, zero Jizhi attempts,
zero unknown execution states, and automatic retry disabled.

Offline audit verified all 23,265 artifact descriptors and byte counts across
23,274 files (14,262,658 bytes), manifest/summary identities, route and
request bindings, exact parsed JSON equality between canonical request and
SDK wire body, prompt/model/schema/segment identities, and absence of both
configured credentials. Replay passed for all three attempts with zero network
or provider calls. An isolated copy resumed with rejecting mock transports,
skipped all accepted units, and made zero provider calls; the original root
was not resumed or changed.

## Decision and limits

This pilot passes its bounded **operational/mechanical** gate for the three
selected units: primary route execution, local contract validation, immutable
attempt evidence, accounting, and accepted-unit resume behavior. Real
automatic handoff remains **UNOBSERVED**, not FAIL, and is not a prerequisite
for this bounded pilot under the current authorization.

This small cohort does not establish semantic correctness, quality, broad
route stability, corpus-scale build behavior, or full semantic-build
readiness. No automatic fallback was exercised, and no new blocker was
observed. No Gemini/GLM request, ordinal21, full build, Prompt/schema/source/
Block A edit, or additional provider request followed this run.
