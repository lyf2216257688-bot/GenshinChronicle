"""One-shot paid Phase 04 Block A Q001-Q070 paired quality gate.

This module is deliberately a runner, not a second RAG implementation.  It
loads the accepted frozen query bindings, prepares the unchanged control and
the WU3 field-aware candidate state, then delegates each question to the
existing ``run_single_question`` boundary.  Provider calls are wrapped with
an issued/resolved ledger so an interrupted occurrence remains auditable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
from time import perf_counter
from typing import Any
from uuid import uuid4

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.generation.generation import (
    BASELINE_QWEN_MODEL_ID,
    BailianControlConfig,
    BailianGenerationProvider,
    BailianOpenAICompatibleTransport,
    workspace_from_bailian_base_url,
)
from genshin_corpus.generation.measure import load_m2_runtime_questions
from genshin_corpus.rag.backend import (
    SingleQuestionBackendConfig,
    prepare_rag_state,
    run_single_question,
)
from .block_a_closure import (
    ACCEPTED_QWEN_DENSE_MANIFEST_SHA256,
    ACCEPTED_QWEN_DENSE_VECTORS_SHA256,
    ACCEPTED_QUERY_ARTIFACT_IDENTITY,
    ACCEPTED_QUERY_VECTORS_SHA256,
    ACCEPTED_RU_MANIFEST_SHA256,
    ACCEPTED_QWEN_RU_BUILD_IDENTITY,
    DEFAULT_LEGACY_LEXICAL_MANIFEST,
    DEFAULT_QWEN_QUERY_ROOT,
    DEFAULT_RU_MANIFEST,
    BlockAClosureError,
    BlockAQualityGateConfig,
    _descriptor,
    _git_state,
    _sha256_file,
    _validate_field_artifact,
)
from .qwen_m2_query_vectors import load_accepted_qwen_query_vectors
from .retrieval_units import load_retrieval_units
from .qwen_rerank import (
    DashScopeQwenRerankConfig,
    DashScopeQwenRerankTransport,
    QWEN_RERANK_MODEL_ID,
)
from .reranking import Reranker


QUESTION_IDS = tuple(f"Q{index:03d}" for index in range(1, 71))
SCHEMA_VERSION = "phase04-rag-block-a-paid-full70-0.1"
MAX_RERANKER_CALLS = 70
MAX_GENERATION_CALLS = 140
MAX_C_ONLY_GENERATION_CALLS = 70
CONTROL_REUSE_PROVIDER_BUDGET = {"embedding": 0, "reranker": 70, "generation": 70, "control_regeneration": 0}
EXPECTED_REUSABLE_CONTROL_RUN_ID = "91b0b07fe784d3dd0bf3ec3a442a362ed50fa29aff59f87c350f2fdc1ea2435d"
EXPECTED_REUSABLE_CONTROL_MANIFEST_SHA256 = "6da13289b33205245736af4a3e4c77b8b1f3b4bb968f370d2618114c5bea9f6a"
EXPECTED_REUSABLE_CONTROL_LEDGER_SHA256 = "5b4f78eca706e68d47a6513e2f626b4cc46dbd78540a56ab9f9b4ac6b9309a37"
EXPECTED_REUSABLE_CONTROL_ROOT = Path(".local/p04-block-a-paid-full70-20260914T061529Z-ab48c2df")
DEFAULT_CLOSURE_ROOT = Path(".local/p04-block-a-closure-20260914-r2")
DEFAULT_RUNTIME_INPUT = Path(".local/p04-rag-m2/questions.runtime.jsonl")
DEFAULT_DENSE_MANIFEST = Path(
    ".local/p04-qwen-full-batch-beijing-20260909/final/dense/metadata/manifest.json"
)
DEFAULT_C_DIAGNOSTIC_ROOT = Path(".local/p04-bm25f-block-a-closure-20260915")
C_DIAGNOSTIC_SHA256 = "2cc53b482344fcb58cae65ee6d3307fdd2710ba1d4ffea32c756dd0b1555c1db"
C_DIAGNOSTIC_CANDIDATE_RETRIEVAL_SHA256 = "3dc0119ce5da7b169e87aa18011d17bb2d22c2cc5534618dcda83a83da597171"
C_DIAGNOSTIC_FIELD_MANIFEST_SHA256 = "8a2826f5b70fac841235e89a1aa989d75852ec0665b0c2e5bf77fb29888a4341"
C_DIAGNOSTIC_FIELD_INDEX_SHA256 = "407c5fda246d32b22479d80028b14e1dd8964de78f6f8b9e440a35eace801d33"
EXPECTED_C_ONLY_PARTIAL_RUN_ID = "4d09456a30470dc679b7d24f1403f687742568a916a5837e6b97e8953f523c40"
EXPECTED_C_ONLY_PARTIAL_CONTROL_EVIDENCE_ID = "b050ef79d93e47439637a2762ff4f848c735e2063ee5c59defbf79b160fb00cd"
EXPECTED_C_ONLY_ORIGINAL_RUNNER_SHA256 = "78036b7197051956cb8b6c87020c80a9b4106d4006483a6124852a82583172ff"
DEFAULT_C_ONLY_PARTIAL_ROOT = Path(".local/p04-block-a-paid-c-only-20260915-6583536f")
RESUME_SCHEMA_VERSION = "phase04-rag-block-a-paid-c-only-resume-0.1"


class BlockAPaidGateError(RuntimeError):
    """Raised when the paid gate cannot start or must stop fail-closed."""


def _write_json(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    body = canonical_json_bytes(dict(value))
    atomic_write(path, body)
    return _descriptor(path)


def _append_ledger(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(canonical_json_bytes(dict(row)) + b"\n")
        handle.flush()
        import os

        os.fsync(handle.fileno())


def _ledger_row_count(path: Path) -> int:
    try:
        return sum(1 for _ in path.open("rb"))
    except OSError:
        return 0


def _safe_provider_metadata(provider: Any) -> dict[str, Any] | None:
    value = getattr(provider, "last_response_metadata", None)
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for key in ("request_id", "model", "status_code"):
        item = value.get(key)
        if isinstance(item, (str, int)) and not isinstance(item, bool):
            result[key] = item
    usage = value.get("usage")
    if isinstance(usage, Mapping):
        result["usage"] = {
            str(key): item
            for key, item in usage.items()
            if isinstance(item, (int, float)) and not isinstance(item, bool)
            and item >= 0 and (not isinstance(item, float) or item == item)
        }
    return result or None


class _LedgerReranker:
    def __init__(self, delegate: Reranker, ledger: Path, question_id: str, counter: dict[str, int]) -> None:
        self.delegate, self.ledger, self.question_id, self.counter = delegate, ledger, question_id, counter
        self.last_response_metadata: dict[str, Any] = {}

    def rerank(self, request: Any) -> Sequence[Any]:
        occurrence_id = f"{self.question_id}-vnext-rerank-{uuid4().hex}"
        self.counter["reranker"] += 1
        if self.counter["reranker"] > MAX_RERANKER_CALLS:
            raise BlockAPaidGateError("reranker budget exceeded")
        _append_ledger(self.ledger, {
            "question_id": self.question_id, "arm": "vnext", "stage": "reranker",
            "occurrence_id": occurrence_id, "status": "issued", "attempt_count": 1,
        })
        started = perf_counter()
        try:
            scores = self.delegate.rerank(request)
            self.last_response_metadata = dict(getattr(self.delegate, "last_response_metadata", {}) or {})
            resolved = {
                "question_id": self.question_id, "arm": "vnext", "stage": "reranker",
                "occurrence_id": occurrence_id, "status": "succeeded", "attempt_count": 1,
                "elapsed_seconds": perf_counter() - started,
                "provider": _safe_provider_metadata(self.delegate),
            }
            _append_ledger(self.ledger, resolved)
            return scores
        except Exception as exc:
            _append_ledger(self.ledger, {
                "question_id": self.question_id, "arm": "vnext", "stage": "reranker",
                "occurrence_id": occurrence_id, "status": "failed", "attempt_count": 1,
                "elapsed_seconds": perf_counter() - started, "error_type": type(exc).__name__,
            })
            raise


class _LedgerGenerationProvider:
    def __init__(
        self,
        delegate: Any,
        ledger: Path,
        arm: str,
        question_id: str,
        counter: dict[str, int],
        *,
        maximum_calls: int = MAX_GENERATION_CALLS,
    ) -> None:
        self.delegate, self.ledger, self.arm, self.question_id, self.counter = delegate, ledger, arm, question_id, counter
        self.maximum_calls = maximum_calls

    def generate(self, request: Any) -> Any:
        occurrence_id = f"{self.question_id}-{self.arm}-generation-{uuid4().hex}"
        self.counter["generation"] += 1
        if self.counter["generation"] > self.maximum_calls:
            raise BlockAPaidGateError("Generation budget exceeded")
        _append_ledger(self.ledger, {
            "question_id": self.question_id, "arm": self.arm, "stage": "generation",
            "occurrence_id": occurrence_id, "status": "issued", "attempt_count": 1,
        })
        started = perf_counter()
        try:
            result = self.delegate.generate(request)
            audit = getattr(result, "provider_audit", {})
            attempts = audit.get("attempts", []) if isinstance(audit, Mapping) else []
            usage = None
            if isinstance(attempts, list) and attempts and isinstance(attempts[-1], Mapping):
                usage = attempts[-1].get("usage")
            _append_ledger(self.ledger, {
                "question_id": self.question_id, "arm": self.arm, "stage": "generation",
                "occurrence_id": occurrence_id,
                "status": "succeeded" if getattr(result, "execution_status", None) == "succeeded" else "failed",
                "attempt_count": len(attempts) if isinstance(attempts, list) else 1,
                "elapsed_seconds": perf_counter() - started,
                "provider_request_id": attempts[-1].get("provider_request_id") if attempts and isinstance(attempts[-1], Mapping) else None,
                "usage": dict(usage) if isinstance(usage, Mapping) else None,
                "execution_status": getattr(result, "execution_status", None),
            })
            return result
        except Exception as exc:
            _append_ledger(self.ledger, {
                "question_id": self.question_id, "arm": self.arm, "stage": "generation",
                "occurrence_id": occurrence_id, "status": "failed", "attempt_count": 1,
                "elapsed_seconds": perf_counter() - started, "error_type": type(exc).__name__,
            })
            raise


def _source_hashes() -> dict[str, str]:
    paths = {
        "runner": Path(__file__),
        "backend": Path("src/genshin_corpus/rag/backend.py"),
        "candidate_retrieval": Path("src/genshin_corpus/retrieval/candidate_retrieval.py"),
        "reranking": Path("src/genshin_corpus/retrieval/reranking.py"),
        "qwen_rerank": Path("src/genshin_corpus/retrieval/qwen_rerank.py"),
        "query_vectors": Path("src/genshin_corpus/retrieval/qwen_m2_query_vectors.py"),
        "assembly": Path("src/genshin_corpus/retrieval/evidence_assembly.py"),
        "generation": Path("src/genshin_corpus/generation/generation.py"),
    }
    return {name: _sha256_file(path) for name, path in paths.items()}


def _validate_production_defaults() -> None:
    config = SingleQuestionBackendConfig()
    if (
        config.candidate_supply_depth != 500
        or config.rerank_depth != 500
        or config.final_top_n != 20
        or not config.reranker_enabled
        or config.rrf_k != 60
    ):
        raise BlockAPaidGateError("production defaults are not the adopted Block A operating point")


def _validate_closure(
    closure_root: Path,
    runtime_input: Path,
    ru_manifest_path: Path = DEFAULT_RU_MANIFEST,
) -> tuple[dict[str, Any], tuple[Any, ...], Path]:
    manifest_path = closure_root / "metadata" / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BlockAPaidGateError("closure manifest is unreadable") from exc
    if manifest.get("status") != "complete" or manifest.get("run_identity") != "24e5bee52543636a145c1eb6523521d11247c282eb5c1c9294cebd5736bf56da":
        raise BlockAPaidGateError("WU3 closure identity/status mismatch")
    vectors = load_accepted_qwen_query_vectors(DEFAULT_QWEN_QUERY_ROOT)
    if len(vectors) != 70 or vectors[0].artifact_identity != ACCEPTED_QUERY_ARTIFACT_IDENTITY or vectors[0].vectors_sha256 != ACCEPTED_QUERY_VECTORS_SHA256:
        raise BlockAPaidGateError("accepted query-vector identity mismatch")
    if _sha256_file(DEFAULT_RU_MANIFEST) != ACCEPTED_RU_MANIFEST_SHA256:
        raise BlockAPaidGateError("accepted RU manifest hash mismatch")
    if _sha256_file(DEFAULT_DENSE_MANIFEST) != ACCEPTED_QWEN_DENSE_MANIFEST_SHA256:
        raise BlockAPaidGateError("accepted Dense manifest hash mismatch")
    field_manifest = closure_root / "field-aware-lexical" / "metadata" / "manifest.json"
    _, ru_units = load_retrieval_units(Path(ru_manifest_path))
    _validate_field_artifact(field_manifest, ru_units)
    for artifact in ("control_output", "retrieval_output", "field_aware_lexical_index", "field_aware_lexical_manifest"):
        descriptor = manifest.get("artifacts", {}).get(artifact)
        if not isinstance(descriptor, Mapping):
            raise BlockAPaidGateError(f"closure artifact descriptor missing: {artifact}")
        artifact_path = Path(str(descriptor.get("path")))
        if not artifact_path.is_absolute():
            artifact_path = Path.cwd() / artifact_path
        if not artifact_path.is_file() or _sha256_file(artifact_path) != descriptor.get("sha256"):
            raise BlockAPaidGateError(f"closure artifact hash mismatch: {artifact}")
    runtime_questions = load_m2_runtime_questions(runtime_input)
    if [item.question_id for item in runtime_questions] != list(QUESTION_IDS):
        raise BlockAPaidGateError("runtime questions are not Q001-Q070")
    return manifest, vectors, field_manifest


def _validate_c_challenger_closure(
    closure_root: Path,
    runtime_input: Path,
    ru_manifest_path: Path = DEFAULT_RU_MANIFEST,
    diagnostic_root: Path = DEFAULT_C_DIAGNOSTIC_ROOT,
) -> tuple[dict[str, Any], tuple[Any, ...], Path]:
    """Bind C to the exact old field projection/index proved by Diagnostic C."""

    diagnostic_path = Path(diagnostic_root) / "diagnostic.json"
    sidecar_path = Path(diagnostic_root) / "artifact.sha256.json"
    if _sha256_file(diagnostic_path) != C_DIAGNOSTIC_SHA256:
        raise BlockAPaidGateError("Diagnostic C artifact hash mismatch")
    sidecar = _read_json(sidecar_path, "Diagnostic C artifact sidecar")
    if sidecar.get("sha256") != C_DIAGNOSTIC_SHA256 or sidecar.get("byte_count") != diagnostic_path.stat().st_size:
        raise BlockAPaidGateError("Diagnostic C sidecar mismatch")
    diagnostic = _read_json(diagnostic_path, "Diagnostic C evidence")
    scorer = diagnostic.get("scorer_change")
    binding = diagnostic.get("source_bindings")
    if (
        diagnostic.get("status") != "PASS"
        or diagnostic.get("repo", {}).get("candidate_retrieval_sha256") != C_DIAGNOSTIC_CANDIDATE_RETRIEVAL_SHA256
        or _sha256_file(Path("src/genshin_corpus/retrieval/candidate_retrieval.py")) != C_DIAGNOSTIC_CANDIDATE_RETRIEVAL_SHA256
        or not isinstance(scorer, Mapping)
        or scorer.get("selected") != "C"
        or scorer.get("version") != "phase04-bm25f-style-0.1"
        or scorer.get("k1") != 1.2
        or scorer.get("b") != 0.75
        or scorer.get("weights") != {"record_title": 2.0, "section_name": 1.25, "speaker": 1.5, "retrieval_visible_text": 1.0}
        or not isinstance(binding, Mapping)
    ):
        raise BlockAPaidGateError("Diagnostic C source/formulation binding mismatch")
    closure_manifest, vectors, _ = _validate_closure_inputs_only(closure_root, runtime_input, ru_manifest_path)
    field_binding = binding.get("field_aware_manifest")
    if not isinstance(field_binding, Mapping):
        raise BlockAPaidGateError("Diagnostic C field manifest binding is missing")
    field_manifest = Path(closure_root) / "field-aware-lexical" / "metadata" / "manifest.json"
    if (
        _sha256_file(field_manifest) != C_DIAGNOSTIC_FIELD_MANIFEST_SHA256
        or field_binding.get("sha256") != C_DIAGNOSTIC_FIELD_MANIFEST_SHA256
        or field_binding.get("index_sha256") != C_DIAGNOSTIC_FIELD_INDEX_SHA256
    ):
        raise BlockAPaidGateError("Diagnostic C field manifest identity mismatch")
    field = _read_json(field_manifest, "Diagnostic C field manifest")
    index = field.get("artifacts", {}).get("index")
    if (
        field.get("status") != "complete"
        or field.get("row_count") != 535802
        or field.get("arm_build_identity") != field_binding.get("identity")
        or field.get("retrieval_unit_build_identity") != ACCEPTED_QWEN_RU_BUILD_IDENTITY
        or not isinstance(index, Mapping)
        or index.get("sha256") != C_DIAGNOSTIC_FIELD_INDEX_SHA256
        or _sha256_file(field_manifest.parent.parent / str(index.get("path"))) != C_DIAGNOSTIC_FIELD_INDEX_SHA256
    ):
        raise BlockAPaidGateError("Diagnostic C field index identity mismatch")
    return closure_manifest, vectors, field_manifest


def _validate_closure_inputs_only(
    closure_root: Path,
    runtime_input: Path,
    ru_manifest_path: Path,
) -> tuple[dict[str, Any], tuple[Any, ...], Path]:
    """Validate shared closure inputs without imposing a scorer label on C's old index."""

    manifest_path = Path(closure_root) / "metadata" / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BlockAPaidGateError("closure manifest is unreadable") from exc
    if manifest.get("status") != "complete" or manifest.get("run_identity") != "24e5bee52543636a145c1eb6523521d11247c282eb5c1c9294cebd5736bf56da":
        raise BlockAPaidGateError("WU3 closure identity/status mismatch")
    vectors = load_accepted_qwen_query_vectors(DEFAULT_QWEN_QUERY_ROOT)
    if len(vectors) != 70 or vectors[0].artifact_identity != ACCEPTED_QUERY_ARTIFACT_IDENTITY or vectors[0].vectors_sha256 != ACCEPTED_QUERY_VECTORS_SHA256:
        raise BlockAPaidGateError("accepted query-vector identity mismatch")
    if _sha256_file(DEFAULT_RU_MANIFEST) != ACCEPTED_RU_MANIFEST_SHA256:
        raise BlockAPaidGateError("accepted RU manifest hash mismatch")
    if _sha256_file(DEFAULT_DENSE_MANIFEST) != ACCEPTED_QWEN_DENSE_MANIFEST_SHA256:
        raise BlockAPaidGateError("accepted Dense manifest hash mismatch")
    for artifact in ("control_output", "retrieval_output", "field_aware_lexical_index", "field_aware_lexical_manifest"):
        descriptor = manifest.get("artifacts", {}).get(artifact)
        if not isinstance(descriptor, Mapping):
            raise BlockAPaidGateError(f"closure artifact descriptor missing: {artifact}")
        artifact_path = Path(str(descriptor.get("path")))
        if not artifact_path.is_absolute():
            artifact_path = Path.cwd() / artifact_path
        if not artifact_path.is_file() or _sha256_file(artifact_path) != descriptor.get("sha256"):
            raise BlockAPaidGateError(f"closure artifact hash mismatch: {artifact}")
    runtime_questions = load_m2_runtime_questions(runtime_input)
    if [item.question_id for item in runtime_questions] != list(QUESTION_IDS):
        raise BlockAPaidGateError("runtime questions are not Q001-Q070")
    return manifest, vectors, Path(closure_root) / "field-aware-lexical" / "metadata" / "manifest.json"


