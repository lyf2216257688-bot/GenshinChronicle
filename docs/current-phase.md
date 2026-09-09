# Current Phase

## Active phase

**Phase 04 - First End-to-End RAG implementation (post-W7 direction reset; P04-RAG-W2 ACCEPTED)**

## Objective

Build the first real, runnable end-to-end RAG baseline on top of the accepted
Raw -> Parsed -> Canonical foundation, then use real human-authored questions
to measure the whole system and optimize the actual bottleneck.

The current baseline direction is:

```text
Canonical
-> Retrieval Units
-> BM25 + local Dense + simple Hybrid
-> deterministic Evidence Assembly
-> provider-neutral Evidence Packet
-> GenerationProvider
-> Answer + Citations
```

This is a **first runnable baseline**, not a final architecture freeze. Models,
retrieval parameters, context-expansion settings, fusion details, storage,
GenerationProviders, and later optimization layers may change when actual
evaluation evidence justifies the change.

## Current status

Repository initialization, Collector v0.1, discovery/contract verification (01A–01C), offline acceptance, and the scoped Collector security gate are complete; `GC-COLLECTOR-001` and `GC-COLLECTOR-002` are closed with 0 remaining findings. P01-EA was completed and checkpointed at `0c5617d`.

P01-EB full crawl (`run_id=p01eb_full_20260824`) is complete and locally auditable: 96/96 listing responses, 32,916 listing records, 16,437 unique `content_id` values, and 16,437/16,437 successful detail responses, with 0 final unresolved failures. Archive/hash/inventory audit and same-run recovery passed. OBC full-corpus profiling is complete. Current production scope is `zh-cn` + OBC only. These results are run-level and contract-bounded; `manifest complete` is not a claim of absolute semantic completeness of the upstream server.

Phase 02 Parsed Schema / Parser Foundation is **CLOSED** at checkpoint
`5c2d131` (`phase02: add incremental parsed acceptance pipeline`). Batch 4
added the executable OBC Raw-to-Parsed run pipeline, immutable Parsed record
storage, Raw dependency accounting, versioned reuse/reparse decisions, and
deterministic corpus-wide acceptance. The accepted P01-EB evidence accounted
for 16,437/16,437 input details, with 0 `blocked_integrity` records. An
independent same-run reuse check produced 16,437 reused records and 0
reparsed records; full local regression passed 53/53 tests.

This detail-level Parsed accounting is an auditable local result, not a claim
of upstream semantic completeness or that every nested component has a
complete semantic handler. The Parsed schema remains a design draft rather
than a frozen contract.

Phase 03 Canonical Schema is **CLOSED / PASS**. Its approved architecture was
completed through Batch 1 (`0f7cff4`), Batch 2 (`e34468e`), Batch 3
(`0fc609b`), Batch 4 (`f287bef`), and Batch 5A (`4726d8a`). The separate
Batch 5B production gate projected the accepted
`phase02-batch4-p01eb-full-20260824-closure-v2` Parsed run to
`phase03-batch5b-p01eb-full-20260824`: its manifest is `complete`, with
16,437 input and accounted records, 0 input-integrity failures, 0 reuse, and
16,437 reprojected Canonical records. Streaming audit verified all 16,437
record paths, hashes, JSON objects, record IDs, and Parsed identity keys.

Phase 01 Raw Collection, Phase 02 Parsed Schema / Parser Foundation, and
Phase 03 Canonical Schema are **CLOSED**. Phase 03 establishes a full OBC
`zh-cn` structural Canonical production projection; it does not establish
semantic completeness, complete unsupported-structure understanding, final
dialogue/speaker semantics, cross-snapshot identity, or Retrieval/RAG quality.

## Active work unit

**P04-W1 — Corpus Profiler + Retrieval Benchmark Foundation** is CLOSED / PASS
at `3cf9694`. **P04-W2 — Derived Retrieval Representation + Lexical Baseline**
is CLOSED at `7d8c510`. **P04-W3 — Diagnostic Benchmark Expansion + Lexical
Failure Isolation** is CLOSED at `34069c2`. **P04-W4 — Diagnostic Evidence
Expansion + Retrieval-Family Decision Gate** is CLOSED / PASS at `2f292f3`.
Its final decision is **D — mixed / UNKNOWN**: it added a bounded
evidence-grounded diagnostic benchmark and a fixed A/B analyzer matrix over
unchanged r02 Retrieval artifacts, without rerunning Canonical projection or
modifying Raw, Parsed, Canonical, or profiler data.

**P04-W5 — Benchmark Reliability & Coverage Expansion** is CLOSED / PASS at
`8ba49d1`. It froze `benchmark-v0.3` before its single A/B matrix run,
preserved all 12 v0.2 anchors, expanded the diagnostic evidence with explicit
Canonical/r02 coverage eligibility, and kept all Retrieval representations,
analyzers, scorer, and Canonical data unchanged. Its measurement evidence is
`docs/research/phase-04/p04-w5-benchmark-reliability.md`: structured overall
improved 5/5 from A to B, but W5-new structured is only 2/2 for the same new
entity and is not cross-entity replication; dialogue and paraphrase remain
mixed; natural HN did not form top-10 contamination. It does not authorize an
analyzer follow-up, representation redesign, Dense pilot, routing/down-rank,
or another Retrieval technology path.

