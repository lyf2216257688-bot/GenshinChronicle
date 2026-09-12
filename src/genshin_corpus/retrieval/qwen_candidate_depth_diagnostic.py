"""Provider-free Qwen candidate-depth diagnostic for the accepted five-question slice."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from typing import Any

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write

from . import broader_admission_comparison as comparison
from .bge_qwen_dense_comparison import (
    ACCEPTED_QWEN_CORPUS_ARM_BUILD_IDENTITY,
    ACCEPTED_QWEN_CORPUS_ROWS_SHA256,
    ACCEPTED_QWEN_CORPUS_VECTORS_SHA256,
    ACCEPTED_QWEN_QUERY_ARTIFACT_IDENTITY,
    ACCEPTED_QWEN_QUERY_VECTORS_SHA256,
)
from .candidate_retrieval import load_batch_candidate_retriever
from .evidence_assembly import (
    DEFERRED_FOOTPRINT_CHARGE_POLICY,
    EvidenceAssemblyConfig,
    EvidenceAssemblyDiagnostics,
    assemble_deferred_footprint_charge_packet,
    evidence_packet_json_bytes,
    prepare_evidence_assembly_context,
)
from .qwen_m2_query_vectors import load_qwen_m2_query_vectors
from .retrieval_units import load_retrieval_units


SCHEMA_VERSION = "p04-qwen-candidate-depth-diagnostic-0.1"
ACCEPTED_SOURCE_COMMIT = "9b0fff07693d336714e39082ffe91928f0b743d2"
EXPECTED_BRANCH = "main"
QUESTION_IDS = ("Q005", "Q011", "Q045", "Q046", "Q049")
DEPTHS = (20, 50, 100, 200, 250, 500)
DIAGNOSTIC_OWNED_PATHS = frozenset(
    {
        "src/genshin_corpus/retrieval/qwen_candidate_depth_diagnostic.py",
        "tests/retrieval/test_qwen_candidate_depth_diagnostic.py",
    }
)
DIAGNOSTIC_ALLOWED_COMMITTED_PATHS = DIAGNOSTIC_OWNED_PATHS | {"docs/current-phase.md"}

DEFAULT_RU_MANIFEST = Path(
    "data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/ru/metadata/manifest.json"
)
DEFAULT_LEXICAL_MANIFEST = Path(
    "data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/lexical/metadata/manifest.json"
)
DEFAULT_QWEN_DENSE_MANIFEST = Path(
    ".local/p04-qwen-full-batch-beijing-20260909/final/dense/metadata/manifest.json"
)
DEFAULT_QWEN_QUERY_ROOT = Path(".local/p04-qwen-m2-query-vectors-beijing-20260910-162427")
DEFAULT_AUDIT_PATH = Path(".local/p04-rag-qwen-16q-failure-attribution-final-20260911/audit_final.json")
DEFAULT_Q011_CORRECTION_PATH = Path(
    ".local/p04-rag-qwen-q011-formal-deferred-oracle-20260912/q011_prose_correction_sidecar.json"
)
DEFAULT_REFERENCE_ROOT = Path(".local/p04-rag-bge-qwen-dense-comparison-70q-20260910-172843")

ACCEPTED_AUDIT_SHA256 = "b3cfced5f2e40335c05e51d63b3426e9fa168cae95997f4452eda8ce16cd4188"
ACCEPTED_Q011_CORRECTION_SHA256 = "f1aa42f75ab2ed737ce30157ee123b37f033451c7e48b92c87043f4f5456ac39"
ACCEPTED_REFERENCE_RUN_IDENTITY = "f15ffb2c7f213e429cd962e53b34c2cc36c22278623377faa2e632586d8840f8"
ACCEPTED_RU_BUILD_IDENTITY = "49b48ee746716add0248fed388d10bd522a930efb582a0f5e827f66681ed8998"


class CandidateDepthDiagnosticError(ValueError):
    """Raised when the diagnostic cannot maintain its accepted bindings."""


def _sha256_file(path: Path) -> str:
    try:
        digest = sha256()
        with Path(path).open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise CandidateDepthDiagnosticError(f"unreadable artifact: {path}") from exc


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateDepthDiagnosticError(f"{label} is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise CandidateDepthDiagnosticError(f"{label} must be a JSON object")
    return value


def _descriptor(path: Path, body: bytes | None = None) -> dict[str, Any]:
    payload = Path(path).read_bytes() if body is None else body
    return {"path": str(path), "sha256": sha256(payload).hexdigest(), "byte_count": len(payload)}


def _immutable_write(path: Path, body: bytes) -> dict[str, Any]:
    path = Path(path)
    if path.exists():
        if path.read_bytes() != body:
            raise CandidateDepthDiagnosticError(f"refusing to overwrite different diagnostic artifact: {path}")
    else:
        atomic_write(path, body)
    return _descriptor(path, body)


def _write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    atomic_write(Path(path), canonical_json_bytes(dict(manifest)))


def _git_output(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CandidateDepthDiagnosticError(f"git baseline check failed: {' '.join(args)}") from exc
    return result.stdout.strip()


def _git_status_paths(status: str) -> list[str]:
    paths: list[str] = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        paths.append(path.replace("\\", "/"))
    return paths


def _diagnostic_owned_file_descriptors() -> list[dict[str, Any]]:
    descriptors: list[dict[str, Any]] = []
    for relative_path in sorted(DIAGNOSTIC_OWNED_PATHS):
        path = Path(relative_path)
        if not path.is_file():
            raise CandidateDepthDiagnosticError(f"diagnostic-owned file is missing: {relative_path}")
        descriptor = _descriptor(path)
        descriptor["path"] = relative_path
        descriptors.append(descriptor)
    return descriptors


def _baseline_state() -> dict[str, Any]:
    branch = _git_output("branch", "--show-current")
    head = _git_output("rev-parse", "HEAD")
    changed = [line for line in _git_output("diff", "--name-only", f"{ACCEPTED_SOURCE_COMMIT}..HEAD").splitlines() if line]
    status = _git_output("status", "--porcelain=v1", "--untracked-files=all")
    normalized_changed = [path.replace("\\", "/") for path in changed]
    worktree_paths = _git_status_paths(status)
    source_changed = [
        path for path in normalized_changed
        if (path.startswith("src/") or path.startswith("tests/")) and path not in DIAGNOSTIC_OWNED_PATHS
    ]
    worktree_source_changed = [
        path for path in worktree_paths
        if (path.startswith("src/") or path.startswith("tests/")) and path not in DIAGNOSTIC_OWNED_PATHS
    ]
    unexpected_committed = [path for path in normalized_changed if path not in DIAGNOSTIC_ALLOWED_COMMITTED_PATHS]
    if branch != EXPECTED_BRANCH:
        raise CandidateDepthDiagnosticError(f"diagnostic requires branch {EXPECTED_BRANCH}, got {branch!r}")
    if source_changed or worktree_source_changed:
        raise CandidateDepthDiagnosticError("retrieval/test source differs from the accepted Qwen baseline")
    if unexpected_committed:
        raise CandidateDepthDiagnosticError(
            f"accepted baseline has unexpected committed paths: {unexpected_committed}"
        )
    owned_files = _diagnostic_owned_file_descriptors()
    return {
        "expected_branch": EXPECTED_BRANCH,
        "expected_source_commit": ACCEPTED_SOURCE_COMMIT,
        "actual_branch": branch,
        "actual_head": head,
        "accepted_to_head_changed_paths": normalized_changed,
        "source_or_test_delta_from_accepted": source_changed,
        "worktree_source_or_test_delta": worktree_source_changed,
        "tracked_worktree_porcelain": status,
        "diagnostic_owned_files": owned_files,
    }


def _config_projection(config: EvidenceAssemblyConfig) -> dict[str, int]:
    # Match the accepted Formal Deferred comparison projection exactly. The
    # internal max_evidence_blocks field is not part of that packet identity.
    return {
        "neighbor_before": config.neighbor_before,
        "neighbor_after": config.neighbor_after,
        "structured_neighbor_before": config.structured_neighbor_before,
        "structured_neighbor_after": config.structured_neighbor_after,
        "dialogue_hops": config.dialogue_hops,
        "per_block_chars": config.per_block_chars,
        "total_context_chars": config.total_context_chars,
    }


def _expected_config() -> EvidenceAssemblyConfig:
    config = EvidenceAssemblyConfig()
    if config.total_context_chars != 12000 or config.per_block_chars != 3000:
        raise CandidateDepthDiagnosticError("accepted Formal Deferred B0 configuration is unavailable")
    return config


def _carrier_manifest(
    audit: Mapping[str, Any],
    correction: Mapping[str, Any],
    units_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    questions = audit.get("questions")
    if not isinstance(questions, Mapping):
        raise CandidateDepthDiagnosticError("accepted 16Q audit lacks question records")
    correction_rows = correction.get("accepted_rank_evidence")
    if correction.get("schema_version") != "p04-rag-qwen-16q-q011-prose-correction-0.1" or not isinstance(correction_rows, list):
        raise CandidateDepthDiagnosticError("Q011 correction sidecar has an unsupported schema")
    q011_ranks: dict[str, int] = {}
    for row in correction_rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("unit_id"), str) or not isinstance(row.get("qwen_dense_exact_rank"), int):
            raise CandidateDepthDiagnosticError("Q011 correction sidecar has an invalid rank row")
        q011_ranks[str(row["unit_id"])] = int(row["qwen_dense_exact_rank"])
    if sorted(q011_ranks.values()) != [112, 188]:
        raise CandidateDepthDiagnosticError("Q011 correction does not bind exact Qwen Dense ranks 112 and 188")

    manifest_questions: list[dict[str, Any]] = []
    for question_id in QUESTION_IDS:
        question = questions.get(question_id)
        if not isinstance(question, Mapping):
            raise CandidateDepthDiagnosticError(f"accepted 16Q audit lacks {question_id}")
        question_identity = question.get("question_identity")
        carriers = question.get("carriers")
        if not isinstance(question_identity, str) or not isinstance(carriers, list) or not carriers:
            raise CandidateDepthDiagnosticError(f"accepted 16Q audit lacks bound carriers for {question_id}")
        normalized: list[dict[str, Any]] = []
        for carrier in carriers:
            if not isinstance(carrier, Mapping):
                raise CandidateDepthDiagnosticError(f"accepted carrier is invalid: {question_id}")
            unit_id = carrier.get("unit_id")
            provenance = carrier.get("provenance")
            if (
                not isinstance(unit_id, str)
                or carrier.get("verification") != "mechanically_verified_answer_bearing"
                or not isinstance(provenance, Mapping)
                or unit_id not in units_by_id
            ):
                raise CandidateDepthDiagnosticError(f"accepted carrier cannot bind to the RU snapshot: {question_id}")
            ru_unit = units_by_id[unit_id]
            source_identity_key = provenance.get("source_identity_key")
            source_order = provenance.get("source_order")
            if not isinstance(source_identity_key, str) or not isinstance(source_order, list):
                raise CandidateDepthDiagnosticError(f"accepted carrier lacks provenance binding: {question_id}")
            # The accepted RU build is the authoritative lineage snapshot. Its id binding is
            # mandatory; audit provenance remains retained without reinterpreting source text.
            normalized.append({
                "unit_id": unit_id,
                "verification": carrier["verification"],
                "audit_provenance": {
                    "source_identity_key": source_identity_key,
                    "source_order": list(source_order),
                },
                "accepted_rank_evidence": {
                    "lexical_exact_rank": carrier.get("lexical_exact_rank_prior_accepted"),
                    "qwen_dense_exact_rank": (
                        q011_ranks[unit_id] if question_id == "Q011" else carrier.get("qwen_dense_exact_rank")
                    ),
                },
                "ru_lineage_binding": {
                    "unit_id": str(ru_unit.get("unit_id")),
                    "source_identity_key": source_identity_key,
                    "source_order": list(source_order),
                },
            })
        if question_id == "Q011" and {row["unit_id"] for row in normalized} != set(q011_ranks):
            raise CandidateDepthDiagnosticError("Q011 correction carrier identities do not match accepted audit carriers")
        manifest_questions.append({
            "question_id": question_id,
            "question_identity": question_identity,
            "carrier_count": len(normalized),
            "carriers": normalized,
        })
    return {
        "schema_version": "p04-qwen-candidate-depth-carriers-0.1",
        "source_audit": {"path": str(DEFAULT_AUDIT_PATH), "sha256": ACCEPTED_AUDIT_SHA256},
        "q011_correction": {
            "path": str(DEFAULT_Q011_CORRECTION_PATH),
            "sha256": ACCEPTED_Q011_CORRECTION_SHA256,
            "applied_exact_dense_ranks": [112, 188],
            "obsolete_wording_excluded": "thousands-deep",
        },
        "retrieval_unit_build_identity": ACCEPTED_RU_BUILD_IDENTITY,
        "questions": manifest_questions,
    }


def _load_reference(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = _read_object(root / "metadata" / "manifest.json", "accepted Qwen comparison manifest")
    if manifest.get("status") != "complete" or manifest.get("run_identity") != ACCEPTED_REFERENCE_RUN_IDENTITY:
        raise CandidateDepthDiagnosticError("accepted Qwen comparison manifest does not match the corrected run")
    references: dict[str, dict[str, Any]] = {}
    for question_id in QUESTION_IDS:
        row = _read_object(root / "comparisons" / f"{question_id}.json", "accepted Qwen comparison row")
        frozen = row.get("frozen_bge_input")
        qwen = row.get("qwen")
        packet = row.get("qwen_packet")
        if not isinstance(frozen, Mapping) or not isinstance(qwen, Mapping) or not isinstance(packet, Mapping):
            raise CandidateDepthDiagnosticError(f"accepted Qwen comparison lacks Top20 bindings: {question_id}")
        summary = packet.get("packet_summary")
        if not isinstance(summary, Mapping):
            raise CandidateDepthDiagnosticError(f"accepted Qwen comparison lacks Packet summary: {question_id}")
        references[question_id] = {
            "lexical_candidate_sha256": frozen.get("lexical_candidate_sha256"),
            "dense_candidate_sha256": qwen.get("dense_candidate_sha256"),
            "hybrid_candidate_sha256": qwen.get("hybrid_candidate_sha256"),
            "packet_sha256": summary.get("packet_sha256"),
            "generation_visible_projection_sha256": summary.get("generation_visible_projection_sha256"),
            "visible_unit_ids": summary.get("visible_unit_ids"),
        }
        if any(not isinstance(value, str) or len(value) != 64 for key, value in references[question_id].items() if key.endswith("sha256")):
            raise CandidateDepthDiagnosticError(f"accepted Qwen comparison hashes are invalid: {question_id}")
        if not isinstance(references[question_id]["visible_unit_ids"], list):
            raise CandidateDepthDiagnosticError(f"accepted Qwen comparison visible list is invalid: {question_id}")
    return manifest, references


def _formal_displacements(packet: Mapping[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    budget = packet.get("budget")
    if isinstance(budget, Mapping):
        omitted = budget.get("omitted_blocks")
        if isinstance(omitted, list):
            for row in omitted:
                if isinstance(row, Mapping) and row.get("displaced_by_anchor_ids"):
                    records.append({
                        "source": "budget_omission",
                        "unit_ids": list(row.get("unit_ids", [])),
                        "reason": row.get("reason"),
                        "displaced_by_anchor_ids": list(row["displaced_by_anchor_ids"]),
                    })
    contract = packet.get("admission_contract")
    if isinstance(contract, Mapping):
        for root in contract.get("direct_footprints", []):
            if not isinstance(root, Mapping):
                continue
            if root.get("reason") is not None:
                records.append({
                    "source": "direct_root",
                    "unit_ids": [root.get("anchor_unit_id")],
                    "reason": root.get("reason"),
                    "displaced_by_anchor_ids": list(root.get("displaced_by_anchor_ids", [])),
                })
            for part in root.get("deferred_footprint_parts", []):
                if isinstance(part, Mapping) and part.get("reason") is not None:
                    records.append({
                        "source": "deferred_footprint",
                        "unit_ids": list(part.get("unit_ids", [])),
                        "reason": part.get("reason"),
                        "displaced_by_anchor_ids": list(part.get("displaced_by_anchor_ids", [])),
                    })
    return records


def _direct_root_ids(packet: Mapping[str, Any]) -> list[str]:
    contract = packet.get("admission_contract")
    if not isinstance(contract, Mapping):
        raise CandidateDepthDiagnosticError("Formal Deferred Packet lacks admission contract")
    roots = contract.get("direct_footprints")
    if not isinstance(roots, list):
        raise CandidateDepthDiagnosticError("Formal Deferred Packet lacks direct-footprint audit")
    return [str(row["anchor_unit_id"]) for row in roots if isinstance(row, Mapping) and row.get("outcome") == "admitted"]


def _carrier_outcomes(carriers: Sequence[Mapping[str, Any]], windows: Mapping[str, Sequence[Mapping[str, Any]]], visible_ids: Sequence[str], direct_root_ids: Sequence[str], first_seen: dict[str, dict[str, int | None]], depth: int) -> list[dict[str, Any]]:
    ranks = {
        mode: {str(row["unit_id"]): int(row["rank"]) for row in rows}
        for mode, rows in windows.items()
    }
    visible, direct = set(visible_ids), set(direct_root_ids)
    outcomes: list[dict[str, Any]] = []
    for carrier in carriers:
        unit_id = str(carrier["unit_id"])
        state = first_seen.setdefault(unit_id, {"lexical": None, "dense": None, "hybrid": None, "packet": None})
        row_ranks = {mode: ranks[mode].get(unit_id) for mode in ("lexical", "dense", "hybrid")}
        for mode, rank in row_ranks.items():
            if rank is not None and state[mode] is None:
                state[mode] = depth
        if unit_id in visible and state["packet"] is None:
            state["packet"] = depth
        outcomes.append({
            "unit_id": unit_id,
            "lexical_rank": row_ranks["lexical"],
            "dense_rank": row_ranks["dense"],
            "hybrid_rank": row_ranks["hybrid"],
            "packet_visible": unit_id in visible,
            "direct_root_visible": unit_id in direct,
            "first_exposure_depth": dict(state),
        })
    return outcomes


def _candidate_hashes(windows: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, str]:
    return {mode: comparison._candidate_sha(rows) for mode, rows in windows.items()}


def _validate_top20(question_id: str, hashes: Mapping[str, str], packet_summary: Mapping[str, Any], reference: Mapping[str, Any]) -> None:
    expected = {
        "lexical": reference["lexical_candidate_sha256"],
        "dense": reference["dense_candidate_sha256"],
        "hybrid": reference["hybrid_candidate_sha256"],
    }
    if dict(hashes) != expected:
        raise CandidateDepthDiagnosticError(f"Top20 candidate replay differs from accepted Qwen comparison: {question_id}")
    for key in ("packet_sha256", "generation_visible_projection_sha256", "visible_unit_ids"):
        if packet_summary.get(key) != reference.get(key):
            raise CandidateDepthDiagnosticError(f"Top20 Formal Deferred replay differs from accepted Qwen comparison: {question_id} {key}")


def _preflight() -> dict[str, Any]:
    baseline = _baseline_state()
    audit_path, correction_path = DEFAULT_AUDIT_PATH, DEFAULT_Q011_CORRECTION_PATH
    if _sha256_file(audit_path) != ACCEPTED_AUDIT_SHA256:
        raise CandidateDepthDiagnosticError("accepted 16Q audit SHA-256 mismatch")
    if _sha256_file(correction_path) != ACCEPTED_Q011_CORRECTION_SHA256:
        raise CandidateDepthDiagnosticError("Q011 correction sidecar SHA-256 mismatch")
    audit, correction = _read_object(audit_path, "accepted 16Q audit"), _read_object(correction_path, "Q011 correction sidecar")
    ru_manifest, units = load_retrieval_units(DEFAULT_RU_MANIFEST)
    if ru_manifest.get("status") != "complete" or ru_manifest.get("build_identity") != ACCEPTED_RU_BUILD_IDENTITY:
        raise CandidateDepthDiagnosticError("accepted Retrieval Unit manifest does not match the Qwen baseline")
    units_by_id = {str(unit.get("unit_id")): unit for unit in units if isinstance(unit, Mapping) and isinstance(unit.get("unit_id"), str)}
    if len(units_by_id) != len(units):
        raise CandidateDepthDiagnosticError("accepted Retrieval Unit snapshot has invalid unit identities")
    carriers = _carrier_manifest(audit, correction, units_by_id)
    reference_manifest, references = _load_reference(DEFAULT_REFERENCE_ROOT)
    question_rows, query_vectors, query_manifest = load_qwen_m2_query_vectors(DEFAULT_QWEN_QUERY_ROOT)
    if query_manifest.get("artifact_identity") != ACCEPTED_QWEN_QUERY_ARTIFACT_IDENTITY:
        raise CandidateDepthDiagnosticError("Qwen query-vector artifact identity mismatch")
    vectors = query_manifest.get("artifacts", {}).get("vectors")
    if not isinstance(vectors, Mapping) or vectors.get("sha256") != ACCEPTED_QWEN_QUERY_VECTORS_SHA256:
        raise CandidateDepthDiagnosticError("Qwen query-vector SHA-256 mismatch")
    questions_by_id = {str(row.get("question_id")): row for row in question_rows if isinstance(row, Mapping) and isinstance(row.get("question_id"), str)}
    if set(QUESTION_IDS) - set(questions_by_id):
        raise CandidateDepthDiagnosticError("persisted Qwen query vectors lack one or more diagnostic questions")
    if getattr(query_vectors, "shape", None) != (70, 2048):
        raise CandidateDepthDiagnosticError("persisted Qwen query vector shape is not the accepted 70x2048 artifact")
    config = _expected_config()
    retriever = load_batch_candidate_retriever(
        DEFAULT_LEXICAL_MANIFEST,
        DEFAULT_QWEN_DENSE_MANIFEST,
        accepted_qwen=True,
    )
    if retriever.lexical_manifest.get("retrieval_unit_build_identity") != ACCEPTED_RU_BUILD_IDENTITY:
        raise CandidateDepthDiagnosticError("lexical index does not bind the accepted RU snapshot")
    if retriever.dense_manifest.get("arm_build_identity") != ACCEPTED_QWEN_CORPUS_ARM_BUILD_IDENTITY:
        raise CandidateDepthDiagnosticError("Qwen Dense arm identity mismatch")
    return {
        "baseline": baseline,
        "carrier_manifest": carriers,
        "reference_manifest": reference_manifest,
        "references": references,
        "question_rows": questions_by_id,
        "query_vectors": query_vectors,
        "query_manifest": query_manifest,
        "retriever": retriever,
        "config": config,
        "input_descriptors": {
            "retrieval_unit_manifest": _descriptor(DEFAULT_RU_MANIFEST),
            "lexical_manifest": _descriptor(DEFAULT_LEXICAL_MANIFEST),
            "qwen_dense_manifest": _descriptor(DEFAULT_QWEN_DENSE_MANIFEST),
            "qwen_query_manifest": _descriptor(DEFAULT_QWEN_QUERY_ROOT / "metadata" / "manifest.json"),
            "accepted_audit": _descriptor(audit_path),
            "q011_correction": _descriptor(correction_path),
            "accepted_qwen_comparison_manifest": _descriptor(DEFAULT_REFERENCE_ROOT / "metadata" / "manifest.json"),
        },
    }


def _run_identity(preflight: Mapping[str, Any]) -> str:
    return sha256_json({
        "schema_version": SCHEMA_VERSION,
        "accepted_source_commit": ACCEPTED_SOURCE_COMMIT,
        "baseline": preflight["baseline"],
        "question_ids": list(QUESTION_IDS),
        "depths": list(DEPTHS),
        "carrier_manifest": preflight["carrier_manifest"],
        "inputs": preflight["input_descriptors"],
        "qwen_query_artifact_identity": preflight["query_manifest"]["artifact_identity"],
        "qwen_query_vectors_sha256": ACCEPTED_QWEN_QUERY_VECTORS_SHA256,
        "qwen_dense_arm_build_identity": ACCEPTED_QWEN_CORPUS_ARM_BUILD_IDENTITY,
        "qwen_dense_vectors_sha256": ACCEPTED_QWEN_CORPUS_VECTORS_SHA256,
        "qwen_dense_rows_sha256": ACCEPTED_QWEN_CORPUS_ROWS_SHA256,
        "retrieval_unit_build_identity": ACCEPTED_RU_BUILD_IDENTITY,
        "bm25": {"k1": 1.2, "b": 0.75},
        "rrf_k": 60,
        "formal_deferred_policy": DEFERRED_FOOTPRINT_CHARGE_POLICY,
        "assembly_config": _config_projection(preflight["config"]),
        "diagnostic_owned_files": preflight["baseline"]["diagnostic_owned_files"],
        "runner_source_sha256": _sha256_file(Path(__file__)),
        "provider_calls": 0,
        "generation_calls": 0,
        "embedding_calls": 0,
    })


def _existing_completed(output_root: Path, run_identity: str) -> tuple[dict[str, Any], set[str]]:
    manifest = _read_object(output_root / "metadata" / "manifest.json", "existing candidate-depth manifest")
    if manifest.get("run_identity") != run_identity:
        raise CandidateDepthDiagnosticError("existing candidate-depth root has a different run identity")
    if manifest.get("status") not in {"in_progress", "failed"}:
        raise CandidateDepthDiagnosticError("existing candidate-depth root is not a resumable partial run")
    descriptors = manifest.get("completed_artifacts")
    if not isinstance(descriptors, Mapping):
        raise CandidateDepthDiagnosticError("existing candidate-depth completion ledger is invalid")
    complete: set[str] = set()
    expected_keys = [f"{question_id}:{depth:03d}" for question_id in QUESTION_IDS for depth in DEPTHS]
    for key, artifact in descriptors.items():
        if not isinstance(key, str) or not isinstance(artifact, Mapping):
            raise CandidateDepthDiagnosticError("existing candidate-depth descriptor is invalid")
        for name in ("record", "packet"):
            descriptor = artifact.get(name)
            if not isinstance(descriptor, Mapping) or not isinstance(descriptor.get("path"), str) or not isinstance(descriptor.get("sha256"), str):
                raise CandidateDepthDiagnosticError("existing candidate-depth descriptor is incomplete")
            path = Path(descriptor["path"])
            if not path.is_file() or _sha256_file(path) != descriptor["sha256"]:
                raise CandidateDepthDiagnosticError("existing candidate-depth artifact failed integrity validation")
        complete.add(key)
    unknown = complete - set(expected_keys)
    if unknown:
        raise CandidateDepthDiagnosticError(f"existing candidate-depth completion ledger has unknown keys: {sorted(unknown)}")
    completed_indexes = sorted(expected_keys.index(key) for key in complete)
    if completed_indexes and completed_indexes != list(range(len(completed_indexes))):
        raise CandidateDepthDiagnosticError("existing candidate-depth completion ledger has a gap")
    return manifest, complete


def _restore_completed_state(
    result: Mapping[str, Any],
    carriers: Sequence[Mapping[str, Any]],
    first_seen: dict[str, dict[str, int | None]],
    top20_visible: dict[str, list[str]],
) -> None:
    question_id = result.get("question_id")
    depth = result.get("depth")
    if not isinstance(question_id, str) or not isinstance(depth, int) or depth not in DEPTHS:
        raise CandidateDepthDiagnosticError("completed result has an invalid question/depth binding")
    windows = result.get("candidate_windows")
    summary = result.get("packet_summary")
    if not isinstance(windows, Mapping) or not isinstance(summary, Mapping):
        raise CandidateDepthDiagnosticError(f"completed result lacks replay state: {question_id} Top{depth}")
    visible_ids = summary.get("visible_unit_ids")
    direct_root_ids = summary.get("direct_root_ids")
    if not isinstance(visible_ids, list) or not isinstance(direct_root_ids, list):
        raise CandidateDepthDiagnosticError(f"completed result lacks Packet visibility state: {question_id} Top{depth}")
    if depth == 20:
        top20_visible[question_id] = list(visible_ids)
    elif question_id not in top20_visible:
        raise CandidateDepthDiagnosticError(f"completed result lacks Top20 baseline: {question_id} Top{depth}")
    derived = _carrier_outcomes(carriers, windows, visible_ids, direct_root_ids, first_seen, depth)
    if result.get("carrier_outcomes") != derived:
        raise CandidateDepthDiagnosticError(f"completed result carrier state failed replay validation: {question_id} Top{depth}")
    if result.get("complete_carrier_set_hybrid_exposed") != all(row["hybrid_rank"] is not None for row in derived):
        raise CandidateDepthDiagnosticError(f"completed result Hybrid completion flag failed validation: {question_id} Top{depth}")
    if result.get("complete_carrier_set_packet_visible") != all(row["packet_visible"] for row in derived):
        raise CandidateDepthDiagnosticError(f"completed result Packet completion flag failed validation: {question_id} Top{depth}")


def _aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    per_question: list[dict[str, Any]] = []
    for question_id in QUESTION_IDS:
        question_rows = [row for row in rows if row["question_id"] == question_id]
        candidate_depth = next((int(row["depth"]) for row in question_rows if row["complete_carrier_set_hybrid_exposed"]), None)
        packet_depth = next((int(row["depth"]) for row in question_rows if row["complete_carrier_set_packet_visible"]), None)
        if candidate_depth is None:
            classification = "candidate_depth_insufficient"
        elif packet_depth is None:
            classification = "candidate_depth_partially_sufficient_downstream_packet_limited"
        else:
            classification = "candidate_depth_sufficient_with_downstream_visibility"
        per_question.append({
            "question_id": question_id,
            "smallest_complete_hybrid_exposure_depth": candidate_depth,
            "smallest_complete_packet_visibility_depth": packet_depth,
            "classification": classification,
        })
    classes = Counter(row["classification"] for row in per_question)
    return {
        "question_count": len(per_question),
        "depths": list(DEPTHS),
        "per_question": per_question,
        "classification_counts": dict(sorted(classes.items())),
        "candidate_depth_sufficient_question_ids": [row["question_id"] for row in per_question if row["smallest_complete_hybrid_exposure_depth"] is not None],
        "downstream_packet_visible_question_ids": [row["question_id"] for row in per_question if row["smallest_complete_packet_visibility_depth"] is not None],
        "reranker_question_remains_justified_question_ids": [
            row["question_id"] for row in per_question
            if row["smallest_complete_hybrid_exposure_depth"] is not None and row["smallest_complete_packet_visibility_depth"] is None
        ],
        "ru_source_investigation_triggered": False,
        "scope_note": "Five-question attribution slice only; no prevalence, semantic-quality, answer-correctness, or production conclusion.",
    }


def run_qwen_candidate_depth_diagnostic(*, output_root: Path | None = None, resume: bool = False) -> dict[str, Any]:
    """Run the fixed provider-free depth ladder using only accepted Qwen artifacts."""

    preflight = _preflight()
    run_identity = _run_identity(preflight)
    output_root = Path(output_root) if output_root is not None else Path(f".local/p04-qwen-candidate-depth-{run_identity}")
    if output_root.exists():
        if not resume:
            raise FileExistsError("candidate-depth output root already exists; use --resume only for a verified partial run")
        manifest, completed = _existing_completed(output_root, run_identity)
    else:
        output_root.mkdir(parents=True)
        completed = set()
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "status": "in_progress",
            "run_identity": run_identity,
            "baseline": preflight["baseline"],
            "question_ids": list(QUESTION_IDS),
            "depth_ladder": list(DEPTHS),
            "config_snapshot": {
                "bm25": {"k1": 1.2, "b": 0.75},
                "rrf_k": 60,
                "formal_deferred_policy": DEFERRED_FOOTPRINT_CHARGE_POLICY,
                "assembly_config": _config_projection(preflight["config"]),
                "input_descriptors": preflight["input_descriptors"],
                "diagnostic_owned_files": preflight["baseline"]["diagnostic_owned_files"],
            },
            "execution_counters": {
                "provider_calls": 0,
                "generation_calls": 0,
                "embedding_calls": 0,
                "candidate_retrievals": 0,
                "formal_deferred_assemblies": 0,
            },
            "completed_artifacts": {},
        }
        carrier_body = canonical_json_bytes(preflight["carrier_manifest"])
        manifest["carrier_manifest"] = _immutable_write(output_root / "metadata" / "carrier_manifest.json", carrier_body)
        _write_manifest(output_root / "metadata" / "manifest.json", manifest)

    carrier_questions = {row["question_id"]: row for row in preflight["carrier_manifest"]["questions"]}
    vector_index = {str(row["question_id"]): index for index, row in enumerate(load_qwen_m2_query_vectors(DEFAULT_QWEN_QUERY_ROOT)[0])}
    context = prepare_evidence_assembly_context(DEFAULT_RU_MANIFEST)
    if context.retrieval_unit_build_identity != ACCEPTED_RU_BUILD_IDENTITY:
        raise CandidateDepthDiagnosticError("prepared Formal Deferred context does not match accepted RU binding")
    first_seen: dict[str, dict[str, int | None]] = {}
    top20_visible: dict[str, list[str]] = {}
    rows: list[dict[str, Any]] = []
    try:
        completed_rows: dict[str, dict[str, Any]] = {}
        for question_id in QUESTION_IDS:
            for depth in DEPTHS:
                key = f"{question_id}:{depth:03d}"
                if key in completed:
                    completed_rows[key] = _read_object(
                        output_root / "results" / f"{question_id}-top-{depth:03d}.json",
                        "existing candidate-depth result",
                    )
        for question_id in QUESTION_IDS:
            for depth in DEPTHS:
                key = f"{question_id}:{depth:03d}"
                if key not in completed_rows:
                    break
                result = completed_rows[key]
                if result.get("run_identity") != run_identity or result.get("question_id") != question_id or result.get("depth") != depth:
                    raise CandidateDepthDiagnosticError(f"completed result identity mismatch: {key}")
                _restore_completed_state(result, carrier_questions[question_id]["carriers"], first_seen, top20_visible)
                rows.append(result)
        for question_id in QUESTION_IDS:
            question = preflight["question_rows"][question_id]
            question_text = question.get("question")
            if not isinstance(question_text, str) or question.get("question_identity") != carrier_questions[question_id]["question_identity"]:
                raise CandidateDepthDiagnosticError(f"persisted Qwen question binding differs from accepted carrier manifest: {question_id}")
            carriers = carrier_questions[question_id]["carriers"]
            for depth in DEPTHS:
                key = f"{question_id}:{depth:03d}"
                if key in completed:
                    continue
                windows = preflight["retriever"].candidates_for_query(
                    question_text,
                    preflight["query_vectors"][vector_index[question_id]],
                    instruction=None,
                    top_k=depth,
                    k1=1.2,
                    b=0.75,
                    rrf_k=60,
                )
                hashes_before = _candidate_hashes(windows)
                diagnostics = EvidenceAssemblyDiagnostics()
                retrieval_audit = {
                    "query_id": question_id,
                    "mode": "hybrid",
                    "candidate_supply": "frozen_bge_vs_qwen" if depth == 20 else "qwen_candidate_depth_diagnostic",
                    "dense_arm": "qwen",
                }
                if depth != 20:
                    retrieval_audit["candidate_depth"] = depth
                packet = assemble_deferred_footprint_charge_packet(
                    DEFAULT_RU_MANIFEST,
                    windows["hybrid"],
                    config=preflight["config"],
                    retrieval_audit=retrieval_audit,
                    prepared_context=context,
                    diagnostics=diagnostics,
                )
                hashes_after = _candidate_hashes(windows)
                if hashes_after != hashes_before:
                    raise CandidateDepthDiagnosticError(f"Formal Deferred mutated candidate input: {question_id} Top{depth}")
                summary = comparison._arm_summary(packet, windows["hybrid"])
                if (
                    summary.get("selection_policy", {}).get("identity") != DEFERRED_FOOTPRINT_CHARGE_POLICY
                    or summary.get("assembly_config") != _config_projection(preflight["config"])
                    or summary.get("retrieval_unit_build_identity") != ACCEPTED_RU_BUILD_IDENTITY
                ):
                    raise CandidateDepthDiagnosticError(f"Formal Deferred control changed during diagnostic: {question_id} Top{depth}")
                if depth == 20:
                    _validate_top20(question_id, hashes_before, summary, preflight["references"][question_id])
                    top20_visible[question_id] = list(summary["visible_unit_ids"])
                visible_ids = list(summary["visible_unit_ids"])
                direct_root_ids = _direct_root_ids(packet)
                carrier_outcomes = _carrier_outcomes(carriers, windows, visible_ids, direct_root_ids, first_seen, depth)
                result = {
                    "schema_version": SCHEMA_VERSION,
                    "run_identity": run_identity,
                    "question_id": question_id,
                    "question_identity": question["question_identity"],
                    "depth": depth,
                    "candidate_windows": windows,
                    "candidate_hashes": hashes_before,
                    "carrier_outcomes": carrier_outcomes,
                    "complete_carrier_set_hybrid_exposed": all(row["hybrid_rank"] is not None for row in carrier_outcomes),
                    "complete_carrier_set_packet_visible": all(row["packet_visible"] for row in carrier_outcomes),
                    "packet_summary": {
                        **summary,
                        "direct_root_ids": direct_root_ids,
                        "remaining_context_chars": int(summary["budget"]["total_context_chars"]) - int(summary["budget"]["used_context_chars"]),
                        "formal_displacements": _formal_displacements(packet),
                    },
                    "top20_visible_delta": {
                        "added_unit_ids": [unit_id for unit_id in visible_ids if unit_id not in set(top20_visible[question_id])],
                        "displaced_unit_ids": [unit_id for unit_id in top20_visible[question_id] if unit_id not in set(visible_ids)],
                    },
                    "selection_trace": diagnostics.selection_trace,
                }
                packet_body = evidence_packet_json_bytes(packet)
                record_body = canonical_json_bytes(result)
                descriptor = {
                    "record": _immutable_write(output_root / "results" / f"{question_id}-top-{depth:03d}.json", record_body),
                    "packet": _immutable_write(output_root / "packets" / question_id / f"top-{depth:03d}.json", packet_body),
                }
                manifest["completed_artifacts"][key] = descriptor
                manifest["execution_counters"]["candidate_retrievals"] += 1
                manifest["execution_counters"]["formal_deferred_assemblies"] += 1
                _write_manifest(output_root / "metadata" / "manifest.json", manifest)
                rows.append(result)
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["failure"] = {"reason": type(exc).__name__, "message": str(exc)}
        _write_manifest(output_root / "metadata" / "manifest.json", manifest)
        if isinstance(exc, CandidateDepthDiagnosticError):
            raise
        raise CandidateDepthDiagnosticError("candidate-depth diagnostic stopped; immutable partial artifacts remain inspectable") from exc

    expected_keys = {f"{question_id}:{depth:03d}" for question_id in QUESTION_IDS for depth in DEPTHS}
    if set(manifest["completed_artifacts"]) != expected_keys or len(rows) != len(expected_keys):
        raise CandidateDepthDiagnosticError("candidate-depth diagnostic ended without all expected question/depth artifacts")
    rows.sort(key=lambda row: (QUESTION_IDS.index(str(row["question_id"])), DEPTHS.index(int(row["depth"]))))
    aggregate = _aggregate(rows)
    comparison_rows = [
        {
            "schema_version": SCHEMA_VERSION,
            "run_identity": run_identity,
            "question_id": row["question_id"],
            "depth": row["depth"],
            "candidate_hashes": row["candidate_hashes"],
            "carrier_outcomes": row["carrier_outcomes"],
            "complete_carrier_set_hybrid_exposed": row["complete_carrier_set_hybrid_exposed"],
            "complete_carrier_set_packet_visible": row["complete_carrier_set_packet_visible"],
            "packet_summary": row["packet_summary"],
            "top20_visible_delta": row["top20_visible_delta"],
        }
        for row in rows
    ]
    comparison_body = b"".join(canonical_json_bytes(row) + b"\n" for row in comparison_rows)
    manifest["aggregate"] = aggregate
    manifest["final_artifacts"] = {
        "aggregate": _immutable_write(output_root / "metadata" / "aggregate.json", canonical_json_bytes(aggregate)),
        "comparison": _immutable_write(output_root / "comparison.jsonl", comparison_body),
    }
    manifest["status"] = "complete"
    _write_manifest(output_root / "metadata" / "manifest.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the provider-free Qwen candidate-depth diagnostic")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    manifest = run_qwen_candidate_depth_diagnostic(output_root=args.output_root, resume=args.resume)
    print(json.dumps({"output_root": str(args.output_root) if args.output_root else f".local/p04-qwen-candidate-depth-{manifest['run_identity']}", "run_identity": manifest["run_identity"], "status": manifest["status"]}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
