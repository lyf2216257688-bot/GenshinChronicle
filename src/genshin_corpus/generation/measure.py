"""Minimal six-question Measure harness for the closed P04 RAG baseline.

Only an explicitly supplied accepted-question JSONL is execution input.  The
module intentionally has no reader for transcript, answer, explanation, or
review-reference material.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
import re
import sys
from contextlib import contextmanager
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.retrieval.candidate_retrieval import (
    DEFAULT_DENSE_MODEL_REVISION,
    encode_dense_query,
    load_dense_query_model,
    retrieve_candidates,
)
from genshin_corpus.retrieval.evidence_assembly import (
    EvidenceAssemblyConfig,
    assemble_evidence_packet,
    write_evidence_packet,
)

from .generation import (
    BASELINE_QWEN_MODEL_ID,
    BailianControlConfig,
    BailianGenerationProvider,
    BailianOpenAICompatibleTransport,
    BailianTransport,
    GenerationConfigurationError,
    GenerationContractError,
    project_generation_request,
    workspace_from_bailian_base_url,
    write_generation_result,
)


M1_SCHEMA_VERSION = "p04-rag-m1-0.1"
M1_QUESTION_COUNT = 6
M1_PROVIDER_ATTEMPT_BUDGET = 6
_QUESTION_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_MODES = ("lexical", "dense", "hybrid")

DEFAULT_M1_ACCEPTED_INPUT = Path(".local/p04-rag-m1/questions.accepted.jsonl")
DEFAULT_M1_BASELINE_ROOT = Path(
    "data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02"
)
DEFAULT_M1_MODEL_DIR = Path(".local/w6-models/bge-small-zh-v1.5-7999e1d")
DEFAULT_M1_RUNTIME_ROOT = Path(".local/w6-runtime")


class M1MeasureError(ValueError):
    """Raised when the narrow M1 execution boundary is not satisfied."""


@dataclass(frozen=True)
class AcceptedQuestion:
    """The sole runtime question shape for M1."""

    question_id: str
    question: str

    @property
    def question_identity(self) -> str:
        return sha256_json({"question_id": self.question_id, "question": self.question})

    def to_dict(self) -> dict[str, str]:
        return {"question_id": self.question_id, "question": self.question}


@dataclass(frozen=True)
class M1Baseline:
    """Caller-overridable paths for the already materialized RAG baseline."""

    root: Path = DEFAULT_M1_BASELINE_ROOT
    model_dir: Path = DEFAULT_M1_MODEL_DIR
    runtime_root: Path = DEFAULT_M1_RUNTIME_ROOT

    @property
    def retrieval_unit_manifest(self) -> Path:
        return self.root / "ru" / "metadata" / "manifest.json"

    @property
    def lexical_manifest(self) -> Path:
        return self.root / "lexical" / "metadata" / "manifest.json"

    @property
    def dense_manifest(self) -> Path:
        return self.root / "dense" / "metadata" / "manifest.json"


def _read_json_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise M1MeasureError(f"{label} is unreadable") from exc
    if not isinstance(value, Mapping):
        raise M1MeasureError(f"{label} must be a JSON object")
    return value


def _validate_question(value: Any, line_number: int) -> AcceptedQuestion:
    if not isinstance(value, Mapping) or set(value) != {"question_id", "question"}:
        raise M1MeasureError(
            f"accepted question line {line_number} must contain exactly question_id and question"
        )
    question_id, question = value["question_id"], value["question"]
    if (
        not isinstance(question_id, str)
        or question_id in {".", ".."}
        or not _QUESTION_ID.fullmatch(question_id)
        or Path(question_id).name != question_id
        or Path(question_id).is_absolute()
    ):
        raise M1MeasureError(f"accepted question line {line_number}.question_id is unsafe")
    if not isinstance(question, str) or not question.strip():
        raise M1MeasureError(f"accepted question {question_id}.question must be a non-empty string")
    return AcceptedQuestion(question_id=question_id, question=question)


def load_accepted_questions(path: Path, *, required_count: int = M1_QUESTION_COUNT) -> list[AcceptedQuestion]:
    """Load exactly six explicit accepted questions; reject all other content."""

    path = Path(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise M1MeasureError("accepted question input is unreadable") from exc
    questions: list[AcceptedQuestion] = []
    seen: set[str] = set()
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            raise M1MeasureError(f"accepted question line {line_number} must not be blank")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise M1MeasureError(f"accepted question line {line_number} is not JSON") from exc
        question = _validate_question(value, line_number)
        if question.question_id in seen:
            raise M1MeasureError(f"duplicate accepted question_id: {question.question_id}")
        seen.add(question.question_id)
        questions.append(question)
    if len(questions) != required_count:
        raise M1MeasureError(f"M1 requires exactly {required_count} accepted questions")
    return questions


def _baseline_metadata(baseline: M1Baseline) -> dict[str, Any]:
    ru = _read_json_object(baseline.retrieval_unit_manifest, "M1 Retrieval Unit manifest")
    lexical = _read_json_object(baseline.lexical_manifest, "M1 lexical manifest")
    dense = _read_json_object(baseline.dense_manifest, "M1 Dense manifest")
    if ru.get("status") != "complete" or lexical.get("status") != "complete" or dense.get("status") != "complete":
        raise M1MeasureError("M1 baseline manifests must all be complete")
    ru_identity = ru.get("build_identity")
    if not isinstance(ru_identity, str) or not ru_identity:
        raise M1MeasureError("M1 Retrieval Unit manifest lacks build_identity")
    if lexical.get("retrieval_unit_build_identity") != ru_identity:
        raise M1MeasureError("M1 lexical manifest is not bound to the Retrieval Unit build")
    if dense.get("retrieval_unit_build_identity") != ru_identity:
        raise M1MeasureError("M1 Dense manifest is not bound to the Retrieval Unit build")
    if dense.get("model_revision") != DEFAULT_DENSE_MODEL_REVISION:
        raise M1MeasureError("M1 Dense model revision does not match the pinned baseline")
    model_file = baseline.model_dir / "model.safetensors"
    expected_model_sha256 = dense.get("model_sha256")
    if not isinstance(expected_model_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_model_sha256):
        raise M1MeasureError("M1 Dense manifest lacks a valid model SHA-256")
    try:
        actual_model_sha256 = sha256(model_file.read_bytes()).hexdigest()
    except OSError as exc:
        raise M1MeasureError("M1 local Dense model weights are unavailable") from exc
    if actual_model_sha256 != expected_model_sha256:
        raise M1MeasureError("M1 local Dense model weights do not match the Dense manifest")
    return {
        "retrieval_unit_build_identity": ru_identity,
        "lexical_build_identity": lexical.get("arm_build_identity"),
        "dense_build_identity": dense.get("arm_build_identity"),
        "dense_model_revision": dense.get("model_revision"),
        "dense_model_sha256": expected_model_sha256,
    }


@contextmanager
def _dense_runtime_path(runtime_root: Path):
    """Expose the bundled runtime only for the local Dense operation."""

    original_path = list(sys.path)
    sys.path.insert(0, str(Path(runtime_root)))
    try:
        yield
    finally:
        sys.path[:] = original_path


def _probe_dense_query_runtime(baseline: M1Baseline, dense_metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Exercise the same local query encoder used by Dense retrieval."""

    runtime_root = Path(baseline.runtime_root)
    if not runtime_root.is_dir():
        raise M1MeasureError("M1 Dense runtime root is unavailable")
    dimension = dense_metadata.get("embedding_dimension")
    instruction = dense_metadata.get("instruction")
    if not isinstance(dimension, int) or isinstance(dimension, bool) or dimension <= 0:
        raise M1MeasureError("M1 Dense manifest lacks a valid embedding dimension")
    if not isinstance(instruction, str):
        raise M1MeasureError("M1 Dense manifest lacks a valid query instruction")
    try:
        with _dense_runtime_path(runtime_root):
            vector = encode_dense_query(
                baseline.model_dir,
                "M1 Dense query runtime preflight",
                instruction=instruction,
            )
        import numpy as np

        probe = np.asarray(vector, dtype=np.float32)
        if probe.ndim != 1 or probe.shape[0] != dimension or not np.isfinite(probe).all():
            raise M1MeasureError("M1 Dense query runtime returned an invalid vector")
    except M1MeasureError:
        raise
    except Exception as exc:
        raise M1MeasureError("M1 Dense query runtime/model is unavailable") from exc
    return {
        "runtime_root": str(runtime_root),
        "embedding_dimension": dimension,
        "device": "cpu",
        "local_files_only": True,
    }