**P04-W6 — Dense family-isolation pilot** is **CLOSED / PASS**. The fixed
`contextualized_leaf` pilot completed with 242,965 documents using the pinned
`BAAI/bge-small-zh-v1.5` snapshot; embedding and frozen benchmark-v0.3
evaluation artifacts passed provenance and integrity review. Dense showed
credible complementary signal, but paraphrase-family repeated rescue was not
established, Dense is not a standalone winner, and the Retrieval winner
remains **UNKNOWN**. Hybrid, ANN/vector DB, reranking, routing/down-rank,
larger models, query rewrite, and representation changes are not authorized.
**P04-W7 blind leaf-only evidence expansion**: the legacy benchmark sanitizer
and Unit 2 structural runner, performance repair, and one production Unit 2
run are CLOSED / PASS. The Unit 3 execution contract is APPROVED / FROZEN.
Unit 3A-1 pre-exposure implementation and its technical-lead actual code gate
are ACCEPTED / PASS. Unit 3A-2 committed production mechanical extraction is
ACCEPTED / FROZEN. Unit 3B-0 pre-exposure production persistence tooling and
its technical-lead actual source/test/docs gate are PASS at checkpoint
`1126a8d`; post-checkpoint generator binding is PASS. Its proven scope is
persistence and write/action ordering only: **write-order correctness !=
read/exposure-order correctness**. Historically, the first ZERO-EXPOSURE
runtime preflight was PASS, semantic exposure OCCURRED, and the contaminated
first production semantic execution WAS BLOCKED at that incident point;
scientific judgments were 0/48 at that incident point. Query quality checking
was NOT EXECUTED; `QUERY_AUTHORING = NOT EXECUTED`; `REAL_C1_CHECK = NOT
EXECUTED`; finalization was NOT EXECUTED;
`BENCHMARK_V0_4 = NOT AUTHORIZED`; and `RETRIEVAL_EVALUATION = NOT AUTHORIZED`.
A-2 remains VALID / FROZEN. The contaminated semantic executor is RETIRED from
further Unit 3 semantic-review and query-author roles. Incident classification
is FINAL C — `SAFE_HELPER_EXISTS_BUT_NOT_EXCLUSIVE_OR_STATE_BOUND`.
The previous accepted exposure/materiality implementation checkpoint is
`225baa7` (pre-finalization-repair source identity); it is not the current
working-source implementation or binding. The minimum persisted
material-quality retry representation/wiring and mechanically separate
production-shaped quality-authority wiring were checkpointed there. The
pre-finalization-repair application-level exclusive/state-bound candidate-body
exposure implementation was checkpointed there as well. The approved
post-incident requirement is a trusted blind executor plus an application-level
mechanical fail-closed gate; OS/filesystem/shell isolation is not required or
claimed for W7. Post-incident exposure authority is owned by
`docs/phases/phase-04-w7-unit3b-exposure-amendment-v1.md`; material-quality retry
authority is owned by
`docs/phases/phase-04-w7-unit3b-material-quality-retry-addendum-v1.md`. The
exposure amendment is effective/frozen at its accepted authority checkpoint;
the material-quality addendum is effective/frozen at checkpoint `46fb3aa`; the
classification gap is closed by that repository-effective authority. The
readiness rule is: candidate N full lifecycle terminal -> candidate N+1 body
may be exposed. No surviving state attributable to the failed semantic
execution was found within the committed source-observable Unit3B runtime
footprint; this does not include or invalidate the pre-existing A-2
accepted/frozen manifest and pack. The independent source/test audit was
PARTIAL at audit time because authoritative N→N+1 exposure-readiness semantics
were then absent; its source findings remain valid. The exposure amendment is
repository-effective / FROZEN; the previously missing
N→N+1 exposure-readiness semantics are now authoritative. The repaired-state
ZERO-EXPOSURE preflight is PASS / CLOSED, with ZERO candidate-body,
future-candidate-body, and real-overlap exposure and ZERO production runtime
state created or modified by the preflight. P04-W7 Unit3B Frozen48 production
execution is PASS / CLOSED: 48/48 semantic states are mechanically complete /
persisted; observed result is 0 ACCEPT / 48 REJECT
(`ANCHOR_NOT_VALID_POSITIVE` ×48). Independent semantic re-adjudication was
NOT PERFORMED and scientific interpretation is DEFERRED. No authored query
attempts, quality judgments, restricted C1, or author feedback occurred.
Production finalization is COMPLETE / PERSISTED / ACTUAL-GATED. Its terminal
manifest is
`data/retrieval/p04-w7-unit3-contextualized-leaf-r01/metadata/unit3_manifest.json`
(3018 bytes; SHA-256
`512eb2fafa44c05c8b7d1d218fc163d051177b2208f89e355e83bf25bb3dcf73`).
Filesystem existence, byte-count, and SHA identities were independently
actual-matched; row-count and accounting values are terminal-manifest /
authoritative-finalizer derived and consistent with the actual-gated
deterministic finalization contract. The terminal manifest is the primary
owner of detailed child artifact identities and accounting. Selected
semantic/control/WR/HN = `0/0/0/0`; quota shortfall = `8/4/6/6`; all queues
are `SHORTFALL / EVIDENCE_INSUFFICIENT`; no refill. Terminal-manifest row
counts are final ledger 48 and freeze candidates 0. `benchmark_v0_4 =
NOT_CREATED` and `retrieval_evaluation = NOT_EXECUTED`; neither is
automatically authorized by this closure. Engineering blocker = NONE. The
sanctioned semantic-facing FINALIZE integration repair is actual-gated and
checkpointed at `50a7afaafa2a7dd79fbdf8505d917f52800d4478`, the current
`w7_unit3.py` tooling/source-binding checkpoint. `225baa756d5870a4c5dc5144c717aec75fdf1b72`
remains the previous/pre-finalization-repair implementation identity. The old
contaminated first-production semantic executor remains RETIRED / MUST NOT
REUSE.

The approved Phase 04 boundary is documented in
`docs/phases/phase-04-retrieval-evidence-assembly.md`.

## Post-W7 direction update

P04-W7 Frozen48 production / finalization / outcome closure remains **PASS /
CLOSED**. Its observed result was 0 ACCEPT / 48 REJECT; it remains a valid
negative result for the frozen W7 candidate-construction design and does not
establish a Retrieval-family winner.

