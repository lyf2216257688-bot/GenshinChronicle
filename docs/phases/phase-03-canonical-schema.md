# Phase 03 - Canonical Schema Contract

Status: **CLOSED / PASS. Architecture Plan APPROVED; Batch 1 CLOSED / PASS @ `0f7cff4`; Batch 2 CLOSED / PASS @ `e34468e`; Batch 3 CLOSED / PASS @ `0fc609b`; Batch 4 CLOSED / PASS @ `f287bef`; Batch 5A CLOSED / PASS @ `4726d8a`; Batch 5B production gate PASS.**

## Authorization

The Phase 03 architecture plan is **APPROVED** and Phase 03 is **CLOSED /
PASS**. The independently reviewed implementation checkpoints are Batch 1 —
Canonical Contract Foundation (`0f7cff4`), Batch 2 — Structural OBC Projector
(`e34468e`), Batch 3 — Dialogue and structured observations (`0fc609b`),
Batch 4 — Storage and incremental processing (`f287bef`), and Batch 5A —
fixture and representative acceptance (`4726d8a`). The separately authorized
Batch 5B streaming production gate passed over the accepted 16,437-record
Parsed snapshot. Its Canonical manifest is `complete`: 16,437 input and
accounted records, 0 input-integrity failures, 0 reuse, and 16,437
reprojected records; streaming audit verified every record path, SHA-256, JSON
object, record ID, and Parsed identity key.

The current production input scope remains `mihoyo_obc` + `zh-cn`. Locale must
remain explicit in contracts so the current scope does not become a permanent
single-language assumption.

## Responsibility and boundary

Canonical converts source-specific Parsed observations into a stable,
deterministic, auditable research-document representation. It is not merely
cleaned text.

Canonical must:

- preserve source text and source values as represented by Parsed;
- preserve meaningful order, grouping, structured values, unknown content,
  unsupported content, diagnostics, and classification uncertainty;
- remain traceable through the exact Parsed observation to available Raw
  evidence;
- keep source evidence, identity, semantic normalization, provenance, and
  future retrieval representations separate.

Canonical may normalize representation only through explicit, versioned,
deterministic rules. It must not silently correct wording or meaning, infer
semantic equivalence, or promote OBC platform content to
`official_game_text` without independent classification evidence.

Canonical does not define Retrieval passages/chunks, embeddings, search
indexes, claims, facts, events, entities, aliases, knowledge graphs, or RAG
behavior.

## Core object model

The minimum durable hierarchy is:

```text
CanonicalRecord
-> CanonicalSection
-> CanonicalUnit
```

Supporting value objects are:

- `CanonicalObservation`: the exact Parsed run, manifest, record, hash,
  fingerprint, status, and version dependencies used to materialize a record;
- `ComponentContext`: source-component grouping and evidence retained without
  introducing a semantic Canonical component entity;
- `LineageLink`: the relationship from a Canonical value to a Parsed path and
  any supported Raw or dependency evidence.

No `Entity`, `Fact`, `Claim`, `Event`, `Passage`, `DocumentRevision`, or
semantic `CanonicalComponent` is introduced in Phase 03.

## Identity contract

Identity, fingerprint/version identity, and source position are independent.

- `record_id` is required. It addresses one Canonical materialized observation
  and is derived deterministically from exactly
  `parsed_run_id + parsed_identity.key + parsed_record_sha256`. It is not a
  semantic identity.
- `parsed_identity` is required for every valid Parsed input record and is
  copied from that record without promotion.
- `source_identity` is present only when the input is a structurally valid
  normal `ParsedDetail` carrying the verified source-contract identity. It is
  copied, not reconstructed.
- Canonical semantic/logical identity remains unresolved and is not populated
  in Phase 03.
- JSON pointers, array indexes, layout paths, and ordinals are source
  positions, not logical identities.

Neither `source_identity` nor any Parsed module/component/content-unit identity
may be used for cross-snapshot semantic merge or projection reuse. The current
source tuple `(mihoyo_obc, zh-cn, content_id)` remains a source-contract
identity only.

## Status and blocked observations

Canonical record status is one of:

- `canonical`: deterministic projection completed without anomaly;
- `canonical_with_anomalies`: projection completed while preserving explicit
  non-blocking diagnostics;
- `blocked_integrity`: the Parsed input record is an accounted
  `blocked_integrity` observation.

For a Parsed `blocked_integrity` record:

- `parsed_identity` remains required and preserves the snapshot-only
  `detail_observation` identity;
- `source_identity` is null;
- zero sections, component contexts, and units is valid;
- the blocked reason and any available diagnostics are preserved;
- input `content_id` and channel memberships are preserved in
  `record_metadata`, with channel membership lineage using
  `evidence_scope=parsed_dependency` and no fabricated detail RawRef;