def _read_json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BlockAPaidGateError(f"{label} is unreadable") from exc
    if not isinstance(value, Mapping):
        raise BlockAPaidGateError(f"{label} must be an object")
    return value


def _descriptor_matches(path: Path, descriptor: Mapping[str, Any], label: str) -> dict[str, Any]:
    actual = _descriptor(path)
    if actual["sha256"] != descriptor.get("sha256") or actual["byte_count"] != descriptor.get("byte_count"):
        raise BlockAPaidGateError(f"{label} hash mismatch")
    return actual


def _control_result_artifacts(result_root: Path, result: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    persistence = result.get("persistence")
    if not isinstance(persistence, Mapping):
        raise BlockAPaidGateError("reused control persistence is missing")
    packet = persistence.get("evidence_packet")
    generation = persistence.get("generation")
    if not isinstance(packet, Mapping) or not isinstance(packet.get("json"), Mapping) or not isinstance(generation, Mapping):
        raise BlockAPaidGateError("reused control artifact descriptors are incomplete")
    packet_json = _descriptor_matches(result_root / "packet" / "evidence_packet.json", packet["json"], "reused control Packet")
    generation_json = _descriptor_matches(result_root / "generation" / "generation_result.json", generation, "reused control Generation")
    return {"packet": packet_json, "generation": generation_json}


def _validate_reused_control_evidence(
    control_root: Path,
    *,
    questions: Sequence[Any],
    legacy_lexical_manifest_path: Path,
    dense_manifest_path: Path,
    generation_config_identity: str,
) -> dict[str, Any]:
    """Validate the one authorized immutable 70Q control before any paid call."""

    control_root = Path(control_root)
    manifest_path = control_root / "metadata" / "manifest.json"
    ledger_path = control_root / "metadata" / "provider_attempts.jsonl"
    manifest_descriptor = _descriptor(manifest_path)
    if manifest_descriptor["sha256"] != EXPECTED_REUSABLE_CONTROL_MANIFEST_SHA256:
        raise BlockAPaidGateError("reused control manifest is not the accepted immutable evidence")
    manifest = _read_json(manifest_path, "reused control manifest")
    if (
        manifest.get("run_identity") != EXPECTED_REUSABLE_CONTROL_RUN_ID
        or manifest.get("status") != "partial_failed"
        or tuple(manifest.get("completed_control_questions", ())) != QUESTION_IDS
    ):
        raise BlockAPaidGateError("reused control identity/completeness mismatch")
    if manifest.get("generation_config_identity") != generation_config_identity:
        raise BlockAPaidGateError("reused control Generation configuration mismatch")
    if manifest.get("generation", {}).get("model_id") != BASELINE_QWEN_MODEL_ID:
        raise BlockAPaidGateError("reused control Generation model mismatch")

    legacy_manifest = _read_json(legacy_lexical_manifest_path, "legacy lexical manifest")
    dense_manifest = _read_json(dense_manifest_path, "Dense manifest")
    legacy_identity = legacy_manifest.get("arm_build_identity")
    dense_identity = dense_manifest.get("arm_build_identity")
    if not isinstance(legacy_identity, str) or not isinstance(dense_identity, str):
        raise BlockAPaidGateError("live control arm identities are unreadable")

    ledger_descriptor = _descriptor(ledger_path)
    if ledger_descriptor["sha256"] != EXPECTED_REUSABLE_CONTROL_LEDGER_SHA256:
        raise BlockAPaidGateError("reused control provider ledger is not the accepted immutable evidence")
    manifest_ledger = manifest.get("artifacts", {}).get("provider_attempts")
    if not isinstance(manifest_ledger, Mapping):
        raise BlockAPaidGateError("reused control provider ledger descriptor is missing")
    _descriptor_matches(ledger_path, manifest_ledger, "reused control provider ledger")
    try:
        ledger_rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BlockAPaidGateError("reused control provider ledger is unreadable") from exc
    control_occurrences: dict[str, list[str]] = {}
    for row in ledger_rows:
        if row.get("arm") != "control" or row.get("stage") != "generation":
            continue
        qid, occurrence_id, status = row.get("question_id"), row.get("occurrence_id"), row.get("status")
        if qid not in QUESTION_IDS or not isinstance(occurrence_id, str) or status not in {"issued", "succeeded", "failed"}:
            raise BlockAPaidGateError("reused control provider ledger row is invalid")
        control_occurrences.setdefault(occurrence_id, []).append(status)
    resolved_by_question: dict[str, int] = {qid: 0 for qid in QUESTION_IDS}
    for occurrence_id, statuses in control_occurrences.items():
        if statuses.count("issued") != 1 or statuses.count("succeeded") != 1 or len(statuses) != 2:
            raise BlockAPaidGateError(f"reused control provider occurrence is unresolved: {occurrence_id}")
        qid = next(
            str(row["question_id"])
            for row in ledger_rows
            if row.get("occurrence_id") == occurrence_id and row.get("arm") == "control"
        )
        resolved_by_question[qid] += 1
    if len(control_occurrences) != len(QUESTION_IDS) or any(count != 1 for count in resolved_by_question.values()):
        raise BlockAPaidGateError("reused control provider ledger is not exactly one successful call per question")

    if tuple(getattr(question, "question_id", None) for question in questions) != QUESTION_IDS:
        raise BlockAPaidGateError("reused control runtime question order is not Q001-Q070")
    result_descriptors: list[dict[str, Any]] = []
    for question in questions:
        qid = question.question_id
        result_root = control_root / "results" / qid / "control" / qid
        result_path = result_root / "rag_result.json"
        result_descriptor = _descriptor(result_path)
        result = _read_json(result_path, f"reused control result {qid}")
        query = result.get("query")
        trace = result.get("retrieval_trace")
        validation = result.get("citation_validation")
        audit = result.get("audit")
        if (
            result.get("status") != "succeeded"
            or result.get("execution_mode") != "generate_answer"
            or not isinstance(query, Mapping)
            or query.get("execution_identity") != qid
            or query.get("request_label") != qid
            or query.get("question_text") != question.question
            or not isinstance(trace, Mapping)
            or trace.get("candidate_supply_depth") != 20
            or trace.get("rrf_k") != 60
            or not isinstance(validation, Mapping)
            or validation.get("citation_integrity") != "pass"
            or validation.get("citation_coverage") != "pass"
            or validation.get("semantic_faithfulness") != "not_evaluated"
            or not isinstance(audit, Mapping)
            or audit.get("config", {}).get("candidate_supply_depth") != 20
            or audit.get("config", {}).get("reranker_enabled") is not False
        ):
            raise BlockAPaidGateError(f"reused control semantics mismatch: {qid}")
        hybrid = trace.get("windows", {}).get("hybrid", [])
        if not isinstance(hybrid, list) or not hybrid:
            raise BlockAPaidGateError(f"reused control Hybrid evidence is missing: {qid}")
        for row in hybrid:
            arms = row.get("retrieval", {}).get("arm_build_identities", {}) if isinstance(row, Mapping) else {}
            if arms.get("lexical") != legacy_identity or arms.get("dense") != dense_identity:
                raise BlockAPaidGateError(f"reused control retrieval arm mismatch: {qid}")
        rerank = result.get("rerank_trace")
        if not isinstance(rerank, Mapping) or rerank.get("status") != "disabled_by_explicit_config":
            raise BlockAPaidGateError(f"reused control reranker semantics mismatch: {qid}")
        generation_identity = result.get("generation", {}).get("result", {}).get("audit", {}).get("execution_config_identity")
        if generation_identity != generation_config_identity:
            raise BlockAPaidGateError(f"reused control Generation result configuration mismatch: {qid}")
        result_descriptors.append({
            "question_id": qid,
            "rag_result": result_descriptor,
            **_control_result_artifacts(result_root, result),
        })
    result_identity = sha256_json(result_descriptors)
    return {
        "root": str(control_root.resolve()),
        "run_identity": EXPECTED_REUSABLE_CONTROL_RUN_ID,
        "manifest": manifest_descriptor,
        "provider_ledger": ledger_descriptor,
        "question_ids": list(QUESTION_IDS),
        "result_count": len(result_descriptors),
        "result_identity": result_identity,
        "legacy_lexical_identity": legacy_identity,
        "dense_identity": dense_identity,
        "generation_config_identity": generation_config_identity,
        "results": result_descriptors,
        "evidence_identity": sha256_json({
            "run_identity": EXPECTED_REUSABLE_CONTROL_RUN_ID,
            "manifest": manifest_descriptor,
            "provider_ledger": ledger_descriptor,
            "result_identity": result_identity,
            "question_ids": list(QUESTION_IDS),
        }),
    }


def _c_only_planning_identity(
    *,
    source_hashes: Mapping[str, str],
    control: Mapping[str, Any],
    query_vectors_sha256: str,
    head: str,
) -> str:
    return sha256_json({
        "kind": "p04-block-a-paid-c-only-preflight-0.1",
        "date": "20260915",
        "head": head,
        "candidate_retrieval": source_hashes["candidate_retrieval"],
        "control_root": ".local/p04-block-a-paid-full70-20260914T061529Z-ab48c2df",
        "control_manifest": control["manifest"]["sha256"],
        "query_vectors": query_vectors_sha256,
        "dense": ACCEPTED_QWEN_DENSE_VECTORS_SHA256,
        "reranker_budget": 70,
        "generation_budget": 70,
    })


def preflight_block_a_paid_full70_control_reuse(
    *,
    reuse_control_root: Path,
    expected_reuse_planning_identity: str | None = None,
    closure_root: Path = DEFAULT_CLOSURE_ROOT,
    runtime_input: Path = DEFAULT_RUNTIME_INPUT,
    ru_manifest_path: Path = DEFAULT_RU_MANIFEST,
    legacy_lexical_manifest_path: Path = DEFAULT_LEGACY_LEXICAL_MANIFEST,
    dense_manifest_path: Path = DEFAULT_DENSE_MANIFEST,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run the C-only paid-gate checks without constructing a provider transport."""

    _validate_production_defaults()
    closure_manifest, bindings, field_manifest = _validate_c_challenger_closure(Path(closure_root), Path(runtime_input), Path(ru_manifest_path))
    source_hashes = _source_hashes()
    values = dict(environment or {}) if environment is not None else None
    env = values if values is not None else __import__("os").environ
    endpoint = env.get("BAILIAN_BASE_URL")
    if not isinstance(endpoint, str) or not endpoint:
        raise BlockAPaidGateError("BAILIAN_BASE_URL is required for control-reuse configuration preflight")
    generation_config = BailianControlConfig(
        region="cn-beijing", endpoint=endpoint, workspace=workspace_from_bailian_base_url(endpoint),
        model_id=BASELINE_QWEN_MODEL_ID, enable_thinking=False,
        max_output_tokens=2048, max_attempts=1, timeout_seconds=30.0,
    )
    rerank_config = DashScopeQwenRerankConfig(
        region="cn-beijing", endpoint="https://ws-gdq9z4ufdb87egio.cn-beijing.maas.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
        workspace="ws-gdq9z4ufdb87egio", timeout_seconds=60.0,
    )
    questions = load_m2_runtime_questions(Path(runtime_input))
    if [question.question_id for question in questions] != list(QUESTION_IDS):
        raise BlockAPaidGateError("runtime questions are not Q001-Q070")
    reused_control = _validate_reused_control_evidence(
        Path(reuse_control_root), questions=questions,
        legacy_lexical_manifest_path=Path(legacy_lexical_manifest_path),
        dense_manifest_path=Path(dense_manifest_path),
        generation_config_identity=generation_config.execution_config_identity,
    )
    planning_identity = _c_only_planning_identity(
        source_hashes=source_hashes,
        control=reused_control,
        query_vectors_sha256=bindings[0].vectors_sha256,
        head=str(_git_state()["head"]),
    )
    if expected_reuse_planning_identity is not None and expected_reuse_planning_identity != planning_identity:
        raise BlockAPaidGateError("reserved C-only planning identity mismatch")
    return {
        "schema_version": "phase04-rag-block-a-paid-c-only-preflight-0.1",
        "status": "provider_free_ready",
        "planning_identity": planning_identity,
        "provider_calls_executed": {"embedding": 0, "reranker": 0, "generation": 0},
        "provider_call_budget": dict(CONTROL_REUSE_PROVIDER_BUDGET),
        "source_hashes": source_hashes,
        "closure": {"run_identity": closure_manifest.get("run_identity"), "field_manifest": _descriptor(field_manifest)},
        "query_vectors": {"artifact_identity": bindings[0].artifact_identity, "sha256": bindings[0].vectors_sha256, "count": len(bindings)},
        "reused_control": reused_control,
        "generation_config_identity": generation_config.execution_config_identity,
        "reranker": rerank_config.identity_projection(),
        "q056_policy": "excluded_from_clean_advancement_gate",
    }


def _review_projection(question_id: str, question: str, result: Mapping[str, Any]) -> dict[str, Any]:
    packet = result.get("evidence_packet")
    refs: list[dict[str, Any]] = []
    if isinstance(packet, Mapping):
        for evidence in packet.get("evidence", []):
            if not isinstance(evidence, Mapping):
                continue
            refs.append({
                "evidence_id": evidence.get("evidence_id"),
                "unit_id": evidence.get("unit_id"),
                "text": evidence.get("text"),
                "display_context": evidence.get("display_context", []),
            })
    telemetry = result.get("telemetry", {})
    return {
        "question_id": question_id, "question": question,
        "evidence_packet": refs,
        "answer": result.get("final_answer"),
        "status": result.get("status"),
        "citation_validation": result.get("citation_validation"),
        "retrieval_lineage": result.get("retrieval_trace"),
        "rerank_lineage": result.get("rerank_trace"),
        "usage": {"provider": telemetry.get("reranker_usage"), "generation": telemetry.get("generation_usage")},
        "timing_seconds": result.get("timing_seconds"),
        "error": result.get("error"),
    }


def _resume_ledger_state(ledger_path: Path, run_identity: str) -> tuple[dict[str, int], dict[str, dict[str, str]]]:
    """Return immutable cumulative calls and terminal occurrences for C-only resume."""

    try:
        rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BlockAPaidGateError("resume provider ledger is unreadable") from exc
    starts = [row for row in rows if isinstance(row, Mapping) and row.get("event") == "run_started"]
    if len(starts) != 1 or starts[0].get("run_identity") != run_identity or starts[0].get("embedding_calls") != 0:
        raise BlockAPaidGateError("resume provider ledger run_started binding mismatch")
    occurrences: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or row.get("event") == "run_started":
            continue
        qid, arm, stage, occurrence_id, status = (
            row.get("question_id"), row.get("arm"), row.get("stage"), row.get("occurrence_id"), row.get("status"),
        )
        if (
            qid not in QUESTION_IDS or arm != "vnext" or stage not in {"reranker", "generation"}
            or not isinstance(occurrence_id, str) or not occurrence_id or status not in {"issued", "succeeded", "failed"}
        ):
            raise BlockAPaidGateError("resume provider ledger contains an invalid or non-C-only occurrence")
        occurrences.setdefault(occurrence_id, []).append(row)
    counters = {"embedding": 0, "reranker": 0, "generation": 0, "control_regeneration": 0}
    resolved: dict[str, dict[str, str]] = {qid: {} for qid in QUESTION_IDS}
    for occurrence_id, events in occurrences.items():
        issued = [row for row in events if row.get("status") == "issued"]
        terminal = [row for row in events if row.get("status") in {"succeeded", "failed"}]
        if len(issued) != 1 or len(terminal) != 1 or len(events) != 2:
            raise BlockAPaidGateError(f"resume provider occurrence is unresolved or duplicated: {occurrence_id}")
        issue, end = issued[0], terminal[0]
        if any(issue.get(key) != end.get(key) for key in ("question_id", "arm", "stage", "occurrence_id")):
            raise BlockAPaidGateError(f"resume provider occurrence fields drifted: {occurrence_id}")
        if end.get("status") != "succeeded":
            raise BlockAPaidGateError(f"resume provider occurrence failed: {occurrence_id}")
        stage = str(issue["stage"])
        qid = str(issue["question_id"])
        if stage in resolved[qid]:
            raise BlockAPaidGateError(f"resume provider has duplicate {stage} occurrence: {qid}")
        resolved[qid][stage] = occurrence_id
        counters[stage] += 1
    if (
        counters["reranker"] != counters["generation"]
        or counters["reranker"] > CONTROL_REUSE_PROVIDER_BUDGET["reranker"]
        or counters["generation"] > CONTROL_REUSE_PROVIDER_BUDGET["generation"]
    ):
        raise BlockAPaidGateError("resume provider ledger cumulative budget mismatch")
    return counters, resolved


def _validate_completed_vnext_result(
    result_root: Path,
    *,
    question: Any,
    field_identity: str,
    dense_identity: str,
    generation_config_identity: str,
) -> dict[str, Any]:
    qid = question.question_id
    result = _read_json(result_root / "rag_result.json", f"resume vNext result {qid}")
    query = result.get("query")
    trace = result.get("retrieval_trace")
    rerank = result.get("rerank_trace")
    validation = result.get("citation_validation")
    persistence = result.get("persistence")
    if (
        result.get("status") != "succeeded"
        or result.get("execution_mode") != "generate_answer"
        or not isinstance(query, Mapping)
        or query.get("execution_identity") != qid
        or query.get("request_label") != qid
        or query.get("question_text") != question.question
        or not isinstance(trace, Mapping)
        or trace.get("candidate_supply_depth") != 500
        or trace.get("rrf_k") != 60
        or not isinstance(rerank, Mapping)
        or rerank.get("status") != "executed_successfully"
        or not isinstance(validation, Mapping)
        or validation.get("citation_integrity") != "pass"
        or validation.get("citation_coverage") != "pass"
        or validation.get("semantic_faithfulness") != "not_evaluated"
        or not isinstance(persistence, Mapping)
    ):
        raise BlockAPaidGateError(f"resume completed vNext result semantics mismatch: {qid}")
    hybrid = trace.get("windows", {}).get("hybrid", [])
    if not isinstance(hybrid, list) or not hybrid:
        raise BlockAPaidGateError(f"resume completed vNext Hybrid evidence is missing: {qid}")
    for row in hybrid:
        arms = row.get("retrieval", {}).get("arm_build_identities", {}) if isinstance(row, Mapping) else {}
        if arms.get("lexical") != field_identity or arms.get("dense") != dense_identity:
            raise BlockAPaidGateError(f"resume completed vNext retrieval arm mismatch: {qid}")
    packet = persistence.get("evidence_packet")
    generation = persistence.get("generation")
    if not isinstance(packet, Mapping) or not isinstance(packet.get("json"), Mapping) or not isinstance(generation, Mapping):
        raise BlockAPaidGateError(f"resume completed vNext artifact descriptors are incomplete: {qid}")
    _descriptor_matches(result_root / "packet" / "evidence_packet.json", packet["json"], f"resume vNext Packet {qid}")
    _descriptor_matches(result_root / "generation" / "generation_result.json", generation, f"resume vNext Generation {qid}")
    result_generation_identity = result.get("generation", {}).get("result", {}).get("audit", {}).get("execution_config_identity")
    if result_generation_identity != generation_config_identity:
        raise BlockAPaidGateError(f"resume completed vNext Generation configuration mismatch: {qid}")
    return dict(result)


def _reconcile_c_only_resume(
    *,
    output_root: Path,
    expected_run_identity: str,
    closure_root: Path,
    runtime_input: Path,
    ru_manifest_path: Path,
    legacy_lexical_manifest_path: Path,
    dense_manifest_path: Path,
    environment: Mapping[str, str] | None,
) -> dict[str, Any]:
    """Validate an exact partial C-only root before a transport can exist."""

    output_root = Path(output_root)
    if output_root.resolve() != DEFAULT_C_ONLY_PARTIAL_ROOT.resolve():
        raise BlockAPaidGateError("resume refuses a replacement output root")
    if expected_run_identity != EXPECTED_C_ONLY_PARTIAL_RUN_ID:
        raise BlockAPaidGateError("resume expected run identity is not authorized")
    manifest_path = output_root / "metadata" / "manifest.json"
    ledger_path = output_root / "metadata" / "provider_attempts.jsonl"
    manifest = _read_json(manifest_path, "resume manifest")
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("run_identity") != expected_run_identity
        or manifest.get("status") not in {"in_progress", "partial_failed", "complete"}
        or tuple(manifest.get("question_ids", ())) != QUESTION_IDS
        or manifest.get("control_reuse") is not True
        or manifest.get("budgets") != CONTROL_REUSE_PROVIDER_BUDGET
        or tuple(manifest.get("completed_control_questions", ())) != QUESTION_IDS
        or tuple(manifest.get("reused_control_questions", ())) != QUESTION_IDS
        or manifest.get("q056_policy") != "excluded_from_clean_advancement_gate"
    ):
        raise BlockAPaidGateError("resume manifest identity/configuration mismatch")
    reused = manifest.get("reused_control")
    if (
        not isinstance(reused, Mapping)
        or reused.get("run_identity") != EXPECTED_REUSABLE_CONTROL_RUN_ID
        or reused.get("evidence_identity") != EXPECTED_C_ONLY_PARTIAL_CONTROL_EVIDENCE_ID
        or tuple(reused.get("question_ids", ())) != QUESTION_IDS
        or reused.get("result_count") != 70
    ):
        raise BlockAPaidGateError("resume reused-control identity mismatch")
    source_hashes = manifest.get("source_hashes")
    if not isinstance(source_hashes, Mapping) or source_hashes.get("runner") != EXPECTED_C_ONLY_ORIGINAL_RUNNER_SHA256:
        raise BlockAPaidGateError("resume original runner source binding mismatch")
    current_sources = _source_hashes()
    for name, current in current_sources.items():
        if name != "runner" and source_hashes.get(name) != current:
            raise BlockAPaidGateError(f"resume source binding drift: {name}")
    _validate_production_defaults()
    closure_manifest, bindings, field_manifest = _validate_c_challenger_closure(closure_root, runtime_input, ru_manifest_path)
    values = dict(environment or {}) if environment is not None else None
    env = values if values is not None else __import__("os").environ
    endpoint = env.get("BAILIAN_BASE_URL")
    if not isinstance(endpoint, str) or not endpoint:
        raise BlockAPaidGateError("BAILIAN_BASE_URL is required for resume configuration preflight")
    generation_config = BailianControlConfig(
        region="cn-beijing", endpoint=endpoint, workspace=workspace_from_bailian_base_url(endpoint),
        model_id=BASELINE_QWEN_MODEL_ID, enable_thinking=False, max_output_tokens=2048,
        max_attempts=1, timeout_seconds=30.0,
    )
    rerank_config = DashScopeQwenRerankConfig(
        region="cn-beijing", endpoint="https://ws-gdq9z4ufdb87egio.cn-beijing.maas.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
        workspace="ws-gdq9z4ufdb87egio", timeout_seconds=60.0,
    )
    config = BlockAQualityGateConfig()
    questions = load_m2_runtime_questions(runtime_input)
    if [question.question_id for question in questions] != list(QUESTION_IDS):
        raise BlockAPaidGateError("resume runtime questions are not Q001-Q070")
    if (
        manifest.get("configuration") != config.projection()
        or manifest.get("configuration_identity") != config.identity
        or manifest.get("generation_config_identity") != generation_config.execution_config_identity
        or manifest.get("reranker") != rerank_config.identity_projection()
        or manifest.get("closure", {}).get("run_identity") != closure_manifest.get("run_identity")
        or manifest.get("inputs", {}).get("query_artifact_identity") != bindings[0].artifact_identity
        or manifest.get("inputs", {}).get("query_vectors_sha256") != bindings[0].vectors_sha256
    ):
        raise BlockAPaidGateError("resume live configuration binding mismatch")
    for key, path in (("runtime_input", runtime_input), ("ru_manifest", ru_manifest_path), ("dense_manifest", dense_manifest_path), ("field_aware_lexical_manifest", field_manifest)):
        descriptor = manifest.get("inputs", {}).get(key)
        if not isinstance(descriptor, Mapping):
            raise BlockAPaidGateError(f"resume input descriptor missing: {key}")
        _descriptor_matches(path, descriptor, f"resume input {key}")
    recorded_control_root = Path(str(reused.get("root")))
    if recorded_control_root.resolve() != EXPECTED_REUSABLE_CONTROL_ROOT.resolve():
        raise BlockAPaidGateError("resume reused-control root binding mismatch")
    # The accepted evidence identity is over descriptors with this original
    # relative spelling; verify the recorded absolute root separately above.
    control_root = EXPECTED_REUSABLE_CONTROL_ROOT
    validated_control = _validate_reused_control_evidence(
        control_root, questions=questions, legacy_lexical_manifest_path=legacy_lexical_manifest_path,
        dense_manifest_path=dense_manifest_path, generation_config_identity=generation_config.execution_config_identity,
    )
    if validated_control.get("evidence_identity") != EXPECTED_C_ONLY_PARTIAL_CONTROL_EVIDENCE_ID:
        raise BlockAPaidGateError("resume reused-control evidence does not reconcile")
    attempts = manifest.get("provider_attempts")
    if not isinstance(attempts, Mapping) or attempts.get("path") != "metadata/provider_attempts.jsonl" or attempts.get("row_count") != _ledger_row_count(ledger_path):
        raise BlockAPaidGateError("resume manifest provider ledger count mismatch")
    artifacts = manifest.get("artifacts")
    if isinstance(artifacts, Mapping) and "provider_attempts" in artifacts:
        descriptor = artifacts["provider_attempts"]
        if not isinstance(descriptor, Mapping):
            raise BlockAPaidGateError("resume provider ledger artifact descriptor is invalid")
        _descriptor_matches(ledger_path, descriptor, "resume provider ledger")
    counters, occurrences = _resume_ledger_state(ledger_path, expected_run_identity)
    field_identity = _read_json(field_manifest, "resume field manifest").get("arm_build_identity")
    dense_identity = _read_json(dense_manifest_path, "resume Dense manifest").get("arm_build_identity")
    if not isinstance(field_identity, str) or not isinstance(dense_identity, str):
        raise BlockAPaidGateError("resume retrieval arm identities are unreadable")
    completed: list[str] = []
    completed_results: dict[str, dict[str, Any]] = {}
    results_root = output_root / "results"
    if results_root.exists():
        for child in results_root.iterdir():
            if child.is_dir() and child.name not in QUESTION_IDS:
                raise BlockAPaidGateError("resume results contain an unknown question directory")
    for question in questions:
        qid = question.question_id
        result_root = results_root / qid / "vnext" / qid
        result_path = result_root / "rag_result.json"
        if result_path.exists():
            if set(occurrences[qid]) != {"reranker", "generation"}:
                raise BlockAPaidGateError(f"resume completed result/provider mismatch: {qid}")
            completed_results[qid] = _validate_completed_vnext_result(
                result_root, question=question, field_identity=field_identity, dense_identity=dense_identity,
                generation_config_identity=generation_config.execution_config_identity,
            )
            completed.append(qid)
        elif (results_root / qid).exists() or occurrences[qid]:
            raise BlockAPaidGateError(f"resume incomplete persisted question state: {qid}")
    if tuple(completed) != QUESTION_IDS[:len(completed)]:
        raise BlockAPaidGateError("resume completed questions are not a contiguous prefix")
    if tuple(manifest.get("completed_questions", ())) != tuple(completed) or tuple(manifest.get("completed_vnext_questions", ())) != tuple(completed):
        raise BlockAPaidGateError("resume completed-question manifest mismatch")
    if manifest.get("actual_provider_calls") is not None and manifest.get("actual_provider_calls") != counters:
        raise BlockAPaidGateError("resume manifest provider totals mismatch")
    first_incomplete = QUESTION_IDS[len(completed)] if len(completed) < len(QUESTION_IDS) else None
    remaining = {key: CONTROL_REUSE_PROVIDER_BUDGET[key] - counters[key] for key in CONTROL_REUSE_PROVIDER_BUDGET}
    if any(value < 0 for value in remaining.values()):
        raise BlockAPaidGateError("resume remaining provider budget is negative")
    return {
        "manifest": dict(manifest), "ledger_path": ledger_path, "questions": questions, "bindings": bindings,
        "field_manifest": field_manifest, "generation_config": generation_config, "rerank_config": rerank_config,
        "config": config, "completed_question_ids": completed, "completed_results": completed_results,
        "first_incomplete_question_id": first_incomplete, "consumed_provider_calls": counters,
        "remaining_provider_calls": remaining, "current_resume_runner_sha256": current_sources["runner"],
        "original_runner_sha256": EXPECTED_C_ONLY_ORIGINAL_RUNNER_SHA256,
        "manifest_descriptor": _descriptor(manifest_path), "ledger_descriptor": _descriptor(ledger_path),
        "reused_control": validated_control,
    }


def preflight_block_a_paid_full70_control_reuse_resume(
    *,
    output_root: Path,
    expected_run_identity: str = EXPECTED_C_ONLY_PARTIAL_RUN_ID,
    closure_root: Path = DEFAULT_CLOSURE_ROOT,
    runtime_input: Path = DEFAULT_RUNTIME_INPUT,
    ru_manifest_path: Path = DEFAULT_RU_MANIFEST,
    legacy_lexical_manifest_path: Path = DEFAULT_LEGACY_LEXICAL_MANIFEST,
    dense_manifest_path: Path = DEFAULT_DENSE_MANIFEST,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Provider-free preflight for only the authorized partial C-only root."""

    context = _reconcile_c_only_resume(
        output_root=Path(output_root), expected_run_identity=expected_run_identity,
        closure_root=Path(closure_root), runtime_input=Path(runtime_input), ru_manifest_path=Path(ru_manifest_path),
        legacy_lexical_manifest_path=Path(legacy_lexical_manifest_path), dense_manifest_path=Path(dense_manifest_path),
        environment=environment,
    )
    return {
        "schema_version": RESUME_SCHEMA_VERSION, "status": "provider_free_resume_ready",
        "run_identity": expected_run_identity, "output_root": str(Path(output_root).resolve()),
        "completed_question_ids": context["completed_question_ids"],
        "first_incomplete_question_id": context["first_incomplete_question_id"],
        "consumed_provider_calls": context["consumed_provider_calls"],
        "remaining_provider_calls": context["remaining_provider_calls"],
        "all_provider_occurrences_resolved": True,
        "manifest": context["manifest_descriptor"], "provider_ledger": context["ledger_descriptor"],
        "reused_control_evidence_identity": context["reused_control"]["evidence_identity"],
        "original_runner_sha256": context["original_runner_sha256"],
        "resume_runner_sha256": context["current_resume_runner_sha256"],
        "provider_calls_executed": {"embedding": 0, "reranker": 0, "generation": 0, "control_regeneration": 0},
        "q056_policy": "excluded_from_clean_advancement_gate",
    }


def resume_block_a_paid_full70_control_reuse(
    *,
    output_root: Path,
    expected_run_identity: str = EXPECTED_C_ONLY_PARTIAL_RUN_ID,
    closure_root: Path = DEFAULT_CLOSURE_ROOT,
    runtime_input: Path = DEFAULT_RUNTIME_INPUT,
    ru_manifest_path: Path = DEFAULT_RU_MANIFEST,
    legacy_lexical_manifest_path: Path = DEFAULT_LEGACY_LEXICAL_MANIFEST,
    dense_manifest_path: Path = DEFAULT_DENSE_MANIFEST,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resume the exact C-only root after complete provider-free reconciliation."""

    context = _reconcile_c_only_resume(
        output_root=Path(output_root), expected_run_identity=expected_run_identity,
        closure_root=Path(closure_root), runtime_input=Path(runtime_input), ru_manifest_path=Path(ru_manifest_path),
        legacy_lexical_manifest_path=Path(legacy_lexical_manifest_path), dense_manifest_path=Path(dense_manifest_path),
        environment=environment,
    )
    manifest = context["manifest"]
    completed = context["completed_question_ids"]
    remaining_questions = list(context["questions"])[len(completed):]
    if not remaining_questions:
        if manifest.get("status") != "complete":
            raise BlockAPaidGateError("resume has no remaining questions but manifest is not complete")
        return manifest
    remaining = context["remaining_provider_calls"]
    if remaining != {"embedding": 0, "reranker": len(remaining_questions), "generation": len(remaining_questions), "control_regeneration": 0}:
        raise BlockAPaidGateError("resume planned cumulative provider budget is not exact")
    values = dict(environment or {}) if environment is not None else None
    env = values if values is not None else __import__("os").environ
    # No provider object is created until the exact suffix and cumulative ceiling are proven above.
    gen_transport = BailianOpenAICompatibleTransport.from_environment(context["generation_config"], environment=env)
    rerank_transport = DashScopeQwenRerankTransport.from_environment(context["rerank_config"], environment=env)
    output_root = Path(output_root)
    ledger_path = context["ledger_path"]
    counters = dict(context["consumed_provider_calls"])
    vnext_state = None
    failure: dict[str, Any] | None = None
    try:
        vnext_state = prepare_rag_state(
            retrieval_unit_manifest_path=Path(ru_manifest_path), lexical_manifest_path=context["field_manifest"],
            dense_manifest_path=Path(dense_manifest_path),
        )
        vnext_config = SingleQuestionBackendConfig(
            candidate_supply_depth=500, rerank_depth=500, final_top_n=20, rerank_output_k=20,
            reranker_enabled=True, rrf_k=60, reranker_projection_max_chars=6000,
            fusion_config=context["config"].fusion_config, assembly_config=context["config"].assembly_config,
        )
        for question, binding in zip(context["questions"][len(completed):], context["bindings"][len(completed):], strict=True):
            qid = question.question_id
            if qid != binding.question_id or question.question != binding.question:
                raise BlockAPaidGateError(f"resume query binding mismatch: {qid}")
            vnext_dir = output_root / "results" / qid / "vnext"
            if vnext_dir.exists():
                raise BlockAPaidGateError(f"resume refuses to overwrite vNext result directory: {qid}")
            generation_provider = _LedgerGenerationProvider(
                BailianGenerationProvider(context["generation_config"], gen_transport), ledger_path, "vnext", qid,
                counters, maximum_calls=MAX_C_ONLY_GENERATION_CALLS,
            )
            reranker = _LedgerReranker(rerank_transport, ledger_path, qid, counters)
            result = run_single_question(
                vnext_state, question.question, precomputed_query=binding, generation_provider=generation_provider,
                reranker=reranker, config=vnext_config, execution_identity=qid, output_root=vnext_dir, request_label=qid,
            )
            if result.get("status") != "succeeded":
                raise BlockAPaidGateError(f"resume vNext provider/local stage failed at {qid}")
            result_root = vnext_dir / qid
            context["completed_results"][qid] = _validate_completed_vnext_result(
                result_root, question=question,
                field_identity=_read_json(context["field_manifest"], "resume field manifest").get("arm_build_identity"),
                dense_identity=_read_json(Path(dense_manifest_path), "resume Dense manifest").get("arm_build_identity"),
                generation_config_identity=context["generation_config"].execution_config_identity,
            )
            completed.append(qid)
            manifest["completed_vnext_questions"] = list(completed)
            manifest["completed_questions"] = list(completed)
            manifest["provider_attempts"]["row_count"] = _ledger_row_count(ledger_path)
            manifest["actual_provider_calls"] = dict(counters)
            _write_json(output_root / "metadata" / "manifest.json", manifest)
    except Exception as exc:
        failure = {"type": type(exc).__name__, "message": str(exc), "completed_questions": len(completed)}
    finally:
        if vnext_state is not None:
            vnext_state.close()
    if failure is not None:
        manifest["status"] = "partial_failed"
        manifest["failure"] = failure
        atomic_write(output_root / "errors" / "partial-state.json", canonical_json_bytes(failure))
    else:
        review_rows = []
        for question in context["questions"]:
            qid = question.question_id
            control_path = Path(str(context["reused_control"]["root"])) / "results" / qid / "control" / qid / "rag_result.json"
            review_rows.append({
                "question_id": qid,
                "control": _review_projection(qid, question.question, _read_json(control_path, f"reused control result {qid}")),
                "vnext": _review_projection(qid, question.question, context["completed_results"][qid]),
                "excluded_from_clean_advancement_gate": qid == "Q056",
            })
        if len(review_rows) != 70 or counters != {"embedding": 0, "reranker": 70, "generation": 70, "control_regeneration": 0}:
            raise BlockAPaidGateError("resume final cumulative accounting/completeness mismatch")
        atomic_write(output_root / "review" / "paired_q001-q070.jsonl", b"".join(canonical_json_bytes(row) + b"\n" for row in review_rows))
        answer_rows = [{
            "question_id": row["question_id"], "question": row["control"]["question"],
            "control": {"answer": row["control"]["answer"], "citation_validation": row["control"]["citation_validation"], "evidence_ids": [item.get("evidence_id") for item in row["control"]["evidence_packet"]]},
            "c_challenger": {"answer": row["vnext"]["answer"], "citation_validation": row["vnext"]["citation_validation"], "evidence_ids": [item.get("evidence_id") for item in row["vnext"]["evidence_packet"]], "retrieval_lineage": row["vnext"]["retrieval_lineage"], "rerank_lineage": row["vnext"]["rerank_lineage"]},
            "excluded_from_clean_advancement_gate": row["excluded_from_clean_advancement_gate"],
        } for row in review_rows]
        atomic_write(output_root / "review" / "answer_evidence_comparison.jsonl", b"".join(canonical_json_bytes(row) + b"\n" for row in answer_rows))
        manifest["status"] = "complete"
        manifest["completed_question_count"] = 70
    manifest["completed_questions"] = list(completed)
    manifest["completed_vnext_questions"] = list(completed)
    manifest["actual_provider_calls"] = dict(counters)
    manifest["provider_attempts"]["row_count"] = _ledger_row_count(ledger_path)
    manifest["artifacts"] = {"provider_attempts": _descriptor(ledger_path)}
    for name, relative in (("paired_review", "review/paired_q001-q070.jsonl"), ("answer_evidence_comparison", "review/answer_evidence_comparison.jsonl")):
        path = output_root / relative
        if path.is_file():
            manifest["artifacts"][name] = _descriptor(path)
    _write_json(output_root / "metadata" / "manifest.json", manifest)
    return manifest


def run_block_a_paid_full70(
    *,
    output_root: Path,
    closure_root: Path = DEFAULT_CLOSURE_ROOT,
    runtime_input: Path = DEFAULT_RUNTIME_INPUT,
    ru_manifest_path: Path = DEFAULT_RU_MANIFEST,
    legacy_lexical_manifest_path: Path = DEFAULT_LEGACY_LEXICAL_MANIFEST,
    dense_manifest_path: Path = DEFAULT_DENSE_MANIFEST,
    reuse_control_root: Path | None = None,
    expected_reuse_planning_identity: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Execute the paired gate, or explicitly reuse its accepted control arm."""

    output_root = Path(output_root)
    if output_root.exists():
        raise FileExistsError("paid output root already exists; refusing to start")
    reuse_root = Path(reuse_control_root) if reuse_control_root is not None else None
    if reuse_root is not None and output_root.resolve() == reuse_root.resolve():
        raise BlockAPaidGateError("paid output root must be separate from reused control evidence")
    _validate_production_defaults()
    closure_validator = _validate_c_challenger_closure if reuse_root is not None else _validate_closure
    closure_manifest, bindings, field_manifest = closure_validator(Path(closure_root), Path(runtime_input), Path(ru_manifest_path))
    source_hashes = _source_hashes()
    config = BlockAQualityGateConfig()
    values = dict(environment or {}) if environment is not None else None
    env = values if values is not None else __import__("os").environ
    endpoint = env.get("BAILIAN_BASE_URL")
    if not isinstance(endpoint, str) or not endpoint:
        raise BlockAPaidGateError("BAILIAN_BASE_URL is required before output-root creation")
    workspace = workspace_from_bailian_base_url(endpoint)
    generation_config = BailianControlConfig(
        region="cn-beijing", endpoint=endpoint, workspace=workspace,
        model_id=BASELINE_QWEN_MODEL_ID, enable_thinking=False,
        max_output_tokens=2048, max_attempts=1, timeout_seconds=30.0,
    )
    rerank_config = DashScopeQwenRerankConfig(
        region="cn-beijing", endpoint="https://ws-gdq9z4ufdb87egio.cn-beijing.maas.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
        workspace="ws-gdq9z4ufdb87egio", timeout_seconds=60.0,
    )
    questions = load_m2_runtime_questions(Path(runtime_input))
    if [question.question_id for question in questions] != list(QUESTION_IDS):
        raise BlockAPaidGateError("runtime questions are not Q001-Q070")
    reused_control: dict[str, Any] | None = None
    if reuse_root is not None:
        reused_control = _validate_reused_control_evidence(
            reuse_root,
            questions=questions,
            legacy_lexical_manifest_path=Path(legacy_lexical_manifest_path),
            dense_manifest_path=Path(dense_manifest_path),
            generation_config_identity=generation_config.execution_config_identity,
        )
        planning_identity = sha256_json({
            "kind": "p04-block-a-paid-c-only-preflight-0.1",
            "date": "20260915",
            "head": _git_state()["head"],
            "candidate_retrieval": source_hashes["candidate_retrieval"],
            "control_root": ".local/p04-block-a-paid-full70-20260914T061529Z-ab48c2df",
            "control_manifest": reused_control["manifest"]["sha256"],
            "query_vectors": bindings[0].vectors_sha256,
            "dense": ACCEPTED_QWEN_DENSE_VECTORS_SHA256,
            "reranker_budget": 70,
            "generation_budget": 70,
        })
        if expected_reuse_planning_identity is not None and expected_reuse_planning_identity != planning_identity:
            raise BlockAPaidGateError("reserved C-only planning identity mismatch")
    provider_budget = dict(CONTROL_REUSE_PROVIDER_BUDGET) if reused_control is not None else {
        "embedding": 0,
        "reranker": MAX_RERANKER_CALLS,
        "generation": MAX_GENERATION_CALLS,
        "control_regeneration": 70,
    }
    if reused_control is not None and provider_budget != CONTROL_REUSE_PROVIDER_BUDGET:
        raise BlockAPaidGateError("C-only provider budget is not exactly 0/70/70 with zero controls")
    run_identity = sha256_json({"schema_version": SCHEMA_VERSION, "config": config.projection(), "generation": generation_config.output_affecting_projection(), "reranker": rerank_config.identity_projection(), "git": _git_state(), "sources": source_hashes, "closure_run_identity": closure_manifest.get("run_identity"), "reused_control": reused_control, "provider_budget": provider_budget})
    output_root.mkdir(parents=True)
    ledger_path = output_root / "metadata" / "provider_attempts.jsonl"
    manifest = {
        "schema_version": SCHEMA_VERSION, "status": "in_progress", "run_identity": run_identity,
        "git": _git_state(), "source_hashes": source_hashes,
        "closure": {"root": str(Path(closure_root).resolve()), "run_identity": closure_manifest.get("run_identity")},
        "inputs": {"runtime_input": _descriptor(Path(runtime_input)), "ru_manifest": _descriptor(Path(ru_manifest_path)), "dense_manifest": _descriptor(Path(dense_manifest_path)), "field_aware_lexical_manifest": _descriptor(field_manifest), "query_artifact_identity": bindings[0].artifact_identity, "query_vectors_sha256": bindings[0].vectors_sha256},
        "configuration": config.projection(), "configuration_identity": config.identity,
        "generation": generation_config.audit_projection(), "generation_config_identity": generation_config.execution_config_identity,
        "reranker": rerank_config.identity_projection(), "budgets": provider_budget,
        "question_ids": list(QUESTION_IDS), "completed_questions": [], "provider_attempts": {"path": "metadata/provider_attempts.jsonl", "row_count": 0},
        "reused_control": reused_control,
        "control_reuse": reused_control is not None,
        "q056_policy": "excluded_from_clean_advancement_gate",
    }
    _write_json(output_root / "metadata" / "manifest.json", manifest)
    _append_ledger(ledger_path, {"event": "run_started", "run_identity": run_identity, "embedding_calls": 0})
    gen_transport = BailianOpenAICompatibleTransport.from_environment(generation_config, environment=env)
    rerank_transport = DashScopeQwenRerankTransport.from_environment(rerank_config, environment=env)
    counters = {"embedding": 0, "reranker": 0, "generation": 0}
    control_review: dict[str, dict[str, Any]] = {}
    vnext_review: dict[str, dict[str, Any]] = {}
    failure: dict[str, Any] | None = None
    try:
        # The accepted lexical indexes are large.  Run each arm in the fixed
        # Q001-Q070 order and release its state before loading the other arm.
        if reused_control is not None:
            for question in questions:
                qid = question.question_id
                control_path = reuse_root / "results" / qid / "control" / qid / "rag_result.json"
                control_review[qid] = _review_projection(qid, question.question, _read_json(control_path, f"reused control result {qid}"))
            manifest["completed_control_questions"] = list(QUESTION_IDS)
            manifest["reused_control_questions"] = list(QUESTION_IDS)
            _write_json(output_root / "metadata" / "manifest.json", manifest)
        else:
            control_state = prepare_rag_state(retrieval_unit_manifest_path=ru_manifest_path, lexical_manifest_path=legacy_lexical_manifest_path, dense_manifest_path=dense_manifest_path)
            for question, binding in zip(questions, bindings, strict=True):
                if question.question_id != binding.question_id or question.question != binding.question:
                    raise BlockAPaidGateError(f"query binding mismatch: {question.question_id}")
                qid = question.question_id
                control_dir = output_root / "results" / qid / "control"
                control_provider = _LedgerGenerationProvider(BailianGenerationProvider(generation_config, gen_transport), ledger_path, "control", qid, counters)
                control_result = run_single_question(
                    control_state,
                    question.question,
                    precomputed_query=binding,
                    generation_provider=control_provider,
                    config=SingleQuestionBackendConfig(
                        candidate_depth=20,
                        candidate_supply_depth=20,
                        rerank_depth=20,
                        rerank_output_k=20,
                        final_top_n=20,
                        reranker_enabled=False,
                        rrf_k=60,
                    ),
                    execution_identity=qid,
                    output_root=control_dir,
                    request_label=qid,
                )
                if control_result.get("status") != "succeeded":
                    raise BlockAPaidGateError(f"control Generation failed at {qid}")
                control_persisted = json.loads((control_dir / qid / "rag_result.json").read_text(encoding="utf-8"))
                control_review[qid] = _review_projection(qid, question.question, control_persisted)
                manifest["completed_control_questions"] = list(control_review)
                manifest["provider_attempts"]["row_count"] = _ledger_row_count(ledger_path)
                _write_json(output_root / "metadata" / "manifest.json", manifest)
            control_state.close()
        vnext_state = prepare_rag_state(retrieval_unit_manifest_path=ru_manifest_path, lexical_manifest_path=field_manifest, dense_manifest_path=dense_manifest_path)
        vnext_config = SingleQuestionBackendConfig(candidate_supply_depth=500, rerank_depth=500, final_top_n=20, rerank_output_k=20, reranker_enabled=True, rrf_k=60, reranker_projection_max_chars=6000, fusion_config=config.fusion_config, assembly_config=config.assembly_config)
        for question, binding in zip(questions, bindings, strict=True):
            qid = question.question_id
            vnext_dir = output_root / "results" / qid / "vnext"
            if question.question_id != binding.question_id or question.question != binding.question:
                raise BlockAPaidGateError(f"query binding mismatch: {question.question_id}")
            vnext_provider = _LedgerGenerationProvider(BailianGenerationProvider(generation_config, gen_transport), ledger_path, "vnext", qid, counters, maximum_calls=MAX_C_ONLY_GENERATION_CALLS if reused_control is not None else MAX_GENERATION_CALLS)
            vnext_reranker = _LedgerReranker(rerank_transport, ledger_path, qid, counters)
            vnext_result = run_single_question(vnext_state, question.question, precomputed_query=binding, generation_provider=vnext_provider, reranker=vnext_reranker, config=vnext_config, execution_identity=qid, output_root=vnext_dir, request_label=qid)
            if vnext_result.get("status") != "succeeded":
                raise BlockAPaidGateError(f"vNext provider/local stage failed at {qid}")
            vnext_persisted = json.loads((vnext_dir / qid / "rag_result.json").read_text(encoding="utf-8"))
            vnext_review[qid] = _review_projection(qid, question.question, vnext_persisted)
            manifest["completed_vnext_questions"] = list(vnext_review)
            manifest["completed_questions"] = [qid for qid in QUESTION_IDS if qid in control_review and qid in vnext_review]
            manifest["provider_attempts"]["row_count"] = _ledger_row_count(ledger_path)
            _write_json(output_root / "metadata" / "manifest.json", manifest)
    except Exception as exc:
        failure = {"type": type(exc).__name__, "message": str(exc), "completed_questions": len(set(control_review) & set(vnext_review))}
    finally:
        for state_name in ("control_state", "vnext_state"):
            state = locals().get(state_name)
            if state is not None:
                state.close()
    review_rows = [
        {"question_id": qid, "control": control_review[qid], "vnext": vnext_review[qid], "excluded_from_clean_advancement_gate": qid == "Q056"}
        for qid in QUESTION_IDS if qid in control_review and qid in vnext_review
    ]
    if failure is not None:
        manifest["status"] = "partial_failed"
        manifest["completed_question_count"] = len(review_rows)
        manifest["failure"] = failure
        atomic_write(output_root / "errors" / "partial-state.json", canonical_json_bytes(failure))
    elif len(review_rows) == 70:
        review_body = b"".join(canonical_json_bytes(row) + b"\n" for row in review_rows)
        atomic_write(output_root / "review" / "paired_q001-q070.jsonl", review_body)
        answer_evidence_rows = [
            {
                "question_id": row["question_id"],
                "question": row["control"]["question"],
                "control": {
                    "answer": row["control"]["answer"],
                    "citation_validation": row["control"]["citation_validation"],
                    "evidence_ids": [item.get("evidence_id") for item in row["control"]["evidence_packet"]],
                },
                "c_challenger": {
                    "answer": row["vnext"]["answer"],
                    "citation_validation": row["vnext"]["citation_validation"],
                    "evidence_ids": [item.get("evidence_id") for item in row["vnext"]["evidence_packet"]],
                    "retrieval_lineage": row["vnext"]["retrieval_lineage"],
                    "rerank_lineage": row["vnext"]["rerank_lineage"],
                },
                "excluded_from_clean_advancement_gate": row["excluded_from_clean_advancement_gate"],
            }
            for row in review_rows
        ]
        answer_evidence_body = b"".join(canonical_json_bytes(row) + b"\n" for row in answer_evidence_rows)
        atomic_write(output_root / "review" / "answer_evidence_comparison.jsonl", answer_evidence_body)
        manifest["status"] = "complete"
        manifest["completed_question_count"] = 70
    else:
        manifest["status"] = "partial_failed"
        manifest["completed_question_count"] = len(review_rows)
    manifest["actual_provider_calls"] = counters
    manifest["provider_attempts"]["row_count"] = _ledger_row_count(ledger_path)
    artifacts = {"provider_attempts": _descriptor(ledger_path)}
    if (output_root / "review" / "paired_q001-q070.jsonl").is_file():
        artifacts["paired_review"] = _descriptor(output_root / "review" / "paired_q001-q070.jsonl")
    if (output_root / "review" / "answer_evidence_comparison.jsonl").is_file():
        artifacts["answer_evidence_comparison"] = _descriptor(output_root / "review" / "answer_evidence_comparison.jsonl")
    manifest["artifacts"] = artifacts
    _write_json(output_root / "metadata" / "manifest.json", manifest)
    return manifest


__all__ = [
    "BlockAPaidGateError",
    "preflight_block_a_paid_full70_control_reuse",
    "preflight_block_a_paid_full70_control_reuse_resume",
    "resume_block_a_paid_full70_control_reuse",
    "run_block_a_paid_full70",
]
