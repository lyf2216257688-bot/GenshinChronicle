# Phase05 R1 discovery comparison — 2026-10-25

Status: **COMPLETE as an experiment; semantic acceptance OPEN, production inactive**. Current authorization/status is owned by
`docs/current-phase.md`. This note owns the isolated experiment design and,
when available, measured results. No production adoption or human semantic
acceptance is implied.

## Question and frozen design

Test discovery of a hidden other source, separately from explaining a given
pair. The frozen scope contains 128 Canonical pages, 268,573 selected narrative
characters in 130 reading batches, and 24 evaluator-only reference proposals.
R0 families are development only; held-out families have separate source
identities and are never used for prompt tuning. Reference proposals are
analyst selected and separately model reviewed, not independently accepted
human gold. The scope is enriched research data, not a random corpus sample.

Routes include original Lexical/Dense/Hybrid, six-query budget controls,
source-only LLM rewriting, ordinary profiles, multiview A0 merged retrieval,
the same A0 texts/vectors/queries with role-filtered needs/supplies matching,
and narrative motifs plus bounded two-hop. Self sources are removed before
the 500-RU candidate arm budget. Document Top-5/10/20 and source occurrence
overlap are distinct measurements; neither proves exact quote delivery or QA
improvement. A content-only A0 diagnostic separates content generation from
adding perspective slots. B reads both source texts and records omissions.

Initial profiles are independent. Repeats use identical prompts and inputs;
arrival-order simulations use fixed cached inputs and do not deploy a new
scheduler. Invalid bindings, incomplete outputs, failed calls, abstentions
and unrevised proposal targets remain in the accounting.

Before any retrieval rankings were computed, profile validation exposed
non-contiguous/edited model quotations. The original strict exact-quote feature
gate is retained as the primary condition. A separately labelled sensitivity
condition reuses the same raw outputs with valid source locators but explicitly
unusable unbound quotes; it makes no semantic repairs and invokes no additional
generation. Merged and bidirectional retrieval share identical information
within each condition. Invalid JSON remains failed rather than being repaired.

## Sources and transport

Canonical run: `phase03-batch5b-p01eb-full-20260824`, source `mihoyo_obc`, locale
`zh-cn`. Formal RU build:
`49b48ee746716add0248fed388d10bd522a930efb582a0f5e827f66681ed8998`.
The existing 535,802-row lexical and Qwen 2048-dimensional Dense artifacts are
read-only inputs. A lossless sparse lexical acceleration is checked against
the existing field-aware formula with fixture tests. Only the selected 128
documents receive semantic profiles; full-corpus results must not be described
as having semantic profiles for all 16,437 documents.

The initial DeepSeek/Jizhi pilot and Qwen-Plus cohort stopped on configured
transport circuits. Their artifacts remain under:

- `data/retrieval/p05-navi-r1-discovery-20261025-r1/`
- `data/retrieval/p05-navi-r1-discovery-20261025-r2-qwen/`

The complete uniform model cohort is separately bound under
`data/retrieval/p05-navi-r1-discovery-20261025-r3-max/`: Qwen Max generates
every compared representation/query; the existing dated Qwen Plus snapshot
performs separate source-text reference/quality review. These are distinct
models, not independent human reviewers. The same-family model limitation
must remain visible.

### Protocol deviation

An audit found **one prohibited reissue** in the stopped `r2-qwen` cohort:
`cohort2-judge-canary` was UNKNOWN, then the same model/wire content was sent
again as `reference-1638-207`. A new job label did not make that safe. Both
attempts and their costs remain preserved; this cohort is excluded from the
complete-model comparison. `protocol-deviation-audit.json` records the finding.
The repaired runner quarantines request content across jobs and cohorts,
including equivalent JSON and delivery-accounting changes, with provider-free
regression tests. A claim of zero unknown-request reissues for the whole R1
campaign is therefore prohibited. The final cohort must be audited separately.

The operator reported moving from Beijing to Tokyo and authorized network
configuration investigation. The workspace service region remains Beijing.
Observed local networking uses FlClash TUN/fake-IP DNS and listening proxy
`127.0.0.1:7890`; the disabled Windows proxy retained a stale `2080` entry.
Unauthenticated probes reproduced intermittent TLS EOF. The research transport
warms and reuses verified TLS connections. Only positive TCP/TLS-failure traces
with no HTTP-header send permit a bounded reconnect; errors after send remain
UNKNOWN and are never retried. Credentials and trace info objects are not
persisted. No global network settings were changed.

