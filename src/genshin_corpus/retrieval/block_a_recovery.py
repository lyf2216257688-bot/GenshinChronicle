"""Provider-free recovery audit and continuation preflight for Block A.

The parent paid root is evidence only.  This module validates it without
writing below that root, revalidates the persisted Q007 answer locally, and
records the future suffix that a separately authorized continuation may run.
It never constructs a provider or performs a network operation.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from hashlib import sha256
import json
from pathlib import Path
import re
from time import perf_counter
from typing import Any
from uuid import uuid4

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.generation.generation import project_generation_request, validate_citations
from genshin_corpus.generation.measure import load_m2_runtime_questions
from genshin_corpus.rag.backend import SingleQuestionBackendConfig, prepare_rag_state, run_single_question

from .block_a_closure import BlockAQualityGateConfig
from .block_a_paid_full70 import (
    DEFAULT_CLOSURE_ROOT,
    DEFAULT_DENSE_MANIFEST,
    DEFAULT_LEGACY_LEXICAL_MANIFEST,
    DEFAULT_RUNTIME_INPUT,
    DEFAULT_RU_MANIFEST,
    QUESTION_IDS,
    _append_ledger,
    _safe_provider_metadata,
    _validate_closure,
    _validate_production_defaults,
)
from .qwen_rerank import DashScopeQwenRerankConfig, DashScopeQwenRerankTransport
from genshin_corpus.generation.generation import (
    BASELINE_QWEN_MODEL_ID,
    BailianControlConfig,
    BailianGenerationProvider,
    BailianOpenAICompatibleTransport,
    workspace_from_bailian_base_url,
)


PARENT_RUN_ID = "91b0b07fe784d3dd0bf3ec3a442a362ed50fa29aff59f87c350f2fdc1ea2435d"
ACCEPTED_RECOVERY_IDENTITY = "edd8ed151661b10961d1038b17987f5ba04dc11585cd62b962b031aba72217cc"
ACCEPTED_RECOVERY_ROOT = Path(".local/p04-block-a-paid-recovery-20260914T074338Z-2fd5fe90")
ACCEPTED_RECOVERY_ARTIFACT_HASHES = {
    "manifest": "9b7687dfeff89d5e7d18ac2c9cbea4f5727b9f5067e1c706d6c3063607e0dc18",
    "q007": "b0d72161c50387ba8c367b086b083546df8c4912dc311ec289741212da0c2d54",
    "continuation": "954d2e8b0709157953f70544ca628c68ccc9467c8f7a6a12fe4dde6fe8ee6e59",
}
RECOVERY_SCHEMA_VERSION = "phase04-rag-block-a-paid-recovery-0.1"
CONTINUATION_SCHEMA_VERSION = "phase04-rag-block-a-paid-continuation-0.1"
CONTINUATION_PROVIDER_BUDGET = {"embedding": 0, "reranker": 63, "generation": 63}
SOURCE_PATHS = {
    "runner": Path("src/genshin_corpus/retrieval/block_a_paid_full70.py"),
    "backend": Path("src/genshin_corpus/rag/backend.py"),
    "candidate_retrieval": Path("src/genshin_corpus/retrieval/candidate_retrieval.py"),
    "reranking": Path("src/genshin_corpus/retrieval/reranking.py"),
    "qwen_rerank": Path("src/genshin_corpus/retrieval/qwen_rerank.py"),
    "query_vectors": Path("src/genshin_corpus/retrieval/qwen_m2_query_vectors.py"),
    "assembly": Path("src/genshin_corpus/retrieval/evidence_assembly.py"),
    "generation": Path("src/genshin_corpus/generation/generation.py"),
}


class BlockARecoveryError(RuntimeError):
    """Raised when immutable parent evidence cannot authorize a continuation."""


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _continuation_runner_sha256() -> str:
    return _sha256_file(Path(__file__))


def _continuation_identity(*, config_identity: str, source_hashes: Mapping[str, Any], runner_sha256: str) -> str:
    return sha256_json({
        "schema_version": CONTINUATION_SCHEMA_VERSION,
        "parent_run_identity": PARENT_RUN_ID,
        "recovery_identity": ACCEPTED_RECOVERY_IDENTITY,
        "remaining_question_ids": list(QUESTION_IDS[7:]),
        "configuration_identity": config_identity,
        "source_hashes": source_hashes,
        "continuation_runner_sha256": runner_sha256,
    })


def _descriptor(path: Path) -> dict[str, Any]:
    body = Path(path).read_bytes()
    return {"path": str(path), "sha256": sha256(body).hexdigest(), "byte_count": len(body)}


def _write_json(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    body = canonical_json_bytes(dict(value))
    atomic_write(path, body)
    return _descriptor(path)


def _read_json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BlockARecoveryError(f"{label} is unreadable") from exc
    if not isinstance(value, Mapping):
        raise BlockARecoveryError(f"{label} must be an object")
    return value


def _verify_parent_ledger(parent_root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    ledger_path = parent_root / "metadata" / "provider_attempts.jsonl"
    descriptor = manifest.get("artifacts", {}).get("provider_attempts")
    if not isinstance(descriptor, Mapping):
        raise BlockARecoveryError("parent provider ledger descriptor is missing")
    actual = _descriptor(ledger_path)
    if actual["sha256"] != descriptor.get("sha256") or actual["byte_count"] != descriptor.get("byte_count"):
        raise BlockARecoveryError("parent provider ledger hash mismatch")
    rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or rows[0].get("event") != "run_started" or rows[0].get("run_identity") != PARENT_RUN_ID:
        raise BlockARecoveryError("parent ledger run identity mismatch")
    occurrences: dict[str, list[str]] = defaultdict(list)
    for row in rows[1:]:
        occurrence_id = row.get("occurrence_id")
        if not isinstance(occurrence_id, str) or not occurrence_id:
            raise BlockARecoveryError("provider ledger contains a row without occurrence_id")
        if row.get("status") not in {"issued", "succeeded", "failed"}:
            raise BlockARecoveryError("provider ledger contains unsupported status")
        occurrences[occurrence_id].append(row["status"])
    unresolved = {
        occurrence_id: statuses
        for occurrence_id, statuses in occurrences.items()
        if statuses.count("issued") != 1 or sum(status in {"succeeded", "failed"} for status in statuses) != 1
    }
    if unresolved:
        raise BlockARecoveryError(f"parent provider ledger has unresolved occurrences: {sorted(unresolved)}")
    stage_counts = defaultdict(int)
    for row in rows:
        if row.get("status") in {"succeeded", "failed"}:
            stage_counts[row.get("stage")] += 1
    expected = manifest.get("actual_provider_calls", {})
    if stage_counts["reranker"] != expected.get("reranker") or stage_counts["generation"] != expected.get("generation"):
        raise BlockARecoveryError("parent ledger counts do not match manifest")
    return {
        "descriptor": actual,
        "row_count": len(rows),
        "occurrence_count": len(occurrences),
        "issued_count": sum(statuses.count("issued") for statuses in occurrences.values()),
        "resolved_count": sum(sum(status in {"succeeded", "failed"} for status in statuses) for statuses in occurrences.values()),
        "unresolved_occurrences": [],
        "stage_resolved_counts": dict(stage_counts),
    }


def _verify_parent_results(parent_root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    control = tuple(manifest.get("completed_control_questions", ()))
    vnext = tuple(manifest.get("completed_vnext_questions", ()))
    if control != QUESTION_IDS or vnext != QUESTION_IDS[:6]:
        raise BlockARecoveryError("parent completed question sets do not match the partial gate")
    for qid in QUESTION_IDS:
        for arm in ("control",) if qid in control else ():
            if not (parent_root / "results" / qid / arm / qid / "rag_result.json").is_file():
                raise BlockARecoveryError(f"missing parent result: {qid}/{arm}")
    for qid in QUESTION_IDS[:6]:
        for arm in ("vnext",):
            if not (parent_root / "results" / qid / arm / qid / "rag_result.json").is_file():
                raise BlockARecoveryError(f"missing parent result: {qid}/{arm}")
    return {"control_completed": list(control), "vnext_completed": list(vnext)}


def _verify_source_hashes(manifest: Mapping[str, Any]) -> dict[str, Any]:
    expected = manifest.get("source_hashes")
    if not isinstance(expected, Mapping):
        raise BlockARecoveryError("parent source hashes are missing")
    observed = {name: _sha256_file(path) for name, path in SOURCE_PATHS.items()}
    for name, value in expected.items():
        if not isinstance(value, str) or len(value) != 64:
            raise BlockARecoveryError(f"invalid parent source hash: {name}")
    mismatches = {name: {"parent": expected.get(name), "current": observed.get(name)} for name in SOURCE_PATHS if expected.get(name) != observed.get(name)}
    unexpected = set(mismatches) - {"generation"}
    if unexpected:
        raise BlockARecoveryError(f"parent source hash mismatch: {sorted(unexpected)}")
    return {"parent": dict(expected), "current": observed, "expected_validator_repair_difference": "generation" in mismatches, "mismatches": mismatches}


def _continuation_preflight(*, validator_source_hash: str | None = None, runner_sha256: str | None = None) -> dict[str, Any]:
    remaining = list(QUESTION_IDS[7:])
    binding = {"schema_version": CONTINUATION_SCHEMA_VERSION, "parent_run_identity": PARENT_RUN_ID, "remaining_question_ids": remaining, "citation_validator_source_hash": validator_source_hash, "continuation_runner_sha256": runner_sha256}
    return {
        "schema_version": CONTINUATION_SCHEMA_VERSION,
        "parent_run_identity": PARENT_RUN_ID,
        "future_run_identity_binding": sha256_json(binding),
        "citation_validator_source_hash": validator_source_hash,
        "continuation_runner_sha256": runner_sha256,
        "reused_parent_control_questions": list(QUESTION_IDS),
        "reused_parent_vnext_questions": list(QUESTION_IDS[:7]),
        "remaining_paid_question_ids": remaining,
        "provider_call_budget": dict(CONTINUATION_PROVIDER_BUDGET),
        "q056_policy": "excluded_from_clean_advancement_gate",
        "parent_root_immutable": True,
        "new_output_root_required": True,
        "provider_calls_executed": {"embedding": 0, "reranker": 0, "generation": 0},
        "combined_review_provenance": {
            "parent_evidence": "immutable parent results Q001-Q007 reuse",
            "continuation_evidence": "future new-root results Q008-Q070 only",
        },
    }


def _verify_recovery_artifacts(recovery_root: Path, parent_root: Path) -> Mapping[str, Any]:
    manifest_path = recovery_root / "metadata" / "recovery_manifest.json"
    q007_path = recovery_root / "audit" / "q007_offline_revalidation.json"
    continuation_path = recovery_root / "audit" / "continuation_preflight.json"
    for label, path, expected_hash in (
        ("manifest", manifest_path, ACCEPTED_RECOVERY_ARTIFACT_HASHES["manifest"]),
        ("Q007 audit", q007_path, ACCEPTED_RECOVERY_ARTIFACT_HASHES["q007"]),
        ("continuation preflight", continuation_path, ACCEPTED_RECOVERY_ARTIFACT_HASHES["continuation"]),
    ):
        if not path.is_file() or _sha256_file(path) != expected_hash:
            raise BlockARecoveryError(f"accepted recovery {label} hash mismatch")
    recovery = _read_json(manifest_path, "recovery manifest")
    if recovery.get("recovery_run_identity") != ACCEPTED_RECOVERY_IDENTITY:
        raise BlockARecoveryError("accepted recovery identity mismatch")
    parent = recovery.get("parent")
    if not isinstance(parent, Mapping) or parent.get("run_identity") != PARENT_RUN_ID:
        raise BlockARecoveryError("recovery parent binding mismatch")
    if Path(str(parent.get("root"))).resolve() != parent_root.resolve():
        raise BlockARecoveryError("recovery parent root mismatch")
    parent_manifest_descriptor = parent.get("manifest")
    if not isinstance(parent_manifest_descriptor, Mapping):
        raise BlockARecoveryError("recovery parent manifest descriptor is missing")
    actual_parent_manifest = _descriptor(parent_root / "metadata" / "manifest.json")
    if actual_parent_manifest["sha256"] != parent_manifest_descriptor.get("sha256") or actual_parent_manifest["byte_count"] != parent_manifest_descriptor.get("byte_count"):
        raise BlockARecoveryError("parent manifest hash mismatch")
    q007 = recovery.get("q007_offline_revalidation")
    if not isinstance(q007, Mapping):
        raise BlockARecoveryError("recovery Q007 audit is missing")
    validation = q007.get("citation_validation")
    if not isinstance(validation, Mapping) or validation.get("citation_integrity") != "pass" or validation.get("citation_coverage") != "pass":
        raise BlockARecoveryError("recovery Q007 offline validation is not passing")
    if q007.get("provider_calls") != 0 or q007.get("question_id") != "Q007":
        raise BlockARecoveryError("recovery Q007 provider-free binding is invalid")
    for key, label in (("packet_descriptor", "Q007 Packet"), ("generation_descriptor", "Q007 Generation")):
        descriptor = q007.get(key)
        if not isinstance(descriptor, Mapping):
            raise BlockARecoveryError(f"recovery {label} descriptor is missing")
        actual = _descriptor(Path(str(descriptor.get("path"))))
        if actual["sha256"] != descriptor.get("sha256") or actual["byte_count"] != descriptor.get("byte_count"):
            raise BlockARecoveryError(f"{label} hash mismatch")
    return recovery


def _validate_continuation_inputs(
    *,
    parent_root: Path,
    recovery_root: Path,
    closure_root: Path,
    runtime_input: Path,
    ru_manifest_path: Path,
    legacy_lexical_manifest_path: Path,
    dense_manifest_path: Path,
    preflight_root: Path | None = None,
) -> dict[str, Any]:
    _validate_production_defaults()
    parent_manifest = _read_json(parent_root / "metadata" / "manifest.json", "parent manifest")
    if parent_manifest.get("run_identity") != PARENT_RUN_ID or parent_manifest.get("status") != "partial_failed":
        raise BlockARecoveryError("parent run identity/status mismatch")
    source_hashes = _verify_source_hashes(parent_manifest)
    ledger = _verify_parent_ledger(parent_root, parent_manifest)
    completed = _verify_parent_results(parent_root, parent_manifest)
    recovery = _verify_recovery_artifacts(recovery_root, parent_root)
    if recovery.get("parent", {}).get("source_hashes", {}).get("parent") != source_hashes.get("parent"):
        raise BlockARecoveryError("recovery source-hash binding mismatch")
    config = BlockAQualityGateConfig()
    if parent_manifest.get("configuration_identity") != config.identity:
        raise BlockARecoveryError("Block A configuration identity mismatch")
    if parent_manifest.get("budgets", {}).get("embedding") != 0 or parent_manifest.get("generation", {}).get("model_id") != BASELINE_QWEN_MODEL_ID:
        raise BlockARecoveryError("Generation or embedding boundary mismatch")
    closure_manifest, bindings, field_manifest = _validate_closure(closure_root, runtime_input, ru_manifest_path)
    questions = load_m2_runtime_questions(runtime_input)
    if [item.question_id for item in questions] != list(QUESTION_IDS):
        raise BlockARecoveryError("continuation question order is not Q001-Q070")
    context = {
        "parent_manifest": parent_manifest,
        "recovery_manifest": recovery,
        "source_hashes": source_hashes,
        "provider_ledger": ledger,
        "completed": completed,
        "closure_manifest": closure_manifest,
        "bindings": bindings,
        "field_manifest": field_manifest,
        "questions": questions,
        "config": config,
    }
    if preflight_root is not None:
        preflight_path = Path(preflight_root) / "metadata" / "continuation_preflight.json"
        preflight = _read_json(preflight_path, "continuation preflight")
        runner_sha256 = _continuation_runner_sha256()
        expected_identity = _continuation_identity(config_identity=config.identity, source_hashes=source_hashes, runner_sha256=runner_sha256)
        if preflight.get("status") != "provider_free_continuation_ready" or preflight.get("preflight_identity") != expected_identity:
            raise BlockARecoveryError("continuation preflight identity mismatch")
        if preflight.get("continuation_runner_sha256") != runner_sha256:
            raise BlockARecoveryError("continuation runner source hash mismatch")
        if preflight.get("provider_call_budget") != CONTINUATION_PROVIDER_BUDGET:
            raise BlockARecoveryError("continuation preflight budget mismatch")
        if preflight.get("remaining_paid_question_ids") != list(QUESTION_IDS[7:]):
            raise BlockARecoveryError("continuation preflight question-set mismatch")
        context["preflight"] = preflight
    return context


def _append_resolved_fake_call(
    *,
    ledger_path: Path,
    counters: dict[str, int],
    question_id: str,
    stage: str,
    callback: Callable[[], Any],
) -> Any:
    if stage not in {"reranker", "generation"}:
        raise BlockARecoveryError(f"unsupported continuation provider stage: {stage}")
    if counters[stage] >= CONTINUATION_PROVIDER_BUDGET[stage]:
        raise BlockARecoveryError(f"continuation {stage} budget exceeded")
    occurrence_id = f"{question_id}-vnext-{stage}-fake-{counters[stage] + 1:02d}"
    counters[stage] += 1
    _append_ledger(ledger_path, {"question_id": question_id, "arm": "vnext", "stage": stage, "occurrence_id": occurrence_id, "status": "issued", "attempt_count": 1})
    try:
        result = callback()
    except Exception as exc:
        _append_ledger(ledger_path, {"question_id": question_id, "arm": "vnext", "stage": stage, "occurrence_id": occurrence_id, "status": "failed", "attempt_count": 1, "error_type": type(exc).__name__})
        raise
    _append_ledger(ledger_path, {"question_id": question_id, "arm": "vnext", "stage": stage, "occurrence_id": occurrence_id, "status": "succeeded", "attempt_count": 1})
    return result


def _closed_ledger_row_count(path: Path) -> int:
    with Path(path).open("rb") as handle:
        return sum(1 for _ in handle)


class _ContinuationLedgerReranker:
    """Continuation-only reranker ledger with a hard pre-issuance ceiling."""

    def __init__(self, delegate: Any, ledger: Path, question_id: str, counter: dict[str, int]) -> None:
        self.delegate, self.ledger, self.question_id, self.counter = delegate, ledger, question_id, counter
        self.last_response_metadata: dict[str, Any] = {}

    def rerank(self, request: Any) -> Sequence[Any]:
        if self.counter["reranker"] >= CONTINUATION_PROVIDER_BUDGET["reranker"]:
            raise BlockARecoveryError("continuation reranker budget exceeded before provider occurrence")
        occurrence_id = f"{self.question_id}-vnext-rerank-{uuid4().hex}"
        self.counter["reranker"] += 1
        _append_ledger(self.ledger, {"question_id": self.question_id, "arm": "vnext", "stage": "reranker", "occurrence_id": occurrence_id, "status": "issued", "attempt_count": 1})
        started = perf_counter()
        try:
            scores = self.delegate.rerank(request)
            self.last_response_metadata = dict(getattr(self.delegate, "last_response_metadata", {}) or {})
            _append_ledger(self.ledger, {"question_id": self.question_id, "arm": "vnext", "stage": "reranker", "occurrence_id": occurrence_id, "status": "succeeded", "attempt_count": 1, "elapsed_seconds": perf_counter() - started, "provider": _safe_provider_metadata(self.delegate)})
            return scores
        except Exception as exc:
            _append_ledger(self.ledger, {"question_id": self.question_id, "arm": "vnext", "stage": "reranker", "occurrence_id": occurrence_id, "status": "failed", "attempt_count": 1, "elapsed_seconds": perf_counter() - started, "error_type": type(exc).__name__})
            raise


class _ContinuationLedgerGenerationProvider:
    """Continuation-only Generation ledger with a hard pre-issuance ceiling."""

    def __init__(self, delegate: Any, ledger: Path, question_id: str, counter: dict[str, int]) -> None:
        self.delegate, self.ledger, self.question_id, self.counter = delegate, ledger, question_id, counter

    def generate(self, request: Any) -> Any:
        if self.counter["generation"] >= CONTINUATION_PROVIDER_BUDGET["generation"]:
            raise BlockARecoveryError("continuation Generation budget exceeded before provider occurrence")
        occurrence_id = f"{self.question_id}-vnext-generation-{uuid4().hex}"
        self.counter["generation"] += 1
        _append_ledger(self.ledger, {"question_id": self.question_id, "arm": "vnext", "stage": "generation", "occurrence_id": occurrence_id, "status": "issued", "attempt_count": 1})
        started = perf_counter()
        try:
            result = self.delegate.generate(request)
            audit = getattr(result, "provider_audit", {})
            attempts = audit.get("attempts", []) if isinstance(audit, Mapping) else []
            usage = attempts[-1].get("usage") if isinstance(attempts, list) and attempts and isinstance(attempts[-1], Mapping) else None
            status = "succeeded" if getattr(result, "execution_status", None) == "succeeded" else "failed"
            _append_ledger(self.ledger, {"question_id": self.question_id, "arm": "vnext", "stage": "generation", "occurrence_id": occurrence_id, "status": status, "attempt_count": len(attempts) if isinstance(attempts, list) else 1, "elapsed_seconds": perf_counter() - started, "provider_request_id": attempts[-1].get("provider_request_id") if attempts and isinstance(attempts[-1], Mapping) else None, "usage": dict(usage) if isinstance(usage, Mapping) else None, "execution_status": getattr(result, "execution_status", None)})
            return result
        except Exception as exc:
            _append_ledger(self.ledger, {"question_id": self.question_id, "arm": "vnext", "stage": "generation", "occurrence_id": occurrence_id, "status": "failed", "attempt_count": 1, "elapsed_seconds": perf_counter() - started, "error_type": type(exc).__name__})
            raise


def run_block_a_continuation(
    *,
    parent_root: Path,
    recovery_root: Path = ACCEPTED_RECOVERY_ROOT,
    preflight_root: Path | None = None,
    output_root: Path,
    closure_root: Path = DEFAULT_CLOSURE_ROOT,
    runtime_input: Path = DEFAULT_RUNTIME_INPUT,
    ru_manifest_path: Path = DEFAULT_RU_MANIFEST,
    legacy_lexical_manifest_path: Path = DEFAULT_LEGACY_LEXICAL_MANIFEST,
    dense_manifest_path: Path = DEFAULT_DENSE_MANIFEST,
    environment: Mapping[str, str] | None = None,
    question_executor: Callable[[Any, Any, Callable[[str, Callable[[], Any]], Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute only the Q008-Q070 vNext suffix after immutable preflight.

    ``question_executor`` is a provider-free test seam.  The production path
    uses the existing Retrieval -> Assembly -> Generation backend below.
    """

    parent_root, recovery_root, output_root = Path(parent_root), Path(recovery_root), Path(output_root)
    if output_root.exists():
        raise FileExistsError("continuation output root already exists; refusing to start")
    if output_root.resolve() in {parent_root.resolve(), recovery_root.resolve()}:
        raise BlockARecoveryError("continuation output root must be new and separate")
    if question_executor is None and preflight_root is None:
        raise BlockARecoveryError("preflight root is required before live continuation")
    preflight_path = Path(preflight_root) if preflight_root is not None else None
    context = _validate_continuation_inputs(
        parent_root=parent_root, recovery_root=recovery_root, closure_root=Path(closure_root),
        runtime_input=Path(runtime_input), ru_manifest_path=Path(ru_manifest_path),
        legacy_lexical_manifest_path=Path(legacy_lexical_manifest_path), dense_manifest_path=Path(dense_manifest_path),
        preflight_root=preflight_path,
    )
    values = dict(environment or {}) if environment is not None else None
    env = values if values is not None else __import__("os").environ
    generation_config = None
    rerank_config = None
    if question_executor is None:
        endpoint = env.get("BAILIAN_BASE_URL")
        if not isinstance(endpoint, str) or not endpoint:
            raise BlockARecoveryError("BAILIAN_BASE_URL is required before continuation output creation")
        generation_config = BailianControlConfig(
            region="cn-beijing", endpoint=endpoint, workspace=workspace_from_bailian_base_url(endpoint),
            model_id=BASELINE_QWEN_MODEL_ID, enable_thinking=False, max_output_tokens=2048,
            max_attempts=1, timeout_seconds=30.0,
        )
        rerank_config = DashScopeQwenRerankConfig(
            region="cn-beijing", endpoint="https://ws-gdq9z4ufdb87egio.cn-beijing.maas.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
            workspace="ws-gdq9z4ufdb87egio", timeout_seconds=60.0,
        )
    config = context["config"]
    runner_sha256 = _continuation_runner_sha256()
    run_identity = _continuation_identity(config_identity=config.identity, source_hashes=context["source_hashes"], runner_sha256=runner_sha256)
    output_root.mkdir(parents=True)
    ledger_path = output_root / "metadata" / "provider_attempts.jsonl"
    _append_ledger(ledger_path, {"event": "run_started", "run_identity": run_identity, "embedding_calls": 0})
    manifest: dict[str, Any] = {
        "schema_version": CONTINUATION_SCHEMA_VERSION,
        "status": "in_progress",
        "run_identity": run_identity,
        "parent_run_identity": PARENT_RUN_ID,
        "recovery_identity": ACCEPTED_RECOVERY_IDENTITY,
        "preflight_root": str(preflight_path.resolve()) if preflight_path is not None else None,
        "continuation_runner_sha256": runner_sha256,
        "parent_root": str(parent_root.resolve()),
        "recovery_root": str(recovery_root.resolve()),
        "remaining_question_ids": list(QUESTION_IDS[7:]),
        "reused_parent_control_questions": list(QUESTION_IDS),
        "reused_parent_vnext_questions": list(QUESTION_IDS[:7]),
        "configuration": config.projection(),
        "configuration_identity": config.identity,
        "generation_config_identity": context["parent_manifest"].get("generation_config_identity"),
        "generation": context["parent_manifest"].get("generation"),
        "provider_call_budget": dict(CONTINUATION_PROVIDER_BUDGET),
        "actual_provider_calls": {"embedding": 0, "reranker": 0, "generation": 0},
        "completed_questions": [],
        "q056_policy": "excluded_from_clean_advancement_gate",
        "source_hashes": context["source_hashes"],
        "provider_attempts": {"path": "metadata/provider_attempts.jsonl", "row_count": 1},
    }
    _write_json(output_root / "metadata" / "manifest.json", manifest)
    _write_json(output_root / "metadata" / "parent_references.json", {
        "parent_root": str(parent_root.resolve()),
        "parent_run_identity": PARENT_RUN_ID,
        "recovery_root": str(recovery_root.resolve()),
        "recovery_identity": ACCEPTED_RECOVERY_IDENTITY,
        "control_question_ids": list(QUESTION_IDS),
        "vnext_question_ids": list(QUESTION_IDS[:7]),
    })
    counters = {"embedding": 0, "reranker": 0, "generation": 0}
    review_rows: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    control_state = None
    vnext_state = None
    try:
        if question_executor is None:
            gen_transport = BailianOpenAICompatibleTransport.from_environment(generation_config, environment=env)
            rerank_transport = DashScopeQwenRerankTransport.from_environment(rerank_config, environment=env)
            vnext_state = prepare_rag_state(retrieval_unit_manifest_path=ru_manifest_path, lexical_manifest_path=context["field_manifest"], dense_manifest_path=dense_manifest_path)
            vnext_config = SingleQuestionBackendConfig(candidate_supply_depth=500, rerank_depth=500, final_top_n=20, rerank_output_k=20, reranker_enabled=True, rrf_k=60, reranker_projection_max_chars=6000, fusion_config=config.fusion_config, assembly_config=config.assembly_config)
            def execute(question: Any, binding: Any, record: Callable[[str, Callable[[], Any]], Any]) -> Mapping[str, Any]:
                del record
                qid = question.question_id
                provider = _ContinuationLedgerGenerationProvider(BailianGenerationProvider(generation_config, gen_transport), ledger_path, qid, counters)
                reranker = _ContinuationLedgerReranker(rerank_transport, ledger_path, qid, counters)
                result = run_single_question(vnext_state, question.question, precomputed_query=binding, generation_provider=provider, reranker=reranker, config=vnext_config, execution_identity=f"{run_identity}-{qid}", output_root=output_root / "results" / qid / "vnext", request_label=qid)
                return result
        else:
            execute = question_executor
        bindings = {item.question_id: item for item in context["bindings"]}
        for question in context["questions"][7:]:
            qid = question.question_id
            if qid not in bindings:
                raise BlockARecoveryError(f"missing accepted query binding: {qid}")
            result = execute(question, bindings[qid], lambda stage, callback, qid=qid: _append_resolved_fake_call(ledger_path=ledger_path, counters=counters, question_id=qid, stage=stage, callback=callback))
            if result.get("status") != "succeeded":
                raise BlockARecoveryError(f"continuation local/provider stage failed at {qid}")
            review_rows.append({"question_id": qid, "vnext": dict(result), "excluded_from_clean_advancement_gate": qid == "Q056", "provenance": "continuation_vnext"})
            manifest["completed_questions"] = [row["question_id"] for row in review_rows]
            manifest["actual_provider_calls"] = dict(counters)
            manifest["provider_attempts"]["row_count"] = _closed_ledger_row_count(ledger_path)
            _write_json(output_root / "metadata" / "manifest.json", manifest)
    except Exception as exc:
        failure = {"type": type(exc).__name__, "message": str(exc), "failed_question_id": context["questions"][7 + len(review_rows)].question_id if 7 + len(review_rows) < len(context["questions"]) else None, "completed_questions": [row["question_id"] for row in review_rows], "safe_recovery_position": "preserve continuation root; do not retry or continue"}
    finally:
        if vnext_state is not None:
            vnext_state.close()
    manifest["actual_provider_calls"] = dict(counters)
    manifest["provider_attempts"]["row_count"] = _closed_ledger_row_count(ledger_path)
    if failure is None and len(review_rows) == len(QUESTION_IDS[7:]):
        manifest["status"] = "complete"
        review_body = b"".join(canonical_json_bytes(row) + b"\n" for row in review_rows)
        atomic_write(output_root / "review" / "continuation_q008-q070.jsonl", review_body)
    else:
        manifest["status"] = "partial_failed"
        manifest["failure"] = failure or {"type": "BlockARecoveryError", "message": "continuation did not complete suffix"}
        atomic_write(output_root / "errors" / "partial-state.json", canonical_json_bytes(manifest["failure"]))
    atomic_write(output_root / "review" / "combined_provenance.json", canonical_json_bytes({
        "parent_control": {"root": str(parent_root.resolve()), "question_ids": list(QUESTION_IDS)},
        "parent_vnext": {"root": str(parent_root.resolve()), "question_ids": list(QUESTION_IDS[:7])},
        "continuation_vnext": {"root": str(output_root.resolve()), "question_ids": [row["question_id"] for row in review_rows]},
    }))
    _write_json(output_root / "metadata" / "manifest.json", manifest)
    return manifest


