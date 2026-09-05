"""Checkpointed M1/M2 Measure harness for the first P04 RAG baseline.

Only a runtime JSONL containing question identity and text is execution input.
M2 source references are parsed into a separate review-only sidecar and are
never accepted by the retrieval or Generation execution boundary.
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
from time import perf_counter
from typing import Any

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.retrieval.candidate_retrieval import (
    BatchCandidateRetriever,
    DEFAULT_DENSE_MODEL_REVISION,
    encode_dense_query,
    load_batch_candidate_retriever,
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
M2_SCHEMA_VERSION = "p04-rag-m2-0.1"
M2_QUESTION_COUNT = 70
M2_PROVIDER_ATTEMPT_BUDGET = 70
DEFAULT_M2_MAX_OUTPUT_TOKENS = 2048
_QUESTION_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_MODES = ("lexical", "dense", "hybrid")
_M2_SECTION_HEADING = re.compile(r"^## (?P<question_id>Q\d{3})\s*$", re.MULTILINE)
_M2_REVIEWED_SECTION = re.compile(
    r"^\*\*题目：\*\* (?P<question>.+?)\n\n"
    r"\*\*参考答案：\*\* (?P<reference_answer>.+?)\n\n"
    r"\*\*人工审核：\*\* (?P<human_review>.+?)$",
    re.DOTALL,
)

DEFAULT_M1_ACCEPTED_INPUT = Path(".local/p04-rag-m1/questions.accepted.jsonl")
DEFAULT_M1_BASELINE_ROOT = Path(
    "data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02"
)
DEFAULT_M1_MODEL_DIR = Path(".local/w6-models/bge-small-zh-v1.5-7999e1d")
DEFAULT_M1_RUNTIME_ROOT = Path(".local/w6-runtime")
DEFAULT_M2_RUNTIME_INPUT = Path(".local/p04-rag-m2/questions.runtime.jsonl")
DEFAULT_M2_REVIEW_ONLY_INPUT = Path(".local/p04-rag-m2/questions.review-only.jsonl")
DEFAULT_M2_SOURCE_MANIFEST = Path(".local/p04-rag-m2/source-manifest.json")


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
class M2ReviewedQuestion:
    """One reviewed source record, separated from the runtime question shape."""

    question: AcceptedQuestion
    reference_answer: str
    human_review: str

    def review_only_dict(self) -> dict[str, str]:
        return {
            "question_id": self.question.question_id,
            "question": self.question.question,
            "reference_answer": self.reference_answer,
            "human_review": self.human_review,
        }


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


def _load_runtime_questions(
    path: Path,
    *,
    required_count: int,
    measure_label: str,
) -> list[AcceptedQuestion]:
    """Load a strict runtime-only question file for one Measure work unit."""

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
        raise M1MeasureError(f"{measure_label} requires exactly {required_count} runtime questions")
    return questions


def load_accepted_questions(path: Path, *, required_count: int = M1_QUESTION_COUNT) -> list[AcceptedQuestion]:
    """Load the closed M1 runtime input without accepting source references."""

    return _load_runtime_questions(path, required_count=required_count, measure_label="M1")


def load_m2_runtime_questions(path: Path) -> list[AcceptedQuestion]:
    """Load exactly Q001-Q070 from an already-separated M2 runtime input."""

    questions = _load_runtime_questions(path, required_count=M2_QUESTION_COUNT, measure_label="M2")
    expected_ids = [f"Q{index:03d}" for index in range(1, M2_QUESTION_COUNT + 1)]
    actual_ids = [item.question_id for item in questions]
    if actual_ids != expected_ids:
        raise M1MeasureError("M2 runtime input must contain exactly Q001 through Q070 in order")
    return questions


def parse_m2_reviewed_source(path: Path) -> list[M2ReviewedQuestion]:
    """Parse the reviewed Markdown only when it establishes one Q001-Q070 set."""

    try:
        source = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise M1MeasureError("M2 reviewed source is unreadable") from exc
    headings = list(_M2_SECTION_HEADING.finditer(source))
    expected_ids = [f"Q{index:03d}" for index in range(1, M2_QUESTION_COUNT + 1)]
    actual_ids = [match.group("question_id") for match in headings]
    if actual_ids != expected_ids:
        raise M1MeasureError("M2 reviewed source must contain exactly one Q001 through Q070 set in order")

    reviewed: list[M2ReviewedQuestion] = []
    for index, heading in enumerate(headings):
        section_end = headings[index + 1].start() if index + 1 < len(headings) else len(source)
        section = source[heading.end():section_end].strip()
        match = _M2_REVIEWED_SECTION.fullmatch(section)
        if match is None:
            raise M1MeasureError(f"M2 reviewed source {heading.group('question_id')} has an unsupported section shape")
        question = match.group("question")
        reference_answer = match.group("reference_answer")
        human_review = match.group("human_review")
        if not question.strip() or not reference_answer.strip() or not human_review.strip():
            raise M1MeasureError(f"M2 reviewed source {heading.group('question_id')} has an empty required field")
        reviewed.append(M2ReviewedQuestion(
            question=AcceptedQuestion(question_id=heading.group("question_id"), question=question),
            reference_answer=reference_answer,
            human_review=human_review,
        ))
    return reviewed


def _artifact_metadata(path: Path, body: bytes) -> dict[str, Any]:
    return {"path": str(path), "sha256": sha256(body).hexdigest(), "byte_count": len(body)}


def _write_planned_artifacts(artifacts: Mapping[Path, bytes]) -> dict[Path, dict[str, Any]]:
    """Write a fixed extraction set only after every conflict check passes."""

    for path, body in artifacts.items():
        if path.exists() and path.read_bytes() != body:
            raise FileExistsError(f"M2 extracted artifact already exists with different bytes: {path}")
    for path, body in artifacts.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            atomic_write(path, body)
    return {path: _artifact_metadata(path, body) for path, body in artifacts.items()}


def prepare_m2_question_inputs(
    source_path: Path,
    *,
    runtime_input: Path = DEFAULT_M2_RUNTIME_INPUT,
    review_only_input: Path = DEFAULT_M2_REVIEW_ONLY_INPUT,
    source_manifest: Path = DEFAULT_M2_SOURCE_MANIFEST,
) -> dict[str, Any]:
    """Extract reviewed Markdown into disjoint runtime and review-only inputs."""

    source_path = Path(source_path)
    reviewed = parse_m2_reviewed_source(source_path)
    try:
        source_bytes = source_path.read_bytes()
    except OSError as exc:
        raise M1MeasureError("M2 reviewed source is unreadable") from exc
    runtime_body = b"".join(canonical_json_bytes(item.question.to_dict()) + b"\n" for item in reviewed)
    review_body = b"".join(canonical_json_bytes(item.review_only_dict()) + b"\n" for item in reviewed)
    source_sha256 = sha256(source_bytes).hexdigest()
    manifest = {
        "schema_version": M2_SCHEMA_VERSION,
        "status": "prepared",
        "source_sha256": source_sha256,
        "question_count": len(reviewed),
        "runtime_input": _artifact_metadata(Path(runtime_input), runtime_body),
        "review_only_input": _artifact_metadata(Path(review_only_input), review_body),
    }
    manifest_body = canonical_json_bytes(manifest)
    written = _write_planned_artifacts({
        Path(runtime_input): runtime_body,
        Path(review_only_input): review_body,
        Path(source_manifest): manifest_body,
    })
    return {
        **manifest,
        "runtime_input": written[Path(runtime_input)],
        "review_only_input": written[Path(review_only_input)],
        "source_manifest": written[Path(source_manifest)],
    }


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


def _preflight_measure(
    runtime_input: Path,
    *,
    baseline: M1Baseline,
    schema_version: str,
    measure_label: str,
    question_loader: Callable[[Path], list[AcceptedQuestion]],
) -> dict[str, Any]:
    """Report shared Retrieval/Dense readiness without any output or provider call."""

    try:
        metadata = _baseline_metadata(baseline)
        dense_manifest = _read_json_object(baseline.dense_manifest, "M1 Dense manifest")
        metadata["dense_runtime"] = _probe_dense_query_runtime(baseline, dense_manifest)
    except M1MeasureError as exc:
        return {
            "schema_version": schema_version,
            "status": "not_ready",
            "runtime_input": str(Path(runtime_input)),
            "preflight_error": str(exc),
            "provider_attempts_issued": 0,
        }
    runtime_input = Path(runtime_input)
    if not runtime_input.is_file():
        return {
            "schema_version": schema_version,
            "status": "awaiting_runtime_questions",
            "runtime_input": str(runtime_input),
            "baseline": metadata,
            "provider_attempts_issued": 0,
        }
    questions = question_loader(runtime_input)
    return {
        "schema_version": schema_version,
        "status": "ready_to_execute",
        "runtime_input": str(runtime_input),
        "runtime_question_count": len(questions),
        "questions": [
            {"question_id": item.question_id, "question_identity": item.question_identity}
            for item in questions
        ],
        "baseline": metadata,
        "provider_attempts_issued": 0,
    }


def preflight_m1(
    accepted_input: Path = DEFAULT_M1_ACCEPTED_INPUT,
    *,
    baseline: M1Baseline = M1Baseline(),
) -> dict[str, Any]:
    """Report closed-M1 readiness without writing artifacts or invoking a provider."""

    result = _preflight_measure(
        accepted_input,
        baseline=baseline,
        schema_version=M1_SCHEMA_VERSION,
        measure_label="M1",
        question_loader=lambda path: load_accepted_questions(path),
    )
    # Preserve M1's historical public preflight keys for existing callers.
    if "runtime_input" in result:
        result["accepted_input"] = result.pop("runtime_input")
    if "runtime_question_count" in result:
        result["accepted_question_count"] = result.pop("runtime_question_count")
    if result["status"] == "awaiting_runtime_questions":
        result["status"] = "awaiting_accepted_questions"
    return result


def preflight_m2(
    runtime_input: Path = DEFAULT_M2_RUNTIME_INPUT,
    *,
    baseline: M1Baseline = M1Baseline(),
) -> dict[str, Any]:
    """Preflight M2's runtime-only input before any output or provider call."""

    return _preflight_measure(
        runtime_input,
        baseline=baseline,
        schema_version=M2_SCHEMA_VERSION,
        measure_label="M2",
        question_loader=load_m2_runtime_questions,
    )


