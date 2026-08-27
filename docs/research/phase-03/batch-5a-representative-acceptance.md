# Phase 03 Batch 5A — Representative Acceptance Evidence

This note records small-sample acceptance evidence for the completed Phase 03
Canonical contracts, OBC projector, and local run pipeline. It is evidence for
Batch 5A only; it does not change the project-status owner in
`docs/current-phase.md`, close Batch 5A or Phase 03, or authorize Batch 5B.

## Evidence boundary

The production Parsed input was the accepted local closure-v2 run:

```text
data/parsed/mihoyo_obc/zh-cn/phase02-batch4-p01eb-full-20260824-closure-v2/
```

Its manifest was read as-is. The manifest SHA-256 observed for this acceptance
was `b52483d76f92102a5cb8ece3340de8aa0630c0a0e1f6dbed04bd28f60922b29d` and
reported 16,437 input / 16,437 accounted records, all `parsed`, with zero
`blocked_integrity` records. Batch 5A did not enumerate or project those
16,437 records.

For the two real samples below, a temporary two-entry copy of the complete
Parsed manifest was created under `data/parsed/.batch5a-representative-temporary/`.
The Canonical pipeline wrote only beneath that temporary directory, the output
was checked, then the directory was removed in the same execution. No
`data/canonical/` corpus was created.

## Selected cases

| Case | Evidence kind | Why selected | Observed Canonical result |
| --- | --- | --- | --- |
| ordinary structural detail | checked-in fixture | `tests/fixtures/parsed/obc-detail.json` supplies a normal OBC detail with source metadata, layout, an unknown-component decoded value, rich-text preservation, RawRef, and channel memberships. | Existing focused projector coverage verifies copied Parsed/source identity, ordered section/component/unit projection, Parsed pointers, inherited Raw lineage, `parsed_dependency` channel lineage, and deterministic JSON. |
| zero-unit / unsupported | hand-built fixture | Provides deterministic coverage of the approved zero-unit / unsupported branches independently of top-level closure-v2 detail status. | Focused projector coverage verifies a zero-unit context remains with `unit_count=0` and no artificial unit; unsupported child/module values remain ordered Canonical unsupported units/sections with source position and lineage. |
| blocked integrity with and without RawRef | hand-built fixture | Closure-v2 has zero blocked records, so production data cannot exercise this accepted input variant. | Focused contract/projector/pipeline coverage verifies null `source_identity`, zero sections, original error plus available diagnostics, metadata-owned content ID/channels, `parsed_dependency` membership lineage, and RawRef 0..N without fabrication. |
| large / structurally complex detail | real Parsed closure-v2 record `501157` (`阿蕾奇诺`) | Already-identifiable Phase 01 representative character sample; 3,555,206 serialized Parsed bytes, 29 modules, 29 component contexts, and 519 units. | Complete two-record Canonical run produced 29 sections, 29 contexts, 519 units, 490 rich-text units, and 548 retained context/unit RawRef links. The retained `UNSUPPORTED_COMPONENT` diagnostic did not cause loss. |
| dialogue with multiple retained representations | real Parsed closure-v2 record `509653` (`影域的遗留`) | Already-identifiable Phase 01 representative quest sample; includes `interactive_dialogue`. | Complete two-record Canonical run produced 8 sections, 8 contexts, 20 units, 11 rich-text units, and one dialogue-graph unit. The interactive component retained generic decoded, rich-text, and dialogue-graph representations. `DIALOGUE_MULTIPLE_PARENT` and `UNSUPPORTED_COMPONENT` remained diagnostics; dialogue-node speaker values remained null. |

## Checks and observations

The real two-record pipeline run used the existing `CanonicalRunPipeline`, not
a second projector path. It reported:

```text
status                         complete
input_record_count             2
accounted_record_count         2
input_integrity_failure_count  0
canonical                      2
canonical_with_anomalies       0
blocked_integrity              0
reproject_count                2
reuse_count                    0
```

For each real record, the acceptance check verified:

- exact serialized Parsed identity and the valid normal-detail source identity;
- one Canonical section per serialized Parsed module and one component context
  per serialized Parsed component;
- component source position and serialized-Parsed lineage pointers;
- child-unit accounting; available RawRefs; and `parsed_dependency` channel
  membership lineage with no attached detail RawRef;
- canonical record SHA-256 against the run-manifest entry;
- deterministic repeated execution with the same Canonical run ID.

Both real records projected with `canonical` status. This is consistent with
the current contract: retained non-blocking diagnostics are evidence and do
not by themselves require `canonical_with_anomalies` when the Parsed record
status is `parsed`. This note does not reclassify those diagnostics.

## Fixture coverage used

The focused Canonical tests used as deterministic fixture coverage for relevant
branches include:

- `test_normal_fixture_projects_ordered_module_component_units_and_lineage`;
- `test_zero_unit_component_is_accounted_without_artificial_unit`;
- `test_unsupported_component_becomes_accounted_unsupported_unit`;
- `test_unsupported_module_is_structurally_accounted_and_keeps_order`;
- `test_batch3_preserves_generic_rich_text_and_dialogue_graph_relationships`;
- `test_batch3_preserves_non_object_dialogue_anomaly_without_blocking_projection`;
- the four `blocked_integrity` contract cases in
  `tests/canonical/test_contracts.py`;
- `test_blocked_parsed_observation_is_projected_without_synthesized_source_identity`.

## Limits retained for Batch 5B and later work

This evidence is a deliberately small acceptance sample. It does not prove
corpus-wide structural closure, upstream semantic completeness,
official-game-text coverage, semantic entity correctness, cross-snapshot
identity, or Retrieval/RAG quality. The separate Batch 5B reviewed streaming
16,437-record Parsed-to-Canonical gate remains required and was not run.

Cross-Parsed-run and cross-snapshot semantic reuse, Canonical semantic/logical
identity, stable child identity, and the long-term semantic significance of
fine-grained lineage/source-position changes to content fingerprints remain
UNKNOWN / DEFERRED.
