# P05-W2 TokenMetro ordinal 21 policy-trigger evidence (2026-09-25)

## Scope

This note records the post-run localization of the ordinal 21 pre-generation
403. It does not replace the immutable benchmark or diagnostic roots, and all
payload variants below are diagnostic evidence rather than benchmark results.
Prompt v2, the output schema, frozen source, and Block A were unchanged.

## Evidence

| Claim | Evidence | Status |
| --- | --- | --- |
| The exact frozen DeepSeek ordinal 21 payload is rejected before generation | `data/retrieval/p05-w2-b-v2-sdk-deepseek-ordinal21-policy-fix-retry-20260925-r2`; HTTP 403 `content_policy_violation`, zero chunks, 1,573.226 ms; request/wire body matches the frozen request | Proven for the tested TokenMetro DeepSeek route |
| `钓鱼` is a reproducible trigger factor | `data/retrieval/p05-w2-goal-tokenmetro-deepseek-minimal-diaoyu-20260925-r1`; system `Return one short token.` plus user `钓鱼` returns the same HTTP 403. Single-character controls in the related localization roots passed the pre-generation gate | Proven as a sufficient trigger in the tested TokenMetro DeepSeek route; not proof of the internal rule |
| Removing all occurrences allows the same full payload through the gate | `data/retrieval/p05-w2-goal-tokenmetro-deepseek-ordinal21-replace-all-diaoyu-20260925-r1`; all 15 exact occurrences were replaced, and the request returned HTTP 200 with streamed chunks and `finish_reason=length` | Proven for the tested diagnostic variant; it is not semantic benchmark evidence |
| The route issue is not a universal DeepSeek prohibition | The same `钓鱼` text passed through a separately observed alternate relay route; TokenMetro and alternate-relay behavior therefore differ | Proven by the cross-route diagnostic observation; exact alternate-route provider ownership remains out of scope |
| The original GLM/DeepSeek ordinal 21 failures are route-level pre-generation blocks | Historical GLM and DeepSeek frozen roots and post-policy retries both returned HTTP 403 with zero stream chunks; GLM/DeepSeek ordinal 21 semantic output was never produced | Proven for the tested TokenMetro routes; not model-unavailability evidence |

The replacement run is deliberately not accepted as a semantic result: it
changed source text and stopped at an output-budget boundary. The unchanged
frozen requests remain the comparison authority and are not overwritten.

## What remains unknown

The evidence does not identify the policy owner. It cannot distinguish a
TokenMetro global filter, a model/channel-specific route policy, or upstream
moderation/provider logic. The client-side request was sent and the rejection
arrived before any stream event, but no TokenMetro server logs or deployed
configuration are available locally. The exact matching rule beyond the
tested `钓鱼` span is therefore unknown.

## Engineering decision

The decision below describes the 2026-09-25 checkpoint. The route-pair
mechanism was implemented at `80616c0`; a later synthetic `钓鱼` diagnostic
completed on TokenMetro primary without a 403, and its explicit retry stopped
on an incomplete primary stream. A real fallback transition remains
unobserved. See `p05-w2-deepseek-real-fallback-diagnostic-20260926.md`,
`p05-w2-deepseek-real-fallback-retry-20260926.md`, and current authority in
`docs/current-phase.md`.

Resolve this at the provider-route/policy layer. Do not edit official source
content, Prompt v2, or the schema to evade the rejection. TokenMetro remains
the low-cost primary-route candidate. A fallback route for an explicit
route-policy 403 is an approved future direction only; it is not implemented,
not selected as a production default, and not part of the current contract.
The next paid work is a TokenMetro-only, same-condition DeepSeek versus GLM
comparison with the known trigger text avoided. It is not a restored 3x3
benchmark and does not authorize a full semantic build.
