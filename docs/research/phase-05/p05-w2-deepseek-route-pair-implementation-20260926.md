# P05-W2 DeepSeek route-pair implementation (2026-09-26)

## Scope

This work implements the provider-route boundary for the selected DeepSeek
V4.1 Flash production candidate. It does not change Prompt v2, the semantic
schema, frozen source inputs, Canonical, Block A, or the full semantic build.
Two one-shot provider canaries were sent after provider-free implementation
and tests, one per route.

## Implementation

- `semantic_tokenmetro_profile.py` now exposes secret-free `tokenmetro` and
  `jizhi` profiles. Runtime base URLs and credentials are selected by
  `TOKENMETRO_BASE_URL`/`TOKENMETRO_API_KEY` and
  `JIZHI_BASE_URL`/`JIZHI_API_KEY`.
- `semantic_sdk_runner.py` now exposes `run_sdk_route_pair`. It creates one
  logical request identity and immutable per-route attempts, uses streaming
  Chat Completions with `max_retries=0`, and records route history, chunks,
  latency, finish state, usage, validation, and fallback counters.
- `replay_sdk_route_attempt` verifies accepted route-pair output from saved
  response and artifact hashes with zero provider/network calls.
- Fallback is allowed only for an exact TokenMetro HTTP 403 JSON
  `error.code=content_policy_violation`, or the observed
  `error.type=content_policy_violation` with null `error.code`, with zero
  chunks, zero visible or reasoning content, and no usage or finish reason.
  The latter shape was verified against the frozen ordinal21 raw error SHA-256
  `7f3cf16ab19f8b09e3b5bb37395f2ed847d63b2e525b645f1bc5634576d997a9`.
  The provider-free test uses a small inline fixture with that observed field
  layout, so it does not depend on a local `data/` root.
  All ambiguous execution states stop without a route change.
- Existing single-route `run_sdk_units` behavior and historical direct-HTTP
  paths remain available and unchanged in their invocation contract.

## Provider-free verification

The focused SDK test module passes 27 tests, including the existing single-
route retry/resume/tamper tests and new route-pair cases for:

- exact policy-403 fallback success;
- ordinary 403 rejection without fallback;
- incomplete stream and timeout stop states;
- same request body and model across both routes;
- ordered attempt accounting and secret absence;
- runtime URL/profile validation;
- resume after a persisted policy 403, accepted-unit skip, and terminal fallback
  failure without reissue.

The combined semantic retrieval set passes 52 tests covering the
SDK runner, legacy live runner, B revision, and Prompt v2 serialization.

## Live validation and current status

One fresh current-key ordinal16 canary was issued on each route, under distinct
immutable roots with no automatic retry:

| Route | Root | HTTP / finish | Local contract | Latency | Input / reasoning / output tokens |
| --- | --- | --- | --- | ---: | --- |
| TokenMetro | `data/retrieval/p05-w2-deepseek-route-canary-tokenmetro-20260926-r1` | 200 / stop | pass | 38,505.467 ms | 1,341 / 8,398 / 9,175 |
| Jizhi | `data/retrieval/p05-w2-deepseek-route-canary-jizhi-20260926-r1` | 200 / stop | pass | 7,750.204 ms | 1,316 / UNKNOWN / 889 |

Both use frozen ordinal16 unit
`67646d38b6fabc54d02ad1d7d5d652248e266cc76feb287703f994bb2505fc09`,
Prompt v2 identity
`25ebc483c5dfcfe9b3dc8ddd01001f75e98ff4c2b5327aa789a3af84a068003e`,
schema identity
`9848b1e07c09b64156497dddc7a1a244f6dd9d7c3d5455c8c0dd0fae380cf312`,
and `stream=true`, `max_tokens=500000`. The logical request identity is
`2f41c2f4d59b49ca8ef2c92da76dd4334d7509ab7fe4e04bcb0b50e92426d6ba`
on both routes; the request and wire SHA-256 values match byte for byte.
Referenced artifact hashes/byte counts and a scan for both configured key
values passed. The outputs are local mechanical passes, not a semantic-quality
comparison. Provider billing/currency remain UNKNOWN.

The TokenMetro canary's first CLI process exited with code 1 after persisting
an accepted terminal because the canary return path expected a `status` field.
The CLI exit handling was corrected without reissuing the request. The Jizhi
canary exited 0. A separate local test accidentally attempted to connect to
the `tokenmetro.fixture` test domain with `transport=None`; it failed before
HTTP and was not a TokenMetro/Jizhi provider request. Subsequent tests used
MockTransport only.

The prior Jizhi 401/no-generation blocker is superseded for this exact
current-key DeepSeek Chat Completions canary. Jizhi behavior for ordinal21,
real fallback execution, route availability over time, and semantic quality
remain UNKNOWN. The route-pair mechanism is structurally ready for a separately
authorized bounded pilot. The CLI currently permits explicit one-route
canaries and rejects automatic route-pair execution until that pilot is
authorized. No ordinal21 or full build was run.
