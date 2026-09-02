# Phase 04 Amendment — First End-to-End RAG v1

> Status: **P04-RAG-W1 Retrieval Unit / deterministic Evidence Assembly core completed; subsequent retrieval and generation work remains separately authorized**
>
> This document records the current engineering direction for the first runnable end-to-end RAG baseline.
> It is **not** a final architecture freeze. The first version exists to make the complete system runnable,
> collect real evidence, identify the actual bottleneck, and then iterate.

## 1. Why this amendment exists

Phase 04 W1–W7 produced useful retrieval and benchmark-construction evidence, but the project spent too much
time expanding benchmark-production/governance machinery before a complete RAG system existed.

The project priority is therefore changed from:

```text
Canonical
→ build increasingly rigorous benchmark machinery
→ determine retrieval winner
→ later build Evidence Assembly / RAG
```

to:

```text
Canonical
→ build a real end-to-end RAG baseline
→ evaluate it with human-authored questions
→ identify the actual bottleneck
→ improve the bottleneck
→ repeat
```

Engineering principle:

> **Build first → Measure second → Optimize third.**

Existing W2–W7 assets may be reused where useful, but their benchmark-production machinery is not automatically
inherited as a requirement of the new path.

---

## 2. Production scope and existing foundation

Production source remains:

```text
zh-cn MiHoYo OBC only
```

Existing data foundation remains:

```text
OBC
→ Raw
→ Parsed
→ Canonical
```

The first end-to-end RAG work must build **on top of the current real Canonical contract**.
Do not redesign Canonical merely to simplify Retrieval.

---

## 3. First runnable RAG baseline

Target main chain:

```text
Canonical
    ↓
Retrieval Units
    ↓
┌───────────────┬────────────────┐
│               │                │
BM25       Local Embedding   structural metadata
│               ↓                │
│          Dense Retrieval        │
└───────────────┬────────────────┘
                ↓
          simple Hybrid
                ↓
        Candidate Evidence
                ↓
        Evidence Assembly
                ↓
          Evidence Packet
                ↓
       GenerationProvider
                ↓
      first implementation: OpenAI
                ↓
        Answer + Citations
```

The baseline must be a real, evolvable system, not a toy prototype.

At the same time, the baseline must avoid infrastructure or optimization that has not yet been justified by
actual end-to-end evidence.

---

## 4. Retrieval modes

The first runnable baseline should support three switchable retrieval modes:

```text
lexical = BM25
dense   = local embedding retrieval
hybrid  = simple BM25 + Dense fusion
```

For the first Hybrid implementation, the preferred starting point is a simple deterministic rank-fusion method
such as Reciprocal Rank Fusion (RRF).

Exact Top-K and fusion constants are implementation/configuration decisions, not permanent architecture rules.
The Plan should recommend reasonable first defaults without turning them into irreversible contracts.

Do not introduce learned fusion, routing, reranking, or other complexity unless later evaluation demonstrates
a real need.

---

## 5. Local Embedding direction

Embedding is retrieval infrastructure and must remain independent of the final answer-model provider.

Current first-version direction:

```text
main local candidate = Qwen3-Embedding-0.6B
existing baseline    = bge-small-zh-v1.5
fallback candidate   = bge-base-zh-v1.5
```

The exact runtime choice remains subject to a small local smoke test and real retrieval/evidence quality.

The first version should not open a broad embedding-model tournament.

Not first-version priorities:

```text
bge-large-zh-v1.5
bge-m3
Qwen3-Embedding-4B
large-scale dimension tuning
model training
```

Corpus vectors and query vectors must use the same embedding model / vector space.

---

## 6. Vector storage

The first version does **not** require a Vector DB or ANN system.

Prefer simple rebuildable local artifacts, for example conceptually:

```text
vectors
retrieval-unit metadata
manifest / build identity
```

Brute-force cosine/dot-product search is acceptable for the first baseline if it is adequate for the current
corpus size and development environment.

Vector DB / ANN may be introduced later if real performance, scale, update, or service requirements justify them.

---

## 7. Retrieval Unit design principles

The exact Retrieval Unit contract is intentionally **not frozen in this amendment**.
The next Plan must derive it from the actual Canonical implementation, fixtures, and existing retrieval assets.

The following principles are already agreed:

