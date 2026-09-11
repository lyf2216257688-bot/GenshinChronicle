# Current Phase

`docs/current-phase.md` is the sole authority for present project state,
production/default behavior, and authorization. Historical checkpoint detail is
preserved in `docs/research/phase-04/p04-rag-historical-checkpoint-register.md`.

## Active phase and work unit

**Phase 04 - First End-to-End RAG (post-W7 direction reset).** The first
end-to-end baseline is implemented, materialized, and measured. Phase 01 Raw,
Phase 02 Parsed, and Phase 03 Canonical are closed; the production corpus scope
remains `zh-CN` MiHoYo OBC.

**Current engineering work unit: none authorized.** This documentation-only
authority audit changes neither technical authorization nor production
behavior. Historical W7/Frozen48 and W1 benchmark-production routes remain
auditable but are stopped/superseded as governing paths for the post-W7 RAG
baseline.

## Current production baseline

- Retrieval Units, deterministic BM25, local Dense, deterministic RRF Hybrid,
  deterministic Evidence Assembly, provider-neutral Evidence Packets, and
  provider-neutral Generation contracts are implemented. The production
  Retrieval-to-Packet baseline has been materialized from the accepted
  16,437-record Canonical corpus (535,802 Retrieval Units).
- **Production Dense is `BAAI/bge-small-zh-v1.5`.** It remains the current
  production Dense arm; a comparison or challenger artifact does not switch
  it.
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
- Qwen3.7 Dense is a **challenger**. Accepted full-corpus and Q001-Q070 query
  vector artifacts exist at the fixed 2048-dimensional dense-only operating
  point. This proves materialization/provenance for the challenger, not
  production superiority or adoption.
- A later dual blind Packet-semantic review is decision-relevant technical-lead
  evidence. Its accepted summary, hashes, and claim boundary are owned by the
  Phase 04 checkpoint register; original reviewer artifact bytes remain
  external/untracked: `QWEN_BETTER = 5`, `BGE_BETTER = 2`,
  `BOTH_SUFFICIENT_EQUIVALENT = 49`, `BOTH_INSUFFICIENT = 14`, and
  `INDETERMINATE = 0`. The strongest supported interpretation is a slight
  directional Qwen signal with overall Packet-semantic utility highly similar
  to BGE. It establishes neither clear superiority nor a production Dense
  switch.
- The current 14Q both-insufficient root-cause attribution artifact is
  **PARTIAL / NOT ACCEPTED, pending correction**. Its `0/14 strict
  rerank-relevant` conclusion does not reconcile the existing Q046
  ranking-boundary signal and later Formal Deferred-Footprint-Charge evidence
  that both verified Q046 decisive carrier occurrences can be mechanically
  Packet-visible when supplied under that policy. This does not prove that a
  real reranker would solve Q046; it only prevents the obsolete claim that
  downstream survival is entirely unproven.
- Reranker value is **UNRESOLVED**, not rejected. No reranker implementation,
  deeper-rank diagnostic, production Dense switch, Dense rebuild, or paid
  Generation run is authorized here.

## Current authorization boundary

The first runnable RAG baseline is an implemented and measured baseline, not a
technology freeze. Formal Deferred-Footprint-Charge adoption authorizes no
new provider call, Generation execution, Retrieval replay, Dense rebuild,
budget change, candidate-boundary adoption, reranker, or architecture
optimization. W7 amendments govern only their completed W7 lineage; the W1
benchmark contract governs only its historical benchmark path.

## Immediate next gate

**NONE AUTHORIZED.** A corrected and re-reviewed 14Q attribution artifact is
a prerequisite before that artifact may support a later architecture decision,
but this status record does not authorize that correction or invent a next
optimization route.

## Evidence owners

- Durable Phase 04 boundary: `docs/phases/phase-04-retrieval-evidence-assembly.md`
- First end-to-end RAG contract and historical plan: `docs/phases/phase-04-first-end-to-end-rag-amendment-v1.md`
- Formal Deferred-Footprint-Charge historical experiment: `docs/research/phase-04/p04-a1-3-deferred-footprint-charge.md`
- W1-W6 historical benchmark and experiment facts: `docs/research/phase-04/`
- W7 historical authority: `docs/phases/phase-04-w7-unit3b-*.md`
- Consolidated Phase 04 checkpoint chronology and artifact identities:
  `docs/research/phase-04/p04-rag-historical-checkpoint-register.md`

All new work must read this file, the applicable Phase 04 contract, and the
relevant evidence owner before it changes scope or status.
