"""Generation contracts and the first Bailian/Qwen control adapter.

The provider-neutral request is deliberately limited to a versioned
instruction, one question, and the generation-visible projection of an
Evidence Packet.  Retrieval audit metadata and provider wire details stay out
of that boundary.  The provider-neutral adapter continues to receive an
explicit transport; the scoped Bailian transport implements the live HTTP
boundary separately in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
import re
import time
from pathlib import Path
from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write

from genshin_corpus.retrieval.evidence_assembly import (
    EVIDENCE_PACKET_SCHEMA_VERSION,
    evidence_packet_json_bytes,
)


GENERATION_REQUEST_SCHEMA_VERSION = "phase04-generation-request-0.1"
GENERATION_RESULT_SCHEMA_VERSION = "phase04-generation-result-0.1"
GENERATION_PROVIDER_ID_BAILIAN = "alibaba_cloud_bailian_model_studio"
BASELINE_QWEN_MODEL_ID = "qwen3.7-plus-2026-05-26"
EXACT_SNAPSHOT_POLICY = "exact_snapshot_required"
SEMANTIC_FAITHFULNESS_NOT_EVALUATED = "not_evaluated"
_EXACT_QWEN_SNAPSHOT = re.compile(r"^qwen[0-9.]+-[a-z]+-[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_EVIDENCE_ID = re.compile(r"^E[0-9]{2,}$")
_BAILIAN_BEIJING_HOST_SUFFIX = ".cn-beijing.maas.aliyuncs.com"
_BAILIAN_COMPATIBLE_BASE_PATH = "/compatible-mode/v1"
# Model Studio's retry guidance is code-and-status-specific.  This explicit
# Chat Completion allowlist deliberately excludes plausible-looking but
# undocumented combinations; unlisted values fail closed without retry.
_BAILIAN_RETRYABLE_ERROR_PAIRS = frozenset({
    (429, "Throttling"),
    (429, "Throttling.RateQuota"),
    (429, "LimitRequests"),
    (429, "limit_requests"),
    (429, "Throttling.BurstRate"),
    (429, "limit_burst_rate"),
    (429, "Throttling.AllocationQuota"),
    (429, "insufficient_quota"),
    (500, "InternalError"),
    (500, "internal_error"),
    (500, "SystemError"),
    (500, "ModelServiceFailed"),
    (500, "RequestTimeOut"),
    (500, "ModelServingError"),
    (503, "ModelServingError"),
    (503, "ModelUnavailable"),
})


class GenerationContractError(ValueError):
    """Raised when a provider-neutral Generation contract is invalid."""


class GenerationConfigurationError(ValueError):
    """Raised when a Bailian control configuration is not safe to execute."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GenerationContractError(f"{label} must be an object")
    return value


def _non_empty_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GenerationContractError(f"{label} must be a non-empty string")
    return value


def _json_safe(value: Any, label: str) -> Any:
    """Fail before a provider audit could contain an unserializable value."""

    try:
        canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise GenerationContractError(f"{label} is not canonical JSON") from exc
    return value


@dataclass(frozen=True)
class GenerationInstruction:
    """A versioned instruction whose complete text is part of request identity."""

    instruction_id: str
    version: str
    text: str

    def __post_init__(self) -> None:
        _non_empty_text(self.instruction_id, "instruction_id")
        _non_empty_text(self.version, "instruction.version")
        _non_empty_text(self.text, "instruction.text")

    def identity_projection(self) -> dict[str, str]:
        return {"instruction_id": self.instruction_id, "version": self.version, "text": self.text}

    @property
    def identity(self) -> str:
        return sha256_json(self.identity_projection())


DEFAULT_GENERATION_INSTRUCTION = GenerationInstruction(
    instruction_id="evidence_grounded_answer",
    version="0.1",
    text=(
        "仅依据提供的证据回答问题。可以综合多条证据进行自然叙述。"
        "对关键陈述使用 [E01] 形式的证据引用；不得引用未提供的证据。"
        "如果证据不能支持回答，请明确说明。"
    ),
)


@dataclass(frozen=True)
class CitationCoveragePolicy:
    """A structural policy only; it does not assess whether citations entail claims."""

    mode: str = "required"
    min_unique_evidence_ids: int = 1

    def __post_init__(self) -> None:
        if self.mode not in {"required", "optional"}:
            raise GenerationContractError("citation coverage mode must be required or optional")
        if not isinstance(self.min_unique_evidence_ids, int) or isinstance(self.min_unique_evidence_ids, bool):
            raise GenerationContractError("citation minimum must be an integer")
        if self.min_unique_evidence_ids < 0:
            raise GenerationContractError("citation minimum must be non-negative")
        if self.mode == "required" and self.min_unique_evidence_ids == 0:
            raise GenerationContractError("required citation coverage must require at least one evidence ID")
        if self.mode == "optional" and self.min_unique_evidence_ids != 0:
            raise GenerationContractError("optional citation coverage must have a zero minimum")

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "min_unique_evidence_ids": self.min_unique_evidence_ids}


