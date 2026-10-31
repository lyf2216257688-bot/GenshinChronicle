# Phase05 Card Chat Strict Function Calling Qualification — 2026-10-31

## Context

User authorized a single narrow paid compatibility test after Responses JSON Schema returned HTTP 400. One sealed T01 天空之翼 input was reused from the five-case blind preparation, without editing production code or historical records. Strict function calling is an official DeepSeek Beta feature requiring the official `/beta` base_url; this test was against TokenMetro's existing `/v1/chat/completions`, so support is route-specific, and actual enforcement is not independently attested.

## Exact conditions

- Model requested: `deepseek-v4.1-flash`; Chat Completions; nonstream; `thinking.type=disabled`; `max_tokens=32000`.
- Tool: forced `submit_navi_card`, `tools[0].function.strict=true`, full Card field structure. Beta-unsupported validation keywords (`minLength`, `maxLength`, `minItems`, `maxItems`, `uniqueItems`) were removed only from the transmitted tool parameters; the original full Schema, evidence binding and exact quotations were enforced locally after generation.
- Shared `genshin_corpus.model_calls` persistent ledger, max 2 attempts solely to permit proven-not-sent recovery; unknown/rejected requests are not resent. Actual result had **one request, zero retries**.

## Measured result

- HTTP 200, `finish_reason=tool_calls`, exactly one `submit_navi_card` call, model identity `deepseek-v4.1-flash`.
- Full Card arguments passed local JSON Schema, alias/source ID binding, exact anchor quote and local data integrity validation. Final state `CONTENT_VERIFIED`; Cards are still `INACTIVE/NOT_REVIEWED`.
- Usage 2,956 input + 13,839 output = **16,795 tokens**, actual fee UNKNOWN.
- Previously sealed T01 Chat + JSON Object (thinking disabled, 32k) used 2,114 input + 9,177 output = **11,291 tokens** and also passed.
- New tool result: 28 observations, 5 interpretations, 5 hypotheses. Previous JSON Object: 21 observations, 4 interpretations, 2 hypotheses. Both preserve main 风神—特瓦林 narrative; new version adds low-value speculative hypotheses; no persuasive semantic quality gain demonstrated.

## Decision

**Keep default for NEW Card runs: Chat Completions, JSON Object, disabled thinking, 32k cap, local full Schema/source validation, existing unified model_calls.** Strict function calling is demonstrably transport-compatible in this single Metro trial, but is NOT automatically preferred: ~49% higher total tokens, no clear semantic improvement, and tool-call parsing integration would be needed. One locally valid output does not prove server-side strict enforcement for all requests. Responses JSON Schema remains unavailable on this Metro route (prior A HTTP 400). Low thinking remains unverified and not the default. Long-document B is deferred.

### Artifacts

- Raw request, raw response, result, exact arguments and local verified Card: `data/retrieval/p05-card-strict-tools-20261031-r1/call/`
- Isolated experiment script and preflight input: `data/retrieval/p05-card-strict-tools-20261031-r1/`
- Existing user-selected profile: `config/phase05-card-api-profile-20261031.json`.
- No production source update, no full-corpus Card generation, no commit or push.

Official reference: https://api-docs.deepseek.com/guides/tool_calls/
