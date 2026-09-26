# P05-W2 Jizhi direct-Python fresh verification (2026-09-25)

## Result

**EXTERNAL BLOCKER.** This fresh work unit did not reach model generation.
No provider route, model ID, or transport was promoted into the Phase05
provider runner, and ordinal21 was not issued because the required DeepSeek
and GLM canaries did not pass.

The probe read only `JIZHI_API_KEY` from the current process environment. The
credential was never printed, hashed, persisted, placed in a request body,
written to evidence, or written to a Windows environment store. A byte scan of
all eight fresh evidence roots reported `SECRET_NOT_FOUND`.

## Fresh environment and client

- Base URL: `https://jizhiapi.site/v1`
- Python client: standard-library `http.client.HTTPSConnection`, HTTP/1.1;
  no CC Switch, localhost relay, TokenMetro, alternate credential, or fixed
  IP was used.
- Authentication hypothesis: `Authorization: Bearer $JIZHI_API_KEY`.
- Automatic retry: disabled; one request per immutable root.
- DNS resolved `jizhiapi.site` to `104.21.29.22` and `172.67.171.68`.
- Python default TLS and an isolated TLS 1.2 attempt were both tested. The
  final route was not changed to a pinned IP.

## Materially distinct hypotheses and evidence

| Hypothesis | Request | Result | Evidence |
| --- | --- | --- | --- |
| Current key is accepted for catalog discovery | `GET /v1/models` | HTTP 401 `INVALID_API_KEY`; application response, no generation | `data/retrieval/p05-w2-jizhi-direct-python-20260925-r1/models` |
| Responses is the supported minimal generation surface | DeepSeek `POST /v1/responses` | TLS/connection reset, WinError 10054; no HTTP, no output | `data/retrieval/p05-w2-jizhi-direct-python-20260925-r2` |
| Responses routes GLM independently | GLM `POST /v1/responses` | TLS/connection reset, WinError 10054; no HTTP, no output | `data/retrieval/p05-w2-jizhi-direct-python-20260925-r3` |
| Chat Completions is the supported minimal generation surface | DeepSeek `POST /v1/chat/completions` | TLS/connection reset, WinError 10054; no HTTP, no output | `data/retrieval/p05-w2-jizhi-direct-python-20260925-r4` |
| Chat Completions routes GLM independently | GLM `POST /v1/chat/completions` | TLS/connection reset, WinError 10054; no HTTP, no output | `data/retrieval/p05-w2-jizhi-direct-python-20260925-r5` |
| Reset is only a TLS-version branch | DeepSeek `POST /v1/responses`, TLS 1.2 only | TLS/connection reset, WinError 10054; no HTTP, no output | `data/retrieval/p05-w2-jizhi-direct-python-20260925-r6` |
| Standard-library behavior is client-specific or non-streaming-only | DeepSeek streaming Responses with `httpx 0.28.1` | ConnectError/WinError 10054; zero stream bytes, no HTTP | `data/retrieval/p05-w2-jizhi-direct-python-20260925-r7` |
| Provider's official compatibility path requires SDK request construction | DeepSeek Responses with official OpenAI SDK 3.19.2 | SDK `APIConnectionError` over ConnectError/WinError 10054; no HTTP or output | `data/retrieval/p05-w2-jizhi-direct-python-20260925-r8` |

The seven POSTs were potentially billable generation requests; the one
`/models` GET was discovery-only. No request returned usage, a provider
request ID, stream chunks, or generated content. For every POST, finish
reason, usage, output size, mechanical validation, and semantic output are
`UNKNOWN`/not produced; the roots preserve the redacted request metadata and
exception terminal, while the discovery root also preserves the raw 401 body.
The conservative fresh
request count is therefore **7 potentially billable POST attempts + 1
discovery GET**. The previously recorded Phase05 ledger is not used as proof
of this fresh route and remains a separate historical accounting record.

## Frozen ordinal21 identity check

The existing frozen Gemini-shared ordinal21 input was read-only verified before
stopping. No semantic bytes, prompt, schema, source text, projection, record
key, or Retrieval Unit identity was modified.

- Preflight identity: `8601fb2e665fd5f1bc1b7a9ef35a871269e3ea00f0b9a23654656a0bb3838da0`
- Prompt v2 identity: `25ebc483c5dfcfe9b3dc8ddd01001f75e98ff4c2b5327aa789a3af84a068003e`
- Experiment identity: `50f7219556771d8698bc5f85f464bd58d2bdd84c5bbea748178ad45db7255e98`
- Schema identity: `9848b1e07c09b64156497dddc7a1a244f6dd9d7c3d5455c8c0dd0fae380cf312`
- Compilation unit: `93da3256f4bae2001a7e9e8224e7b2f2fe3eee85bf03d917bbf4e9e12195fa91`
- Semantic input identity: `7e7193bb27514d8237aadff170fc5f8826c3441f3feec329be123a9592c6c281`
- Canonical payload SHA-256: `96f107c8a2c72adf3a74102a4261b2098379e701616fdd0feb03589ce86aaa5f`
- Serialized payload size: 13,611 bytes; segment count: 6

Ordinal21 was deliberately not sent because the canary gate was not met.

## Runner and acceptance state

No production/provider-runner code was changed: there is no reliable,
generation-proven direct-Python route to integrate. Focused project tests were
not expanded because the integration gate was not reached; the independent
probe was syntax-checked and its evidence roots are immutable. The remaining
blocker is provider/account/edge state: the current key is rejected by
`/v1/models`, while model POSTs reset before HTTP on both candidate API
surfaces. The provider must supply a currently valid key/account authorization
and a reachable direct Python API path. The minimum reproduction is the
redacted one-request `GET https://jizhiapi.site/v1/models` with Bearer auth,
which returned HTTP 401 `INVALID_API_KEY`.
