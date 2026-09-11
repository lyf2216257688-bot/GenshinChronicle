# Phase 04 RAG Checkpoint Register

> Compact provenance and navigation index for completed Phase 04 work. This is
> not current-state authority: present phase, production/default behavior,
> authorization, and immediate-next status are owned solely by
> `docs/current-phase.md`. Detailed historical narrative is preserved in the
> lookup-only [checkpoint archive](p04-rag-historical-checkpoint-archive.md).

## Evidence ownership

| Scope | Primary owner | Register role |
| --- | --- | --- |
| Durable Phase 04 Retrieval / Assembly boundary | `docs/phases/phase-04-retrieval-evidence-assembly.md` | Contract pointer |
| First end-to-end RAG plan and runtime boundaries | `docs/phases/phase-04-first-end-to-end-rag-amendment-v1.md` | Historical plan pointer |
| W1 benchmark | `docs/research/phase-04/benchmark-contract.md` | Checkpoint pointer |
| W2-W6 experiments | The correspondingly named `p04-w*.md` note | Checkpoint pointer |
| Formal Deferred shadow/adoption evidence | `docs/research/phase-04/p04-a1-3-deferred-footprint-charge.md` plus the parity source below | Evidence pointer |
| W7 lineage | The two `docs/phases/phase-04-w7-*.md` amendments | Historical authority pointer |
| Blind BGE/Qwen Packet review | This register (summary only; original reviewer bytes are external/untracked) | Accepted review index |

## Checkpoints

### Foundation and historical benchmark path

- **P01-EB / Phase 02 / Phase 03 closure:** accepted OBC `zh-cn` corpus and
  Canonical closure identities are retained in the archive; phase contracts
  and scoped research notes remain the primary owners.
- **P04-W1:** CLOSED / PASS at `3cf9694`; benchmark and profiler details belong
  to `benchmark-contract.md` and the archive.
- **P04-W2:** CLOSED at `7d8c510`; representation and lexical details belong
  to `p04-w2-derived-representations.md`.
- **P04-W3:** CLOSED at `34069c2`; diagnostic expansion details belong to
  `p04-w3-diagnostic-benchmark.md`.
- **P04-W4:** CLOSED / PASS at `2f292f3`, decision D — mixed / UNKNOWN; details
  belong to `p04-w4-diagnostic-evidence.md`.
- **P04-W5:** CLOSED / PASS at `8ba49d1`; reliability and coverage details
  belong to `p04-w5-benchmark-reliability.md`.
- **P04-W6:** CLOSED / PASS Dense family-isolation pilot; its model, artifact,
  and non-selection evidence belong to `p04-w6-dense-family-isolation.md`.

### W7 lineage and stopped routes

- **P04-W7 Frozen48:** CLOSED / PASS for its frozen leaf-only lineage, with
  observed `0 ACCEPT / 48 REJECT`; the W7 amendments own its safety and retry
  contracts. The contaminated executor is retired and W7 does not govern the
  post-W7 RAG path.
- The Frozen48 D1/body-failure-taxonomy route and the separately sent
  Semantic-First Benchmark Design Plan were stopped. They are historical
  routes, not pending work or authorization for restart.

### Post-W7 first end-to-end RAG

- **P04-RAG-W1/W2:** accepted Retrieval Unit, deterministic Assembly, BM25,
  local BGE Dense, and RRF implementation checkpoints; detailed identities and
  chronology are in the archive, while executable behavior is authoritative in
  `src/`.
- **P04-RAG-G1/G2 and M1/M2:** accepted offline controls and scoped live
  Generation evidence are historical checkpoint facts. M2 runtime input was
  `question_id` + `question`; reference answers and human review stayed in
  review-only sidecars. The archive preserves run identities and boundaries.
- **A1 selection/admission shadows:** accepted/rejected mechanics, correction
  sidecars, and `.local` identities remain historical experiments. Their
  production relation is superseded by the current authority in
  `docs/current-phase.md`.

### Formal Deferred-Footprint-Charge

- The 22Q Deferred shadow is accepted diagnostic evidence at B0 `12,000`; its
  primary owner is `p04-a1-3-deferred-footprint-charge.md`, with historical
  non-adoption preserved there. Later formal adoption is recorded by repository
  checkpoint `778c182` (`phase04: adopt deferred footprint charge assembly`).