def _candidates_for_question(
    question: AcceptedQuestion,
    baseline: M1Baseline,
    dense_model: Any,
    *,
    batch_retriever: BatchCandidateRetriever | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Encode once, then retrieve all three candidate modes for one question."""

    dense_manifest = (
        batch_retriever.dense_manifest
        if batch_retriever is not None
        else _read_json_object(baseline.dense_manifest, "M1 Dense manifest")
    )
    instruction = dense_manifest.get("instruction")
    if not isinstance(instruction, str):
        raise M1MeasureError("M1 Dense manifest lacks a valid query instruction")
    query_vector = encode_dense_query(
        baseline.model_dir,
        question.question,
        instruction=instruction,
        model=dense_model,
    )
    if batch_retriever is not None:
        return batch_retriever.candidates_for_query(
            question.question,
            query_vector,
            instruction=instruction,
        )
    candidates_by_mode: dict[str, list[dict[str, Any]]] = {}
    for mode in _MODES:
        candidates_by_mode[mode] = retrieve_candidates(
            mode,
            lexical_manifest_path=baseline.lexical_manifest,
            dense_manifest_path=baseline.dense_manifest,
            query=question.question,
            query_vector=query_vector,
            instruction=instruction,
        )
    return candidates_by_mode


def _assemble_question_packets(
    question: AcceptedQuestion,
    baseline: M1Baseline,
    candidates_by_mode: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Mapping[str, Any]]:
    """Build the same three Evidence Packets from already-ranked candidates."""

    if set(candidates_by_mode) != set(_MODES):
        raise M1MeasureError("Measure requires lexical, dense, and hybrid candidates")
    packets: dict[str, Mapping[str, Any]] = {}
    for mode in _MODES:
        packets[mode] = assemble_evidence_packet(
            baseline.retrieval_unit_manifest,
            candidates_by_mode[mode],
            config=EvidenceAssemblyConfig(),
            retrieval_audit={
                "query_id": question.question_id,
                "query_text": question.question,
                "mode": mode,
            },
        )
    return packets


def _packet_for_question(
    question: AcceptedQuestion,
    baseline: M1Baseline,
    dense_model: Any,
    *,
    batch_retriever: BatchCandidateRetriever | None = None,
) -> dict[str, Mapping[str, Any]]:
    """Build all Packets from one Dense query vector for this question."""

    return _assemble_question_packets(
        question,
        baseline,
        _candidates_for_question(question, baseline, dense_model, batch_retriever=batch_retriever),
    )


def write_question_packets(output_root: Path, packets: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Persist exactly one Packet per M1 retrieval mode, conflict-safely."""

    if set(packets) != set(_MODES):
        raise M1MeasureError("M1 requires exactly lexical, dense, and hybrid Packets")
    return {mode: write_evidence_packet(Path(output_root) / "packets" / mode, packets[mode]) for mode in _MODES}


def _review_row(
    question: AcceptedQuestion,
    packet_artifacts: Mapping[str, Any],
    generation_artifact: Mapping[str, Any],
    *,
    include_question_text: bool = False,
) -> dict[str, Any]:
    row: dict[str, Any] = {
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
    if include_question_text:
        row["question"] = question.question
    return row


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


def _run_measure(
    runtime_input: Path,
    output_root: Path,
    *,
    baseline: M1Baseline,
    schema_version: str,
    measure_label: str,
    question_loader: Callable[[Path], list[AcceptedQuestion]],
    preflight_runner: Callable[[Path], dict[str, Any]],
    provider_attempt_budget: int,
    input_field: str,
    count_field: str,
    max_output_tokens: int,
    include_question_text_in_review: bool,
    review_filename: str,
    use_batch_retrieval: bool,
    capture_execution_timing: bool,
    environment: Mapping[str, str] | None,
    transport_factory: Callable[[BailianControlConfig, Mapping[str, str] | None], BailianTransport] | None,
) -> dict[str, Any]:
    """Run the shared lexical/Dense/Hybrid Packet and hybrid Generation path."""

    preflight = preflight_runner(runtime_input)
    if preflight["status"] != "ready_to_execute":
        raise M1MeasureError(f"{measure_label} cannot execute without its strict runtime input")
    output_root = Path(output_root)
    if output_root.exists():
        raise FileExistsError(f"{measure_label} output root already exists; refusing to rerun or overwrite occurrences")
    questions = question_loader(runtime_input)
    values = os.environ if environment is None else environment
    endpoint = values.get("BAILIAN_BASE_URL")
    if not isinstance(endpoint, str) or not endpoint:
        raise GenerationConfigurationError(f"BAILIAN_BASE_URL must be set before {measure_label} execution")
    config = BailianControlConfig(
        region="cn-beijing",
        endpoint=endpoint,
        workspace=workspace_from_bailian_base_url(endpoint),
        model_id=BASELINE_QWEN_MODEL_ID,
        enable_thinking=False,
        max_output_tokens=max_output_tokens,
        max_attempts=1,
    )
    if transport_factory is None:
        transport = BailianOpenAICompatibleTransport.from_environment(config, environment=values)
    else:
        transport = transport_factory(config, values)
    provider = BailianGenerationProvider(config, transport)

    execution_started = perf_counter() if capture_execution_timing else None
    execution_timing = {
        "batch_preparation_seconds": 0.0,
        "retrieval_seconds": 0.0,
        "evidence_assembly_seconds": 0.0,
        "provider_generation_seconds": 0.0,
    }
    # Recheck the pinned artifact immediately before constructing the batch encoder.
    preparation_started = perf_counter() if capture_execution_timing else None
    _baseline_metadata(baseline)
    with _dense_runtime_path(baseline.runtime_root):
        dense_model = load_dense_query_model(baseline.model_dir)
        batch_retriever = (
            load_batch_candidate_retriever(baseline.lexical_manifest, baseline.dense_manifest)
            if use_batch_retrieval
            else None
        )
        if capture_execution_timing and preparation_started is not None:
            execution_timing["batch_preparation_seconds"] = perf_counter() - preparation_started
        output_root.mkdir(parents=True)
        review_rows: list[dict[str, Any]] = []
        question_rows: list[dict[str, Any]] = []
        attempts_issued = 0
        for question in questions:
            if attempts_issued >= provider_attempt_budget:
                raise M1MeasureError(f"{measure_label} provider-attempt budget exhausted before the next question")
            if use_batch_retrieval:
                retrieval_started = perf_counter()
                candidates_by_mode = _candidates_for_question(
                    question,
                    baseline,
                    dense_model,
                    batch_retriever=batch_retriever,
                )
                if capture_execution_timing:
                    execution_timing["retrieval_seconds"] += perf_counter() - retrieval_started
                assembly_started = perf_counter()
                packets = _assemble_question_packets(question, baseline, candidates_by_mode)
                if capture_execution_timing:
                    execution_timing["evidence_assembly_seconds"] += perf_counter() - assembly_started
            else:
                packets = _packet_for_question(question, baseline, dense_model)
            packet_artifacts = write_question_packets(output_root / question.question_id, packets)
            request = project_generation_request(
                packets["hybrid"], question=question.question, question_id=question.question_id
            )
            generation_started = perf_counter() if capture_execution_timing else None
            result = provider.generate(request)
            if capture_execution_timing and generation_started is not None:
                execution_timing["provider_generation_seconds"] += perf_counter() - generation_started
            attempts_issued += _attempt_count(result)
            if attempts_issued > provider_attempt_budget:
                raise M1MeasureError(f"{measure_label} provider-attempt budget exceeded")
            result_artifact = write_generation_result(
                output_root / question.question_id / "generation", result
            )
            review_rows.append(_review_row(
                question,
                packet_artifacts,
                result_artifact,
                include_question_text=include_question_text_in_review,
            ))
            question_rows.append({
                "question_id": question.question_id,
                "question_identity": question.question_identity,
                "packet_artifacts": packet_artifacts,
                "semantic_request_identity": result.semantic_request_identity,
                "execution_config_identity": result.execution_config_identity,
                "generation_artifact": result_artifact,
                "execution_status": result.execution_status,
            })

    review_artifact = _write_review_rows(output_root / review_filename, review_rows)
    manifest: dict[str, Any] = {
        "schema_version": schema_version,
        "status": "complete",
        input_field: str(runtime_input),
        count_field: len(questions),
        "provider_attempt_budget": provider_attempt_budget,
        "provider_attempts_issued": attempts_issued,
        "baseline": preflight["baseline"],
        "questions": question_rows,
        "review_artifact": review_artifact,
    }
    if measure_label == "M2":
        manifest["generation_configuration"] = config.output_affecting_projection()
        manifest["review_index_artifact"] = review_artifact
    if capture_execution_timing and execution_started is not None:
        manifest["execution_timing"] = {
            "schema_version": "p04-rag-m2-execution-timing-0.1",
            **execution_timing,
            "total_execution_seconds": perf_counter() - execution_started,
        }
    body = canonical_json_bytes(manifest)
    atomic_write(output_root / "manifest.json", body)
    return manifest


def run_m1_measure(
    accepted_input: Path,
    output_root: Path,
    *,
    baseline: M1Baseline = M1Baseline(),
    environment: Mapping[str, str] | None = None,
    transport_factory: Callable[[BailianControlConfig, Mapping[str, str] | None], BailianTransport] | None = None,
) -> dict[str, Any]:
    """Run closed M1 through the shared Measure execution path."""

    return _run_measure(
        accepted_input,
        output_root,
        baseline=baseline,
        schema_version=M1_SCHEMA_VERSION,
        measure_label="M1",
        question_loader=lambda path: load_accepted_questions(path),
        preflight_runner=lambda path: preflight_m1(path, baseline=baseline),
        provider_attempt_budget=M1_PROVIDER_ATTEMPT_BUDGET,
        input_field="accepted_input",
        count_field="accepted_question_count",
        max_output_tokens=1024,
        include_question_text_in_review=False,
        review_filename="review.jsonl",
        use_batch_retrieval=False,
        capture_execution_timing=False,
        environment=environment,
        transport_factory=transport_factory,
    )


def run_m2_measure(
    runtime_input: Path,
    output_root: Path,
    *,
    baseline: M1Baseline = M1Baseline(),
    max_output_tokens: int = DEFAULT_M2_MAX_OUTPUT_TOKENS,
    environment: Mapping[str, str] | None = None,
    transport_factory: Callable[[BailianControlConfig, Mapping[str, str] | None], BailianTransport] | None = None,
) -> dict[str, Any]:
    """Run M2's 70 reviewed runtime questions through the M1 execution path."""

    return _run_measure(
        runtime_input,
        output_root,
        baseline=baseline,
        schema_version=M2_SCHEMA_VERSION,
        measure_label="M2",
        question_loader=load_m2_runtime_questions,
        preflight_runner=lambda path: preflight_m2(path, baseline=baseline),
        provider_attempt_budget=M2_PROVIDER_ATTEMPT_BUDGET,
        input_field="runtime_input",
        count_field="runtime_question_count",
        max_output_tokens=max_output_tokens,
        include_question_text_in_review=True,
        review_filename="review_index.jsonl",
        use_batch_retrieval=True,
        capture_execution_timing=True,
        environment=environment,
        transport_factory=transport_factory,
    )