The user subsequently stopped the Frozen48 D1/body-failure-taxonomy path and
also stopped the separately sent Semantic-First Benchmark Design Plan.
Neither is a pending task and neither should be resumed or resent.

Current project priority is now **First End-to-End RAG**. The governing design
direction is:

`docs/phases/phase-04-first-end-to-end-rag-amendment-v1.md`

Historical authorization statements inside the W1-W7 status paragraphs above
remain valid as historical snapshots of those work units, but they do **not**
override this later current-state direction. In particular, old statements
that Hybrid, RRF, embedding, or downstream RAG work were "NOT AUTHORIZED" are
not current permanent technology bans.

That amendment records the agreed first runnable baseline direction.
**P04-RAG-W1 — Versioned Retrieval Unit Builder + Deterministic Evidence
Assembly Core** is implemented, has passed technical-lead actual review, and
is **ACCEPTED**. It added only rebuildable Retrieval Units, audited
skip/failure ledgers, candidate-neutral structural Assembly, and
provider-neutral Evidence Packet JSON/Markdown. It did not run a production
corpus build or add BM25, Dense, Hybrid, embeddings, GenerationProvider,
benchmark production, or a Canonical change. Later work remains separately
scoped and authorized; this does not constitute a permanent rejection of
reranking, ANN/vector DB, different embeddings, different Hybrid methods, or
additional GenerationProviders when end-to-end evidence justifies them.

P04-RAG-W2 — Versioned Candidate Retrieval over W1 RU (BM25 + local Dense +
deterministic RRF Hybrid) implementation and narrow Dense correctness closure
are complete and accepted at the scoped technical-lead gate. It uses separate lexical/Dense build identities, local
bge-small-zh-v1.5 vectors over W1 Retrieval Units, deterministic candidate
serialization, and direct candidate-neutral Evidence Assembly handoff. No
GenerationProvider, benchmark production, or production-scale corpus run was
performed.

The first Qwen3.7 Embedding Challenger synchronous-preflight seam and its
Qwen-specific DashScope synchronous transport adapter are **IMPLEMENTED /
OFFLINE-TESTED**. They fix the challenger operating point to
`qwen3.7-text-embedding`, 2048 dimensions, dense-only output, document/query
roles, and no custom query instruction. The adapter maps those semantics to
the configured DashScope synchronous embeddings URL with `text_type` and
`output_type` in `parameters`; its region, workspace, and endpoint provenance
is explicit and secret-free. It writes only a locally validated
Dense-compatible artifact with remote-provider provenance; it adds no Batch
path, full-corpus build, 70Q query-vector artifact, or production caller.
Accepted BGE artifacts, BM25, RRF, Top-K, and Formal Deferred Assembly remain
unchanged. Offline adapter tests do not establish live Qwen capability: actual
region/workspace availability, 2048-dimensional provider support,
returned-model/role availability, and live provider behavior remain
**UNKNOWN** pending separately authorized live synchronous preflight.

Production materialization attempt #1 is retained as a partial run: RU and
lexical artifacts were generated, while Dense was not generated because of a
Dense runtime-preflight gap that was subsequently repaired. The runtime-
preflight repair actual gate is **PASS**.

Production materialization attempt #2 is **COMPLETE / PASS** under the
technical-lead actual evidence gate: the Canonical input contains 16,437
complete records; W1 produced 535,802 Retrieval Units with failure count 0;
lexical and Dense each contain 535,802 rows bound to the same W1 RU build.
Dense uses the pinned local-only CPU bge-small-zh-v1.5 model, revision
`7999e1d3359715c523056ef9478215996d62a620`, SHA-256
`354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026`, with
512-dimensional FP32/L2 vectors. Evidence Packet JSON and Markdown were
generated for q01/q02 × lexical/dense/hybrid. q01 Hybrid was repeated with
the same artifacts, query, and configuration and produced byte-exact JSON and
Markdown outputs; determinism PASS.

Therefore, the production Retrieval → Evidence Packet baseline is
**MATERIALIZED / PASS**. This does not mean that full RAG is complete.
P04-RAG-G1 offline Generation control baseline is **PASS / CLOSED** at the
technical-lead actual source/test gate: focused Generation tests passed 14/14,
affected Retrieval/Evidence Assembly regression passed 73/73, and the normal
Windows full suite passed 240/240. It adds a provider-neutral Generation
request/result contract, a
generation-visible Evidence Packet projection, deterministic request/config
identities separate from nondeterministic execution occurrences, local citation
membership/coverage validation only, and one injected-transport Bailian/Qwen
control adapter. Its baseline configuration requires exact model
`qwen3.7-plus-2026-05-26` and explicitly configures
`enable_thinking=false`; aliases and challengers are not implemented. It made
no real API call. Invalid control configuration fails before any provider
invocation or occurrence and is not serialized as a Generation result; result
statuses describe only valid-config occurrences. Citation
`semantic_faithfulness` is invariantly `not_evaluated`, rather than a local
semantic verdict.

**P04-RAG-G2 — Bailian live transport + one-request Qwen control smoke** is
**PASS / CLOSED**. Its offline source/security/test gate passed. The user then
personally ran exactly one approved `q01/hybrid` production Packet smoke using
`qwen3.7-plus-2026-05-26`, explicit `enable_thinking=false`, and
`max_attempts=1`: it produced `succeeded` after exactly one HTTP 200 provider
attempt with `finish_reason=stop`. Local citation integrity and configured
coverage both passed. The persisted `generation_result.json` SHA-256 is
`704e24bff6ba811ceee9e69dbbb5feca6ceb83e5430a65af3af9b6e4be3ca936`.
This proves the scoped provider transport, local Generation integration, and
citation structural path; it does **not** assess semantic faithfulness or
answer quality, which remain `not_evaluated`. The approximately 70-question
open-ended scenario Measure and Retrieval/Embedding/Rerank challenger work are
`NOT STARTED`.

