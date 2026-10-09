# P05-W2 Semantic Quality Review v0.1 (2026-10-08)

## Current evidence preparation checkpoint

The current user-authorized unit prepared 12 independently reviewable samples
from the **2026-10-04 historical-200 Responses atomic extraction**. It did not
perform semantic adjudication, carry forward the historical judgments below,
calibrate a threshold, call a model or network, regenerate output, repair data,
change a production contract, or push Git.

The new immutable evidence root is
`data/retrieval/p05-w2-atomic-quality-evidence-20261008-r2`.
Its auditable index is `index.json` (SHA-256
`8c417e5ecaaba9faa2120a914e1b411e037ec618434ecbc9c6c2284834bcb0da`),
the preparation accounting is `completeness.json`, and the 12-row readback
verification is `integrity-check.json` with status PASS. Earlier r1 is a
preparation checkpoint; r2 adds official Raw copies, source sidecars and
stronger mechanical/input binding without altering r1 or historical roots.
The human-readable entry point is `README.md`, with one `review.md` per sample.
The portable archive is
`data/retrieval/p05-w2-atomic-quality-evidence-20261008-r2.zip` (162 files,
580,564 bytes; SHA-256
`3e6efbff0581263742668089afcfa41e10720a84d8b08fe29e77ffda9dbd8ba7`).
Every archive entry was compared byte-for-byte to the final evidence root.

| Set | Record IDs | Strict replay balance | Selection reason |
|---|---|---|---|
| Research | 507825, 501287, 723, 509618, 1350, 2778 | 3 PASS / 3 FAIL | Specified research candidates, task-relation fields, dialogue controls and structured regional material |
| Independent validation | 505240, 5310, 6473, 508046, 502342, 508276 | 3 PASS / 3 FAIL | New dialogue, task-relation, structured rich-base and material/collapse samples |

The selection uses only identity/type and mechanical metadata. PASS/FAIL is
the frozen strict replay status, not a semantic expectation; all semantic
outcomes remain unadjudicated. Normal controls are included by ordinary input
type and mechanical PASS, without presuming semantic correctness.

Each sample directory contains `official_raw-01.json` (byte-identical official
Raw response) and `source_sidecar.json` with Canonical/Parsed/Raw JSON Pointers
and RU identities. `source_payload.json` contains the exact retained official
source segments from the frozen manifest, `source_rendering.txt` (lossless
structured text rendering), `canonical_output.json`, `validation.json`,
`issued.json`, `request.json`, `terminal.json`, `source_location.json`,
`provenance.json`, and
`mechanical_verdict.json`. The provenance distinguishes logical compilation ID
from physical attempt path and binds the original manifest, source payload,
request/wire identities, canonical output and copied artifact hashes. The
mechanical file records historical runtime acceptance, strict replay, Source
Anchor v0.1 and literal-span sibling statuses separately, with report hashes,
policy and implementation/dependency hashes. The extraction contract pins
Prompt v0.7, schema and source build identities. Sample-manifest and issued
runtime request identities are recorded separately because they hash different
contracts; issued identities are verified against the runtime manifest.

Inputs are the parent Responses root, Metro continuation, and final4-r2 roots,
with their three 2026-10-04 manifests, and the existing strict replay report
`.local/p05-w2-historical-atomic-replay-audit-20261007-r1/report.json`.
No later non-atomic output, final4-r1 unknown output, or repair-sibling output
was substituted. Continuation manifest entries duplicate parent logical units;
the packet resolves the report's authoritative run root and then matches
`issued.unit_id`, rather than treating the physical directory as a logical ID.

The finite exclusion ledger is `prior-review-exclusion-ledger.json`. It
conservatively excludes prior manual review IDs, prior-turn inspected cases,
and mechanically investigated recovery/control IDs. Exact record-key/ID
searches in the Phase05 research records showed no selected validation ID as a
reviewed case. The 14-case 2026-09-27 spot-review selection was recovered from
its prior execution record: sorting historical sample rows by
`(stratum, compilation_unit_id)` and taking positions `0,15,...,195`. Those
record IDs and the prior-turn book ordinal 104 (`1144`) are included in the
exclusion ledger. The validation intersection with the ledger is zero. This is
membership independence from the available repository evidence; no untracked
human inspection history can be proven absent. Reviewers should preserve the
validation split and report any newly discovered prior exposure before using
it for evaluator calibration.
The final conservative ledger contains 46 record IDs.

