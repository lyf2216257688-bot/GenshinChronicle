# P05-Navi-E1 — local tools and P05-W1 wiring

Date: 2026-10-11. Baseline: `main@b29a8ae37fc626d414bbd156b331ea36c1b828d2`.
Scope: provider-free mechanical E1; no autonomous model, paid/provider/network
calls, KG relations, production/default change, corpus rebuild, commit or push.

## Implementation

- `retrieval/navi_exploration.py`: existing Lexical scoring over the complete
  index; streamed query-term projection preserves corpus denominator and
  document lengths. Snapshot identities/hashes and actual selected RU are
  verified. Source reads use manifest-bound records and existing deterministic
  Canonical-to-RU projection, exposing original unit values, metadata, dialogue
  graphs, lineage and component context. Prefix truncation is explicit.
- `rag/navi_experiment.py`: recorded search/read/clue/stop actions, atomic
  checkpoint, input/decision identity, completed-action replay reuse and hard
  budgets. Complete serialized Unicode response characters are charged,
  including locators/metadata; these are not model token or dollar estimates.
- Review projection produces only existing `SemanticItem(kind="topic")` and
  hierarchy. Review must explicitly bind group/checkpoint, reviewer, source
  qualification, rationale and narrative caveat. Unreviewed results remain
  `not_accepted` and inactive. An explicitly opted-in recorded fixture uses
  `accepted_for_fixture_test`; it cannot be promoted to curated human acceptance.
- Wiring directly reuses `run_p05_navigation`, `merge_navigation_candidate_rows`
  and `assemble_deferred_footprint_charge_packet`. No P05-W1 algorithm was changed.
- `rag/navi_e1_smoke.py`: CLI requiring explicit snapshot/output/start paths.
  It derives the search title from the start text; subsequent reads refer to
  returned hit coordinates, not hardcoded reference-answer content IDs. The
  selected hit index is a recorded mechanical decision, not autonomous choice.
- Narrow upstream additions: explicit scoped loading in `load_retrieval_units`
  and `prepare_evidence_assembly_context`, plus detached RU lookup/ID accessors.
  Full artifacts are verified/scanned; all units of selected pages are retained.
  Default full-snapshot behavior is unchanged and scope is visible in Packet
  build audit. These interfaces reduce resident memory, not disk scan work.

## Verification

Focused fixture tests: **102/102 PASS** across
`test_navi_exploration` (25), `test_p05_semantic_navigation` (20),
`test_rag_w1` (35), `test_single_question_backend` (22).
Syntax checks and `git diff --check` passed. No full-corpus rebuild or broad
semantic re-review was executed. Unit tests use small checked-in Canonical
fixtures; real corpus is used only by the explicitly invoked smoke.

Covered boundaries: lexical-score parity for both index types; complete
source reads, truncation and metadata budgets; invalid RU/manifest/Canonical
projection; no-network execution; checkpoint identity/tamper checks; completed
action reuse; separate unverified clues; round/search/read/context bounds;
missing/invalid review; explicit fixture versus curated projection; dialogue
structure retention; formal Deferred Assembly; baseline-only and omitted
candidates not counted as gain; scoped page companions.

## Real-source smoke and artifacts

Authoritative successful local root:
`data/retrieval/p05-navi-e1-real-20261011-r3/`.
Files: `summary.json`, `wiring.json`, `exploration/session.json`,
`unreviewed/`, `mechanical-fixture/`, and `review-request.json`.

RU build:
`49b48ee746716add0248fed388d10bd522a930efb582a0f5e827f66681ed8998`.
Start: content `3429`, Canonical `s7:u3`, Parsed
`/modules/7/components/0/units/3`, RU
`d9fed6511005d773cdb702580731656454c0f6d40685451dfe11ddbb1b45cfb8`.
The start was supplied by the work order, verified against the actual corpus,
and its text yielded search query `常世国龙蛇传`.