**P04-RAG-M1 — six-question lightweight diagnostic Measure** is **PASS /
CLOSED**. Its purpose was a small human-reviewed diagnostic of Evidence
Packets and answers, not a formal semantic benchmark or gold-truth closure.
`run-001` is preserved and was not reused: system Python lacked the local
Dense runtime, so it stopped before provider invocation with 0 provider
attempts. The bundled `.local/w6-runtime` actual-preflight repair then passed.
`run-002` completed all 6 accepted questions with 6 provider attempts. q020
exposed truncation at the fixed `max_output_tokens=1024` control value; this is
not a Retrieval failure. Generation output length is therefore a
user-configurable product direction. M2 selects `max_output_tokens=2048` as
one explicit 70Q operating point to avoid repeating that observed limit; it is
not a long-term product optimum. The Dense execution batch now shares one verified encoder and
performs one query encoding per question; preflight remains a separate actual
probe. M1 semantic interpretation remains lightweight and observed, not a
gold-benchmark conclusion. No Retrieval, Embedding, or Rerank challenger work
is authorized by M1.

**P04-RAG-M2 — 70Q Open-ended Scenario Measure** was executed under the
user's direct-Execute decision. Its offline implementation enforced that:
the reviewed source is strictly parsed as exactly Q001–Q070, while the runtime
input contains only `question_id` and unaltered `question` text. `参考答案` and
`人工审核` are held in a separate review-only sidecar and cannot enter
Retrieval, Evidence Packets, Generation, or request identity. M2 reuses the
checkpointed lexical/Dense/Hybrid Packet path and hybrid Generation path, with
one actual Dense preflight, one verified encoder per batch, one query encoding
per question, 70 single-attempt provider occurrences, per-question artifacts,
and a compact review index for later semantic triage. The pre-live actual
review found redundant per-question arm loading/ranking in the inherited path;
the bounded M2 batch-reuse repair verifies and
loads the existing lexical/Dense indexes once, computes each arm once per
question, and builds Hybrid by the same deterministic RRF over those exact arm
candidates. Execution-only aggregate timing separates batch preparation,
Retrieval, Evidence Assembly, provider Generation, and total time without
affecting identities or correctness. The paid 70Q run completed at
`.local/p04-rag-m2/run-001`: the manifest is `complete`, with 70 single-attempt
provider occurrences and the retained per-question Packet, Generation, and
review-index artifacts. Its recorded aggregate timing is evidence only. The
initial strict human semantic audit is complete: RAG-grounded outcomes are PASS
37 / PARTIAL 11 / FAIL 21 / ERROR 1, not a general model-correctness rate.
Representative attribution identifies Q028/Q068 as Assembly-loss cases and
Q020/Q052 as Generation controls; Q011 upstream recall remains UNKNOWN. This
execution does not select a Retrieval, embedding, reranking, or Assembly
challenger technology.

## Post-Adoption Current Authority

The following is the current authority for Evidence Assembly admission. After
explicit user authorization and an actual source/diff gate PASS, the
production-facing materialization caller uses the formal
`assemble_deferred_footprint_charge_packet` API. **Formal
Deferred-Footprint-Charge is the current production-facing Assembly default.**
The generic v1 path is the historical callable production baseline before this
adoption. A1-2-1 v2 remains the accepted post-M2 comparison/admission control;
it is not the production default. Candidate Boundary remains unadopted.

The provider-free Stage 0 Packet-semantic validation and targeted paired Stage
1 validation are COMPLETE / PASS accepted evidence. They remain scoped
evidence, not a population prevalence result, broad Deferred superiority claim,
or a full-70 paid Generation uplift rate. The direct v1-to-Deferred semantic
bridge is also COMPLETE / PASS accepted evidence under its bounded contract.

The full-70 provider-free A1-2-1 v2 versus Deferred-Footprint comparison is
COMPLETE / PASS accepted evidence over the same frozen Hybrid Top20 supply,
the same RU/build, B0 = `12,000`, and the same per-block/configuration.
Formal-to-Shadow parity is likewise COMPLETE / PASS accepted evidence. Neither
completed evidence path performed Retrieval, Dense, RRF, Provider, or
Generation calls. Expanded Candidate Boundary remains excluded from the
comparison and unadopted.

The historical full-70 candidate-supply gap is CLOSED by accepted Step 0 at
`.local/p04-rag-broader-admission-70q-candidate-supply/`. Its run identity is
`fe0f40824fd5ef20b00c0668c5594ab0d8d9869e367551f5d4ba75a94ae82f1e`, and
its implementation/checkpoint is `3dd5317`. Actual reproduction was Q001-Q070
70/70; lexical, Dense, and Hybrid candidate hashes 70/70 each; 210/210 total
exact hashes; and persisted historical full Hybrid arrays 22/22 exact. Provider
and Generation calls were both 0. This accepted `.local` candidate supply is
frozen input: do not rerun or resume it.

Production scope remains `zh-CN + MiHoYo OBC only`. Evidence Packets remain an
auditable interface, not a substitute for RAG quality. B0 = `12,000` is the
primary comparison operating point; `14,965` remains sensitivity evidence only,
not a production default. This production-default switch authorizes no new
paid or full production run, Retrieval challenger, reranker, embedding change,
Candidate Boundary adoption, budget change, Generation/provider change, query
rewrite, RU redesign, agentic retrieval, GraphRAG, or RAGFlow migration.

