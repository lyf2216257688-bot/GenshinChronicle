# P05-W2 inventory and compact navigation architecture (2026-10-01)

## Decision and Boundary

Prefer an immutable, recall-oriented candidate ledger followed by an
independently versioned compact navigation **view over that ledger**. The view
groups related candidates, records omissions explicitly, and attaches scoped
interpretations to mechanically verified source quotes. It does not replace
the ledger with a shorter rewritten fact list. Both ledger and view remain
derived, source-bound navigation material. Official RU/Evidence Packet lineage
is still the authority for final answers and citations.

The architecture direction has bounded evidence. Its current automatic
semantic correction is **NOT PASS**. No output is promoted to active KG,
production/default, or a full semantic build. Group counts and label lengths
are descriptive operating measurements, not acceptance thresholds.

The user authorized architecture research, implementation, and sufficient
DeepSeek live experiments for this question. Work remained on the original
four frozen units; one repeat of current-inventory point1 and one reuse of
historical point1 tested input sensitivity. `502434` was not investigated.
No model comparison, corpus benchmark, RAG rerun, or push occurred.

## Why Separate the Representations

V3's small-set task omitted important point1 content. V4's inventory task
recovered it but created duplicate events/relations, incidental mentions,
poetic expansion and polarity errors. V5 reduced 119 items to 14 and again
omitted substantial frozen content. All three used the same output contract;
the evidence points to conflicting task objectives rather than a provenance
or compiler failure.

The first two-stage trials still asked Stage B to rewrite the entire semantic
list. Curator v0.1 compressed historical point1 to 70 items but remained noisy.
V0.2 reached 29 items, mostly by copying several independent propositions into
long labels: its longest label was 206 characters. Affirmative waiting and
prohibition shared global negative polarity; positive interior light and
negative exterior light were mixed, while substitute `negation`/`epistemic`
fields made normalization inconsistent. The ordinary control still duplicated
event and relation propositions. V0.3 restored shorter, separated records but
rebounded to 60 items and omitted the names/actions memory distinction.
These trials are NOT PASS, despite passing mechanical schema/binding gates.

A view over preserved candidates avoids this second destructive rewrite.
The full candidate ledger remains expandable; repeated singing is one visible
navigation group rather than several visible propositions. Poetic material
can remain in the inactive candidate ledger without becoming a standalone
navigation fact/node. Scoped annotations keep conflicting polarities inside
a group from becoming one global qualifier. This preserves candidate
accessibility; it does not automatically prove candidate truth or usefulness.

## Implementation and Identities

`src/genshin_corpus/retrieval/semantic_two_stage_experiment.py` is opt-in. It
prepares and reloads a frozen experiment manifest, verifies immutable inventory
bytes and producer source/Prompt against the v5 gate's original input, and
uses the existing formal route-pair runner. Added side input has a new semantic
input/request identity; original segments remain byte-equivalent in canonical
JSON. Runtime routes must match the frozen operating point before invocation.
Existing source binding, schema, local-reference validator, route fallback and
fail-closed behavior remain unchanged.

Stage A uses existing v4 Prompt. Stage B's tested grouped-view Prompt is
`phase05-w2-inventory-navigation-view-prompt-0.1`. Its identity is recorded in
each immutable run manifest and mechanical evidence. The prototype uses the
existing output envelope with explicit `qualifiers.role` values:

- `navigation_group`: concise heading, group kind, immutable inventory member
  IDs and source-scoped annotations; no group-wide semantic qualifier.
- `inventory_accounting`: explicit omission reasons and missing-source-claim
  declarations. This audit item is not a navigation node.

The additional offline contract rejects unknown/duplicate/unaccounted members,
wrong group source lineage, invented source quotes, global group qualifiers,
and declared source claims missing from inventory. It checks mechanics, not
whether an omission reason or qualifier is semantically correct. Provider
`missing_source_claims=[]` is not a completeness certificate.

All source quotes are checked against a deterministic text projection of the
original supplied source, including HTML entity decoding. This verifies text
presence only; it does not invent precise spans or prove quote entailment.
Original candidate qualifiers are immutable. An annotation is a proposed
source-scoped interpretation, not a blanket overwrite of a multi-clause item.
The grouped prototype is not wired to active semantic build/materialization
and does not claim accepted graph traversal or answer improvement.