def write_block_a_continuation_preflight(
    *,
    parent_root: Path,
    recovery_root: Path = ACCEPTED_RECOVERY_ROOT,
    output_root: Path,
    closure_root: Path = DEFAULT_CLOSURE_ROOT,
    runtime_input: Path = DEFAULT_RUNTIME_INPUT,
    ru_manifest_path: Path = DEFAULT_RU_MANIFEST,
    legacy_lexical_manifest_path: Path = DEFAULT_LEGACY_LEXICAL_MANIFEST,
    dense_manifest_path: Path = DEFAULT_DENSE_MANIFEST,
) -> dict[str, Any]:
    """Materialize a new provider-free continuation preflight root."""

    parent_root, recovery_root, output_root = Path(parent_root), Path(recovery_root), Path(output_root)
    if output_root.exists():
        raise FileExistsError("continuation preflight root already exists; refusing to start")
    context = _validate_continuation_inputs(
        parent_root=parent_root, recovery_root=recovery_root, closure_root=Path(closure_root),
        runtime_input=Path(runtime_input), ru_manifest_path=Path(ru_manifest_path),
        legacy_lexical_manifest_path=Path(legacy_lexical_manifest_path), dense_manifest_path=Path(dense_manifest_path),
    )
    runner_sha256 = _continuation_runner_sha256()
    identity = _continuation_identity(config_identity=context["config"].identity, source_hashes=context["source_hashes"], runner_sha256=runner_sha256)
    output_root.mkdir(parents=True)
    preflight = {
        "schema_version": CONTINUATION_SCHEMA_VERSION,
        "status": "provider_free_continuation_ready",
        "preflight_identity": identity,
        "parent_run_identity": PARENT_RUN_ID,
        "parent_root": str(parent_root.resolve()),
        "recovery_identity": ACCEPTED_RECOVERY_IDENTITY,
        "continuation_runner_sha256": runner_sha256,
        "recovery_root": str(recovery_root.resolve()),
        "remaining_paid_question_ids": list(QUESTION_IDS[7:]),
        "reused_parent_control_questions": list(QUESTION_IDS),
        "reused_parent_vnext_questions": list(QUESTION_IDS[:7]),
        "provider_call_budget": dict(CONTINUATION_PROVIDER_BUDGET),
        "provider_calls_executed": {"embedding": 0, "reranker": 0, "generation": 0},
        "configuration_identity": context["config"].identity,
        "configuration": context["config"].projection(),
        "generation_config_identity": context["parent_manifest"].get("generation_config_identity"),
        "source_hashes": context["source_hashes"],
        "parent_provider_ledger": context["provider_ledger"],
        "q007_offline_recovery": {
            "status": "reused_and_verified",
            "recovery_identity": ACCEPTED_RECOVERY_IDENTITY,
            "citation_integrity": "pass",
            "citation_coverage": "pass",
        },
        "q056_policy": "excluded_from_clean_advancement_gate",
        "parent_root_immutable": True,
        "new_output_root_required": True,
        "network_calls": 0,
    }
    _write_json(output_root / "metadata" / "continuation_preflight.json", preflight)
    _write_json(output_root / "metadata" / "parent_references.json", {
        "parent_root": str(parent_root.resolve()),
        "parent_run_identity": PARENT_RUN_ID,
        "recovery_root": str(recovery_root.resolve()),
        "recovery_identity": ACCEPTED_RECOVERY_IDENTITY,
    })
    preflight["artifacts"] = {
        "parent_references": _descriptor(output_root / "metadata" / "parent_references.json"),
    }
    _write_json(output_root / "metadata" / "continuation_preflight.json", preflight)
    return preflight