## Budget and evidence

Issued reservations precede dispatch. Independent request/token/conservative
cost and unknown-execution circuits prevent runaway work; the complete cohort
reserves at most RMB 450 accounting units, leaving RMB 50 for preserved pilot
cohorts within a total RMB 500 conservative accounting envelope. These are
explicit accounting rates, not provider tariffs or an invoice. Actual billed
cost remains UNKNOWN without billing evidence. Successful token counts and
unresolved reservations are reported separately.

Every cohort preserves its protocol, source identities, requests, raw results,
execution state and validation outcomes. Generated data is under `data/`;
the opt-in runner and small provider-free tests do not change production RAG,
Generation, accepted embeddings, or source evidence.

## Interpretation and recommended first version

The measured operating point does **not** establish that multi-view A0,
role-separated needs/supplies matching, or fixed two-hop discovery beats simpler
retrieval. Recommend a retrieval-first offline Navi pipeline: deterministic
structure/body selection and a coverage ledger; original-text candidates;
optional lightweight content profiles in one merged index; and separate B
dual-source review before retaining a navigation link. Source-only query rewriting
is an optional candidate arm, with no measured general recall improvement here.
Do not require two role-specific indexes, exhaustive open-question generation,
fixed multi-hop traversal, V scheduling, KG or online production integration.
The smallest first version need not build a dedicated semantic profile index: retain lightweight reusable reading records, source retrieval with optionally cached rewriting, and B review. Profile indexing remains an optional arm until independently verified incremental links justify it. This is a recommendation for the next reviewed design, not production adoption.

Only 128 pages have profiles. The so-called full-corpus profile methods combine
full raw retrieval with that selected semantic subset. A post-ranking diagnostic
also combines full raw retrieval with **raw retrieval restricted to the same
128 pages**, using the same RRF. Its success matches the profile arms on held-out
Hit@20. Thus the headline improvement over unrestricted raw retrieval is
confounded by the selected subset. These controls were added after seeing ranks;
they are openly diagnostic, not a preregistered replication. No targets, prompts,
or original rankings were changed. The profile-generated query-only full-corpus
arms are separately preserved and do not show the same headline advantage.

### Concrete source checks

- `3121` 奇特的羽毛 has just 67 selected characters describing acquisition.
  The proposed `1632` 如雷的盛怒 relationship is an unsuitable positive target
  for this supplied body. Its pre-ranking reference review rejected it; it remains
  in the original denominator. No replacement target was selected afterwards.
- `6350` 水仙之梦 describes knights/dragons, the institution and subsequent
  separation; `500037` 逐影猎人 explicitly recalls childhood hero/dragon games
  and named companions. This is a concrete literary reading opportunity.
  The target ranks full raw Hybrid 25, rewrite 200, ordinary profile 4,
  A0 merged 11, bidirectional 29, bridge 17. Its automated supporting quotation
  fails exact binding. Rank improvement is observed; semantic acceptance is open.
- `1146` 竹林月夜 describes three moon sisters, a silver chariot and catastrophe;
  `3867` 辰砂往生录 recounts three sisters' deaths and the solar chariot's fall,
  while explicitly calling some material folk tradition. This non-proposal
  candidate rises from raw Hybrid 18/rewrite 156 to A0 merged/bidirectional 1.
  It is a lead for reading, not proof of all identities or a new gold label;
  its model quotation also failed the strict binding gate.
- `1612` 护摩之杖 and `1627` 胡桃 provide funeral/purification background.
  Full Hybrid 33 becomes ordinary 4 and merged/bidirectional 3. This does not
  identify every unnamed historical speaker as Hu Tao or her ancestor.
- Development case `1148` 醉客轶事 → `5404` 乐园遗落之花 ranks bridge 12,
  merged 17, bidirectional 20, ordinary 55; the raw Hybrid target is outside
  the retained window. Model discussion emphasizes dream/consciousness parallels,
  which does not fully establish the analyst's proposed fairy/teacher reading.

### Boundaries, validation failures and remaining uncertainty

Ordinary/A0 production used 292 reading requests including repeats: 290 valid
JSON profiles and 2 invalid outputs. The failed main A0 profile is distractor
`909`; main A0 has 127 usable documents, not 128. Ordinary main profiles cover
128; its failure occurs in one repeat. Exact contiguous-quote validation rejects
760 generated features (ordinary 376, A0 384). A locator-only sensitivity condition
keeps the same underlying outputs and marks bad quotes `UNBOUND_NOT_EVIDENCE`.
It does not repair text, change source evidence, or demonstrate a role-matching
advantage after the binding penalty is reduced.

