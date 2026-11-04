"""Retained-evidence Assembly variants over Formal Deferred Assembly.

The formal Packet is the retention floor. New roots can use only space released
by proved visible aliases or conservative display edits. The combined variant is selected by the shared runtime default.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from . import evidence_assembly as assembly
from .evidence_projection import PROJECTION_VERSION, project_structured_unit


COMPACTION_VERSION = "phase04-retained-evidence-1"
VARIANTS = ("aliases", "html", "combined")


def _labels(member: Mapping[str, Any]) -> tuple[Any, ...]:
    context = member["record_context"]
    return (context.get("record_title"), context.get("section_name"),
            member["structure"].get("dialogue", {}).get("speaker"))


def _visible_aliases(blocks: list[dict], units: Mapping[str, Mapping[str, Any]]) -> list[dict]:
    groups: dict[tuple, list[tuple[int, dict]]] = {}
    for index, block in enumerate(blocks):
        if len(block["members"]) == 1:
            member = block["members"][0]
            groups.setdefault((member["text"], _labels(member)), []).append((index, member))
    aliases = []
    for group in groups.values():
        if len(group) != 2:
            continue
        rich = next((m for _, m in group if m["content_type"] == "rich_text"), None)
        dialogue = next((m for _, m in group if m["content_type"] == "dialogue_node"), None)
        if rich is None or dialogue is None:
            continue
        proof = assembly._dialogue_source_occurrence_alias_proof(units[rich["unit_id"]], units[dialogue["unit_id"]])
        if proof is None:
            continue
        # Only already-visible blocks participate. A representative cannot be
        # suppressed before admission, as happened in the historical challenger.
        def priority(item: tuple[int, dict]) -> tuple:
            index, member = item
            retrieval = member["retrieval"]
            return (retrieval["rank"] if retrieval is not None else float("inf"), index)
        (_, representative), (index, suppressed) = sorted(group, key=priority)
        aliases.append({"suppressed_unit_id": suppressed["unit_id"],
                        "representative_unit_id": representative["unit_id"],
                        "suppressed_member": deepcopy(suppressed),
                        "control_evidence_id": blocks[index]["evidence_id"],
                        "released_chars": blocks[index]["char_count"], "proof": proof})
    return aliases


def _project_block(block: dict, state: Any) -> dict:
    projected_members = []
    for member in block["members"]:
        unit = state.units_by_id[member["unit_id"]]
        key = (unit["content_type"], *assembly._base_address(unit), canonical_json_bytes(dict(assembly._nested(unit))))
        projection = project_structured_unit(unit, fragment_chain=state.fragment_chains[key])
        member["display_projection"] = {k: v for k, v in projection.items() if k != "text"}
        member["display_text"] = projection["text"]
        projected_members.append(projection["text"])
    # Reuse the original structural separators, never concatenate newly
    # adjacent source occurrences after suppressing a different block.
    original_parts, parts = [], []
    for index, (member, text) in enumerate(zip(block["members"], projected_members)):
        if index:
            left = state.units_by_id[block["members"][index - 1]["unit_id"]]
            right = state.units_by_id[member["unit_id"]]
            if assembly._fragment_adjacent(left, right):
                separator = ""
            elif assembly._structured_line_adjacent(left, right):
                separator = "\n"
            else:
                separator = "\n\n"
            original_parts.append(separator)
            parts.append(separator)
        original_parts.append(member["text"])
        parts.append(text)
    if "".join(original_parts) != block["text"]:
        raise assembly.EvidenceAssemblyError("control block member text mismatch")
    block["original_text"] = block["text"]
    block["text"] = "".join(parts)
    block["char_count"] = len(block["text"])
    return block


def assemble_retained_evidence_packet(
    retrieval_unit_manifest_path: Path,
    ranked_candidates: Sequence[Mapping[str, Any]],
    *,
    variant: str,
    config: assembly.EvidenceAssemblyConfig | None = None,
    retrieval_audit: Mapping[str, Any] | None = None,
    prepared_context: assembly.PreparedAssemblyContext | None = None,
) -> dict[str, Any]:
    """Assemble one explicit retained-evidence variant over the formal control."""
    if variant not in VARIANTS:
        raise ValueError("unsupported retained-evidence variant")
    config = config or assembly.EvidenceAssemblyConfig()
    context = prepared_context or assembly.prepare_evidence_assembly_context(retrieval_unit_manifest_path)
    control = assembly.assemble_deferred_footprint_charge_packet(
        retrieval_unit_manifest_path, ranked_candidates, config=config,
        retrieval_audit=retrieval_audit, prepared_context=context,
    )
    state = assembly._prepared_state(context)
    packet = deepcopy(control)
    blocks = packet["evidence"]
    aliases = _visible_aliases(blocks, state.units_by_id) if variant in {"aliases", "combined"} else []
    suppressed = {row["suppressed_unit_id"] for row in aliases}
    retained = [block for block in blocks if block["members"][0]["unit_id"] not in suppressed]
    for block in retained:
        block["control_evidence_id"] = block.pop("evidence_id")
        block["admission_phase"] = "retained"
    if variant in {"html", "combined"}:
        retained = [_project_block(block, state) for block in retained]
    used = sum(block["char_count"] for block in retained)
    after_retention = used
    visible = {member["unit_id"] for block in retained for member in block["members"]}
    refill = []
    for row in sorted(control["admission_contract"]["direct_footprints"], key=lambda row: row["anchor_key"]):
        unit_id = row["anchor_unit_id"]
        if row["reason"] != "direct_total_context_char_budget_conflict" or unit_id in visible | suppressed:
            continue
        unit = state.units_by_id[unit_id]
        candidate = next(item["candidate"] for item in control["admission_contract"]["candidate_observations"]
                         if item["candidate"]["unit_id"] == unit_id)
        selection = {unit_id: {"retrieval": {"rank": row["anchor_key"][0],
                                            "input_index": row["anchor_key"][1], "retrieval": dict(candidate.get("retrieval", {}))},
                               "reasons": [{"kind": "retained_evidence_refill", "rank": row["anchor_key"][0]}]}}
        block = assembly._finish_block([unit], selection)
        # Strip private grouping keys exactly as the formal Packet does.
        block = {k: block[k] for k in ("members", "text", "char_count", "source_order")}
        block["admission_phase"] = "refill"
        if variant in {"html", "combined"}:
            block = _project_block(block, state)
        before = used
        fits = used + block["char_count"] <= config.total_context_chars
        if fits:
            retained.append(block)
            visible.add(unit_id)
            used += block["char_count"]
        refill.append({"unit_id": unit_id, "unit_ids": [unit_id], "rank": row["anchor_key"][0],
                       "full_footprint_char_count": row["immutable_direct_footprint"]["char_count"],
                       "full_footprint_per_block_eligible": row["full_footprint_per_block_eligible"],
                       "display_chars": block["char_count"], "original_chars": len(unit["retrieval_visible_text"]),
                       "budget_before": before, "budget_after": used,
                       "outcome": "admitted" if fits else "omitted",
                       "reason": None if fits else "total_context_char_budget"})
    retained.sort(key=lambda block: (tuple(block["source_order"]), tuple(assembly._block_unit_ids(block))))
    evidence_by_unit = {}
    for index, block in enumerate(retained, 1):
        block["evidence_id"] = f"E{index:02d}"
        for member in block["members"]:
            evidence_by_unit[member["unit_id"]] = block["evidence_id"]
    for alias in aliases:
        alias["representative_evidence_id"] = evidence_by_unit[alias["representative_unit_id"]]
    original_ids = {m["unit_id"] for b in control["evidence"] for m in b["members"]}
    if original_ids - visible != suppressed or any(b["char_count"] > config.per_block_chars for b in retained):
        raise assembly.EvidenceAssemblyError("retained-evidence invariant failed")
    if used > config.total_context_chars or used != sum(b["char_count"] for b in retained):
        raise assembly.EvidenceAssemblyError("retained-evidence budget mismatch")
    policy = {"identity": COMPACTION_VERSION, "variant": variant,
              "display_projection": PROJECTION_VERSION if variant in {"html", "combined"} else None,
              "control_selection_policy": control["selection_policy"]}
    packet.update(assembly_version=COMPACTION_VERSION, selection_policy=policy,
                  selection_policy_identity=sha256_json(policy), evidence=retained,
                  assembly_config_identity=sha256_json({"policy": policy, "config": packet["assembly_config"]}))
    packet["control_admission_contract"] = packet.pop("admission_contract")
    packet["control_budget"] = packet.pop("budget")
    packet["admission_contract"] = {
        "identity": COMPACTION_VERSION,
        "control_packet_sha256": sha256(assembly.evidence_packet_json_bytes(control)).hexdigest(),
        "aliases": aliases, "refill": refill,
        "retained_unit_ids": sorted(original_ids - suppressed),
        "final_evidence_by_unit": evidence_by_unit,
        "alias_released_chars": sum(row["released_chars"] for row in aliases),
        "projection_released_chars": control["budget"]["used_context_chars"] - after_retention
                                     - sum(row["released_chars"] for row in aliases),
        "retention_verified": True,
    }
    packet["budget"] = {"per_block_chars": config.per_block_chars, "total_context_chars": config.total_context_chars,
                        "used_context_chars": used, "used_evidence_blocks": len(retained),
                        "omitted_blocks": [row for row in refill if row["outcome"] == "omitted"]}
    return packet