@dataclass(frozen=True)
class GenerationEvidence:
    """The safe, provider-visible subset of one Evidence Packet evidence item."""

    evidence_id: str
    text: str
    display_context: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, str) or not _EVIDENCE_ID.fullmatch(self.evidence_id):
            raise GenerationContractError("generation evidence_id must match E followed by at least two digits")
        _non_empty_text(self.text, f"generation evidence {self.evidence_id}.text")
        if any(not isinstance(item, str) or not item for item in self.display_context):
            raise GenerationContractError("generation evidence display_context must contain non-empty strings")

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "text": self.text,
            "display_context": list(self.display_context),
        }


@dataclass(frozen=True)
class GenerationRequest:
    """Provider-neutral semantic input, without Retrieval audit or vendor settings."""

    instruction: GenerationInstruction
    question: str
    evidence: tuple[GenerationEvidence, ...]
    citation_policy: CitationCoveragePolicy
    evidence_packet_schema_version: str
    evidence_packet_sha256: str
    question_id: str | None = None

    def __post_init__(self) -> None:
        _non_empty_text(self.question, "question")
        if not isinstance(self.evidence_packet_schema_version, str) or not self.evidence_packet_schema_version:
            raise GenerationContractError("evidence_packet_schema_version must be a non-empty string")
        if not isinstance(self.evidence_packet_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.evidence_packet_sha256):
            raise GenerationContractError("evidence_packet_sha256 must be a SHA-256 hex digest")
        if self.question_id is not None and (not isinstance(self.question_id, str) or not self.question_id):
            raise GenerationContractError("question_id must be null or a non-empty string")
        identifiers = [item.evidence_id for item in self.evidence]
        if len(identifiers) != len(set(identifiers)):
            raise GenerationContractError("generation-visible evidence IDs must be unique")

    def semantic_projection(self) -> dict[str, Any]:
        """Exactly the output-relevant provider-neutral input, in source order."""

        return {
            "schema_version": GENERATION_REQUEST_SCHEMA_VERSION,
            "instruction": self.instruction.identity_projection(),
            "question": self.question,
            "evidence": [item.to_dict() for item in self.evidence],
            "citation_policy": self.citation_policy.to_dict(),
            "evidence_packet_schema_version": self.evidence_packet_schema_version,
        }

    @property
    def semantic_request_identity(self) -> str:
        return sha256_json(self.semantic_projection())

    def audit_projection(self) -> dict[str, Any]:
        """Reference the original packet without persisting it or its retrieval audit."""

        return {
            "instruction_id": self.instruction.instruction_id,
            "instruction_version": self.instruction.version,
            "instruction_sha256": self.instruction.identity,
            "question_id": self.question_id,
            "evidence_packet_schema_version": self.evidence_packet_schema_version,
            "evidence_packet_sha256": self.evidence_packet_sha256,
            "generation_visible_evidence_ids": [item.evidence_id for item in self.evidence],
            "citation_policy": self.citation_policy.to_dict(),
        }


def _display_context(evidence: Mapping[str, Any]) -> tuple[str, ...]:
    """Extract only existing human-facing labels; provenance remains Packet audit data."""

    contexts: list[str] = []
    members = evidence.get("members", [])
    if not isinstance(members, list):
        raise GenerationContractError("Evidence Packet evidence.members must be a list when present")
    for index, raw_member in enumerate(members):
        member = _mapping(raw_member, f"Evidence Packet evidence.members[{index}]")
        record_context = member.get("record_context", {})
        record_context = _mapping(record_context, f"Evidence Packet member {index}.record_context")
        labels = [
            record_context.get("record_title"),
            record_context.get("section_name"),
        ]
        structure = member.get("structure", {})
        if structure is not None:
            structure = _mapping(structure, f"Evidence Packet member {index}.structure")
            dialogue = structure.get("dialogue")
            if dialogue is not None:
                dialogue = _mapping(dialogue, f"Evidence Packet member {index}.structure.dialogue")
                labels.append(dialogue.get("speaker"))
        label = " / ".join(item for item in labels if isinstance(item, str) and item.strip())
        if label and label not in contexts:
            contexts.append(label)
    return tuple(contexts)