def preflight_m1(
    accepted_input: Path = DEFAULT_M1_ACCEPTED_INPUT,
    *,
    baseline: M1Baseline = M1Baseline(),
) -> dict[str, Any]:
    """Report readiness without writing artifacts or invoking any provider."""

    try:
        metadata = _baseline_metadata(baseline)
        dense_manifest = _read_json_object(baseline.dense_manifest, "M1 Dense manifest")
        metadata["dense_runtime"] = _probe_dense_query_runtime(baseline, dense_manifest)
    except M1MeasureError as exc:
        return {
            "schema_version": M1_SCHEMA_VERSION,
            "status": "not_ready",
            "accepted_input": str(Path(accepted_input)),
            "preflight_error": str(exc),
            "provider_attempts_issued": 0,
        }
    accepted_input = Path(accepted_input)
    if not accepted_input.is_file():
        return {
            "schema_version": M1_SCHEMA_VERSION,
            "status": "awaiting_accepted_questions",
            "accepted_input": str(accepted_input),
            "baseline": metadata,
            "provider_attempts_issued": 0,
        }
    questions = load_accepted_questions(accepted_input)
    return {
        "schema_version": M1_SCHEMA_VERSION,
        "status": "ready_to_execute",
        "accepted_input": str(accepted_input),
        "accepted_question_count": len(questions),
        "questions": [
            {"question_id": item.question_id, "question_identity": item.question_identity}
            for item in questions
        ],
        "baseline": metadata,
        "provider_attempts_issued": 0,
    }