The B judge often edits or combines quotations. Failed positive bindings remain
unreviewed; they are not automatically false associations. Reject/insufficient
verdicts did not require a supporting quote under this research gate. Early
artifacts nevertheless label those outputs `verified_for_delivered_text`;
that label is **not a verified quote claim** for those verdicts. The runner now
reports their actual quote status separately; immutable earlier outputs remain
preserved. This asymmetric gate makes reviewed-only useful/reject proportions
selection-biased. Useful lower/upper bounds and missingness are reported; the
experiment cannot establish reliable semantic precision or literary recall.

One development rewrite introduces a title absent from the source: `3470`,
repeat 0/query 0, 冰风迷途的勇士. The source-only constraint violation is retained
in `query-source-audit.json`; other forms of pretrained knowledge leakage cannot
be ruled out by this title audit. Hidden target lists were not supplied to
generation, and held-out prompts were not tuned on outcomes.

The body policy is a research selector, not comprehensive Canonical support:
rich text only, channel/module rules, minimum 55 characters, occurrence splitting
at 8,000 characters and 14,000-character reading batches. It excludes structural
carriers and may omit relevant short/structured dialogue. B sees at most 12,000
selected characters per side; omitted context remains a limitation. Book
aggregation and at most two needs/supplies/motifs per batch can miss local
mechanisms. The bridge uses two direct queries and four from two intermediate
profiles selected within the 128-page pool; its full arm subsequently searches
all raw RUs. It is not an unconstrained full-corpus multi-hop exploration.

Both independent and held-out terminology have narrow meanings: review uses
a different model from generation, but both are Qwen and there is no independent
human gold; 9 positive held-out proposals span only 7 families, with one unsuitable
target. These are discovery proposals, not a population recall estimate. The
experiment cannot select DeepSeek on semantic quality because its cohort stopped
on transport. No production BGE reranking, Evidence Packet admission or final-QA
comparison was performed. Phase05 semantic acceptance stays OPEN.

### Next recommended work, requiring a new decision

Before any full knowledge production, independently review the frozen proposals
and a method-blind union of candidate pairs, explicitly including rejected,
unbound and novel literary candidates. Then freeze a larger family-separated
evaluation with matched source-scope priors and compatible embeddings. Prefer
program-extracted quotes from verified locators to asking the model to recreate
quotes. Test a lightweight content profile/merged index against scope-matched
raw retrieval; retain extra perspective fields only if they add valid unique
links. Do not infer that all A0 or bidirectional variants have been disproved by
this one bounded operating point. V's delivery/coverage/checkpoint mechanics
remain reusable, while active exploration and automatic triggers stay optional.

## Measured tables and audit

### Held-out positive proposal hits

Every cell uses the original 9 held-out proposals, including the unsuitable feather target. Hit@20 counts any proposed target; macro recall also accounts for partially found multi-target cases. These are not independently accepted gold scores.

| Method | Local Hit@5 | Local Hit@20 | Full/subset Hit@5 | Full/subset Hit@20 | Full macro recall@20 |
|---|---:|---:|---:|---:|---:|
| raw_lexical | 7/9 | 7/9 | 5/9 | 7/9 | 0.722 |
| raw_dense | 5/9 | 8/9 | 2/9 | 5/9 | 0.500 |
| raw_hybrid | 7/9 | 8/9 | 4/9 | 6/9 | 0.611 |
| raw_hybrid_budget6 | 6/9 | 8/9 | 4/9 | 5/9 | 0.556 |
| rewrite_hybrid_r0 | 6/9 | 8/9 | 4/9 | 6/9 | 0.611 |
| ordinary_profile | 6/9 | 8/9 | 8/9 | 8/9 | 0.889 |
| a0_content_only | 5/9 | 7/9 | 7/9 | 8/9 | 0.889 |
| a0_merged | 5/9 | 7/9 | 7/9 | 8/9 | 0.889 |
| bidirectional | 4/9 | 4/9 | 7/9 | 7/9 | 0.778 |
| narrative_bridge | 2/9 | 3/9 | 5/9 | 8/9 | 0.833 |
| a0_merged_locator | 7/9 | 7/9 | 7/9 | 8/9 | 0.889 |
| bidirectional_locator | 6/9 | 7/9 | 7/9 | 8/9 | 0.889 |