def project_generation_request(
    packet: Mapping[str, Any],
    *,
    question: str,
    instruction: GenerationInstruction = DEFAULT_GENERATION_INSTRUCTION,
    citation_policy: CitationCoveragePolicy | None = None,
    question_id: str | None = None,
) -> GenerationRequest:
    """Project the current Evidence Packet into the only context visible to Generation."""

    value = _mapping(packet, "Evidence Packet")
    if value.get("schema_version") != EVIDENCE_PACKET_SCHEMA_VERSION:
        raise GenerationContractError("Evidence Packet schema is unsupported for Generation")
    evidence_rows = value.get("evidence")
    if not isinstance(evidence_rows, list):
        raise GenerationContractError("Evidence Packet evidence must be a list")
    projected: list[GenerationEvidence] = []
    for index, raw_evidence in enumerate(evidence_rows):
        evidence = _mapping(raw_evidence, f"Evidence Packet evidence[{index}]")
        evidence_id = evidence.get("evidence_id")
        text = evidence.get("text")
        projected.append(GenerationEvidence(
            evidence_id=evidence_id if isinstance(evidence_id, str) else "",
            text=text if isinstance(text, str) else "",
            display_context=_display_context(evidence),
        ))
    body = evidence_packet_json_bytes(value)
    return GenerationRequest(
        instruction=instruction,
        question=question,
        evidence=tuple(projected),
        citation_policy=citation_policy or CitationCoveragePolicy(),
        evidence_packet_schema_version=EVIDENCE_PACKET_SCHEMA_VERSION,
        evidence_packet_sha256=sha256(body).hexdigest(),
        question_id=question_id,
    )


@dataclass(frozen=True)
class CitationValidation:
    """Deterministic citation structure only; semantic support is unassessed."""

    citation_tokens: tuple[str, ...]
    integrity: str
    coverage: str
    reasons: tuple[Mapping[str, Any], ...]
    @property
    def semantic_faithfulness(self) -> str:
        """Fixed absence-of-evaluation marker; G1 cannot set a semantic verdict."""

        return SEMANTIC_FAITHFULNESS_NOT_EVALUATED

    def to_dict(self) -> dict[str, Any]:
        return {
            "citation_tokens": list(self.citation_tokens),
            "citation_integrity": self.integrity,
            "citation_coverage": self.coverage,
            "validation_reasons": [dict(item) for item in self.reasons],
            "semantic_faithfulness": SEMANTIC_FAITHFULNESS_NOT_EVALUATED,
        }


def validate_citations(answer_text: str, request: GenerationRequest) -> CitationValidation:
    """Validate Packet-membership and configured coverage only, never semantic support."""

    _non_empty_text(answer_text, "answer_text")
    valid_ids = {item.evidence_id for item in request.evidence}
    cited: list[str] = []
    reasons: list[dict[str, Any]] = []
    position = 0
    while position < len(answer_text):
        start = answer_text.find("[", position)
        if start < 0:
            break
        position = start + 1
        if not answer_text.startswith("E", position):
            continue
        end = answer_text.find("]", position)
        line_end = answer_text.find("\n", position)
        if end < 0 or (line_end >= 0 and line_end < end):
            reasons.append({"kind": "malformed_citation_token", "token": answer_text[start:line_end if line_end >= 0 else len(answer_text)]})
            break
        token = answer_text[start:end + 1]
        identifier = answer_text[position:end]
        position = end + 1
        if not _EVIDENCE_ID.fullmatch(identifier):
            reasons.append({"kind": "malformed_citation_token", "token": token})
            continue
        if identifier not in valid_ids:
            reasons.append({"kind": "unknown_evidence_id", "evidence_id": identifier})
            continue
        if identifier not in cited:
            cited.append(identifier)

    integrity = "fail" if reasons else "pass"
    if not request.evidence:
        coverage = "not_applicable"
    elif request.citation_policy.mode == "optional":
        coverage = "pass"
    elif len(cited) >= request.citation_policy.min_unique_evidence_ids:
        coverage = "pass"
    else:
        coverage = "fail"
        reasons.append({
            "kind": "citation_coverage_below_minimum",
            "required_minimum": request.citation_policy.min_unique_evidence_ids,
            "actual_unique_evidence_ids": len(cited),
        })
    return CitationValidation(tuple(cited), integrity, coverage, tuple(reasons))


@dataclass(frozen=True)
class GenerationResult:
    """One valid-config provider occurrence plus structural local validation.

    ``execution_status`` begins only after construction-time configuration
    validation and occurrence creation.  Configuration failures therefore
    fail fast as :class:`GenerationConfigurationError`; they are not a
    Generation occurrence/result and do not have a ``configuration_error``
    result status.
    """

    execution_status: str
    answer_text: str | None
    citation_validation: CitationValidation | None
    semantic_request_identity: str
    execution_config_identity: str
    request_audit: Mapping[str, Any]
    provider_audit: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.execution_status not in {"succeeded", "provider_error", "response_invalid"}:
            raise GenerationContractError("unsupported Generation execution_status")
        if self.execution_status == "succeeded" and (not isinstance(self.answer_text, str) or not self.answer_text):
            raise GenerationContractError("succeeded Generation result requires answer_text")
        if self.execution_status != "succeeded" and self.answer_text is not None:
            raise GenerationContractError("failed Generation result must not contain answer_text")
        if self.execution_status == "succeeded" and self.citation_validation is None:
            raise GenerationContractError("succeeded Generation result requires citation validation")
        _json_safe(dict(self.request_audit), "request audit")
        _json_safe(dict(self.provider_audit), "provider audit")

    def to_dict(self) -> dict[str, Any]:
        validation = self.citation_validation.to_dict() if self.citation_validation is not None else {
            "citation_tokens": [],
            "citation_integrity": "not_applicable",
            "citation_coverage": "not_applicable",
            "validation_reasons": [],
            "semantic_faithfulness": SEMANTIC_FAITHFULNESS_NOT_EVALUATED,
        }
        return {
            "schema_version": GENERATION_RESULT_SCHEMA_VERSION,
            "result": {
                "execution_status": self.execution_status,
                "answer_text": self.answer_text,
                **validation,
            },
            "audit": {
                "semantic_request_identity": self.semantic_request_identity,
                "execution_config_identity": self.execution_config_identity,
                "request": dict(self.request_audit),
                "provider_execution": dict(self.provider_audit),
            },
        }