## Live Results

All completed runs used DeepSeek V4.1 Flash, TokenMetro primary, Jizhi fallback,
streaming, `max_tokens=300000`, timeout 900 seconds, empty generation
parameters and zero automatic retries. All 20 completed requests were
accepted on TokenMetro primary, with zero fallback attempts. One extra
grouped historical-point1 request was accidentally interrupted by the agent;
its root remains unresolved and was never resumed or used semantically.
The identical frozen manifest was executed in a fresh r2 root.

| Trial | Point1 | Point2 | Dated email | 6430 |
| --- | ---: | ---: | ---: | ---: |
| New v4-style inventory, item count | 44 | 53 | 38 | 16 |
| Curator v0.1, current inventory | 44 | 66 | 31 | 15 |
| Curator v0.2, historical point1/current controls | 29 | 30 | 30 | 13 |
| Grouped view, current inventory, navigation groups | 13 | 9 | 9 | 5 |

Additional point1 trials: v0.1 over historical inventory produced 70 items;
v0.3 produced 60; grouped historical inventory produced 5 navigation groups
over 119 candidates; repeated current inventory produced 11 navigation groups
over the same 44 candidates. Grouped output includes one additional accounting
item per unit, excluded from group counts. Groups are not semantically
equivalent to inventory items, so count ratios are not accuracy metrics.

The four-unit source/config gate remains
`724aad586e2e0a21e2353fbcbc6c2dc4bae8cd1b210ec642e25154e5c60e794a`.
Frozen grouped current-inventory manifest:
`D:/Batch4_review/p05-w2-grouped-navigation-current-inventory-freeze-20261001-r1.json`,
identity `c1c0a5725362be925524faeac7d1832d4571b7bc536803919947a779b2051a09`.
Its live root is
`D:/Batch4_review/p05-w2-grouped-navigation-current-inventory-20261001-r1`,
run identity `3a3a2a62da405d562994ab627494e4a67d1ce583133caabe4d12e14fc2c26865`.
Repeat root:
`D:/Batch4_review/p05-w2-grouped-navigation-current-point1-repeat-20261001-r1`,
run identity `9a2378dd0b63b32d61a7bda9a7978a139e6800fb5dab1831105ce4f4e1ba0ff1`.

Completed-root audit/replay and full output hashes are saved at
`data/retrieval/p05-w2-two-stage-review-20261001-r1/mechanical_evidence.json`,
identity `357ab830c1b85af7793fc766bd33facc73675b3a0872cf8fda4ed742da9f059a`.
It independently compares original source projections to the frozen gate,
records intermediate inventory hashes, and audits eight completed roots.
Audit activity itself is zero provider/network. Aggregate completed-run
provider usage is 128,869 input / 397,732 output tokens, including 345,831
reasoning tokens. Interrupted usage, monetary cost, currency, billability and
upstream model revision remain UNKNOWN.

Historical v3/v4/v5 evidence is preserved. Actual historical true-point1 v4
also used 300000/900/stream/no-retry and SDK 3.19.2; other older baselines keep
their original parameters. No baseline was regenerated for parameter parity.

## Semantic Review

The companion
`p05-w2-inventory-navigation-architecture-review-20261001.json` binds the
manual source review and exact 39 frozen claim IDs to current inventory IDs.
All 39 source distinctions are accessible through inventory members in both
current point1 grouped outputs. This is recall/accessibility evidence, not 39
independently accepted normalized facts. Some inventory records have several
clauses; source syntax around forgetting names/actions remains ambiguous.

Historical v4's 119 items did not explicitly preserve the source phrase
`竟忘记了我的名字与所行的`. Curator v0.2 recovered it from source, whereas
v0.3 omitted it. Historical grouping preserved all supplied candidates but
failed to report this source gap. This disproves treating inventory size or a
model's self-check as a high-recall guarantee. The new 44-item inventory does
retain the phrase in f3. Changing granularity is not itself proof of model
randomness; provider/channel revision and request context also remain UNKNOWN.

