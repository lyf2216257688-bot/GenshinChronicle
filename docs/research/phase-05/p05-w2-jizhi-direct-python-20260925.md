# P05-W2 Jizhi Python-direct recovery (2026-09-25)

## Accounting

The prior Jizhi continuation conservatively accounted **11/20** provider
attempts. The user added 50 requests, changing the cumulative hard cap to
**70** without resetting the ledger. This direct-only continuation issued
seven new model-endpoint attempts, each with automatic retry set to zero and a
unique immutable evidence root. The current conservative paid/provider ledger
is therefore **18/70**, with **52** remaining. Discovery GETs are tracked
separately because they did not invoke model generation. No TokenMetro request
was made.

The initial local-only root
`data/retrieval/p05-w2-goal-jizhi-pinned-ip-models-20260925-r1` failed before
send during secret-redaction validation and is not a provider attempt.

## Evidence-led route selection

Fresh DNS returned `104.21.29.22` and `172.67.171.68`. Python TLS to
`104.21.29.22` reset during handshake; TLS 1.2 and TLS 1.3 to
`172.67.171.68` completed. The diagnostic therefore pinned the latter IP while
preserving TLS SNI and HTTP Host as `jizhiapi.site`, using Python
`http.client`/HTTP/1.1. The script is
`.local/p05_jizhi_pinned_ip.py`; it stores redacted wire metadata and complete
exception chains without storing the key.

Public Jizhi entry and model-plaza responses exposed the configured API base
`https://jizhiapi.site/v1`, the frontend's Bearer session convention, and exact
target IDs in public groups 93 and 48. Relevant discovery roots:

- `data/retrieval/p05-w2-goal-jizhi-public-entry-20260925-r1`
- `data/retrieval/p05-w2-goal-jizhi-public-bundle-20260925-r1`
- `data/retrieval/p05-w2-goal-jizhi-public-model-plaza-20260925-r1`
- `data/retrieval/p05-w2-goal-jizhi-model-plaza-20260925-r1`
- `data/retrieval/p05-w2-goal-jizhi-model-plaza-api-20260925-r1`
- `data/retrieval/p05-w2-goal-jizhi-pinned-ip-models-20260925-r2`

The pinned `/v1/models` request reached Cloudflare/Jizhi over TLS 1.3 and
HTTP/1.1 but returned HTTP 401 `INVALID_API_KEY`. The key's non-secret shape
was checked as `sk-` prefix, no whitespace; the value was not persisted.

## New provider attempts

| Attempt | Model | Python path and changed variable | Result | Evidence root |
| ---: | --- | --- | --- | --- |
| 12 | `deepseek-v4.1-flash` | pinned IPv4, HTTP/1.1, `/v1/responses`, minimal non-streaming | HTTP 401 `INVALID_API_KEY` | `data/retrieval/p05-w2-goal-jizhi-deepseek-pinned-ip-responses-20260925-r1` |
| 13 | `glm-5.3-flash` | same Responses path | HTTP 401 `INVALID_API_KEY` | `data/retrieval/p05-w2-goal-jizhi-glm-pinned-ip-responses-20260925-r1` |
| 14 | `deepseek-v4.1-flash` | pinned route, `/v1/chat/completions` | HTTP 401 `INVALID_API_KEY` | `data/retrieval/p05-w2-goal-jizhi-deepseek-pinned-ip-chat-20260925-r1` |
| 15 | `glm-5.3-flash` | same Chat Completions path | HTTP 401 `INVALID_API_KEY` | `data/retrieval/p05-w2-goal-jizhi-glm-pinned-ip-chat-20260925-r1` |
| 16 | `deepseek-v4.1-flash` | documented public `/responses` route without `/v1` | HTTP 401 `INVALID_API_KEY` | `data/retrieval/p05-w2-goal-jizhi-deepseek-root-responses-20260925-r1` |
| 17 | `glm-5.3-flash` | same root Responses route | HTTP 401 `INVALID_API_KEY` | `data/retrieval/p05-w2-goal-jizhi-glm-root-responses-20260925-r1` |
| 18 | `gemini-3.8-flash` control | historical successful catalog ID, pinned `/v1/responses` | HTTP 401 `INVALID_API_KEY` | `data/retrieval/p05-w2-goal-jizhi-gemini-pinned-ip-responses-20260925-r1` |

All seven attempts reached the Jizhi application layer and persisted complete
redacted HTTP response metadata. None reached generation, returned usage, or
produced stream chunks.

## Conclusion and stopping reason

The current `JIZHI_API_KEY` failed uniformly on the pinned Python route for
`/v1/models`, `/v1/responses`, `/v1/chat/completions`, and the documented root
`/responses` route. The Gemini control also returned 401, so the failure is not
specific to DeepSeek or GLM model routing. Public model-plaza evidence confirms
the target IDs exist, but does not grant this key access.

The previous SDK/header/stream paths remain separately evidenced, including
their full WinError 10054 chains. Combining those results with the pinned
raw-socket HTTP 401s rules out Python SDK serialization, endpoint surface,
model ID, TLS handshake on the reachable edge, and HTTP/1.1 transport as the
remaining primary cause. The remaining plausible cause is current Jizhi
key/account/group authorization or an external edge/session state discrepancy;
the earlier project-side `/v1/models` HTTP 200 with Gemini-only IDs remains
unexplained by current evidence.

No Python direct path reached model generation. Neither frozen ordinal21 input
was executed in this continuation, so Jizhi policy/content blocking remains
`UNKNOWN` for both models. The frozen roots and semantic inputs were untouched.
Further progress requires a provider-side/account-state change or a different
credential, which is outside this scope. CC Switch, its credentials, and its
local relay were not used or modified in this continuation.
