# G1 standalone field specification 0.1

This is the experimental serialization contract for
`schema_version=p05-navi-g1-0.1`; it does not alter Canonical, RU, Evidence
Packet, or production RAG schemas. JSON strings are UTF-8; offsets count Python
Unicode characters in the exact Canonical `normalized_text`. Ranges are
half-open `[start,end)`, not bytes. Arrays preserve source order unless stated.
No model-produced quotation is an evidence field.
Card/Link IDs are scoped to the immutable run identity; cross-run cache keys
must include that identity and the bound Canonical snapshot, not source ID alone.

## Common evidence and source objects

### Snippet (the only quote object)

| Field | Type / rule |
|---|---|
| `id` | string, unique within run; historical IDs preserved, new IDs encode source/section/unit/range |
| `source` | string, verified source content identity, not a semantic entity |
| `record_id` | string, exact Canonical record ID |
| `canonical_sha256` | string, hash of bound Canonical file |
| `section`, `unit` | integers, real Canonical ordinals |
| `section_name` | string, exact retained source module name |
| `start`, `end` | integers, exact Canonical character range |
| `text` | string, program extraction of that range, never LLM reconstruction |
| `text_sha256` | string, SHA256 of UTF-8 `text` |
| `official_unit_ids` | nonempty string array; exact intersecting official RU fragments in source order |

Complete occurrence RU reconstruction must equal Canonical text; a hash or
substring hit in another occurrence is insufficient. Snippets alone prove
neither the proposition nor source authorship.

### Source category and endpoint

`source_category` contains `channels:string[]`,
`category_screen:secondary_analysis|observer|catalog|unknown`,
`passage_authorship:UNVERIFIED`, `independent_history_witness:false`.
Category screening is derived from verified source memberships/title rules;
catalog hosting does not authenticate every paragraph as game text.

Each Link endpoint contains `cid`, `title`, `record_id`, `canonical_path`,
`canonical_sha256`, `source_category`. Source lineage is resolved through the
run's documents map (`source_lineage`, retaining Raw refs and Parsed pointer).
Paths are explicit input bindings, not provider prompts or public URLs.

`structure_inventory` contains one row for every Canonical unit:
`section:int`, `unit:int`, `section_name:string`, `kind:string`,
`characters:int`, `parent_component_key:string|null`,
`canonical_content_role:object|null`, `canonical_provenance:object|null`.
Empty, structured, unsupported and excluded units remain visible. Structure
inventory does not mean these units were read semantically.

## Navi Card

File envelopes: `cards-reused.json` / `cards-generated.json` contain
`cards:Card[]`, `failures:object[]`. Earlier failed pre-send projections have
separate filenames and remain auditable.

| Field | Type / rule |
|---|---|
| `schema_version` | exact version string |
| `card_id` | string; `g1:legacy:CID` or `g1:new:CID`, versioned within immutable run |
| `source_id`, `title`, `record_id`, `canonical_path`, `canonical_sha256` | strings, one Canonical management unit |
| `source_category`, `structure_inventory` | objects defined above |
| `parts` | ordered array of batch profiles, described below |
| `queries` | cached Query array, no fixed number required |
| `coverage` | Coverage object, described below |
| `reuse_status` | `REUSED_LEGACY_COMPATIBILITY`, `NEW_SOURCE`, or `NEW_VERSION` |
| `mechanical_status` | `PASS` or `FAIL`; source/schema/reading receipts only |
| `model_quality` | `LEGACY_NOT_G1_REVIEWED` or `SELF_CHECK_ONLY`; separate model checks live in quality artifact |
| `human_acceptance` | `NOT_REVIEWED` in this run |
| `navigation_status` | `INACTIVE` |
| `selection` | generated cards only: `cid`, `stratum`, `reason`; source-only cohort or legacy structure probe |

### Parts and Query

All parts have `overview:string` and `details:Detail[]`, `open_leads:Detail[]`.
Detail contains `text:string` and nonempty `evidence_ids:string[]`. IDs must
resolve to the delivered source. A bound ID proves where to check a claim;
it does not prove the model's interpretation. Arrays can be empty. There is
no mandatory need/supply/motif slot or fixed detail count.

