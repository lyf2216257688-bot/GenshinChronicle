# Current Phase

`docs/current-phase.md` is the sole authority for present project state,
production/default behavior, and authorization. The compact Phase 04 checkpoint
index is `docs/research/phase-04/p04-rag-historical-checkpoint-register.md`;
its detailed lookup-only archive is linked there and is not current authority.

## Active phase and work unit

**Phase 04 - First End-to-End RAG (post-W7 direction reset).** The first
end-to-end baseline is implemented, materialized, and measured. Phase 01 Raw,
Phase 02 Parsed, and Phase 03 Canonical are closed; the production corpus scope
remains `zh-cn` MiHoYo OBC.

**Current engineering work unit: P04 Qwen Candidate Depth Diagnostic.** The
Qwen Dense production adoption is CLOSED / PASS at
`9b0fff07693d336714e39082ffe91928f0b743d2`; the completed Qwen 16Q Failure
Attribution actual review closes PASS WITH Q011 CORRECTION. Historical
W7/Frozen48 and W1 benchmark-production routes remain auditable but are
stopped/superseded as governing paths for the post-W7 RAG baseline.

## Current production baseline

- Retrieval Units, deterministic BM25, local Dense, deterministic RRF Hybrid,
  deterministic Evidence Assembly, provider-neutral Evidence Packets, and
  provider-neutral Generation contracts are implemented. The production
  Retrieval-to-Packet baseline has been materialized from the accepted
  16,437-record Canonical corpus (535,802 Retrieval Units).
- **Production Dense is the accepted Qwen `qwen3.7-text-embedding` artifact**
  at 2048 dimensions, dense-only, FP32/L2, 535,802 rows, arm identity
  `be3efd531bcf514148e9f2b3162dbaed99fe1ac0b5257121864dff6416525922`, vector
  SHA-256 `4d6337822459ede93f18d5384e37cbbbef4363b830a5e705d788864dc02dfb8a`,
  rows SHA-256 `54590bc5a198ad65301cf6e274c9c0931b48288015596760f5d3b7d12caee701`,
  manifest SHA-256 `6b4330e67cd7c4284a9e396d65ae6be8a43fc5fac26804a54f6840928b5937d5`,
  and RU build identity
  `49b48ee746716add0248fed388d10bd522a930efb582a0f5e827f66681ed8998`.
  The matrix is reused in place and loaded with read-only mmap. BGE remains a
  retained historical/control implementation, not a production fallback.
- **Formal Deferred-Footprint-Charge is the production-facing Evidence
  Assembly default.** Production materialization calls
  `assemble_deferred_footprint_charge_packet`. Historical v1 is the
  pre-adoption callable baseline; A1-2-1 v2 is a comparison/admission control,
  not the production default. Candidate Boundary is unadopted.
- B0 = `12,000` total context characters and `3,000` per block are current
  operating points. They are configurable controls, not correctness
  invariants, proven optima, or an authorization to retune the budget.
- Generation remains provider-neutral. The implemented scoped control uses
  Alibaba Cloud Bailian/Qwen with the exact
  `qwen3.7-plus-2026-05-26` snapshot and `enable_thinking=false`; no new
  Generation run is authorized by this status record.

## Decision-relevant evidence

- The corrected provider-free BGE-versus-Qwen 70Q mechanical comparison is
  **COMPLETE**. It held the accepted corpus/RU supply, BM25, RRF, Top20, and
  Formal Deferred-Footprint-Charge controls fixed. Its mechanical differences
  are not semantic quality, a retrieval winner, or production-Dense-adoption
  evidence. Exact historical identities and counts are in the Phase 04
  checkpoint register.
- The accepted Qwen3.7 full-corpus and Q001-Q070 query vector artifacts were
  previously evaluated as a **challenger checkpoint** at the fixed
  2048-dimensional dense-only operating point. Those historical results prove
  materialization/provenance; the current production adoption is recorded
  above and does not reinterpret the historical comparison as a quality claim.
- A later dual blind Packet-semantic review is decision-relevant technical-lead
  evidence. Its accepted summary, hashes, and claim boundary are owned by the
  Phase 04 checkpoint register; original reviewer artifact bytes remain
  external/untracked: `QWEN_BETTER = 5`, `BGE_BETTER = 2`,
  `BOTH_SUFFICIENT_EQUIVALENT = 49`, `BOTH_INSUFFICIENT = 14`, and
  `INDETERMINATE = 0`. The strongest supported interpretation is a slight
  directional Qwen signal with overall Packet-semantic utility highly similar
  to BGE. It establishes neither clear superiority nor a production Dense
  switch.