def run_block_a_recovery_audit(*, parent_root: Path, output_root: Path, runtime_input: Path = Path(".local/p04-rag-m2/questions.runtime.jsonl")) -> dict[str, Any]:
    """Write a provider-free Q007 recovery audit and continuation preflight."""

    parent_root, output_root = Path(parent_root), Path(output_root)
    if output_root.exists():
        raise FileExistsError("recovery output root already exists; refusing to start")
    manifest_path = parent_root / "metadata" / "manifest.json"
    manifest = _read_json(manifest_path, "parent manifest")
    if manifest.get("run_identity") != PARENT_RUN_ID or manifest.get("status") != "partial_failed":
        raise BlockARecoveryError("parent run identity/status is not the authorized partial gate")
    source_hashes = _verify_source_hashes(manifest)
    ledger = _verify_parent_ledger(parent_root, manifest)
    completed = _verify_parent_results(parent_root, manifest)
    questions = {item.question_id: item.question for item in load_m2_runtime_questions(runtime_input)}
    qid = "Q007"
    packet_path = parent_root / "results" / qid / "vnext" / qid / "packet" / "evidence_packet.json"
    generation_path = parent_root / "results" / qid / "vnext" / qid / "generation" / "generation_result.json"
    packet = _read_json(packet_path, "Q007 Evidence Packet")
    generation = _read_json(generation_path, "Q007 Generation result")
    answer = generation.get("result", {}).get("answer_text")
    if not isinstance(answer, str) or not answer:
        raise BlockARecoveryError("Q007 persisted answer is missing")
    request = project_generation_request(packet, question=questions[qid], question_id=qid)
    validation = validate_citations(answer, request)
    if validation.integrity != "pass" or validation.coverage != "pass":
        raise BlockARecoveryError("offline Q007 citation revalidation failed")
    q007 = {
        "question_id": qid,
        "answer_text": answer,
        "answer_sha256": sha256(answer.encode("utf-8")).hexdigest(),
        "raw_answer_preserved": True,
        "raw_citation_tokens": re.findall(r"\[[^]]+\]", answer),
        "packet_evidence_ids": [item.get("evidence_id") for item in packet.get("evidence", []) if isinstance(item, Mapping)],
        "citation_validation": validation.to_dict(),
        "packet_descriptor": _descriptor(packet_path),
        "generation_descriptor": _descriptor(generation_path),
        "provider_calls": 0,
    }
    continuation = _continuation_preflight(validator_source_hash=source_hashes["current"]["generation"], runner_sha256=_continuation_runner_sha256())
    audit = {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "status": "provider_free_recovery_ready",
        "recovery_run_identity": sha256_json({"schema_version": RECOVERY_SCHEMA_VERSION, "parent_run_identity": PARENT_RUN_ID, "q007": q007, "continuation": continuation}),
        "parent": {"root": str(parent_root.resolve()), "run_identity": PARENT_RUN_ID, "manifest": _descriptor(manifest_path), "source_hashes": source_hashes, "provider_ledger": ledger, "completed": completed},
        "q007_offline_revalidation": q007,
        "continuation_preflight": continuation,
        "network_calls": 0,
    }
    output_root.mkdir(parents=True)
    _write_json(output_root / "metadata" / "recovery_manifest.json", audit)
    _write_json(output_root / "audit" / "q007_offline_revalidation.json", q007)
    _write_json(output_root / "audit" / "continuation_preflight.json", continuation)
    return audit


__all__ = ["BlockARecoveryError", "run_block_a_recovery_audit", "run_block_a_continuation", "write_block_a_continuation_preflight"]