Post-ranking scope controls, using full raw retrieval plus the same 128-source raw subset:

| Diagnostic | Hit@5 | Hit@20 | Macro recall@20 |
|---|---:|---:|---:|
| raw_subset_augmented | 8/9 | 8/9 | 0.889 |
| raw6_subset_augmented | 8/9 | 8/9 | 0.889 |
| rewrite_subset_augmented | 7/9 | 8/9 | 0.889 |

Without subset augmentation, full-corpus profile-derived query-only Hit@20 is ordinary 5/9, A0 6/9, bidirectional 6/9, bridge 4/9. The A0 and bidirectional query-only arms use identical queries, so their equality is expected.

### Quality, unique increments and controls

B processes all 959 selected pairs, with 2 additional scope-control pairs. Main results: 8 useful, 28 tentative, 445 reject, 1 insufficient, 477 unreviewed. Of the unreviewed pairs, 454 have failed supporting quote/ref validation, 12 have no selected narrative body, 10 are execution UNKNOWN and 1 uses an invalid verdict. There is no automated semantic PASS.

Held-out full-arm Top5 contains 45 distinct source/target slots per method. Counts below are method-blind model judgments, not precision against human truth. Reject did not require exact supporting quotes; missingness is strongly asymmetric.

| Method | Useful | Tentative | Reject | Unreviewed |
|---|---:|---:|---:|---:|
| raw_hybrid | 4 | 2 | 6 | 33 |
| raw_hybrid_budget6 | 4 | 2 | 7 | 32 |
| rewrite_hybrid_r0 | 2 | 3 | 7 | 33 |
| ordinary_profile | 2 | 2 | 10 | 31 |
| a0_merged | 2 | 3 | 10 | 30 |
| bidirectional | 2 | 4 | 12 | 27 |
| narrative_bridge | 1 | 3 | 13 | 28 |
| raw6_subset_augmented | 2 | 3 | 14 | 26 |

New Top5 pairs relative to the same-scope raw6 subset control:

| Method | New pairs | Useful | Tentative | Reject | Unreviewed |
|---|---:|---:|---:|---:|---:|
| ordinary_profile | 12 | 0 | 0 | 4 | 8 |
| a0_merged | 18 | 0 | 1 | 8 | 9 |
| bidirectional | 16 | 0 | 2 | 6 | 8 |
| narrative_bridge | 25 | 0 | 2 | 11 | 12 |

Thus no strictly bound useful unique Top5 increment is established over the scope-matched control in the held-out seeds; tentative links and numerous unreviewed pairs remain candidates for independent review. This is not proof that all unreviewed candidates are false. Six explicit bad propositions checked by both models produce 10 reject and 2 insufficient, zero endorsements. The duplicate control is not counted as two independent historical witnesses.

### Stability and incremental arrival

| Route | Repeat comparisons | Mean Top20 Jaccard | Minimum |
|---|---:|---:|---:|
| a0_merged | 15 | 0.655 | 0.379 |
| bidirectional | 15 | 0.477 | 0.176 |
| ordinary_profile | 15 | 0.686 | 0.481 |
| rewrite (full) | 40 | 0.429 | 0.143 |
| rewrite (local) | 40 | 0.588 | 0.290 |

Eight sources have three independent ordinary/A0 generations. One invalid ordinary repeat leaves 15 usable comparisons per profile route; both source and repeated target slots change in this sensitivity analysis. Rewriting has three generations for 20 seeds (40 baseline/repeat comparisons). Identical temperature does not produce stable ranks.

Arrival simulations preserve fixed profiles and use six reversed-role queries per new source over existing sources. The source-ID, reverse and random orders recover respectively 5/9, 5/9 and 3/9 held-out proposals. Their edge-set Jaccard is 0.233 to 0.406. This demonstrates bounded incremental matching mechanics and order sensitivity, not a deployed scheduler or chronological coverage guarantee. No claim of finding every old unresolved need is supported.

### Engineering comparison