New parts additionally contain:

- `queries:Detail[]`: source-only search expressions with evidence IDs.
- `read_ids:string[]`: exactly every ID in this delivered batch, no duplicate.
- `limitations:string`: source role, omitted context and interpretive limits.
- `origin:{job,model,response_sha256}`: exact request/response lineage.

Legacy parts additionally contain:

- `reading_receipt:{delivered_occurrences,read_ids,state}`. Each occurrence
  has `section`, `unit`, `start`, `end`, `id` copied from the historical read.
  `state=HISTORICAL_DELIVERY_NOT_SEMANTIC_COMPLETENESS`.
- `origin:{path,sha256,model}` binds the original R1 response.
- Legacy Detail fields `binding_precision=legacy_occurrence_not_claim_span`,
  `legacy_quote_used=false`, `legacy_quote_exact:boolean`. The original raw
  quote remains in R1; it is never emitted as source evidence here. Invalid
  legacy refs are failure rows rather than silently repaired claims.

Card Query contains `text:string`, `query_id:string` (hash of text),
`evidence_ids:string[]`, `target_documents_supplied:false`, and `origin`:
`R1_CACHED_EXACT`, `DETERMINISTIC_LEGACY_CARD_VIEW`, or `G1_SOURCE_ONLY_CARD`.
Query cache identity is separate from proof of source-only semantics; the
no-target prompt and request audit establish delivery, and model quality
checks separately assess leakage/over-inference.

### Coverage

All cards contain `selected_scope_delivered:boolean` and
`whole_page_semantic_coverage:false`. Legacy coverage includes `excluded`
(historical unit dispositions with section/unit/reason) and `detail_limit`
(the old five-detail restriction). Generated coverage includes
`delivered_ids`, `selected_ids`, `unread_ids`, all string arrays. Batch read
IDs and coverage are delivery/acknowledgment, not exhaustive semantic recall.
No source is called complete merely because a short summary was generated.

## Navi Link

File envelopes `links-reused.json` / `links-generated.json` contain
`links:Link[]`; new generation also contains `failures:object[]`. Every
reused disposition, including failures and unreviewed rows, stays counted.

| Field | Type / rule |
|---|---|
| `schema_version`, `link_id` | strings; pair identity derives from source IDs in this frozen snapshot, never the claim text |
| `endpoints` | map `A`/`B` to Source endpoints; may be empty only for explicitly failed/unreviewed U |
| `proposition` | string, specific strongest safe joint-reading claim; empty for unreviewed failure |
| `relation_type` | `direct_reference`, `background`, `contrast`, `literary_parallel`, `shared_mechanism`, `duplicate`, `gameplay_only`, `none`, or failure `unknown` |
| `evidence_ids` | map A/B -> string arrays; both nonempty for mechanical dual-source PASS |
| `extracted_evidence` | map A/B -> Snippet arrays, selected by model ID and extracted/verified by program |
| `increment_a_from_b`, `increment_b_from_a` | strings; actual information added in each original review direction, never silently reversed |
| `explanation` | string, concrete cross-source correspondences and utility |
| `interpretation_boundary` | string, counterevidence, source limits, omissions, alternatives |
| `claim_level` | `textual`, `interpretive`, `speculative`, or unreviewed `unknown` |
| `endpoint_roles` | map A/B -> `game_text`, `in_world_literature`, `attributed_testimony`, `editorial_compilation`, `secondary_analysis`, `gameplay`, `unknown`; empty for legacy/unreviewed |
| `role_basis` | map A/B -> nonempty reasons; empty for legacy/unreviewed |
| `anchored_correspondences` | string array; concrete shared structures, not generic themes; empty legal for rejection/legacy |
| `unsupported_identity_or_cause` | boolean, or null for unreviewed |
| `model_verdict` | `useful`, `tentative`, `reject`, `insufficient`, `unreviewed` |
| `model_grade` | model's `A/B/C/D/U`, or legacy/failure `NOT_PROVIDED` |
| `grade_recommendation` | program's conservative `A/B/C/D/U` recommendation |
| `grade_policy` | `conservative_program_projection_0.1` |
| `mechanical_status` | `PASS` or `NOT_VERIFIED`, distinct from verdict |
| `model_review` | `REVIEWED_NOT_HUMAN_GOLD`, `MODEL_OUTPUT_SCHEMA_INCOMPLETE`, or `NOT_REVIEWED` |
| `human_acceptance`, `navigation_status` | `NOT_REVIEWED`, `INACTIVE` |
| `certainty` | `interpretation_not_established_history` for B, otherwise `no_history_certification` |
| `independent_history_witness` | false; no link certifies game history |
| `reuse_origin` | exact old artifact/pair/job/model, or new job/model/reuse=false, or explicit failed-job reason |
| `discovery` | preserved directional source/target/method/rank/RU metadata; never supplied to B |
| `generation_self_check` | new B's explicit self-check, or `LEGACY_NOT_PROVIDED` |
| `reading_coverage` | new successful B only: A/B delivered/scoped chars, complete_verified_scope, whole_page_coverage=false, omitted_snippet_count |
| `initial_grade_recommendation` | consolidated knowledge only: grade before targeted dispute handling |
| `review_disposition` | consolidated knowledge only: initial-only, same-model agreement, model disagreement, failed targeted review, or agent-flagged standard dispute; never human acceptance |
| `targeted_review_reference` | consolidated reviewed rows only: `{artifact,link_id}` pointing to both retained judgments |

