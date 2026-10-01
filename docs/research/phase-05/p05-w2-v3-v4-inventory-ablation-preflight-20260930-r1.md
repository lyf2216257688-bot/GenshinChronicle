# P05-W2 v3/v4 Inventory Ablation Preflight (2026-09-30)

## Frozen sample

The new immutable provider-free preflight root is
data/retrieval/p05-w2-v3-v4-inventory-ablation-preflight-20260930-r1.
Its preflight identity is
89e4ba9e913c94e5f3e84464a42da89a91779f339f79e9fe4867ec0a32e0fae5,
and sample identity is
28706d0ac48a5e2698841293fcd67ce3278cc613125abf814446ed505f73a05b.

The sample contains exactly three units:

- mihoyo_obc:zh-cn:507825, point 1, compilation unit
  e8adb18e3c85b6294dbd2429bdd6f7d80505b3dd4ad414dd412d45a1d4cc97ac,
  input identity 18a98a0e..., confirmed material omission target.
- mihoyo_obc:zh-cn:502434, compilation unit
  b4c07fdd526edba1c4215a98b38a05b3fc5e2e800a64e73b4c4b07be09298886,
  input identity a94b8b05..., source-binding/validator guard. Its semantic
  outcome remains UNKNOWN; no validator relaxation is implied.
- mihoyo_obc:zh-cn:6430, 奇异的甘露封印…【教程】, compilation unit
  6196fe4f1214366d7a2233b0275e724a5159f097e9de9a105fd9dee9ee822c25,
  input identity 2b25ded6..., selected as the ordinary control.

The control was selected from the immutable 200-unit root, not by a mechanical
signal. Its source and accepted v3 output were read directly. The source has
textual structured and text projections plus an image-only segment. The output
has four events, five mentions, two relations, preserves hypothetical/tentative
qualifiers, covers textual segments, and marks the image-only segment
unsupported. The bounded verdict is no confirmed semantic issue. This makes it
a complexity-matched control for context continuity, relations, modality, and
over-extraction regression. No second short control was necessary.

## Comparison contract

The same frozen payloads, compiler-produced input identities, model and route
operating point, schema, validator, source-binding policy, and SDK route-pair
runner are used for both prompt variants:

- v3 prompt identity:
  2c332577f91edb50806a4f4c2cf663b720932c4833ac0e6023e1050c74370a2d
- v4 prompt identity:
  e7ffc4788e42dd4d1214a36ea35aaa2b2c179f676e237ba0e393c0f9eea9404c
- model: deepseek-v4.1-flash
- primary route: TokenMetro; fallback route: Jizhi
- runner: semantic_sdk_runner.run_sdk_route_pair
- streaming Chat Completions, max_tokens=500000, generation parameters
  unchanged, automatic retry disabled
- schema identity:
  9848b1e07c09b64156497dddc7a1a244f6dd9d7c3d5455c8c0dd0fae380cf312
- source-binding policy: phase05-w2-source-binding-0.2
- validator: strict JSON/schema/source-binding/local-reference chain

The planned live comparison is three v3 requests plus three v4 requests.
The preflight records zero provider and network calls. It does not authorize
live execution, retry, model comparison, semantic acceptance, or production
adoption. Provider model revision, usage/charge, semantic quality, and the
502434 semantic outcome remain UNKNOWN.

## Verification

The preflight root contains the canonical payload set, unit identities,
both prompt contracts, selection rationale, control review, and the
provider-free manifest. The source roots remain immutable. No old 200-unit or
targeted live root was modified.
## Later identity correction

The `507825` row in this three-unit preflight was labeled point1 but its frozen
payload is the middle point stage (`seg-1429481a5c3c335f6270:p1`, source text
begins with `（点位2）`). This preflight remains immutable historical evidence;
it is not evidence for the confirmed point1 omission. The corrected true-point1
sample has a separate preflight and live root recorded in
`p05-w2-true-point1-v3-v4-20260930.md`.
