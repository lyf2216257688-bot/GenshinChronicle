# Phase 02 - Parsed Schema Contract (Draft)

Status: **CLOSED; draft Parsed contract remains evolutionary**

This document defines the first executable Parsed-layer contracts for the
source-specific `mihoyo_obc` adapter. It is deliberately not a frozen schema.

## Closure

Phase 02 closed at checkpoint `5c2d131` (`phase02: add incremental parsed
acceptance pipeline`). Accepted local P01-EB validation accounted for
16,437/16,437 input details with 0 `blocked_integrity` records. An independent
same-run reuse validation reported 16,437 reused and 0 reparsed records; full
local regression passed 53/53 tests.

These results establish an auditable deterministic Parsed result for this
local Raw snapshot. They do not claim upstream semantic completeness, complete
semantic handling for every nested component, or a frozen Parsed schema.

## Boundary

Raw remains immutable evidence and the only input to parsing. Parsed performs
deterministic source decoding, structure preservation, traceable extraction,
and explicit anomaly/unsupported reporting. Parsed does not normalize
Canonical entities, create retrieval chunks, generate embeddings, extract
claims, or implement RAG behavior.

## Draft record hierarchy

`ParsedDetail` represents one `data.page` observation. It owns ordered
`ParsedModule` records. A module owns ordered `ParsedComponent` records. A
component may produce ordered `ParsedContentUnit` records. A source value that
cannot yet be understood is represented by `ParsedUnknown` and is retained,
never silently dropped.

The hierarchy is source-oriented. `component_id` is a dispatch key from OBC,
not a Canonical semantic type. `template_layout` and `modules[]` are retained
as separate source structures because their orders are not interchangeable in
the observed payloads.

## Shared contract

Every record carries:

- `schema_version` and parser/rule version metadata;
- a provisional identity with an explicit stability (`logical`, `candidate`,
  or `snapshot_only`);
- one or more `RawRef` values containing run, artifact, hash, content ID, and
  JSON pointer information;
- source position, separate from identity;
- source and Parsed fingerprints;
- `parse_status` and diagnostics;
- separate, versioned `provenance` and `content_role` classifications.

The Batch 1 implementation provided the classification container. Batch 3 adds
one source-structure rule: `interactive_dialogue` may receive the provisional
`dialogue` content role. Provenance remains `unknown`; provenance and role are
independent and versioned. Unknown and mixed states remain valid first-class
results.

## Identity and fingerprints

Detail identity is currently logical for `(source, locale, content_id)` within
the verified Phase 01 contract. Module, component, and content-unit identities
remain candidate or snapshot-only until source stability is verified. Array
indexes and layout paths are positions, not stable identity.

`source_fingerprint` hashes a deterministic canonical JSON representation of
the exact source value. `parsed_fingerprint` hashes a deterministic semantic
projection supplied by the parser. Runtime paths, timestamps, diagnostics, and
run metadata must not be included in a Parsed semantic projection.

## Status and failure policy

- `parsed`: deterministic extraction completed without anomaly;
- `parsed_with_anomalies`: extraction completed with non-blocking diagnostics;
- `preserved_unsupported`: source value was retained but no handler was
  promoted for it;
- `blocked_integrity`: required identity, artifact hash, or envelope integrity
  is invalid for the requested scope.

Unknown local structure is non-blocking when its exact value and RawRef are
preserved. Artifact/hash or page/content identity conflicts are blocking for
the affected detail and must remain visible in the run manifest.

## Deferred contracts

The following remain UNKNOWN: complete `content_role` and `provenance`
taxonomies, cross-snapshot module/component/block identity, HTML normalization
rules, and the full interactive-dialogue source contract (independent speaker
field, option/dialogue semantics, group semantics, cross-group ordering and
references). They require targeted evidence before promotion.

Cross-snapshot stable identity and immutable semantic-projection reuse remain
deferred until an explicit observation/projection contract is designed. Canonical,
Retrieval, and RAG decisions are outside this closed Phase 02 contract.

## Batch 2 promotion boundary

The OBC adapter now decodes the verified `data.page` envelope, preserves
module/component and template-layout observations, and emits a structure-
preserving rich-text value for markup-bearing strings. Component IDs remain
generic dispatch keys; no component family is promoted to a Canonical semantic
type, so generic components are explicitly marked `preserved_unsupported` while
their decoded payload remains available. The standard-library HTML parser is
used only to preserve observed tags, attributes, text, links, media, entry
references, and text-to-tree paths. Normalized text is a derived view and can
be regenerated from `raw_markup` and the retained tree. Artifact SHA-256 and
page/content identity mismatches are blocking integrity errors.

## Batch 3 promotion boundary

The OBC `interactive_dialogue` component now emits an additional
`dialogue_graph` content unit. Groups, source node IDs, node insertion order,
child edge order, options, dialogue values, icons, unknown node fields, and
Raw pointers are retained. The representation is a graph: shared children,
multiple parents, cycles, orphan nodes, dangling edges, and root anomalies are
reported deterministically rather than coerced into a tree or a single
storyline. Speaker is never inferred from HTML text. The original generic
decoded component unit remains available.

Classification is intentionally minimal and deterministic. The
`interactive_dialogue` source component ID supplies only the provisional
`dialogue` role label; provenance stays `unknown`, and no corpus-wide semantic
classification or taxonomy freeze is performed.

## Batch 4 promotion boundary

The OBC Parsed runner now reads one completed local Raw run, validates each
detail artifact against both its Raw manifest path/hash and response metadata,
and writes one immutable Parsed record per input detail. Its manifest carries
the Raw run/manifest dependency, schema/parser/rule versions, per-observation
dependency fingerprints, record hashes, detail-level status accounting, and
deduplicated nested diagnostics. An observation dependency includes the Raw
artifact fingerprint, effective ordered channel memberships passed to the
adapter, and schema/parser/pipeline/rule dependencies. A change to one of
those inputs invalidates only the corresponding observation in a new Parsed
run.

Reuse is intentionally limited to the same source, locale, and Raw run ID:
reused records retain their original `RawRef`, so Phase 02 does not claim that
a record from another Raw snapshot represents the current observation.
Cross-snapshot immutable semantic-projection reuse remains deferred until an
explicit observation/projection contract exists. Completed-run fast reuse also
rechecks each Raw artifact/metadata and record hash, and recomputes the
expected observation dependency before returning a complete manifest. A
completed Parsed manifest is conflict-safe and is not silently replaced by
different content.

Corpus-wide local acceptance for `p01eb_full_20260824` accounted for all
16,437 completed Raw detail artifacts. The resulting detail statuses were
16,437 `parsed`, 0 `parsed_with_anomalies`, 0 `preserved_unsupported`, and 0
`blocked_integrity`. This status accounting describes deterministic parsing of
this local Raw snapshot. It does not establish upstream semantic completeness,
freeze unknown component semantics, or change the identity evidence boundary.
