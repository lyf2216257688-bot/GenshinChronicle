"""BGE v2-m3 local reranker full70 downstream answer gate.

This runner consumes accepted current-C Hybrid500 results as immutable retrieval
input.  It changes only the local reranker arm; Assembly, Generation,
citation-validation, persistence, and recovery semantics remain the hardened
GTE full70 contract.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.generation.generation import (
    BailianControlConfig,
    BailianGenerationProvider,
    BailianOpenAICompatibleTransport,
    workspace_from_bailian_base_url,
)
from genshin_corpus.generation.measure import load_m2_runtime_questions
from genshin_corpus.rag.backend import PreparedRagState, SingleQuestionBackendConfig, run_single_question
from genshin_corpus.retrieval.bge_reranker_v2_m3 import (
    MAX_LENGTH,
    MODEL_ID,
    MODEL_REVISION,
    MODEL_WEIGHT_SHA256,
    PROJECTION_MAX_CHARS,
    BgeRerankerV2M3,
    BgeRerankerV2M3Config,
    verify_pinned_local_model,
)
from genshin_corpus.retrieval.evidence_assembly import EvidenceAssemblyConfig, prepare_evidence_assembly_context
from genshin_corpus.retrieval.qwen_m2_query_vectors import load_accepted_qwen_query_vectors
from genshin_corpus.retrieval.reranking import RankFusionConfig


SCHEMA_VERSION = "phase04-bge-reranker-v2-m3-local70-answer-gate-v1"
RECOVERY_CONTRACT_VERSION = "phase04-bge-reranker-v2-m3-local70-recovery-v2"
LEDGER_SCHEMA_VERSION = "phase04-bge-reranker-v2-m3-local70-provider-ledger-v2"
GENERATION_STAGE = "generation"
GENERATION_ARM = "bge_reranker_v2_m3"
DEFAULT_ROOT = Path(".local/p04-bge-reranker-v2-m3-local70-answer-gate-20260917-r1")
CURRENT_C_ROOT = Path(".local/p04-block-a-paid-c-only-20260915-6583536f")
CURRENT_C_MANIFEST_SHA256 = "a9e67f58570e1f359c15b1e2f08b7d83961061ab23f8e3bf353066609c638359"
QUERY_ROOT = Path(".local/p04-qwen-m2-query-vectors-beijing-20260910-162427")
RUNTIME_INPUT = Path(".local/p04-rag-m2/questions.runtime.jsonl")
RU_MANIFEST = Path("data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/ru/metadata/manifest.json")
MAX_GENERATION_CALLS = 70
QUESTION_IDS = tuple(f"Q{i:03d}" for i in range(1, 71))
FUSION = RankFusionConfig(hybrid_weight=0.35, rerank_weight=0.65, denominator=60)


class BgeFull70GateError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _descriptor(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve()), "byte_count": path.stat().st_size, "sha256": _sha256(path)}


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BgeFull70GateError(f"unreadable JSON: {path}") from exc


def _write(path: Path, value: Any) -> dict[str, Any]:
    body = canonical_json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, body)
    return {
        "path": str(path.relative_to(path.parents[2])) if len(path.parents) > 2 else str(path),
        "byte_count": len(body),
        "sha256": sha256(body).hexdigest(),
    }


def _expected_model_identity(model_path: Path) -> dict[str, Any]:
    model_path = Path(model_path).resolve()
    return {
        "model": MODEL_ID,
        "revision": MODEL_REVISION,
        "model_path": str(model_path),
        "model_weight_sha256": MODEL_WEIGHT_SHA256,
        "dtype": "float16",
        "device": "cuda",
        "batch_size": 1,
        "max_length": MAX_LENGTH,
        "projection_max_chars": PROJECTION_MAX_CHARS,
    }


def _verify_bge_model_identity(model_path: Path) -> dict[str, Any]:
    model_path = Path(model_path)
    try:
        materialization = verify_pinned_local_model(model_path)
    except Exception as exc:
        raise BgeFull70GateError("pinned BGE model identity verification failed") from exc
    expected = _expected_model_identity(model_path)
    if (
        materialization.get("model") != expected["model"]
        or materialization.get("revision") != expected["revision"]
        or materialization.get("model_path") != expected["model_path"]
        or materialization.get("model_weight", {}).get("sha256") != expected["model_weight_sha256"]
    ):
        raise BgeFull70GateError("pinned BGE model identity mismatch")
    return materialization


class _PersistedRetriever:
    """Retriever seam backed solely by accepted persisted current-C windows."""

    def __init__(self, windows: Mapping[str, Mapping[str, Any]]) -> None:
        self.windows = windows
        self.calls = 0

    def candidates_for_query(self, question: str, vector: Any, **_: Any) -> dict[str, Any]:
        self.calls += 1
        qid = next((key for key, value in self.windows.items() if value.get("question") == question), None)
        if qid is None:
            raise BgeFull70GateError("persisted Hybrid500 question binding is missing")
        return json.loads(json.dumps(self.windows[qid]["windows"], ensure_ascii=False))


class _LedgerGenerationProvider:
    def __init__(
        self,
        delegate: Any,
        ledger: Path,
        question_id: str,
        counters: dict[str, int],
        *,
        run_identity: str,
    ) -> None:
        self.delegate, self.ledger, self.question_id, self.counters = delegate, ledger, question_id, counters
        self.run_identity = run_identity
        self.terminal_event: dict[str, Any] | None = None
        self._issued = False

    def generate(self, request: Any) -> Any:
        if self.question_id not in QUESTION_IDS:
            raise BgeFull70GateError("unknown Generation question ID")
        if self._issued:
            raise BgeFull70GateError("duplicate Generation invocation")
        if self.counters["generation"] >= MAX_GENERATION_CALLS:
            raise BgeFull70GateError("Generation hard budget exceeded")
        occurrence_id = f"{self.question_id}-{GENERATION_STAGE}-{uuid4().hex}"
        _append(
            self.ledger,
            _ledger_event(
                run_identity=self.run_identity,
                question_id=self.question_id,
                occurrence_id=occurrence_id,
                status="issued",
            ),
        )
        self._issued = True
        self.counters["generation"] += 1
        try:
            result = self.delegate.generate(request)
            audit = getattr(result, "provider_audit", {})
            attempts = audit.get("attempts", []) if isinstance(audit, Mapping) else []
            if not isinstance(attempts, list) or len(attempts) != 1:
                raise BgeFull70GateError("Generation provider violated max_attempts=1")
            status = "succeeded" if getattr(result, "execution_status", None) == "succeeded" else "failed"
            self.terminal_event = _ledger_event(
                run_identity=self.run_identity,
                question_id=self.question_id,
                occurrence_id=occurrence_id,
                status=status,
                execution_status=getattr(result, "execution_status", None),
                observed_provider_attempt_count=1,
            )
            _append(self.ledger, self.terminal_event)
            return result
        except Exception as exc:
            if self.terminal_event is None:
                self.terminal_event = _ledger_event(
                    run_identity=self.run_identity,
                    question_id=self.question_id,
                    occurrence_id=occurrence_id,
                    status="failed",
                    execution_status="delegate_exception",
                    error_type=type(exc).__name__,
                )
                _append(self.ledger, self.terminal_event)
            raise


def _append(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(canonical_json_bytes(dict(value)) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def _ledger_event(*, run_identity: str, question_id: str, occurrence_id: str, status: str, **extra: Any) -> dict[str, Any]:
    return {
        "event": "generation_occurrence",
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "recovery_contract_version": RECOVERY_CONTRACT_VERSION,
        "run_identity": run_identity,
        "question_id": question_id,
        "stage": GENERATION_STAGE,
        "arm": GENERATION_ARM,
        "occurrence_id": occurrence_id,
        "status": status,
        "attempt_count": 1,
        **extra,
    }


def _load_windows(root: Path) -> dict[str, dict[str, Any]]:
    manifest = root / "metadata/manifest.json"
    if _sha256(manifest) != CURRENT_C_MANIFEST_SHA256:
        raise BgeFull70GateError("accepted current-C manifest hash mismatch")
    rows = {}
    for qid in QUESTION_IDS:
        result_path = root / f"results/{qid}/vnext/{qid}/rag_result.json"
        result = _read(result_path)
        trace = result.get("retrieval_trace")
        if result.get("status") != "succeeded" or not isinstance(trace, Mapping) or len(trace.get("windows", {}).get("hybrid", [])) != 500:
            raise BgeFull70GateError(f"current-C Hybrid500 is incomplete: {qid}")
        hybrid = trace["windows"]["hybrid"]
        if [int(row.get("rank")) for row in hybrid] != list(range(1, 501)):
            raise BgeFull70GateError(f"current-C Hybrid500 rank corruption: {qid}")
        rows[qid] = {
            "question": result["query"]["question_text"],
            "windows": trace["windows"],
            "source": _descriptor(result_path),
            "source_result": result,
        }
    return rows


def _ledger_state(path: Path, *, run_identity: str) -> dict[str, Any]:
    """Strictly validate durable Generation occurrence ownership and terminality."""
    if not path.exists():
        raise BgeFull70GateError("missing Generation ledger")
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except Exception as exc:
        raise BgeFull70GateError("unreadable Generation ledger") from exc
    started = [row for row in rows if row.get("event") == "run_started"]
    if len(started) != 1:
        raise BgeFull70GateError("ambiguous Generation ledger run start")
    start = started[0]
    if (
        start.get("ledger_schema_version") != LEDGER_SCHEMA_VERSION
        or start.get("recovery_contract_version") != RECOVERY_CONTRACT_VERSION
        or start.get("run_identity") != run_identity
        or start.get("question_id") is not None
        or start.get("stage") != GENERATION_STAGE
        or start.get("arm") != GENERATION_ARM
        or start.get("occurrence_id") is not None
        or start.get("attempt_count") != 1
        or start.get("expected_question_ids") != list(QUESTION_IDS)
        or start.get("automatic_retry") != 0
    ):
        raise BgeFull70GateError("Generation ledger run identity mismatch")
    by_occurrence: dict[str, list[dict[str, Any]]] = {}
    issued_order: list[str] = []
    for row in rows:
        if row.get("event") == "run_started":
            continue
        if row.get("event") != "generation_occurrence":
            raise BgeFull70GateError("unknown Generation ledger event")
        occurrence_id, question_id = row.get("occurrence_id"), row.get("question_id")
        if not isinstance(occurrence_id, str) or not occurrence_id:
            raise BgeFull70GateError("missing Generation occurrence ID")
        if question_id not in QUESTION_IDS:
            raise BgeFull70GateError("unknown Generation question ID")
        if (
            row.get("ledger_schema_version") != LEDGER_SCHEMA_VERSION
            or row.get("recovery_contract_version") != RECOVERY_CONTRACT_VERSION
            or row.get("run_identity") != run_identity
            or row.get("stage") != GENERATION_STAGE
            or row.get("arm") != GENERATION_ARM
            or row.get("attempt_count") != 1
            or row.get("status") not in {"issued", "succeeded", "failed"}
        ):
            raise BgeFull70GateError("Generation ledger row identity mismatch")
        if row["status"] == "issued":
            issued_order.append(question_id)
        by_occurrence.setdefault(occurrence_id, []).append(row)
    terminal_by_question: dict[str, dict[str, Any]] = {}
    for occurrence_id, occurrence_rows in by_occurrence.items():
        issued_rows = [row for row in occurrence_rows if row["status"] == "issued"]
        terminal_rows = [row for row in occurrence_rows if row["status"] in {"succeeded", "failed"}]
        if len(issued_rows) != 1:
            raise BgeFull70GateError(f"duplicate or missing issued Generation row: {occurrence_id}")
        if len(terminal_rows) != 1:
            raise BgeFull70GateError(f"duplicate or missing terminal Generation row: {occurrence_id}")
        if len(occurrence_rows) != 2:
            raise BgeFull70GateError(f"duplicate Generation occurrence ID: {occurrence_id}")
        if occurrence_rows[0]["status"] != "issued" or occurrence_rows[1]["status"] not in {"succeeded", "failed"}:
            raise BgeFull70GateError(f"Generation ledger occurrence order mismatch: {occurrence_id}")
        issued, terminal = issued_rows[0], terminal_rows[0]
        for field in ("ledger_schema_version", "recovery_contract_version", "run_identity", "question_id", "stage", "arm", "occurrence_id", "attempt_count"):
            if issued[field] != terminal[field]:
                raise BgeFull70GateError(f"Generation ledger occurrence disagreement: {occurrence_id}")
        if issued["question_id"] in terminal_by_question:
            raise BgeFull70GateError(f"duplicate Generation occurrence for question: {issued['question_id']}")
        terminal_by_question[issued["question_id"]] = terminal
    if issued_order != list(QUESTION_IDS[: len(issued_order)]):
        raise BgeFull70GateError("Generation ledger question order mismatch")
    completed = tuple(issued_order)
    return {"terminal_by_question": terminal_by_question, "completed_questions": completed, "generation_count": len(completed)}


def _terminal_outcome(completed: tuple[str, ...], terminal_by_question: Mapping[str, Mapping[str, Any]]) -> tuple[str, str, list[str], bool]:
    """Return status, terminal status, failed IDs, and execution completion."""
    failed = [question_id for question_id in completed if terminal_by_question[question_id]["status"] == "failed"]
    if completed != QUESTION_IDS:
        return "in_progress", "in_progress", failed, False
    return ("partial_failed", "partial", failed, True) if failed else ("complete", "complete", [], True)


def _validate_recovery_manifest(output_root: Path, manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("recovery_contract_version") != RECOVERY_CONTRACT_VERSION:
        raise BgeFull70GateError("resume identity mismatch")
    model = manifest.get("model")
    if not isinstance(model, Mapping) or not isinstance(model.get("model_path"), str) or not model["model_path"]:
        raise BgeFull70GateError("resume model root mismatch")
    if manifest.get("runner_sha256") != _sha256(Path(__file__)) or model != _expected_model_identity(Path(model["model_path"])):
        raise BgeFull70GateError("resume identity mismatch")
    if manifest.get("question_ids") != list(QUESTION_IDS):
        raise BgeFull70GateError("resume question membership mismatch")
    if not isinstance(manifest.get("run_identity"), str) or not manifest["run_identity"]:
        raise BgeFull70GateError("resume run identity mismatch")
    config = manifest.get("configuration")
    if (
        not isinstance(config, Mapping)
        or config.get("fusion") != FUSION.to_dict()
        or config.get("candidate_supply_depth") != 500
        or config.get("rerank_depth") != 500
        or config.get("final_top_n") != 20
        or config.get("reranker_projection_max_chars") != PROJECTION_MAX_CHARS
        or config.get("assembly") != "accepted current-C assembly_config"
        or config.get("generation", {}).get("max_attempts") != 1
    ):
        raise BgeFull70GateError("resume configuration mismatch")
    completed = manifest.get("completed_questions")
    if not isinstance(completed, list) or len(completed) != len(set(completed)) or any(question_id not in QUESTION_IDS for question_id in completed):
        raise BgeFull70GateError("manifest completed_questions mismatch")


def _reconcile_recovery(output_root: Path, manifest: Mapping[str, Any], ledger: Mapping[str, Any]) -> tuple[str, ...]:
    completed = tuple(ledger["completed_questions"])
    terminal_by_question = ledger["terminal_by_question"]
    if manifest.get("completed_questions") != list(completed):
        raise BgeFull70GateError("manifest completed_questions mismatch")
    results_root = output_root / "results"
    observed = set()
    if results_root.exists():
        for path in results_root.iterdir():
            if path.is_dir() and (path / path.name / "rag_result.json").exists():
                observed.add(path.name)
    if observed != set(completed):
        raise BgeFull70GateError("ledger/result directory mismatch")
    for question_id in completed:
        result = _read(results_root / question_id / question_id / "rag_result.json")
        evidence = _read(results_root / question_id / "answer_evidence.json")
        terminal = terminal_by_question[question_id]
        if result.get("status") != terminal["status"] or evidence.get("status") != terminal["status"]:
            raise BgeFull70GateError(f"ledger/result status mismatch: {question_id}")
        if evidence.get("question_id") != question_id or evidence.get("generation_occurrence") != terminal:
            raise BgeFull70GateError(f"persisted Generation evidence mismatch: {question_id}")
        if evidence.get("generation") != result.get("persistence", {}).get("generation"):
            raise BgeFull70GateError(f"persisted Generation descriptor mismatch: {question_id}")
    expected_counts = {"embedding": 0, "reranker": len(completed), "generation": len(completed)}
    if manifest.get("actual_provider_calls") != expected_counts or manifest.get("network_provider_occurrences") != len(completed):
        raise BgeFull70GateError("manifest provider count mismatch")
    expected_status, expected_terminal, failed, execution_complete = _terminal_outcome(completed, terminal_by_question)
    if execution_complete:
        if (
            manifest.get("status") != expected_status
            or manifest.get("terminal_status") != expected_terminal
            or manifest.get("failed_question_ids") != failed
            or manifest.get("execution_complete") is not True
        ):
            raise BgeFull70GateError("terminal manifest state mismatch")
    elif (
        manifest.get("status") != expected_status
        or manifest.get("terminal_status") != expected_terminal
        or manifest.get("execution_complete") is not False
    ):
        raise BgeFull70GateError("incomplete manifest state mismatch")
    return completed


def _build_state(windows: Mapping[str, Mapping[str, Any]]) -> PreparedRagState:
    context = prepare_evidence_assembly_context(RU_MANIFEST)
    retriever = _PersistedRetriever(windows)
    return PreparedRagState(
        RU_MANIFEST.resolve(),
        Path("persisted-current-C"),
        Path("accepted-qwen-dense"),
        retriever,
        context,
        str(context.retrieval_unit_build_identity),
        "persisted-current-C",
        "accepted-qwen-dense",
    )


def _generation_config(environment: Mapping[str, str]) -> BailianControlConfig:
    endpoint = environment.get("BAILIAN_BASE_URL")
    if not endpoint:
        raise BgeFull70GateError("BAILIAN_BASE_URL is required")
    return BailianControlConfig(
        region="cn-beijing",
        endpoint=endpoint,
        workspace=workspace_from_bailian_base_url(endpoint),
        model_id="qwen3.7-plus-2026-05-26",
        enable_thinking=False,
        max_output_tokens=2048,
        max_attempts=1,
        timeout_seconds=30.0,
    )


def run_bge_full70(
    *,
    model_path: Path,
    output_root: Path = DEFAULT_ROOT,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    output_root = Path(output_root)
    model_path = Path(model_path)
    if output_root.exists():
        manifest_path = output_root / "metadata/manifest.json"
        if not manifest_path.exists():
            raise FileExistsError("BGE output root exists with ambiguous state")
        manifest = _read(manifest_path)
        if manifest.get("status") in {"complete", "partial_failed"}:
            return manifest
        return _resume_bge_full70(output_root, model_path=model_path, environment=environment)
    windows = _load_windows(CURRENT_C_ROOT)
    questions = load_m2_runtime_questions(RUNTIME_INPUT)
    if tuple(question.question_id for question in questions) != QUESTION_IDS:
        raise BgeFull70GateError("runtime questions are not Q001-Q070")
    vectors = load_accepted_qwen_query_vectors(QUERY_ROOT)
    if tuple(vector.question_id for vector in vectors) != QUESTION_IDS:
        raise BgeFull70GateError("accepted query vectors are not Q001-Q070")
    env = dict(environment or os.environ)
    generation_config = _generation_config(env)
    _verify_bge_model_identity(model_path)
    output_root.mkdir(parents=True)
    (output_root / "metadata").mkdir()
    (output_root / "results").mkdir()
    runner_hash = _sha256(Path(__file__))
    model_identity = _expected_model_identity(model_path)
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "recovery_contract_version": RECOVERY_CONTRACT_VERSION,
        "status": "in_progress",
        "terminal_status": "in_progress",
        "execution_complete": False,
        "failed_question_ids": [],
        "run_identity": sha256_json({
            "schema": SCHEMA_VERSION,
            "recovery_contract": RECOVERY_CONTRACT_VERSION,
            "root": str(output_root.resolve()),
            "runner": runner_hash,
            "model": model_identity,
        }),
        "runner_sha256": runner_hash,
        "model": model_identity,
        "question_ids": list(QUESTION_IDS),
        "source": {
            "root": str(CURRENT_C_ROOT.resolve()),
            "manifest": _descriptor(CURRENT_C_ROOT / "metadata/manifest.json"),
            "hybrid_source": "accepted current-C retrieval_trace.windows.hybrid",
        },
        "inputs": {
            "runtime_input": _descriptor(RUNTIME_INPUT),
            "query_vectors": {
                "root": str(QUERY_ROOT.resolve()),
                "artifact_identity": "a286fc34c643800cf5ba9d8071ce78be9938fa2f64507fa0ab828b71eeea3732",
            },
            "ru_manifest": _descriptor(RU_MANIFEST),
        },
        "configuration": {
            "fusion": FUSION.to_dict(),
            "candidate_supply_depth": 500,
            "rerank_depth": 500,
            "final_top_n": 20,
            "reranker_projection_max_chars": PROJECTION_MAX_CHARS,
            "assembly": "accepted current-C assembly_config",
            "generation": generation_config.audit_projection(),
        },
        "budgets": {"embedding": 0, "reranker": 70, "generation": 70},
        "completed_questions": [],
        "actual_provider_calls": {"embedding": 0, "reranker": 0, "generation": 0},
        "network_provider_occurrences": 0,
    }
    _write(output_root / "metadata/manifest.json", manifest)
    ledger = output_root / "metadata/provider_attempts.jsonl"
    _append(
        ledger,
        {
            "event": "run_started",
            "ledger_schema_version": LEDGER_SCHEMA_VERSION,
            "recovery_contract_version": RECOVERY_CONTRACT_VERSION,
            "run_identity": manifest["run_identity"],
            "question_id": None,
            "stage": GENERATION_STAGE,
            "arm": GENERATION_ARM,
            "occurrence_id": None,
            "attempt_count": 1,
            "expected_question_ids": list(QUESTION_IDS),
            "embedding_calls": 0,
            "reranker_calls": 0,
            "generation_budget": 70,
            "automatic_retry": 0,
        },
    )
    _validate_recovery_manifest(output_root, manifest)
    _reconcile_recovery(output_root, manifest, _ledger_state(ledger, run_identity=manifest["run_identity"]))
    return _execute_from_manifest(output_root, manifest, windows, questions, vectors, generation_config, ledger, env, model_path=model_path)


def _resume_bge_full70(
    output_root: Path,
    *,
    model_path: Path,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    manifest = _read(output_root / "metadata/manifest.json")
    _validate_recovery_manifest(output_root, manifest)
    if manifest["model"] != _expected_model_identity(model_path):
        raise BgeFull70GateError("resume model root mismatch")
    ledger = _ledger_state(output_root / "metadata/provider_attempts.jsonl", run_identity=manifest["run_identity"])
    done = _reconcile_recovery(output_root, manifest, ledger)
    if done == QUESTION_IDS:
        return manifest
    windows = _load_windows(CURRENT_C_ROOT)
    questions = load_m2_runtime_questions(RUNTIME_INPUT)
    vectors = load_accepted_qwen_query_vectors(QUERY_ROOT)
    if tuple(question.question_id for question in questions) != QUESTION_IDS or tuple(vector.question_id for vector in vectors) != QUESTION_IDS:
        raise BgeFull70GateError("runtime question/vector membership is not Q001-Q070")
    if len(done) > MAX_GENERATION_CALLS:
        raise BgeFull70GateError("Generation ledger exceeds hard budget")
    env = dict(environment or os.environ)
    config = _generation_config(env)
    if manifest["configuration"]["generation"] != config.audit_projection():
        raise BgeFull70GateError("resume Generation configuration mismatch")
    _verify_bge_model_identity(model_path)
    return _execute_from_manifest(
        output_root,
        manifest,
        windows,
        questions,
        vectors,
        config,
        ledger,
        env,
        model_path=model_path,
        skip=set(done),
    )


def _execute_from_manifest(
    output_root: Path,
    manifest: dict[str, Any],
    windows: Mapping[str, Mapping[str, Any]],
    questions: Any,
    vectors: Any,
    generation_config: Any,
    ledger: Path,
    environment: Mapping[str, str],
    *,
    model_path: Path,
    skip: set[str] | None = None,
) -> dict[str, Any]:
    skip = set(skip or ())
    counters = {"embedding": 0, "reranker": 0, "generation": len(skip)}
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HOME"] = str((output_root / "runtime/hf-home").resolve())
    os.environ["HF_MODULES_CACHE"] = str((output_root / "runtime/hf-modules").resolve())
    reranker = BgeRerankerV2M3(BgeRerankerV2M3Config(Path(model_path), max_length=MAX_LENGTH, batch_size=1))
    reranker._lazy_load()
    provider = BailianGenerationProvider(
        generation_config,
        BailianOpenAICompatibleTransport.from_environment(generation_config, environment=environment),
    )
    state = _build_state(windows)
    try:
        for question, vector in zip(questions, vectors, strict=True):
            question_id = question.question_id
            if question_id in skip:
                continue
            source = windows[question_id]["source_result"]
            assembly_config = SingleQuestionBackendConfig(
                candidate_supply_depth=500,
                rerank_depth=500,
                rerank_output_k=20,
                final_top_n=20,
                reranker_enabled=True,
                rrf_k=60,
                reranker_projection_max_chars=PROJECTION_MAX_CHARS,
                fusion_config=FUSION,
                assembly_config=EvidenceAssemblyConfig(**source["audit"]["config"]["assembly_config"]),
            )
            wrapped = _LedgerGenerationProvider(provider, ledger, question_id, counters, run_identity=manifest["run_identity"])
            result = run_single_question(
                state,
                question.question,
                precomputed_query=vector,
                generation_provider=wrapped,
                reranker=reranker,
                config=assembly_config,
                execution_identity=question_id,
                output_root=output_root / "results" / question_id,
                request_label=question_id,
            )
            if wrapped.terminal_event is None:
                raise BgeFull70GateError(f"missing terminal Generation ledger event: {question_id}")
            counters["reranker"] += 1
            manifest["completed_questions"].append(question_id)
            manifest["actual_provider_calls"] = counters
            manifest["network_provider_occurrences"] = counters["generation"]
            _write(
                output_root / "results" / question_id / "answer_evidence.json",
                {
                    "question_id": question_id,
                    "question": question.question,
                    "answer": result.get("final_answer"),
                    "status": result.get("status"),
                    "citation_validation": result.get("citation_validation"),
                    "packet": result.get("persistence", {}).get("evidence_packet"),
                    "generation": result.get("persistence", {}).get("generation"),
                    "generation_occurrence": wrapped.terminal_event,
                    "error": result.get("error"),
                },
            )
            _write(output_root / "results" / question_id / "bge_rerank_audit.json", reranker.last_request_metadata)
            _write(output_root / "metadata/manifest.json", manifest)
            print(f"[{len(manifest['completed_questions']):02d}/70] {question_id}", flush=True)
    finally:
        state.close()
    if len(manifest["completed_questions"]) == 70:
        rows = []
        for question_id in QUESTION_IDS:
            result = _read(output_root / "results" / question_id / question_id / "rag_result.json")
            rows.append(
                {
                    "question_id": question_id,
                    "question": result["query"]["question_text"],
                    "answer": result.get("final_answer"),
                    "status": result.get("status"),
                    "citation_validation": result.get("citation_validation"),
                    "packet": result.get("persistence", {}).get("evidence_packet"),
                    "generation": result.get("persistence", {}).get("generation"),
                    "error": result.get("error"),
                }
            )
        body = b"".join(canonical_json_bytes(row) + b"\n" for row in rows)
        atomic_write(output_root / "answer_evidence_comparison.jsonl", body)
        ledger_state = _ledger_state(ledger, run_identity=manifest["run_identity"])
        status, terminal_status, failed_questions, execution_complete = _terminal_outcome(QUESTION_IDS, ledger_state["terminal_by_question"])
        manifest["execution_complete"] = execution_complete
        manifest["failed_question_ids"] = failed_questions
        manifest["status"] = status
        manifest["terminal_status"] = terminal_status
        manifest["artifacts"] = {
            "answer_evidence_comparison": _descriptor(output_root / "answer_evidence_comparison.jsonl"),
            "manifest": _descriptor(output_root / "metadata/manifest.json"),
        }
    else:
        manifest["status"] = "in_progress"
        manifest["terminal_status"] = "in_progress"
    _write(output_root / "metadata/manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    print(json.dumps(run_bge_full70(model_path=args.model_path, output_root=args.output_root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