Completeness/readback results: 12/12 selected attempt pairs exist; all copied
canonical, issued and runtime validation bytes match their original paths;
all source payload hashes and record keys match the packet provenance;
32/32 selected source segments have sidecars; 12/12 official Raw responses
and Canonical records match their upstream persisted hashes. Original consumed
input hashes match before, after, and during readback; the scope is selected
packets plus authority files, not a re-audit of all 200 attempts. The index
inventories 160 packet artifact hashes;
research/validation counts are 6/6 and strict PASS/FAIL counts are 6/6.
Focused offline tests passed 6/6 for source-location preservation, exclusion
disjointness, logical-to-physical mapping/duplicate rejection, output tamper
and original-input drift detection, and overwrite refusal. The real r2
preparation ran with socket connections blocked; provider/network calls were
zero. Compilation and whitespace checks passed. Original history was read only.
Related historical-audit/literal-span and preparation tests passed 23/23 in
total. A separate read-only check resolved all 32 selected Raw JSON Pointers.

Limits: extraction coverage is the **retained provider-facing source projection**.
The full historical official Raw response is included for context, but fields
outside the retained payload are not automatically model omissions. No page
was newly fetched; downloaded image/video bytes and complete Parsed/RU records
are not included. Existing HTML, dialogue splits, references and unsupported
material remain visible. The original strict report used
`working-tree:validate_atomic_inventory`; r2 binds its selected statuses to the
completed sibling replay's exact validator dependency SHA-256 without rerunning
or changing the validator. No semantic labels, false-positive/
false-negative rates, or quality thresholds are supplied. ChatGPT's subsequent
review must derive every claim and omission from each packet's source/output,
and keep insufficient source scope or attribution evidence unresolved.

## Historical checkpoint qualification

The following earlier content is preserved as a historical 2026-09-27
**non-atomic scoped-output** review. It was previously presented too broadly as
current historical-200 evidence. Its semantic judgments and validation labels
are not adopted for the new 2026-10-04 packet, and its inspected record IDs are
excluded from the new independent validation set.

## Historical 2026-09-27 review checkpoint (superseded for this task)

This older provider-free, read-only manual adjudication covered 12 accepted
outputs from the immutable **2026-09-27 non-atomic scoped semantic root**
`data/retrieval/p05-w2-scoped-semantic-20260927-r1` and does not rerun,
resume, rebuild, repair, or overwrite it. The complete row ledger, source
payload identities and canonical-output hashes are in
`data/retrieval/p05-w2-semantic-quality-review-v01-20261008-r1/review.json`.
The report SHA-256 is
`5ae3aff9a07d0a135113c28a9c38ade1bca309f1a00fdeff9c5c121ababb757c`.

The six calibration units are ordinals 7, 29, 40, 57, 63 and 194. They retain
the known `507825` omission target and existing normal controls. The six
disjoint validation units are ordinals 18, 41, 61, 88, 195 and 199, spanning
rich-base fields, ordinary and multi-branch dialogue, media/map mixtures and
an empty structured observation. This is an independent sample by membership,
not a blinded second reviewer or a prevalence estimate.

## Findings

The review confirms that mechanical acceptance hides several distinct semantic
failure classes:

- The known `507825` point-1 omission remains a candidate miss: the output
  preserves only part of the source's independent events and relations.
- Validation `501287` loses explicit `前置任务/起始NPC/结束NPC/后续任务`
  role relations while retaining some names and topics. This is an omission
  with attribution/role impact, not a source-binding failure.
- Validation `4333` promotes the UI instruction `四国语音展示请下划浏览` to a
  fact. Validation `509684` promotes a media-only segment to the fact `视频`.
  These are unsupported UI/media extractions and were not nominated by the
  simple structural signals.
- Validation `501752` exposes a qualifier boundary: imperative `切莫...`
  is encoded as `polarity=negative`, and map image tabs are emitted as a fact.
  The candidate signal did fire, but it does not identify the semantic cause.

The normal controls also matter. `500660`, `4402` and `1078` trigger coverage,
qualifier-absence or empty-output signals without a confirmed semantic issue.
`503380`, `506793`, `501323` and `504120` are bounded normal controls with no
confirmed issue. These are bounded false-positive observations, not a measured
signal precision.

## Accounting and interpretation

Calibration has 1/6 issue-confirmed and 5/6 no-confirmed-issue units.
Validation has 4/6 issue-confirmed and 2/6 no-confirmed-issue units. Across the
12 rows, there are 3 candidate flags without a confirmed issue, 4 candidate
misses with a confirmed issue, 1 candidate flag with a confirmed issue, and 4
candidate misses without a confirmed issue. These counts describe this mixed
review set only; they are not precision/recall, a risk score, or an acceptance
threshold.

The useful v0.1 calibration target is therefore a review rubric with separate
labels for material omission, fact/role misinterpretation, attribution,
qualifier scope and UI/media overextraction. Structural triggers may nominate
review, but they cannot decide semantic quality. The next implementation seam,
if separately authorized, should persist these adjudication labels and source
evidence beside the original output, preserving the original verdict and
keeping unresolved cases `UNKNOWN`.

Semantic correctness, corpus-scale quality, production eligibility, Navi/KG
activation and Full Semantic Build remain `UNKNOWN`/blocked. No production
contract or historical artifact was changed; provider and network calls were
zero.
