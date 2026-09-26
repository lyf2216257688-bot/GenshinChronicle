# P05-W2 Jizhi path recovery continuation (2026-09-25)

## Accounting

The previous route-recovery work unit consumed one new Jizhi provider attempt:
the minimal streaming Chat Completions canary at
`data/retrieval/p05-w2-goal-jizhi-deepseek-canary-20260924-r1`. This continuation
uses that as attempt 1 of the existing 20-request Jizhi cap. It issued ten
additional model-endpoint attempts, each in its own immutable root, with SDK
automatic retries set to zero. The conservative continuation ledger is therefore
**11/20**. The two original frozen ordinal 21 Jizhi attempts and discovery GETs
remain historical roots with their original accounting and are not merged into
this ledger. The curl `/models` check was discovery-only and is not counted as a
paid model attempt. No TokenMetro request was issued.

## Path matrix

| Attempt | Path variable changed | Result | Evidence |
| ---: | --- | --- | --- |
| 1 (prior unit) | SDK Chat Completions, streaming, direct Jizhi | WinError 10054 before HTTP, 0 chunks | `data/retrieval/p05-w2-goal-jizhi-deepseek-canary-20260924-r1` |
| 2 | CC Switch local relay `127.0.0.1:15721/v1/responses`, non-streaming, DeepSeek | Local relay HTTP 400 `billing_model_not_configured`; provider `月卡`; no Jizhi upstream | `data/retrieval/p05-w2-goal-jizhi-local-relay-deepseek-canary-20260925-r1` |
| 3-4 | Direct Jizhi Responses, non-streaming, HTTPX, DeepSeek/GLM | HTTP 401 `INVALID_API_KEY` for both | `data/retrieval/p05-w2-goal-jizhi-deepseek-responses-direct-20260925-r1`, `data/retrieval/p05-w2-goal-jizhi-glm-responses-direct-20260925-r1` |
| 5-6 | Official OpenAI SDK Responses, non-streaming, DeepSeek/GLM | WinError 10054 before HTTP for both | `data/retrieval/p05-w2-goal-jizhi-deepseek-sdk-responses-20260925-r1`, `data/retrieval/p05-w2-goal-jizhi-glm-sdk-responses-20260925-r1` |
| 7 | Direct HTTP Responses with SDK-observed `Accept: application/json` and `User-Agent: OpenAI/Python 3.19.2` | WinError 10054 before HTTP | `data/retrieval/p05-w2-goal-jizhi-deepseek-header-variant-20260925-r1` |
| 8 | Direct HTTP Responses with only `User-Agent: OpenAI/Python 3.19.2` added | WinError 10054 before HTTP | `data/retrieval/p05-w2-goal-jizhi-deepseek-user-agent-20260925-r1` |
| 9 | Direct HTTP Responses with only `Accept: application/json` added | WinError 10054 before HTTP | `data/retrieval/p05-w2-goal-jizhi-deepseek-accept-20260925-r1` |
| 10-11 | Direct HTTP streaming Responses, `Accept: text/event-stream`, DeepSeek/GLM | WinError 10054 before HTTP, 0 lines for both | `data/retrieval/p05-w2-goal-jizhi-deepseek-responses-stream-20260925-r1`, `data/retrieval/p05-w2-goal-jizhi-glm-responses-stream-20260925-r1` |

A separate direct `curl.exe` `/v1/models` discovery used HTTP/1.1 and the
process Jizhi key. Its first default connection reset; an IPv4/TLS 1.2 retry
reached HTTP 401 `INVALID_API_KEY` and was persisted at
`data/retrieval/p05-w2-goal-jizhi-curl-models-20260925-r1`.

## CC Switch success path and discrepancy

Read-only historical CC Switch logs identify the success path as Codex local
proxy takeover with an OpenAI **Responses** request to
`https://jizhiapi.site/v1/responses`, not Chat Completions. On 2026-09-23,
provider `b7727667-f885-4356-866b-3d002147f7ae` recorded three DeepSeek
`deepseek-v4.1-flash` HTTP 200 responses with non-zero input/output usage.
The same log interval records a GLM model-account 404 for `gpt-5.6-luna` and
separate Gemini calls; it does not prove a Jizhi GLM 200.

The historical provider configuration uses `wire_api = "responses"`,
`requires_openai_auth = true`, and the same Jizhi base URL. The local relay is
currently healthy, but `/status` reports the active provider as the non-Jizhi
`月卡` profile, whose upstream is `app.awakenob.com/codex/v1`; this explains the
local `billing_model_not_configured` result. The current CC Switch database has
no Jizhi provider active.

Non-secret auth metadata comparison shows the process `JIZHI_API_KEY` matches
the historical Jizhi Gemini profile (`736b...`), while the historical Jizhi
DeepSeek-success profile (`b772...`) has a different credential value. The
alternate value is stored secret material and was not extracted, reused, or
printed. This is the concrete unresolved discrepancy behind direct 401s and
why the historical DeepSeek success path cannot be replayed under the current
authorization without either changing CC Switch selection or obtaining the
correct profile credential.

## Stopping boundary

The actual CC Switch success surface is identified as local relay takeover plus
Responses, and the main client differences were tested: direct HTTP, official
SDK, non-streaming, streaming, SDK headers, and the running relay. A 10054 is
therefore scoped to the tested header/transport paths; it is not being treated
as a provider-wide failure. No remaining path can be tested without one of the
following out-of-scope actions: modifying CC Switch configuration to select the
historical Jizhi provider, using its different stored secret, or obtaining a
new credential from the user. No Jizhi path reached model generation under the
current process key, so frozen ordinal 21 policy behavior remains UNKNOWN for
both DeepSeek and GLM. No semantic input, schema, source, production default,
or TokenMetro evidence was changed.
