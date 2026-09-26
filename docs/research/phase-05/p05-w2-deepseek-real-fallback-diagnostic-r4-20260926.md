# P05-W2 DeepSeek real route-pair diagnostic r4 (2026-09-26)

## Scope and fixed request

This was one separately authorized diagnostic through the production
`run_sdk_route_pair` entry at checkpoint `80616c0`. It used a fresh root,
`data/retrieval/p05-w2-deepseek-route-fallback-diagnostic-20260926-r4`, and one
synthetic unit only:
`diagnostic-only-diaoyu-route-pair-20260926-r4`, source identity
`diagnostic-only:diaoyu-20260926-r4`, segment `diagnostic-diaoyu-s1`, and text
`钓鱼`. It did not use ordinal21, formal source text, or a semantic pilot.

The zero-network preflight confirmed that the root did not exist, both runtime
credentials were set, the TokenMetro and Jizhi profiles selected
`deepseek-v4.1-flash`, and the pinned SDK versions were `openai 3.19.2` and
`httpx 0.28.1`. Prompt v2, the output schema, `stream=true`,
`max_tokens=16384`, the 300-second timeout, and zero automatic retry were
unchanged. The fixed identities were:

| Binding | Identity |
| --- | --- |
| Prompt v2 | `25ebc483c5dfcfe9b3dc8ddd01001f75e98ff4c2b5327aa789a3af84a068003e` |
| Output schema | `9848b1e07c09b64156497dddc7a1a244f6dd9d7c3d5455c8c0dd0fae380cf312` |
| Payload SHA-256 | `022e6109a45a9b61af68ea6bc8353f105275057634aa5f8b8250b06b2b96b20e` |
| Semantic input | `8fc21f89abac7a11a5b6ce4fe6e6ee9386d79b3314a026fb0ba3f5d1db110d39` |
| Logical request | `3f91de075546aba04693f03dd7e9ff48f5c780ee08b227aa81fb579fce1296c1` |
| Canonical request | `1b942f3a63929ae690b15b219fac27d30e5d42ca0ee5bf50bf9c24a24d9ebdf2` |

The route-profile identities fixed during preflight were TokenMetro
`76415d9d19435d5b6e12cc3a94160093cf9c55703ad349effa8123dd6a7b3d97` and
Jizhi `28c060d7621d9be39c65dbe3e88da6e4cbe019cdcb323f5700e475f08e438d19`.

## Provider result

Manifest/run identity is
`691879e9c0fd946313cd45835930060bdd391c5abc65b12122b83f96c9300b14` and the
terminal-summary identity is
`8e7200a81d5f570fd70107cfb37a9ae60c2e94829a862d2c5ed65fc262953674`.
Only `attempt-001` was issued, on TokenMetro primary. It returned HTTP 200
with a complete stream and `finish_reason=stop` after 938 chunks and
`6920.491 ms`; the provider request ID was
`b75424db28f1aef4f4f9d766ad930a21`. The generated output had 282 visible
characters and 3,760 reasoning characters. Local JSON/schema validation and
diagnostic segment binding passed, so the terminal disposition was
`accepted_for_local_contract`.

The attempt was not a pre-generation policy response:
`fallback_eligible=false`, `fallback_decision=not_eligible`, and
`sdk_error_type=null`. The terminal summary records one provider attempt,
`tokenmetro_policy_403=0`, `jizhi_fallback_issued=0`,
`accepted_primary=1`, and `accepted_fallback=0`. Jizhi was not called, and no
second route body exists to compare. The saved request bytes hash is the
canonical identity above; the SDK-captured wire bytes hash is
`c03eaf984cc09d8ef0a575e402d13b2c87b0b869da9b78b7a59ed1e76db3b921`.
The reconstructed response hash is
`6b604597872425accb9038149cf11217d3eddbd3a3b25a748ef9c4d5389b7d4f`.

TokenMetro reported 1,199 input tokens, 937 output tokens, 849 reasoning
tokens, and credit `0`. Currency and billable status are `UNKNOWN`; no Jizhi
usage or charge was incurred.

## Offline verification

The r4 audit verified 944 persisted artifact descriptors and byte counts across
949 files, route/request/terminal identities, checkpoint and terminal-summary
identity, and absence of both configured credential values. Local replay made
zero network/provider calls. A copied root resumed with rejecting mock
transports, skipped the accepted unit, and made zero provider calls; the
original evidence root was not resumed or modified.

## Decision

**Real fallback acceptance: NOT PASS / transition unobserved.** This fresh
diagnostic again completed on TokenMetro primary, so the strict policy-403
condition did not occur and the runner correctly did not switch to Jizhi. The
r1, r2, r3, and r4 observations show that the synthetic request can complete
or stop on the primary route depending on the observed provider stream, but do
not establish that the historical policy trigger was removed or explain its
intermittency. This result does not authorize a bounded production pilot, an
ordinal21 run, a full build, or another retry.