The recorded third returned hit was content `3508`, 绘真【末代图书管理员】,
Canonical `s7:u14`, Parsed `/modules/7/components/0/units/14`, RU
`507db1f9aeab14f76a6909932f1b42dafc6b014ff64e382f6c08734249ea3a10`.
It discusses the collected book's cover/language discrepancy. This is a
character's account, not proof of bibliographic identity or historical truth.
The real smoke did not hardcode `3508`, and does not establish an optimal
search or read policy. The keyword query also returned editorial/play-related
material; a hit is not a source qualification or relevance verdict.

| Audit denominator | Count |
|---|---:|
| Recorded completed actions | 5 |
| Search calls / returned candidates | 1 / 5 |
| Session read calls / distinct read RU | 2 / 2 |
| Separate seed preparation read | 1 |
| Delivered session Unicode response chars | 30,877 / 50,000 cap |
| Scoped pages / retained RU | 2 / 71 |
| Complete artifact rows verified | 535,802 |
| Baseline Packet visible RU | 2 |
| Navigation resolved / Assembly input RU | 2 / 2 |
| Baseline overlap navigation RU | 1 |
| Final Packet visible RU | 4 |
| Novel direct navigation RU visible | 1 |
| Additional new page-context RU visible | 1 |
| Omitted navigation RU / baseline lost | 0 / 0 |
| Unreviewed active / inactive topic items | 0 / 1 |
| Mechanical fixture active topic items / graph edges | 1 / 0 |
| Provider / network / Generation calls | 0 / 0 / 0 |

The final Packet's additional companion is
`9ab527aa2d183368343491b39a18814d33e8e64ff9af8ca9dce8c31bb4dddb23`.
Only the direct navigation RU is counted in the navigation novelty field;
companion visibility is separately inferable from the full Packet sets.
The existing Generation request projection excludes navigation trace.
The baseline is an explicit recorded start-only mechanical control, **not**
a formal Phase04 Hybrid baseline or live sufficiency decision.

Human semantic acceptance is **PENDING**. The real-source unreviewed build
has no active items. The fixture view exists only to test source/Packet wiring;
`review-request.json` is a pending template, not a completed human adjudication.
The human-reviewed projection path is verified by focused synthetic tests.

## Failed attempts retained

`p05-navi-e1-real-20261011-r1` preserves a completed mechanical search/read but
failed P05 navigation: Assembly emitted a valid page companion missing from
the initial navigation authority index. The correction supplies all verified
scoped page RU to P05; only read sources are grouped in the view.

`p05-navi-e1-real-20261011-r2` preserves a complete ledger but rejects active
projection of a partial Canonical dialogue graph under the 20,000-character
read cap. Its recorded second hit was too large. r3 uses the third search hit,
a rich-text occurrence, as an explicit recorded mechanical decision. No
failed root was overwritten; none is semantic success evidence.

A manually transcribed start ID was also rejected before a run root was
created; the exact ID was then taken from the existing case locator artifact.
No provider requests occurred in any attempt.

## Limits and next boundary

- No model autonomy, recall/precision, source-role correctness or answer gain
  has been established. Same-name books and character claims retain uncertainty.
- Full Canonical context can exceed a read budget. Partial data remains visible
  and cannot be accepted here; E2 context selection is still unresolved.
- Scoped reads still scan immutable artifacts and use local private projection
  primitives; performance and future public API factoring remain open.
- The recorded hit choice was adjusted after observable failures. This is
  mechanical development evidence, not a held-out or unbiased semantic trial.
- The local ledger supports deterministic read/search recovery only. It has no
  provider transport, request retry/fallback or unknown-execution recovery.
- Real human review of navigation groups, official-text qualification,
  standalone broader story links, formal Hybrid comparison, model/token/cost
  budgets and any E2 provider execution require separate review/authorization.

E1 stops at user review. No production active view is published and no E2 is run.
