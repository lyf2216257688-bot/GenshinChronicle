# P05-W2 Targeted v3 Live Semantic Validation (2026-09-28)

## Boundary

This is the small live follow-up to the provider-free preflight documented in
`p05-w2-targeted-v3-preflight-20260928.md`. The immutable 200-unit root
`data/retrieval/p05-w2-scoped-semantic-20260927-r1` was not rerun, resumed, or
modified. The run used the committed v3 prompt and the explicit SDK route-pair
API after the user-authorized live step. It is targeted semantic evidence only;
it is not a benchmark, a v2/v3 superiority claim, or production-adoption
evidence.

## Frozen identities and request boundary

- Input root: `data/retrieval/p05-w2-targeted-v3-20260928-r1/input`.
- Preflight root: `data/retrieval/p05-w2-targeted-v3-20260928-r1/preflight-final`.
- Preflight identity: `02b1b33cc742edf0575c08782d048290627562a70b2837c1df9e2f9beb338827`.
- Targeted semantic build identity: `561bf9b6dffdeaeaa394ee924db509d0747a9f808511fe947007aa311f92100b`.
- Parent semantic build identity: `45520b23490a1c66289476c71dbe55c38cd206167342ebeb5894721055c59677`.
- RU build identity: `49b48ee746716add0248fed388d10bd522a930efb582a0f5e827f66681ed8998`.
- v3 prompt identity: `2c332577f91edb50806a4f4c2cf663b720932c4833ac0e6023e1050c74370a2d`.
- Request-parameter identity: `38a73966aaeaa27d726139dfb5d5b9ff04d1e23bf9287becb8e82871f47fbc4b`.
- Payload-set identity: `4afc097ddbd72a0359966fff9aab449599130e9f76b1d84915a9cd8457826f28`.
- Run root: `data/retrieval/p05-w2-targeted-v3-20260928-r1/live-20260928-r1`.
- Run identity: `a6f88a784b5ff9ed15eadba33d1552c967e0cbad2bd1eaccf4f973496a72fb2d`.
- Terminal summary identity: `aa320bc9854eac8a7d113028786c1277ff383767280558b8160fc3ef77a1a585`.

The preflight fixed 11 primary requests. A policy-403 response alone could
legally issue one Jizhi fallback for that unit, so the conditional fallback
ceiling was 11 and the combined hard ceiling was 22. Automatic retry and sample
expansion were disabled.

## Frozen unit set

| Record | Unit ID | Role | Result |
| --- | --- | --- | --- |
| `507825` 古老的字迹【安瓦蒂尼尔湖】 | `6e6e03b95e39d30f0ff668d4def20fa01dcc22ee372f0fc8ec12a043d246b12c` | point 1 known problem | accepted local contract |
| same | `e8adb18e3c85b6294dbd2429bdd6f7d80505b3dd4ad414dd412d45a1d4cc97ac` | point 2 known problem | accepted local contract |
| same | `1dd76e47ac8561ea5c104c07a26b4edea08732f1db1179175fee19a3f3036d12` | point 3 known problem | accepted local contract |
| `2606` 托马 | `be4c99f58f0187245fb3064084ece0fab11cabc3905f375766bd20109b7f3f90` | dated email 2026 | accepted local contract |
| same | `e21802f2e3de28f9681235ba3d4149d83e426d42d51a3411985b9affff58b994` | dated email 2025 | accepted local contract |
| same | `cdc36ca9923a1c727ed50244b919537fc1ce620e0873278ab9b5e514e4aa6c0d` | dated email 2024 | accepted local contract |
| same | `92bd38aff6abf14718719d7c77851bfd5ab3ae66d09c6a0941d2e720f605dd47` | dated email 2023 | accepted local contract |
| same | `a2afa2b9135ddfdbb6ed4387c8d178f42f7832c9eb7e6af5e93351baf265d49c` | dated email 2022 | accepted local contract |
| `502434` 残破的出勤记录【阿陀河谷】 | `b4c07fdd526edba1c4215a98b38a05b3fc5e2e800a64e73b4c4b07be09298886` | map/text known problem | blocked at source binding |
| `6572` 敌人索引 | `93f2fed53e39d30f0ff668d4def20fa01dcc22ee372f0fc8ec12a043d246b12c` | sparse control | pending |
| `6430` 奇异的甘露封印…【教程】 | `50042a4f9faff7728cfa78631c7189760717694a47f48896d75e887a5474befa` | ordinary multi-event control | pending |

The set was kept small because the three point stages and five dated email
stages were no longer equivalent to their old aggregated parent units after the
compiler change. The map/text case directly exercised the new minimal binding
rule. The two controls were retained to detect ordinary-case regression, but
the fail-closed stop left them unattempted.

## Mechanical execution

Nine TokenMetro primary requests were issued before the stop. Eight returned
HTTP 200 complete streams and passed strict JSON parsing, the authoritative
schema, and local source binding. The ninth request, for `502434`, also passed
HTTP/stream/JSON/schema checks but was rejected by source binding:

`semantic output item 11 source binding is not directly supported by map_desc`

The response, stream archive, request artifacts, validation result, and terminal
record are retained under that unit's `attempt-001/` directory. There were zero
Jizhi fallback requests, zero policy-403 transitions, zero automatic retries,
and no expansion. The run status is `blocked`, with 8 accepted units, 1 blocked
unit, and 2 pending units. Offline audit/replay passed over all 9 attempts and
the 8 accepted outputs. Provider usage and billing remain provider-reported
evidence only; billable and charge status are `UNKNOWN`.

## Targeted semantic review

The review used the exact frozen source payloads and accepted canonical outputs.
It answers the three targeted questions with bounded observations:

1. **Independent events and stages:** the three `507825` point units and all
   five `2606` dated email units were independently processed. The email units
   retain subject/sender/date facts and split developments that were previously
   compressed. This confirms that the compiler split is reaching the prompt.
   It does not prove completeness: point 1 still emits only a small central
   command from a much longer passage. Point 2's output array lists
   departure/speech before arrival/naming, but the schema does not define array
   order as chronology, so a chronology failure remains UNKNOWN.
2. **`map_desc` and textual binding:** the new binding gate stopped `502434`
   fail-closed. The authoritative schema normalizes item order before binding;
   normalized item 11 is `t2/topic/位置` and binds only to `map_desc`, while
   the decoded map list also contains `tab_name=位置`. The current validator
   metadata does not expose map tab names, so this is a validator-boundary
   candidate rather than confirmed model source-binding error. No binding-policy change
   follows from this case, and it cannot be counted as semantic improvement.
3. **Side effects:** stage-local topic paths and local references were retained
   in accepted outputs. The `507825` point-2 array ordering and the `2606`
   attribution question remain UNKNOWN because the schema does not define array
   order as chronology and the email narrator makes the attribution plausible.
   The two controls were not executed, so ordinary-case regression is UNKNOWN.
   No duplicate-event pattern was promoted to a confirmed systemic regression
   from this small set.

These observations are semantic review evidence, not a semantic PASS. The
compiler/provider-free contracts remain mechanically verified; semantic
acceptance, active-view eligibility, and production/default adoption remain
open or blocked.

## Stop boundary

The run stopped at unit 9/11 under the existing fail-closed boundary. No retry of
`502434`, no execution of the two pending controls, and no broader build or
benchmark was performed. Continuing would require a new decision about the
binding failure and the unresolved ordering/attribution risks; the existing
input, preflight, and live roots remain immutable evidence.