def _packet_for_question(
    question: AcceptedQuestion,
    baseline: M1Baseline,
    dense_model: Any,
) -> dict[str, Mapping[str, Any]]:
    """Build all M1 Packets from one Dense query vector for this question."""

    dense_manifest = _read_json_object(baseline.dense_manifest, "M1 Dense manifest")
    instruction = dense_manifest.get("instruction")
    if not isinstance(instruction, str):
        raise M1MeasureError("M1 Dense manifest lacks a valid query instruction")
    packets: dict[str, Mapping[str, Any]] = {}
    query_vector = encode_dense_query(
        baseline.model_dir,
        question.question,
        instruction=instruction,
        model=dense_model,
    )
    for mode in _MODES:
        candidates = retrieve_candidates(
            mode,
            lexical_manifest_path=baseline.lexical_manifest,
            dense_manifest_path=baseline.dense_manifest,
            query=question.question,
            query_vector=query_vector,
            instruction=instruction,
        )
        packets[mode] = assemble_evidence_packet(
            baseline.retrieval_unit_manifest,
            candidates,
            config=EvidenceAssemblyConfig(),
            retrieval_audit={
                "query_id": question.question_id,
                "query_text": question.question,
                "mode": mode,
            },
        )
    return packets


def write_question_packets(output_root: Path, packets: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Persist exactly one Packet per M1 retrieval mode, conflict-safely."""

    if set(packets) != set(_MODES):
        raise M1MeasureError("M1 requires exactly lexical, dense, and hybrid Packets")
    return {mode: write_evidence_packet(Path(output_root) / "packets" / mode, packets[mode]) for mode in _MODES}


def _review_row(question: AcceptedQuestion, packet_artifacts: Mapping[str, Any], generation_artifact: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "question_id": question.question_id,
        "question_identity": question.question_identity,
        "packet_artifacts": dict(packet_artifacts),
        "generation_artifact": dict(generation_artifact),
        "evidence_by_mode": {mode: None for mode in _MODES},
        "answer": None,
        "primary_attribution": None,
        "secondary_attribution": None,
        "reason": None,
        "reference_consulted": False,
    }


def _write_review_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    body = b"".join(canonical_json_bytes(dict(row)) + b"\n" for row in rows)
    if path.exists() and path.read_bytes() != body:
        raise FileExistsError(f"M1 review artifact already exists with different bytes: {path}")
    if not path.exists():
        atomic_write(path, body)
    return {"path": path.name, "sha256": sha256(body).hexdigest(), "byte_count": len(body)}


def _attempt_count(result: Any) -> int:
    audit = result.provider_audit if hasattr(result, "provider_audit") else {}
    attempts = audit.get("attempts") if isinstance(audit, Mapping) else None
    if not isinstance(attempts, list) or len(attempts) != 1:
        raise M1MeasureError("M1 Generation occurrence must contain exactly one provider attempt")
    return 1


def run_m1_measure(
    accepted_input: Path,
    output_root: Path,
    *,
    baseline: M1Baseline = M1Baseline(),
    environment: Mapping[str, str] | None = None,
    transport_factory: Callable[[BailianControlConfig, Mapping[str, str] | None], BailianTransport] | None = None,
) -> dict[str, Any]:
    """Run M1 after strict preflight; each accepted question gets one hybrid occurrence."""

    preflight = preflight_m1(accepted_input, baseline=baseline)
    if preflight["status"] != "ready_to_execute":
        raise M1MeasureError("M1 cannot execute without exactly six accepted questions")
    output_root = Path(output_root)
    if output_root.exists():
        raise FileExistsError("M1 output root already exists; refusing to rerun or overwrite occurrences")
    questions = load_accepted_questions(accepted_input)
    values = os.environ if environment is None else environment
    endpoint = values.get("BAILIAN_BASE_URL")
    if not isinstance(endpoint, str) or not endpoint:
        raise GenerationConfigurationError("BAILIAN_BASE_URL must be set before M1 execution")
    config = BailianControlConfig(
        region="cn-beijing",
        endpoint=endpoint,
        workspace=workspace_from_bailian_base_url(endpoint),
        model_id=BASELINE_QWEN_MODEL_ID,
        enable_thinking=False,
        max_attempts=1,
    )
    if transport_factory is None:
        transport = BailianOpenAICompatibleTransport.from_environment(config, environment=values)
    else:
        transport = transport_factory(config, values)
    provider = BailianGenerationProvider(config, transport)

    # Recheck the pinned artifact immediately before constructing the batch encoder.
    _baseline_metadata(baseline)
    with _dense_runtime_path(baseline.runtime_root):
        dense_model = load_dense_query_model(baseline.model_dir)
        output_root.mkdir(parents=True)
        review_rows: list[dict[str, Any]] = []
        question_rows: list[dict[str, Any]] = []
        attempts_issued = 0
        for question in questions:
            if attempts_issued >= M1_PROVIDER_ATTEMPT_BUDGET:
                raise M1MeasureError("M1 provider-attempt budget exhausted before the next question")
            packets = _packet_for_question(question, baseline, dense_model)
            packet_artifacts = write_question_packets(output_root / question.question_id, packets)
            request = project_generation_request(
                packets["hybrid"], question=question.question, question_id=question.question_id
            )
            result = provider.generate(request)
            attempts_issued += _attempt_count(result)
            if attempts_issued > M1_PROVIDER_ATTEMPT_BUDGET:
                raise M1MeasureError("M1 provider-attempt budget exceeded")
            result_artifact = write_generation_result(
                output_root / question.question_id / "generation", result
            )
            review_rows.append(_review_row(question, packet_artifacts, result_artifact))
            question_rows.append({
                "question_id": question.question_id,
                "question_identity": question.question_identity,
                "packet_artifacts": packet_artifacts,
                "semantic_request_identity": result.semantic_request_identity,
                "execution_config_identity": result.execution_config_identity,
                "generation_artifact": result_artifact,
                "execution_status": result.execution_status,
            })

    review_artifact = _write_review_rows(output_root / "review.jsonl", review_rows)
    manifest = {
        "schema_version": M1_SCHEMA_VERSION,
        "status": "complete",
        "accepted_input": str(accepted_input),
        "accepted_question_count": len(questions),
        "provider_attempt_budget": M1_PROVIDER_ATTEMPT_BUDGET,
        "provider_attempts_issued": attempts_issued,
        "baseline": preflight["baseline"],
        "questions": question_rows,
        "review_artifact": review_artifact,
    }
    body = canonical_json_bytes(manifest)
    atomic_write(output_root / "manifest.json", body)
    return manifest
