# P04-W1 Retrieval Benchmark Contract

Schema version: `phase04-benchmark-0.1`.

The benchmark is an evaluation artifact, not a Canonical semantic layer. Its
main track is product-weighted for lore, narrative, character, and
worldbuilding questions. Diagnostic slices expose capabilities and failure
modes without fixing a query count or category distribution.

Each query has a track, one or more slices, an optional positive
`product_weight`, and evidence annotations. Evidence
is `direct`, `supporting`, or `hard_negative`; a query also lists one or more
primary sufficient evidence sets and may list known alternatives. This permits
simple exact-name annotations while allowing structured, graph, and
multi-evidence questions to require their actual context.

`benchmark-v0.json` uses `product_weight` only as a provisional seed
prioritization hint. Its final scale, distribution, and scoring semantics are
UNKNOWN and are not frozen by this contract or by the seed values.

An optional `assembly_expectations` object records query-specific context needs
without requiring all queries to use it. An evidence location always identifies an existing `record_id` and may add a
Section ordinal, ComponentContext observation key, Unit ordinal, Parsed JSON
pointer, decoded JSON pointer, matching RawRef fields, and dialogue
group/node/edge coordinates. These are observation-scoped Canonical addresses,
not semantic or cross-snapshot IDs.

Every supplied location coordinate, lineage selector, or dialogue selector
must resolve against the addressed Canonical evidence or the annotation is
rejected. `component_observation_key` and `unit_ordinal` require a Section;
decoded pointers and dialogue selectors require a Unit. Parsed pointers and
RawRef selectors resolve against the deepest explicitly addressed existing
Canonical scope (Record, Section, ComponentContext, or Unit). When both a
dialogue node and edge are supplied, the node must participate in that edge in
the same resolved group. `hard_negative` evidence is never permitted in a
primary or alternative sufficient evidence set.

Location, dialogue, and dialogue-edge selector objects accept only their
documented fields; unknown selectors are rejected. An empty decoded JSON
Pointer addresses the Unit's decoded root and resolves only when that root is
present and non-null. Structural ordinals are non-negative integers, never
booleans.

`benchmark-v0.json` is a deliberately small schema/resolver seed. It is not an
exhaustive corpus benchmark, answer key, role taxonomy, or technology choice.
