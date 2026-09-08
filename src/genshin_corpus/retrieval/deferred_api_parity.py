"""Provider-free parity checking for the formal Deferred Assembly API.

This runner consumes only the accepted Step-0 Hybrid Top20 supply and the
accepted broader-admission comparison artifacts.  It does not call Retrieval,
Dense, RRF, a provider, or Generation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write

from . import broader_admission_comparison as comparison
from .evidence_assembly import (
    DEFERRED_FOOTPRINT_CHARGE,
    DEFERRED_FOOTPRINT_CHARGE_POLICY,
    DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY,
    EvidenceAssemblyConfig,
    EvidenceAssemblyDiagnostics,
    assemble_deferred_footprint_charge_packet,
    prepare_evidence_assembly_context,
)


SCHEMA_VERSION = "p04-rag-deferred-api-parity-0.1"
QUESTION_IDS = comparison.QUESTION_IDS
DEFAULT_SUPPLY_ROOT = comparison.DEFAULT_SUPPLY_ROOT
DEFAULT_COMPARISON_ROOT = comparison.DEFAULT_OUTPUT_ROOT
DEFAULT_RU_MANIFEST = comparison.DEFAULT_RU_MANIFEST
DEFAULT_OUTPUT_ROOT = Path(".local/p04-rag-deferred-api-parity")

ACCEPTED_SOURCE_COMMIT = "f30725af7178aa6f107b61c960f73cac99daddf4"
GATE_A_FORMAL_SOURCE_BASELINE = ACCEPTED_SOURCE_COMMIT
GATE_A_FORMAL_SOURCE_SHA256 = {
    "src/genshin_corpus/retrieval/evidence_assembly.py": "4f0dcf3bc646a78453e71b0e0fe083d4fb16331cd72ab6d80287231a2faedace",
    "src/genshin_corpus/retrieval/broader_admission_comparison.py": "93bdb70dd2469be02cb0c1bbddf0296df0862d6f5fd6e3d8b25a9f2927b31cf4",
    "src/genshin_corpus/generation/generation.py": "392fcedbe2977a579662e48b75711daeb06146809a16ea1dbc060b6f9110bbfe",
}
ACCEPTED_COMPARISON_RUN_IDENTITY = "2c6057fc89fd844a41be60c6f49e672c4f36b647b0f38791d91b7de49587e464"
ACCEPTED_SUPPLY_MANIFEST_SHA256 = "77c69ceb461c0be2670f700e0e1058954306252ce363b85d328b16c75b730acc"
ACCEPTED_COMPARISON_MANIFEST_SHA256 = "151769a8f3c93d67dbf10d62a85662566d6aba8285d0d765f9b4392bd4a69e17"
ACCEPTED_RU_MANIFEST_SHA256 = "dc6bbd30cc6fbc83fa38132085fb8550f20322082467fe24aadcb4239ca78673"

_NORMALIZED_CONTRACT_IDENTITY = "gate-a-normalized-deferred-contract"


class DeferredApiParityError(ValueError):
    """Raised when parity inputs, output recovery, or parity itself is unsafe."""


def _read_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeferredApiParityError(f"{label} is unreadable: {path}") from exc
    if not isinstance(value, Mapping):
        raise DeferredApiParityError(f"{label} must be an object")
    return value


def _file_sha256(path: Path, label: str) -> str:
    try:
        body = Path(path).read_bytes()
    except OSError as exc:
        raise DeferredApiParityError(f"{label} is unreadable: {path}") from exc
    from hashlib import sha256

    return sha256(body).hexdigest()


def _descriptor(path: Path, body: bytes) -> dict[str, Any]:
    return {"path": str(path), "sha256": _file_sha256_from_bytes(body), "byte_count": len(body)}


def _file_sha256_from_bytes(body: bytes) -> str:
    from hashlib import sha256

    return sha256(body).hexdigest()


def _immutable_write(path: Path, body: bytes) -> dict[str, Any]:
    if path.exists():
        try:
            existing = path.read_bytes()
        except OSError as exc:
            raise DeferredApiParityError(f"parity artifact is unreadable: {path}") from exc
        if existing != body:
            raise DeferredApiParityError(f"refusing to overwrite different parity artifact: {path}")
    else:
        atomic_write(path, body)
    return _descriptor(path, body)


def _project_path(relative_path: str) -> Path:
    return Path(__file__).resolve().parents[3] / relative_path


def _source_bindings() -> dict[str, Any]:
    """Bind the approved formal helpers and the exact runner bytes.

    The current Git HEAD is deliberately not part of this contract: the
    runner may be checkpointed after Gate A without changing the semantics it
    checks.  The bytes actually imported/executed remain the authority.
    """

    helper_hashes: dict[str, str] = {}
    for relative_path, expected_sha256 in GATE_A_FORMAL_SOURCE_SHA256.items():
        actual_sha256 = _file_sha256(_project_path(relative_path), f"Gate-A source file: {relative_path}")
        if actual_sha256 != expected_sha256:
            raise DeferredApiParityError(
                f"Gate-A source file is not byte-equivalent to the accepted baseline: {relative_path}"
            )
        helper_hashes[relative_path] = actual_sha256
    runner_relative_path = "src/genshin_corpus/retrieval/deferred_api_parity.py"
    runner_path = _project_path(runner_relative_path)
    return {
        "gate_a_formal_source_baseline": GATE_A_FORMAL_SOURCE_BASELINE,
        "gate_a_formal_source_sha256": helper_hashes,
        "parity_runner_source_path": runner_relative_path,
        "parity_runner_source_sha256": _file_sha256(runner_path, "parity runner source file"),
    }


def _operating_config(config: EvidenceAssemblyConfig) -> dict[str, Any]:
    return comparison._v2_operating_config(config)


def _expected_identity(
    packet: Mapping[str, Any],
    *,
    assembly_version: str,
    policy_binding: Mapping[str, Any],
    config_key: str = "config",
) -> str:
    return sha256_json({
        "assembly_version": assembly_version,
        config_key: packet["assembly_config"],
        **dict(policy_binding),
    })


def _validate_formal_packet(packet: Mapping[str, Any], *, question_id: str, config: EvidenceAssemblyConfig) -> None:
    if packet.get("assembly_version") != DEFERRED_FOOTPRINT_CHARGE_POLICY:
        raise DeferredApiParityError(f"formal Packet assembly identity is invalid: {question_id}")
    if packet.get("retrieval_unit_build", {}).get("build_identity") != comparison.ACCEPTED_RU_BUILD_IDENTITY:
        raise DeferredApiParityError(f"formal Packet RU build does not match the accepted binding: {question_id}")
    if packet.get("assembly_config") != _operating_config(config):
        raise DeferredApiParityError(f"formal Packet assembly configuration is invalid: {question_id}")
    if packet.get("selection_policy") != DEFERRED_FOOTPRINT_CHARGE.to_dict():
        raise DeferredApiParityError(f"formal Packet selection policy is invalid: {question_id}")
    binding = {"selection_policy": DEFERRED_FOOTPRINT_CHARGE.to_dict()}
    if packet.get("assembly_config_identity") != _expected_identity(
        packet, assembly_version=DEFERRED_FOOTPRINT_CHARGE_POLICY, policy_binding=binding
    ):
        raise DeferredApiParityError(f"formal Packet assembly identity hash is invalid: {question_id}")
    if packet.get("selection_policy_identity") != _expected_identity(
        packet,
        assembly_version=DEFERRED_FOOTPRINT_CHARGE_POLICY,
        policy_binding={**binding, "assembly_config": packet["assembly_config"]},
        config_key="assembly_config",
    ):
        raise DeferredApiParityError(f"formal Packet selection identity hash is invalid: {question_id}")
    contract = packet.get("admission_contract")
    if not isinstance(contract, Mapping) or contract.get("identity") != DEFERRED_FOOTPRINT_CHARGE_POLICY:
        raise DeferredApiParityError(f"formal Packet admission contract is invalid: {question_id}")
    if "shadow_contract" in packet:
        raise DeferredApiParityError(f"formal Packet contains shadow namespace metadata: {question_id}")


def _normalize_packet(packet: Mapping[str, Any], *, shadow: bool, question_id: str) -> dict[str, Any]:
    """Remove only the Gate-A identity/namespace differences.

    All evidence, ordering, provenance, visible members, budget fields,
    omissions, displacements, and accounting remain in the comparison.
    """

    value = json.loads(canonical_json_bytes(dict(packet)).decode("utf-8"))
    expected_version = DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY if shadow else DEFERRED_FOOTPRINT_CHARGE_POLICY
    expected_policy = (
        comparison.V2_DIRECT_FIRST_CONTEXT_CAP.to_dict()
        if shadow
        else DEFERRED_FOOTPRINT_CHARGE.to_dict()
    )
    namespace = "shadow_contract" if shadow else "admission_contract"
    member_namespace = "shadow_anchor_memberships" if shadow else "admission_anchor_memberships"
    render_namespace = "shadow_rendering" if shadow else "admission_rendering"
    if value.get("assembly_version") != expected_version or value.get("selection_policy") != expected_policy:
        raise DeferredApiParityError(f"historical/formal Packet policy binding is invalid: {question_id}")
    identity_binding = (
        {"control_selection_policy": comparison.V2_DIRECT_FIRST_CONTEXT_CAP.to_dict()}
        if shadow
        else {"selection_policy": DEFERRED_FOOTPRINT_CHARGE.to_dict()}
    )
    if value.get("assembly_config_identity") != _expected_identity(
        value, assembly_version=expected_version, policy_binding=identity_binding
    ):
        raise DeferredApiParityError(f"Packet assembly identity binding is invalid: {question_id}")
    if value.get("selection_policy_identity") != _expected_identity(
        value,
        assembly_version=expected_version,
        policy_binding={**identity_binding, "assembly_config": value["assembly_config"]},
        config_key="assembly_config",
    ):
        raise DeferredApiParityError(f"Packet selection identity binding is invalid: {question_id}")
    contract = value.pop(namespace, None)
    if not isinstance(contract, dict) or contract.get("identity") != expected_version:
        raise DeferredApiParityError(f"Packet contract namespace is invalid: {question_id}")
    if shadow:
        if contract.get("control_selection_policy") != comparison.V2_DIRECT_FIRST_CONTEXT_CAP.to_dict():
            raise DeferredApiParityError(f"historical shadow control policy is invalid: {question_id}")
        contract.pop("control_selection_policy")
    else:
        if "control_selection_policy" in contract:
            raise DeferredApiParityError(f"formal Packet has unexpected control policy metadata: {question_id}")
    contract.pop("identity")
    value["admission_contract"] = {"identity": _NORMALIZED_CONTRACT_IDENTITY, **contract}
    for block in value.get("evidence", []):
        if not isinstance(block, dict):
            raise DeferredApiParityError(f"Packet evidence block is invalid: {question_id}")
        for member in block.get("members", []):
            if not isinstance(member, dict) or member_namespace not in member or render_namespace not in member:
                raise DeferredApiParityError(f"Packet member namespace is invalid: {question_id}")
            other_membership = "admission_anchor_memberships" if shadow else "shadow_anchor_memberships"
            other_render = "admission_rendering" if shadow else "shadow_rendering"
            if other_membership in member or other_render in member:
                raise DeferredApiParityError(f"Packet contains mixed member namespaces: {question_id}")
            member["admission_anchor_memberships"] = member.pop(member_namespace)
            member["admission_rendering"] = member.pop(render_namespace)
    value.pop("assembly_version")
    value.pop("assembly_config_identity")
    value.pop("selection_policy")
    value.pop("selection_policy_identity")
    return value


def _normalize_trace(trace: Mapping[str, Any], *, shadow: bool, question_id: str) -> dict[str, Any]:
    value = json.loads(canonical_json_bytes(dict(trace)).decode("utf-8"))
    source_key = "shadow_contract" if shadow else "admission_contract"
    other_key = "admission_contract" if shadow else "shadow_contract"
    expected = DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY if shadow else DEFERRED_FOOTPRINT_CHARGE_POLICY
    if value.get(source_key) != expected or other_key in value:
        raise DeferredApiParityError(f"selection trace contract namespace is invalid: {question_id}")
    value.pop(source_key)
    value["admission_contract"] = _NORMALIZED_CONTRACT_IDENTITY
    return value


def _compare_question(
    frozen: Mapping[str, Any],
    historical_row: Mapping[str, Any],
    *,
    retrieval_unit_manifest_path: Path,
    prepared_context: Any,
    config: EvidenceAssemblyConfig,
) -> dict[str, Any]:
    record = frozen["record"]
    question_id = str(record["question_id"])
    candidates = frozen["hybrid_rows"]
    before_sha = comparison._candidate_sha(candidates)
    if before_sha != frozen.get("accepted_hybrid_sha256"):
        raise DeferredApiParityError(f"frozen candidate binding changed before assembly: {question_id}")
    diagnostics = EvidenceAssemblyDiagnostics()
    formal_packet = assemble_deferred_footprint_charge_packet(
        retrieval_unit_manifest_path,
        candidates,
        config=config,
        retrieval_audit={"query_id": question_id, "mode": "hybrid", "candidate_supply": "frozen_step0"},
        prepared_context=prepared_context,
        diagnostics=diagnostics,
    )
    after_sha = comparison._candidate_sha(candidates)
    if after_sha != before_sha:
        raise DeferredApiParityError(f"formal path mutated frozen candidates: {question_id}")
    _validate_formal_packet(formal_packet, question_id=question_id, config=config)
    historical_packet = historical_row.get("challenger_packet")
    historical_trace = historical_row.get("challenger_selection_trace")
    if not isinstance(historical_packet, Mapping) or not isinstance(historical_trace, Mapping):
        raise DeferredApiParityError(f"accepted historical challenger is incomplete: {question_id}")
    formal_projection, formal_projection_sha = comparison._evidence_visible_projection(formal_packet)
    accepted_challenger = historical_row.get("challenger")
    if not isinstance(accepted_challenger, Mapping):
        raise DeferredApiParityError(f"accepted historical challenger summary is incomplete: {question_id}")
    if (
        formal_projection != accepted_challenger.get("generation_visible_projection")
        or formal_projection_sha != accepted_challenger.get("generation_visible_projection_sha256")
    ):
        raise DeferredApiParityError(f"Generation-visible parity mismatch: {question_id}")
    normalized_formal = _normalize_packet(formal_packet, shadow=False, question_id=question_id)
    normalized_shadow = _normalize_packet(historical_packet, shadow=True, question_id=question_id)
    if normalized_formal != normalized_shadow:
        raise DeferredApiParityError(f"mechanical/accounting parity mismatch: {question_id}")
    formal_trace = diagnostics.selection_trace
    if not isinstance(formal_trace, Mapping):
        raise DeferredApiParityError(f"formal selection trace is missing: {question_id}")
    if _normalize_trace(formal_trace, shadow=False, question_id=question_id) != _normalize_trace(
        historical_trace, shadow=True, question_id=question_id
    ):
        raise DeferredApiParityError(f"selection/admission trace parity mismatch: {question_id}")
    return {
        "schema_version": SCHEMA_VERSION,
        "question_id": question_id,
        "question_identity": record["question_identity"],
        "frozen_input": {
            "step0_run_identity": record["run_identity"],
            "candidate_sha256": before_sha,
            "candidate_count": len(candidates),
        },
        "historical_comparison": {
            "run_identity": ACCEPTED_COMPARISON_RUN_IDENTITY,
            "challenger_policy": DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY,
            "generation_visible_projection_sha256": accepted_challenger["generation_visible_projection_sha256"],
        },
        "formal": {
            "packet_sha256": _file_sha256_from_bytes(comparison.evidence_packet_json_bytes(formal_packet)),
            "generation_visible_projection": formal_projection,
            "generation_visible_projection_sha256": formal_projection_sha,
        },
        "parity": {
            "generation_visible": True,
            "mechanical_accounting": True,
            "selection_trace": True,
        },
        "formal_packet": formal_packet,
        "formal_selection_trace": dict(formal_trace),
    }


def _validate_accepted_comparison(
    comparison_root: Path,
    *,
    supply_root: Path,
    supply_manifest: Mapping[str, Any],
    config: EvidenceAssemblyConfig,
) -> tuple[Mapping[str, Any], dict[str, Mapping[str, Any]]]:
    manifest_path = comparison_root / "metadata" / "manifest.json"
    if _file_sha256(manifest_path, "accepted comparison manifest") != ACCEPTED_COMPARISON_MANIFEST_SHA256:
        raise DeferredApiParityError("accepted comparison artifact manifest SHA-256 mismatch")
    manifest = _read_object(manifest_path, "accepted comparison manifest")
    expected = {
        "schema_version": comparison.SCHEMA_VERSION,
        "status": "complete",
        "run_identity": ACCEPTED_COMPARISON_RUN_IDENTITY,
        "supply_run_identity": supply_manifest["run_identity"],
        "external_hybrid_candidate_oracle_sha256": comparison.ACCEPTED_CANDIDATE_HASHES_SHA256,
        "retrieval_unit_build_identity": comparison.ACCEPTED_RU_BUILD_IDENTITY,
        "candidate_mode": "hybrid_top20",
        "question_ids": list(QUESTION_IDS),
        "completed_question_ids": list(QUESTION_IDS),
        "completed_question_count": 70,
        "assembly_config": _operating_config(config),
        "control_selection_policy": comparison.V2_DIRECT_FIRST_CONTEXT_CAP.to_dict(),
        "challenger_policy": DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY,
        "provider_calls": 0,
        "generation_calls": 0,
        "retrieval_calls": 0,
        "dense_query_encodings": 0,
        "rrf_recomputations": 0,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise DeferredApiParityError("accepted comparison manifest binding is invalid")
    descriptors = manifest.get("completed_artifacts")
    if not isinstance(descriptors, Mapping) or set(descriptors) != set(QUESTION_IDS):
        raise DeferredApiParityError("accepted comparison artifact ledger is incomplete")
    rows: dict[str, Mapping[str, Any]] = {}
    contract = comparison._comparison_contract({
        "run_identity": ACCEPTED_COMPARISON_RUN_IDENTITY,
        "assembly_config": _operating_config(config),
        "control_selection_policy": comparison.V2_DIRECT_FIRST_CONTEXT_CAP.to_dict(),
    })
    for question_id in QUESTION_IDS:
        path = comparison_root / "comparisons" / f"{question_id}.json"
        descriptor = descriptors.get(question_id)
        if not isinstance(descriptor, Mapping) or not path.is_file():
            raise DeferredApiParityError(f"accepted comparison artifact is missing: {question_id}")
        try:
            body = path.read_bytes()
        except OSError as exc:
            raise DeferredApiParityError(f"accepted comparison artifact is unreadable: {question_id}") from exc
        if descriptor.get("sha256") != _file_sha256_from_bytes(body) or descriptor.get("byte_count") != len(body):
            raise DeferredApiParityError(f"accepted comparison artifact descriptor mismatch: {question_id}")
        row = _read_object(path, "accepted comparison row")
        frozen = comparison._load_frozen_question(supply_root, question_id, supply_manifest)
        comparison._validate_comparison_row(row, question_id=question_id, frozen=frozen, contract=contract, config=config)
        rows[question_id] = row
    for kind, expected_path in {
        "aggregate": comparison_root / "metadata" / "aggregate.json",
        "non_identical_index": comparison_root / "index" / "non_identical.jsonl",
    }.items():
        descriptor = manifest.get("final_artifacts", {}).get(kind)
        if not isinstance(descriptor, Mapping) or not expected_path.is_file():
            raise DeferredApiParityError(f"accepted comparison final artifact is missing: {kind}")
        body = expected_path.read_bytes()
        if descriptor.get("sha256") != _file_sha256_from_bytes(body) or descriptor.get("byte_count") != len(body):
            raise DeferredApiParityError(f"accepted comparison final artifact mismatch: {kind}")
    expected_aggregate = comparison._aggregate([rows[question_id] for question_id in QUESTION_IDS])
    aggregate = _read_object(comparison_root / "metadata" / "aggregate.json", "accepted comparison aggregate")
    if aggregate != expected_aggregate:
        raise DeferredApiParityError("accepted comparison aggregate does not match its rows")
    expected_index = comparison._non_identical_index([rows[question_id] for question_id in QUESTION_IDS])
    if (comparison_root / "index" / "non_identical.jsonl").read_bytes() != expected_index:
        raise DeferredApiParityError("accepted comparison index does not match its rows")
    return manifest, rows


def _preflight(
    *,
    supply_root: Path,
    comparison_root: Path,
    retrieval_unit_manifest_path: Path,
) -> tuple[dict[str, Any], Mapping[str, Any], dict[str, Mapping[str, Any]], EvidenceAssemblyConfig]:
    source_bindings = _source_bindings()
    supply_manifest = comparison._validate_supply_manifest(supply_root)
    config = comparison._expected_config()
    if _file_sha256(supply_root / "metadata" / "manifest.json", "frozen supply manifest") != ACCEPTED_SUPPLY_MANIFEST_SHA256:
        raise DeferredApiParityError("frozen supply manifest SHA-256 mismatch")
    if _file_sha256(retrieval_unit_manifest_path, "accepted RU manifest") != ACCEPTED_RU_MANIFEST_SHA256:
        raise DeferredApiParityError("accepted RU manifest SHA-256 mismatch")
    ru_manifest = _read_object(retrieval_unit_manifest_path, "accepted Retrieval Unit manifest")
    if ru_manifest.get("status") != "complete" or ru_manifest.get("build_identity") != comparison.ACCEPTED_RU_BUILD_IDENTITY:
        raise DeferredApiParityError("accepted Retrieval Unit manifest binding is invalid")
    comparison_manifest, rows = _validate_accepted_comparison(
        comparison_root,
        supply_root=supply_root,
        supply_manifest=supply_manifest,
        config=config,
    )
    bindings = {
        **source_bindings,
        "supply_manifest_sha256": ACCEPTED_SUPPLY_MANIFEST_SHA256,
        "supply_run_identity": supply_manifest["run_identity"],
        "comparison_manifest_sha256": ACCEPTED_COMPARISON_MANIFEST_SHA256,
        "comparison_run_identity": comparison_manifest["run_identity"],
        "ru_manifest_sha256": ACCEPTED_RU_MANIFEST_SHA256,
        "retrieval_unit_build_identity": comparison.ACCEPTED_RU_BUILD_IDENTITY,
        "candidate_oracle_sha256": comparison.ACCEPTED_CANDIDATE_HASHES_SHA256,
        "assembly_config": _operating_config(config),
        "formal_policy": DEFERRED_FOOTPRINT_CHARGE_POLICY,
        "historical_shadow_policy": DEFERRED_FOOTPRINT_CHARGE_SHADOW_POLICY,
    }
    return bindings, supply_manifest, rows, config


def _run_identity(bindings: Mapping[str, Any]) -> str:
    return sha256_json({
        "schema_version": SCHEMA_VERSION,
        "bindings": dict(bindings),
        "question_ids": list(QUESTION_IDS),
        "counters": {
            "retrieval_calls": 0,
            "dense_query_encodings": 0,
            "rrf_recomputations": 0,
            "provider_calls": 0,
            "generation_calls": 0,
        },
    })


def _write_manifest(path: Path, value: Mapping[str, Any]) -> None:
    atomic_write(path, canonical_json_bytes(dict(value)))


def run_deferred_api_parity(
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    *,
    supply_root: Path = DEFAULT_SUPPLY_ROOT,
    comparison_root: Path = DEFAULT_COMPARISON_ROOT,
    retrieval_unit_manifest_path: Path = DEFAULT_RU_MANIFEST,
    resume: bool = False,
    progress: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run the formal-vs-accepted-shadow parity checker.

    The complete 70-question run is intentionally caller-controlled; this
    function performs no Retrieval, Dense, RRF, Provider, or Generation work.
    """

    output_root = Path(output_root)
    bindings, supply_manifest, historical_rows, config = _preflight(
        supply_root=Path(supply_root),
        comparison_root=Path(comparison_root),
        retrieval_unit_manifest_path=Path(retrieval_unit_manifest_path),
    )
    run_identity = _run_identity(bindings)
    manifest_path = output_root / "metadata" / "manifest.json"
    contract = {
        "schema_version": SCHEMA_VERSION,
        "run_identity": run_identity,
        "bindings": bindings,
        "question_ids": list(QUESTION_IDS),
        "counters": {
            "retrieval_calls": 0,
            "dense_query_encodings": 0,
            "rrf_recomputations": 0,
            "provider_calls": 0,
            "generation_calls": 0,
        },
    }
    completed: list[str] = []
    descriptors: dict[str, Any] = {}
    if output_root.exists():
        if not resume:
            raise DeferredApiParityError("parity output root already exists; use --resume only for a bound partial run")
        existing = _read_object(manifest_path, "existing parity manifest")
        if existing.get("status") == "complete":
            raise DeferredApiParityError("parity output is complete; refusing to overwrite or resume it")
        if existing.get("status") not in {"partial", "failed"} or any(existing.get(k) != v for k, v in contract.items()):
            raise DeferredApiParityError("existing parity output is not bound to the exact source and inputs")
        raw_completed = existing.get("completed_question_ids")
        raw_descriptors = existing.get("completed_artifacts")
        if (
            not isinstance(raw_completed, list)
            or raw_completed != list(QUESTION_IDS[: len(raw_completed)])
            or not isinstance(raw_descriptors, Mapping)
            or set(raw_descriptors) != set(raw_completed)
        ):
            raise DeferredApiParityError("existing parity recovery ledger is invalid")
        artifact_ids = {path.stem for path in (output_root / "parity").glob("*.json")}
        if artifact_ids != set(raw_completed):
            raise DeferredApiParityError("existing parity artifacts do not match the recovery ledger")
        for question_id in raw_completed:
            path = output_root / "parity" / f"{question_id}.json"
            body = path.read_bytes()
            descriptor = raw_descriptors[question_id]
            if descriptor != _descriptor(path, body):
                raise DeferredApiParityError(f"existing parity artifact integrity failed: {question_id}")
        completed = list(raw_completed)
        descriptors = dict(raw_descriptors)
    else:
        output_root.mkdir(parents=True)
    manifest: dict[str, Any] = {
        **contract,
        "status": "partial",
        "completed_question_ids": completed,
        "completed_question_count": len(completed),
        "completed_artifacts": descriptors,
    }
    _write_manifest(manifest_path, manifest)
    try:
        prepared_context = prepare_evidence_assembly_context(retrieval_unit_manifest_path)
        if prepared_context.retrieval_unit_build_identity != comparison.ACCEPTED_RU_BUILD_IDENTITY:
            raise DeferredApiParityError("prepared RU context does not match the accepted binding")
        for question_id in QUESTION_IDS:
            if question_id in completed:
                continue
            frozen = comparison._load_frozen_question(Path(supply_root), question_id, supply_manifest)
            row = _compare_question(
                frozen,
                historical_rows[question_id],
                retrieval_unit_manifest_path=Path(retrieval_unit_manifest_path),
                prepared_context=prepared_context,
                config=config,
            )
            body = canonical_json_bytes(row)
            artifact = _immutable_write(output_root / "parity" / f"{question_id}.json", body)
            completed.append(question_id)
            descriptors[question_id] = artifact
            manifest["completed_question_ids"] = list(completed)
            manifest["completed_question_count"] = len(completed)
            manifest["completed_artifacts"] = descriptors
            _write_manifest(manifest_path, manifest)
            if progress is not None:
                progress({"event": "question_complete", "question_id": question_id, "processed": len(completed), "total": 70})
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["completed_question_ids"] = list(completed)
        manifest["completed_question_count"] = len(completed)
        manifest["completed_artifacts"] = descriptors
        manifest["failure"] = {"reason": type(exc).__name__, "message": str(exc)}
        _write_manifest(manifest_path, manifest)
        if isinstance(exc, DeferredApiParityError):
            raise
        raise DeferredApiParityError("provider-free Deferred parity stopped; partial artifacts remain inspectable") from exc
    if completed != list(QUESTION_IDS):
        raise DeferredApiParityError("parity ended without Q001-Q070")
    manifest["status"] = "complete"
    manifest.pop("failure", None)
    _write_manifest(manifest_path, manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check formal Deferred Assembly parity against the accepted shadow")
    parser.add_argument("--supply-root", type=Path, default=DEFAULT_SUPPLY_ROOT)
    parser.add_argument("--comparison-root", type=Path, default=DEFAULT_COMPARISON_ROOT)
    parser.add_argument("--ru-manifest", type=Path, default=DEFAULT_RU_MANIFEST)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    result = run_deferred_api_parity(
        args.output_root,
        supply_root=args.supply_root,
        comparison_root=args.comparison_root,
        retrieval_unit_manifest_path=args.ru_manifest,
        resume=args.resume,
        progress=lambda value: print(canonical_json_bytes(dict(value)).decode("utf-8"), flush=True),
    )
    print(canonical_json_bytes(result).decode("utf-8"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