First current grouping separated unknown sound, unknown commander, not-daring,
affirmative waiting, prohibitions and interior/exterior light with scoped
quotes. Its repeat left the candidate's unknown-sound negative polarity
uncorrected, and added ambiguous modality to mournful sound without a clear
epistemic trigger. Email grouping adds uncertain modality to unnamed roles/
pronouns, conflating unresolved entity identity with uncertainty of a claim.
Nested attribution and correction completeness remain unstable. These are
real semantic defects; neither source quote presence nor full accounting
converts the result into semantic PASS.

Point2 retains naming, name meaning, co-residence, inability-to-speak versus
later speech/wisdom, reported southern mission, permission, departure and
remembrance through 52/53 candidates; only a point-marker topic was omitted.
Prospective/actual interpretation still needs care. The email retains 37/38
candidates with date, invitation, uncertain arrival, reported gifts,
location/distance, self-correction, visitor uncertainty and greeting intent;
only a courtesy closing was omitted. Ordinary 6430 retains 16/16 candidates
and their conditional/tentative qualifiers, while its image-only segment
stays explicitly no-navigation and unbound to textual group claims. Group
headings are navigation labels; reading them as unconditional facts would be
a new regression.

## Atomic inventory follow-up (2026-10-02)

The next bounded unit added an opt-in atomic Stage A Prompt over the existing
v5 gate inputs. It requires one independently stated proposition per item,
an exact original-source `qualifiers.source_quote`, and qualifier fields scoped
to attribution, modality, polarity, time, condition, and source quote. The
manifest preserves the v5 gate identity as its frozen dependency while binding
request/source identity to the gate's frozen semantic-build identity.

Provider-free validation passed after fixing the import and identity wiring:
65/65 focused unittest cases, `py_compile`, and `git diff --check`. The
two-unit manifest `D:/Batch4_review/p05-w2-atomic-inventory-manifest-20261002-r1.json`
has identity `661be7d4c59f01f7dc1ae6223098bd2a73c42b08148cda1accf08a0be771dc03`
and freezes point1 plus control `6430` with the existing DeepSeek V4.1 Flash /
TokenMetro-primary operating point (`300000` tokens, 900 seconds, streaming,
zero automatic retries). A separate one-unit control manifest was also frozen
for `6430` so the point1 stream failure could not affect its result.

The first point1 request was issued once at
`D:/Batch4_review/p05-w2-atomic-inventory-live-20261002-r1`. TokenMetro
returned HTTP 200 but emitted no SSE chunk for 307 seconds; the attempt ended
`stream_complete=false`, with no visible output and UNKNOWN usage. The runner
stopped fail-closed before Jizhi fallback and left point1 pending/blocked. This
root is immutable mechanical evidence; it is not semantic evidence and was not
retried in place.

The independent `6430` root
`D:/Batch4_review/p05-w2-atomic-inventory-live-6430-20261002-r1` completed with
one accepted TokenMetro request, complete stream, local schema/source-binding
PASS, and atomic contract PASS. Its 11 items used exact source quotes and
separated conditional/uncertain claims in this small control. This is a local
control signal only; it does not establish point1 recall or general stability.

A point1-only retry under the same manifest semantics used a new immutable root
`D:/Batch4_review/p05-w2-atomic-inventory-live-point1-20261002-r2`; TokenMetro
failed with `APIConnectionError` before any stream chunk, so it contains no
semantic output. A subsequent point1-only retry used
`D:/Batch4_review/p05-w2-atomic-inventory-live-point1-20261002-r3` with the same
point1 input, atomic Prompt, route and operating configuration. TokenMetro
returned HTTP 200 with one complete stream and local schema/source-binding PASS.
The output contains 83 candidates and all 39 frozen point1 claims are accessible
through source-quoted inventory members, reducing v4's 119 items while
retaining the known high-recall claim set.

The semantic result remains NOT PASS for qualifier and atomic-scope correctness:
`我的心未曾晓得这声音` is marked negative instead of uncertainty, several
rhetorical questions use an imprecise condition field, and inability to forget
grief is merged with removing suffering. Repeated `我便歌唱` and other short
phrases remain as separate source-bound candidates; compact navigation must
still deduplicate them. The validator was minimally corrected so repeated source
text at distinct positions is retained for semantic review instead of rejected
by quote-text uniqueness. The independent audit is
`D:/Batch4_review/p05-w2-atomic-inventory-live-point1-20261002-r3-semantic-audit.json`.
Atomic recall is bounded PASS (39/39 accessible), while atomicity and qualifier
scope remain NOT PASS. Do not expand the sample or activate the
inventory/navigation architecture yet.