1. **Retrieve relatively small units; restore context later.**
2. A Canonical structural leaf is not automatically a Retrieval Unit.
3. First-version filtering should remove only highly obvious junk/no-content units.
4. Do not rebuild W7-style semantic eligibility / lore-worthiness machinery.
5. Prefer conservative filtering: avoiding false deletion of real evidence is more important than perfectly removing noise.
6. Retrieval-visible text should contain little repetitive template/schema material.
7. Structural relations such as parent/sibling/ordinal/provenance should primarily support Evidence Assembly.
8. Do not redesign Canonical for retrieval convenience.
9. Different content types may require different leaf-selection rules, but they should converge on a shared Retrieval Unit contract.

The Plan must inspect current Canonical reality before proposing exact rules for:

```text
ordinary rich text
books / long-form text
dialogue
tables / attribute-like structures
obvious junk filtering
retrieval-visible text
assembly-visible metadata
```

---

## 8. Evidence Assembly design principles

Evidence Assembly is a required first-version system component.
Top-K retrieval results must not simply be concatenated and sent to the LLM.

The first implementation should remain simple and deterministic.

Expected baseline responsibilities include:

```text
deduplicate
→ merge/organize structurally adjacent evidence where justified
→ restore a small amount of deterministic context
→ preserve source order and provenance
→ respect a context/evidence budget
→ emit an Evidence Packet
```

### 8.1 Deterministic invariants

The Plan should treat the following as invariants rather than tuning knobs:

```text
same input + same configuration → same Evidence Packet
never invent adjacency
never merge across unrelated sources merely because vectors are similar
preserve canonical provenance
preserve deterministic source ordering
do not use an LLM to decide first-version context expansion
```

### 8.2 Context size and expansion must be configurable

Do **not** hard-code a single permanent neighbor length or evidence budget into the architecture.

The first implementation should expose bounded, reproducible configuration for relevant quantities such as:

```text
retrieval_top_k
neighbor_before
neighbor_after
max_evidence_blocks
per-block limits
total context/evidence budget
content-type-specific defaults where justified
```

The Plan should recommend reasonable initial defaults, but these defaults are only a first operating point.

The purpose of configuration is to allow later evaluation of:

```text
evidence completeness
noise
answer quality
latency
token/API cost
```

without rewriting the Retrieval / Assembly architecture.

Do not overengineer this into a generic strategy/plugin framework in v1.
Implement one simple deterministic default policy while keeping the relevant parameters configurable.

---

## 9. Evidence Packet is a first-class product interface

Evidence Packet is not merely an internal prompt fragment.

It should be provider-neutral and usable in several ways:

```text
1. direct input to GenerationProvider
2. local structured output
3. human-readable Markdown output
4. optional packaging for sharing
5. manual upload to an external AI by the user
```

Expected first-version representations:

```text
evidence_packet.json
evidence_packet.md
```

Optional ZIP packaging may wrap these files, but no complex package format is needed in v1.

Each evidence item should preserve enough provenance to trace it back to Canonical and the official OBC source.

The exact schema remains a Plan/implementation decision, but it should support at least the conceptual information:

```text
evidence_id
evidence text
source entry identity/title
canonical provenance/address
relevant section/speaker/context metadata when available
```

Retrieval audit metadata may also be preserved separately, for example:

```text
retrieval mode
BM25 rank/score
Dense rank/score
Hybrid rank
embedding identity
retrieval build identity
```

User-facing answer citations should remain readable, for example `[E03]`, while the Evidence Packet retains the
full provenance behind that evidence ID.

---

## 10. GenerationProvider

The architecture must use a generic:

```text
GenerationProvider
```

The first implementation will use OpenAI.

Do not freeze a long-term vendor list.
Future product needs may add, remove, or replace providers.

Retrieval, Evidence Assembly, and Evidence Packet must not depend on a specific generation vendor.

The system should remain useful even when generation is skipped entirely and the user only exports the Evidence Packet.

---

## 11. Application shape

First-version application shape:

```text
Python library + CLI
```

GUI, Web UI, installer, multi-user service, and cloud deployment are later concerns.

Future presentation layers should call the core library rather than duplicate retrieval/assembly logic.

---

## 12. Corpus / retrieval build updates

The first version should use a full deterministic rebuild:

```text
new Raw snapshot
→ Parsed
→ Canonical
→ rebuild Retrieval Units
→ rebuild BM25
→ rebuild vectors
```

Do not implement incremental indexing in v1.

Each retrieval build should preserve enough identity for reproducibility, such as conceptually:

```text
Canonical source/build identity
Retrieval Unit generator/version identity
Embedding model identity
BM25 configuration
important artifact hashes / manifest
```

This is lightweight reproducibility, not a return to W7-style benchmark governance.

---

