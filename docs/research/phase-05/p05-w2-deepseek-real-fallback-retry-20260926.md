# P05-W2 DeepSeek real route-pair retry diagnostic (2026-09-26)

## Scope and request

The user explicitly authorized one retry after the first synthetic `钓鱼`
route-pair diagnostic completed on TokenMetro primary. This r2 run used a new
root, `data/retrieval/p05-w2-deepseek-route-fallback-diagnostic-20260926-r2`.
It did not reuse or change r1 evidence. The sole synthetic segment
`diagnostic-diaoyu-s1` contains `钓鱼。` repeated 15 times, matching only the
historically observed occurrence count, not ordinal21 text or identity. The
unit is `diagnostic-only-diaoyu-repeat15-route-pair-20260926-r2`; its source
identity is `diagnostic-only:diaoyu-repeat15-20260926-r2`. The unchanged
Prompt v2 and output schema were loaded through the production
`run_sdk_route_pair` entry.

The zero-network preflight confirmed a fresh root, both credential variables
set, TokenMetro and Jizhi route profiles for `deepseek-v4.1-flash`, and pinned
`openai` 3.19.2 / `httpx` 0.28.1. Chat Completions used `stream=true`,
`max_tokens=16384`, a 300-second timeout, and zero automatic retries.

| Binding | Identity |
| --- | --- |
| Prompt v2 | `25ebc483c5dfcfe9b3dc8ddd01001f75e98ff4c2b5327aa789a3af84a068003e` |
| Output schema | `9848b1e07c09b64156497dddc7a1a244f6dd9d7c3d5455c8c0dd0fae380cf312` |
| Synthetic payload SHA-256 | `443ac09215ded47cb3f5971192c67f6b47473ac42af1075b6a1664e48e66b9f7` |
| Semantic input | `78f37ad5cd34c6e11b1bd929738b8bac2c7cf20be9c384790baca276dd3ccc8a` |
| Logical request | `77050aaf61b0bb6d9e2aafa6239930446245462885e840d166b6e26b3095b7f5` |
| Canonical request / wire request identity | `9106859106cee485ba620cac26abd184aa3b4919d31cf28ba0636341e83166c6` |

## Provider result and accounting

The manifest binds run identity
`1ae6cd415ec6be0ad0845c190c0ee530af8acdaf7e4bb14d817723c2482bb000`.
Only `units/a5ca2c069d70b22f438e2cae029c3c88973d42a611076fdba1a76f1def509a71/attempt-001`
exists. TokenMetro returned HTTP 200, then its stream reported an error before completion
after 1,592 SDK chunks and 9,921.048 ms. The attempt recorded 7,153 reasoning
characters, zero visible characters, no finish reason, and no usage. The SDK
error was `APIError: Upstream response stream ended before completion`.
There is no accepted output or local validation result; token use, provider
charge, currency, and billable status are `UNKNOWN`. The client evidence does
not determine whether generation or billing continued upstream.

The primary terminal records `execution_state=unknown`,
`disposition=transport_failure`, `fallback_eligible=false`, and
`fallback_decision=not_eligible`. The checkpoint and terminal summary report
`stopped_unknown_execution`, one provider attempt,
`tokenmetro_policy_403=0`, `jizhi_fallback_issued=0`, and
`stopped_indeterminate=1`. This is the required fail-closed outcome for an
incomplete HTTP 200 stream; a Jizhi attempt or automatic retry would have
violated the route-pair contract.

The saved canonical request SHA-256 is
`9106859106cee485ba620cac26abd184aa3b4919d31cf28ba0636341e83166c6`.
The SDK-captured wire bytes SHA-256 is
`7a9a93f807addf24e87e9fab891baa8ec2b2339f0f39aa556d583160f359fc64`;
their parsed JSON equals the saved canonical request. All 1,592 SDK chunks
and the SDK error are persisted. No completed-response envelope was created
because the stream was incomplete. This run has no second-route request body
to compare.

## Interruption forensics

The pinned OpenAI SDK 3.19.2 raises a plain `APIError` with the provider's
message when an HTTP 200 SSE frame contains a JSON `error` object. Its
transport timeout and connection failures raise different exception classes.
A zero-network MockTransport reproduction with one normal chunk followed by
an SSE `error` frame produced the same `APIError` text. The r2 stream therefore
reached an in-band error event after reasoning had begun; this was not the
client's 300-second timeout, a pre-generation HTTP 403, or a clean
`finish_reason=length` response.

The r2 runner saved the error message but not the SDK exception's structured
`body` or raw SSE error frame. The exact error code and the responsible server
layer are consequently `UNKNOWN`: client evidence cannot distinguish the
TokenMetro gateway from its upstream model channel. The response exposed no
persisted `x-request-id`. For provider log correlation, the safe identifiers
are the stream completion ID `3e7887c2ca9a525ce66fd15875f33b9f`, the
TokenMetro `deepseek-v4.1-flash` Chat Completions route, the request identity
above, and the UTC window `2026-09-26 15:09:31` to `15:09:41`. No credential
is needed in a support report.

The route-pair runner now preserves the SDK's `code`, `type`, and `param`
fields, when present, as bounded and credential-redacted `provider_error_*`
fields in future `sdk_error.json` artifacts. A focused MockTransport test
confirms those fields, secret redaction, and no fallback after a stream error;
the SDK test module passes 28/28. This improves diagnosis only and cannot
retroactively recover r2's discarded error body or repair a remote stream.
The production response remains fail closed. Resolving the upstream break
requires TokenMetro-side log diagnosis and repair, followed by a separately
authorized fresh-root canary; switching to Jizhi after partial generation
would violate the current route-pair contract.

## Offline audit and decision

An offline audit verified all 1,596 referenced artifact hashes and byte
counts, request/route/attempt/checkpoint identities, and absence of both
configured credential values in all 1,601 files. An isolated copy of the root
was resumed with rejecting mock transports: the terminal partial-stream state
blocked further work with zero provider calls. The original root was not
resumed or modified. The r1 manifest and terminal hashes remained unchanged
before the r2 call.

**Real fallback acceptance: NOT PASS / transition unobserved.** The primary
did not return a pre-generation policy 403. Its HTTP 200 partial stream makes
execution indeterminate, so the production runner correctly stopped before
Jizhi. This result does not prove that the historical 403 policy was removed
or identify the cause of the stream interruption. Both r1 and r2 consumed
exactly one TokenMetro attempt each; neither issued a Jizhi attempt. No
bounded production pilot, ordinal21, full build, or further retry followed.