Formal Deferred-Footprint-Charge production adoption is **CLOSED**. Repository
checkpoint `0284777 phase04: add qwen embedding challenger preflight seam` is
complete, and the working tree was clean immediately afterward. The Qwen
challenger Plan and its provider-free synchronous-preflight seam, including the
issued-attempt accounting repair, are complete, offline-tested, and reviewed
**PASS**. The Qwen-specific DashScope synchronous transport adapter is now
implemented/offline-tested. Its workspace-region credential-binding repair,
empty-code success handling, and final transport/body response-status guard
are each **ACTUAL-REVIEW PASS**. The generic execution-mode isolation repair
and CLI outcome exit-status repair are **ACTUAL-REVIEW PASS**. Repository
checkpoint `48a397b phase04: add qwen live preflight runner` was the clean
starting point. The explicitly authorized Beijing Qwen3.7 live synchronous
preflight completed successfully in `.local/p04-qwen-live-preflight-beijing-20260909-131758`:
`execution_mode=live_dashscope`, `provider_api_status=live_dashscope_succeeded`,
and both logical attempts, `document` then `query`, succeeded. The requested
model was `qwen3.7-text-embedding`; the configured Beijing region/workspace
endpoint was usable with the existing local `DASHSCOPE_API_KEY`. The persisted
Dense manifest records `model_name=qwen3.7-text-embedding`,
`embedding_dimension=2048`, `row_count=1`, `dtype=float32`, and
`normalization=L2`. Live Beijing provider availability and the requested
2048-dimensional synchronous preflight are therefore no longer **UNKNOWN**.
No Batch work has been executed or proven, no full 535,802-RU Qwen embedding
build has started, no 70Q Qwen query-vector artifact has been built, no
BGE-vs-Qwen full quality comparison has been run, and no production Dense
adoption or switch has occurred. The sole immediate next action is the
repository checkpoint of this live-preflight result/state only. No further
provider/API work is authorized by this successful preflight; every real
provider/API request remains separately subject to explicit user
authorization. No broader retrieval or optimization work is pre-authorized.

## Historical A1 Status

P04-RAG-M2 execution is complete; do not rerun or overwrite `run-001`.
**P04-RAG-A1-1 — Candidate fidelity and prepared Evidence Assembly context**
is implemented and locally verified as the v1-compatible diagnostic foundation.
It preserves existing Evidence selection semantics and issues no provider
calls. Its follow-on work remains separately scoped; Retrieval, embedding,
reranking, and other challenger work remain outside this completed unit.

**P04-RAG-A1-2-1 — versioned v2 Evidence Selection challenger** is
implemented and verified by a no-Generation replay at
`.local/p04-rag-a1-2-review/diagnostic.json`. v1 remains the unchanged
byte-compatible control. v2 admits direct-containing structural blocks before
context-only blocks, with an explicit context-only cap of 8 and deterministic
total-character budget conflicts recorded as operating-point omissions rather
than integrity failures. The primary replay held corpus/build, arm outputs,
Hybrid top-20, RRF, RU, expansion, per-block limit, and 12,000-character total
budget fixed; it verified Q028/Q068 carrier visibility, Q020/Q052 Hybrid
visible-evidence non-regression, v1 preserved-M2 byte equality, and v2 repeat
determinism with 0 provider calls. Its timing/memory values are replay evidence
under one Prepared Assembly Context, not M2 original timing. This closes only
the versioned admission-policy work unit; budget tuning, arm-union/depth
experiments, block splitting, retrieval changes, and Generation remain
separately authorized work.

**P04-RAG-A1-2-2 — exact dialogue-source-occurrence alias-suppression
challenger** is implemented with implementation correctness **PASS** under policy
identity `phase04-rag-a1-2-2-exact-dialogue-source-occurrence-alias-suppression-0.1`.
Its 22-question / 66-row persisted-candidate measurement is actual-review
**FAIL** for retention acceptance. The scope remains limited to mechanically
proved `rich_text` <-> `dialogue_node` aliases for the same actual dialogue Raw
occurrence; existing v1 and A1-2-1 v2 behavior remains outside this challenger.
Candidate arrays were unchanged and all alias traces were reverified against the
checkpointed occurrence proof.

The retention failure is deterministic greedy-admission displacement, not an
alias-proof mismatch: Q012/dense admitted a rank-17 2,713-character Block while
non-alias control-visible direct ranks 18/19/20 were lost; Q041/lexical admitted
rank 13 (1,432 chars) while ranks 14/17 were lost; Q050/lexical admitted rank 15
(2,508 chars) while ranks 16/17/18 were lost; Q058/dense admitted rank 16
(1,191 chars) while rank 18 was lost; and Q064/lexical admitted rank 15
(1,200 chars) while rank 19 was lost. Each newly admitted Block already existed
in the control with identical membership and character count, but was omitted
there by `direct_total_context_char_budget_conflict`. Q045/dense separately
shows that a pre-admission alias representative is insufficient when that
representative is not final-Packet-visible. A1-2-2 is therefore **NOT ADOPTED**
as production/default selection behavior; at that checkpoint, A1-2-1 v2 was
the accepted control/current selector. Checkpoint `71675eb` remains valid, and
no further A1-2-2 implementation work is authorized.

For the Q012 Hybrid gate, the approved negative result remains: 122 chars
released, rank-19 headroom 704 -> 826, the decisive 2,059-character Block
remains omitted, and the shortfall is 1,233 chars. Q012 is not rescued. The
known Q032 retention-positive pairs satisfy the narrow occurrence proof.
`actual_characters_released` in the measure is the net final selected-Packet
delta (`control_selected_chars - challenger_selected_chars`), not gross alias
suppression: aggregate 11,722 is not 11,722 redundant chars removed, and dense
`-418` means dense challenger Packets were cumulatively 418 selected chars
larger under this net metric. It is not a semantic benefit measure.

The current Selection Precision attempt stops here; Selection Precision remains
**OPEN / NOT CLOSED**. The read-only Candidate Boundary shadow diagnostic is
**COMPLETE / PASS**. Its original measurement remains valid: the persisted
lexical and Dense Top20 union was reconstructed with exact current RRF
semantics, the Hybrid prefix matched **22/22**, CONTROL Packet reproduction
matched **22/22**, and the original aggregates and eight CONTROL-visible direct
losses remain valid. The original artifact is
`.local/p04-rag-candidate-boundary-shadow/diagnostic.json` (446647 bytes;
SHA-256 `6e8d59bd4cd33200f25e88927cd91da564b7af128572f8911104633a6be12e58`).