## 13. First end-to-end evaluation

The user will manually extract approximately 70 human-authored Genshin knowledge questions from:

```text
BV1dMBLYTEwt
BV1RpiMBKEQo
```

The first version does not require a new complex benchmark-production protocol or exhaustive gold-evidence annotation.

Minimum question input:

```text
question_id
question
expected_answer
```

The RAG evaluation flow should make it practical to inspect:

```text
BM25 evidence
Dense evidence
Hybrid evidence
selected Evidence Packet
generated answer
citations
```

The user will manually review:

```text
Evidence = PASS / PARTIAL / FAIL
Answer   = PASS / PARTIAL / FAIL
```

Failed cases may then be attributed as needed:

```text
R = Retrieval
A = Evidence Assembly
G = Generation / reasoning
Q = Question / source-coverage issue
```

The purpose is to discover the actual system bottleneck, not to create another large benchmark-governance subsystem.

---

## 14. First-version exclusions are temporary, not permanent

The following are intentionally deferred from the first runnable baseline:

```text
new W7-style benchmark mining/governance
complex semantic eligibility
exhaustive gold evidence
Vector DB
ANN
reranker
query router
learned fusion
agentic / iterative retrieval
multi-hop planner
knowledge graph
LLM-controlled evidence selection
learned context compression
model training
simultaneous implementation of many GenerationProviders
production GUI / Web / cloud infrastructure
```

This is **not** a long-term rejection of these techniques.

If end-to-end evaluation or real product use shows that one of them addresses an actual bottleneck, it may be
introduced in a later iteration.

---

## 15. Existing Phase 04 assets

Reuse useful existing assets where appropriate, including conceptually:

```text
Canonical identities and provenance
structural relations
W2 representation lessons
contextualized-leaf lessons
existing BM25 / dense experiment utilities
deterministic retrieval/build helpers
```

Do not automatically inherit as new-path requirements:

```text
W7 candidate mining
Frozen48 machinery
semantic reviewer authority
material-quality retry governance
benchmark-production blindness/authority machinery
diagnostic lineage created only to support the old benchmark-production path
```

Principle:

> **Reuse tools, not baggage.**

---

## 16. Completed first implementation boundary

The reviewed Plan was approved and **P04-RAG-W1** implemented the first
bounded layer below candidate retrieval:

```text
Canonical
→ versioned Retrieval Units
→ candidate-neutral deterministic Evidence Assembly
→ provider-neutral Evidence Packet JSON / Markdown
```

It adds no BM25/Dense/Hybrid retrieval implementation, embedding, benchmark
production, GenerationProvider, or Canonical change. Retrieval Unit identity
is source-occurrence based; build identity is recorded separately. Known
non-indexable Canonical structures are audited skips, while unknown schema,
kind, or malformed supported shape fails closed.

The completed Plan answered the following from the live repository:

Its central question is:

> **How should the current real Canonical data be transformed into Retrieval Units, and how should context be restored in the simplest deterministic Evidence Assembly design?**

The Plan must explicitly answer:

1. What the current Canonical structures/addresses actually provide.
2. Which Canonical structures should become Retrieval Units for each major content type.
3. Which obvious-noise rules are safe enough for v1.
4. What text is retrieval-visible versus metadata-only.
5. How deterministic adjacency / parent / dialogue context can be recovered from current data.
6. Which Evidence Assembly behaviors are invariants.
7. Which context/Top-K/budget choices should be configurable.
8. Recommended first defaults and their rationale.
9. How configuration/build identity should be recorded for reproducibility.
10. How the design avoids forcing a future rewrite when later tuning the context window/strategy.
11. What existing W2–W7 code/assets can be reused without importing obsolete governance.
12. **Exactly one first implementation work unit** after the Plan is approved.

Later work must retain the same Canonical provenance and deterministic
assembly invariants, and requires its own scoped authorization.

---

## 17. Non-freeze statement

Everything in this amendment should be interpreted as the current **first runnable baseline direction**.

The expected lifecycle is:

```text
build runnable v1
→ run real questions
→ inspect evidence and answers
→ identify real bottleneck
→ tune / replace / enhance the responsible component
→ rerun evaluation
→ continue iterating
```

Specific models, retrieval parameters, context windows, fusion details, storage choices, providers, and even some
component boundaries may change later when justified by evidence.

The stable goal is not to preserve today's implementation choices.

The stable goal is:

> **a high-quality, evidence-grounded Genshin lore / narrative / worldbuilding RAG built from the zh-cn MiHoYo OBC corpus.**
