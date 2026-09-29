# P05-W2 Offline Semantic Quality Signal Investigation (2026-09-29)

## Boundary

This work unit is provider-free. It reads the immutable 200-unit scoped
semantic build and the existing targeted v3 preflight/live artifacts. It does
not rerun, resume, rebuild, or modify either source root, and it does not
change semantic runtime behavior, source-binding policy, active Navi/KG
eligibility, or production defaults.

The durable result is
`data/retrieval/p05-w2-semantic-quality-signals-20260929-r6` with report
identity
`1874359d7d9e75cf97fcfa74b229c943bfc2ecde300bc2d218338c6175cc6f1d`.
Earlier analyzer drafts were written under `...-r1` through `...-r5`; the
stage-marker implementation, targeted adjudication boundary, and control
review accounting were corrected before this result was accepted. The r6 root
is the authoritative result for this investigation.

## Inputs and identities

- 200-unit source root:
  `data/retrieval/p05-w2-scoped-semantic-20260927-r1`
- 200-unit sample identity:
  `72e1ec582cfb98c06c4ff15d74c2abe5f50d8c7f7e53381d3d224b5d7434577a`
- 200-unit run identity:
  `84ee8e1eab5d3f2bc3119a96ec1bd68c149828635e9fbef4f0adf90dc285a935`
- targeted source root:
  `data/retrieval/p05-w2-targeted-v3-20260928-r1`
- targeted preflight identity:
  `02b1b33cc742edf0575c08782d048290627562a70b2837c1df9e2f9beb338827`
- targeted payload identity:
  `4afc097ddbd72a0359966fff9aab449599130e9f76b1d84915a9cd8457826f28`
- targeted live run identity:
  `a6f88a784b5ff9ed15eadba33d1552c967e0cbad2bd1eaccf4f973496a72fb2d`

The analyzer recorded zero provider/API/network calls. Its manifest binds the
input artifact hashes and the output artifact hashes. `analysis.json`,
`distribution.json`, and `targeted-cases.json` are research evidence, not
semantic acceptance artifacts.

## Distribution evidence

The 200 accepted local-contract units produced these provider-free candidate
signals:

| Signal | Count | Interpretation |
| --- | ---: | --- |
| input has more than one segment | 105/200 | Broad input-structure candidate; insufficient alone |
| input contains map/text mixture | 22/200 | Relevant to source-binding review |
| empty output | 20/200 | Strongly concentrated in inputs under 1 KB; not a semantic-failure label |
| output has at most one item | 35/200 | Includes sparse/structured controls; weak standalone signal |
| non-covered segment disposition | 51/200 | Validator/input-accounting signal, not quality proof |
| non-empty output with no qualifiers | 86/200 | Too common to imply attribution failure |
| validator non-acceptance | 0/200 | Expected from the completed mechanical run |

The size split was material: 18 of 20 empty outputs were in the 51 units below
1 KB. This supports using input kind/size and omission metadata to decide
which units merit review, while rejecting a rule that treats every empty or
sparse output as incorrect. A known omission in `507825` point 1 was not
captured by these simple output/validator signals, so their recall is not
established. The targeted set contains eight units with compiler-preserved
explicit stage metadata; this is a targeted input fact, not a 200-unit
distribution estimate.

## Targeted observations

The analyzer inspected all 11 frozen targeted units and the 9 attempts present
in the immutable live root. The two controls (`6572`, `6430`) remain
`not_attempted` because the live run stopped fail-closed at `502434`.

The analyzer itself records no semantic adjudication. The separate targeted
source/output review remains the research evidence for these bounded findings:

- `507825` point 1 has a confirmed material independent-event omission despite
  local contract acceptance, but the simple signals in this report did not
  route it clearly.
- `502434` is rejected after the authoritative schema normalizes item order;
  normalized item 11 is `t2/topic/位置`, bound only to `map_desc`. The decoded
  map list contains a `tab_name` of `位置`. This makes the case a validator
  boundary candidate, while the model claim and the correct contract outcome
  remain `UNKNOWN`; no validator relaxation follows.

`507825` point 2 ordering, `507825` point 3 coverage/modality, `2606`
attribution, and ordinary-case semantic regression remain `UNKNOWN`.

Four small ordinary controls were read from source and accepted output. Their
bounded results are in
`p05-w2-semantic-quality-signal-control-review-20260929.json`: two signals
produced review triggers on outputs with no confirmed semantic issue, a split
ID `:p509` was correctly recognized as non-explicit stage metadata after the
analyzer correction, and a map-only case had no map/text binding concern.
This is descriptive evidence only; false-positive/false-negative precision and
recall remain `UNKNOWN`.

The report preserves unit IDs, request ordinals, source segment IDs, terminal
dispositions, and relative attempt artifact paths so later review can return to
the official source/RU lineage. It does not create a new unresolved or active
semantic schema.

## Design conclusion

Existing signals justify continued evidence collection, but not a claim that
selective review admission is already effective. The useful initial group is
the conjunction of deterministic input structure, compiler omission/oversize
state, output sparsity or coverage anomalies, and validator dispositions. The
known false positives and the unflagged `507825` omission mean that no signal
has enough evidence to become a semantic acceptance gate, fixed risk score, or
automatic active/inactive rule.

Selective self-review or repair remains a later hypothesis. This report does
not establish that a second model pass can recover omitted events, fix
attribution, or distinguish a validator boundary case. Stronger-model
fallback, retry count, thresholds, self-check schema, and escalation policy
remain undecided. Any future experiment must preserve the original output and
provenance, use a new immutable root, and keep unresolved results inactive.

## Verification

The provider-free analyzer passed `py_compile` and focused regression tests
`36/36` (3 new analyzer-specific tests plus affected existing semantic
compiler, preflight, and live-runner suites).
The existing 200-unit immutable root was read-only. No full semantic build,
benchmark, provider request, network request, retry, or model comparison was
performed.
