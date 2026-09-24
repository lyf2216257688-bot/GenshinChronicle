# P05-W2 Prompt v2 SDK three-model comparison (2026-09-24)

## Boundary and identities

The aborted direct-HTTP r1 root remains immutable at
`data/retrieval/p05-w2-b-v2-three-model-20260924-r1` (four consumed attempts).
The fresh SDK comparison root is
`data/retrieval/p05-w2-b-v2-sdk-three-model-20260924-r2`.

- Benchmark identity: `2f2a2a95c64920fac9ee54834f2bac57ac197c3ede0b9bf848599119fe7df237`.
- Run identity: `2398e4a2189a06d483399964c6adf4c41588aaf5b8eabd7d93ca5a93a644d9a0`.
- Unchanged Prompt v2 identity: `25ebc483c5dfcfe9b3dc8ddd01001f75e98ff4c2b5327aa789a3af84a068003e`.
- Unchanged output schema identity: `9848b1e07c09b64156497dddc7a1a244f6dd9d7c3d5455c8c0dd0fae380cf312`.
- Frozen inputs: Gemini preflight ordinals 16, 20, 21 from `p05-w2-live-preflight-20260923-r2`.
- Transport: `openai` Python SDK 3.19.2, `base_url=https://tokenmetro.com/v1`, Chat Completions, `max_retries=0`.
- Request body: exactly `model`, identical `system`/`user` messages per unit, and `max_tokens=16384`.

The SDK benchmark has its own request/config identity. It does not change the
historical B JSON-object request contract, Prompt v2, output schema, sample,
validator, source binding, or Block A behavior. The zero-network SDK mock
preflight verified the nine planned wire bodies, exact messages, request cap,
and positive/negative local source-binding checks. It issued zero provider
requests. A unit was attempted once; there was no retry or batching.

## Terminal results

| Model | Ordinal | HTTP / finish | Local disposition | Latency ms | Input / output tokens | Reported credit |
| --- | ---: | --- | --- | ---: | --- | ---: |
| Gemini | 16 | 200 / stop | accepted for local contract | 16196.853 | 1617 / 1616 | UNKNOWN |
| DeepSeek | 16 | 200 / stop | accepted for local contract | 62060.520 | 1341 / 13576 | 1.59 |
| GLM | 16 | 503 / none | transport failure | 241524.116 | UNKNOWN | UNKNOWN |
| Gemini | 20 | 200 / stop | accepted for local contract | 31020.493 | 4084 / 12410 | UNKNOWN |
| DeepSeek | 20 | 200 / length | output budget failure; strict JSON parse rejected | 65576.338 | 3515 / 16384 | 1.97 |
| Gemini | 21 | 200 / stop | accepted for local contract | 14358.585 | 5688 / 2316 | UNKNOWN |

DeepSeek ordinal 16 used 12,622 thinking tokens within its 13,576 completion
tokens. DeepSeek ordinal 20 used 14,414 thinking tokens within its 16,384
completion tokens. Its truncated content did not reach schema, local-reference,
or source-binding validation. GLM's raw HTTP 503 response reported "Service
temporarily unavailable"; its ordinal 20/21 were not attempted. DeepSeek
ordinal 21 was not attempted. These are mechanical outcomes, not semantic
quality judgments. Reported `credit` has no currency or billable status in the
response, so monetary cost remains UNKNOWN.

The run consumed six of the nine permitted requests. `gate_16.json` admitted
Gemini and DeepSeek to ordinal 20; `gate_20.json` admitted Gemini alone to
ordinal 21. Each attempt preserves a sanitized request, raw SDK/HTTP response,
terminal ledger, latency, usage, applicable canonical output, and validation
disposition. Artifact hashes and absence of the configured credential in the
output root were checked after the run.

## Review boundary

`review_bundle.html` places each frozen source unit beside all three model
columns, including original raw responses and local validation. The three
Gemini outputs and DeepSeek ordinal 16 passed mechanical gates only. This run
does not establish semantic acceptance, a complete three-model quality ranking,
a winning model, corpus-scale build readiness, or production adoption.