The original `decisive_carrier_traces` field was invalid because it bound the
wrong units; it is not used as the Candidate Boundary gate result. The
corrected bindings are recorded in
`.local/p04-rag-candidate-boundary-shadow/review-correction.json` (3491 bytes;
SHA-256 `b0060bc2b6218a0ac348ced5d5fe3689d3f085b756e8f9d9b3f697d12bc73da0`).
The corrected Q016/Q041/Q046 decisive carriers are mechanically beyond the
current Hybrid Top20 and are all omitted by the unchanged A1-2-1 Assembly;
Candidate Boundary alone is therefore insufficient under the current
Assembly, and the shadow demonstrates cross-layer retention risk. No semantic
or end-to-end quality gain is claimed. Candidate Boundary production policy
remains **NOT AUTHORIZED / NOT STARTED**.

**P04-RAG-A1-3 — candidate-anchored Selection / Assembly shadow** is
**ACTUAL-GATE PASS** as an experimental capability only. It introduces
candidate-anchored immutable direct footprints and explicit shared-context
membership/accounting, so candidate-anchor grouping no longer relies on
incidental global source-order merging. It remains a shadow Packet path: v1 is
unchanged and, at that checkpoint, A1-2-1 v2 was the accepted/current selector.
It does not make A1-3 production/default behavior and does not change Candidate
Retrieval, BM25, Dense, Hybrid/RRF, Top-K, the current evidence budget,
Generation, or Candidate Boundary production policy.

The bounded persisted-attribution 22Q diagnostic is
`.local/p04-rag-a1-3-candidate-anchored-shadow-22q/diagnostic.json`
(SHA-256 `182bfb545871f11b287a7122776cabe242a3a881c7769948f87e32a3605ab267`).
Its correction sidecar is authoritative for invalid aggregate/control fields:
`.local/p04-rag-a1-3-candidate-anchored-shadow-22q/review-correction.json`
(SHA-256 `7922edf3d57e80d67c0916d2439cc00e9468fafd05551a48336be9d2f6154cd7`).
Valid evidence is persisted candidate binding 66/66, original per-row
same-input shadow Packet determinism 66/66, original per-row same-input shadow
trace determinism 66/66, and corrected A1-2-1 v2 control Packet reconstruction
66/66. The bounded reconstruction made zero Retrieval, Dense, Provider, or
Generation calls. The original diagnostic aggregate determinism and v2-control
hash-match fields are invalid and are not gate evidence.

The experimental shadow Packet hashes remain **UNKNOWN / non-blocking** for
independent reconstruction because the effective historical shadow
`retrieval_audit` input was not persisted. This does not invalidate the
per-row same-input determinism evidence or corrected v2 control reconstruction.
The 66 mechanical mode rows selected all 20 direct roots in 24 rows and fewer
than 20 in 42; `direct_total_context_char_budget_conflict` occurred in 37 rows
with 148 omissions. These are Packet/admission observations, not semantic or
end-to-end quality evidence. Finite-budget direct omission therefore remains
material and unresolved under unchanged v2-style admission.

A1-3's original bounded diagnostic used persisted attribution candidate supply
only. The subsequent **P04 A1-3 x Candidate Boundary cross-measure** is
**ACTUAL-GATE PASS** as a read-only/local diagnostic work unit; it is not
CLOSED or CHECKPOINTED. Its original diagnostic is
`.local/p04-rag-a1-3-candidate-boundary-cross-measure/diagnostic.json`
(SHA-256 `fb815cd5e13813ccf270e9192cafb13d46d5d47052ea0903e7d3bbf1bb09683f`).
It made zero Retrieval calls, Dense encoding/rebuild calls, Provider calls, or
Generation calls, and made no production/default policy change. A1-2-1 v2
remains the accepted/current selector, A1-3 remains experimental/shadow, and
Candidate Boundary production remains **NOT AUTHORIZED**.

The cross-measure reconstructed the current-RRF first20 exactly against the
persisted Hybrid Top20 for **22/22** questions. It admitted 380 CONTROL direct
roots and incurred zero CONTROL-admitted direct-root losses after EXPANDED
candidate supply: the A1-3 real expanded-candidate direct-root retention
contract is **PASS**. In this 22Q cross-measure, A1-3 incurred none of the previously
observed accidental higher-priority direct-root retention losses under
lower-priority candidate expansion. It does not change the correctness
invariants of identity, provenance, determinism, fail-closed behavior, or
stable A1-3 retention behavior; the 12k context/evidence budget, Top-K, and
RRF constants remain operating points, not an approved architecture fix.

CONTROL/EXPANDED direct `direct_total_context_char_budget_conflict` omissions
were 44/259. Q012 remains omitted by that reason; the corrected Q016, Q041,
and both corrected Q046 decisive carriers are present in expanded candidate
scope and likewise remain omitted by that reason. Expanded supply therefore
did not rescue any verified decisive carrier under unchanged admission policy.
No semantic or end-to-end answer-quality gain is claimed, and this unresolved
22Q diagnostic subset is not representative evidence for the full 70Q
population.

The original diagnostic remains immutable. Its correction sidecar is
`.local/p04-rag-a1-3-candidate-boundary-cross-measure/review-correction.json`
(SHA-256 `d6b0755de8c8e5df5aabeab307642b9cc372e8ac2696ad0ceb54217ae3595765`)
and is authoritative only for the ambiguous original
`aggregate.rank_gt20_newly_packet_visible` name/value as a strict visibility
delta. The valid renamed count is
`rank_gt20_expanded_candidate_packet_visible = 136`; the strict
`rank_gt20_visibility_delta_control_to_expanded = 130`; and
`rank_gt20_expanded_candidates_already_control_visible = 6`, with those six
already Packet-visible in CONTROL through context. This correction changes none
of the 380 CONTROL direct roots, zero direct-root losses, 44/259 budget-conflict
counts, carrier dispositions, or absence of semantic/answer-quality evidence.

