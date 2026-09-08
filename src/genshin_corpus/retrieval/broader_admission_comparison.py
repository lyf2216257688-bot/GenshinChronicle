"""Provider-free broader admission comparison over the frozen Step-0 supply."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.generation.generation import GenerationContractError, project_generation_request

from .broader_admission_supply import (
    ACCEPTED_CANDIDATE_HASHES_SHA256,
    DEFAULT_CANDIDATE_HASHES,
    _historical_hashes,
)
from .evidence_assembly import (
    DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY,
    V2_DIRECT_FIRST_CONTEXT_CAP,
    V2_DIRECT_FIRST_CONTEXT_CAP_POLICY,
    EvidenceAssemblyConfig,
    EvidenceAssemblyDiagnostics,
    assemble_deferred_footprint_charge_shadow_packet,
    assemble_evidence_packet,
    evidence_packet_json_bytes,
    prepare_evidence_assembly_context,
)


SCHEMA_VERSION = "p04-rag-broader-admission-comparison-0.2"
QUESTION_IDS = tuple(f"Q{number:03d}" for number in range(1, 71))
DEFAULT_SUPPLY_ROOT = Path(".local/p04-rag-broader-admission-70q-candidate-supply")
DEFAULT_RU_MANIFEST = Path(
    "data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/ru/metadata/manifest.json"
)
DEFAULT_OUTPUT_ROOT = Path(".local/p04-rag-broader-admission-70q-comparison")
ACCEPTED_SUPPLY_RUN_IDENTITY = "fe0f40824fd5ef20b00c0668c5594ab0d8d9869e367551f5d4ba75a94ae82f1e"
ACCEPTED_RU_BUILD_IDENTITY = "49b48ee746716add0248fed388d10bd522a930efb582a0f5e827f66681ed8998"
ACCEPTED_LEXICAL_BUILD_IDENTITY = "1eab8db130daeef16c48f3185de2fd54466584884861af01cf01936472d80d04"
ACCEPTED_DENSE_BUILD_IDENTITY = "3d38d7f72000222c074544ce1835a25e01c42461143de9c4f46aecb259710936"
_EVIDENCE_IDENTITY_QUESTION = "P04 broader-admission Evidence Packet identity projection."


class BroaderAdmissionComparisonError(ValueError):
    """Raised when frozen input, policy, or recovery bindings are unsafe."""


def _read_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BroaderAdmissionComparisonError(f"{label} is unreadable: {path}") from exc
    if not isinstance(value, Mapping):
        raise BroaderAdmissionComparisonError(f"{label} must be an object")
    return value


def _sha256_bytes(body: bytes) -> str:
    return sha256(body).hexdigest()


def _candidate_sha(rows: Sequence[Mapping[str, Any]]) -> str:
    return _sha256_bytes(canonical_json_bytes(list(rows)))


def _descriptor(path: Path, body: bytes) -> dict[str, Any]:
    return {"path": str(path), "sha256": _sha256_bytes(body), "byte_count": len(body)}


def _immutable_write(path: Path, body: bytes) -> dict[str, Any]:
    if path.exists():
        if path.read_bytes() != body:
            raise BroaderAdmissionComparisonError(f"refusing to overwrite different comparison artifact: {path}")
    else:
        atomic_write(path, body)
    return _descriptor(path, body)


def _write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    atomic_write(path, canonical_json_bytes(dict(manifest)))


def _expected_config() -> EvidenceAssemblyConfig:
    config = EvidenceAssemblyConfig()
    if config.total_context_chars != 12000 or config.per_block_chars != 3000:
        raise BroaderAdmissionComparisonError("accepted B0 Evidence Assembly configuration is unavailable")
    return config


def _v2_operating_config(config: EvidenceAssemblyConfig) -> dict[str, Any]:
    return {
        "neighbor_before": config.neighbor_before,
        "neighbor_after": config.neighbor_after,
        "structured_neighbor_before": config.structured_neighbor_before,
        "structured_neighbor_after": config.structured_neighbor_after,
        "dialogue_hops": config.dialogue_hops,
        "per_block_chars": config.per_block_chars,
        "total_context_chars": config.total_context_chars,
    }


def _accepted_hybrid_hashes() -> dict[str, str]:
    """Load the external Step-0 oracle before trusting frozen candidate rows."""

    try:
        actual = _sha256_bytes(Path(DEFAULT_CANDIDATE_HASHES).read_bytes())
    except OSError as exc:
        raise BroaderAdmissionComparisonError("accepted 70Q candidate diagnostic is unreadable") from exc
    if actual != ACCEPTED_CANDIDATE_HASHES_SHA256:
        raise BroaderAdmissionComparisonError(
            "accepted 70Q candidate diagnostic does not match its external SHA-256 binding"
        )
    try:
        hashes = _historical_hashes(Path(DEFAULT_CANDIDATE_HASHES))
    except ValueError as exc:
        raise BroaderAdmissionComparisonError("accepted 70Q candidate diagnostic is structurally invalid") from exc
    return {question_id: hashes[(question_id, "hybrid")] for question_id in QUESTION_IDS}


def _validate_supply_manifest(supply_root: Path) -> Mapping[str, Any]:
    accepted_hybrid_hashes = _accepted_hybrid_hashes()
    manifest = _read_object(supply_root / "metadata" / "manifest.json", "frozen Step-0 manifest")
    if manifest.get("schema_version") != "p04-rag-broader-admission-candidate-supply-0.1":
        raise BroaderAdmissionComparisonError("frozen Step-0 supply has an unsupported schema")
    if manifest.get("status") != "accepted":
        raise BroaderAdmissionComparisonError("frozen Step-0 supply is not accepted")
    if manifest.get("run_identity") != ACCEPTED_SUPPLY_RUN_IDENTITY:
        raise BroaderAdmissionComparisonError("frozen Step-0 supply run identity does not match the accepted binding")
    if manifest.get("completed_question_ids") != list(QUESTION_IDS):
        raise BroaderAdmissionComparisonError("frozen Step-0 supply does not account for Q001-Q070 in order")
    if manifest.get("completed_question_count") != 70 or manifest.get("top_k") != 20 or manifest.get("rrf_k") != 60:
        raise BroaderAdmissionComparisonError("frozen Step-0 supply has an unsupported candidate contract")
    if manifest.get("provider_attempts_issued") != 0 or manifest.get("generation_calls") != 0:
        raise BroaderAdmissionComparisonError("frozen Step-0 supply does not preserve the provider-free contract")
    if manifest.get("reproduction") != {"lexical": 70, "dense": 70, "hybrid": 70, "total": 210, "full_hybrid_rows": 22}:
        raise BroaderAdmissionComparisonError("frozen Step-0 supply lacks the accepted exact reproduction gate")
    baseline = manifest.get("baseline")
    if not isinstance(baseline, Mapping):
        raise BroaderAdmissionComparisonError("frozen Step-0 supply lacks baseline bindings")
    expected = {
        "retrieval_unit_build_identity": ACCEPTED_RU_BUILD_IDENTITY,
        "lexical_build_identity": ACCEPTED_LEXICAL_BUILD_IDENTITY,
        "dense_build_identity": ACCEPTED_DENSE_BUILD_IDENTITY,
    }
    if any(baseline.get(key) != value for key, value in expected.items()):
        raise BroaderAdmissionComparisonError("frozen Step-0 supply baseline does not match accepted identities")
    historical = manifest.get("historical_bindings")
    if not isinstance(historical, Mapping) or historical.get("candidate_hashes_sha256") != ACCEPTED_CANDIDATE_HASHES_SHA256:
        raise BroaderAdmissionComparisonError("frozen Step-0 supply lacks the accepted external candidate-oracle binding")
    validated = dict(manifest)
    # This in-memory binding is deliberately not taken from the Step-0 output.
    validated["_accepted_hybrid_hashes"] = accepted_hybrid_hashes
    return validated


def _validate_hybrid_rows(rows: Any, *, question_id: str, supply_baseline: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if not isinstance(rows, list) or len(rows) != 20:
        raise BroaderAdmissionComparisonError(f"frozen Hybrid Top20 is incomplete: {question_id}")
    for expected_rank, row in enumerate(rows, 1):
        if not isinstance(row, Mapping) or row.get("rank") != expected_rank or not isinstance(row.get("unit_id"), str):
            raise BroaderAdmissionComparisonError(f"frozen Hybrid Top20 ordering is invalid: {question_id}")
        retrieval = row.get("retrieval")
        if not isinstance(retrieval, Mapping) or retrieval.get("mode") != "hybrid":
            raise BroaderAdmissionComparisonError(f"frozen Hybrid candidate mode is invalid: {question_id}")
        identities = retrieval.get("arm_build_identities")
        fusion = retrieval.get("fusion")
        if not isinstance(identities, Mapping) or identities.get("lexical") != supply_baseline["lexical_build_identity"] or identities.get("dense") != supply_baseline["dense_build_identity"]:
            raise BroaderAdmissionComparisonError(f"frozen Hybrid candidate arm binding is invalid: {question_id}")
        if not isinstance(fusion, Mapping) or fusion.get("method") != "rrf":
            raise BroaderAdmissionComparisonError(f"frozen Hybrid candidate fusion binding is invalid: {question_id}")
    return rows


def _load_frozen_question(supply_root: Path, question_id: str, supply_manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    record_path = supply_root / "candidates" / f"{question_id}.json"
    record = _read_object(record_path, "frozen Step-0 candidate record")
    if record.get("schema_version") != supply_manifest.get("schema_version") or record.get("run_identity") != supply_manifest.get("run_identity"):
        raise BroaderAdmissionComparisonError(f"frozen candidate record is not bound to Step 0: {question_id}")
    if record.get("question_id") != question_id or not isinstance(record.get("question_identity"), str):
        raise BroaderAdmissionComparisonError(f"frozen candidate record question binding is invalid: {question_id}")
    windows = record.get("candidate_windows")
    hashes = record.get("candidate_hashes")
    if not isinstance(windows, Mapping) or not isinstance(hashes, Mapping):
        raise BroaderAdmissionComparisonError(f"frozen candidate record lacks candidate windows: {question_id}")
    rows = _validate_hybrid_rows(windows.get("hybrid"), question_id=question_id, supply_baseline=supply_manifest["baseline"])
    actual_sha = _candidate_sha(rows)
    if hashes.get("hybrid") != actual_sha:
        raise BroaderAdmissionComparisonError(f"frozen Hybrid candidate SHA mismatch: {question_id}")
    accepted_hybrid_hashes = supply_manifest.get("_accepted_hybrid_hashes")
    if not isinstance(accepted_hybrid_hashes, Mapping) or accepted_hybrid_hashes.get(question_id) != actual_sha:
        raise BroaderAdmissionComparisonError(
            f"frozen Hybrid candidate SHA does not match the accepted historical oracle: {question_id}"
        )
    return {
        "record_path": record_path,
        "record": record,
        "hybrid_rows": rows,
        "hybrid_sha256": actual_sha,
        "accepted_hybrid_sha256": actual_sha,
    }


def _visible_unit_ids(packet: Mapping[str, Any]) -> list[str]:
    evidence = packet.get("evidence")
    if not isinstance(evidence, list):
        raise BroaderAdmissionComparisonError("Evidence Packet lacks evidence rows")
    return [str(member["unit_id"]) for block in evidence for member in block.get("members", [])]


def _candidate_root_ids(candidates: Sequence[Mapping[str, Any]], packet: Mapping[str, Any]) -> list[str]:
    visible = set(_visible_unit_ids(packet))
    return [str(row["unit_id"]) for row in candidates if str(row["unit_id"]) in visible]


def _reason_counts(packet: Mapping[str, Any]) -> dict[str, int]:
    omitted = packet.get("budget", {}).get("omitted_blocks", [])
    if not isinstance(omitted, list):
        raise BroaderAdmissionComparisonError("Evidence Packet omission audit is invalid")
    return dict(sorted(Counter(str(row.get("reason")) for row in omitted if isinstance(row, Mapping)).items()))


def _supported_displacements(packet: Mapping[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in packet.get("budget", {}).get("omitted_blocks", []):
        if isinstance(row, Mapping) and row.get("displaced_by_anchor_ids"):
            records.append({
                "source": "budget_omission",
                "unit_ids": list(row.get("unit_ids", [])),
                "reason": row.get("reason"),
                "displaced_by_anchor_ids": list(row["displaced_by_anchor_ids"]),
            })
    contract = packet.get("shadow_contract")
    if isinstance(contract, Mapping):
        for root in contract.get("direct_footprints", []):
            if isinstance(root, Mapping) and root.get("reason") is not None:
                records.append({
                    "source": "direct_footprint",
                    "unit_ids": [root.get("anchor_unit_id")],
                    "reason": root.get("reason"),
                    "displaced_by_anchor_ids": list(root.get("displaced_by_anchor_ids", [])),
                })
            if isinstance(root, Mapping):
                for part in root.get("deferred_footprint_parts", []):
                    if isinstance(part, Mapping) and part.get("reason") is not None:
                        records.append({
                            "source": "deferred_footprint",
                            "unit_ids": list(part.get("unit_ids", [])),
                            "reason": part.get("reason"),
                            "displaced_by_anchor_ids": list(part.get("displaced_by_anchor_ids", [])),
                        })
    return records


def _evidence_visible_projection(packet: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """Hash only the Packet content that crosses the Generation evidence boundary.

    The comparison has no question text or Generation execution.  The fixed
    placeholder lets the accepted request projector validate and normalize the
    Packet while the identity intentionally excludes that shared placeholder.
    """

    try:
        semantic = project_generation_request(packet, question=_EVIDENCE_IDENTITY_QUESTION).semantic_projection()
    except GenerationContractError as exc:
        raise BroaderAdmissionComparisonError("Evidence Packet cannot form a generation-visible projection") from exc
    projection = {
        "schema_version": semantic["schema_version"],
        "instruction": semantic["instruction"],
        "evidence": semantic["evidence"],
        "citation_policy": semantic["citation_policy"],
        "evidence_packet_schema_version": semantic["evidence_packet_schema_version"],
    }
    return projection, sha256_json(projection)


def _arm_summary(packet: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    budget = packet.get("budget")
    if not isinstance(budget, Mapping):
        raise BroaderAdmissionComparisonError("Evidence Packet budget is invalid")
    visible = _visible_unit_ids(packet)
    generation_visible_projection, generation_visible_projection_sha256 = _evidence_visible_projection(packet)
    return {
        "packet_sha256": _sha256_bytes(evidence_packet_json_bytes(packet)),
        "generation_visible_projection": generation_visible_projection,
        "generation_visible_projection_sha256": generation_visible_projection_sha256,
        "assembly_version": packet.get("assembly_version"),
        "selection_policy": packet.get("selection_policy"),
        "selection_policy_identity": packet.get("selection_policy_identity"),
        "assembly_config": packet.get("assembly_config"),
        "assembly_config_identity": packet.get("assembly_config_identity"),
        "retrieval_unit_build_identity": packet.get("retrieval_unit_build", {}).get("build_identity"),
        "visible_unit_ids": visible,
        "final_visible_candidate_root_ids": _candidate_root_ids(candidates, packet),
        "budget": {
            "per_block_chars": budget.get("per_block_chars"),
            "total_context_chars": budget.get("total_context_chars"),
            "used_context_chars": budget.get("used_context_chars"),
            "used_evidence_blocks": budget.get("used_evidence_blocks"),
            "used_direct_containing_blocks": budget.get("used_direct_containing_blocks"),
            "used_context_only_blocks": budget.get("used_context_only_blocks"),
        },
        "omission_reason_counts": _reason_counts(packet),
        "supported_displacements": _supported_displacements(packet),
    }


def _mechanical_class(control: Mapping[str, Any], challenger: Mapping[str, Any]) -> str:
    if control["generation_visible_projection_sha256"] == challenger["generation_visible_projection_sha256"]:
        return "identical"
    control_roots = set(control["final_visible_candidate_root_ids"])
    challenger_roots = set(challenger["final_visible_candidate_root_ids"])
    gains, losses = challenger_roots - control_roots, control_roots - challenger_roots
    if gains and losses:
        return "substitution_or_mixed_gain_loss"
    if gains:
        return "additive_only"
    if losses:
        return "retention_loss_only"
    return "non_identical_without_direct_root_delta"


def _validate_packets(control: Mapping[str, Any], challenger: Mapping[str, Any], config: EvidenceAssemblyConfig) -> None:
    for packet, label in ((control, "control"), (challenger, "challenger")):
        if packet.get("retrieval_unit_build", {}).get("build_identity") != ACCEPTED_RU_BUILD_IDENTITY:
            raise BroaderAdmissionComparisonError(f"{label} Packet RU build does not match the accepted binding")
        if packet.get("assembly_config", {}).get("total_context_chars") != 12000:
            raise BroaderAdmissionComparisonError(f"{label} Packet does not use B0=12000")
        if packet.get("assembly_config") != _v2_operating_config(config):
            raise BroaderAdmissionComparisonError(f"{label} Packet does not use the accepted v2 operating configuration")
    if control.get("selection_policy", {}).get("identity") != V2_DIRECT_FIRST_CONTEXT_CAP_POLICY:
        raise BroaderAdmissionComparisonError("control Packet does not use A1-2-1 v2")
    challenger_contract = challenger.get("shadow_contract", {})
    if (
        challenger.get("selection_policy") != V2_DIRECT_FIRST_CONTEXT_CAP.to_dict()
        or not isinstance(challenger_contract, Mapping)
        or challenger_contract.get("identity") != DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY
        or challenger_contract.get("control_selection_policy") != V2_DIRECT_FIRST_CONTEXT_CAP.to_dict()
    ):
        raise BroaderAdmissionComparisonError("challenger Packet does not use Deferred-Footprint")
    if control.get("assembly_config") != challenger.get("assembly_config"):
        raise BroaderAdmissionComparisonError("arms do not share the Evidence Assembly operating configuration")


def compare_frozen_question(
    frozen: Mapping[str, Any],
    *,
    retrieval_unit_manifest_path: Path,
    prepared_context: Any,
    config: EvidenceAssemblyConfig,
) -> dict[str, Any]:
    """Compare both existing policy paths using one exact frozen Hybrid list."""

    record = frozen["record"]
    question_id = str(record["question_id"])
    candidates = frozen["hybrid_rows"]
    before_sha = _candidate_sha(candidates)
    if frozen.get("accepted_hybrid_sha256") != before_sha:
        raise BroaderAdmissionComparisonError(
            f"frozen Hybrid candidate SHA does not match the accepted historical oracle: {question_id}"
        )
    audit = {"query_id": question_id, "mode": "hybrid", "candidate_supply": "frozen_step0"}
    control_diagnostics = EvidenceAssemblyDiagnostics()
    control_candidates = candidates
    control_packet = assemble_evidence_packet(
        retrieval_unit_manifest_path,
        control_candidates,
        config=config,
        retrieval_audit=audit,
        prepared_context=prepared_context,
        diagnostics=control_diagnostics,
        selection_policy=V2_DIRECT_FIRST_CONTEXT_CAP,
    )
    after_control_sha = _candidate_sha(candidates)
    if after_control_sha != before_sha:
        raise BroaderAdmissionComparisonError(f"control path mutated frozen candidates: {question_id}")
    challenger_diagnostics = EvidenceAssemblyDiagnostics()
    challenger_candidates = candidates
    challenger_packet = assemble_deferred_footprint_charge_shadow_packet(
        retrieval_unit_manifest_path,
        challenger_candidates,
        config=config,
        retrieval_audit=audit,
        prepared_context=prepared_context,
        diagnostics=challenger_diagnostics,
    )
    after_challenger_sha = _candidate_sha(candidates)
    if after_challenger_sha != before_sha:
        raise BroaderAdmissionComparisonError(f"challenger path mutated frozen candidates: {question_id}")
    if control_candidates is not challenger_candidates:
        raise BroaderAdmissionComparisonError(f"arms did not receive the same frozen candidate object: {question_id}")
    _validate_packets(control_packet, challenger_packet, config)
    control = _arm_summary(control_packet, candidates)
    challenger = _arm_summary(challenger_packet, candidates)
    control_roots = set(control["final_visible_candidate_root_ids"])
    challenger_roots = set(challenger["final_visible_candidate_root_ids"])
    return {
        "schema_version": SCHEMA_VERSION,
        "question_id": question_id,
        "question_identity": record["question_identity"],
        "frozen_input": {
            "step0_run_identity": record["run_identity"],
            "source_candidate_path": str(frozen["record_path"]),
            "hybrid_candidate_sha256": before_sha,
            "accepted_historical_hybrid_sha256": frozen["accepted_hybrid_sha256"],
            "candidate_count": len(candidates),
            "same_object_passed_to_both_arms": True,
            "candidate_sha256_before": before_sha,
            "candidate_sha256_after_control": after_control_sha,
            "candidate_sha256_after_challenger": after_challenger_sha,
        },
        "policy_bindings": {
            "retrieval_unit_build_identity": ACCEPTED_RU_BUILD_IDENTITY,
            "control_selection_policy": V2_DIRECT_FIRST_CONTEXT_CAP.to_dict(),
            "challenger_policy": DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY,
            "assembly_config": control_packet["assembly_config"],
        },
        "control": control,
        "challenger": challenger,
        "evidence_identical": (
            control["generation_visible_projection_sha256"]
            == challenger["generation_visible_projection_sha256"]
        ),
        "control_visible_to_challenger_invisible_roots": [unit_id for unit_id in control["final_visible_candidate_root_ids"] if unit_id not in challenger_roots],
        "control_invisible_to_challenger_visible_roots": [unit_id for unit_id in challenger["final_visible_candidate_root_ids"] if unit_id not in control_roots],
        "shared_visible_roots": [unit_id for unit_id in control["final_visible_candidate_root_ids"] if unit_id in challenger_roots],
        "mechanical_class": _mechanical_class(control, challenger),
        "control_selection_trace": control_diagnostics.selection_trace,
        "challenger_selection_trace": challenger_diagnostics.selection_trace,
        "control_packet": control_packet,
        "challenger_packet": challenger_packet,
    }


def _comparison_run_identity(supply_manifest: Mapping[str, Any], config: EvidenceAssemblyConfig) -> str:
    return sha256_json({
        "schema_version": SCHEMA_VERSION,
        "supply_run_identity": supply_manifest["run_identity"],
        "external_hybrid_candidate_oracle_sha256": ACCEPTED_CANDIDATE_HASHES_SHA256,
        "ru_build_identity": ACCEPTED_RU_BUILD_IDENTITY,
        "control_policy": V2_DIRECT_FIRST_CONTEXT_CAP.to_dict(),
        "challenger_policy": DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY,
        "assembly_config": _v2_operating_config(config),
        "candidate_mode": "hybrid_top20",
        "provider_generation": 0,
    })


def preflight_broader_admission_comparison(
    supply_root: Path = DEFAULT_SUPPLY_ROOT,
    *,
    retrieval_unit_manifest_path: Path = DEFAULT_RU_MANIFEST,
) -> dict[str, Any]:
    """Validate immutable Step-0 and control/challenger bindings without comparison."""

    supply_root = Path(supply_root)
    supply_manifest = _validate_supply_manifest(supply_root)
    config = _expected_config()
    ru_manifest = _read_object(retrieval_unit_manifest_path, "accepted Retrieval Unit manifest")
    if ru_manifest.get("status") != "complete" or ru_manifest.get("build_identity") != ACCEPTED_RU_BUILD_IDENTITY:
        raise BroaderAdmissionComparisonError("accepted Retrieval Unit manifest does not match the frozen supply")
    for question_id in QUESTION_IDS:
        _load_frozen_question(supply_root, question_id, supply_manifest)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ready_to_compare",
        "supply_root": str(supply_root),
        "supply_run_identity": supply_manifest["run_identity"],
        "retrieval_unit_manifest": str(retrieval_unit_manifest_path),
        "retrieval_unit_build_identity": ACCEPTED_RU_BUILD_IDENTITY,
        "question_count": 70,
        "candidate_mode": "hybrid_top20",
        "assembly_config": _v2_operating_config(config),
        "control_selection_policy": V2_DIRECT_FIRST_CONTEXT_CAP.to_dict(),
        "challenger_policy": DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY,
        "run_identity": _comparison_run_identity(supply_manifest, config),
        "provider_calls": 0,
        "generation_calls": 0,
        "retrieval_calls": 0,
        "dense_query_encodings": 0,
        "rrf_recomputations": 0,
    }


def _comparison_contract(preflight: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_identity": preflight["run_identity"],
        "supply_run_identity": ACCEPTED_SUPPLY_RUN_IDENTITY,
        "external_hybrid_candidate_oracle_sha256": ACCEPTED_CANDIDATE_HASHES_SHA256,
        "retrieval_unit_build_identity": ACCEPTED_RU_BUILD_IDENTITY,
        "candidate_mode": "hybrid_top20",
        "question_ids": list(QUESTION_IDS),
        "assembly_config": preflight["assembly_config"],
        "control_selection_policy": preflight["control_selection_policy"],
        "challenger_policy": DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY,
        "provider_calls": 0,
        "generation_calls": 0,
        "retrieval_calls": 0,
        "dense_query_encodings": 0,
        "rrf_recomputations": 0,
    }


def _expected_row_policy_bindings(contract: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "retrieval_unit_build_identity": contract["retrieval_unit_build_identity"],
        "control_selection_policy": contract["control_selection_policy"],
        "challenger_policy": contract["challenger_policy"],
        "assembly_config": contract["assembly_config"],
    }


def _validate_comparison_row(
    row: Mapping[str, Any],
    *,
    question_id: str,
    frozen: Mapping[str, Any],
    contract: Mapping[str, Any],
    config: EvidenceAssemblyConfig,
) -> None:
    """Recompute all contract-bound derived values for new and resumed rows."""

    record = frozen["record"]
    if row.get("schema_version") != contract["schema_version"] or row.get("question_id") != question_id:
        raise BroaderAdmissionComparisonError(f"comparison row schema or question binding is invalid: {question_id}")
    if row.get("question_identity") != record.get("question_identity"):
        raise BroaderAdmissionComparisonError(f"comparison row question identity is invalid: {question_id}")
    frozen_input = row.get("frozen_input")
    expected_frozen = {
        "step0_run_identity": contract["supply_run_identity"],
        "source_candidate_path": str(frozen["record_path"]),
        "hybrid_candidate_sha256": frozen["hybrid_sha256"],
        "accepted_historical_hybrid_sha256": frozen["accepted_hybrid_sha256"],
        "candidate_count": 20,
        "same_object_passed_to_both_arms": True,
        "candidate_sha256_before": frozen["hybrid_sha256"],
        "candidate_sha256_after_control": frozen["hybrid_sha256"],
        "candidate_sha256_after_challenger": frozen["hybrid_sha256"],
    }
    if frozen_input != expected_frozen:
        raise BroaderAdmissionComparisonError(f"comparison row frozen-input binding is invalid: {question_id}")
    if row.get("policy_bindings") != _expected_row_policy_bindings(contract):
        raise BroaderAdmissionComparisonError(f"comparison row policy/config binding is invalid: {question_id}")
    control_packet, challenger_packet = row.get("control_packet"), row.get("challenger_packet")
    if not isinstance(control_packet, Mapping) or not isinstance(challenger_packet, Mapping):
        raise BroaderAdmissionComparisonError(f"comparison row lacks embedded Evidence Packets: {question_id}")
    _validate_packets(control_packet, challenger_packet, config)
    control = _arm_summary(control_packet, frozen["hybrid_rows"])
    challenger = _arm_summary(challenger_packet, frozen["hybrid_rows"])
    if row.get("control") != control or row.get("challenger") != challenger:
        raise BroaderAdmissionComparisonError(f"comparison row Packet audit/projection binding is invalid: {question_id}")
    control_roots = set(control["final_visible_candidate_root_ids"])
    challenger_roots = set(challenger["final_visible_candidate_root_ids"])
    expected_derived = {
        "evidence_identical": control["generation_visible_projection_sha256"] == challenger["generation_visible_projection_sha256"],
        "control_visible_to_challenger_invisible_roots": [
            unit_id for unit_id in control["final_visible_candidate_root_ids"] if unit_id not in challenger_roots
        ],
        "control_invisible_to_challenger_visible_roots": [
            unit_id for unit_id in challenger["final_visible_candidate_root_ids"] if unit_id not in control_roots
        ],
        "shared_visible_roots": [
            unit_id for unit_id in control["final_visible_candidate_root_ids"] if unit_id in challenger_roots
        ],
        "mechanical_class": _mechanical_class(control, challenger),
    }
    if any(row.get(key) != value for key, value in expected_derived.items()):
        raise BroaderAdmissionComparisonError(f"comparison row mechanical derivation is invalid: {question_id}")


def _validate_existing_rows(
    output_root: Path,
    manifest: Mapping[str, Any],
    contract: Mapping[str, Any],
    supply_root: Path,
    supply_manifest: Mapping[str, Any],
    config: EvidenceAssemblyConfig,
) -> tuple[set[str], dict[str, Any]]:
    if manifest.get("status") == "complete":
        raise BroaderAdmissionComparisonError("comparison output is complete; refusing to overwrite or resume it")
    if manifest.get("status") not in {"partial", "failed"}:
        raise BroaderAdmissionComparisonError("existing comparison output is incompatible with the frozen input")
    if any(manifest.get(key) != value for key, value in contract.items()):
        raise BroaderAdmissionComparisonError("existing comparison output does not match the current comparison contract")
    completed = manifest.get("completed_question_ids")
    descriptors = manifest.get("completed_artifacts")
    if (
        not isinstance(completed, list)
        or completed != list(QUESTION_IDS[:len(completed)])
        or manifest.get("completed_question_count") != len(completed)
        or not isinstance(descriptors, Mapping)
        or set(descriptors) != set(completed)
    ):
        raise BroaderAdmissionComparisonError("existing comparison recovery ledger is invalid")
    artifact_ids = {path.stem for path in (output_root / "comparisons").glob("*.json")}
    if artifact_ids != set(completed):
        raise BroaderAdmissionComparisonError("existing comparison artifacts do not match the completion ledger")
    for question_id in completed:
        descriptor = descriptors.get(question_id)
        path = output_root / "comparisons" / f"{question_id}.json"
        if not isinstance(descriptor, Mapping) or not path.is_file():
            raise BroaderAdmissionComparisonError(f"existing comparison artifact failed integrity validation: {question_id}")
        body = path.read_bytes()
        if (
            descriptor != _descriptor(path, body)
            or _sha256_bytes(body) != descriptor.get("sha256")
        ):
            raise BroaderAdmissionComparisonError(f"existing comparison artifact failed integrity validation: {question_id}")
        row = _read_object(path, "existing comparison artifact")
        frozen = _load_frozen_question(supply_root, question_id, supply_manifest)
        _validate_comparison_row(row, question_id=question_id, frozen=frozen, contract=contract, config=config)
    return set(completed), dict(descriptors)


def _aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    classes = Counter(str(row["mechanical_class"]) for row in rows)
    non_identical = [row for row in rows if not row["evidence_identical"]]
    return {
        "question_count": len(rows),
        "evidence_identical_count": len(rows) - len(non_identical),
        "evidence_non_identical_count": len(non_identical),
        "mechanical_class_counts": dict(sorted(classes.items())),
        "questions_with_direct_root_gains": sum(bool(row["control_invisible_to_challenger_visible_roots"]) for row in rows),
        "questions_with_direct_root_losses": sum(bool(row["control_visible_to_challenger_invisible_roots"]) for row in rows),
    }


def _non_identical_index(rows: Sequence[Mapping[str, Any]]) -> bytes:
    entries = []
    for row in rows:
        if row["evidence_identical"]:
            continue
        entries.append({
            "question_id": row["question_id"],
            "mechanical_class": row["mechanical_class"],
            "comparison_path": f"comparisons/{row['question_id']}.json",
            "control_packet_sha256": row["control"]["packet_sha256"],
            "challenger_packet_sha256": row["challenger"]["packet_sha256"],
            "control_generation_visible_projection_sha256": row["control"]["generation_visible_projection_sha256"],
            "challenger_generation_visible_projection_sha256": row["challenger"]["generation_visible_projection_sha256"],
            "direct_root_gains": row["control_invisible_to_challenger_visible_roots"],
            "direct_root_losses": row["control_visible_to_challenger_invisible_roots"],
        })
    return b"".join(canonical_json_bytes(entry) + b"\n" for entry in entries)


def run_broader_admission_comparison(
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    *,
    supply_root: Path = DEFAULT_SUPPLY_ROOT,
    retrieval_unit_manifest_path: Path = DEFAULT_RU_MANIFEST,
    resume: bool = False,
    progress: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Compare current v2 and Deferred-Footprint without any Retrieval or Provider work."""

    output_root, supply_root = Path(output_root), Path(supply_root)
    preflight = preflight_broader_admission_comparison(supply_root, retrieval_unit_manifest_path=retrieval_unit_manifest_path)
    supply_manifest = _validate_supply_manifest(supply_root)
    config = _expected_config()
    contract = _comparison_contract(preflight)
    manifest_path = output_root / "metadata" / "manifest.json"
    if output_root.exists():
        if not resume:
            raise FileExistsError("comparison output root already exists; use --resume only for an integrity-checked partial run")
        existing = _read_object(manifest_path, "existing comparison manifest")
        completed, descriptors = _validate_existing_rows(
            output_root, existing, contract, supply_root, supply_manifest, config
        )
    else:
        completed, descriptors = set(), {}
        output_root.mkdir(parents=True)
    manifest: dict[str, Any] = {
        **contract,
        "status": "partial",
        "supply_root": str(supply_root),
        "retrieval_unit_manifest": str(retrieval_unit_manifest_path),
        "completed_question_ids": [question_id for question_id in QUESTION_IDS if question_id in completed],
        "completed_question_count": len(completed),
        "completed_artifacts": descriptors,
    }
    _write_manifest(manifest_path, manifest)
    try:
        prepared_context = prepare_evidence_assembly_context(retrieval_unit_manifest_path)
        if prepared_context.retrieval_unit_build_identity != ACCEPTED_RU_BUILD_IDENTITY:
            raise BroaderAdmissionComparisonError("prepared RU context does not match frozen Step-0 binding")
        for question_id in QUESTION_IDS:
            if question_id in completed:
                continue
            frozen = _load_frozen_question(supply_root, question_id, supply_manifest)
            row = compare_frozen_question(
                frozen,
                retrieval_unit_manifest_path=Path(retrieval_unit_manifest_path),
                prepared_context=prepared_context,
                config=config,
            )
            _validate_comparison_row(row, question_id=question_id, frozen=frozen, contract=contract, config=config)
            body = canonical_json_bytes(row)
            artifact = _immutable_write(output_root / "comparisons" / f"{question_id}.json", body)
            completed.add(question_id)
            descriptors[question_id] = artifact
            manifest["completed_question_ids"] = [item for item in QUESTION_IDS if item in completed]
            manifest["completed_question_count"] = len(completed)
            manifest["completed_artifacts"] = descriptors
            _write_manifest(manifest_path, manifest)
            if progress is not None:
                progress({"event": "question_complete", "question_id": question_id, "processed": len(completed), "total": 70})
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["failure"] = {"reason": type(exc).__name__, "message": str(exc)}
        _write_manifest(manifest_path, manifest)
        if isinstance(exc, BroaderAdmissionComparisonError):
            raise
        raise BroaderAdmissionComparisonError("provider-free comparison stopped; partial artifacts remain inspectable") from exc
    if len(completed) != len(QUESTION_IDS):
        raise BroaderAdmissionComparisonError("comparison ended without Q001-Q070")
    rows = [_read_object(output_root / "comparisons" / f"{question_id}.json", "comparison artifact") for question_id in QUESTION_IDS]
    aggregate = _aggregate(rows)
    _immutable_write(output_root / "metadata" / "aggregate.json", canonical_json_bytes(aggregate))
    _immutable_write(output_root / "index" / "non_identical.jsonl", _non_identical_index(rows))
    manifest["aggregate"] = aggregate
    manifest["final_artifacts"] = {
        "aggregate": _descriptor(output_root / "metadata" / "aggregate.json", (output_root / "metadata" / "aggregate.json").read_bytes()),
        "non_identical_index": _descriptor(output_root / "index" / "non_identical.jsonl", (output_root / "index" / "non_identical.jsonl").read_bytes()),
    }
    manifest["status"] = "complete"
    manifest.pop("failure", None)
    _write_manifest(manifest_path, manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare v2 and Deferred-Footprint against the frozen Step-0 Hybrid Top20 supply")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "run"):
        command = commands.add_parser(name)
        command.add_argument("--supply-root", type=Path, default=DEFAULT_SUPPLY_ROOT)
        command.add_argument("--ru-manifest", type=Path, default=DEFAULT_RU_MANIFEST)
        if name == "run":
            command.add_argument("--output-root", type=Path, required=True)
            command.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "preflight":
        result = preflight_broader_admission_comparison(args.supply_root, retrieval_unit_manifest_path=args.ru_manifest)
    else:
        def report(value: Mapping[str, Any]) -> None:
            print(canonical_json_bytes(dict(value)).decode("utf-8"), flush=True)
        result = run_broader_admission_comparison(
            args.output_root,
            supply_root=args.supply_root,
            retrieval_unit_manifest_path=args.ru_manifest,
            resume=args.resume,
            progress=report,
        )
    print(canonical_json_bytes(result).decode("utf-8"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