- the Parsed record hash and all available lineage are preserved;
- a source identity must not be synthesized from manifest source, locale, or
  `content_id`.

A missing Parsed record, unreadable record, or Parsed record hash mismatch is
a Canonical input-integrity failure. It makes the Canonical run incomplete and
must not be converted into a synthetic blocked Canonical record.

For every record entry in a validated complete Parsed manifest, exactly one
Canonical record must be accounted. A valid serialized `blocked_integrity`
record is an input variant covered by that rule, not a record to skip.

## Structural projection boundary

`CanonicalSection` is a source-independent structural container. The initial
OBC rule mapping one ordered `ParsedModule` to one `CanonicalSection` is a
versioned projector policy, provisionally `obc-modules-as-sections-0.1`, not a
permanent schema invariant.

Module ID, module index, repeated/submodule flags, origin-module ID, and layout
observations remain source context. They are not promoted to stable Canonical
section identity. Any `section_id` or `unit_id` used by the contract is only an
observation-local addressing label; it is not a semantic or cross-snapshot
identity and is not derived from Parsed child IDs as a stable key. Unsupported
module entries must remain ordered and accounted; the projector must not drop
them because they lack normal module shape.

## Component context

Phase 03 does not introduce a semantic `CanonicalComponent`. Each section
instead retains ordered `ComponentContext` values containing the minimum
component-level evidence required to interpret and audit its units:

- observation-only component key and component ordinal;
- source component ID and source data encoding;
- source layout and style;
- Parsed component fingerprint, status, classifications, and diagnostics;
- component source position and available RawRefs;
- child-unit ordinals and count.

Each Canonical unit references its parent component context. A zero-unit
component still has a component context with `unit_count=0`; no artificial
text unit is created. Component grouping, ordering, unsupported state, and
diagnostics must not be silently flattened away.

## Lineage contract

Every Canonical record must carry a `CanonicalObservation` with:

- Parsed run ID;
- Parsed manifest path and SHA-256;
- Parsed record path and SHA-256;
- Parsed schema/parser/pipeline/rule versions;
- Parsed status and Parsed semantic fingerprint.

Every section, component context, and unit must be traceable to its source
inside the Parsed record. `LineageLink` distinguishes:

- `parsed_json_pointer`: a pointer inside the Parsed record;
- RawRef `json_pointer` / `embedded_json_pointer`: pointers inside Raw;
- `evidence_scope=direct_raw`: the Parsed node carries exact Raw evidence;
- `evidence_scope=inherited_parent_raw`: only a supported parent RawRef is
  available and must not be described as an exact value pointer;
- `evidence_scope=parsed_dependency`: the value comes from a Parsed-run
  dependency rather than an attached RawRef.

RawRefs are 0..N because not every projectable value has an exact attached
RawRef. They must never be fabricated. Channel memberships, for example, are
derived from the Parsed manifest dependency and use `parsed_dependency` plus a
dependency locator; they do not inherit a detail RawRef as if that artifact
proved membership.

The Canonical run manifest is authoritative for the input Parsed manifest
dependency. Its SHA-256 is also copied into each `CanonicalObservation` so an
individual record remains independently verifiable.

## Metadata ownership

Metadata has one explicit home at each structural level:

- record/page source metadata, channel memberships, and template layout belong
  to record metadata;
- module layout observations and module source context belong to sections;
- component encoding, layout, style, classifications, and diagnostics belong
  to `ComponentContext`;
- unit metadata belongs to `CanonicalUnit` only when the value has an actual
  unit-level ordering, lineage, or preservation requirement.

The OBC projector must not turn all page, navigation, alias, menu, filter, or
editorial metadata into Canonical units merely for uniformity. A metadata unit
requires an explicit versioned projector rule and evidence that record- or
section-level metadata cannot preserve the needed ordered value.

## Content and semantic boundaries

Canonical units may represent text, dialogue graphs, structured observations,
metadata with a justified unit-level role, and unsupported values. A decoded
mapping is a structured observation, not a verified fact.

Provenance and `content_role` remain separate, versioned, provisional
classifications. `unknown` and `mixed` remain valid. The existing provisional
`interactive_dialogue -> dialogue` content-role rule may be carried forward;
it does not establish provenance or a final narrative ontology.

Dialogue projection must preserve the Parsed graph, groups, nodes, edges,
orders, options, dialogue values, icons, unknown fields, RawRefs, and
diagnostics. It must not infer speakers, repair anomalies, linearize branches,
or reinterpret group/option semantics. Generic decoded, rich-text, and
dialogue-graph units derived from one component must retain their relationship
and must not be silently deduplicated as equivalent statements.