- Provider-free parity/adoption provenance is implemented by
  `src/genshin_corpus/retrieval/deferred_api_parity.py`: Gate-A source baseline
  `f30725a` (formal implementation commit
  `f30725af7178aa6f107b61c960f73cac99daddf4`; parity runner checkpoint
  `7658944`), accepted comparison run
  `2c6057fc89fd844a41be60c6f49e672c4f36b647b0f38791d91b7de49587e464`, supply
  manifest `77c69ceb461c0be2670f700e0e1058954306252ce363b85d328b16c75b730acc`,
  comparison manifest `151769a8f3c93d67dbf10d62a85662566d6aba8285d0d765f9b4392bd4a69e17`,
  and RU manifest `dc6bbd30cc6fbc83fa38132085fb8550f20322082467fe24aadcb4239ca78673`.
  The source binds helper hashes and the parity-runner bytes; this register
  does not claim unpersisted output bytes beyond those identities.
- Current production-facing use of the formal API is interpreted only by
  `docs/current-phase.md`; the shadow remains historical evidence and is not a
  competing current selector.

### Corrected BGE/Qwen comparison and blind review

- The corrected fixed-control mechanical comparison is complete at
  `.local/p04-rag-bge-qwen-dense-comparison-70q-20260910-172843`, run identity
  `f15ffb2c7f213e429cd962e53b34c2cc36c22278623377faa2e632586d8840f8`.
  Mechanical candidate/Packet differences are not semantic quality evidence.
- **Later accepted blind Packet-semantic review (after that comparison):** two
  independent reviewers saw only question + blinded Packet A/B before
  unblinding. Reviewer 1: A_BETTER `2`, B_BETTER `4`,
  BOTH_SUFFICIENT_EQUIVALENT `51`, BOTH_INSUFFICIENT `13`, INDETERMINATE `0`.
  Reviewer 2: A_BETTER `2`, B_BETTER `2`, BOTH_SUFFICIENT_EQUIVALENT `51`,
  BOTH_INSUFFICIENT `15`, INDETERMINATE `0`. Exact primary-label agreement was
  `63/70 = 90%`; no A_BETTER/B_BETTER cross-label disagreement occurred.
- Frozen blind consensus before unblinding: A_BETTER `3`, B_BETTER `4`,
  BOTH_SUFFICIENT_EQUIVALENT `49`, BOTH_INSUFFICIENT `14`, INDETERMINATE `0`.
  Consensus JSONL SHA-256:
  `910e3ccfe92783ff32e46c1c67494d607a120ab15fe8ee69e92d1e4c11596e94`.
  Consensus summary SHA-256:
  `d9de573c165597939d3a0a0509b38db3f050e6584993e28b24618229c4c6837d`.
  Accepted unblinding map SHA-256:
  `17aa4f614f828ffcf929a48e4c4e6540362e378c8dbaf63ede2195772ce2f692`;
  mapping complete `70/70` and bound to the corrected comparison descriptor.
- After unblinding: QWEN_BETTER `5`, BGE_BETTER `2`,
  BOTH_SUFFICIENT_EQUIVALENT `49`, BOTH_INSUFFICIENT `14`, INDETERMINATE `0`.
  Directional Qwen: Q004 medium, Q023 medium, Q041 high, Q061 high, Q064 high.
  Directional BGE: Q022 high, Q057 medium. Both-insufficient: Q003, Q005,
  Q008, Q011, Q015, Q033, Q044, Q045, Q046, Q049, Q050, Q058, Q060, Q070.
  Unblinded JSONL SHA-256:
  `2ec71922951d95825f9d01b6ccb21f7641688687dd22a446bd724d03ecd4fc1c`.
  Unblinded summary SHA-256:
  `f21c8f0f9e147cebadf2cfd2e8c9b1f8200aece4d747d2cd1975e4cd90190825`.
- Claim boundary: this is only a slight directional Qwen signal in fixed-
  control 70Q Evidence Packet semantic utility; overall utility remains highly
  similar, with `49/70` both sufficient/equivalent, `14/70` both insufficient,
  and only `7/70` directional winners. It is not a benchmark-winner claim,
  does not establish clear Qwen superiority, does not authorize production
  Dense adoption, and does not establish Answer correctness, completeness,
  citation faithfulness, or hallucination improvement. Original reviewer
  artifact bytes remain external/untracked.

## Historical lookup rule

Use this index to locate the primary evidence owner, then consult the owner or
the [archive](p04-rag-historical-checkpoint-archive.md) for historical detail.
Do not use checkpoint wording as current authorization; re-read
`docs/current-phase.md` before any scope or status conclusion.
