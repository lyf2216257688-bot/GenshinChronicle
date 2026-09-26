# P05-W2 DeepSeek real route-pair fallback diagnostic (2026-09-26)

## Scope and fixed request

One paid diagnostic invoked the production `run_sdk_route_pair` entry from
checkpoint `80616c0`. The input was synthetic and independent of the frozen
sample: unit `diagnostic-only-diaoyu-route-pair-20260926-r1`, source identity
`diagnostic-only:diaoyu-20260926-r1`, and one segment
`diagnostic-diaoyu-s1` with text `钓鱼`. This is not ordinal21, a formal
semantic unit, or real Genshin corpus acceptance. Prompt v2 and the output
schema were loaded unchanged; no source, Block A, or full build was touched.

The zero-network preflight found a new root, both route credentials set,
TokenMetro/Jizhi profiles for `deepseek-v4.1-flash`, `openai` 3.19.2, and
`httpx` 0.28.1. The call used Chat Completions, `stream=true`,
`max_tokens=16384`, timeout 300 seconds, and no automatic retry. Preflight
fixed these identities before the call:

| Binding | Identity |
| --- | --- |
| Prompt v2 | `25ebc483c5dfcfe9b3dc8ddd01001f75e98ff4c2b5327aa789a3af84a068003e` |
| Output schema | `9848b1e07c09b64156497dddc7a1a244f6dd9d7c3d5455c8c0dd0fae380cf312` |
| Synthetic payload SHA-256 | `976cae69a49c7de5f8c50992074d3cf4e5d8098650b3bb28719d00bc167e6e00` |
| Semantic input | `703a1a06492628a4ce4e9bbec87296542542ff1230736e5151275bf325831dda` |
| Logical request | `39307592810eb30e138a7434e5c59a2fac1df96094bc3286a2bdd0ca5a5c5439` |
| Canonical request / wire request identity | `cb2fe4d23b99ef0142d74ba8b2d93a05f6ec17973ac9dd6a924dacd4ef8f1166` |

## Observed provider result

Immutable evidence is under
`data/retrieval/p05-w2-deepseek-route-fallback-diagnostic-20260926-r1`.
Its `manifest.json` binds run identity
`18da8d6587528192ea5455c46fd7a938adf519eff8e3618254f17c3b082d5857`.
Only `units/8b3b6c518f6f40d9d03e9b39abd180e34c95fa1fdcbad683e2956a8714c516e0/attempt-001`
exists. TokenMetro returned HTTP 200 and 770 stream chunks in 5,997.486 ms;
`finish_reason=stop`, stream completion, and local JSON/schema/source-binding
validation passed. The output contains one topic item, `钓鱼`, bound to the
diagnostic segment. This is a mechanical local-contract pass, not a semantic
quality or production adoption result.

The terminal has `fallback_eligible=false` and
`disposition=accepted_for_local_contract`. There was no policy 403 and no
Jizhi attempt. `terminal_summary.json` and `checkpoint.json` both report one
provider attempt, `tokenmetro_policy_403=0`, `jizhi_fallback_issued=0`,
`accepted_primary=1`, and `accepted_fallback=0`. There was no retry or second
diagnostic request. TokenMetro reported 1,197 input tokens, 769 output tokens,
681 reasoning tokens, 896 cached tokens, and credit `0.05`; credit currency
and billable status remain `UNKNOWN`. Jizhi usage and charge were not incurred
by this run.

The saved canonical request SHA-256 is
`cb2fe4d23b99ef0142d74ba8b2d93a05f6ec17973ac9dd6a924dacd4ef8f1166`.
The SDK-captured wire bytes have SHA-256
`50ee9aaa286c968abeb7a78ac7b13c21094aa88d10bc9825d834b3223146bbdf`;
their parsed JSON equals the saved canonical request. The saved
`raw_response.bin` SHA-256 is
`32ebda7189a26122d1414fdfa60f1d16b278d577b62b172cafabc07ec3400b31`.
For this successful stream, that file is the runner's reconstructed Chat
Completions envelope; all 770 SDK chunks are separately persisted. This run
has no second-route body to compare byte for byte.

## Offline audit and decision

An offline audit verified all 776 referenced artifact hashes and byte counts,
the request, unit, route, checkpoint, and terminal identities, and absence of
both configured credential values in all 781 files. Provider-free replay
passed with zero network calls. A copy under `.local` was resumed with
rejecting mock transports: it skipped the accepted unit with zero provider
calls; the immutable original was not resumed or modified. Focused SDK tests
passed 27/27 before the paid call.

**Real fallback acceptance: NOT PASS / transition unobserved.** The primary
route completed successfully, so the production runner correctly did not
enter its fallback branch. This does not invalidate the 2026-09-25 policy 403
evidence for a different request shape and time, and does not show that the
old route policy has been removed. The cause of the changed behavior is
`UNKNOWN`. The route-pair implementation retains provider-free fallback tests
and two separate route canaries, but this work does not prove a real
TokenMetro-to-Jizhi transition. The engineering evidence requested before a
bounded production pilot is incomplete; the pilot remains separately
authorized and was not started.

An explicitly authorized r2 retry later stopped on an incomplete TokenMetro
primary stream without fallback. Its separate evidence and accounting are in
`p05-w2-deepseek-real-fallback-retry-20260926.md`; this r1 root remains
unchanged.
