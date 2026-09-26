# P05-W2 ordinal 21 route recovery (2026-09-24)

## Scope and accounting

The diagnostic started at Git HEAD `367450bf71aaa1d84fd3e9bde23a9dc6d329cf64`
with an existing uncommitted `docs/current-phase.md` edit. It used the original
frozen Prompt v2 ordinal 21 system/user strings and preserved all prior roots.
The new work issued **one** potentially billable Jizhi request and **four**
potentially billable TokenMetro requests, against separate caps of 20 each.
SDK automatic retries were zero; each new run root contains one issued attempt
and one terminal record. One `/models` GET per provider and local DNS/TCP/TLS
checks were discovery, not model-generation attempts. Provider monetary charges
and the billable status of failed requests remain `UNKNOWN`.

## Actual routes and results

| Provider / model | Actual API surface | Connectivity and generation | Ordinal 21 policy / mechanical result | New potentially billable attempts | Evidence |
| --- | --- | --- | --- | ---: | --- |
| Jizhi / DeepSeek | `https://jizhiapi.site/v1/chat/completions`, SDK streaming | Small canary: no HTTP, `ConnectionResetError` WinError 10054 during TLS/connect, zero chunks. Earlier exact frozen request also had `APIConnectionError`, zero chunks. | Policy and local validation `UNKNOWN`; generation not reached. | 1 | `data/retrieval/p05-w2-goal-jizhi-deepseek-canary-20260924-r1`; prior `p05-w2-b-v2-sdk-deepseek-ordinal21-jizhi-20260924-r1` |
| Jizhi / GLM | Same Chat Completions route | Earlier exact frozen request: HTTP 404 `model_not_found`, no chunks; new `/models` discovery: HTTP 401 `INVALID_API_KEY`. | Policy and local validation `UNKNOWN`; generation not reached. | 0 | Prior `data/retrieval/p05-w2-b-v2-sdk-glm-ordinal21-jizhi-20260924-r1`; new `data/retrieval/p05-w2-goal-jizhi-models-20260924-r2` |
| TokenMetro / DeepSeek | `https://tokenmetro.com/v1/responses`, SDK streaming; prior Chat Completions route | Small Responses canary: HTTP 200, completed, 37 input / 59 output tokens. Frozen ordinal 21: HTTP 403, zero events. | `content_policy_violation` before generation; local validator not reached. | 2 | `data/retrieval/p05-w2-goal-tokenmetro-deepseek-responses-canary-20260924-r1`; `data/retrieval/p05-w2-goal-tokenmetro-deepseek-ordinal21-responses-20260924-r1` |
| TokenMetro / GLM | Same Responses route; prior Chat Completions route | Small Responses canary: HTTP 200, completed, 19 input / 49 output tokens. Frozen ordinal 21: HTTP 403, zero events. | `content_policy_violation` before generation; local validator not reached. | 2 | `data/retrieval/p05-w2-goal-tokenmetro-glm-responses-canary-20260924-r1`; `data/retrieval/p05-w2-goal-tokenmetro-glm-ordinal21-responses-20260924-r1` |

The TokenMetro catalog root
`data/retrieval/p05-w2-goal-tokenmetro-models-20260924-r1` returned HTTP 200,
listed both exact model IDs, and advertised `openai` and `openai-response`
endpoint types. This justified the Responses canaries. Both original Chat
Completions ordinal 21 controls and their later post-policy retries had already
returned the same HTTP 403 with zero chunks. The Responses variant preserved
the original system and user UTF-8 bytes in `instructions` and `input`;
preflight checked prompt, schema, source, semantic-input, and compilation-unit
identities against the original roots. `max_output_tokens=500000` retained the
numeric ceiling. The API surface and request field names changed, so these are
**transport variants**, not full-body byte-identical Chat Completions retries.
Both original roots' referenced artifact hashes and the new roots' artifact
hashes passed local checks; no runtime API key was found in the new roots.

## Diagnosis and boundary

Jizhi DNS resolution and TCP port 443 succeeded, while `curl` and the SDK
observed TLS connection reset. A later `/v1/models` GET reached the server but
returned `401 INVALID_API_KEY`; an earlier preserved GET had returned HTTP 200
with only 11 Gemini IDs. The present key/route combination is therefore not a
usable DeepSeek or GLM route. The reason for the changing TLS/auth behavior and
the earlier GLM account-group 404 is `UNKNOWN`. The process key is set, but no
user- or machine-scope Jizhi key exists to independently compare; a read-only
snapshot of the current CC Switch database had no active Jizhi provider entry.
It does not resolve the previously observed CC Switch versus project difference.
No Jizhi frozen policy control was issued after these blockers.

TokenMetro's small Chat Completions canaries from earlier evidence and the new
Responses canaries establish account, model, and both advertised API surfaces
for small input. The frozen ordinal 21 content receives the same gateway error
on both surfaces, for both models, before streaming. The existing outgoing-wire
audit excludes a locally fabricated 403. The exact selected upstream/account
channel and whether the rejection is at TokenMetro or its upstream are
`UNKNOWN`; neither is exposed by these responses. Further phrase/record-key
localization, payload edits, or same-route repeats would not resolve the route
or policy blocker and were not performed.

No route currently supplies a DeepSeek or GLM ordinal 21 semantic output for
local validation or the three-model comparison. Gemini's prior results remain
separate mechanical evidence; there is no model winner or production/default
adoption from this diagnostic.