Phase 03 permits deterministic structural normalization only. It does not
merge repeated presentation blocks, aliases, multiple blocks, aggregate/index
pages, source observations, or similar text based on assumed semantic
equivalence.

## Versions, fingerprints, and incremental boundary

Canonical dependencies must separately version:

- Canonical schema;
- Parsed-to-Canonical transform;
- structural normalization rules;
- relevant classification rules.

The dependency fingerprint includes the Parsed run and manifest dependency,
Parsed record SHA-256 and semantic fingerprint, and all relevant Canonical
versions. The Canonical content fingerprint covers the deterministic Canonical
content projection and excludes Parsed observation/dependency fields such as
run identifiers, dependency hashes, and version inputs, as well as run paths,
timestamps, output paths, and other non-semantic runtime metadata. The current
0.1 content projection serializes retained lineage and source-position evidence,
so its current fingerprint may reflect that evidence. Whether a fine-grained
Parsed/Raw lineage-location or source-position change has cross-snapshot
semantic significance remains unresolved and deferred; this runtime behavior
does not settle that policy.

Same Parsed-run reuse may be implemented in a later Phase 03 batch after record
and dependency hashes are revalidated. Cross-Parsed-run or cross-Raw-snapshot
semantic projection reuse remains unresolved and must not be implemented as if
equivalence were proven.

## Deterministic serialization and storage boundary

The completed Phase 03 implementation uses deterministic UTF-8 JSON,
immutable/conflict-safe record artifacts, per-record SHA-256 values, and a
manifest carrying input dependencies, versions, status/accounting totals,
diagnostics, and reuse/reprojection decisions. No database, search index,
vector store, graph database, or serving infrastructure is established.

## Implementation batches

1. **Canonical Contract Foundation — CLOSED / PASS @ `0f7cff4`**: contracts/value objects, identity and
   blocked-record boundaries, lineage, component context, metadata ownership,
   support/status rules, version/fingerprint contract, deterministic
   serializer, and hand-built fixtures/tests. The hand-built identity fixtures
   must cover a normal `ParsedDetail` with `source_identity`, a
   `blocked_integrity` record with a RawRef, a `blocked_integrity` record
   without a RawRef, and the rule that `blocked_integrity` must not synthesize
   `source_identity` from manifest source, locale, or `content_id`.
2. **Structural OBC Projector — CLOSED / PASS @ `e34468e`**: the versioned module-to-section rule,
   component contexts, ordered units, unsupported accounting, record metadata,
   and source-position preservation.
3. **Dialogue and Structured Observations — CLOSED / PASS @ `0fc609b`**: rich text, generic decoded values,
   dialogue graph preservation, diagnostics, and derived relationships.
4. **Storage and Incremental Processing — CLOSED / PASS @ `f287bef`**: immutable Canonical runs,
   manifests, integrity checks, same Parsed-run reuse, and dependency-driven
   reprojection.
5. **Acceptance and Documentation**:
   - **5A - Fixture, Representative Acceptance, and Documentation — CLOSED / PASS @ `4726d8a`**:
     fixtures, representative real Parsed records, and documentation closure.
   - **5B - Production Corpus-Wide Canonical Gate — PASS**: the separate, high-cost
     streaming Parsed-to-Canonical gate over the accepted 16,437-record
     snapshot. Its run ID is `phase03-batch5b-p01eb-full-20260824`; it used
     Parsed manifest SHA-256
     `b52483d76f92102a5cb8ece3340de8aa0630c0a0e1f6dbed04bd28f60922b29d`.

## Acceptance and closure gate

Batch 5B completed the separately authorized streaming production acceptance
over the accepted 16,437-record Parsed snapshot using the existing Canonical
pipeline. This was not a Raw crawl or Raw-to-Parsed reparse. The completed
Canonical manifest records 16,437 input and 16,437 accounted records, 0
input-integrity failures, 0 reuse, and 16,437 reprojected records.

The streaming audit verified all 16,437 Canonical record paths, SHA-256
values, JSON objects, record IDs, and Parsed identity keys. Known unsupported
and dialogue diagnostics remained non-blocking evidence under the defined
projector contract. No second full 16,437-record reuse or materialization run
was performed.

This closure establishes bounded structural/accounting, lineage, deterministic
serialization, and manifest/record-integrity properties only. It does not
prove upstream semantic completeness, official-game-text coverage, semantic
entity correctness, cross-snapshot identity, or Retrieval/RAG quality. It does
not establish cross-run normalized semantic serialization or content-
fingerprint comparison semantics.

Phase 03 may close while complete provenance/content-role taxonomies,
cross-snapshot identity/reuse, independent speaker fields, full dialogue
semantics, final corpus size/token counts, Retrieval passage/chunk design, and
all Retrieval/RAG technology choices remain explicitly unresolved.