| Route | Additional offline work | Main limitation at this operating point |
|---|---|---|
| Raw retrieval | Existing lexical/Dense indexes | Body noise, query projection and archived vector compatibility |
| Source-only rewrite | One model read and six queries per seed | No held-out Hit@20 gain; unstable, one title constraint violation |
| Ordinary profile | One independent profile per reading batch, derived index | Subset and body-selection confounding; reusable compact navigation representation |
| A0 merged | Content/needs/supplies/motifs from one A0 generation | Extra features do not establish independent useful increment |
| Bidirectional | Same A0 information/vectors and six queries, extra role masks | Worse strict local hits and repeat stability; hard masks can discard useful candidates |
| Bridge/two-hop | Two direct and four intermediate-profile queries | Error propagation and low local recall; both edges require B review |
| B pair review | Read both bodies for every admitted candidate | Largest measured cost; quotation failures prevent reliable semantic acceptance |

The two A0 index mechanisms share exactly the same generated A0 information and vectors within each binding condition. Different access rules, rather than extra generation, produce their differences. Profile methods use different text tokenization/statistics from the formal RU lexical index; the comparison is between complete research routes, not an isolated embedding-only causal effect. Raw six-query controls can repeat a short source passage; equal query counts do not imply six distinct facts or equal generation cost.

### Artifact map

- `protocol.json`, `operating-point*.json`: frozen prompts, budgets and explicit operating amendments.
- `corpus.json`, `evaluator-only/`: scope, immutable proposals and pre-ranking reference review.
- `profiles.json`, `rewrites.json`, `responses/`, `attempts/`, `code-snapshots/`: input/output and exact implementation provenance.
- `rankings.json`, `local-profile-rankings.json`: original method rankings and RU/occurrence supports.
- `quality.json`, `quality-continuation.json`, `quality-final.json`: preserved review passes and only two pre-reservation local failure completions.
- `scope-control-rankings.json`, `scope-controls-report.json`: diagnostic controls with two extra pair reviews.
- `stability.json`, `negative-controls.json`, `supplemental-analysis.json`: repeats, order/control results and explicit useful bounds.
- `embedding-drift-selection.json`, `embedding-drift.json`: exact-input 12-RU archived/current geometry check.
- `matched-body-selection.json`, `matched-body-control.json`: post-ranking current/archived embeddings on the same 586 scoped body RUs.
- `protocol-deviation-audit.json`, `prewarm-interruption-audit.json`: protocol and local-interruption disclosure.
- `artifact-inventory.json`, `final-audit.json`: final hashes, source/credential and UNKNOWN reissue checks.

All paths above are relative to `data/retrieval/p05-navi-r1-discovery-20261025-r3-max/`. Main generated reports do not incorporate later controls automatically; this note and the supplemental/control JSONs provide their interpretation.

## Geometry/body diagnostic and final cost

The exact-input 12-RU diagnostic has minimum cosine 0.955 and median 0.975 between archived and current document embeddings. A further 586-RU check (583 unique texts) has minimum 0.662 and median 0.972. All inputs are the original `retrieval_visible_text`, dimension 2048 and document role. The discrepancy is observed; remote-alias changes, Batch/synchronous differences or another cause remain unresolved. No archived input or production index was modified. Full-corpus Dense comparisons therefore retain a compatibility limitation. This is not a controlled finding about DeepSeek or a proven consequence of moving to Tokyo.

The post-ranking matched-body diagnostic restricts both archived/current raw methods to the same 586 narrative RUs from the 128 selected sources, preserving formal BM25 weights/statistics and all original queries. It cannot resolve the full-index space mismatch, but provides a body/geometry-matched local countercheck.

| Query / retrieval | Archived-body Hit@20 | Current-body Hit@20 | Current-body Hit@5 |
|---|---:|---:|---:|
| raw / lexical | 7/9 | 7/9 | 7/9 |
| raw / dense | 7/9 | 7/9 | 6/9 |
| raw / hybrid | 7/9 | 7/9 | 7/9 |
| raw6 / lexical | 7/9 | 7/9 | 7/9 |
| raw6 / dense | 6/9 | 7/9 | 6/9 |
| raw6 / hybrid | 7/9 | 7/9 | 6/9 |
| rewrite / lexical | 8/9 | 8/9 | 8/9 |
| rewrite / dense | 6/9 | 7/9 | 6/9 |
| rewrite / hybrid | 8/9 | 8/9 | 6/9 |
| ordinary_query / lexical | 7/9 | 7/9 | 7/9 |
| ordinary_query / dense | 7/9 | 8/9 | 6/9 |
| ordinary_query / hybrid | 7/9 | 7/9 | 7/9 |
| a0_query / lexical | 7/9 | 7/9 | 6/9 |
| a0_query / dense | 7/9 | 7/9 | 6/9 |
| a0_query / hybrid | 7/9 | 7/9 | 7/9 |

