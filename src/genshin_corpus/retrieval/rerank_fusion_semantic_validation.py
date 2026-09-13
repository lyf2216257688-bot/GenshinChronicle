"""Provider-free preflight and bounded live runner for the Qwen3.8-Max probe.

The preflight validates only the already accepted fused Packets for Q056/Q068.
The live path is intentionally opt-in and reuses the audited Bailian
GenerationProvider/Bailian transport; it never rebuilds Retrieval, reranking,
fusion, Assembly, or Evidence Packets.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.generation.generation import (
    DEFAULT_GENERATION_INSTRUCTION,
    REQUESTED_ALIAS_POLICY,
    BailianControlConfig,
    BailianGenerationProvider,
    BailianOpenAICompatibleTransport,
    GenerationInstruction,
    GenerationProvider,
    GenerationResult,
    project_generation_request,
    validate_citations,
    workspace_from_bailian_base_url,
)
from genshin_corpus.retrieval.evidence_assembly import EVIDENCE_PACKET_SCHEMA_VERSION


PREFLIGHT_SCHEMA_VERSION = "p04-qwen38-max-instruction-v02-preflight-0.1"
LIVE_SCHEMA_VERSION = "p04-qwen38-max-instruction-v02-live-0.1"
SELECTED_QUESTION_IDS = ("Q056", "Q068")
FUSION_ROOT = Path(".local/p04-rerank-fusion-challenger-20260913-repaired-run1")
RUNTIME_INPUT = Path(".local/p04-rag-m2/questions.runtime.jsonl")
V01_PREFLIGHT_ROOT = Path(".local/p04-qwen38-max-thinking-probe-preflight-5764f68393b0879c")
V01_LIVE_ROOT = Path(".local/p04-qwen38-max-thinking-probe-live-20260913-174713")
FUSION_MANIFEST_SHA256 = "8241c5e3194a5964ab7b3bac0ad801f293ffacc8fd8de7197836fae50889368b"
FUSION_COMPARISON_SHA256 = "a0bc7f29321ccdbedd0e02b541c50f0686820b1b872c9b16c036d543cb5d58e0"
FUSION_RUN_IDENTITY = "54f6b75763b0f24e6c605760bf133cfe26b02a6b587a5dd6163fe7b0d6c6e3b6"
FUSION_CONFIG_IDENTITY = "52ba66ef6791e73db51de8f9a78831559b595097428dbe3cfaab601e7e48df50"

FUSED_PACKET_SHA256 = {
    "Q056": "aa771cee883e3f952bb2383e6483025f31d1d0003d24efa30cfc7e9f533b3a0c",
    "Q068": "988effc2c0f636601bac86c81c63b88b6f343ca0742d71ec9ca1eed0798c6175",
}
PRIOR_SEMANTIC_REQUEST_IDENTITY = {
    "Q056": "468b2e8d78b858d06aed0ca471486c43fd4347f9fdc9913bc7e2032c441cb8bc",
    "Q068": "5c381dc5e40f1e058a2b9c6fc8a0da4848101352f8b3f03124249c0df787aba4",
}
V01_GENERATION_ARTIFACT_SHA256 = {
    "Q056": "4eea7ba5ddb82225f7f1571a1c3a05f626d3b7c8880037c396478fb0147c8d56",
    "Q068": "cc60ab9cd5ea5e2c036827ff5dedb7bcaf5900d5738a60db2b558f657787eae2",
}

CHALLENGER_MODEL_ID = "qwen3.8-max"
CHALLENGER_MODEL_REFERENCE_POLICY = REQUESTED_ALIAS_POLICY
CHALLENGER_TEMPERATURE = 0.0
CHALLENGER_ENABLE_THINKING = True
CHALLENGER_THINKING_BUDGET = 4096
CHALLENGER_MAX_OUTPUT_TOKENS = 2048
CHALLENGER_STREAM = False
CHALLENGER_MAX_ATTEMPTS = 1
CHALLENGER_AUTOMATIC_RETRIES = 0

V02_GENERATION_INSTRUCTION = GenerationInstruction(
    instruction_id="evidence_grounded_answer",
    version="0.2",
    text=(
        "仅依据提供的证据回答问题。可以综合多条证据进行自然叙述。"
        "对关键陈述使用 [E01] 形式的证据引用；不得引用未提供的证据。"
        "如果证据不能支持回答，请明确说明。"
        "回答须对应问题所问的确切关系或身份层级，不得将给予者、使用者、接受者等不同角色，"
        "或实体本身、别名、来源实体、分离或衍生个体、相关角色等不同身份层级自动等同。"
        "证据存在冲突、推测、暂定观点或后续明确揭示时，应区分其事实状态，优先采用直接且明确支持"
        "所问关系的证据；若无法由证据消解冲突，应说明不确定性，勿仅因关联而选择实体。"
    ),
)


class SemanticValidationError(ValueError):
    """Raised when a frozen input, identity, or live boundary is invalid."""


@dataclass(frozen=True)
class FutureLiveBudget:
    max_generation_calls: int = 2
    reranker_calls: int = 0
    embedding_calls: int = 0
    retrieval_recomputations: int = 0
    automatic_retries: int = CHALLENGER_AUTOMATIC_RETRIES
    max_attempts: int = CHALLENGER_MAX_ATTEMPTS

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_generation_calls": self.max_generation_calls,
            "reranker_calls": self.reranker_calls,
            "embedding_calls": self.embedding_calls,
            "retrieval_recomputations": self.retrieval_recomputations,
            "automatic_retries": self.automatic_retries,
            "max_attempts": self.max_attempts,
            "selected_questions": list(SELECTED_QUESTION_IDS),
            "new_generation_arm": "fused",
            "stop_after_first_failure": True,
            "persist_incrementally": True,
        }


LIVE_BUDGET = FutureLiveBudget()
FUTURE_LIVE_COMMAND = (
    "$env:PYTHONPATH='src'; python -m genshin_corpus.retrieval.rerank_fusion_semantic_validation "
    "--live-v02 --preflight-root <PREFLIGHT_ROOT> --output-root <NEW_UNIQUE_LIVE_ROOT>"
)


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - error detail is deliberately bounded
        raise SemanticValidationError(f"cannot read {label}") from exc
    if not isinstance(value, dict):
        raise SemanticValidationError(f"{label} must be an object")
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SemanticValidationError(message)


def _artifact(path: Path, *, expected_sha256: str | None = None) -> dict[str, str]:
    _require(path.is_file(), f"missing artifact: {path}")
    actual = _sha256_file(path)
    if expected_sha256 is not None:
        _require(actual == expected_sha256, f"hash binding failed: {path}")
    return {"path": str(path), "sha256": actual}


def _load_runtime_questions() -> dict[str, dict[str, str]]:
    _require(RUNTIME_INPUT.is_file(), "runtime question input is missing")
    questions: dict[str, dict[str, str]] = {}
    for line_number, line in enumerate(RUNTIME_INPUT.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SemanticValidationError(f"runtime question line {line_number} is invalid") from exc
        _require(isinstance(row, dict) and set(row) == {"question_id", "question"}, f"runtime question line {line_number} shape mismatch")
        question_id, question = row["question_id"], row["question"]
        _require(isinstance(question_id, str) and isinstance(question, str) and question.strip(), f"runtime question line {line_number} value mismatch")
        _require(question_id not in questions, f"duplicate runtime question: {question_id}")
        questions[question_id] = {
            "question_id": question_id,
            "question": question,
            "question_identity": sha256_json(row),
        }
    _require(tuple(question_id for question_id in SELECTED_QUESTION_IDS if question_id in questions) == SELECTED_QUESTION_IDS, "selected question is absent from runtime input")
    return questions


def _challenger_config(*, endpoint: str, workspace: str) -> BailianControlConfig:
    return BailianControlConfig(
        region="cn-beijing",
        endpoint=endpoint,
        workspace=workspace,
        model_id=CHALLENGER_MODEL_ID,
        model_reference_policy=CHALLENGER_MODEL_REFERENCE_POLICY,
        enable_thinking=CHALLENGER_ENABLE_THINKING,
        thinking_budget=CHALLENGER_THINKING_BUDGET,
        temperature=CHALLENGER_TEMPERATURE,
        max_output_tokens=CHALLENGER_MAX_OUTPUT_TOKENS,
        max_attempts=CHALLENGER_MAX_ATTEMPTS,
        retry_backoff_seconds=0.0,
    )


def _historical_endpoint_workspace() -> tuple[str, str]:
    source = _read_json(
        Path(".local/p04-qwen-rerank-70q-20260912-c1/Q056/control/generation/generation_result.json"),
        "accepted historical Generation",
    )
    provider = source.get("audit", {}).get("provider_execution", {})
    config = provider.get("control_config") if isinstance(provider, Mapping) else None
    _require(isinstance(config, Mapping), "accepted historical Generation config is missing")
    endpoint, workspace = config.get("endpoint"), config.get("workspace")
    _require(isinstance(endpoint, str) and isinstance(workspace, str), "accepted historical endpoint binding is missing")
    return endpoint, workspace


def challenger_execution_config_projection() -> dict[str, Any]:
    """Safe challenger fields persisted in preflight; no endpoint/workspace."""

    return {
        "provider_id": "alibaba_cloud_bailian_model_studio",
        "platform": "Bailian/Model Studio",
        "region": "cn-beijing",
        "model_id": CHALLENGER_MODEL_ID,
        "model_reference_policy": CHALLENGER_MODEL_REFERENCE_POLICY,
        "temperature": CHALLENGER_TEMPERATURE,
        "enable_thinking": CHALLENGER_ENABLE_THINKING,
        "thinking_budget": CHALLENGER_THINKING_BUDGET,
        "max_output_tokens": CHALLENGER_MAX_OUTPUT_TOKENS,
        "stream": CHALLENGER_STREAM,
        "max_attempts": CHALLENGER_MAX_ATTEMPTS,
        "automatic_retries": CHALLENGER_AUTOMATIC_RETRIES,
        "instruction_identity": V02_GENERATION_INSTRUCTION.identity,
        "citation_policy": {"mode": "required", "min_unique_evidence_ids": 1},
        "packet_schema": EVIDENCE_PACKET_SCHEMA_VERSION,
    }


def challenger_execution_config_identity() -> str:
    endpoint, workspace = _historical_endpoint_workspace()
    return _challenger_config(endpoint=endpoint, workspace=workspace).execution_config_identity


def experimental_instruction_binding() -> dict[str, str]:
    return {
        "instruction_id": V02_GENERATION_INSTRUCTION.instruction_id,
        "version": V02_GENERATION_INSTRUCTION.version,
        "text": V02_GENERATION_INSTRUCTION.text,
        "text_sha256": sha256(V02_GENERATION_INSTRUCTION.text.encode("utf-8")).hexdigest(),
        "identity": V02_GENERATION_INSTRUCTION.identity,
    }


def instruction_only_semantic_delta(
    v01_request: Any,
    v02_request: Any,
) -> dict[str, Any]:
    """Prove the two provider-visible projections differ only at instruction."""

    v01_projection = v01_request.semantic_projection()
    v02_projection = v02_request.semantic_projection()
    differing_subtrees = [
        key for key in v01_projection
        if v01_projection.get(key) != v02_projection.get(key)
    ]
    _require(differing_subtrees == ["instruction"], "v0.1/v0.2 semantic delta is not instruction-only")
    reconstructed = dict(v01_projection)
    reconstructed["instruction"] = v02_projection["instruction"]
    _require(reconstructed == v02_projection, "v0.2 semantic projection has an unbound delta")
    return {
        "differing_subtrees": differing_subtrees,
        "v01_semantic_projection_sha256": sha256_json(v01_projection),
        "v02_semantic_projection_sha256": sha256_json(v02_projection),
        "unchanged_subtree_sha256": {
            key: sha256_json(v01_projection[key])
            for key in v01_projection
            if key != "instruction"
        },
        "v01_instruction": v01_projection["instruction"],
        "v02_instruction": v02_projection["instruction"],
    }


def _validate_v01_control_artifacts() -> dict[str, Any]:
    preflight_path = V01_PREFLIGHT_ROOT / "metadata" / "manifest.json"
    live_path = V01_LIVE_ROOT / "metadata" / "manifest.json"
    preflight = _read_json(preflight_path, "accepted v0.1 preflight")
    live = _read_json(live_path, "accepted v0.1 live manifest")
    _require(preflight.get("status") == "READY_PROVIDER_FREE_PREFLIGHT", "accepted v0.1 preflight is not READY")
    _require(preflight.get("selected_question_ids") == list(SELECTED_QUESTION_IDS), "accepted v0.1 preflight question mismatch")
    _require(preflight.get("challenger_execution_config_identity") == challenger_execution_config_identity(), "accepted v0.1 config identity mismatch")
    _require(preflight.get("challenger_execution_config", {}).get("instruction_identity") == DEFAULT_GENERATION_INSTRUCTION.identity, "accepted v0.1 instruction binding mismatch")
    _require(live.get("status") == "complete", "accepted v0.1 live run is incomplete")
    _require(live.get("provider_attempts") == 2 and live.get("generation_calls") == 2, "accepted v0.1 live count mismatch")
    _require(live.get("challenger_execution_config_identity") == challenger_execution_config_identity(), "accepted v0.1 live config identity mismatch")
    rows = live.get("rows")
    _require(isinstance(rows, list) and tuple(row.get("question_id") for row in rows) == SELECTED_QUESTION_IDS, "accepted v0.1 live rows mismatch")
    generation_results: dict[str, dict[str, str]] = {}
    for row in rows:
        question_id = row["question_id"]
        _require(row.get("fused_packet_sha256") == FUSED_PACKET_SHA256[question_id], f"{question_id} v0.1 Packet mismatch")
        _require(row.get("semantic_request_identity") == PRIOR_SEMANTIC_REQUEST_IDENTITY[question_id], f"{question_id} v0.1 semantic identity mismatch")
        artifact = row.get("generation_result_artifact")
        _require(isinstance(artifact, Mapping) and artifact.get("sha256") == V01_GENERATION_ARTIFACT_SHA256[question_id], f"{question_id} v0.1 Generation hash mismatch")
        result_path = V01_LIVE_ROOT / str(artifact.get("path", ""))
        generation_results[question_id] = _artifact(result_path, expected_sha256=V01_GENERATION_ARTIFACT_SHA256[question_id])
    return {
        "v01_preflight_manifest": _artifact(preflight_path),
        "v01_live_manifest": _artifact(live_path),
        "v01_generation_results": generation_results,
        "historical_network_accounting": {
            "reported_provider_network_calls": live.get("provider_network_calls"),
            "successful_http_200_rows": 2,
            "repair_scope": "future_transport_invocation_count_only",
        },
    }


def _validate_fused_packet(question_id: str, question: str) -> dict[str, Any]:
    path = FUSION_ROOT / "packets" / question_id / "fused.json"
    packet = _read_json(path, f"{question_id} fused Packet")
    physical = _artifact(path, expected_sha256=FUSED_PACKET_SHA256[question_id])
    _require(packet.get("schema_version") == EVIDENCE_PACKET_SCHEMA_VERSION, f"{question_id} Packet schema mismatch")
    retrieval_audit = packet.get("retrieval_audit")
    metadata = retrieval_audit.get("retrieval_metadata") if isinstance(retrieval_audit, Mapping) else None
    _require(isinstance(metadata, Mapping), f"{question_id} retrieval metadata missing")
    _require(metadata.get("query_id") == question_id and metadata.get("query_text") == question, f"{question_id} question binding mismatch")
    fusion = metadata.get("fusion_challenger")
    _require(isinstance(fusion, Mapping) and fusion.get("config_identity") == FUSION_CONFIG_IDENTITY, f"{question_id} fusion identity mismatch")
    _require(isinstance(packet.get("assembly_config"), Mapping), f"{question_id} assembly config missing")
    _require(isinstance(packet.get("budget"), Mapping), f"{question_id} budget missing")
    v01_request = project_generation_request(packet, question=question, question_id=question_id)
    v02_request = project_generation_request(packet, question=question, question_id=question_id, instruction=V02_GENERATION_INSTRUCTION)
    _require(v01_request.evidence_packet_sha256 == v02_request.evidence_packet_sha256 == FUSED_PACKET_SHA256[question_id], f"{question_id} Packet digest mismatch")
    _require(v01_request.semantic_request_identity == PRIOR_SEMANTIC_REQUEST_IDENTITY[question_id], f"{question_id} v0.1 semantic request identity mismatch")
    _require(v02_request.semantic_request_identity != v01_request.semantic_request_identity, f"{question_id} v0.2 semantic request identity did not change")
    delta = instruction_only_semantic_delta(v01_request, v02_request)
    return {
        "fused_packet": physical,
        "fused_packet_sha256": physical["sha256"],
        "v01_semantic_request_identity": v01_request.semantic_request_identity,
        "v02_semantic_request_identity": v02_request.semantic_request_identity,
        "instruction_only_semantic_delta": delta,
        "generation_visible_evidence_ids": [evidence.evidence_id for evidence in v02_request.evidence],
    }


def _validate_no_secret_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            _require(not any(marker in lowered for marker in ("api_key", "authorization", "credential", "secret", "password", "endpoint", "base_url", "workspace")), "secret-bearing field cannot be persisted")
            _validate_no_secret_keys(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _validate_no_secret_keys(child)


def _validate_no_reference_leakage(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            _require(key not in {"reference_answer", "human_review", "reasoning_content", "raw_response"}, "review/raw reasoning field leaked into runtime artifact")
            _validate_no_reference_leakage(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _validate_no_reference_leakage(child)


def validate_live_budget(budget: FutureLiveBudget = LIVE_BUDGET) -> None:
    _require(budget.max_generation_calls == 2, "probe Generation budget must be exactly 2")
    _require(budget.reranker_calls == 0 and budget.embedding_calls == 0 and budget.retrieval_recomputations == 0, "probe widens Retrieval scope")
    _require(budget.automatic_retries == 0 and budget.max_attempts == 1, "probe permits retries")


def live_arm_allowed(arm: str) -> bool:
    return arm == "fused"


def stop_after_first_live_failure(statuses: Sequence[str]) -> tuple[str, ...]:
    completed: list[str] = []
    for status in statuses:
        if status != "succeeded":
            break
        completed.append(status)
    return tuple(completed)


def compute_preflight_identity(
    *,
    source_bindings: Mapping[str, Any],
    effective_question_bindings: Sequence[Mapping[str, Any]],
    implementation_sha256: str,
    challenger_config: Mapping[str, Any] | None = None,
    challenger_config_identity: str | None = None,
    instruction_binding: Mapping[str, Any] | None = None,
) -> str:
    return sha256_json({
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "selected_question_ids": list(SELECTED_QUESTION_IDS),
        "source_bindings": source_bindings,
        "effective_question_bindings": list(effective_question_bindings),
        "implementation_sha256": implementation_sha256,
        "challenger_execution_config": dict(challenger_config or challenger_execution_config_projection()),
        "challenger_execution_config_identity": challenger_config_identity or challenger_execution_config_identity(),
        "experimental_instruction": dict(instruction_binding or experimental_instruction_binding()),
        "max_provider_attempts": 2,
        "automatic_retries": 0,
        "provider_network_calls_during_preflight": 0,
    })


def _preflight_payload_sha(manifest: Mapping[str, Any]) -> str:
    payload = dict(manifest)
    payload.pop("manifest_payload_sha256", None)
    return sha256(canonical_json_bytes(payload)).hexdigest()


def _implementation_bindings() -> dict[str, dict[str, str]]:
    runner = Path(__file__).resolve()
    transport = Path(__file__).resolve().parents[1] / "generation" / "generation.py"
    return {
        "runner": {"path": str(runner), "sha256": _sha256_file(runner)},
        "generation_transport": {"path": str(transport), "sha256": _sha256_file(transport)},
    }


def _safe_live_generation_payload(result: GenerationResult) -> dict[str, Any]:
    raw = result.to_dict()
    audit = raw["audit"]
    provider = audit.get("provider_execution", {})
    safe_provider = {
        key: provider[key]
        for key in ("provider_id", "generation_occurrence", "attempts", "response_sha256")
        if key in provider
    }
    payload = {
        "schema_version": raw["schema_version"],
        "result": raw["result"],
        "audit": {
            "semantic_request_identity": audit["semantic_request_identity"],
            "execution_config_identity": audit["execution_config_identity"],
            "request": audit["request"],
            "provider_execution": safe_provider,
        },
    }
    _validate_no_secret_keys(payload)
    _validate_no_reference_leakage(payload)
    serialized = canonical_json_bytes(payload).decode("utf-8").lower()
    _require("reasoning_content" not in serialized and "raw_response" not in serialized, "raw reasoning payload cannot be persisted")
    _require("https://" not in serialized and "bailian_base_url" not in serialized, "provider origin cannot be persisted")
    return payload


def _persist_live_occurrence(output_root: Path, question_id: str, result: GenerationResult) -> dict[str, Any]:
    payload = _safe_live_generation_payload(result)
    body = canonical_json_bytes(payload)
    path = output_root / "results" / question_id / "generation_result.json"
    if path.exists() and path.read_bytes() != body:
        raise SemanticValidationError(f"refusing to overwrite live Generation result: {path}")
    if not path.exists():
        atomic_write(path, body)
    return {"path": str(path.relative_to(output_root)), "sha256": sha256(body).hexdigest(), "byte_count": len(body)}


def _provider_network_call_count(provider: GenerationProvider) -> int | None:
    value = getattr(provider, "provider_network_calls", None)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _runtime_bailian_provider(*, expected_identity: str, environment: Mapping[str, str] | None = None) -> GenerationProvider:
    values = os.environ if environment is None else environment
    endpoint = values.get("BAILIAN_BASE_URL")
    _require(isinstance(endpoint, str) and endpoint, "BAILIAN_BASE_URL must be set for live execution")
    workspace = workspace_from_bailian_base_url(endpoint)
    config = _challenger_config(endpoint=endpoint, workspace=workspace)
    _require(config.execution_config_identity == expected_identity, "runtime challenger config identity mismatch")
    transport = BailianOpenAICompatibleTransport.from_environment(config, environment=values)
    return BailianGenerationProvider(config, transport)


def _verify_preflight_for_live(preflight_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    _require(preflight_root.is_dir(), "preflight root is missing")
    manifest_path = preflight_root / "metadata" / "manifest.json"
    manifest = _read_json(manifest_path, "preflight manifest")
    _require(manifest.get("schema_version") == PREFLIGHT_SCHEMA_VERSION and manifest.get("status") == "READY_PROVIDER_FREE_PREFLIGHT", "preflight is not READY")
    _require(manifest.get("manifest_payload_sha256") == _preflight_payload_sha(manifest), "preflight payload identity mismatch")
    _require(_sha256_file(manifest_path) == sha256(canonical_json_bytes(manifest)).hexdigest(), "preflight manifest physical hash mismatch")
    implementations = manifest.get("implementation_bindings")
    _require(isinstance(implementations, Mapping) and implementations == _implementation_bindings(), "live implementation SHA drift")
    _require(manifest.get("selected_question_ids") == list(SELECTED_QUESTION_IDS), "preflight question selection mismatch")
    challenger = manifest.get("challenger_execution_config")
    _require(challenger == challenger_execution_config_projection(), "preflight challenger config mismatch")
    instruction = manifest.get("experimental_instruction")
    _require(instruction == experimental_instruction_binding(), "preflight v0.2 instruction binding mismatch")
    expected_identity = manifest.get("challenger_execution_config_identity")
    _require(isinstance(expected_identity, str) and expected_identity == challenger_execution_config_identity(), "preflight challenger identity mismatch")
    budget = manifest.get("future_live_budget")
    _require(isinstance(budget, Mapping), "preflight live budget missing")
    validate_live_budget(FutureLiveBudget(**{key: budget[key] for key in ("max_generation_calls", "reranker_calls", "embedding_calls", "retrieval_recomputations", "automatic_retries", "max_attempts")}))
    rows = manifest.get("rows")
    _require(isinstance(rows, list) and tuple(row.get("question_id") for row in rows) == SELECTED_QUESTION_IDS, "preflight rows mismatch")
    row_map = {row["question_id"]: row for row in rows}
    recomputed = compute_preflight_identity(
        source_bindings=manifest.get("source_bindings", {}),
        effective_question_bindings=[row_map[qid]["effective_input_binding"] for qid in SELECTED_QUESTION_IDS],
        implementation_sha256=implementations["runner"]["sha256"],
        challenger_config=challenger,
        challenger_config_identity=expected_identity,
        instruction_binding=instruction,
    )
    _require(manifest.get("preflight_identity") == recomputed, "preflight identity recomputation mismatch")
    _validate_no_secret_keys(manifest)
    _validate_no_reference_leakage(manifest)
    for qid in SELECTED_QUESTION_IDS:
        binding = row_map[qid].get("effective_input_binding")
        _require(isinstance(binding, Mapping), f"{qid} input binding missing")
        packet_path = Path(row_map[qid]["fused_packet"]["path"])
        _require(_sha256_file(packet_path) == binding.get("fused_packet_sha256") == FUSED_PACKET_SHA256[qid], f"{qid} fused Packet drift")
        _require(row_map[qid].get("v01_semantic_request_identity") == PRIOR_SEMANTIC_REQUEST_IDENTITY[qid], f"{qid} v0.1 semantic identity drift")
        _require(isinstance(row_map[qid].get("v02_semantic_request_identity"), str), f"{qid} v0.2 semantic identity missing")
        _require(row_map[qid].get("v02_semantic_request_identity") != PRIOR_SEMANTIC_REQUEST_IDENTITY[qid], f"{qid} v0.2 semantic identity did not change")
        delta = row_map[qid].get("instruction_only_semantic_delta")
        _require(isinstance(delta, Mapping) and delta.get("differing_subtrees") == ["instruction"], f"{qid} semantic delta proof mismatch")
    return manifest, row_map


def run_live_probe(
    *,
    preflight_root: Path,
    output_root: Path,
    provider: GenerationProvider | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run exactly two fused Qwen3.8-Max attempts after a verified preflight."""

    manifest, row_map = _verify_preflight_for_live(Path(preflight_root))
    output_root = Path(output_root)
    _require(not output_root.exists(), "refusing to overwrite existing live output root")
    active_provider = provider if provider is not None else _runtime_bailian_provider(
        expected_identity=manifest["challenger_execution_config_identity"],
        environment=environment,
    )
    output_root.mkdir(parents=True)
    live_manifest: dict[str, Any] = {
        "schema_version": LIVE_SCHEMA_VERSION,
        "status": "running",
        "preflight_identity": manifest["preflight_identity"],
        "selected_question_ids": list(SELECTED_QUESTION_IDS),
        "challenger_execution_config_identity": manifest["challenger_execution_config_identity"],
        "future_live_budget": LIVE_BUDGET.to_dict(),
        "provider_attempts": 0,
        "generation_calls": 0,
        "instruction_identity": V02_GENERATION_INSTRUCTION.identity,
        "rows": [],
    }
    atomic_write(output_root / "metadata" / "manifest.json", canonical_json_bytes(live_manifest))
    questions = _load_runtime_questions()
    failure_seen = False
    for index, question_id in enumerate(SELECTED_QUESTION_IDS):
        row = row_map[question_id]
        packet_path = Path(row["fused_packet"]["path"])
        packet = _read_json(packet_path, f"{question_id} fused Packet")
        v01_request = project_generation_request(packet, question=questions[question_id]["question"], question_id=question_id)
        request = project_generation_request(
            packet,
            question=questions[question_id]["question"],
            question_id=question_id,
            instruction=V02_GENERATION_INSTRUCTION,
        )
        _require(v01_request.semantic_request_identity == PRIOR_SEMANTIC_REQUEST_IDENTITY[question_id], f"{question_id} v0.1 request identity drift")
        _require(request.semantic_request_identity == row["v02_semantic_request_identity"], f"{question_id} v0.2 request identity drift")
        instruction_only_semantic_delta(v01_request, request)
        live_manifest["generation_calls"] += 1
        live_manifest["provider_attempts"] += 1
        returned_result: GenerationResult | None = None
        occurrence: dict[str, Any] | None = None
        failure_stage = "provider_return"
        try:
            result = active_provider.generate(request)
            _require(isinstance(result, GenerationResult), f"{question_id} provider returned invalid result")
            returned_result = result
            # Paid occurrence durability precedes every local citation/config check.
            occurrence = _persist_live_occurrence(output_root, question_id, result)
            attempts = result.provider_audit.get("attempts", []) if isinstance(result.provider_audit, Mapping) else []
            failure_stage = "execution_config"
            _require(result.execution_config_identity == manifest["challenger_execution_config_identity"], f"{question_id} runtime config drift")
            failure_stage = "provider_accounting"
            _require(isinstance(attempts, list) and len(attempts) == 1, f"{question_id} provider accounting mismatch")
            status = result.execution_status
            local_validation: dict[str, Any] = {"status": "not_applicable"}
            if status == "succeeded":
                failure_stage = "citation_integrity"
                validation = validate_citations(result.answer_text or "", request)
                _require(result.citation_validation is not None and result.citation_validation.to_dict() == validation.to_dict(), f"{question_id} citation validation mismatch")
                _require(validation.integrity == "pass", f"{question_id} citation integrity failure")
                failure_stage = "citation_coverage"
                _require(validation.coverage == "pass", f"{question_id} citation coverage failure")
                local_validation = {"status": "pass", "citation_integrity": validation.integrity, "citation_coverage": validation.coverage}
            live_manifest["rows"].append({
                "question_id": question_id,
                "question_identity": questions[question_id]["question_identity"],
                "fused_packet_sha256": FUSED_PACKET_SHA256[question_id],
                "v01_semantic_request_identity": v01_request.semantic_request_identity,
                "semantic_request_identity": request.semantic_request_identity,
                "execution_config_identity": result.execution_config_identity,
                "provider_execution_status": status,
                "provider_attempt_count": 1,
                "generation_result_artifact": occurrence,
                "local_citation_validation": local_validation,
                "execution_status": status,
            })
            if status != "succeeded":
                failure_seen = True
        except Exception as exc:
            failure_seen = True
            local_failure = {"status": "failed", "stage": failure_stage, "failure_type": type(exc).__name__}
            atomic_write(output_root / "results" / question_id / "local_validation.json", canonical_json_bytes(local_failure))
            live_manifest["rows"].append({
                "question_id": question_id,
                "question_identity": questions[question_id]["question_identity"],
                "fused_packet_sha256": FUSED_PACKET_SHA256[question_id],
                "v01_semantic_request_identity": v01_request.semantic_request_identity,
                "semantic_request_identity": request.semantic_request_identity,
                "provider_execution_status": returned_result.execution_status if returned_result is not None else "not_returned",
                "provider_attempt_count": 1,
                "generation_result_artifact": occurrence,
                "execution_status": "local_validation_failed" if returned_result is not None else "local_failure",
                "local_failure_type": type(exc).__name__,
            })
        provider_network_calls = _provider_network_call_count(active_provider)
        if provider_network_calls is not None:
            live_manifest["provider_network_calls"] = provider_network_calls
        if failure_seen:
            live_manifest["rows"].extend({"question_id": rest, "execution_status": "NOT_RUN"} for rest in SELECTED_QUESTION_IDS[index + 1:])
            live_manifest["status"] = "partial_failed"
            atomic_write(output_root / "metadata" / "manifest.json", canonical_json_bytes(live_manifest))
            return live_manifest
        atomic_write(output_root / "metadata" / "manifest.json", canonical_json_bytes(live_manifest))
    live_manifest["status"] = "complete"
    atomic_write(output_root / "metadata" / "manifest.json", canonical_json_bytes(live_manifest))
    return live_manifest


