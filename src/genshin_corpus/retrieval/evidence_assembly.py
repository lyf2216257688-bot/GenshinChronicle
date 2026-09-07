"""Candidate-neutral, deterministic Evidence Assembly for Retrieval Units.

Ranking belongs upstream.  This module accepts already ranked candidates and
uses Canonical containment, ordinal continuity, fragment coordinates, observed
dialogue edges, and (only under the explicit A1-2-2 policy) mechanically proved
exact dialogue-source-occurrence aliases to produce a provider-neutral Evidence
Packet.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from time import perf_counter
from typing import Any
from types import MappingProxyType
from weakref import WeakKeyDictionary
import json

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write

from .retrieval_units import RETRIEVAL_UNIT_SCHEMA_VERSION, RetrievalUnitError, load_retrieval_units


EVIDENCE_PACKET_SCHEMA_VERSION = "phase04-evidence-packet-0.1"
EVIDENCE_ASSEMBLY_VERSION = "phase04-rag-w1-deterministic-assembly-0.1"
V2_DIRECT_FIRST_CONTEXT_CAP_POLICY = "phase04-rag-a1-2-direct-first-context-cap-0.1"
CANDIDATE_ANCHORED_SHADOW_POLICY = "phase04-rag-a1-3-candidate-anchored-shadow-0.1"
DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY = "phase04-rag-a1-3-deferred-footprint-charge-shadow-0.1"
A1_2_2_EXACT_DIALOGUE_SOURCE_OCCURRENCE_ALIAS_SUPPRESSION_POLICY = (
    "phase04-rag-a1-2-2-exact-dialogue-source-occurrence-alias-suppression-0.1"
)


class EvidenceAssemblyError(ValueError):
    """Raised when candidates or Retrieval Units cannot be assembled safely."""


@dataclass(frozen=True)
class EvidenceAssemblyConfig:
    """Tunable deterministic context/budget operating point, not architecture constants."""

    neighbor_before: int = 1
    neighbor_after: int = 1
    structured_neighbor_before: int = 0
    structured_neighbor_after: int = 0
    dialogue_hops: int = 1
    max_evidence_blocks: int = 8
    per_block_chars: int = 3000
    total_context_chars: int = 12000

    def __post_init__(self) -> None:
        for name in (
            "neighbor_before",
            "neighbor_after",
            "structured_neighbor_before",
            "structured_neighbor_after",
            "dialogue_hops",
            "max_evidence_blocks",
            "per_block_chars",
            "total_context_chars",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.max_evidence_blocks == 0 or self.per_block_chars == 0 or self.total_context_chars == 0:
            raise ValueError("Evidence block and context budgets must be positive")

    def to_dict(self) -> dict[str, int]:
        return {
            "neighbor_before": self.neighbor_before,
            "neighbor_after": self.neighbor_after,
            "structured_neighbor_before": self.structured_neighbor_before,
            "structured_neighbor_after": self.structured_neighbor_after,
            "dialogue_hops": self.dialogue_hops,
            "max_evidence_blocks": self.max_evidence_blocks,
            "per_block_chars": self.per_block_chars,
            "total_context_chars": self.total_context_chars,
        }


@dataclass(frozen=True)
class EvidenceSelectionPolicy:
    """Versioned final-admission policy, independent from v1 configuration.

    The challengers currently expose only approved direct-first policies.
    Keeping this object separate ensures that ``EvidenceAssemblyConfig`` and
    its v1 packet identity retain their legacy meanings.
    """

    identity: str
    context_only_block_cap: int

    def __post_init__(self) -> None:
        if self.identity not in {
            V2_DIRECT_FIRST_CONTEXT_CAP_POLICY,
            A1_2_2_EXACT_DIALOGUE_SOURCE_OCCURRENCE_ALIAS_SUPPRESSION_POLICY,
        }:
            raise ValueError("unsupported Evidence Selection policy identity")
        if (
            not isinstance(self.context_only_block_cap, int)
            or isinstance(self.context_only_block_cap, bool)
            or self.context_only_block_cap <= 0
        ):
            raise ValueError("context_only_block_cap must be a positive integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "context_only_block_cap": self.context_only_block_cap,
        }


V2_DIRECT_FIRST_CONTEXT_CAP = EvidenceSelectionPolicy(
    identity=V2_DIRECT_FIRST_CONTEXT_CAP_POLICY,
    context_only_block_cap=8,
)

A1_2_2_EXACT_DIALOGUE_SOURCE_OCCURRENCE_ALIAS_SUPPRESSION = EvidenceSelectionPolicy(
    identity=A1_2_2_EXACT_DIALOGUE_SOURCE_OCCURRENCE_ALIAS_SUPPRESSION_POLICY,
    context_only_block_cap=8,
)


@dataclass
class EvidenceAssemblyDiagnostics:
    """Execution-only timings and selection trace; never part of Packet identity."""

    preparation_seconds: dict[str, float]
    assembly_seconds: dict[str, float]
    serialization_seconds: dict[str, float]
    selection_trace: dict[str, Any] | None = None

    def __init__(self) -> None:
        self.preparation_seconds = {}
        self.assembly_seconds = {}
        self.serialization_seconds = {}
        self.selection_trace = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "phase04-rag-a1-assembly-diagnostics-0.1",
            "preparation_seconds": dict(sorted(self.preparation_seconds.items())),
            "assembly_seconds": dict(sorted(self.assembly_seconds.items())),
            "serialization_seconds": dict(sorted(self.serialization_seconds.items())),
            "selection_trace": self.selection_trace,
        }


@dataclass(frozen=True)
class _PreparedAssemblyState:
    """Private mutable-data holder for one prepared, verified RU snapshot."""

    units_by_id: Mapping[str, Mapping[str, Any]]
    rich_by_context_ordinal: Mapping[tuple[Any, ...], Mapping[str, Any]]
    structured_by_canonical_unit: Mapping[tuple[Any, ...], tuple[Mapping[str, Any], ...]]
    structured_positions: Mapping[str, int]
    fragment_chains: Mapping[tuple[Any, ...], tuple[Mapping[str, Any], ...]]
    fragment_positions: Mapping[str, int]
    dialogue_nodes: Mapping[tuple[Any, ...], tuple[Mapping[str, Any], ...]]
    dialogue_neighbors: Mapping[tuple[Any, ...], Mapping[str, tuple[str, ...]]]
    canonical_input_json: bytes
    retrieval_unit_schema_version: Any


@dataclass(frozen=True, slots=True, weakref_slot=True, eq=False)
class PreparedAssemblyContext:
    """Opaque handle for one fully verified RU snapshot and its lookups.

    The large RU rows and structural indexes remain module-private.  This
    avoids a deep copy of the corpus while preventing callers from changing a
    prepared snapshot through the public context object.
    """

    retrieval_unit_manifest_path: Path
    retrieval_unit_build_identity: str

    @property
    def build_manifest(self) -> Mapping[str, Any]:
        """Return a detached, minimal build projection for compatibility."""

        state = _prepared_state(self)
        return MappingProxyType({
            "build_identity": self.retrieval_unit_build_identity,
            "canonical_input": json.loads(state.canonical_input_json),
            "retrieval_unit_schema_version": state.retrieval_unit_schema_version,
        })


_PREPARED_ASSEMBLY_STATES: WeakKeyDictionary[PreparedAssemblyContext, _PreparedAssemblyState] = WeakKeyDictionary()


def _prepared_state(context: PreparedAssemblyContext) -> _PreparedAssemblyState:
    try:
        return _PREPARED_ASSEMBLY_STATES[context]
    except KeyError as exc:
        raise EvidenceAssemblyError("Prepared Assembly context is not a verified snapshot") from exc


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EvidenceAssemblyError(f"{label} must be an object")
    return value


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _source_order(unit: Mapping[str, Any]) -> tuple[int, ...]:
    value = unit.get("source_order")
    if not isinstance(value, list) or not value or any(not _is_int(item) for item in value):
        raise EvidenceAssemblyError("Retrieval Unit source_order must be a non-empty integer list")
    return tuple(value)


def _address(unit: Mapping[str, Any]) -> Mapping[str, Any]:
    source = _mapping(unit.get("source"), "Retrieval Unit source")
    address = _mapping(source.get("canonical_address"), "Retrieval Unit canonical address")
    required = (
        "source_identity_key",
        "record_id",
        "section_ordinal",
        "component_observation_key",
        "component_ordinal",
        "canonical_unit_ordinal",
        "canonical_unit_kind",
        "parsed_json_pointer",
    )
    for key in required:
        if key not in address:
            raise EvidenceAssemblyError(f"Retrieval Unit canonical address lacks {key}")
    return address


def _base_address(unit: Mapping[str, Any], *, include_unit_ordinal: bool = True) -> tuple[Any, ...]:
    address = _address(unit)
    fields = [
        address["source_identity_key"],
        address["record_id"],
        address["section_ordinal"],
        address["component_observation_key"],
        address["component_ordinal"],
    ]
    if include_unit_ordinal:
        fields.extend((address["canonical_unit_ordinal"], address["parsed_json_pointer"]))
    return tuple(fields)


def _nested(unit: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(unit.get("nested_selector"), "Retrieval Unit nested selector")


def _fragment(unit: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(unit.get("fragment_selector"), "Retrieval Unit fragment selector")


def _dialogue_locator(unit: Mapping[str, Any]) -> tuple[Any, ...] | None:
    if unit.get("content_type") != "dialogue_node":
        return None
    dialogue = _mapping(_mapping(unit.get("structure"), "Retrieval Unit structure").get("dialogue"), "Retrieval Unit dialogue structure")
    return (*_base_address(unit), dialogue.get("group_ordering"), dialogue.get("node_source_id"))


def _fragment_index(unit: Mapping[str, Any]) -> int:
    value = _fragment(unit).get("fragment_index", 0)
    return value if _is_int(value) else 0


def _is_same_fragment_chain(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return (
        left.get("content_type") == right.get("content_type")
        and _base_address(left) == _base_address(right)
        and dict(_nested(left)) == dict(_nested(right))
    )


def _fragment_adjacent(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if not _is_same_fragment_chain(left, right):
        return False
    first = _fragment(left)
    second = _fragment(right)
    if first.get("kind") in {"unicode_codepoint_range", "oversized_scalar_line_range"}:
        return (
            first.get("kind") == second.get("kind")
            and first.get("fragment_count") == second.get("fragment_count")
            and _fragment_index(right) == _fragment_index(left) + 1
        )
    return False


def _rich_ordinal_adjacent(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if left.get("content_type") != "rich_text" or right.get("content_type") != "rich_text":
        return False
    if _base_address(left, include_unit_ordinal=False) != _base_address(right, include_unit_ordinal=False):
        return False
    left_address = _address(left)
    right_address = _address(right)
    if right_address["canonical_unit_ordinal"] != left_address["canonical_unit_ordinal"] + 1:
        return False
    left_fragment = _fragment(left)
    right_fragment = _fragment(right)
    return (
        (left_fragment.get("kind") == "whole" or left_fragment.get("fragment_index") == left_fragment.get("fragment_count", 1) - 1)
        and (right_fragment.get("kind") == "whole" or right_fragment.get("fragment_index") == 0)
    )


def _structured_line_adjacent(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if left.get("content_type") != "structured" or right.get("content_type") != "structured":
        return False
    if _base_address(left) != _base_address(right):
        return False
    first = _fragment(left)
    second = _fragment(right)

    def scalar_range(fragment: Mapping[str, Any]) -> tuple[int, int] | None:
        if fragment.get("kind") == "packed_scalar_lines":
            start, end = fragment.get("scalar_line_start"), fragment.get("scalar_line_end")
            return (start, end) if _is_int(start) and _is_int(end) else None
        if fragment.get("kind") == "oversized_scalar_line_range":
            index = fragment.get("scalar_line_index")
            return (index, index) if _is_int(index) else None
        return None

    left_range, right_range = scalar_range(first), scalar_range(second)
    if left_range is None or right_range is None:
        return False
    if left_range == right_range:
        return _fragment_adjacent(left, right)
    return left_range[1] + 1 == right_range[0]


def _mergeable(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return _fragment_adjacent(left, right) or _rich_ordinal_adjacent(left, right) or _structured_line_adjacent(left, right)


def _unit_public(unit: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping(unit["source"], "Retrieval Unit source")
    return {
        "unit_id": unit["unit_id"],
        "content_type": unit["content_type"],
        "text": unit["retrieval_visible_text"],
        "canonical_address": dict(_address(unit)),
        "lineage": dict(_mapping(source.get("lineage"), "Retrieval Unit lineage")),
        "record_context": dict(_mapping(source.get("record_context"), "Retrieval Unit record context")),
        "provenance": dict(_mapping(source.get("provenance"), "Retrieval Unit provenance")),
        "content_role": dict(_mapping(source.get("content_role"), "Retrieval Unit content role")),
        "nested_selector": dict(_nested(unit)),
        "fragment_selector": dict(_fragment(unit)),
        "structure": dict(_mapping(unit.get("structure"), "Retrieval Unit structure")),
        "source_order": list(_source_order(unit)),
    }


def _candidate_rows(
    candidates: Sequence[Mapping[str, Any]],
    units_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    selected: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    for index, raw_candidate in enumerate(candidates):
        candidate = _mapping(raw_candidate, f"candidate {index}")
        unit_id = candidate.get("unit_id")
        rank = candidate.get("rank")
        if not isinstance(unit_id, str) or unit_id not in units_by_id:
            raise EvidenceAssemblyError(f"candidate {index} references an unknown Retrieval Unit")
        if not _is_int(rank) or rank <= 0:
            raise EvidenceAssemblyError(f"candidate {index}.rank must be a positive integer")
        retrieval = candidate.get("retrieval", {})
        if not isinstance(retrieval, Mapping):
            raise EvidenceAssemblyError(f"candidate {index}.retrieval must be an object")
        row = {"unit_id": unit_id, "rank": rank, "input_index": index, "retrieval": dict(retrieval)}
        previous = selected.get(unit_id)
        if previous is None or (row["rank"], row["input_index"]) < (previous["rank"], previous["input_index"]):
            if previous is not None:
                rejected.append({"unit_id": unit_id, "rank": previous["rank"], "input_index": previous["input_index"], "reason": "duplicate_source_occurrence"})
            selected[unit_id] = row
        else:
            rejected.append({"unit_id": unit_id, "rank": rank, "input_index": index, "reason": "duplicate_source_occurrence"})
    trace = []
    selected_indexes = {row["input_index"] for row in selected.values()}
    rejected_indexes = {row["input_index"] for row in rejected}
    for index, raw_candidate in enumerate(candidates):
        candidate = _mapping(raw_candidate, f"candidate {index}")
        trace.append({
            "input_index": index,
            "unit_id": candidate["unit_id"],
            "rank": candidate["rank"],
            "outcome": "direct_candidate" if index in selected_indexes else "duplicate_source_occurrence",
        })
    if selected_indexes | rejected_indexes != set(range(len(candidates))):
        raise EvidenceAssemblyError("candidate accounting is incomplete")
    return selected, rejected, trace


def _append_reason(selection: dict[str, dict[str, Any]], unit_id: str, reason: Mapping[str, Any]) -> None:
    current = selection.get(unit_id)
    if current is None:
        selection[unit_id] = {"retrieval": None, "reasons": [dict(reason)]}
    elif dict(reason) not in current["reasons"]:
        current["reasons"].append(dict(reason))


def prepare_evidence_assembly_context(
    retrieval_unit_manifest_path: Path,
    *,
    diagnostics: EvidenceAssemblyDiagnostics | None = None,
) -> PreparedAssemblyContext:
    """Load and validate one RU snapshot, then build query-independent lookups."""

    started = perf_counter()
    load_started = perf_counter()
    build_manifest, units = load_retrieval_units(
        retrieval_unit_manifest_path,
        timings=None if diagnostics is None else diagnostics.preparation_seconds,
    )
    if diagnostics is not None:
        diagnostics.preparation_seconds["load_and_validate"] = perf_counter() - load_started
    index_started = perf_counter()
    units_by_id: dict[str, Mapping[str, Any]] = {}
    rich_by_context_ordinal: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    structured_by_canonical_unit: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    fragment_chains: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    dialogue_nodes: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    dialogue_edges: dict[tuple[Any, ...], set[tuple[str, str]]] = defaultdict(set)
    for unit in units:
        unit_id = unit.get("unit_id")
        if not isinstance(unit_id, str) or unit_id in units_by_id:
            raise EvidenceAssemblyError("Retrieval Unit artifact has duplicate or invalid unit_id")
        if unit.get("schema_version") != RETRIEVAL_UNIT_SCHEMA_VERSION:
            raise EvidenceAssemblyError("Retrieval Unit artifact schema is unsupported")
        units_by_id[unit_id] = unit
        if unit.get("content_type") == "rich_text" and _fragment(unit).get("kind") == "whole":
            address = _address(unit)
            rich_by_context_ordinal[(*_base_address(unit, include_unit_ordinal=False), address["canonical_unit_ordinal"])] = unit
        fragment_chains[(unit.get("content_type"), *_base_address(unit), canonical_json_bytes(dict(_nested(unit))))].append(unit)
        if unit.get("content_type") == "structured":
            structured_by_canonical_unit[_base_address(unit)].append(unit)
        locator = _dialogue_locator(unit)
        if locator is not None:
            dialogue_nodes[locator].append(unit)
            dialogue = _mapping(_mapping(unit.get("structure"), "Retrieval Unit structure").get("dialogue"), "Retrieval Unit dialogue structure")
            group_base = locator[:-1]
            for edge in dialogue.get("observed_edges", []):
                if isinstance(edge, Mapping) and isinstance(edge.get("parent_id"), str) and isinstance(edge.get("child_id"), str):
                    dialogue_edges[group_base].add((edge["parent_id"], edge["child_id"]))
    fragment_positions: dict[str, int] = {}
    for values in fragment_chains.values():
        values.sort(key=lambda item: (_fragment_index(item), _source_order(item), str(item["unit_id"])))
        for position, unit in enumerate(values):
            fragment_positions[str(unit["unit_id"])] = position
    structured_positions: dict[str, int] = {}
    for values in structured_by_canonical_unit.values():
        values.sort(key=lambda item: (_source_order(item), str(item["unit_id"])))
        for position, unit in enumerate(values):
            structured_positions[str(unit["unit_id"])] = position
    for values in dialogue_nodes.values():
        values.sort(key=lambda item: (_source_order(item), str(item["unit_id"])))
    dialogue_neighbors: dict[tuple[Any, ...], dict[str, tuple[str, ...]]] = {}
    for group_base, edges in dialogue_edges.items():
        neighbors: dict[str, set[str]] = defaultdict(set)
        for parent_id, child_id in edges:
            neighbors[parent_id].add(child_id)
            neighbors[child_id].add(parent_id)
        dialogue_neighbors[group_base] = {node_id: tuple(sorted(values)) for node_id, values in neighbors.items()}
    if diagnostics is not None:
        diagnostics.preparation_seconds["structural_index"] = perf_counter() - index_started
        diagnostics.preparation_seconds["total"] = perf_counter() - started
    build_identity = build_manifest.get("build_identity")
    if not isinstance(build_identity, str) or not build_identity:
        raise EvidenceAssemblyError("Retrieval Unit build manifest lacks build_identity")
    canonical_input_json = canonical_json_bytes(
        dict(_mapping(build_manifest.get("canonical_input"), "Retrieval Unit build canonical input"))
    )
    context = PreparedAssemblyContext(
        retrieval_unit_manifest_path=Path(retrieval_unit_manifest_path).resolve(),
        retrieval_unit_build_identity=build_identity,
    )
    _PREPARED_ASSEMBLY_STATES[context] = _PreparedAssemblyState(
        units_by_id=MappingProxyType(units_by_id),
        rich_by_context_ordinal=MappingProxyType(rich_by_context_ordinal),
        structured_by_canonical_unit=MappingProxyType({key: tuple(values) for key, values in structured_by_canonical_unit.items()}),
        structured_positions=MappingProxyType(structured_positions),
        fragment_chains=MappingProxyType({key: tuple(values) for key, values in fragment_chains.items()}),
        fragment_positions=MappingProxyType(fragment_positions),
        dialogue_nodes=MappingProxyType({key: tuple(values) for key, values in dialogue_nodes.items()}),
        dialogue_neighbors=MappingProxyType({key: MappingProxyType(value) for key, value in dialogue_neighbors.items()}),
        canonical_input_json=canonical_input_json,
        retrieval_unit_schema_version=build_manifest.get("retrieval_unit_schema_version"),
    )
    return context


def _expand_context(
    *,
    direct: Mapping[str, Mapping[str, Any]],
    state: _PreparedAssemblyState,
    config: EvidenceAssemblyConfig,
) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}

    for unit_id, candidate in sorted(direct.items(), key=lambda item: (item[1]["rank"], item[1]["input_index"], item[0])):
        _append_reason(selected, unit_id, {"kind": "retrieved", "rank": candidate["rank"], "input_index": candidate["input_index"]})
        selected[unit_id]["retrieval"] = dict(candidate)

    # Structural neighbor expansion starts from direct candidates only, avoiding recursive drift.
    for unit_id, candidate in direct.items():
        unit = state.units_by_id[unit_id]
        content_type = unit.get("content_type")
        before = config.structured_neighbor_before if content_type == "structured" else config.neighbor_before
        after = config.structured_neighbor_after if content_type == "structured" else config.neighbor_after
        chain_key = (content_type, *_base_address(unit), canonical_json_bytes(dict(_nested(unit))))
        chain = state.fragment_chains.get(chain_key, ())
        if chain and (before or after):
            position = state.fragment_positions[unit_id]
            for related in chain[max(0, position - before):position]:
                _append_reason(selected, str(related["unit_id"]), {"kind": "fragment_neighbor", "from_unit_id": unit_id})
            for related in chain[position + 1:position + 1 + after]:
                _append_reason(selected, str(related["unit_id"]), {"kind": "fragment_neighbor", "from_unit_id": unit_id})
        if content_type == "structured" and (before or after):
            chain = state.structured_by_canonical_unit.get(_base_address(unit), ())
            if chain:
                position = state.structured_positions[unit_id]
                for related in chain[max(0, position - before):position]:
                    _append_reason(selected, str(related["unit_id"]), {"kind": "structured_neighbor", "from_unit_id": unit_id})
                for related in chain[position + 1:position + 1 + after]:
                    _append_reason(selected, str(related["unit_id"]), {"kind": "structured_neighbor", "from_unit_id": unit_id})
        # A fragmented leaf may extend only through its explicit fragment chain.
        # Ordinal siblings are meaningful only when the selected leaf is whole;
        # otherwise a bounded expansion could jump beyond unselected fragments.
        if content_type == "rich_text" and _fragment(unit).get("kind") == "whole" and (before or after):
            address = _address(unit)
            for ordinal in range(int(address["canonical_unit_ordinal"]) - before, int(address["canonical_unit_ordinal"]) + after + 1):
                if ordinal < 0 or ordinal == address["canonical_unit_ordinal"]:
                    continue
                related = state.rich_by_context_ordinal.get((*_base_address(unit, include_unit_ordinal=False), ordinal))
                if related is not None:
                    _append_reason(selected, str(related["unit_id"]), {"kind": "ordinal_neighbor", "from_unit_id": unit_id})
        locator = _dialogue_locator(unit)
        if locator is not None and config.dialogue_hops:
            seen = {locator[-1]}
            frontier = {locator[-1]}
            group_base = locator[:-1]
            for hop in range(1, config.dialogue_hops + 1):
                next_nodes: set[str] = set()
                neighbors = state.dialogue_neighbors.get(group_base, {})
                for node_id in frontier:
                    next_nodes.update(item for item in neighbors.get(node_id, ()) if item not in seen)
                for node_id in sorted(next_nodes):
                    related_locator = (*group_base, node_id)
                    for related in state.dialogue_nodes.get(related_locator, ()):
                        _append_reason(selected, str(related["unit_id"]), {"kind": "observed_dialogue_edge", "from_unit_id": unit_id, "hop": hop})
                seen.update(next_nodes)
                frontier = next_nodes
                if not frontier:
                    break
    return selected


_ALIAS_RAW_IDENTITY_FIELDS = (
    "source",
    "locale",
    "run_id",
    "artifact_kind",
    "artifact_path",
    "artifact_sha256",
    "content_id",
    "json_pointer",
)


def _whole_fragment(unit: Mapping[str, Any]) -> bool:
    return dict(_fragment(unit)) == {"kind": "whole"}


def _raw_ref(value: Any) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    if any(not isinstance(value.get(key), str) or not value[key] for key in _ALIAS_RAW_IDENTITY_FIELDS):
        return None
    pointer = value.get("embedded_json_pointer")
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        return None
    return value


def _pointer_terminal(pointer: str) -> str | None:
    if not pointer.startswith("/"):
        return None
    token = pointer.rsplit("/", 1)[1]
    output = ""
    index = 0
    while index < len(token):
        char = token[index]
        if char != "~":
            output += char
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            return None
        output += "~" if token[index + 1] == "0" else "/"
        index += 2
    return output


def _dialogue_source_occurrence_alias_proof(
    rich: Mapping[str, Any],
    dialogue: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return a narrow Raw-occurrence proof for one rich/dialogue alias."""

    try:
        if rich.get("content_type") != "rich_text" or dialogue.get("content_type") != "dialogue_node":
            return None
        if rich.get("retrieval_visible_text") != dialogue.get("retrieval_visible_text"):
            return None
        if not _whole_fragment(rich) or not _whole_fragment(dialogue):
            return None
        rich_source, dialogue_source = _mapping(rich.get("source"), "Retrieval Unit source"), _mapping(dialogue.get("source"), "Retrieval Unit source")
        rich_address, dialogue_address = _address(rich), _address(dialogue)
        for key in ("source_identity_key", "record_id", "section_ordinal", "component_observation_key", "component_ordinal"):
            if rich_address.get(key) != dialogue_address.get(key):
                return None
        rich_context = _mapping(rich_source.get("record_context"), "Retrieval Unit record context")
        dialogue_context = _mapping(dialogue_source.get("record_context"), "Retrieval Unit record context")
        if rich_context.get("source_component_id") != "interactive_dialogue" or dialogue_context.get("source_component_id") != "interactive_dialogue":
            return None
        rich_lineage = _mapping(rich_source.get("lineage"), "Retrieval Unit lineage")
        raw_refs = rich_lineage.get("raw_refs")
        if rich_lineage.get("evidence_scope") != "direct_raw" or not isinstance(raw_refs, list) or len(raw_refs) != 1:
            return None
        rich_raw = _raw_ref(raw_refs[0])
        dialogue_structure = _mapping(dialogue.get("structure"), "Retrieval Unit structure")
        dialogue_detail = _mapping(dialogue_structure.get("dialogue"), "Retrieval Unit dialogue structure")
        dialogue_raw = _raw_ref(dialogue_detail.get("node_raw_ref"))
        if rich_raw is None or dialogue_raw is None:
            return None
        if any(rich_raw[key] != dialogue_raw[key] for key in _ALIAS_RAW_IDENTITY_FIELDS):
            return None
        rich_value_sha = rich_raw.get("source_value_sha256")
        dialogue_value_sha = dialogue_raw.get("source_value_sha256")
        for value in (rich_value_sha, dialogue_value_sha):
            if value not in (None, "") and not isinstance(value, str):
                return None
        if rich_value_sha not in (None, "") or dialogue_value_sha not in (None, ""):
            if rich_value_sha != dialogue_value_sha:
                return None
        dialogue_pointer = dialogue_raw["embedded_json_pointer"]
        if rich_raw["embedded_json_pointer"] != f"{dialogue_pointer}/dialogue":
            return None
        node_id = _nested(dialogue).get("node_source_id")
        if not isinstance(node_id, str) or not node_id or _pointer_terminal(dialogue_pointer) != node_id:
            return None
        return {
            "kind": "exact_dialogue_source_occurrence_alias",
            "text_sha256": sha256(str(rich["retrieval_visible_text"]).encode("utf-8")).hexdigest(),
            "raw_identity": {key: rich_raw[key] for key in _ALIAS_RAW_IDENTITY_FIELDS},
            "dialogue_node_raw_embedded_json_pointer": dialogue_pointer,
            "rich_raw_embedded_json_pointer": rich_raw["embedded_json_pointer"],
            "node_source_id": node_id,
        }
    except (EvidenceAssemblyError, KeyError, TypeError):
        return None