def generation_result_json_bytes(result: GenerationResult) -> bytes:
    return canonical_json_bytes(result.to_dict())


def write_generation_result(occurrence_output_root: Path, result: GenerationResult) -> dict[str, Any]:
    """Persist one occurrence under a caller-selected root without raw payloads.

    A caller that keeps multiple nondeterministic occurrences must select a
    distinct output root for each one, for example
    ``<run>/occurrences/<occurrence_id>``.  Rewriting a different occurrence
    at the same root fails closed rather than silently replacing audit data.
    """

    path = Path(occurrence_output_root) / "generation_result.json"
    body = generation_result_json_bytes(result)
    if path.exists() and path.read_bytes() != body:
        raise FileExistsError(f"Generation result already exists with different bytes: {path}")
    if not path.exists():
        atomic_write(path, body)
    return {"path": "generation_result.json", "sha256": sha256(body).hexdigest(), "byte_count": len(body)}


@runtime_checkable
class GenerationProvider(Protocol):
    """The minimal provider-neutral operational boundary."""

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Generate one occurrence for a validated provider-neutral request."""


@dataclass(frozen=True)
class BailianControlConfig:
    """Bailian-only operating configuration; credentials are intentionally absent.

    Invalid configuration raises :class:`GenerationConfigurationError` here,
    before a provider invocation or generation occurrence can exist.
    """

    region: str
    endpoint: str
    workspace: str
    model_id: str = BASELINE_QWEN_MODEL_ID
    model_reference_policy: str = EXACT_SNAPSHOT_POLICY
    enable_thinking: bool = False
    temperature: float = 0.0
    max_output_tokens: int = 1024
    timeout_seconds: float = 30.0
    max_attempts: int = 3
    retry_backoff_seconds: float = 1.0
    api_key_env: str = "DASHSCOPE_API_KEY"

    def __post_init__(self) -> None:
        for name in ("region", "workspace", "api_key_env"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise GenerationConfigurationError(f"Bailian {name} must be a non-empty string")
        if not isinstance(self.endpoint, str) or not self.endpoint.startswith("https://"):
            raise GenerationConfigurationError("Bailian endpoint must be an explicit HTTPS URL")
        if self.model_reference_policy != EXACT_SNAPSHOT_POLICY:
            raise GenerationConfigurationError("Bailian baseline requires exact_snapshot_required model policy")
        if not isinstance(self.model_id, str) or not _EXACT_QWEN_SNAPSHOT.fullmatch(self.model_id):
            raise GenerationConfigurationError("Bailian baseline requires an exact dated Qwen model ID, not an alias")
        if self.model_id != BASELINE_QWEN_MODEL_ID:
            raise GenerationConfigurationError("Bailian control baseline model must be qwen3.7-plus-2026-05-26")
        if not isinstance(self.enable_thinking, bool):
            raise GenerationConfigurationError("enable_thinking must be explicit boolean configuration")
        if not isinstance(self.temperature, (int, float)) or isinstance(self.temperature, bool) or not math.isfinite(float(self.temperature)) or self.temperature < 0:
            raise GenerationConfigurationError("temperature must be a finite non-negative number")
        if not isinstance(self.max_output_tokens, int) or isinstance(self.max_output_tokens, bool) or self.max_output_tokens <= 0:
            raise GenerationConfigurationError("max_output_tokens must be a positive integer")
        if not isinstance(self.timeout_seconds, (int, float)) or isinstance(self.timeout_seconds, bool) or not math.isfinite(float(self.timeout_seconds)) or self.timeout_seconds <= 0:
            raise GenerationConfigurationError("timeout_seconds must be a finite positive number")
        if not isinstance(self.max_attempts, int) or isinstance(self.max_attempts, bool) or self.max_attempts <= 0:
            raise GenerationConfigurationError("max_attempts must be a positive integer")
        if not isinstance(self.retry_backoff_seconds, (int, float)) or isinstance(self.retry_backoff_seconds, bool) or not math.isfinite(float(self.retry_backoff_seconds)) or self.retry_backoff_seconds < 0:
            raise GenerationConfigurationError("retry_backoff_seconds must be a finite non-negative number")

    def output_affecting_projection(self) -> dict[str, Any]:
        return {
            "provider_id": GENERATION_PROVIDER_ID_BAILIAN,
            "region": self.region,
            "endpoint": self.endpoint,
            "workspace": self.workspace,
            "model_id": self.model_id,
            "model_reference_policy": self.model_reference_policy,
            "enable_thinking": self.enable_thinking,
            "temperature": float(self.temperature),
            "max_output_tokens": self.max_output_tokens,
        }

    @property
    def execution_config_identity(self) -> str:
        return sha256_json(self.output_affecting_projection())

    def audit_projection(self) -> dict[str, Any]:
        """Safe config audit: omit api_key_env as well as any credential value."""

        return {
            **self.output_affecting_projection(),
            "timeout_seconds": float(self.timeout_seconds),
            "max_attempts": self.max_attempts,
            "retry_backoff_seconds": float(self.retry_backoff_seconds),
        }


@dataclass(frozen=True)
class BailianTransportResponse:
    """Minimal normalized response accepted from an injected transport."""

    answer_text: Any
    provider_request_id: str | None = None
    usage: Mapping[str, Any] | None = None
    finish_reason: str | None = None


class BailianTransportError(Exception):
    """A normalized Bailian transport error, never a raw credential-bearing payload."""

    def __init__(
        self,
        *,
        status_code: int | None,
        code: str,
        message: str,
        provider_request_id: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        if status_code is not None and (
            not isinstance(status_code, int) or isinstance(status_code, bool) or status_code < 100
        ):
            raise GenerationContractError("Bailian error status_code must be null or an HTTP status integer")
        if not isinstance(code, str) or not code:
            raise GenerationContractError("Bailian error code must be a non-empty string")
        if not isinstance(message, str) or not message:
            raise GenerationContractError("Bailian error message must be a non-empty string")
        if retry_after_seconds is not None and (not isinstance(retry_after_seconds, (int, float)) or isinstance(retry_after_seconds, bool) or retry_after_seconds < 0):
            raise GenerationContractError("retry_after_seconds must be null or non-negative")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.provider_request_id = provider_request_id
        self.retry_after_seconds = None if retry_after_seconds is None else float(retry_after_seconds)


class BailianTransport(Protocol):
    """Injected transport only.  A real HTTP transport is explicitly out of G1 scope."""

    def invoke(self, payload: Mapping[str, Any], *, timeout_seconds: float) -> BailianTransportResponse:
        """Return a normalized response or raise BailianTransportError."""


class _RejectRedirectHandler(HTTPRedirectHandler):
    """Do not let urllib issue a second credential-bearing request on redirect."""

    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _bailian_base_url(config: BailianControlConfig) -> str:
    """Validate the only credential-bearing origin accepted by the live transport."""

    try:
        parsed = urlsplit(config.endpoint)
        port = parsed.port
    except ValueError as exc:
        raise GenerationConfigurationError("Bailian endpoint has an invalid port") from exc
    expected_host = f"{config.workspace}{_BAILIAN_BEIJING_HOST_SUFFIX}".lower()
    if config.region != "cn-beijing":
        raise GenerationConfigurationError("live Bailian transport requires region cn-beijing")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", config.workspace.lower()):
        raise GenerationConfigurationError("Bailian workspace must be a safe endpoint host label")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname.lower() != expected_host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or parsed.path not in {_BAILIAN_COMPATIBLE_BASE_PATH, f"{_BAILIAN_COMPATIBLE_BASE_PATH}/"}
    ):
        raise GenerationConfigurationError(
            "Bailian endpoint must be the cn-beijing workspace-dedicated compatible-mode base URL"
        )
    return urlunsplit(("https", expected_host, _BAILIAN_COMPATIBLE_BASE_PATH, "", ""))


def workspace_from_bailian_base_url(endpoint: str) -> str:
    """Extract a non-secret Beijing workspace ID only after strict URL validation."""

    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise GenerationConfigurationError("BAILIAN_BASE_URL has an invalid port") from exc
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname.endswith(_BAILIAN_BEIJING_HOST_SUFFIX)
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or parsed.path not in {_BAILIAN_COMPATIBLE_BASE_PATH, f"{_BAILIAN_COMPATIBLE_BASE_PATH}/"}
    ):
        raise GenerationConfigurationError(
            "BAILIAN_BASE_URL must be the cn-beijing workspace-dedicated compatible-mode base URL"
        )
    workspace = hostname[: -len(_BAILIAN_BEIJING_HOST_SUFFIX)]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", workspace):
        raise GenerationConfigurationError("BAILIAN_BASE_URL has no safe workspace host label")
    return workspace


def _retry_after_seconds(headers: Any) -> float | None:
    value = headers.get("Retry-After") if headers is not None else None
    if not isinstance(value, str):
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _provider_request_id(headers: Any) -> str | None:
    if headers is None:
        return None
    for name in ("x-acs-request-id", "x-request-id"):
        value = headers.get(name)
        if isinstance(value, str) and value:
            return value
    return None


def _error_code_and_message(body: bytes) -> tuple[str, str]:
    """Parse only the documented code; never retain a provider error message."""

    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "UnknownProviderError", "Bailian returned an unreadable error response"
    if not isinstance(value, Mapping):
        return "UnknownProviderError", "Bailian returned an invalid error response"
    code = value.get("code")
    return (
        code if isinstance(code, str) and code else "UnknownProviderError",
        "Bailian returned an error response",
    )


class BailianOpenAICompatibleTransport:
    """One stdlib transport for Bailian's workspace-dedicated Chat Completion API.

    It is provider-specific.  The provider-neutral request/result boundary sees
    only :class:`BailianTransportResponse` and :class:`BailianTransportError`.
    """

    def __init__(self, config: BailianControlConfig, api_key: str, *, opener: Any | None = None) -> None:
        self._base_url = _bailian_base_url(config)
        if not isinstance(api_key, str) or not api_key:
            raise GenerationConfigurationError("DASHSCOPE_API_KEY must be a non-empty environment value")
        self._api_key = api_key
        self._opener = opener or build_opener(_RejectRedirectHandler())

    @classmethod
    def from_environment(
        cls,
        config: BailianControlConfig,
        *,
        environment: Mapping[str, str] | None = None,
        opener: Any | None = None,
    ) -> BailianOpenAICompatibleTransport:
        """Validate origin before reading the credential from the environment."""

        _bailian_base_url(config)
        values = os.environ if environment is None else environment
        api_key = values.get(config.api_key_env)
        return cls(config, api_key if isinstance(api_key, str) else "", opener=opener)

    @property
    def endpoint(self) -> str:
        return f"{self._base_url}/chat/completions"

    @staticmethod
    def wire_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        """Map the G1 adapter payload to the official OpenAI-compatible wire form."""

        model, messages = payload.get("model"), payload.get("messages")
        parameters = payload.get("generation_parameters")
        if not isinstance(model, str) or not isinstance(messages, list) or not isinstance(parameters, Mapping):
            raise GenerationContractError("Bailian adapter payload is invalid for OpenAI-compatible transport")
        enable_thinking = parameters.get("enable_thinking")
        temperature = parameters.get("temperature")
        max_output_tokens = parameters.get("max_output_tokens")
        if not isinstance(enable_thinking, bool):
            raise GenerationContractError("Bailian enable_thinking must be a boolean")
        if not isinstance(temperature, (int, float)) or isinstance(temperature, bool):
            raise GenerationContractError("Bailian temperature must be numeric")
        if not isinstance(max_output_tokens, int) or isinstance(max_output_tokens, bool):
            raise GenerationContractError("Bailian max_output_tokens must be an integer")
        return {
            "model": model,
            "messages": messages,
            "temperature": float(temperature),
            "max_tokens": max_output_tokens,
            "enable_thinking": enable_thinking,
            "stream": False,
        }

    def invoke(self, payload: Mapping[str, Any], *, timeout_seconds: float) -> BailianTransportResponse:
        wire = self.wire_payload(payload)
        body = canonical_json_bytes(wire)
        request = Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                response_body = response.read()
                headers = response.headers
        except HTTPError as error:
            try:
                code, message = _error_code_and_message(error.read())
            finally:
                error.close()
            if 300 <= error.code < 400:
                code, message = "RedirectRejected", "Bailian redirect was rejected before a follow-up request"
            raise BailianTransportError(
                status_code=error.code,
                code=code,
                message=message,
                provider_request_id=_provider_request_id(error.headers),
                retry_after_seconds=_retry_after_seconds(error.headers),
            ) from None
        except URLError as error:
            raise BailianTransportError(
                status_code=None,
                code="TransportConnectionError",
                message="Bailian transport connection failed",
            ) from None
        try:
            parsed = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return BailianTransportResponse(
                answer_text=None,
                provider_request_id=_provider_request_id(headers),
            )
        if not isinstance(parsed, Mapping):
            return BailianTransportResponse(
                answer_text=None,
                provider_request_id=_provider_request_id(headers),
            )
        choices = parsed.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else None
        message = choice.get("message") if isinstance(choice, Mapping) else None
        answer = message.get("content") if isinstance(message, Mapping) else None
        usage = parsed.get("usage")
        finish_reason = choice.get("finish_reason") if isinstance(choice, Mapping) else None
        response_id = parsed.get("id")
        return BailianTransportResponse(
            answer_text=answer,
            provider_request_id=response_id if isinstance(response_id, str) and response_id else _provider_request_id(headers),
            usage=usage if isinstance(usage, Mapping) else None,
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class BailianGenerationProvider:
    """Map the neutral request to a Qwen control invocation through injected transport."""

    def __init__(
        self,
        config: BailianControlConfig,
        transport: BailianTransport,
        *,
        occurrence_factory: Callable[[], str] | None = None,
        clock: Callable[[], str] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._occurrence_factory = occurrence_factory or (lambda: str(uuid4()))
        self._clock = clock or _utc_now
        # Tests may inject a no-op sleeper.  The production default must honor
        # configured retry delays rather than turning them into no-ops.
        self._sleeper = sleeper or time.sleep

    @staticmethod
    def _messages(request: GenerationRequest) -> list[dict[str, str]]:
        evidence_parts: list[str] = []
        for evidence in request.evidence:
            context = f"\n上下文：{'；'.join(evidence.display_context)}" if evidence.display_context else ""
            evidence_parts.append(f"[{evidence.evidence_id}]{context}\n{evidence.text}")
        user_content = f"问题：\n{request.question}\n\n证据：\n" + ("\n\n".join(evidence_parts) if evidence_parts else "（当前 Evidence Packet 没有可见证据。）")
        return [
            {"role": "system", "content": request.instruction.text},
            {"role": "user", "content": user_content},
        ]

    def invocation_payload(self, request: GenerationRequest) -> dict[str, Any]:
        """A testable adapter mapping, not a live HTTP request implementation."""

        return {
            "model": self._config.model_id,
            "messages": self._messages(request),
            "generation_parameters": {
                "enable_thinking": self._config.enable_thinking,
                "temperature": float(self._config.temperature),
                "max_output_tokens": self._config.max_output_tokens,
            },
            "workspace": self._config.workspace,
        }

    @staticmethod
    def _error_category(error: BailianTransportError) -> tuple[str, bool]:
        """Retry only documented transient Bailian code/status pairs."""

        pair = (error.status_code, error.code)
        if pair not in _BAILIAN_RETRYABLE_ERROR_PAIRS:
            return "provider_error", False
        if error.status_code == 429:
            return "throttled", True
        if pair == (503, "ModelUnavailable"):
            return "model_unavailable", True
        return "model_service_error", True

    def _provider_audit_base(self, occurrence_id: str, occurred_at: str) -> dict[str, Any]:
        return {
            "provider_id": GENERATION_PROVIDER_ID_BAILIAN,
            "control_config": self._config.audit_projection(),
            "generation_occurrence": {"occurrence_id": occurrence_id, "occurred_at": occurred_at},
            "attempts": [],
        }

    def _failure_result(
        self,
        *,
        status: str,
        request: GenerationRequest,
        audit: Mapping[str, Any],
    ) -> GenerationResult:
        return GenerationResult(
            execution_status=status,
            answer_text=None,
            citation_validation=None,
            semantic_request_identity=request.semantic_request_identity,
            execution_config_identity=self._config.execution_config_identity,
            request_audit=request.audit_projection(),
            provider_audit=audit,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if not isinstance(request, GenerationRequest):
            raise GenerationContractError("Bailian provider requires a GenerationRequest")
        occurrence_id, occurred_at = self._occurrence_factory(), self._clock()
        if not isinstance(occurrence_id, str) or not occurrence_id:
            raise GenerationContractError("generation occurrence ID must be a non-empty string")
        if not isinstance(occurred_at, str) or not occurred_at:
            raise GenerationContractError("generation occurrence timestamp must be a non-empty string")
        audit = self._provider_audit_base(occurrence_id, occurred_at)
        payload = self.invocation_payload(request)
        for attempt in range(1, self._config.max_attempts + 1):
            try:
                response = self._transport.invoke(payload, timeout_seconds=float(self._config.timeout_seconds))
            except TimeoutError:
                # This is a local execution timeout rather than a provider
                # HTTP code.  It is safe to make one bounded retry attempt
                # because the adapter has no idempotency claim and records
                # every occurrence/attempt for later interpretation.
                retry = attempt < self._config.max_attempts
                attempt_audit = {
                    "attempt": attempt,
                    "status_code": None,
                    "provider_code": None,
                    "provider_request_id": None,
                    "error_category": "timeout",
                    "retryable": True,
                    "retry_decision": "retry" if retry else "stop",
                }
                if retry:
                    delay = float(self._config.retry_backoff_seconds) * attempt
                    attempt_audit["retry_delay_seconds"] = delay
                    audit["attempts"].append(attempt_audit)
                    self._sleeper(delay)
                    continue
                audit["attempts"].append(attempt_audit)
                return self._failure_result(status="provider_error", request=request, audit=audit)
            except BailianTransportError as error:
                category, retryable = self._error_category(error)
                retry = retryable and attempt < self._config.max_attempts
                attempt_audit: dict[str, Any] = {
                    "attempt": attempt,
                    "status_code": error.status_code,
                    "provider_code": error.code,
                    "provider_request_id": error.provider_request_id,
                    "error_category": category,
                    "retryable": retryable,
                    "retry_decision": "retry" if retry else "stop",
                }
                if retry:
                    delay = error.retry_after_seconds if error.retry_after_seconds is not None else float(self._config.retry_backoff_seconds) * attempt
                    attempt_audit["retry_delay_seconds"] = delay
                    audit["attempts"].append(attempt_audit)
                    self._sleeper(delay)
                    continue
                audit["attempts"].append(attempt_audit)
                return self._failure_result(status="provider_error", request=request, audit=audit)
            if not isinstance(response, BailianTransportResponse) or not isinstance(response.answer_text, str) or not response.answer_text:
                audit["attempts"].append({
                    "attempt": attempt,
                    "status_code": None,
                    "provider_code": None,
                    "provider_request_id": None,
                    "error_category": "malformed_provider_response",
                    "retryable": False,
                    "retry_decision": "stop",
                })
                return self._failure_result(status="response_invalid", request=request, audit=audit)
            usage = dict(response.usage) if isinstance(response.usage, Mapping) else None
            if usage is not None:
                _json_safe(usage, "Bailian response usage")
            audit["attempts"].append({
                "attempt": attempt,
                "status_code": 200,
                "provider_code": None,
                "provider_request_id": response.provider_request_id,
                "error_category": None,
                "retryable": False,
                "retry_decision": "completed",
                "finish_reason": response.finish_reason,
                "usage": usage,
            })
            audit["response_sha256"] = sha256(canonical_json_bytes({"answer_text": response.answer_text})).hexdigest()
            validation = validate_citations(response.answer_text, request)
            return GenerationResult(
                execution_status="succeeded",
                answer_text=response.answer_text,
                citation_validation=validation,
                semantic_request_identity=request.semantic_request_identity,
                execution_config_identity=self._config.execution_config_identity,
                request_audit=request.audit_projection(),
                provider_audit=audit,
            )
        raise AssertionError("bounded Bailian retry loop must return")


DEFAULT_G2_SMOKE_PACKET = Path(
    "data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/"
    "packets/q01/hybrid/evidence_packet.json"
)


def run_bailian_control_smoke(
    *,
    packet_path: Path = DEFAULT_G2_SMOKE_PACKET,
    output_root: Path,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run exactly one locally authorized Qwen control occurrence.

    The caller owns environment variables and authorization.  This helper
    deliberately fixes ``max_attempts=1`` so one invocation can issue no more
    than one provider request.  It never prints a secret, request body, or
    provider error message.
    """

    packet_path = Path(packet_path)
    output_root = Path(output_root)
    if (output_root / "generation_result.json").exists():
        raise FileExistsError("smoke output already contains generation_result.json; refusing to send a new request")
    values = os.environ if environment is None else environment
    endpoint = values.get("BAILIAN_BASE_URL")
    if not isinstance(endpoint, str) or not endpoint:
        raise GenerationConfigurationError("BAILIAN_BASE_URL must be set in the local environment")
    workspace = workspace_from_bailian_base_url(endpoint)
    try:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenerationContractError("smoke Evidence Packet is unreadable") from exc
    if not isinstance(packet, Mapping):
        raise GenerationContractError("smoke Evidence Packet must be an object")
    retrieval_audit = packet.get("retrieval_audit")
    retrieval_metadata = retrieval_audit.get("retrieval_metadata") if isinstance(retrieval_audit, Mapping) else None
    question = retrieval_metadata.get("query_text") if isinstance(retrieval_metadata, Mapping) else None
    question_id = retrieval_metadata.get("query_id") if isinstance(retrieval_metadata, Mapping) else None
    if not isinstance(question, str) or not question.strip():
        raise GenerationContractError("smoke Evidence Packet lacks retrieval_audit.retrieval_metadata.query_text")
    if question_id is not None and not isinstance(question_id, str):
        raise GenerationContractError("smoke Evidence Packet query_id must be text when present")
    request = project_generation_request(packet, question=question, question_id=question_id)
    config = BailianControlConfig(
        region="cn-beijing",
        endpoint=endpoint,
        workspace=workspace,
        model_id=BASELINE_QWEN_MODEL_ID,
        enable_thinking=False,
        max_attempts=1,
    )
    transport = BailianOpenAICompatibleTransport.from_environment(config, environment=environment)
    result = BailianGenerationProvider(config, transport).generate(request)
    persisted = write_generation_result(output_root, result)
    return {
        "execution_status": result.execution_status,
        "semantic_request_identity": result.semantic_request_identity,
        "execution_config_identity": result.execution_config_identity,
        "computed_packet_sha256": request.evidence_packet_sha256,
        "result_artifact": persisted,
        "semantic_faithfulness": SEMANTIC_FAITHFULNESS_NOT_EVALUATED,
    }