**P04 Bounded Budget Sensitivity Measure** is **ACTUAL-GATE PASS** as a
read-only/local diagnostic work unit; it is not CLOSED or CHECKPOINTED. Its
primary diagnostic is `.local/p04-rag-budget-sensitivity/diagnostic.json`
(SHA-256 `fb85b467032275405e70303e89ea4f27631f12753f99149ca6045dbb30d0629c`),
produced by `.local/p04-rag-budget-sensitivity/run.py` (SHA-256
`261fdf42b91eebe86da9d55756659ba941367595e500266c0003e0ffea7f12b6`). It used
the accepted persisted 22Q EXPANDED candidate inputs and exact
`retrieval_audit`, preserved the current-RRF first20 binding and accepted B0
Packet/trace identities **22/22**, and made zero live Retrieval calls, Dense
encoding/rebuild calls, Provider calls, or Generation calls. It made no
production/default policy change.

The measure held candidate supply/order, direct-root identities and priorities,
immutable footprints, membership/ownership, per-block limit, context
construction, context-only cap, provenance, and admission semantics fixed. The
only changed field was `total_context_chars`: B0 = `12,000` and B1 = `14,965`.
B1 is only the deterministic diagnostic sensitivity point derived from the
accepted B0 trace: the maximum direct total-budget-conflict `would_total` among
roots within the existing per-block limit, uniquely Q064 rank 32
(`budget_before = 11,985`, marginal direct chars = `2,980`,
`would_total = 14,965`, shortfall = `2,965`). It is not an optimum, production
recommendation, globally required budget, or an upper bound guaranteeing
recovery of all current budget-conflicted roots. No production budget change is
authorized.

Actual B0 -> B1 mechanics were admitted direct roots `513 -> 588` (net `+75`),
direct selected chars `256,660 -> 311,854`, and context selected chars
`2,981 -> 4,396`. Direct transitions were 86 B0 total-budget-conflict -> B1
admitted, 173 total-budget-conflict at both points, 18 non-total direct
constraints (all existing per-block limits), and 11 B0 admitted -> B1
total-budget-conflict. Direct total-budget-conflict omissions therefore changed
`259 -> 184` by `259 - 86 + 11 = 184`; admitted-root accounting is
`513 + 86 - 11 = 588`.

The 11 B0-admitted -> B1-budget-conflicted roots are not an A1-3 correctness
regression. Under the unchanged priority-ordered greedy trajectory, added
capacity admitted previously omitted higher-priority footprints which then
consumed capacity; later lower-priority roots became infeasible. This is a
finite-capacity trajectory/displacement effect, not a violation of identity,
immutable-footprint, provenance, ownership/membership, or determinism
constraints. Larger budget is not set-monotonic, and these transitions do not
prove the greedy policy semantically wrong.

Verified decisive carrier dispositions were Q012 B0 total-budget omitted -> B1
admitted / mechanically Packet-visible; corrected Q016 and Q041 total-budget
omitted at both points; and both corrected Q046 carriers B0 total-budget
omitted -> B1 admitted / mechanically Packet-visible. Thus **3/5** verified
decisive carrier occurrences are mechanically Packet-visible at B1. They are
not semantically rescued, and no answer-quality or Generation gain was
measured.

Context-only selected blocks changed `77 -> 87`; context total-budget omissions
changed `328 -> 277`; context-only-cap omissions changed `20 -> 61`; and
aggregate per-block-limit omissions remained 19. The direct-root non-total
constraint count is 18; the aggregate per-block count also includes one
context-side omission. These counts do not establish semantic utility.

The measure verifies that 12k is an active mechanical capacity constraint under
the current admission policy on this selected unresolved 22Q diagnostic subset.
B1 mechanically recovers substantial direct evidence, including 3/5 verified
decisive carrier occurrences, while the 11 downstream conflicts show that
capacity and the unchanged greedy trajectory interact. Larger/bounded
configurable evidence budget is therefore a materially supported design axis,
but not an authorized production/default policy, and `14,965` is not a proposed
production default. The stronger next investigation is an admission-policy
challenger: finite capacity still requires explicit evidence tradeoffs and the
current footprint/admission semantics determine what survives. It should
examine direct evidence roots against supporting context/footprint capacity,
but this status record authorizes no two-ledger, direct-first, quota, packing,
splitting, or other implementation shape. Rank priority itself is not
disproved.

Reranker/ranking work remains deferred one layer, not rejected; Q016/Q041
remaining omitted does not erase existing ranking evidence. Embedding/query/RU/
upstream Retrieval changes remain deferred pending later attribution. At that
checkpoint, A1-2-1 v2 was the accepted/current selector and A1-3 was
experimental/shadow; Candidate Boundary production remains **NOT AUTHORIZED**;
and 12k remains the current control operating point, not a correctness
invariant or proven optimum.
Budget, Top-K, and RRF remain operating points. Identity, provenance,
deterministic ordering, fail-closed behavior, and A1-3 immutable-footprint /
retention properties remain correctness constraints. Semantic usefulness of
recovered roots, larger-Packet answer-quality impact, the appropriate production
budget, bounded-adaptive-budget value, final admission policy, reranker value,
embedding value, and full-70 prevalence remain unresolved. This selected 22Q
result must not be extrapolated to the full 70Q workload.