def _alias_representative_key(unit_id: str, selection: Mapping[str, Mapping[str, Any]], units_by_id: Mapping[str, Mapping[str, Any]]) -> tuple[Any, ...]:
    retrieval = selection[unit_id].get("retrieval")
    direct_key = (0, int(retrieval["rank"]), int(retrieval["input_index"])) if isinstance(retrieval, Mapping) else (1, 2**63, 2**63)
    return (*direct_key, _source_order(units_by_id[unit_id]), unit_id)


def _suppress_dialogue_source_occurrence_aliases(
    selection: dict[str, dict[str, Any]],
    units_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Remove only proved rich/dialogue aliases after all structural expansion."""

    rich_ids = sorted((unit_id for unit_id in selection if units_by_id[unit_id].get("content_type") == "rich_text"))
    dialogue_ids = sorted((unit_id for unit_id in selection if units_by_id[unit_id].get("content_type") == "dialogue_node"))
    edges: list[tuple[str, str, dict[str, Any]]] = []
    for rich_id in rich_ids:
        for dialogue_id in dialogue_ids:
            proof = _dialogue_source_occurrence_alias_proof(units_by_id[rich_id], units_by_id[dialogue_id])
            if proof is not None:
                edges.append((rich_id, dialogue_id, proof))
    if not edges:
        return []
    participants = {unit_id for edge in edges for unit_id in edge[:2]}
    groups: list[set[str]] = []
    while participants:
        group = {participants.pop()}
        changed = True
        while changed:
            changed = False
            for left, right, _ in edges:
                if (left in group) ^ (right in group):
                    group.update((left, right))
                    participants.discard(left)
                    participants.discard(right)
                    changed = True
        groups.append(group)
    trace: list[dict[str, Any]] = []
    for group in groups:
        if len(group) != 2:
            continue
        left, right = sorted(group)
        proof = next(item for first, second, item in edges if {first, second} == {left, right})
        representative, suppressed = sorted(group, key=lambda unit_id: _alias_representative_key(unit_id, selection, units_by_id))
        trace.append({
            "suppressed_unit_id": suppressed,
            "representative_unit_id": representative,
            "proof": proof,
        })
        del selection[suppressed]
    return sorted(trace, key=canonical_json_bytes)


def _block_members(selection: Mapping[str, Mapping[str, Any]], units_by_id: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    values = [units_by_id[unit_id] for unit_id in selection]
    values.sort(key=lambda item: (_source_order(item), str(item["unit_id"])))
    blocks: list[dict[str, Any]] = []
    members: list[Mapping[str, Any]] = []
    for unit in values:
        if members and not _mergeable(members[-1], unit):
            blocks.append(_finish_block(members, selection))
            members = []
        members.append(unit)
    if members:
        blocks.append(_finish_block(members, selection))
    return blocks


def _finish_block(members: list[Mapping[str, Any]], selection: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    member_rows = []
    direct_candidates: list[tuple[int, int, str]] = []
    for member in members:
        selection_info = selection[str(member["unit_id"])]
        retrieval = selection_info.get("retrieval")
        if isinstance(retrieval, Mapping):
            direct_candidates.append((int(retrieval["rank"]), int(retrieval["input_index"]), str(member["unit_id"])))
        public = _unit_public(member)
        public["assembly_reasons"] = sorted(selection_info["reasons"], key=canonical_json_bytes)
        public["retrieval"] = None if retrieval is None else {
            "rank": retrieval["rank"],
            "input_index": retrieval["input_index"],
            "metadata": dict(retrieval["retrieval"]),
        }
        member_rows.append(public)
    text_parts = [member_rows[0]["text"]]
    for left, right, right_row in zip(members, members[1:], member_rows[1:]):
        if _fragment_adjacent(left, right):
            separator = ""
        elif _structured_line_adjacent(left, right):
            separator = "\n"
        else:
            separator = "\n\n"
        text_parts.extend((separator, right_row["text"]))
    text = "".join(text_parts)
    direct_candidates.sort()
    return {
        "members": member_rows,
        "text": text,
        "char_count": len(text),
        "source_order": member_rows[0]["source_order"],
        "priority": direct_candidates[0][0] if direct_candidates else None,
        "direct_candidate_order": direct_candidates,
    }


def _select_blocks(blocks: list[dict[str, Any]], config: EvidenceAssemblyConfig) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ranked = sorted(blocks, key=lambda block: (block["priority"] is None, block["priority"] if block["priority"] is not None else 2**63, tuple(block["source_order"])))
    accepted: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    used_chars = 0
    for block in ranked:
        member_ids = [member["unit_id"] for member in block["members"]]
        if block["char_count"] > config.per_block_chars:
            omitted.append({"unit_ids": member_ids, "reason": "per_block_char_limit", "char_count": block["char_count"]})
        elif len(accepted) >= config.max_evidence_blocks:
            omitted.append({"unit_ids": member_ids, "reason": "max_evidence_blocks", "char_count": block["char_count"]})
        elif used_chars + block["char_count"] > config.total_context_chars:
            omitted.append({"unit_ids": member_ids, "reason": "total_context_char_budget", "char_count": block["char_count"]})
        else:
            accepted.append(block)
            used_chars += block["char_count"]
    accepted.sort(key=lambda block: tuple(block["source_order"]))
    return accepted, omitted


def _block_unit_ids(block: Mapping[str, Any]) -> list[str]:
    return [str(member["unit_id"]) for member in block["members"]]


def _v2_direct_admission_key(block: Mapping[str, Any]) -> tuple[Any, ...]:
    direct_candidates = block["direct_candidate_order"]
    if not direct_candidates:
        raise EvidenceAssemblyError("direct-containing block lacks a direct candidate order")
    rank, input_index, _ = direct_candidates[0]
    return (rank, input_index, tuple(block["source_order"]), tuple(_block_unit_ids(block)))


def _v2_context_admission_key(block: Mapping[str, Any]) -> tuple[Any, ...]:
    return (tuple(block["source_order"]), tuple(_block_unit_ids(block)))


def _select_blocks_v2(
    blocks: list[dict[str, Any]],
    config: EvidenceAssemblyConfig,
    policy: EvidenceSelectionPolicy,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Admit direct-containing blocks before bounded context-only blocks.

    Selection remains structural and deterministic.  A char-budget conflict is
    recorded as an operating-point omission; validation failures continue to
    raise before this function is reached.
    """

    direct_blocks = sorted(
        (block for block in blocks if block["priority"] is not None),
        key=_v2_direct_admission_key,
    )
    context_blocks = sorted(
        (block for block in blocks if block["priority"] is None),
        key=_v2_context_admission_key,
    )
    accepted: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    admission_order: list[dict[str, Any]] = []
    used_chars = 0
    accepted_direct = 0
    accepted_context = 0

    for block in direct_blocks:
        unit_ids = _block_unit_ids(block)
        key = _v2_direct_admission_key(block)
        if block["char_count"] > config.per_block_chars:
            reason = "per_block_char_limit"
        elif used_chars + block["char_count"] > config.total_context_chars:
            reason = "direct_total_context_char_budget_conflict"
        else:
            accepted.append(block)
            accepted_direct += 1
            used_chars += block["char_count"]
            admission_order.append({"block_kind": "direct_containing", "unit_ids": unit_ids, "admission_key": list(key), "outcome": "admitted"})
            continue
        omitted.append({"unit_ids": unit_ids, "reason": reason, "char_count": block["char_count"], "block_kind": "direct_containing"})
        admission_order.append({"block_kind": "direct_containing", "unit_ids": unit_ids, "admission_key": list(key), "outcome": "omitted", "reason": reason})

    for block in context_blocks:
        unit_ids = _block_unit_ids(block)
        key = _v2_context_admission_key(block)
        if block["char_count"] > config.per_block_chars:
            reason = "per_block_char_limit"
        elif accepted_context >= policy.context_only_block_cap:
            reason = "context_only_block_cap"
        elif used_chars + block["char_count"] > config.total_context_chars:
            reason = "context_total_context_char_budget"
        else:
            accepted.append(block)
            accepted_context += 1
            used_chars += block["char_count"]
            admission_order.append({"block_kind": "context_only", "unit_ids": unit_ids, "admission_key": list(key), "outcome": "admitted"})
            continue
        omitted.append({"unit_ids": unit_ids, "reason": reason, "char_count": block["char_count"], "block_kind": "context_only"})
        admission_order.append({"block_kind": "context_only", "unit_ids": unit_ids, "admission_key": list(key), "outcome": "omitted", "reason": reason})

    accepted.sort(key=lambda block: (tuple(block["source_order"]), tuple(_block_unit_ids(block))))
    return accepted, omitted, {
        "direct_containing_block_count": len(direct_blocks),
        "context_only_block_count": len(context_blocks),
        "selected_direct_containing_blocks": accepted_direct,
        "selected_context_only_blocks": accepted_context,
        "admission_order": admission_order,
    }


def _v2_packet_config(config: EvidenceAssemblyConfig) -> dict[str, Any]:
    """Expose only configuration fields used by the v2 policy.

    In particular, the v1 total-block cap is intentionally absent: v2's 8 is
    the policy's context-only cap, not a generic evidence-block limit.
    """

    return {
        "neighbor_before": config.neighbor_before,
        "neighbor_after": config.neighbor_after,
        "structured_neighbor_before": config.structured_neighbor_before,
        "structured_neighbor_after": config.structured_neighbor_after,
        "dialogue_hops": config.dialogue_hops,
        "per_block_chars": config.per_block_chars,
        "total_context_chars": config.total_context_chars,
    }


def _shadow_anchor_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    return (int(candidate["rank"]), int(candidate["input_index"]), str(candidate["unit_id"]))


def _shadow_block_parts(
    block: Mapping[str, Any],
    *,
    allowed_unit_ids: set[str],
    units_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return non-duplicated structural subblocks without inventing adjacency."""

    original_members = list(block["members"])
    parts: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    for public_member in original_members:
        unit_id = str(public_member["unit_id"])
        if unit_id not in allowed_unit_ids:
            if current:
                parts.append(current)
                current = []
            continue
        unit = units_by_id[unit_id]
        if current and not _mergeable(current[-1], unit):
            parts.append(current)
            current = []
        current.append(unit)
    if current:
        parts.append(current)

    public_by_id = {str(member["unit_id"]): member for member in original_members}
    output: list[dict[str, Any]] = []
    for members in parts:
        member_rows = [dict(public_by_id[str(unit["unit_id"])]) for unit in members]
        text_parts = [member_rows[0]["text"]]
        for left, right, right_row in zip(members, members[1:], member_rows[1:]):
            if _fragment_adjacent(left, right):
                separator = ""
            elif _structured_line_adjacent(left, right):
                separator = "\n"
            else:
                separator = "\n\n"
            text_parts.extend((separator, right_row["text"]))
        text = "".join(text_parts)
        output.append({
            "members": member_rows,
            "text": text,
            "char_count": len(text),
            "source_order": member_rows[0]["source_order"],
        })
    return output


def _candidate_anchored_shadow_plan(
    direct: Mapping[str, Mapping[str, Any]],
    state: _PreparedAssemblyState,
    config: EvidenceAssemblyConfig,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Build immutable per-direct-candidate footprints from verified structure."""

    anchors: list[dict[str, Any]] = []
    memberships: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for unit_id, candidate in sorted(direct.items(), key=lambda item: _shadow_anchor_key(item[1])):
        local_selection = _expand_context(direct={unit_id: candidate}, state=state, config=config)
        local_blocks = _block_members(local_selection, state.units_by_id)
        direct_blocks = [
            block for block in local_blocks
            if unit_id in _block_unit_ids(block)
        ]
        if len(direct_blocks) != 1:
            raise EvidenceAssemblyError("candidate-anchored shadow requires exactly one direct root block")
        direct_block = direct_blocks[0]
        context_blocks = [block for block in local_blocks if block is not direct_block]
        anchor_key = _shadow_anchor_key(candidate)
        anchor = {
            "anchor_unit_id": unit_id,
            "anchor_key": anchor_key,
            "candidate": dict(candidate),
            "direct_block": direct_block,
            "context_blocks": context_blocks,
            "immutable_direct_footprint": {
                "unit_ids": _block_unit_ids(direct_block),
                "char_count": direct_block["char_count"],
            },
        }
        anchors.append(anchor)
        for selected_id, selection_info in sorted(local_selection.items()):
            memberships[selected_id].append({
                "anchor_unit_id": unit_id,
                "anchor_key": list(anchor_key),
                "membership_kind": "direct_root" if selected_id == unit_id else "context",
                "relations": sorted(selection_info["reasons"], key=canonical_json_bytes),
            })
    return anchors, dict(memberships)


def _annotate_shadow_members(
    blocks: Sequence[Mapping[str, Any]],
    memberships: Mapping[str, Sequence[Mapping[str, Any]]],
    rendered_by: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for block in blocks:
        members = []
        for original in block["members"]:
            member = dict(original)
            unit_id = str(member["unit_id"])
            member["shadow_anchor_memberships"] = [dict(item) for item in memberships[unit_id]]
            member["shadow_rendering"] = dict(rendered_by[unit_id])
            members.append(member)
        output.append({
            "members": members,
            "text": block["text"],
            "char_count": block["char_count"],
            "source_order": list(block["source_order"]),
        })
    return output


def assemble_candidate_anchored_shadow_packet(
    retrieval_unit_manifest_path: Path,
    ranked_candidates: Sequence[Mapping[str, Any]],
    *,
    config: EvidenceAssemblyConfig | None = None,
    retrieval_audit: Mapping[str, Any] | None = None,
    prepared_context: PreparedAssemblyContext | None = None,
    diagnostics: EvidenceAssemblyDiagnostics | None = None,
) -> dict[str, Any]:
    """Assemble a shadow Packet with candidate-anchored, immutable footprints.

    This is deliberately separate from the accepted v1 and A1-2-1 v2 paths.
    It keeps v2's direct-first then bounded-context admission order while
    preventing a newly supplied lower-priority candidate from changing a
    pre-existing anchor's structural footprint or priority.
    """

    config = config or EvidenceAssemblyConfig()
    if prepared_context is None:
        prepared_context = prepare_evidence_assembly_context(retrieval_unit_manifest_path, diagnostics=diagnostics)
    elif prepared_context.retrieval_unit_manifest_path != Path(retrieval_unit_manifest_path).resolve():
        raise EvidenceAssemblyError("Prepared Assembly context does not match the requested Retrieval Unit manifest")
    state = _prepared_state(prepared_context)
    candidate_started = perf_counter()
    direct, deduplicated, candidate_trace = _candidate_rows(ranked_candidates, state.units_by_id)
    candidate_observations = [
        {
            "input_index": index,
            "candidate": dict(_mapping(candidate, f"candidate {index}")),
            "outcome": candidate_trace[index]["outcome"],
        }
        for index, candidate in enumerate(ranked_candidates)
    ]
    if diagnostics is not None:
        diagnostics.assembly_seconds["candidate_normalization"] = perf_counter() - candidate_started
    planning_started = perf_counter()
    anchors, memberships = _candidate_anchored_shadow_plan(direct, state, config)
    if diagnostics is not None:
        diagnostics.assembly_seconds["candidate_anchored_footprints"] = perf_counter() - planning_started

    membership_owner = {
        unit_id: min(rows, key=lambda row: tuple(row["anchor_key"]))["anchor_unit_id"]
        for unit_id, rows in memberships.items()
    }
    rendered_by: dict[str, dict[str, Any]] = {}
    selected_blocks: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    anchor_outcomes: list[dict[str, Any]] = []
    admission_order: list[dict[str, Any]] = []
    selected_anchor_ids: list[str] = []
    used_chars = 0

    # The direct phase retains the accepted v2 ordering.  Each direct block is
    # constructed before admission, so later anchors cannot mutate its cost.
    for anchor in anchors:
        root_id = anchor["anchor_unit_id"]
        block = anchor["direct_block"]
        all_ids = _block_unit_ids(block)
        newly_rendered = set(all_ids) - set(rendered_by)
        rendered_root_before_admission = root_id in rendered_by
        parts = _shadow_block_parts(block, allowed_unit_ids=newly_rendered, units_by_id=state.units_by_id)
        marginal_chars = sum(part["char_count"] for part in parts)
        before = used_chars
        outcome: str
        reason: str | None = None
        if block["char_count"] > config.per_block_chars:
            outcome, reason = "omitted", "per_block_char_limit"
        elif rendered_root_before_admission:
            outcome = "already_visible_via_higher_priority_anchor"
        elif used_chars + marginal_chars > config.total_context_chars:
            outcome, reason = "omitted", "direct_total_context_char_budget_conflict"
        else:
            outcome = "admitted"
            selected_anchor_ids.append(root_id)
            used_chars += marginal_chars
            for part in parts:
                for member in part["members"]:
                    rendered_by[str(member["unit_id"])] = {
                        "phase": "direct",
                        "anchor_unit_id": root_id,
                        "budget_charge_block": f"direct:{root_id}",
                    }
                selected_blocks.append({**part, "shadow_phase": "direct", "shadow_anchor_unit_id": root_id})
        anchor_outcome = {
            "anchor_unit_id": root_id,
            "anchor_key": list(anchor["anchor_key"]),
            "immutable_direct_footprint": dict(anchor["immutable_direct_footprint"]),
            "marginal_direct_chars": marginal_chars,
            "budget_before": before,
            "budget_after": used_chars,
            "outcome": outcome,
            "root_visible_before_admission": rendered_root_before_admission,
        }
        if reason is not None:
            anchor_outcome["reason"] = reason
            anchor_outcome["displaced_by_anchor_ids"] = list(selected_anchor_ids)
            omitted.append({
                "unit_ids": all_ids,
                "reason": reason,
                "char_count": block["char_count"],
                "block_kind": "direct_containing",
                "anchor_unit_id": root_id,
                "budget_before": before,
                "required_marginal_chars": marginal_chars,
                "displaced_by_anchor_ids": list(selected_anchor_ids),
            })
        anchor_outcomes.append(anchor_outcome)
        admission_order.append(dict(anchor_outcome))

    # Context remains optional and follows the existing v2 cap.  Direct roots
    # are never reintroduced as context after their direct disposition.
    context_candidates: list[tuple[tuple[Any, ...], str, dict[str, Any]]] = []
    direct_ids = set(direct)
    for anchor in anchors:
        for block in anchor["context_blocks"]:
            allowed = {
                unit_id for unit_id in _block_unit_ids(block)
                if unit_id not in direct_ids and membership_owner.get(unit_id) == anchor["anchor_unit_id"]
            }
            for part in _shadow_block_parts(block, allowed_unit_ids=allowed, units_by_id=state.units_by_id):
                context_candidates.append((
                    (tuple(part["source_order"]), tuple(_block_unit_ids(part)), tuple(anchor["anchor_key"])),
                    anchor["anchor_unit_id"],
                    part,
                ))
    selected_context = 0
    for _, owner_id, block in sorted(context_candidates, key=lambda item: item[0]):
        unit_ids = _block_unit_ids(block)
        newly_rendered = set(unit_ids) - set(rendered_by)
        if not newly_rendered:
            continue
        parts = _shadow_block_parts(block, allowed_unit_ids=newly_rendered, units_by_id=state.units_by_id)
        marginal_chars = sum(part["char_count"] for part in parts)
        before = used_chars
        if block["char_count"] > config.per_block_chars:
            reason = "per_block_char_limit"
        elif selected_context >= V2_DIRECT_FIRST_CONTEXT_CAP.context_only_block_cap:
            reason = "context_only_block_cap"
        elif used_chars + marginal_chars > config.total_context_chars:
            reason = "context_total_context_char_budget"
        else:
            selected_context += 1
            used_chars += marginal_chars
            for part in parts:
                for member in part["members"]:
                    rendered_by[str(member["unit_id"])] = {
                        "phase": "context",
                        "anchor_unit_id": owner_id,
                        "budget_charge_block": f"context:{owner_id}",
                    }
                selected_blocks.append({**part, "shadow_phase": "context", "shadow_anchor_unit_id": owner_id})
            admission_order.append({
                "block_kind": "context_only",
                "anchor_unit_id": owner_id,
                "unit_ids": unit_ids,
                "budget_before": before,
                "budget_after": used_chars,
                "marginal_chars": marginal_chars,
                "outcome": "admitted",
            })
            continue
        omitted.append({
            "unit_ids": unit_ids,
            "reason": reason,
            "char_count": block["char_count"],
            "block_kind": "context_only",
            "anchor_unit_id": owner_id,
            "budget_before": before,
            "required_marginal_chars": marginal_chars,
        })
        admission_order.append({
            "block_kind": "context_only",
            "anchor_unit_id": owner_id,
            "unit_ids": unit_ids,
            "budget_before": before,
            "outcome": "omitted",
            "reason": reason,
        })

    selected_blocks.sort(key=lambda block: (tuple(block["source_order"]), tuple(_block_unit_ids(block))))
    public_blocks = _annotate_shadow_members(selected_blocks, memberships, rendered_by)
    evidence = [
        {
            "evidence_id": f"E{index:02d}",
            "text": block["text"],
            "char_count": block["char_count"],
            "source_order": block["source_order"],
            "members": block["members"],
        }
        for index, block in enumerate(public_blocks, 1)
    ]
    build_manifest = prepared_context.build_manifest
    occurrence_audit = []
    for unit_id in sorted(memberships):
        occurrence_audit.append({
            "unit_id": unit_id,
            "presentation_owner_anchor_unit_id": membership_owner[unit_id],
            "memberships": [dict(item) for item in memberships[unit_id]],
            "rendering": rendered_by.get(unit_id, {"phase": "omitted"}),
        })
    packet = {
        "schema_version": EVIDENCE_PACKET_SCHEMA_VERSION,
        "assembly_version": CANDIDATE_ANCHORED_SHADOW_POLICY,
        "retrieval_unit_build": {
            "build_identity": prepared_context.retrieval_unit_build_identity,
            "canonical_input": dict(_mapping(build_manifest.get("canonical_input"), "Retrieval Unit build canonical input")),
            "retrieval_unit_schema_version": build_manifest.get("retrieval_unit_schema_version"),
        },
        "assembly_config": _v2_packet_config(config),
        "assembly_config_identity": sha256_json({
            "assembly_version": CANDIDATE_ANCHORED_SHADOW_POLICY,
            "config": _v2_packet_config(config),
            "control_selection_policy": V2_DIRECT_FIRST_CONTEXT_CAP.to_dict(),
        }),
        "selection_policy": V2_DIRECT_FIRST_CONTEXT_CAP.to_dict(),
        "selection_policy_identity": sha256_json({
            "assembly_version": CANDIDATE_ANCHORED_SHADOW_POLICY,
            "control_selection_policy": V2_DIRECT_FIRST_CONTEXT_CAP.to_dict(),
            "assembly_config": _v2_packet_config(config),
        }),
        "retrieval_audit": {
            "input_candidate_count": len(ranked_candidates),
            "deduplicated_candidates": deduplicated,
            "direct_candidate_count": len(direct),
            "retrieval_metadata": dict(retrieval_audit or {}),
        },
        "shadow_contract": {
            "identity": CANDIDATE_ANCHORED_SHADOW_POLICY,
            "control_selection_policy": V2_DIRECT_FIRST_CONTEXT_CAP.to_dict(),
            "candidate_observations": candidate_observations,
            "direct_footprints": anchor_outcomes,
            "context_occurrences": occurrence_audit,
        },
        "evidence": evidence,
        "budget": {
            "per_block_chars": config.per_block_chars,
            "total_context_chars": config.total_context_chars,
            "context_only_block_cap": V2_DIRECT_FIRST_CONTEXT_CAP.context_only_block_cap,
            "used_direct_containing_blocks": sum(1 for item in anchor_outcomes if item["outcome"] == "admitted"),
            "used_context_only_blocks": selected_context,
            "used_evidence_blocks": len(evidence),
            "used_context_chars": sum(item["char_count"] for item in evidence),
            "omitted_blocks": omitted,
        },
    }
    if diagnostics is not None:
        diagnostics.selection_trace = {
            "shadow_contract": CANDIDATE_ANCHORED_SHADOW_POLICY,
            "candidate_outcomes": candidate_trace,
            "candidate_observations": candidate_observations,
            "direct_footprints": anchor_outcomes,
            "context_occurrences": occurrence_audit,
            "admission_order": admission_order,
        }
    return packet


def assemble_deferred_footprint_charge_shadow_packet(
    retrieval_unit_manifest_path: Path,
    ranked_candidates: Sequence[Mapping[str, Any]],
    *,
    config: EvidenceAssemblyConfig | None = None,
    retrieval_audit: Mapping[str, Any] | None = None,
    prepared_context: PreparedAssemblyContext | None = None,
    diagnostics: EvidenceAssemblyDiagnostics | None = None,
) -> dict[str, Any]:
    """Assemble a diagnostic Packet with deferred non-root footprint charging.

    This is an A1-3-derived shadow policy.  It admits each direct root as a
    singleton projection, but retains the complete immutable direct footprint
    and its original per-block eligibility check.  Non-root footprint members
    are charged in anchor order before the existing context-only pass.
    """

    config = config or EvidenceAssemblyConfig()
    if prepared_context is None:
        prepared_context = prepare_evidence_assembly_context(retrieval_unit_manifest_path, diagnostics=diagnostics)
    elif prepared_context.retrieval_unit_manifest_path != Path(retrieval_unit_manifest_path).resolve():
        raise EvidenceAssemblyError("Prepared Assembly context does not match the requested Retrieval Unit manifest")
    state = _prepared_state(prepared_context)
    direct, deduplicated, candidate_trace = _candidate_rows(ranked_candidates, state.units_by_id)
    candidate_observations = [
        {
            "input_index": index,
            "candidate": dict(_mapping(candidate, f"candidate {index}")),
            "outcome": candidate_trace[index]["outcome"],
        }
        for index, candidate in enumerate(ranked_candidates)
    ]
    anchors, memberships = _candidate_anchored_shadow_plan(direct, state, config)
    membership_owner = {
        unit_id: min(rows, key=lambda row: tuple(row["anchor_key"]))["anchor_unit_id"]
        for unit_id, rows in memberships.items()
    }

    rendered_by: dict[str, dict[str, Any]] = {}
    selected_blocks: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    admission_order: list[dict[str, Any]] = []
    anchor_outcomes: list[dict[str, Any]] = []
    direct_ids = set(direct)
    direct_admitted_anchor_ids: list[str] = []
    used_chars = 0

    # Pass 1: preserve priority order, but charge only the actual root RU.
    for anchor in anchors:
        root_id = anchor["anchor_unit_id"]
        full_block = anchor["direct_block"]
        full_ids = _block_unit_ids(full_block)
        root_parts = _shadow_block_parts(
            full_block,
            allowed_unit_ids={root_id},
            units_by_id=state.units_by_id,
        )
        if len(root_parts) != 1 or _block_unit_ids(root_parts[0]) != [root_id]:
            raise EvidenceAssemblyError("deferred footprint shadow requires one legal singleton root projection")
        root_part = root_parts[0]
        root_chars = int(root_part["char_count"])
        full_eligible = int(full_block["char_count"]) <= config.per_block_chars
        before = used_chars
        root_visible_before_admission = root_id in rendered_by
        outcome: str
        reason: str | None = None
        if not full_eligible:
            outcome, reason = "omitted", "per_block_char_limit"
        elif root_visible_before_admission:
            outcome = "already_visible_via_higher_priority_anchor"
        elif used_chars + root_chars > config.total_context_chars:
            outcome, reason = "omitted", "direct_total_context_char_budget_conflict"
        else:
            outcome = "admitted"
            direct_admitted_anchor_ids.append(root_id)
            used_chars += root_chars
            for member in root_part["members"]:
                unit_id = str(member["unit_id"])
                rendered_by[unit_id] = {
                    "phase": "direct_root",
                    "anchor_unit_id": root_id,
                    "budget_charge_block": f"direct_root:{root_id}",
                }
            selected_blocks.append({
                **root_part,
                "shadow_phase": "direct_root",
                "shadow_anchor_unit_id": root_id,
            })
        row = {
            "anchor_unit_id": root_id,
            "anchor_key": list(anchor["anchor_key"]),
            "immutable_direct_footprint": dict(anchor["immutable_direct_footprint"]),
            "full_footprint_per_block_eligible": full_eligible,
            "root_projection": {
                "unit_ids": [root_id],
                "char_count": root_chars,
                "source_order": list(root_part["source_order"]),
                "owner_anchor_unit_id": root_id,
                "render_phase": "direct_root" if outcome == "admitted" else "omitted",
            },
            "root_marginal_chars": root_chars if outcome == "admitted" else 0,
            "budget_before": before,
            "budget_after": used_chars,
            "outcome": outcome,
            "root_visible_before_admission": root_visible_before_admission,
            "owner_anchor_unit_id": root_id,
            "render_phase": "direct_root" if outcome == "admitted" else "omitted",
            "reason": reason,
            "displaced_by_anchor_ids": list(direct_admitted_anchor_ids) if reason is not None else [],
            "deferred_footprint_parts": [],
            "deferred_footprint_marginal_chars": 0,
        }
        if reason is not None:
            omitted.append({
                "unit_ids": full_ids,
                "reason": reason,
                "char_count": int(full_block["char_count"]),
                "block_kind": "direct_containing",
                "anchor_unit_id": root_id,
                "budget_before": before,
                "required_marginal_chars": root_chars,
                "displaced_by_anchor_ids": list(direct_admitted_anchor_ids),
            })
        anchor_outcomes.append(row)
        admission_order.append({
            "block_kind": "direct_root",
            "anchor_unit_id": root_id,
            "unit_ids": [root_id],
            "full_footprint_unit_ids": full_ids,
            "budget_before": before,
            "budget_after": used_chars,
            "marginal_chars": row["root_marginal_chars"],
            "outcome": outcome,
            **({"reason": reason} if reason is not None else {}),
        })

    # Pass 2: charge non-root members of each immutable footprint in anchor order.
    deferred_ids: set[str] = set()
    anchor_by_id = {anchor["anchor_unit_id"]: anchor for anchor in anchors}
    for row in anchor_outcomes:
        anchor = anchor_by_id[row["anchor_unit_id"]]
        full_ids = set(_block_unit_ids(anchor["direct_block"]))
        # Direct candidacy does not cancel a RU's membership in an earlier
        # immutable footprint.  Duplicate rendering/charging is handled by
        # rendered_by, so later direct roots become already-visible when the
        # higher-priority anchor admitted the shared occurrence.
        allowed = full_ids - {anchor["anchor_unit_id"]}
        deferred_ids.update(allowed)
        if not row["full_footprint_per_block_eligible"]:
            continue
        for part in _shadow_block_parts(
            anchor["direct_block"],
            allowed_unit_ids=allowed,
            units_by_id=state.units_by_id,
        ):
            newly_rendered = set(_block_unit_ids(part)) - set(rendered_by)
            part_rows = []
            part_outcome = "already_visible_via_higher_priority_anchor"
            part_reason: str | None = None
            before = used_chars
            if newly_rendered:
                part_rows = _shadow_block_parts(
                    part,
                    allowed_unit_ids=newly_rendered,
                    units_by_id=state.units_by_id,
                )
                if any(item["char_count"] > config.per_block_chars for item in part_rows):
                    part_outcome, part_reason = "omitted", "per_block_char_limit"
                elif used_chars + sum(int(item["char_count"]) for item in part_rows) > config.total_context_chars:
                    part_outcome, part_reason = "omitted", "deferred_footprint_total_context_char_budget_conflict"
                else:
                    part_outcome = "admitted"
                    charge = sum(int(item["char_count"]) for item in part_rows)
                    used_chars += charge
                    for item in part_rows:
                        for member in item["members"]:
                            unit_id = str(member["unit_id"])
                            rendered_by[unit_id] = {
                                "phase": "deferred_footprint",
                                "anchor_unit_id": anchor["anchor_unit_id"],
                                "budget_charge_block": f"deferred_footprint:{anchor['anchor_unit_id']}",
                            }
                        selected_blocks.append({
                            **item,
                            "shadow_phase": "deferred_footprint",
                            "shadow_anchor_unit_id": anchor["anchor_unit_id"],
                        })
            charge = used_chars - before
            part_audit = {
                "unit_ids": _block_unit_ids(part),
                "source_order": list(part["source_order"]),
                "char_count": int(part["char_count"]),
                "marginal_chars": charge,
                "budget_before": before,
                "budget_after": used_chars,
                "outcome": part_outcome,
                "owner_anchor_unit_id": anchor["anchor_unit_id"],
                "render_phase": "deferred_footprint" if part_outcome == "admitted" else "omitted",
                "reason": part_reason,
                "displaced_by_anchor_ids": list(direct_admitted_anchor_ids) if part_reason is not None else [],
            }
            if part_reason is not None:
                omitted.append({
                    "unit_ids": _block_unit_ids(part),
                    "reason": part_reason,
                    "char_count": int(part["char_count"]),
                    "block_kind": "deferred_direct_footprint",
                    "anchor_unit_id": anchor["anchor_unit_id"],
                    "budget_before": before,
                    "required_marginal_chars": int(part["char_count"]),
                    "displaced_by_anchor_ids": list(direct_admitted_anchor_ids),
                })
            row["deferred_footprint_parts"].append(part_audit)
            row["deferred_footprint_marginal_chars"] += charge
            admission_order.append({
                "block_kind": "deferred_direct_footprint",
                "anchor_unit_id": anchor["anchor_unit_id"],
                **part_audit,
            })

    # Pass 3: retain the existing context-only admission order/capacity rules.
    context_candidates: list[tuple[tuple[Any, ...], str, dict[str, Any]]] = []
    for anchor in anchors:
        for block in anchor["context_blocks"]:
            allowed = {
                unit_id for unit_id in _block_unit_ids(block)
                if unit_id not in direct_ids
                and unit_id not in deferred_ids
                and membership_owner.get(unit_id) == anchor["anchor_unit_id"]
            }
            for part in _shadow_block_parts(block, allowed_unit_ids=allowed, units_by_id=state.units_by_id):
                context_candidates.append((
                    (tuple(part["source_order"]), tuple(_block_unit_ids(part)), tuple(anchor["anchor_key"])),
                    anchor["anchor_unit_id"],
                    part,
                ))
    selected_context = 0
    for _, owner_id, block in sorted(context_candidates, key=lambda item: item[0]):
        unit_ids = _block_unit_ids(block)
        newly_rendered = set(unit_ids) - set(rendered_by)
        if not newly_rendered:
            continue
        parts = _shadow_block_parts(block, allowed_unit_ids=newly_rendered, units_by_id=state.units_by_id)
        marginal_chars = sum(int(part["char_count"]) for part in parts)
        before = used_chars
        if block["char_count"] > config.per_block_chars:
            reason = "per_block_char_limit"
        elif selected_context >= V2_DIRECT_FIRST_CONTEXT_CAP.context_only_block_cap:
            reason = "context_only_block_cap"
        elif used_chars + marginal_chars > config.total_context_chars:
            reason = "context_total_context_char_budget"
        else:
            selected_context += 1
            used_chars += marginal_chars
            for part in parts:
                for member in part["members"]:
                    unit_id = str(member["unit_id"])
                    rendered_by[unit_id] = {
                        "phase": "context",
                        "anchor_unit_id": owner_id,
                        "budget_charge_block": f"context:{owner_id}",
                    }
                selected_blocks.append({**part, "shadow_phase": "context", "shadow_anchor_unit_id": owner_id})
            admission_order.append({
                "block_kind": "context_only",
                "anchor_unit_id": owner_id,
                "unit_ids": unit_ids,
                "budget_before": before,
                "budget_after": used_chars,
                "marginal_chars": marginal_chars,
                "outcome": "admitted",
            })
            continue
        omitted.append({
            "unit_ids": unit_ids,
            "reason": reason,
            "char_count": int(block["char_count"]),
            "block_kind": "context_only",
            "anchor_unit_id": owner_id,
            "budget_before": before,
            "required_marginal_chars": marginal_chars,
        })
        admission_order.append({
            "block_kind": "context_only",
            "anchor_unit_id": owner_id,
            "unit_ids": unit_ids,
            "budget_before": before,
            "outcome": "omitted",
            "reason": reason,
        })

    selected_blocks.sort(key=lambda block: (tuple(block["source_order"]), tuple(_block_unit_ids(block))))
    public_blocks = _annotate_shadow_members(selected_blocks, memberships, rendered_by)
    evidence = [
        {
            "evidence_id": f"E{index:02d}",
            "text": block["text"],
            "char_count": block["char_count"],
            "source_order": block["source_order"],
            "members": block["members"],
        }
        for index, block in enumerate(public_blocks, 1)
    ]
    build_manifest = prepared_context.build_manifest
    occurrence_audit = [
        {
            "unit_id": unit_id,
            "presentation_owner_anchor_unit_id": membership_owner[unit_id],
            "memberships": [dict(item) for item in memberships[unit_id]],
            "rendering": rendered_by.get(unit_id, {"phase": "omitted"}),
        }
        for unit_id in sorted(memberships)
    ]
    packet = {
        "schema_version": EVIDENCE_PACKET_SCHEMA_VERSION,
        "assembly_version": DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY,
        "retrieval_unit_build": {
            "build_identity": prepared_context.retrieval_unit_build_identity,
            "canonical_input": dict(_mapping(build_manifest.get("canonical_input"), "Retrieval Unit build canonical input")),
            "retrieval_unit_schema_version": build_manifest.get("retrieval_unit_schema_version"),
        },
        "assembly_config": _v2_packet_config(config),
        "assembly_config_identity": sha256_json({
            "assembly_version": DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY,
            "config": _v2_packet_config(config),
            "control_selection_policy": V2_DIRECT_FIRST_CONTEXT_CAP.to_dict(),
        }),
        "selection_policy": V2_DIRECT_FIRST_CONTEXT_CAP.to_dict(),
        "selection_policy_identity": sha256_json({
            "assembly_version": DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY,
            "control_selection_policy": V2_DIRECT_FIRST_CONTEXT_CAP.to_dict(),
            "assembly_config": _v2_packet_config(config),
        }),
        "retrieval_audit": {
            "input_candidate_count": len(ranked_candidates),
            "deduplicated_candidates": deduplicated,
            "direct_candidate_count": len(direct),
            "retrieval_metadata": dict(retrieval_audit or {}),
        },
        "shadow_contract": {
            "identity": DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY,
            "control_selection_policy": V2_DIRECT_FIRST_CONTEXT_CAP.to_dict(),
            "candidate_observations": candidate_observations,
            "direct_footprints": anchor_outcomes,
            "context_occurrences": occurrence_audit,
        },
        "evidence": evidence,
        "budget": {
            "per_block_chars": config.per_block_chars,
            "total_context_chars": config.total_context_chars,
            "context_only_block_cap": V2_DIRECT_FIRST_CONTEXT_CAP.context_only_block_cap,
            "used_direct_containing_blocks": sum(1 for row in anchor_outcomes if row["outcome"] == "admitted"),
            "used_context_only_blocks": selected_context,
            "used_evidence_blocks": len(evidence),
            "used_context_chars": sum(int(item["char_count"]) for item in evidence),
            "omitted_blocks": omitted,
        },
    }
    if diagnostics is not None:
        diagnostics.selection_trace = {
            "shadow_contract": DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY,
            "candidate_outcomes": candidate_trace,
            "candidate_observations": candidate_observations,
            "direct_footprints": anchor_outcomes,
            "context_occurrences": occurrence_audit,
            "admission_order": admission_order,
        }
    return packet


def assemble_evidence_packet(
    retrieval_unit_manifest_path: Path,
    ranked_candidates: Sequence[Mapping[str, Any]],
    *,
    config: EvidenceAssemblyConfig | None = None,
    retrieval_audit: Mapping[str, Any] | None = None,
    prepared_context: PreparedAssemblyContext | None = None,
    diagnostics: EvidenceAssemblyDiagnostics | None = None,
    selection_policy: EvidenceSelectionPolicy | None = None,
) -> dict[str, Any]:
    """Assemble one deterministic provider-neutral Evidence Packet.

    ``ranked_candidates`` must be supplied by an upstream retrieval mode. v1
    and the existing A1-2-1 v2 policy perform no text-identity selection.
    A1-2-2 uses byte-exact text only inside its mechanically proved dialogue
    Raw-occurrence alias check; no similarity, normalization, semantic
    selection, re-ranking, or model decision is performed.
    ``selection_policy=None`` retains the byte-compatible v1 selector.
    """

    config = config or EvidenceAssemblyConfig()
    if prepared_context is None:
        prepared_context = prepare_evidence_assembly_context(retrieval_unit_manifest_path, diagnostics=diagnostics)
    elif prepared_context.retrieval_unit_manifest_path != Path(retrieval_unit_manifest_path).resolve():
        raise EvidenceAssemblyError("Prepared Assembly context does not match the requested Retrieval Unit manifest")
    state = _prepared_state(prepared_context)
    candidate_started = perf_counter()
    direct, deduplicated, candidate_trace = _candidate_rows(ranked_candidates, state.units_by_id)
    if diagnostics is not None:
        diagnostics.assembly_seconds["candidate_normalization"] = perf_counter() - candidate_started
    expansion_started = perf_counter()
    selection = _expand_context(direct=direct, state=state, config=config)
    if diagnostics is not None:
        diagnostics.assembly_seconds["context_expansion"] = perf_counter() - expansion_started
    alias_suppressions: list[dict[str, Any]] = []
    if selection_policy is not None and selection_policy.identity == A1_2_2_EXACT_DIALOGUE_SOURCE_OCCURRENCE_ALIAS_SUPPRESSION_POLICY:
        alias_started = perf_counter()
        alias_suppressions = _suppress_dialogue_source_occurrence_aliases(selection, state.units_by_id)
        if diagnostics is not None:
            diagnostics.assembly_seconds["dialogue_source_occurrence_alias_suppression"] = perf_counter() - alias_started
    block_started = perf_counter()
    blocks = _block_members(selection, state.units_by_id)
    if diagnostics is not None:
        diagnostics.assembly_seconds["block_construction"] = perf_counter() - block_started
    selection_started = perf_counter()
    v2_selection: dict[str, Any] | None = None
    if selection_policy is None:
        blocks, omitted = _select_blocks(blocks, config)
    else:
        blocks, omitted, v2_selection = _select_blocks_v2(blocks, config, selection_policy)
    if diagnostics is not None:
        diagnostics.assembly_seconds["block_selection"] = perf_counter() - selection_started
    evidence = []
    for index, block in enumerate(blocks, 1):
        evidence.append({
            "evidence_id": f"E{index:02d}",
            "text": block["text"],
            "char_count": block["char_count"],
            "source_order": block["source_order"],
            "members": block["members"],
        })
    audit = dict(retrieval_audit or {})
    build_manifest = prepared_context.build_manifest
    packet = {
        "schema_version": EVIDENCE_PACKET_SCHEMA_VERSION,
        "assembly_version": EVIDENCE_ASSEMBLY_VERSION,
        "retrieval_unit_build": {
            "build_identity": prepared_context.retrieval_unit_build_identity,
            "canonical_input": dict(_mapping(build_manifest.get("canonical_input"), "Retrieval Unit build canonical input")),
            "retrieval_unit_schema_version": build_manifest.get("retrieval_unit_schema_version"),
        },
        "assembly_config": config.to_dict(),
        "assembly_config_identity": sha256_json({"assembly_version": EVIDENCE_ASSEMBLY_VERSION, "config": config.to_dict()}),
        "retrieval_audit": {
            "input_candidate_count": len(ranked_candidates),
            "deduplicated_candidates": deduplicated,
            "direct_candidate_count": len(direct),
            "retrieval_metadata": audit,
        },
        "evidence": evidence,
        "budget": {
            "max_evidence_blocks": config.max_evidence_blocks,
            "per_block_chars": config.per_block_chars,
            "total_context_chars": config.total_context_chars,
            "used_evidence_blocks": len(evidence),
            "used_context_chars": sum(item["char_count"] for item in evidence),
            "omitted_blocks": omitted,
        },
    }
    if selection_policy is not None:
        if v2_selection is None:
            raise EvidenceAssemblyError("v2 selection accounting is absent")
        policy_dict = selection_policy.to_dict()
        packet["selection_policy"] = policy_dict
        packet["selection_policy_identity"] = sha256_json({
            "assembly_version": EVIDENCE_ASSEMBLY_VERSION,
            "selection_policy": policy_dict,
            "assembly_config": _v2_packet_config(config),
        })
        packet["assembly_config"] = _v2_packet_config(config)
        packet["assembly_config_identity"] = sha256_json({
            "assembly_version": EVIDENCE_ASSEMBLY_VERSION,
            "config": _v2_packet_config(config),
            "selection_policy": policy_dict,
        })
        packet["budget"] = {
            "per_block_chars": config.per_block_chars,
            "total_context_chars": config.total_context_chars,
            "context_only_block_cap": selection_policy.context_only_block_cap,
            "used_direct_containing_blocks": v2_selection["selected_direct_containing_blocks"],
            "used_context_only_blocks": v2_selection["selected_context_only_blocks"],
            "used_evidence_blocks": len(evidence),
            "used_context_chars": sum(item["char_count"] for item in evidence),
            "omitted_blocks": omitted,
        }
    if diagnostics is not None:
        omitted_by_unit = {
            unit_id: item["reason"]
            for item in omitted
            for unit_id in item["unit_ids"]
        }
        diagnostics.selection_trace = {
            "candidate_outcomes": candidate_trace,
            "selected_units": [
                {
                    "unit_id": unit_id,
                    "reasons": sorted(value["reasons"], key=canonical_json_bytes),
                    "outcome": omitted_by_unit.get(unit_id, "included"),
                }
                for unit_id, value in sorted(selection.items())
            ],
        }
        if selection_policy is not None:
            if v2_selection is None:
                raise EvidenceAssemblyError("v2 selection accounting is absent")
            candidate_counts: dict[str, int] = {
                "input": len(ranked_candidates),
                "direct": len(direct),
                "context": sum(1 for value in selection.values() if value["retrieval"] is None),
            }
            if selection_policy.identity == A1_2_2_EXACT_DIALOGUE_SOURCE_OCCURRENCE_ALIAS_SUPPRESSION_POLICY:
                candidate_counts["post_suppression_direct"] = sum(
                    1 for value in selection.values() if value["retrieval"] is not None
                )
                candidate_counts["suppressed_alias_count"] = len(alias_suppressions)
            diagnostics.selection_trace.update({
                "selection_policy": selection_policy.to_dict(),
                "candidate_counts": candidate_counts,
                "block_counts": {
                    "direct_containing": v2_selection["direct_containing_block_count"],
                    "context_only": v2_selection["context_only_block_count"],
                    "selected_direct_containing": v2_selection["selected_direct_containing_blocks"],
                    "selected_context_only": v2_selection["selected_context_only_blocks"],
                },
                "admission_order": v2_selection["admission_order"],
            })
            if selection_policy.identity == A1_2_2_EXACT_DIALOGUE_SOURCE_OCCURRENCE_ALIAS_SUPPRESSION_POLICY:
                diagnostics.selection_trace["suppressed_aliases"] = alias_suppressions
    return packet


def evidence_packet_json_bytes(packet: Mapping[str, Any]) -> bytes:
    """Return deterministic JSON packet bytes with no clocks, paths, or UUIDs."""

    return canonical_json_bytes(dict(packet))


def render_evidence_packet(
    packet: Mapping[str, Any],
    *,
    diagnostics: EvidenceAssemblyDiagnostics | None = None,
) -> tuple[bytes, bytes]:
    """Render Packet bytes while optionally recording non-identity timing."""

    json_started = perf_counter()
    json_body = evidence_packet_json_bytes(packet)
    if diagnostics is not None:
        diagnostics.serialization_seconds["json"] = perf_counter() - json_started
    markdown_started = perf_counter()
    markdown_body = evidence_packet_markdown(packet).encode("utf-8")
    if diagnostics is not None:
        diagnostics.serialization_seconds["markdown"] = perf_counter() - markdown_started
    return json_body, markdown_body


def evidence_packet_markdown(packet: Mapping[str, Any]) -> str:
    """Render deterministic, human-readable Markdown without altering packet data."""

    value = _mapping(packet, "Evidence Packet")
    lines = ["# Evidence Packet", ""]
    for evidence in value.get("evidence", []):
        item = _mapping(evidence, "Evidence Packet evidence")
        lines.extend((f"## [{item['evidence_id']}]", "", str(item["text"]), ""))
        for member in item.get("members", []):
            row = _mapping(member, "Evidence Packet member")
            address = _mapping(row.get("canonical_address"), "Evidence Packet canonical address")
            lines.append(
                f"- `{row['unit_id']}` — {address['source_identity_key']} / section {address['section_ordinal']} / "
                f"component {address['component_observation_key']} / unit {address['canonical_unit_ordinal']}"
            )
        lines.append("")
    omitted = _mapping(value.get("budget"), "Evidence Packet budget").get("omitted_blocks", [])
    if omitted:
        lines.extend(("## Omitted by budget", ""))
        for item in omitted:
            row = _mapping(item, "Evidence Packet omission")
            lines.append(f"- {row['reason']}: {', '.join(str(unit_id) for unit_id in row['unit_ids'])}")
        lines.append("")
    return "\n".join(lines)


def write_evidence_packet(
    output_root: Path,
    packet: Mapping[str, Any],
    *,
    diagnostics: EvidenceAssemblyDiagnostics | None = None,
) -> dict[str, Any]:
    """Persist JSON/Markdown packet bytes conflict-safely and deterministically."""

    output_root = Path(output_root)
    json_body, markdown_body = render_evidence_packet(packet, diagnostics=diagnostics)
    paths = {
        "json": output_root / "evidence_packet.json",
        "markdown": output_root / "evidence_packet.md",
    }
    for path, body in ((paths["json"], json_body), (paths["markdown"], markdown_body)):
        if path.exists() and path.read_bytes() != body:
            raise FileExistsError(f"Evidence Packet artifact already exists with different bytes: {path}")
    for path, body in ((paths["json"], json_body), (paths["markdown"], markdown_body)):
        if not path.exists():
            atomic_write(path, body)
    return {
        "json": {"path": "evidence_packet.json", "sha256": sha256(json_body).hexdigest(), "byte_count": len(json_body)},
        "markdown": {"path": "evidence_packet.md", "sha256": sha256(markdown_body).hexdigest(), "byte_count": len(markdown_body)},
    }
