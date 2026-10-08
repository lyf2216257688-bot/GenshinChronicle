# Historical-200 Atomic Replay and Source Anchor v0.1 (2026-10-07)

This provider-free research replay reads the immutable historical-200 manifests,
attempt evidence, sample payloads, and canonical outputs. It does not change
historical roots, production semantic schema, Prompt, or the strict production
validator. Runtime-history verdicts, strict current replay verdicts, and
Source Anchor v0.1 sibling verdicts are retained separately.

## Inputs and Identity

The three manifests and immutable run roots are listed in the replay report at
`.local/p05-w2-historical-atomic-replay-audit-20261007-r2/source-anchor-v01-replay.json`.
The source classification authority is the prior artifact
`.local/p05-w2-historical-atomic-replay-audit-20261007-r1/report.json`; its
SHA-256 is recorded in the new report. Each row binds its source payload and
canonical output hashes. The sibling verdict also records the strict
validator source hash, policy identity and implementation hash, and
classification-ledger hash. A ledger/canonical hash or item-identity mismatch
stops the replay rather than guessing eligibility.

The 83 historical canonical-fail members retain their historical runtime
disposition `accepted_for_local_contract`; the separate no-canonical member
retains `local_validation_failed`. No historical verdict was overwritten.

## Accounting

| State | Before: strict current replay | Source Anchor sibling |
|---|---:|---:|
| Logical sample | 200 | 200 |
| PASS | 107 | 107 |
| Canonical FAIL | 83 | 83 |
| No canonical | 1 | 1 UNKNOWN |
| Provider omitted | 9 | 9 |
| Entire previously failing units recovered | - | 0 |
| Units with at least one anchored item | - | 36 |
| Items accepted by Source Anchor v0.1 | - | 62 |

The strict replay remains 107 PASS / 83 canonical FAIL / 1 no canonical, with
9 omitted. The sibling does not convert a mixed unit to PASS: all 83
canonical-fail units still have at least one unresolved item. The 107 prior
PASS units have zero regressions and remain `PASS_NATURAL_QUOTE` at item level.

## Failure Classes and Item Results

The unit-level primary failure classes remain the latest audit's distribution:

| Unit-level class | Units | v0.1 disposition |
|---|---:|---|
| `source_projection_or_quote_binding` | 25 | FAIL; projection is not reconstructed by v0.1 |
| `structured_value_quote_projection_boundary` | 18 | Candidate class only; 62 of 146 failed items receive a unique decoded-scalar anchor |
| `fabricated_or_unbound_quote` | 31 | FAIL; zero items receive an anchor |
| `metadata_serialized_json_contamination` | 9 | FAIL; zero items receive an anchor |

Before v0.1, 945 failed items comprise 251 projection/binding, 146
structured-value boundary, 303 fabricated/unbound, and 245 metadata items.
The 62 anchored items are a subset of the 146 structured-value items. After
v0.1, 883 items remain failed: 251 projection/binding, 84 structured-value,
303 fabricated/unbound, and 245 metadata items. The remaining 84 structured
items fail closed because no unique eligible scalar anchor was established.
Every anchor item was cross-checked against the prior ledger and classified
there as `structured_value_quote_projection_boundary`.

Natural text `source_quote` acceptance still uses the unchanged exact bound
source projection from `validate_atomic_inventory()`. Structured v0.1 anchors
are analysis-only evidence containing policy, segment ID, JSON Pointer into
`value.decoded`, exact scalar hash, and exact quote. Metadata keys and
serialized JSON are not eligible. This is mechanical source binding only;
semantic correctness remains `UNKNOWN` for every row.

The exact 36 unit IDs and their 62 anchored local item IDs are recorded in
`source_anchor_v01_items` in the replay JSON. Representative anchors include
`m1 -> /value/decoded/name` for unit
`09627a63901e1e7555e2a8f116959b2f5d5d10714bdf16d3a4c965d5bf4237e3`,
`m8,m9,m10,m11` to distinct decoded feedback nicknames for unit
`1139daff95284998e8da2ff4421ec3a00aaad3a574489236232ebd18977bdc90`, and
`c1` through `c4` to decoded list keys for unit
`b93c814582d9e33f9b312245702fc3ce678a08f77222403084f0c876f37289e2`.

## Verification and Boundaries

Focused tests passed 18/18, including a regression proving that the presence
of a matching decoded scalar cannot override a `fabricated_or_unbound_quote`
ledger classification. The replay reported provider calls = 0 and network
calls = 0. It did not rerun a model, mutate an immutable root, repair
semantics, assign risk scores, activate Navi/KG, run Full Build, or push.

Source Anchor v0.1 is not production adoption and does not establish semantic
truth. All remaining failures and the 9 omitted units remain as recorded; the
no-canonical unit remains UNKNOWN in the sibling replay.
