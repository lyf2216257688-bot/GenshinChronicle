"""Candidate-neutral, deterministic Evidence Assembly for Retrieval Units.

Ranking belongs upstream.  This module accepts already ranked candidates and
uses only Canonical containment, ordinal continuity, fragment coordinates, and
observed dialogue edges to produce a provider-neutral Evidence Packet.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write

from .retrieval_units import RETRIEVAL_UNIT_SCHEMA_VERSION, RetrievalUnitError, load_retrieval_units


EVIDENCE_PACKET_SCHEMA_VERSION = "phase04-evidence-packet-0.1"
EVIDENCE_ASSEMBLY_VERSION = "phase04-rag-w1-deterministic-assembly-0.1"


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


def _candidate_rows(candidates: Sequence[Mapping[str, Any]], units_by_id: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
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
    return selected, rejected


def _append_reason(selection: dict[str, dict[str, Any]], unit_id: str, reason: Mapping[str, Any]) -> None:
    current = selection.get(unit_id)
    if current is None:
        selection[unit_id] = {"retrieval": None, "reasons": [dict(reason)]}
    elif dict(reason) not in current["reasons"]:
        current["reasons"].append(dict(reason))


def _expand_context(
    *,
    direct: Mapping[str, Mapping[str, Any]],
    units_by_id: Mapping[str, Mapping[str, Any]],
    config: EvidenceAssemblyConfig,
) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    rich_by_context_ordinal: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    structured_by_canonical_unit: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    fragment_chains: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    dialogue_nodes: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    dialogue_edges: dict[tuple[Any, ...], list[tuple[str, str]]] = defaultdict(list)
    for unit in units_by_id.values():
        if unit.get("content_type") == "rich_text" and _fragment(unit).get("kind") == "whole":
            address = _address(unit)
            rich_by_context_ordinal[(*_base_address(unit, include_unit_ordinal=False), address["canonical_unit_ordinal"])] = unit
        fragment_chains[(unit.get("content_type"), *_base_address(unit), canonical_json_bytes(dict(_nested(unit))))].append(unit)
        if unit.get("content_type") == "structured":
            structured_by_canonical_unit[_base_address(unit)].append(unit)
        locator = _dialogue_locator(unit)
        if locator is not None:
            dialogue_nodes[locator].append(unit)
            structure = _mapping(unit.get("structure"), "Retrieval Unit structure")
            dialogue = _mapping(structure.get("dialogue"), "Retrieval Unit dialogue structure")
            group_base = locator[:-1]
            for edge in dialogue.get("observed_edges", []):
                if isinstance(edge, Mapping) and isinstance(edge.get("parent_id"), str) and isinstance(edge.get("child_id"), str):
                    pair = (edge["parent_id"], edge["child_id"])
                    if pair not in dialogue_edges[group_base]:
                        dialogue_edges[group_base].append(pair)
    for values in fragment_chains.values():
        values.sort(key=lambda item: (_fragment_index(item), _source_order(item), str(item["unit_id"])))
    for values in structured_by_canonical_unit.values():
        values.sort(key=lambda item: (_source_order(item), str(item["unit_id"])))
    for values in dialogue_nodes.values():
        values.sort(key=lambda item: (_source_order(item), str(item["unit_id"])))
    for key in dialogue_edges:
        dialogue_edges[key].sort()

    for unit_id, candidate in sorted(direct.items(), key=lambda item: (item[1]["rank"], item[1]["input_index"], item[0])):
        _append_reason(selected, unit_id, {"kind": "retrieved", "rank": candidate["rank"], "input_index": candidate["input_index"]})
        selected[unit_id]["retrieval"] = dict(candidate)

    # Structural neighbor expansion starts from direct candidates only, avoiding recursive drift.
    for unit_id, candidate in direct.items():
        unit = units_by_id[unit_id]
        content_type = unit.get("content_type")
        before = config.structured_neighbor_before if content_type == "structured" else config.neighbor_before
        after = config.structured_neighbor_after if content_type == "structured" else config.neighbor_after
        chain_key = (content_type, *_base_address(unit), canonical_json_bytes(dict(_nested(unit))))
        chain = fragment_chains.get(chain_key, [])
        if chain and (before or after):
            position = next((index for index, item in enumerate(chain) if item.get("unit_id") == unit_id), None)
            if position is not None:
                for related in chain[max(0, position - before):position]:
                    _append_reason(selected, str(related["unit_id"]), {"kind": "fragment_neighbor", "from_unit_id": unit_id})
                for related in chain[position + 1:position + 1 + after]:
                    _append_reason(selected, str(related["unit_id"]), {"kind": "fragment_neighbor", "from_unit_id": unit_id})
        if content_type == "structured" and (before or after):
            chain = structured_by_canonical_unit.get(_base_address(unit), [])
            position = next((index for index, item in enumerate(chain) if item.get("unit_id") == unit_id), None)
            if position is not None:
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
                related = rich_by_context_ordinal.get((*_base_address(unit, include_unit_ordinal=False), ordinal))
                if related is not None:
                    _append_reason(selected, str(related["unit_id"]), {"kind": "ordinal_neighbor", "from_unit_id": unit_id})
        locator = _dialogue_locator(unit)
        if locator is not None and config.dialogue_hops:
            seen = {locator[-1]}
            frontier = {locator[-1]}
            group_base = locator[:-1]
            for hop in range(1, config.dialogue_hops + 1):
                next_nodes: set[str] = set()
                for parent_id, child_id in dialogue_edges.get(group_base, []):
                    if parent_id in frontier and child_id not in seen:
                        next_nodes.add(child_id)
                    if child_id in frontier and parent_id not in seen:
                        next_nodes.add(parent_id)
                for node_id in sorted(next_nodes):
                    related_locator = (*group_base, node_id)
                    for related in dialogue_nodes.get(related_locator, []):
                        _append_reason(selected, str(related["unit_id"]), {"kind": "observed_dialogue_edge", "from_unit_id": unit_id, "hop": hop})
                seen.update(next_nodes)
                frontier = next_nodes
                if not frontier:
                    break
    return selected


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
    direct_ranks: list[int] = []
    for member in members:
        selection_info = selection[str(member["unit_id"])]
        retrieval = selection_info.get("retrieval")
        if isinstance(retrieval, Mapping):
            direct_ranks.append(int(retrieval["rank"]))
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
    return {
        "members": member_rows,
        "text": text,
        "char_count": len(text),
        "source_order": member_rows[0]["source_order"],
        "priority": min(direct_ranks) if direct_ranks else None,
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


def assemble_evidence_packet(
    retrieval_unit_manifest_path: Path,
    ranked_candidates: Sequence[Mapping[str, Any]],
    *,
    config: EvidenceAssemblyConfig | None = None,
    retrieval_audit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble one deterministic provider-neutral Evidence Packet.

    ``ranked_candidates`` must be supplied by an upstream retrieval mode.  No
    similarity, re-ranking, text identity, or model decision is performed.
    """

    config = config or EvidenceAssemblyConfig()
    build_manifest, units = load_retrieval_units(retrieval_unit_manifest_path)
    units_by_id: dict[str, Mapping[str, Any]] = {}
    for unit in units:
        unit_id = unit.get("unit_id")
        if not isinstance(unit_id, str) or unit_id in units_by_id:
            raise EvidenceAssemblyError("Retrieval Unit artifact has duplicate or invalid unit_id")
        if unit.get("schema_version") != RETRIEVAL_UNIT_SCHEMA_VERSION:
            raise EvidenceAssemblyError("Retrieval Unit artifact schema is unsupported")
        units_by_id[unit_id] = unit
    direct, deduplicated = _candidate_rows(ranked_candidates, units_by_id)
    selection = _expand_context(direct=direct, units_by_id=units_by_id, config=config)
    blocks, omitted = _select_blocks(_block_members(selection, units_by_id), config)
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
    packet = {
        "schema_version": EVIDENCE_PACKET_SCHEMA_VERSION,
        "assembly_version": EVIDENCE_ASSEMBLY_VERSION,
        "retrieval_unit_build": {
            "build_identity": build_manifest.get("build_identity"),
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
    return packet


def evidence_packet_json_bytes(packet: Mapping[str, Any]) -> bytes:
    """Return deterministic JSON packet bytes with no clocks, paths, or UUIDs."""

    return canonical_json_bytes(dict(packet))


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


def write_evidence_packet(output_root: Path, packet: Mapping[str, Any]) -> dict[str, Any]:
    """Persist JSON/Markdown packet bytes conflict-safely and deterministically."""

    output_root = Path(output_root)
    json_body = evidence_packet_json_bytes(packet)
    markdown_body = evidence_packet_markdown(packet).encode("utf-8")
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
