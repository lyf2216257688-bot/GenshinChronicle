# P05-W2 Scoped Semantic Build r1 (2026-09-27)

## Scope and freeze

This is the separately authorized paid/live approximately 200-unit scoped
Semantic Build after the 2026-09-27 runtime scale-hardening checkpoint. It
does not change Prompt v2, the authoritative output schema, source inputs,
Block A, model selection, route selection, or the Phase 05 semantic acceptance
boundary. No full build, sharding, concurrency, payload slimming, index, or
journal optimization was run.

The source lineage is the accepted provider-free U1/r7 root
`data/retrieval/p05-w2-u1-provider-free-20260922-r7`, with semantic build
identity
`45520b23490a1c66289476c71dbe55c38cd206167342ebeb5894721055c59677`.
The fresh RU-bound, non-partial, non-oversized corpus population is 206,849
units. Applying the existing 16,000-byte provider-facing input ceiling leaves
205,208 units in the bounded sample-admission pool. The separate corpus
accounting remains 2,342 RU-unbound units and 2,002 `oversized_unresolved`
units; 1,641 additional units exceeded the sample input ceiling. The
206,849-unit population contains 15,711 distinct source records.

The deterministic sample has 200 units from 200 distinct source records. It
sorts by a SHA-256 derived from the fixed semantic build identity and unit ID,
uses one unit per source record, and allocates across 31 strata defined by
`unit_scope`, source-derived `kind`/category, and serialized input-size bucket
(`lt1k`, `1to4k`, `4to8k`, `8to16k`). The frozen identities are:

- sample identity: `72e1ec582cfb98c06c4ff15d74c2abe5f50d8c7f7e53381d3d224b5d7434577a`
- provider-free preflight identity: `01622a5c3d103f5389d68567c31cedc9809058ff0bc98fc030abb86d46e099e7`
- live freeze identity: `ec72dbf572d81ef3e60e333eb2ee301aaa59f2ea7e4749eec551a1ed8cbb1ec9`
- run identity: `84ee8e1eab5d3f2bc3119a96ec1bd68c149828635e9fbef4f0adf90dc285a935`

The freeze was persisted before the first provider request with the output
root, unit/request identities, Prompt v2 identity
`25ebc483c5dfcfe9b3dc8ddd01001f75e98ff4c2b5327aa789a3af84a068003e`, schema
identity `9848b1e07c09b64156497dddc7a1a244f6dd9d7c3d5455c8c0dd0fae380cf312`,
model `deepseek-v4.1-flash`, TokenMetro primary, Jizhi fallback, streaming
Chat Completions, `max_tokens=500000`, and automatic retry disabled.

## Live result

The immutable run root is
`data/retrieval/p05-w2-scoped-semantic-20260927-r1/run`.

- 200/200 logical units completed with `accepted_for_local_contract`.
- 200 TokenMetro primary attempts; 0 Jizhi fallback attempts.
- 0 failed units, 0 execution-UNKNOWN units, 0 policy-403 transitions.
- No fallback was artificially induced. Real automatic handoff remains
  **UNOBSERVED**, not PASS or FAIL.
- Every accepted output passed strict JSON parsing, authoritative schema
  validation, exact segment coverage/source binding, and local-reference
  validation. This is mechanical contract evidence only.

Reported TokenMetro usage totals are 463,121 input, 2,217,052 output, and
1,965,960 reasoning tokens. Reported credit totals 111.60 in the provider's
credit field. Every terminal marks currency and billable status as `UNKNOWN`,
so verified monetary cost is `UNKNOWN`; reported credit is retained as raw
provider accounting, not converted into currency.

Measured latency was 3,052.410 ms minimum, 49,498.560 ms p50,
46,244.646 ms mean, 76,621.282 ms p90, and 108,739.676 ms maximum. There
were 2,213,505 stream chunks, 678,437 visible characters, and 6,832,724
reasoning characters. The run contains 1,803 files and 896,466,466 logical
bytes: 200 `stream.jsonl` files account for 881,535,308 bytes; 200 raw
responses account for 9,075,223 bytes; and 200 canonical outputs account for
912,941 bytes. Credentials were not present in persisted artifacts.

## Offline audit and resume

`audit_sdk_route_integrity()` passed over all 200 attempts and accepted
outputs with zero provider/network calls. It verified manifest/run identity,
route configuration, request and wire identities, artifact hashes, stream
framing, provider output replay, and source bindings.

An isolated copy of the immutable root was resumed with the same frozen units,
Prompt/schema/model/routes, and rejecting mock-free runtime path. Resume
reported `complete`, 200 accepted units, 200 historical attempts, and
`provider_attempts_this_invocation=0`; no request was reissued.

## Semantic spot review

A deterministic spread of 14 accepted outputs across the frozen strata was
reviewed offline together with aggregate output structure. Across all 200
outputs there were 387 facts, 1,092 mentions, 462 relations, 407 topics, and
511 events. 87 units contained at least one event and 132 contained at least
one relation. Twenty units returned zero semantic items; four units contained
an `ambiguous` segment disposition. Segment accounting remained complete: 578
segments were `covered`, 69 `no_navigation_material`, 10 `ambiguous`, and 2
`unsupported`.

The reviewed examples include plausible event sequences and relations in
dialogue-rich inputs, conservative ambiguous/no-item handling in sparse
inputs, and structured-observation outputs dominated by mentions/facts rather
than events. Some structured or list-like outputs are extraction-heavy and
require curated review for navigation usefulness, qualifier preservation, and
omission; no active semantic-view eligibility or production adoption is
claimed. The sample is real RU-bound Genshin material, but this spot review is
not a human acceptance set and does not establish corpus-wide semantic quality.

## Decision boundary

The approximately 200-unit operational/mechanical scope is complete and is
frozen for review. No material correctness, accounting, identity, persistence,
or route execution blocker was observed in this scope. Real fallback remains
unobserved, semantic acceptance remains open, and the evidence does not
authorize expanding the sample, starting a full build, or changing production
defaults. Stop here pending the next decision.
