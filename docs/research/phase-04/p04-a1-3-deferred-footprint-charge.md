# P04 Evidence Admission Challenger — Deferred-Footprint-Charge Shadow

> Historical checkpoint / current relation: this note preserves the accepted
> 22Q shadow experiment and its then-valid non-adoption decision. Formal
> Deferred-Footprint-Charge was later adopted as the production-facing Assembly
> default through its formal API. That later adoption does not alter this
> experiment's result, scope, or hashes; current production authority is
> `docs/current-phase.md`.

## Status

ACCEPTED / CHECKPOINTED as a diagnostic-only mechanical work unit at B0 =
`12,000`. The deferred-footprint-charge shadow is mechanically supported on the
accepted persisted 22Q EXPANDED candidate supply. Its confirmed
shared-occurrence eligibility defect is repaired. At this historical
checkpoint, production/default adoption remained **NOT AUTHORIZED**;
A1-2-1 v2 was the accepted/current selector and A1-3 remained
experimental/shadow.

## Accepted artifacts

- Primary diagnostic:
  `.local/p04-rag-a1-3-deferred-footprint-charge-shadow-b0-0.4/diagnostic.json`
  - SHA-256: `fe72eb6ee13fed537964d6e57963ad069c320a9de00dcb1db997bd4fe9d81107`
- Accepted review-correction sidecar (binding/counting/outcome-label
  qualification):
  `.local/p04-rag-a1-3-deferred-footprint-charge-shadow-b0-0.4/review-correction.json`
  - SHA-256: `7bcd0f8093ea56aaa42f2d60a9e03d58b6434efc531f05e8a427545493031df7`

The accepted `.0.1` challenger diagnostic remains unchanged for historical
comparison; `.0.4` is the repaired and accepted mechanical measure. The
`.local` artifacts are intentionally ignored and are identified here by their
hashes.

## Implementation record

The challenger preserves the approved three-pass admission timing only:

1. Direct roots are processed in existing candidate priority order and charged
   through the already-existing legal singleton Evidence Packet projection.
2. Non-root immutable-footprint members are deferred until after root
   admission. The repaired behavior explicitly preserves an occurrence's
   higher-priority anchor membership even when the same Retrieval Unit is also
   a later direct candidate. Direct candidacy does not silently cancel deferred
   eligibility.
3. The existing context-only admission behavior remains unchanged.

Duplicate presentation/rendering and charging are suppressed by the existing
rendering audit. Once an occurrence is rendered, later processing neither
renders nor charges it again. Candidate priority, RU/source-occurrence
identity, Raw -> Parsed -> Canonical provenance, source ordering, ownership,
complete immutable-footprint audit, full-footprint per-block eligibility,
marginal accounting, determinism, and fail-closed behavior are preserved.

Focused regression coverage and the affected
`tests.retrieval.test_rag_w1` suite pass: 33/33. The accepted v2 B0 control
Packet replay is byte/trace identical 22/22, and the accepted A1-3 EXPANDED
Packet/trace replay is 22/22. The challenger Packet and trace are
deterministic across same-input runs.

## Same-supply mechanical result

Comparison is against accepted A1-3 EXPANDED on the same candidate supply:

| Transition | Count |
| --- | ---: |
| Packet-visible in both | 518 |
| Packet-invisible in both | 145 |
| A1-3-invisible -> challenger-visible | 131 |
| A1-3-visible -> challenger-invisible | 4 |

All four remaining losses are mechanically explained by verified policy
mechanics:

| Question | RU prefix | Verified omission |
| --- | --- | --- |
| Q012 | `bc2bf572` | direct total-budget conflict: `11,990 + 96 = 12,086`; shortfall `86` |
| Q016 | `af1d7461` | direct total-budget conflict: `11,302 + 1,200 = 12,502`; shortfall `502` |
| Q057 | `4647010d` | direct total-budget conflict: `11,878 + 188 = 12,066`; shortfall `66` |
| Q061 | `674e96df` | preserved full-footprint per-block limit: `3,014 > 3,000` |

The five tracked decisive carrier occurrences — Q012, Q016, Q041, and both
Q046 occurrences — are mechanically Packet-visible through admitted singleton
direct roots. This is an admission/visibility result only. It is **not** a
semantic rescue and establishes no semantic or answer-quality utility.

## Shared-footprint counting correction

The original aggregate field
`shared_occurrence_eligibility_audited = 22` is a relation count, not a unique
occurrence count. The accepted sidecar records the corrected counting bases:

- 29 unique shared-footprint occurrences;
- 30 occurrence-to-footprint-anchor relations;
- occurrence eligibility partition: 21 with at least one eligible relation and
  8 with none;
- relation eligibility partition: 22 eligible/audited and 8 ineligible;
- explicitly rejected derivation: `29 - 22 = 7`.

A shared occurrence is a RU that is itself a direct-root occurrence and also
has context-anchor membership. A relation is a shared occurrence to a context
anchor whose immutable direct footprint contains that RU.

## Trace-label qualification

The challenger trace label
`already_visible_via_higher_priority_anchor` is over-specific for the
three-pass challenger. It means only that the occurrence was already rendered
before the recorded deferred admission attempt. It does not determine
presentation ownership or actual rendering anchor. The authoritative actual
rendering is `context_occurrences[].rendering`. The observed 20
outcome-bearing occurrence-to-anchor deferred attempt records actually render
17 occurrences via `direct_root` and 3 via `deferred_footprint`.

## Decision boundary

The mechanical admission hypothesis is **supported** at B0 on this persisted
22Q EXPANDED supply. Semantic / answer-quality utility is **UNKNOWN**. This
selected 22Q diagnostic subset must not be treated as a full-70 prevalence
estimate. Carrier visibility is not semantic rescue. No B1, Retrieval, Dense,
RRF rebuild, Generation, or provider call was used, and no production/default
admission policy is authorized by this checkpoint.