With the current embedding/body control, original Hybrid is 7/9, source-only rewriting + Hybrid is 8/9, and ordinary-profile querying of the raw body is 7/9 (Dense alone 8/9). Original local ordinary-profile retrieval is 8/9. A0-generated queries on current raw body Hybrid remain 7/9. This supplies no convincing reason to require a dedicated semantic profile index instead of cached rewriting plus source retrieval. These diagnostic arms were not separately subjected to a new exhaustive Top5 B union; original primary arms share the complete 959-pair evaluation. No new human-gold assertion is made.

| Cohort | Model reservations | Embedding reservations | Resolved | UNKNOWN | NOT_SENT | Known accounting RMB | Known + reserved RMB |
|---|---:|---:|---:|---:|---:|---:|---:|
| p05-navi-r1-discovery-20261025-r1 | 16 | 1 | 8 | 6 | 3 | 0.452 | 5.011 |
| p05-navi-r1-discovery-20261025-r2-qwen | 143 | 0 | 122 | 21 | 0 | 8.774 | 20.045 |
| p05-navi-r1-discovery-20261025-r3-max | 1348 | 267 | 1605 | 10 | 0 | 106.898 | 113.302 |

Total known usage: **5,592,511 tokens**. Conservative known-response accounting: **RMB 116.124**. Including unresolved reservations: **RMB 138.357**, under the RMB 500 campaign ceiling. **37 UNKNOWN attempts** remain unresolved across all cohorts; actual billed amount is UNKNOWN. NOT_SENT is not a proven paid call. Connection warming has no credentials/model payload. Request reservations can include bounded, proven unsent connection attempts; they are not synonymous with billed model executions.

Final-cohort successful accounting by stage (includes repeats and diagnostics):

| Stage | Input tokens | Output tokens | Known accounting RMB |
|---|---:|---:|---:|
| embedding | 479,122 | 0 | 2.396 |
| negative | 35,772 | 2,850 | 0.765 |
| other | 396 | 387 | 0.037 |
| profile-a0 | 260,587 | 102,523 | 12.111 |
| profile-ordinary | 246,279 | 91,611 | 11.023 |
| quality | 3,581,740 | 279,076 | 76.052 |
| reference | 103,349 | 10,857 | 2.419 |
| rewrite | 80,577 | 11,097 | 2.096 |

B review dominates this experiment, whereas ordinary and A0 generation use similar reading budgets; splitting identical A0 slots into role channels requires no extra model generation. The conservative ledger rates (15/80 RMB per million model input/output tokens; 5 RMB per million embedding input tokens) are accounting limits, not a verified price quote. Full-corpus production costs cannot be inferred by simply scaling 128 enriched pages, particularly with B candidate growth and empty/unsupported bodies.

Final cost owner: `final-costs.json`; the earlier `summary.json` cost snapshot precedes later controls. The initial absolute UNKNOWN circuit stopped the main review at 496/959; amendment 03 raised only that absolute threshold to 50 while preserving the 20% ratio, request/token/cost ceilings and all UNKNOWN quarantines. A later local warming-log collision was repaired without overwriting logs. The long unauthenticated warm was stopped with zero outstanding paid reservations and zero issued requests for the affected last pair, then warming alone was bounded to 10 seconds. These interruptions do not justify retrying any execution-unknown paid request.

## Final verification

The final audit checked 334 source Canonical files with zero hash mismatches, 11,625 experiment files in the hashed inventory, zero configured-key leaks in scanned uncompressed artifacts, zero outstanding paid reservations and zero UNKNOWN-content reissues in r3. Across the full campaign it confirms exactly the disclosed r2 reissue, not zero. All 8 useful and 28 tentative main judgments have both supporting quotes bound; 321 reject and 1 insufficient outputs do not have verified dual quotes (reject did not require them), and 124 reject outputs do. Early blanket verified labels must be interpreted with this audit qualification.

Provider-free verification: 19 R1 guards, 6 model-interface consistency tests, and 55 existing V tests PASS (80 total). `git diff --check` PASS; no full-repository test claim. The production retrieval/generation source/configuration is unchanged. No commit, push, deployment, full-corpus formal semantic build or KG work occurred. The operator's original uncommitted discussion edits were retained while updating their current-state owner.
