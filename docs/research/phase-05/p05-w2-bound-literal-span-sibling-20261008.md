# Bound Literal-Span Research Sibling (2026-10-08)

## Scope

The user authorized only the six pure CONTRACT_FIXABLE units / 19 remaining
items selected by the historical-200 repair-routing r3 artifact. The implementation
is `src/genshin_corpus/retrieval/historical_bound_literal_span_sibling.py`;
the final report is
`data/retrieval/p05-w2-bound-literal-span-sibling-20261008-r3/report.json`.
Earlier r1/r2 are implementation checkpoints, not the final authority.

This policy is `phase05-w2-bound-literal-span-sibling-0.1`. It pins the routing
and Source Anchor replay hashes, the exact six unit identities and 19 local IDs.
It does not dynamically authorize later CONTRACT_FIXABLE entries. Source Anchor
v0.1 and this literal-span policy remain research siblings, not production
semantic schema/Prompt/validator adoption. No model output, source quote, source
refs, semantic label, coverage, qualifiers or historical verdict is modified.

## Anchor Contract

An eligible item must retain its frozen routing identity, failure class,
literal-span operation and unique bound source evidence. The anchor contains:

```json
{
  "kind": "bound_literal_span",
  "segment_id": "original item-bound segment ID",
  "json_pointer": "/value/...",
  "value_sha256": "sha256 of the entire exact UTF-8 source scalar",
  "start": 0,
  "end": 10,
  "span_unit": "unicode_codepoint",
  "source_quote": "original exact quote"
}
```

`start` is inclusive and `end` exclusive, in Unicode codepoints of the retained
literal scalar, not bytes, rendered DOM offsets or normalized text. The slice
must exactly reproduce the original quote. The candidate is counted across
all eligible scalar values in all original bound segments. Multiple values,
multiple occurrences (including overlaps), duplicate segment identities,
pointer/hash/span/quote tamper and frozen-evidence mismatch fail closed.

No HTML attribute extraction or semantic interpretation is added. Some of the
19 existing quotes contain markup; they are matched literally as source strings,
not converted to HTML name/amount/resource semantics. Pure `data-entry-*`
attribute fragments and technical/resource/header paths are excluded. The 21
HTML name/amount extension candidates from routing are outside the pinned scope.
Fabricated/unbound and serialized-metadata failure classes are ineligible even
if a later literal search happens to find a string.

## Accounting

| State | Frozen strict replay | Source Anchor v0.1 | Literal-span sibling |
|---|---:|---:|---:|
| Logical sample | 200 | 200 | 200 |
| Mechanical PASS units | 107 | 107 | 113 |
| Canonical FAIL units | 83 | 83 | 77 |
| No canonical | 1 FAIL | 1 UNKNOWN | 1 UNKNOWN |
| Provider omitted | 9 | 9 | 9 |
| New recovered units in this work | - | - | 6 |
| New recovered items in this work | - | - | 19 |
| Remaining failed items | 945 | 883 | 864 |

The original strict verdict remains 107/83/1/9. The new sibling recovers 6/6
target units and 19/19 new items. Two existing v0.1 scalar anchors in the target
units are independently replayed and retained; these are additional to the 19
new span items. The existing 62 v0.1 anchors across the sample are not counted as
new recovery.

| Recovered unit ID | Title | New passing local IDs |
|---|---|---|
| `207e61c1ee31a9a08c07ae206cb09cf5629bc3c1cbc9e1f6bab9bb56eeeb4d1d` | 空月之歌 第六幕「散于晨雾的月芒」 | i114, i119, i123, i124, i131, i136 |
| `63636df90f16a60494ff9f976b5e11131c592cc4dc7fd0fc647c82e023402fca` | 海妮耶 | e14, e15 |
| `65d26338764b46bfcbf201fe27073b064e769ebe944ca3dc67a660f5ca610ddd` | 神樱大祓 | e19 |
| `9429ff71c478843e52bdce6f317b6cba6f7287b5a69425f069091351a5fe6202` | 香木「赤璋」 | f1, f2, f3, f4, f5, f6 |
| `d9e8790e4b92297cc1dda73ce3672f254b089ba490c409a49107b38fe001d14b` | 雪山迷踪 | i85 |
| `f255a6f8fe19d05dc70f252c33c2672cc211b198d423bc22c0c5837e5073478e` | 被藏匿的账本 | f_desc_910, f_holder_910, f_progress_910 |

New recovered item classes are 9 projection/binding and 10 structured boundary.
Remaining failures: projection/binding 242, structured boundary 74,
fabricated/unbound 303, metadata contamination 245 (864 total). The remaining
77 canonical-fail units and the no-canonical UNKNOWN retain their item reasons.
No MODEL_REPAIRABLE or UNKNOWN item receives a new anchor.

## Verdict and Input Governance

Each unit retains historical runtime, strict replay, Source Anchor v0.1 and new
literal-span sibling verdict fields. The sibling verdict identity binds the
policy, implementation and shared validation dependency hashes, routing report,
v0.1 replay, payload and canonical output. Each new item additionally binds its
original item hash. No-canonical verdict records an unavailable output identity.

Before attempting new anchors, the replay actually executes unchanged strict
and v0.1 validators for all 190 existing canonical outputs, including structural
envelope/navigation checks. Their statuses, errors and item verdicts exactly
match the frozen evidence. All 107 strict PASS units replay PASS: zero regressions.
All 548 disallowed failed-item controls (303 fabricated/unbound + 245 metadata)
remain FAIL, with zero new admissions. Existing v0.1 PASS labels are not blindly
copied; their anchors are revalidated by the existing research implementation.

All 1,734 files in the three immutable historical roots are hashed before and
after replay, with both contents and file-set equality checked. Including
manifests/routing/replay/ledger, 1,740 immutable input files are unchanged. The
prior routing input hash inventory is verified before use. Policy/validator
implementation hashes are also checked before output. Output uses a fresh
sibling path and exclusive creation; overwrite is rejected.

## Verification and Boundaries

Focused tests passed 40/40 across the literal-span, routing, historical-audit
and two-stage modules, including 11 new literal-span tests. The tests cover
unique spans, non-BMP codepoint offsets, multiple scalar/segment/occurrence
ambiguity, duplicate source identity, pointer/value/span/quote tamper, frozen
item/source-evidence tamper, exact scope, disallowed routes/classes, pure
attribute exclusion, input hash mismatch and overwrite rejection. A small
synthetic end-to-end fixture exercises six/19 scope plus 107 strict controls;
real acceptance accounting comes from the immutable historical artifacts above.

The real replay ran with socket connect blocked; provider/network calls = 0.
Compilation and tracked/new-file whitespace diff checks passed. No production
schema/Prompt/validator change, semantic repair/reviewer, HTML attribute
extension, risk score, Navi/KG, Full Build, model call or push occurred. The
existing `docs/current-phase.md` user modification was preserved unchanged.

All semantic correctness remains UNKNOWN. Mechanical source binding does not
prove attribution, fact truth, claim completeness or semantic equivalence, and
does not activate semantic navigation or production eligibility.

## One Next Work Unit

Recommend one provider-free actual-review unit of this research sibling: independently
verify the 19 persisted anchors, all six unit aggregations, immutable identities,
negative controls and versioned verdict separation. Do not expand the sample,
adopt production schema or implement model/HTML-attribute repair in that review.