- The Qwen 16Q Failure Attribution actual review is **PASS WITH Q011
  CORRECTION**. Its primary local audit artifact is
  `.local/p04-rag-qwen-16q-failure-attribution-final-20260911/audit_final.json`
  (91,300 bytes, SHA-256
  `b3cfced5f2e40335c05e51d63b3426e9fa168cae95997f4452eda8ce16cd4188`).
  `artifact_hashes_final.json` records no accepted audit run identity;
  that identity is **UNKNOWN** and must not be inferred. The old historical
  `strict rerank relevant = 0/14` conclusion is not architecture authority.
- Q011's accepted Qwen Dense exact ranks are `112` and `188`; the phrase
  `thousands-deep` is invalid and must not be reused. The correction sidecar
  is `.local/p04-rag-qwen-q011-formal-deferred-oracle-20260912/q011_prose_correction_sidecar.json`
  (693 bytes, SHA-256
  `f1aa42f75ab2ed737ce30157ee123b37f033451c7e48b92c87043f4f5456ac39`).
- The Q011 Formal Deferred oracle is **PASS**:
  `.local/p04-rag-qwen-q011-formal-deferred-oracle-20260912/q011_oracle.json`
  (33,138 bytes, SHA-256
  `e70e0694ce23b39f9fc64d01ead6edaab478da2f7688ea8792cc55ada5f1b5ad`,
  run identity
  `506cc3301aa025b4f710918321284c58871bea496b4bb3cda32de66729319472`).
  It promoted Q011's verified carriers ahead of the unchanged persisted
  production Qwen Hybrid Top20; unchanged Formal Deferred admitted both as
  direct roots. This mechanically confirms Q011 downstream survival only.
  It does not establish reranker efficacy or authorize wider production
  candidate depth, a production reranker, or an Assembly/budget change.
- The fixed-budget oracle tradeoff is material and explained: the production
  Packet had 28 visible units; the diagnostic Packet had 26, including the
  two newly visible verified Q011 carriers. Four previously production-visible
  units were displaced: two direct-candidate/direct-containing losses from the
  finite total-context budget, one deferred-footprint visibility loss, and one
  context-only visibility loss. This is not evidence that Assembly is
  defective; future candidate-depth/ranking experiments must measure this
  finite-budget displacement cost.
- The reviewed attribution retains source/RU/coverage ambiguities, unresolved
  carrier identities, deeper retrieval/query-recall failures, and mechanically
  established bounded candidate/ranking failures. Q011 and Q046 are positive
  downstream-survival controls when their verified evidence is supplied.
  Reranker efficacy remains **UNKNOWN**.

## Current authorization boundary

Arbitrary Dense/Hybrid queries use the existing Qwen synchronous query seam
(`role=query`, dimension 2048, dense-only, no custom instruction), one request
per query. Missing credentials, provider/transport failures, or
artifact/provenance mismatches fail closed; there is no BGE fallback.

The first runnable RAG baseline is an implemented and measured baseline, not a
technology freeze. The production chain remains unchanged:
`lexical Top20 + Qwen Dense Top20 -> deterministic RRF60 -> Hybrid Top20 -> Formal Deferred`.
No production parameter changed. W7 amendments govern only their completed W7
lineage; the W1 benchmark contract governs only its historical benchmark path.

## Immediate next gate

**P04 Qwen Candidate Depth Diagnostic.** This is the unique next technical
work unit and is an investigation, not an approved production change. It must
test provider-free whether bounded wider candidate supply can expose the
already verified ranking-boundary carriers for `Q005`, `Q011`, `Q045`, `Q046`,
and `Q049`, then measure carrier exposure, ranking, and downstream
finite-budget displacement. Only after that diagnostic may the project decide
whether a reranker challenger is justified. Wider production TopK, reranking,
new retrieval policy, Generation, and any Retrieval/Assembly operating-point
change are not approved.

Relevant remaining UNKNOWNs are actual reranker efficacy, optimal candidate
depth, semantic value of displaced evidence under wider supply, source/RU
resolution for unresolved source/coverage questions, answer-level Generation
impact, and the optimal future Retrieval/Assembly operating point.

## Evidence owners

- Durable Phase 04 boundary: `docs/phases/phase-04-retrieval-evidence-assembly.md`
- First end-to-end RAG contract and historical plan: `docs/phases/phase-04-first-end-to-end-rag-amendment-v1.md`
- Formal Deferred-Footprint-Charge historical experiment: `docs/research/phase-04/p04-a1-3-deferred-footprint-charge.md`
- W1-W6 historical benchmark and experiment facts: `docs/research/phase-04/`
- W7 historical authority: `docs/phases/phase-04-w7-unit3b-*.md`
- Compact Phase 04 checkpoint index and artifact identities:
  `docs/research/phase-04/p04-rag-historical-checkpoint-register.md` (the
  detailed chronology is lookup-only in its linked archive)

All new work must read this file, the applicable Phase 04 contract, and the
relevant evidence owner before it changes scope or status.
