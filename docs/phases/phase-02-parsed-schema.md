# Phase 02 - Parsed Schema Contract (Draft)

Status: **draft; Batch 1 foundation**

This document defines the first executable Parsed-layer contracts for the
source-specific `mihoyo_obc` adapter. It is deliberately not a frozen schema.

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

The Batch 1 implementation only provides the classification container and
defaults it to `unknown`. It does not guess author or semantic role.

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
rules, and the interactive-dialogue graph contract. They require targeted
evidence before promotion.
