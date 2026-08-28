# P04-W2 — Derived Retrieval Representation + Lexical Baseline

This note records one deterministic offline experiment over accepted Canonical
run `phase03-batch5b-p01eb-full-20260824`. All counts and metrics below are
observations of that run and the deliberately small benchmark-v0 seed; they do
not select a production Retrieval technology or establish corpus contracts.

## Derived-document boundary

Schema `phase04-retrieval-document-0.1` and representation version
`phase04-derived-representation-0.2` produce versioned, rebuildable documents.
Each document carries explicit Canonical source coverage: record, section,
component context, unit, the unit LineageLink, and where applicable decoded
paths or observed dialogue group/node/edge coordinates. Its deterministic
artifact address hashes representation version, arm, text, coverage, and
observable metadata; it is not a semantic or cross-snapshot identity.

Gold matching uses that coverage only. A record match accepts a document from
the record; Section and ComponentContext matches require the corresponding
explicit address; Unit matches require the exact Unit. Decoded-path and
dialogue-node/edge gold additionally require the corresponding explicit
coverage. Text equality and common page membership never count as a hit.

## Implemented experiment arms

- `naked_leaf`: only existing `rich_text.normalized_text`; no parent context.
- `contextualized_leaf`: the same leaf plus existing record name, Section name,
  and source component ID.
- `structured_path_value`: generic decoded scalar path/value view only, not a
  raw/decoded JSON blob. It covers the emitted decoded paths and root.
- `dialogue_graph_local`: one observed edge-local parent/child text view, plus
  isolated observed nodes. It preserves group ordering, source IDs, and edges;
  it infers neither speaker nor a linear dialogue sequence.

The latter two arms were justified by W1 observations of 84,071 decoded
mappings and 5,002 dialogue graphs / 62,727 observed edges. No section passage
or component-specific semantic mapping was introduced.

## Artifact and lexical observation

The manifest-driven build SHA-256 checked all 16,437 Canonical records once.
It produced four gzipped run-level JSONL artifacts under ignored
`data/retrieval/`, not one file per Unit:

| arm | documents | compressed bytes |
| --- | ---: | ---: |
| naked_leaf | 242,965 | 39,409,639 |
| contextualized_leaf | 242,965 | 40,022,787 |
| structured_path_value | 81,730 | 48,328,716 |
| dialogue_graph_local | 69,735 | 23,101,207 |

The lexical experiment is stdlib-only, using CJK character unigrams plus
lower-cased contiguous ASCII tokens (`phase04-cjk-unigram-ascii-token-0.1`)
and a deterministic BM25-style scorer. Repaired result
`phase04-lexical-experiment-0.2` uses
`phase04-bm25-source-address-tiebreak-0.2`: equal scores are ordered only by
explicit Canonical source coverage (record, Section, ComponentContext, Unit,
decoded paths, then observed dialogue coordinates). Exact duplicate coverage
keys retain their deterministic artifact occurrence order; derivative document
identity never participates. It is an interpretable offline instrument, not a
production index or a selection of BM25 as the future solution.

The preceding `lexical-experiment.json` used document-ID tie breaking. It is
retained as superseded historical evidence, but its metrics, ranks, and
hard-negative positions must not be used for Retrieval architecture selection.
The authoritative repaired result is
`metadata/lexical-experiment-source-address-tiebreak.json`, with the same r02
representation manifest dependency and unchanged artifacts.

## Benchmark-v0 results

`benchmark-v0` has 11 queries / 13 locations and is only a smoke/diagnostic
seed. Metrics are unweighted and coverage-based.

| arm | Recall@1 | Recall@5 | Recall@10 | MRR | primary sufficient@10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| naked_leaf | 0.2727 | 0.2727 | 0.3636 | 0.2900 | 0.2727 |
| contextualized_leaf | 0.2727 | 0.3636 | 0.4545 | 0.3367 | 0.3636 |
| structured_path_value | 0 | 0.0909 | 0.0909 | 0.0230 | 0.0909 |
| dialogue_graph_local | 0 | 0 | 0 | 0.0001 | 0 |

Context adds one top-10 positive hit over naked leaves: `月童的库藏` reaches
rank 2 (versus 23); the readable-book query remains top 10 at rank 9 (versus
8). Record-level exact-name coverage is rank 66 naked and rank 12
contextualized, so title context helps but is still outside top 10 for that
leaf arm. It also improves the quote/paraphrase ranks but does not bring them
into top 10. Exact text/map/weapon examples reach top 10 in both leaf arms.
The relationship query retrieves one observation at rank 1 but its complete
two-item sufficient set is rank 543 for the contextualized arm. The existing
gameplay/build hard negative is not top 10 (rank 79,545 naked; 97,348
contextualized), so this seed exposes one contamination candidate but does not
quantify a routing need.

Generic structural and graph projections make their required evidence
addressable: birthday ranks 48, voice-table ranks 31, and the branch edge ranks
718. Their failure to reach top 10 is lexical overlap/ranking evidence, not a
reason to erase the projections. No broad Section/page document was allowed to
claim Unit, decoded-path, or dialogue-edge credit.

The simple in-memory implementation observed index-build times of about
7.7–28.2 seconds per arm and mean per-query full-scan times of about
1.4–3.4 seconds. These are implementation-cost observations, not serving
latency claims.

## Compact per-query failure analysis

The run-level lexical result records top-10 document addresses and every gold
rank for every arm. The table below is a concise reading of those deterministic
observations, not an LLM judgment.

| benchmark-v0 query | best observed coverage / rank | diagnostic observation |
| --- | --- | --- |
| exact-name 阿蕾奇诺 | naked rank 66; contextualized record-level rank 12; structured rank 5 | title context helps leaf ranking, but no dedicated record document exists in W2; structured's record-level hit is a diagnostic side effect, not a structured-value result. |
| birthday | structured rank 48 | generic decoded projection covers the exact path; lexical ranking is weak. |
| character-story quote | contextualized rank 407 | quote is covered but sparse lexical overlap/common characters keep it outside top 10. |
| character paraphrase | contextualized rank 139 | expected paraphrase failure for lexical overlap, not a coverage failure. |
| dialogue branch | graph-local rank 718 | option/edge coverage exists; graph-local lexical ranking is inadequate. |
| 月童的库藏 | contextualized rank 2 | parent observable context materially improves this narrative case. |
| readable book | naked rank 8; contextualized rank 9 | direct lexical evidence reaches top 10 in both leaf views. |
| weapon story | naked/contextualized rank 1 | direct lexical evidence reaches top 1. |
| NPC/map | naked/contextualized rank 1 | direct lexical evidence reaches top 1. |
| relation, two observations | statement rank 1; contextualized speaker rank 543 | one item retrieval differs from complete sufficient-evidence coverage; future assembly remains separate. |
| character voice | structured rank 31 | generic decoded path/value view covers the Unit but is outside top 10. |

Only one benchmark hard negative is annotated. Its exact document ranks far
below top 10 in both leaf arms, so this run does not establish a corpus-wide
wrong-role contamination rate or justify a routing mechanism.

## Limits and next evidence need

The seed is too small to choose lexical, dense, Hybrid, analyzer, reranking,
or routing architecture. It supports retaining contextual leaf, generic
structured-path/value, and graph-local representations as experimental
derivatives. It also shows that benchmark growth must add evidence-grounded
structured and dialogue cases before judging those arms. P04-W3
contamination/routing work is not yet justified by this single hard-negative
observation alone; any future work must first measure contamination on a larger
diagnostic slice without inventing a content-role taxonomy.