The preceding Candidate Boundary cross-measure established that
**admission-policy investigation** was the stronger next architecture decision
candidate than an immediate reranker or embedding replacement. The subsequent
**P04 Evidence Admission Challenger — Deferred-Footprint-Charge Shadow** is now
ACCEPTED / CHECKPOINTED as a diagnostic-only mechanical work unit. It repaired
the confirmed shared-occurrence eligibility defect and is mechanically
supported at B0 = `12,000` on the accepted persisted 22Q EXPANDED candidate
supply. Its accepted `.0.4` diagnostic is
`.local/p04-rag-a1-3-deferred-footprint-charge-shadow-b0-0.4/diagnostic.json`
(SHA-256 `fe72eb6ee13fed537964d6e57963ad069c320a9de00dcb1db997bd4fe9d81107`),
qualified by
`.local/p04-rag-a1-3-deferred-footprint-charge-shadow-b0-0.4/review-correction.json`
(SHA-256 `7bcd0f8093ea56aaa42f2d60a9e03d58b6434efc531f05e8a427545493031df7`).

The accepted same-supply transitions versus accepted A1-3 EXPANDED are 518
visible in both, 145 invisible in both, 131 A1-3-invisible ->
challenger-visible, and 4 A1-3-visible -> challenger-invisible. All four
remaining losses are mechanically explained: Q012/Q016/Q057 by exact
direct total-budget arithmetic and Q061 by the preserved full-footprint
per-block limit. The five tracked decisive carrier occurrences — Q012, Q016,
Q041, and both Q046 occurrences — are mechanically Packet-visible via admitted
singleton direct roots. They are not semantically rescued.

The sidecar corrects shared-footprint counting bases: 29 unique occurrences and
30 occurrence-to-footprint-anchor relations, with occurrence eligibility
partition 21/8 and relation eligibility partition 22/8. It rejects
`29 - 22 = 7`. The challenger trace label
`already_visible_via_higher_priority_anchor` is over-specific: it means only
that an occurrence was already rendered before that deferred admission attempt.
Authoritative actual rendering is `context_occurrences[].rendering`. Focused
and affected validation passed `tests.retrieval.test_rag_w1` 33/33, and the
accepted evidence record is
`docs/research/phase-04/p04-a1-3-deferred-footprint-charge.md`.

Mechanical admission hypothesis is **supported**; semantic / answer-quality
utility remains **UNKNOWN**. This selected 22Q result is not a full-70
prevalence claim. At this historical shadow checkpoint, A1-2-1 v2 was the
accepted/current selector; A1-3 and this challenger were experimental/shadow,
and production/default adoption was **NOT AUTHORIZED**. That historical status
is superseded for the production-facing caller by the Post-Adoption Current
Authority above. Reranker/ranking challengers remain deferred, not rejected;
embedding/query/RU/upstream Retrieval changes remain deferred pending later
attribution; and Candidate Boundary production remains **NOT AUTHORIZED**.

The historical reconstructed old-v2 Packet SHA mismatch remains
**UNKNOWN / non-blocking**, and the full-70 no-Retrieval candidate source
remains **UNKNOWN**. The preceding Candidate Boundary cross-measure itself used
persisted candidates only: no Retrieval, Dense, RRF reconstruction replay,
Assembly replay, Generation/Provider, reranker, semantic selector, query
rewrite, arm union, or budget increase was executed. The later bounded Budget
Sensitivity Measure is the separately recorded Assembly replay above.

## Phase transition boundary

Phase 01 Raw Collection, Phase 02 Parsed Schema / Parser Foundation, and Phase
03 Canonical Schema remain **CLOSED**. P04-W1-W7 accepted/closed work remains
historical evidence and reusable tooling where applicable.

Phase 02 Parsed contracts remain source-specific and evolutionary; UNKNOWN or
unsupported source structures must not be guessed or silently discarded.
Phase 03 Canonical contracts preserve structural source evidence and lineage,
not semantic equivalence.

The new First End-to-End RAG direction builds on the current real Canonical
contract; it must not redesign Canonical merely for Retrieval convenience.

The reviewed P04-RAG-W1 Plan received explicit execution authorization and is
complete. All later First End-to-End RAG implementation work still requires a
separate reviewed scope and explicit execution authorization.

First-version exclusions in the amendment are temporary scope controls, not
permanent technology bans. In particular, Vector DB / ANN, reranking, more
complex Hybrid methods, query routing/decomposition, agentic retrieval,
knowledge graphs, different embeddings, and additional GenerationProviders
may be reconsidered later if end-to-end evidence shows they address a real
bottleneck.

GenerationProvider remains provider-neutral. The first operational platform is
Alibaba Cloud Bailian / Model Studio and the first model family is Qwen. The
implemented offline control baseline uses exact snapshot
`qwen3.7-plus-2026-05-26` rather than the dynamic `qwen3.7-plus` alias, with
explicit `enable_thinking=false` as a configurable operating point. This is a
first control baseline, not a long-term generator winner. Qwen Max, later
Qwen versions, and OpenAI remain later same-Evidence challengers/provider
candidates, not current implementations.

Knowledge-graph / semantic-identity layers and production UI remain outside
the first runnable RAG baseline unless separately authorized.

## Source of truth

Current stage and immediate next action: this file.

Current First End-to-End RAG design direction:
`docs/phases/phase-04-first-end-to-end-rag-amendment-v1.md`.

The general Phase 04 boundary remains documented in
`docs/phases/phase-04-retrieval-evidence-assembly.md`; W7-specific amendments
and addenda remain authoritative for the historical W7 work they governed,
unless a later document explicitly supersedes a current-state decision.

Phase 01 workflow and closure evidence:
`docs/phases/phase-01-raw-collection.md` and relevant
`docs/research/phase-01/` notes.

Closed Phase 02 and Phase 03 authority:
`docs/phases/phase-02-parsed-schema.md` and
`docs/phases/phase-03-canonical-schema.md`.

Architecture/design work must also follow `docs/architecture-overview.md`,
`AGENTS.md`, actual repository code/tests, and verified OBC evidence.