A/B orientation is the original delivered review orientation. Discovery
direction remains separately preserved; use the source ID map to select the
correct directional increment. Reversing a source pair does not license
copying the other direction's information gain.

## Other artifacts and failure rules

- `documents.json`, `candidate-documents.json`: source maps with Canonical
  hash/path, lineage, real unit inventory and selected snippet IDs; candidates
  extend source inputs without changing the earlier map.
- `snippets.json`, `candidate-snippets.json`: immutable exact-evidence maps.
- `query-execution-plan.json`: every executed and omitted cached query with
  source ID; records the finite four-query projection policy.
- `rankings.json`: original Lexical/Dense/Hybrid RU rankings per query, source
  document RRF order, selected candidate positions and bound index hashes.
- `candidates.json`: directional pair IDs, source IDs, selected rank and RU
  support; frozen before B; disjointness flags are checked mechanically.
- `deliveries/`: exact source payloads; B additionally stores alias-to-snippet
  maps and endpoint orientation. `attempts/`, `responses/`, reconciliation
  projections retain original wire/stream/usage/terminal identities.
- `targeted-review-plan.json`, `targeted-reviews.json`: risk/outcome selection,
  first and separate model grades, agreement, both propositions, and explicit
  `RETAIN_BOTH_MODEL_JUDGMENTS_NO_HUMAN_ACCEPTANCE` resolution.
- `protocol-addendum-qualification.json`, `links-qualified.json`: explicit
  complete-schema follow-up for resolved base-valid missing-field outputs;
  initial failures and new calls remain separate. This is not an UNKNOWN retry
  or silent field fabrication.
- `navigation-knowledge.json`: consolidated inactive recommendations; disputed
  grades and failed A/B targeted checks enter C, binding/evidence failures U.
  `agent-review-findings.json` labels Codex judgments separately from humans.
- `ordinary-unique-replay.json`: actual cached-query/vector Hybrid replay,
  original/replayed Top20 and all 49 unique-candidate rediscovery dispositions;
  no model or embedding call and no new independent gold.
- `card-quality-checks.json`: per-card model coverage and query-quality
  judgments with selected source IDs, not semantic ground truth.
- `input-hashes.json`, `source-checks.json`, `summary.json`,
  `g1-artifact-inventory.json`: history/source/run integrity and separate
  reuse/new counts, failures, dispositions, requests/tokens and authority.

Missing IDs, endpoint mismatch, invalid semantic enums, fabricated quotes,
incomplete read receipts, unresolved execution, JSON errors or changed
immutable inputs fail closed. Keep failure records in denominators. A failed
new Link has U and empty endpoints/evidence, with actual sources retained in
its failure/discovery row. No guessed IDs, automatic JSON semantic repair,
unknown-request reissue or promotion to human acceptance is permitted.