## Atomic inventory v0.7 bounded validation (2026-10-03)

The atomic inventory Prompt was revised opt-in from v0.4 to v0.7 in three
small steps, preserving all prior prompt identities and the existing schema,
source binding, route, and operating point. The revisions clarified adjacent
antecedent resolution for known objects, intended outcomes versus completed
states, conditional antecedents, and immediate self-correction. The source
projection validator was also corrected to accept exact quotes from retained
`decoded.rich_text` alongside the deterministic text projection; both forms
must still occur literally in the supplied source value.

New immutable manifests and roots are recorded under
`D:/Batch4_review/p05-w2-atomic-inventory-manifest-*-20261003-v07-r1.json` and
`D:/Batch4_review/p05-w2-atomic-inventory-live-*-20261003-v07-r1`.
Two independent point1 requests used the same v0.7 manifest and completed on
TokenMetro primary: 85 and 83 candidates. The dated-email control completed
with 38 candidates. The 6430 control completed with 14 candidates; its initial
validator rejection was preserved, then the same immutable output was replayed
after the rich-text projection fix and passed local quote/source binding. No
live root was overwritten or retried in place.

The source-anchored review at
`D:/Batch4_review/p05-w2-atomic-inventory-v07-review-20261003-r1.json`
maps all 39 frozen point1 claims independently in both point1 outputs. Both
runs keep unknown sound, unknown spirit/commander identity, and unknown
mourning target scoped as modality; keep prior seeing, explicit prohibitions,
and explicit non-finding as polarity; split inability to forget from inability
to remove suffering; preserve rhetorical and ambiguous memory syntax; and keep
the known gold-wrapping object known. The controls preserve conditional
antecedents/tentative outcomes and mark the email's superseded comparison as
self-corrected before the stronger final claim. This is bounded semantic review
evidence, not automatic semantic acceptance or production eligibility.

Provider-free focused tests passed 66/66, `py_compile`, and `git diff --check`.
Each accepted live root has one TokenMetro request, zero fallback attempts, and
passes offline SDK integrity; the 6430 replay also passes the atomic contract.
The v0.7 point1 result is stable across two requests for this fixed input, but
ordinary-corpus stability, provider revision, nested attribution, exact
occurrence identity for repeated quotes, and broader poetic interpretation
remain UNKNOWN. This bounded unit is sufficient to consider a separately
authorized larger semantic validation sample, while Full Semantic Build,
production/default adoption, active KG/Navi activation, and push remain out of
scope.
## Verification and Next Decision

Provider-free tests passed: two-stage experiment 9/9, SDK runner discovery
44/44, Prompt tests 10/10 and B-revision tests 9/9. These are the actually run
counts, not the older 63/63 documentation count. Mock transport exercised the
formal runner with side input and replay; real API activity in these tests was
zero. Completed live roots all passed offline integrity/replay. All six
grouped accepted requests passed group accounting/source quote checks.

Do not expand to a larger automatic semantic build or active KG yet. Enough
evidence exists to choose representation separation, but not a reliable
automatic semantic operating point. The next bounded work should address
atomic/scoped inventory claims and complete interpretation of inherited
qualifier errors on these same failure cases, then validate typed navigation
edges/RU resolution before sampling more broadly. This result itself does
not authorize another run.

Long-term correctness boundaries: immutable originals/intermediates; exact
source and stage/request identity; complete candidate disposition accounting;
no silent loss under compaction; explicit source-scoped modality, negation,
attribution and state/temporal distinctions; separate mechanical and semantic
acceptance; inactive unaccepted views; RU/Evidence Packet citation authority.

Operating choices: v4 inventory Prompt, grouped Prompt v0.1, same DeepSeek for
both stages, two requests, current token/timeout/route settings, four samples,
group shapes/counts and use of the existing topic/qualifiers envelope. No new
production schema, model default, global entity normalization, graph database,
or permanent two-model/two-Prompt requirement is established.