def run_live_fused(**kwargs: Any) -> dict[str, Any]:
    """Compatibility alias for the experiment's fused-only live runner."""

    return run_live_probe(**kwargs)


def run_preflight(*, output_root: Path | None = None) -> dict[str, Any]:
    """Validate and persist one unique provider-free v0.2 Q056/Q068 preflight."""

    validate_live_budget()
    _require(tuple(SELECTED_QUESTION_IDS) == ("Q056", "Q068"), "probe question selection changed")
    questions = _load_runtime_questions()
    fusion_manifest_path = FUSION_ROOT / "metadata" / "manifest.json"
    fusion_comparison_path = FUSION_ROOT / "metadata" / "comparison.json"
    runtime_artifact = _artifact(RUNTIME_INPUT)
    fusion_manifest_artifact = _artifact(fusion_manifest_path, expected_sha256=FUSION_MANIFEST_SHA256)
    fusion_comparison_artifact = _artifact(fusion_comparison_path, expected_sha256=FUSION_COMPARISON_SHA256)
    fusion_manifest = _read_json(fusion_manifest_path, "fusion manifest")
    fusion_comparison = _read_json(fusion_comparison_path, "fusion comparison")
    _require(fusion_manifest.get("run_identity") == FUSION_RUN_IDENTITY and fusion_manifest.get("status") == "complete", "fusion run binding mismatch")
    _require(fusion_manifest.get("fusion_config_identity") == FUSION_CONFIG_IDENTITY, "fusion config binding mismatch")
    _require(fusion_manifest.get("provider_calls") == {"embedding": 0, "generation": 0, "network": 0, "rerank": 0}, "fusion provider accounting mismatch")
    _require(fusion_comparison.get("run_identity") == FUSION_RUN_IDENTITY, "fusion comparison run binding mismatch")
    v01_control_bindings = _validate_v01_control_artifacts()
    comparison_rows = {row.get("question_id"): row for row in fusion_comparison.get("questions", []) if isinstance(row, Mapping)}
    rows: list[dict[str, Any]] = []
    for question_id in SELECTED_QUESTION_IDS:
        _require(question_id in comparison_rows, f"{question_id} fusion comparison row missing")
        question = questions[question_id]["question"]
        packet_binding = _validate_fused_packet(question_id, question)
        rows.append({
            "question_id": question_id,
            "question_identity": questions[question_id]["question_identity"],
            "question": question,
            "fused_packet": packet_binding["fused_packet"],
            "v01_semantic_request_identity": packet_binding["v01_semantic_request_identity"],
            "v02_semantic_request_identity": packet_binding["v02_semantic_request_identity"],
            "instruction_only_semantic_delta": packet_binding["instruction_only_semantic_delta"],
            "effective_input_binding": {
                "fused_packet_sha256": packet_binding["fused_packet_sha256"],
                "v01_semantic_request_identity": packet_binding["v01_semantic_request_identity"],
                "v02_semantic_request_identity": packet_binding["v02_semantic_request_identity"],
                "comparison_row_sha256": sha256(canonical_json_bytes(comparison_rows[question_id])).hexdigest(),
            },
        })
    challenger_projection = challenger_execution_config_projection()
    challenger_identity = challenger_execution_config_identity()
    implementations = _implementation_bindings()
    source_bindings = {
        "fusion_manifest": fusion_manifest_artifact,
        "fusion_comparison": fusion_comparison_artifact,
        "runtime_input": runtime_artifact,
        "generation_transport": implementations["generation_transport"],
        **v01_control_bindings,
    }
    implementation_sha256 = implementations["runner"]["sha256"]
    identity = compute_preflight_identity(
        source_bindings=source_bindings,
        effective_question_bindings=[row["effective_input_binding"] for row in rows],
        implementation_sha256=implementation_sha256,
        challenger_config=challenger_projection,
        challenger_config_identity=challenger_identity,
        instruction_binding=experimental_instruction_binding(),
    )
    root = Path(output_root) if output_root is not None else Path(f".local/p04-qwen38-max-instruction-v02-preflight-{identity[:16]}")
    _require(not root.exists(), f"refusing to overwrite existing preflight root: {root}")
    manifest: dict[str, Any] = {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "status": "READY_PROVIDER_FREE_PREFLIGHT",
        "preflight_identity": identity,
        "implementation_bindings": implementations,
        "selected_question_ids": list(SELECTED_QUESTION_IDS),
        "source_bindings": source_bindings,
        "fusion": {"run_identity": FUSION_RUN_IDENTITY, "config_identity": FUSION_CONFIG_IDENTITY, "packet_sha256": dict(FUSED_PACKET_SHA256)},
        "challenger_execution_config": challenger_projection,
        "challenger_execution_config_identity": challenger_identity,
        "experimental_instruction": experimental_instruction_binding(),
        "future_live_budget": LIVE_BUDGET.to_dict(),
        "max_provider_attempts": 2,
        "zero_provider_accounting": {"provider_network_calls_during_preflight": 0, "generation": 0, "network": 0, "rerank": 0, "embedding": 0},
        "future_live_command": FUTURE_LIVE_COMMAND,
        "rows": rows,
    }
    _validate_no_secret_keys(manifest)
    _validate_no_reference_leakage(manifest)
    manifest["manifest_payload_sha256"] = _preflight_payload_sha(manifest)
    root.mkdir(parents=True)
    atomic_write(root / "metadata" / "preflight.json", canonical_json_bytes(manifest))
    atomic_write(root / "metadata" / "manifest.json", canonical_json_bytes(manifest))
    for row in rows:
        atomic_write(root / "results" / f"{row['question_id']}.json", canonical_json_bytes(row))
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight-v02", action="store_true")
    parser.add_argument("--live-v02", action="store_true")
    parser.add_argument("--preflight-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args(argv)
    if args.live_v02:
        if args.preflight_root is None or args.output_root is None:
            raise SystemExit("live probe requires --preflight-root and --output-root")
        result = run_live_probe(preflight_root=args.preflight_root, output_root=args.output_root)
        print(json.dumps({"status": result["status"], "root": str(args.output_root), "provider_attempts": result["provider_attempts"]}, ensure_ascii=False))
        return 0
    if args.preflight_root is not None:
        raise SystemExit("--preflight-root is valid only for live execution")
    if not args.preflight_v02:
        raise SystemExit("select --preflight-v02 or --live-v02")
    result = run_preflight(output_root=args.output_root)
    print(json.dumps({"status": result["status"], "preflight_identity": result["preflight_identity"], "root": str(args.output_root) if args.output_root else f".local/p04-qwen38-max-instruction-v02-preflight-{result['preflight_identity'][:16]}"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
