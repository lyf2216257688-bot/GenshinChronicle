"""Fail-closed execution seam for the frozen P05-W2 semantic experiment.

The runner owns experiment identity, attempt accounting, crash-safe evidence
ordering, and the provider-neutral output contract.  Provider HTTP dialects
are deliberately supplied by an explicit adapter factory; this module does not
guess whether two nominally identical models share an endpoint or request
format.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import importlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping, Protocol, Sequence

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write

from .semantic_compiler_u1 import (
    SEMANTIC_ITEM_KINDS,
    SEMANTIC_OUTPUT_SCHEMA_IDENTITY,
    SEMANTIC_OUTPUT_SCHEMA_VERSION,
    SEGMENT_COVERAGE_DISPOSITIONS,
    SemanticCompilerU1Error,
    semantic_output_schema,
    semantic_input_identity,
    validate_semantic_output_schema,
    validate_semantic_source_binding,
)


LIVE_RUNNER_SCHEMA_VERSION = "phase05-w2-semantic-live-runner-0.1"
ACCEPTED_PREFLIGHT_IDENTITY = "8601fb2e665fd5f1bc1b7a9ef35a871269e3ea00f0b9a23654656a0bb3838da0"
FROZEN_SEMANTIC_BUILD_IDENTITY = "45520b23490a1c66289476c71dbe55c38cd206167342ebeb5894721055c59677"
B_EXPERIMENT_REVISION = "phase05-w2-b-json-object-0.1"
B_PROMPT_VERSION = "phase05-w2-b-json-object-prompt-0.1"
B_V2_EXPERIMENT_REVISION = "phase05-w2-b-json-object-0.2"
B_V2_PROMPT_VERSION = "phase05-w2-b-json-object-prompt-0.2"
B_REQUEST_CONTRACT_VERSION = "phase05-w2-b-json-object-request-0.1"
GEMINI_A_USER_AGENT = "GenshinChronicle-Phase05-SemanticCanary/0.1"
TRANSPORT_RESPONSE_HEADER_ALLOWLIST = frozenset({"content-type", "server", "cf-ray", "x-request-id"})
CHANNELS = ("gemini_a", "gemini_b", "deepseek", "glm")
CHANNEL_LIMITS = {"gemini_a": 30, "gemini_b": 30, "deepseek": 18, "glm": 0}
CHANNEL_PROVIDERS = {"gemini_a": "Gemini", "gemini_b": "Gemini", "deepseek": "JizhiAPI", "glm": "TokenMetro"}
CHANNEL_ENV_PREFIX = {
    "gemini_a": "GENSHIN_P05_GEMINI_A",
    "gemini_b": "GENSHIN_P05_GEMINI_B",
    "deepseek": "GENSHIN_P05_DEEPSEEK",
    "glm": "GENSHIN_P05_GLM",
}
OPENAI_CHAT_ADAPTER_FACTORY = "genshin_corpus.retrieval.semantic_openai_chat_adapter:create_adapter"
CHANNEL_TRANSPORT_PROFILES: Mapping[str, Mapping[str, str | None]] = {
    "gemini_a": {
        "endpoint": "https://jizhiapi.site/v1",
        "transport_model": "gemini-3.8-flash",
        "auth_mode": "bearer",
        "reasoning_effort": "medium",
        "user_agent": GEMINI_A_USER_AGENT,
    },
    "gemini_b": {
        "endpoint": "https://tokenmetro.com/v1",
        "transport_model": "gemini-3.8-flash",
        "auth_mode": "bearer",
        "reasoning_effort": "medium",
        "user_agent": None,
    },
    "deepseek": {
        "endpoint": "https://jizhiapi.site/v1",
        "transport_model": "deepseek-v4.1-flash",
        "auth_mode": "bearer",
        "reasoning_effort": None,
        "user_agent": GEMINI_A_USER_AGENT,
    },
    "glm": {
        "endpoint": "https://tokenmetro.com/v1",
        "transport_model": "glm-5.3-flash",
        "auth_mode": "bearer",
        "reasoning_effort": None,
        "user_agent": None,
    },
}
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]+$")
_SAFE_ENV = re.compile(r"^[A-Z][A-Z0-9_]*$")


def b_prompt_contract() -> dict[str, Any]:
    """Return the compact, provider-neutral B prompt contract."""

    return {
        "version": B_PROMPT_VERSION,
        "instruction": (
            "Return exactly one raw JSON object and nothing else: no prose and no Markdown fence. "
            "Use exactly the top-level keys schema_version, items, and segment_coverage. "
            f"schema_version must be exactly {SEMANTIC_OUTPUT_SCHEMA_VERSION}; never copy the input schema_version."
        ),
        "item_contract": {
            "allowed_fields": [
                "local_id", "kind", "label", "source_segment_ids", "topic_path", "subject_ref",
                "object_ref", "predicate", "event_type", "participants", "qualifiers",
            ],
            "required_fields": ["local_id", "kind", "label", "source_segment_ids", "topic_path", "qualifiers"],
            "kind_enum": sorted(SEMANTIC_ITEM_KINDS),
            "rules": [
                "local_id and label are non-empty strings",
                "source_segment_ids is a non-empty unique array containing only supplied segment_id values",
                "topic_path is a unique array of non-empty strings",
                "subject_ref, object_ref, predicate, and event_type are string or null when present; participants is a unique array of non-empty strings when present",
                "qualifiers is an object",
                "do not add fields",
            ],
        },
        "coverage_contract": {
            "exact_fields": ["segment_id", "disposition", "reason"],
            "disposition_enum": sorted(SEGMENT_COVERAGE_DISPOSITIONS),
            "rules": [
                "include every supplied segment_id exactly once and no other segment_id",
                "items may reference only segments whose disposition is covered",
                "reason must be a non-empty string when disposition is not covered; for covered it may be null",
                "do not add fields",
            ],
        },
        "empty_output": "Use items=[] and still provide complete segment_coverage.",
        "minimal_example": {
            "condition": "Only when the sole supplied segment_id is s1",
            "output": {
                "schema_version": SEMANTIC_OUTPUT_SCHEMA_VERSION,
                "items": [{
                    "local_id": "i1", "kind": "event", "label": "A meets B",
                    "source_segment_ids": ["s1"], "topic_path": [], "qualifiers": {},
                }],
                "segment_coverage": [{"segment_id": "s1", "disposition": "covered", "reason": None}],
            },
            "warning": "Never copy s1 unless s1 is actually supplied; use the exact supplied segment IDs.",
        },
        "acceptance_boundary": "Local strict JSON, schema, and source-binding validation is authoritative; semantic correctness is separate.",
    }


def b_v2_prompt_contract() -> dict[str, Any]:
    """Define the shared, source-bound navigation extraction task for comparison."""
    prompt = b_prompt_contract()
    prompt["version"] = B_V2_PROMPT_VERSION
    prompt["task"] = (
        "Extract a small set of source-supported navigation records for a topic hierarchy "
        "and sparse event/relation graph. Preserve material event turns, explicit relationships, "
        "participants, attribution, uncertainty, and negation. Do not replace them with a "
        "paragraph-level plot summary or try to answer a question."
    )
    prompt["extraction_rules"] = [
        "Separate consequential changes in goal, possession, apparent outcome, reversal, and state into distinct event items when the source states them; retain their order only when explicit.",
        "Extract explicit relationships as separate relation items with a source-supported predicate; preserve a speaker's hypothesis as attributed and uncertain, not as an unconditional fact.",
        "Use mention items for locally identified participants or relation endpoints. Each item's local_id is unique only within this one response; an equal name in another unit is not a shared entity identity.",
        "For relation items, subject_ref and object_ref must point to local_id values of mention or event items in this response. For event items, participants must point to local_id values of mention items in this response. Never put bare names in reference fields.",
        "Use qualifiers.attribution for a known speaker, narrator, or document voice; qualifiers.modality for reported, tentative, or hypothetical claims; and qualifiers.polarity for explicit negation. Omit a qualifier when unsupported. Do not merge voices across a change of handwriting or speaker.",
        "Do not infer causality, inverse or transitive relations, semantic equivalence, or a generic related_to link. Do not invent identities, participants, events, or precise spans.",
        "Use topic_path only for source-supported navigation grouping. A shared label or path does not establish global identity.",
        "For each item, use the smallest sufficient set of supplied source_segment_ids. Add a second segment only when the assertion actually needs both; duplicated structural and text projections are not independent evidence.",
        "If a segment contains material navigation information, extract its distinct supported items even when another item already cites that segment. segment_coverage is input accounting, not a claim that all important meaning was extracted.",
        "When material is ambiguous or unsupported, report that segment disposition with a reason instead of guessing; use no_navigation_material only when the supplied segment truly has no useful navigation content.",
    ]
    prompt["item_contract"]["rules"] = [
        *prompt["item_contract"]["rules"],
        "relation subject_ref and object_ref resolve to local mention/event local_id values; event participants resolve to local mention local_id values",
        "qualifiers preserve source attribution, modality, and explicit negation without turning reported speech into objective fact",
    ]
    prompt["coverage_contract"]["rules"].append(
        "covered accounts for a processed segment; it does not assert semantic completeness or importance"
    )
    prompt["minimal_example"] = {
        "condition": "Only when the sole supplied segment_id is s1 and it explicitly says A met B",
        "output": {
            "schema_version": SEMANTIC_OUTPUT_SCHEMA_VERSION,
            "items": [
                {"local_id": "m1", "kind": "mention", "label": "A", "source_segment_ids": ["s1"], "topic_path": [], "qualifiers": {}},
                {"local_id": "m2", "kind": "mention", "label": "B", "source_segment_ids": ["s1"], "topic_path": [], "qualifiers": {}},
                {"local_id": "e1", "kind": "event", "label": "A met B", "source_segment_ids": ["s1"], "topic_path": [], "event_type": "meeting", "participants": ["m1", "m2"], "qualifiers": {}},
            ],
            "segment_coverage": [{"segment_id": "s1", "disposition": "covered", "reason": None}],
        },
        "warning": "s1, A, and B are placeholders; use only identifiers and claims present in the supplied input.",
    }
    return prompt


def b_request_contract() -> dict[str, Any]:
    """Return the provider wire contract that distinguishes B from frozen A."""

    return {
        "version": B_REQUEST_CONTRACT_VERSION,
        "endpoint_family": "openai_compatible_chat_completions",
        "structured_output_mode": "JSON_OBJECT",
        "response_format": {"type": "json_object"},
        "stream": False,
        "generation_parameters": {"temperature": 0.0},
        "strict_json_schema_sent": False,
    }


@dataclass(frozen=True)
class SemanticExperimentContract:
    revision: str
    prompt_contract: Mapping[str, Any]
    request_contract: Mapping[str, Any]
    output_schema_identity: str
    frozen_preflight_identity: str
    frozen_semantic_build_identity: str
    acceptance_authority: str

    @property
    def prompt_identity(self) -> str:
        return sha256_json(self.prompt_contract)

    @property
    def request_contract_identity(self) -> str:
        return sha256_json(self.request_contract)

    def safe_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "prompt_identity": self.prompt_identity,
            "request_contract_identity": self.request_contract_identity,
            "output_schema_identity": self.output_schema_identity,
            "frozen_preflight_identity": self.frozen_preflight_identity,
            "frozen_semantic_build_identity": self.frozen_semantic_build_identity,
            "acceptance_authority": self.acceptance_authority,
        }

    @property
    def identity(self) -> str:
        return sha256_json(self.safe_dict())


def b_experiment_contract() -> SemanticExperimentContract:
    return SemanticExperimentContract(
        revision=B_EXPERIMENT_REVISION,
        prompt_contract=b_prompt_contract(),
        request_contract=b_request_contract(),
        output_schema_identity=SEMANTIC_OUTPUT_SCHEMA_IDENTITY,
        frozen_preflight_identity=ACCEPTED_PREFLIGHT_IDENTITY,
        frozen_semantic_build_identity=FROZEN_SEMANTIC_BUILD_IDENTITY,
        acceptance_authority="local_strict_json_schema_and_source_binding",
    )


def b_v2_experiment_contract() -> SemanticExperimentContract:
    return SemanticExperimentContract(
        revision=B_V2_EXPERIMENT_REVISION,
        prompt_contract=b_v2_prompt_contract(),
        request_contract=b_request_contract(),
        output_schema_identity=SEMANTIC_OUTPUT_SCHEMA_IDENTITY,
        frozen_preflight_identity=ACCEPTED_PREFLIGHT_IDENTITY,
        frozen_semantic_build_identity=FROZEN_SEMANTIC_BUILD_IDENTITY,
        acceptance_authority="local_strict_json_schema_and_source_binding",
    )


class SemanticLiveRunnerError(ValueError):
    """Raised when a live execution input or persisted state is unsafe."""


def validate_b_v2_navigation_references(value: Mapping[str, Any]) -> None:
    """Check the v2 local-reference convention after authoritative schema validation."""
    items = value.get("items")
    if not isinstance(items, list):
        raise SemanticLiveRunnerError("B v2 items must be a list")
    by_id = {item["local_id"]: item for item in items}
    if len(by_id) != len(items):
        raise SemanticLiveRunnerError("B v2 local_id values must be unique")
    for item in items:
        if item["kind"] == "relation":
            if not item.get("predicate") or str(item["predicate"]).lower() == "related_to":
                raise SemanticLiveRunnerError("B v2 relation requires an explicit predicate")
            for field in ("subject_ref", "object_ref"):
                target = by_id.get(item.get(field))
                if target is None or target["kind"] not in {"mention", "event"}:
                    raise SemanticLiveRunnerError(f"B v2 relation {field} must resolve locally")
        elif item["kind"] == "event":
            for ref in item.get("participants", []):
                target = by_id.get(ref)
                if target is None or target["kind"] != "mention":
                    raise SemanticLiveRunnerError("B v2 event participant must resolve to a local mention")


class SemanticProviderTransportError(Exception):
    """Normalized one-attempt transport failure, optionally with raw evidence."""

    def __init__(self, code: str, *, raw_response_bytes: bytes | None = None, transport_status: int | None = None, provider_request_id: str | None = None, usage: Mapping[str, Any] | None = None, charge: Any = None, billable: bool | None = None, response_headers: Mapping[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.raw_response_bytes = raw_response_bytes
        self.transport_status = transport_status
        self.provider_request_id = provider_request_id
        self.usage = usage
        self.charge = charge
        self.billable = billable
        self.response_headers = response_headers


@dataclass(frozen=True)
class ChannelConfig:
    channel: str
    provider: str
    endpoint: str
    adapter_factory: str
    auth_mode: str
    api_key_env: str
    model_alias: str
    transport_model: str
    reasoning_or_thinking: str
    structured_output_mode: str
    generation_parameters: Mapping[str, Any]
    max_output_tokens: int
    timeout_seconds: float
    user_agent: str | None = None

    def __post_init__(self) -> None:
        if self.channel not in CHANNELS:
            raise SemanticLiveRunnerError(f"unsupported semantic channel: {self.channel}")
        if self.provider != CHANNEL_PROVIDERS[self.channel]:
            raise SemanticLiveRunnerError("channel/provider mismatch")
        if not isinstance(self.endpoint, str) or not self.endpoint:
            raise SemanticLiveRunnerError("provider endpoint is required")
        try:
            from urllib.parse import urlsplit

            parsed = urlsplit(self.endpoint)
        except ValueError as exc:
            raise SemanticLiveRunnerError("provider endpoint is invalid") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise SemanticLiveRunnerError("endpoint must be an explicit URL without credentials, query, or fragment")
        if not isinstance(self.adapter_factory, str) or ":" not in self.adapter_factory:
            raise SemanticLiveRunnerError("adapter_factory must be an explicit module:function reference")
        if not isinstance(self.auth_mode, str) or not _SAFE_ID.fullmatch(self.auth_mode):
            raise SemanticLiveRunnerError("auth_mode must be an explicit safe identifier")
        if not isinstance(self.api_key_env, str) or not _SAFE_ENV.fullmatch(self.api_key_env):
            raise SemanticLiveRunnerError("api_key_env must be an environment variable name")
        if not isinstance(self.model_alias, str) or not self.model_alias:
            raise SemanticLiveRunnerError("model alias is required")
        if not isinstance(self.transport_model, str) or not _SAFE_ID.fullmatch(self.transport_model):
            raise SemanticLiveRunnerError("transport model must be an explicit safe identifier")
        if not isinstance(self.reasoning_or_thinking, str) or not self.reasoning_or_thinking:
            raise SemanticLiveRunnerError("reasoning/thinking setting is required")
        if self.structured_output_mode not in {"JSON_SCHEMA", "JSON_OBJECT"}:
            raise SemanticLiveRunnerError("structured_output_mode must be JSON_SCHEMA or JSON_OBJECT")
        if not isinstance(self.generation_parameters, Mapping):
            raise SemanticLiveRunnerError("generation_parameters must be an object")
        if not isinstance(self.max_output_tokens, int) or self.max_output_tokens <= 0:
            raise SemanticLiveRunnerError("max_output_tokens must be positive")
        if not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
            raise SemanticLiveRunnerError("timeout_seconds must be positive")
        if self.user_agent is not None and (
            not isinstance(self.user_agent, str)
            or not self.user_agent
            or len(self.user_agent) > 256
            or "\r" in self.user_agent
            or "\n" in self.user_agent
        ):
            raise SemanticLiveRunnerError("user_agent must be a bounded single-line string or null")

    @property
    def maximum_attempts(self) -> int:
        return CHANNEL_LIMITS[self.channel]

    @property
    def config_identity(self) -> str:
        return sha256_json(self.safe_dict())

    def safe_dict(self) -> dict[str, Any]:
        """Return persistable configuration without credentials."""

        result = {
            "channel": self.channel,
            "provider": self.provider,
            "endpoint": self.endpoint,
            "adapter_factory": self.adapter_factory,
            "auth_mode": self.auth_mode,
            "api_key_env": self.api_key_env,
            "model_alias": self.model_alias,
            "transport_model": self.transport_model,
            "reasoning_or_thinking": self.reasoning_or_thinking,
            "structured_output_mode": self.structured_output_mode,
            "generation_parameters": dict(self.generation_parameters),
            "max_output_tokens": self.max_output_tokens,
            "timeout_seconds": self.timeout_seconds,
            "automatic_retry": False,
            "maximum_attempts": self.maximum_attempts,
        }
        if self.user_agent is not None:
            result["user_agent"] = self.user_agent
        return result

    @classmethod
    def from_environment(cls, channel: str, environment: Mapping[str, str] | None = None) -> "ChannelConfig":
        values = os.environ if environment is None else environment
        if channel not in CHANNELS:
            raise SemanticLiveRunnerError(f"unsupported semantic channel: {channel}")
        prefix = CHANNEL_ENV_PREFIX[channel]
        transport = CHANNEL_TRANSPORT_PROFILES[channel]
        expected = "primary" if channel.startswith("gemini") else "challenger"
        defaults = {
            "primary": {"provider": "Gemini", "model": "Gemini 3.8 Flash", "reasoning": "Medium"},
            "challenger": {"provider": "JizhiAPI", "model": "DeepSeek V4.1 Flash", "reasoning": "no separate control configured"},
        }[expected]
        if channel == "glm":
            defaults = {"provider": "TokenMetro", "model": "GLM 5.3 Flash", "reasoning": "no separate control configured"}

        def optional(name: str, default: str) -> str:
            value = values.get(f"{prefix}_{name}", default)
            return value if isinstance(value, str) and value else default

        def fixed(name: str, expected_value: str) -> str:
            configured = values.get(f"{prefix}_{name}")
            if configured is not None and configured != expected_value:
                raise SemanticLiveRunnerError(f"{prefix}_{name} differs from the authorized channel transport profile")
            return expected_value

        def fixed_optional(name: str, expected_value: str | None) -> str | None:
            configured = values.get(f"{prefix}_{name}")
            if configured is not None and configured != expected_value:
                raise SemanticLiveRunnerError(f"{prefix}_{name} differs from the authorized channel transport profile")
            return expected_value

        generation = {"temperature": float(values.get(f"{prefix}_TEMPERATURE", "0.0"))}
        return cls(
            channel=channel,
            provider=defaults["provider"],
            endpoint=fixed("ENDPOINT", str(transport["endpoint"])),
            adapter_factory=fixed("ADAPTER_FACTORY", OPENAI_CHAT_ADAPTER_FACTORY),
            auth_mode=fixed("AUTH_MODE", str(transport["auth_mode"])),
            api_key_env=(fixed("API_KEY_ENV", "TOKENMETRO_API_KEY") if channel == "glm" else optional("API_KEY_ENV", f"{prefix}_API_KEY")),
            model_alias=optional("MODEL", defaults["model"]),
            transport_model=fixed("TRANSPORT_MODEL", str(transport["transport_model"])),
            reasoning_or_thinking=optional("REASONING", defaults["reasoning"]),
            structured_output_mode=optional("STRUCTURED_OUTPUT", "JSON_SCHEMA"),
            generation_parameters=generation,
            max_output_tokens=int(values.get(f"{prefix}_MAX_OUTPUT_TOKENS", "8192")),
            timeout_seconds=float(values.get(f"{prefix}_TIMEOUT_SECONDS", "120")),
            user_agent=fixed_optional("USER_AGENT", transport["user_agent"]),
        )

    @classmethod
    def for_b_json_object(cls, channel: str, environment: Mapping[str, str] | None = None) -> "ChannelConfig":
        """Load the explicit B configuration without changing frozen-A defaults."""

        values = dict(os.environ if environment is None else environment)
        if channel not in CHANNELS:
            raise SemanticLiveRunnerError(f"unsupported semantic channel: {channel}")
        key = f"{CHANNEL_ENV_PREFIX[channel]}_STRUCTURED_OUTPUT"
        configured = values.get(key)
        if configured is not None and configured != "JSON_OBJECT":
            raise SemanticLiveRunnerError(f"{key} must be JSON_OBJECT for experiment B")
        values[key] = "JSON_OBJECT"
        return cls.from_environment(channel, values)


@dataclass(frozen=True)
class SemanticProviderRequest:
    channel: str
    compilation_unit_id: str
    semantic_input_identity: str
    payload: Mapping[str, Any]
    prompt_contract: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    config: ChannelConfig
    request_ordinal: int

    @property
    def payload_bytes(self) -> bytes:
        return canonical_json_bytes(self.payload)


@dataclass(frozen=True)
class SemanticProviderResponse:
    raw_response_bytes: bytes
    transport_status: int | None = None
    provider_request_id: str | None = None
    usage: Mapping[str, Any] | None = None
    charge: Any = None
    billable: bool | None = None
    finish_reason: str | None = None
    response_headers: Mapping[str, Any] | None = None


class SemanticProviderAdapter(Protocol):
    def build_request_body(self, request: SemanticProviderRequest) -> bytes:
        """Serialize one provider wire request using the channel's dialect."""

    def invoke(self, request: SemanticProviderRequest) -> SemanticProviderResponse:
        """Perform exactly one request, with no retry or fallback."""

    def extract_content(self, raw_response_bytes: bytes) -> str:
        """Extract assistant content from one provider response envelope."""

    def parse_content(self, content: str) -> Mapping[str, Any]:
        """Strictly parse the whole assistant content as one JSON object."""

    def parse_response(self, raw_response_bytes: bytes) -> Mapping[str, Any]:
        """Compatibility composition of extraction and strict parsing."""


def load_adapter(factory_reference: str, config: ChannelConfig, environment: Mapping[str, str] | None = None,
                 *, allow_legacy_live: bool = False) -> SemanticProviderAdapter:
    """Load the configured dialect adapter without persisting secrets."""

    module_name, function_name = factory_reference.split(":", 1)
    if not module_name or not function_name:
        raise SemanticLiveRunnerError("adapter factory reference is invalid")
    try:
        factory = getattr(importlib.import_module(module_name), function_name)
    except (ImportError, AttributeError) as exc:
        raise SemanticLiveRunnerError("cannot load configured provider adapter factory") from exc
    if not callable(factory):
        raise SemanticLiveRunnerError("configured adapter factory is not callable")
    if allow_legacy_live is True and factory_reference != OPENAI_CHAT_ADAPTER_FACTORY:
        raise SemanticLiveRunnerError("legacy direct-HTTP opt-in requires the legacy adapter factory")
    if factory_reference == OPENAI_CHAT_ADAPTER_FACTORY:
        adapter = factory(config, environment if environment is not None else os.environ,
                          allow_legacy_live=allow_legacy_live is True)
    else:
        adapter = factory(config, environment if environment is not None else os.environ)
    required = ("build_request_body", "invoke", "extract_content", "parse_content", "parse_response")
    if any(not callable(getattr(adapter, name, None)) for name in required):
        raise SemanticLiveRunnerError("provider adapter does not implement the semantic transport protocol")
    return adapter


def load_offline_adapter(factory_reference: str, config: ChannelConfig) -> SemanticProviderAdapter:
    """Load a parsing/request adapter that cannot invoke a provider."""

    module_name, _ = factory_reference.split(":", 1)
    try:
        factory = getattr(importlib.import_module(module_name), "create_offline_adapter")
    except (ImportError, AttributeError) as exc:
        raise SemanticLiveRunnerError("configured provider adapter has no offline factory") from exc
    adapter = factory(config)
    required = ("build_request_body", "invoke", "extract_content", "parse_content", "parse_response")
    if any(not callable(getattr(adapter, name, None)) for name in required):
        raise SemanticLiveRunnerError("offline adapter does not implement the semantic transport protocol")
    return adapter


def _sha(body: bytes) -> str:
    import hashlib

    return hashlib.sha256(body).hexdigest()


def _write_bytes(path: Path, body: bytes) -> dict[str, Any]:
    if path.exists():
        if path.read_bytes() != body:
            raise SemanticLiveRunnerError(f"immutable artifact differs: {path}")
    else:
        atomic_write(path, body)
    return {"path": str(path), "sha256": _sha(body), "byte_count": len(body)}


def _write_json(path: Path, value: Any) -> dict[str, Any]:
    return _write_bytes(path, canonical_json_bytes(value))


def _relative_artifact(descriptor: Mapping[str, Any], root: Path) -> dict[str, Any]:
    result = dict(descriptor)
    result["path"] = str(Path(str(descriptor["path"])).relative_to(root))
    return result


def _write_ledger(root: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    body = b"".join(canonical_json_bytes(dict(row)) + b"\n" for row in rows)
    # The ledger is the one intentionally append/resolve artifact: issuance
    # is persisted before invocation, then the same row is resolved after the
    # response.  atomic_write keeps each replacement crash-safe.
    atomic_write(root / "metadata" / "request_ledger.jsonl", body)


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SemanticLiveRunnerError(f"cannot read JSON artifact: {path}") from exc
    if not isinstance(value, Mapping):
        raise SemanticLiveRunnerError(f"expected JSON object: {path}")
    return value


def _read_ledger(root: Path) -> list[dict[str, Any]]:
    path = root / "metadata" / "request_ledger.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, Mapping):
                raise SemanticLiveRunnerError("request ledger row is not an object")
            rows.append(dict(value))
    return rows


def _unit_rows(preflight_root: Path, channel: str) -> list[Mapping[str, Any]]:
    path = preflight_root / ("deepseek_units.json" if channel == "deepseek" else "gemini_units.json")
    rows = _read_json(path).get("items")
    if not isinstance(rows, list):
        raise SemanticLiveRunnerError("frozen unit artifact has no items")
    return [row for row in rows if isinstance(row, Mapping)]


def _payload_rows(preflight_root: Path, channel: str) -> list[Mapping[str, Any]]:
    path = preflight_root / ("deepseek_payloads.jsonl.gz" if channel == "deepseek" else "gemini_payloads.jsonl.gz")
    rows: list[Mapping[str, Any]] = []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                value = json.loads(line)
                if not isinstance(value, Mapping):
                    raise SemanticLiveRunnerError("frozen provider payload row is not an object")
                rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SemanticLiveRunnerError(f"cannot read frozen provider payloads: {path}") from exc
    return rows


def _verify_preflight_artifact(preflight_root: Path, preflight: Mapping[str, Any], filename: str) -> None:
    descriptor = preflight.get("artifacts", {}).get(filename)
    if not isinstance(descriptor, Mapping):
        raise SemanticLiveRunnerError(f"preflight has no immutable descriptor for {filename}")
    path = preflight_root / filename
    if not path.exists() or _sha(path.read_bytes()) != descriptor.get("sha256"):
        raise SemanticLiveRunnerError(f"frozen preflight artifact hash mismatch: {filename}")


def _validate_config_against_preflight(
    config: ChannelConfig,
    preflight: Mapping[str, Any],
    experiment: SemanticExperimentContract | None,
) -> None:
    expected_key = "challenger" if config.channel == "deepseek" else "primary"
    expected = preflight.get("provider_execution_config", {}).get(expected_key, {})
    frozen_semantic_provider = "DeepSeek" if config.channel == "deepseek" else config.provider
    if frozen_semantic_provider != expected.get("provider") or config.model_alias != expected.get("model_alias") or config.reasoning_or_thinking != expected.get("reasoning_or_thinking"):
        raise SemanticLiveRunnerError("runtime provider/model/reasoning differs from frozen preflight")
    if experiment is None:
        if config.structured_output_mode != expected.get("structured_output_mode"):
            raise SemanticLiveRunnerError("runtime structured-output mode differs from frozen preflight")
    elif config.structured_output_mode != experiment.request_contract.get("structured_output_mode"):
        raise SemanticLiveRunnerError("runtime structured-output mode differs from experiment contract")
    if dict(config.generation_parameters) != dict(expected.get("generation_parameters", {})):
        raise SemanticLiveRunnerError("runtime generation parameters differ from frozen preflight")
    if config.max_output_tokens != expected.get("max_output_tokens") or config.timeout_seconds != expected.get("timeout_seconds"):
        raise SemanticLiveRunnerError("runtime output/timeout setting differs from frozen preflight")


def _safe_secret_values(environment: Mapping[str, str], config: ChannelConfig) -> tuple[str, ...]:
    if config.auth_mode.lower() in {"none", "anonymous"}:
        return ()
    value = environment.get(config.api_key_env)
    if value is None:
        raise SemanticLiveRunnerError(f"{config.api_key_env} must be set immediately before a provider request")
    if not isinstance(value, str) or not value:
        raise SemanticLiveRunnerError("provider API key environment value must be non-empty")
    return (value,)


def _redact(text: str, secrets: Sequence[str]) -> str:
    result = str(text)
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    return result


def _safe_usage(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return "UNKNOWN"
    if isinstance(value, (int, float)) and value >= 0:
        return value
    return "UNKNOWN"


def _safe_charge(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return "UNKNOWN"
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and len(value) <= 128 and "\n" not in value and "\r" not in value:
        return value
    return "UNKNOWN"


def _response_chars(body: bytes) -> int | str:
    try:
        return len(body.decode("utf-8"))
    except UnicodeDecodeError:
        return "UNKNOWN"


def _usage(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"input_tokens": "UNKNOWN", "output_tokens": "UNKNOWN", "cached_tokens": "UNKNOWN", "reasoning_tokens": "UNKNOWN"}
    return {key: _safe_usage(value.get(key)) for key in ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens")}


def _safe_response_headers(value: Mapping[str, Any] | None, secrets: Sequence[str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str] = {}
    for raw_name, raw_value in value.items():
        name = str(raw_name).lower()
        if name not in TRANSPORT_RESPONSE_HEADER_ALLOWLIST or not isinstance(raw_value, str):
            continue
        if not raw_value or len(raw_value) > 1024 or "\r" in raw_value or "\n" in raw_value:
            continue
        if any(secret in raw_value for secret in secrets):
            continue
        result[name] = raw_value
    return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SemanticResponseValidationError(SemanticLiveRunnerError):
    """One explicit local-validation layer rejected a preserved response."""

    def __init__(
        self,
        layer: str,
        cause: Exception,
        diagnostics: Mapping[str, Any],
        *,
        content: str | None = None,
        parsed: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(str(cause))
        self.layer = layer
        self.diagnostics = dict(diagnostics)
        self.content = content
        self.parsed = parsed


def _validation_diagnostics() -> dict[str, Any]:
    return {
        "content_extraction": {"status": "not_started"},
        "strict_json_parse": {"status": "not_started"},
        "authoritative_schema_validation": {"status": "not_started", "schema_identity": SEMANTIC_OUTPUT_SCHEMA_IDENTITY},
        "source_binding_validation": {"status": "not_started"},
        "terminal_disposition": "rejected_fail_closed",
        "semantic_correctness": "not_assessed",
        "production_adoption": "not_authorized",
    }


def _validate_raw_response(
    adapter: SemanticProviderAdapter,
    raw: bytes,
    *,
    expected_segment_ids: Sequence[str],
) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any]]:
    diagnostics = _validation_diagnostics()
    content: str | None = None
    parsed: Mapping[str, Any] | None = None
    if not callable(getattr(adapter, "extract_content", None)) or not callable(getattr(adapter, "parse_content", None)):
        try:
            parsed = adapter.parse_response(raw)
            if not isinstance(parsed, Mapping):
                raise SemanticLiveRunnerError("provider semantic output is not a JSON object")
            diagnostics["content_extraction"] = {"status": "legacy_combined_passed"}
            diagnostics["strict_json_parse"] = {"status": "legacy_combined_passed", "top_level": "object"}
        except Exception as exc:
            diagnostics["content_extraction"] = {"status": "legacy_combined_rejected", "error": str(exc)}
            diagnostics["strict_json_parse"] = {"status": "legacy_combined_rejected", "error": str(exc)}
            raise SemanticResponseValidationError("content_extraction_or_strict_json_parse", exc, diagnostics) from exc
    else:
        try:
            content = adapter.extract_content(raw)
            if not isinstance(content, str) or not content.strip():
                raise SemanticLiveRunnerError("provider response has no semantic output content")
            diagnostics["content_extraction"] = {"status": "passed", "content_chars": len(content)}
        except Exception as exc:
            diagnostics["content_extraction"] = {"status": "rejected", "error": str(exc)}
            raise SemanticResponseValidationError("content_extraction", exc, diagnostics) from exc
        try:
            parsed = adapter.parse_content(content)
            if not isinstance(parsed, Mapping):
                raise SemanticLiveRunnerError("provider semantic output is not a JSON object")
            diagnostics["strict_json_parse"] = {"status": "passed", "top_level": "object"}
        except Exception as exc:
            diagnostics["strict_json_parse"] = {"status": "rejected", "error": str(exc)}
            raise SemanticResponseValidationError("strict_json_parse", exc, diagnostics, content=content) from exc
    assert parsed is not None
    try:
        schema_valid = validate_semantic_output_schema(parsed)
        diagnostics["authoritative_schema_validation"] = {"status": "passed", "schema_identity": SEMANTIC_OUTPUT_SCHEMA_IDENTITY}
    except Exception as exc:
        diagnostics["authoritative_schema_validation"] = {"status": "rejected", "schema_identity": SEMANTIC_OUTPUT_SCHEMA_IDENTITY, "error": str(exc)}
        raise SemanticResponseValidationError("authoritative_schema_validation", exc, diagnostics, content=content, parsed=parsed) from exc
    try:
        normalized = validate_semantic_source_binding(schema_valid, expected_segment_ids=expected_segment_ids)
        diagnostics["source_binding_validation"] = {"status": "passed", "expected_segment_count": len(expected_segment_ids)}
    except Exception as exc:
        diagnostics["source_binding_validation"] = {"status": "rejected", "expected_segment_count": len(expected_segment_ids), "error": str(exc)}
        raise SemanticResponseValidationError("source_binding_validation", exc, diagnostics, content=content, parsed=schema_valid) from exc
    diagnostics["terminal_disposition"] = "accepted_for_local_contract"
    return normalized, content, dict(parsed), diagnostics


def _persist_local_validation(
    run_root: Path,
    artifact_stem: str,
    diagnostics: Mapping[str, Any],
    *,
    content: str | None,
    parsed: Mapping[str, Any] | None,
) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    if content is not None:
        artifacts["extracted_content_artifact"] = _relative_artifact(
            _write_bytes(run_root / "content" / f"{artifact_stem}.txt", content.encode("utf-8")), run_root
        )
    if parsed is not None:
        artifacts["parsed_candidate_artifact"] = _relative_artifact(
            _write_json(run_root / "parsed_candidates" / f"{artifact_stem}.json", parsed), run_root
        )
    validation = {**dict(diagnostics), "artifacts": artifacts}
    descriptor = _relative_artifact(_write_json(run_root / "validation" / f"{artifact_stem}.json", validation), run_root)
    return {**artifacts, "validation_artifact": descriptor}


def _select_units(rows: Sequence[Mapping[str, Any]], ledger: Sequence[Mapping[str, Any]], *, unit_id: str | None, remaining: bool) -> list[Mapping[str, Any]]:
    if (unit_id is None) == (not remaining):
        raise SemanticLiveRunnerError("choose exactly one of --unit-id or --remaining")
    attempted = {str(row.get("compilation_unit_id")) for row in ledger}
    by_id = {str(row.get("compilation_unit_id")): row for row in rows}
    if unit_id is not None:
        if unit_id not in by_id:
            raise SemanticLiveRunnerError(f"unit is not in the frozen channel set: {unit_id}")
        if unit_id in attempted:
            raise SemanticLiveRunnerError("unit already has an issued attempt; automatic retry is disabled")
        return [by_id[unit_id]]
    return [row for row in rows if str(row.get("compilation_unit_id")) not in attempted]


def _prior_attempt_rows(
    roots: Sequence[Path],
    *,
    preflight_identity: str,
    channel: str,
    frozen_unit_ids: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        root = Path(root)
        manifest = _read_json(root / "metadata" / "run_manifest.json")
        if manifest.get("preflight_identity") != preflight_identity or manifest.get("channel") != channel:
            raise SemanticLiveRunnerError("prior attempt root does not match the frozen preflight/channel")
        for row in _read_ledger(root):
            unit_id = str(row.get("compilation_unit_id"))
            if unit_id not in frozen_unit_ids or unit_id in seen:
                raise SemanticLiveRunnerError("prior attempt ledger has an invalid or duplicate frozen unit")
            if row.get("status") not in {"issued", "succeeded", "failed"}:
                raise SemanticLiveRunnerError("prior attempt ledger row has no consumed attempt status")
            for artifact_key in ("request_artifact", "response_artifact"):
                descriptor = row.get(artifact_key)
                if descriptor is None and artifact_key == "response_artifact":
                    continue
                if not isinstance(descriptor, Mapping):
                    raise SemanticLiveRunnerError(f"prior attempt ledger has no valid {artifact_key}")
                artifact_path = root / str(descriptor.get("path", ""))
                try:
                    body = artifact_path.read_bytes()
                except OSError as exc:
                    raise SemanticLiveRunnerError(f"cannot read prior attempt {artifact_key}") from exc
                if descriptor.get("sha256") != _sha(body) or descriptor.get("byte_count") != len(body):
                    raise SemanticLiveRunnerError(f"prior attempt {artifact_key} integrity mismatch")
            seen.add(unit_id)
            rows.append(row)
    return rows


def run_channel(
    preflight_root: Path,
    run_root: Path,
    config: ChannelConfig,
    adapter: SemanticProviderAdapter,
    *,
    unit_id: str | None = None,
    remaining: bool = False,
    environment: Mapping[str, str] | None = None,
    prior_attempt_roots: Sequence[Path] = (),
    experiment: SemanticExperimentContract | None = None,
) -> dict[str, Any]:
    """Run one canary or the unattempted remainder of one isolated channel."""

    from .semantic_openai_chat_adapter import OpenAIChatCompletionsAdapter

    if (isinstance(adapter, OpenAIChatCompletionsAdapter)
            and type(adapter).invoke is OpenAIChatCompletionsAdapter.invoke
            and not adapter.legacy_live_enabled):
        raise SemanticLiveRunnerError("legacy direct-HTTP live invocation requires explicit opt-in")
    preflight_root, run_root = Path(preflight_root), Path(run_root)
    environment = os.environ if environment is None else environment
    preflight = _read_json(preflight_root / "preflight.json")
    if preflight.get("status") != "provider_free_ready":
        raise SemanticLiveRunnerError("preflight is not provider-free ready")
    declared_preflight_identity = preflight.get("preflight_identity")
    if declared_preflight_identity != ACCEPTED_PREFLIGHT_IDENTITY:
        raise SemanticLiveRunnerError("preflight identity is not the exact accepted frozen identity")
    # U1 preflight identity is defined over the semantic/configuration body;
    # its later-added artifact descriptors are integrity metadata, not input
    # dependencies of that identity.
    recomputed_preflight_identity = sha256_json({key: value for key, value in preflight.items() if key not in {"preflight_identity", "artifacts"}})
    if declared_preflight_identity != recomputed_preflight_identity:
        raise SemanticLiveRunnerError("frozen preflight identity self-check failed")
    if experiment is not None:
        accepted_b = b_experiment_contract()
        if experiment.identity != accepted_b.identity or experiment.safe_dict() != accepted_b.safe_dict():
            raise SemanticLiveRunnerError("experiment contract is not the accepted B revision")
        if experiment.frozen_preflight_identity != declared_preflight_identity:
            raise SemanticLiveRunnerError("B experiment does not bind the frozen preflight")
        if preflight.get("u1_semantic_build_identity") != experiment.frozen_semantic_build_identity:
            raise SemanticLiveRunnerError("B experiment does not bind the frozen semantic build")
    unit_filename = "deepseek_units.json" if config.channel == "deepseek" else "gemini_units.json"
    payload_filename = "deepseek_payloads.jsonl.gz" if config.channel == "deepseek" else "gemini_payloads.jsonl.gz"
    _verify_preflight_artifact(preflight_root, preflight, unit_filename)
    _verify_preflight_artifact(preflight_root, preflight, payload_filename)
    _validate_config_against_preflight(config, preflight, experiment)
    units = _unit_rows(preflight_root, config.channel)
    payload_rows = _payload_rows(preflight_root, config.channel)
    if len(payload_rows) != len(units):
        raise SemanticLiveRunnerError("frozen unit and payload counts differ")
    if len(units) != (18 if config.channel == "deepseek" else 30):
        raise SemanticLiveRunnerError("frozen channel unit count is not accepted")
    if config.channel == "deepseek" and not prior_attempt_roots:
        raise SemanticLiveRunnerError("DeepSeek formal execution requires prior attempt evidence")
    if experiment is not None and not prior_attempt_roots:
        raise SemanticLiveRunnerError("B formal execution requires prior consumed-attempt evidence")
    prior_ledger = _prior_attempt_rows(
        prior_attempt_roots,
        preflight_identity=str(preflight.get("preflight_identity")),
        channel=config.channel,
        frozen_unit_ids={str(row.get("compilation_unit_id")) for row in units},
    )
    secrets = _safe_secret_values(environment, config)
    if any(secret in config.endpoint or secret in config.adapter_factory for secret in secrets):
        raise SemanticLiveRunnerError("provider configuration contains a configured secret")
    existing_manifest = run_root / "metadata" / "run_manifest.json"
    if experiment is not None and run_root.exists() and not existing_manifest.exists():
        raise SemanticLiveRunnerError("B run root must be new or contain its matching run manifest")
    if existing_manifest.exists():
        manifest = _read_json(existing_manifest)
        if manifest.get("preflight_identity") != preflight.get("preflight_identity") or manifest.get("channel") != config.channel or manifest.get("config_identity") != config.config_identity:
            raise SemanticLiveRunnerError("existing run root does not match frozen preflight/channel/config")
        expected_experiment_identity = experiment.identity if experiment is not None else None
        if manifest.get("experiment_identity") != expected_experiment_identity:
            raise SemanticLiveRunnerError("existing run root does not match experiment identity")
    else:
        run_root.mkdir(parents=True, exist_ok=True)
        created_at = _utc_now()
        contract_artifacts: dict[str, Any] = {}
        if experiment is not None:
            contract_artifacts = {
                "prompt_contract": _relative_artifact(_write_json(run_root / "contracts" / "prompt_contract.json", experiment.prompt_contract), run_root),
                "request_contract": _relative_artifact(_write_json(run_root / "contracts" / "request_contract.json", experiment.request_contract), run_root),
                "output_schema": _relative_artifact(_write_json(run_root / "contracts" / "semantic_output_schema.json", semantic_output_schema()), run_root),
            }
        run_identity = sha256_json({
            "schema_version": LIVE_RUNNER_SCHEMA_VERSION,
            "created_at": created_at,
            "preflight_identity": preflight.get("preflight_identity"),
            "channel": config.channel,
            "config_identity": config.config_identity,
            "experiment_identity": experiment.identity if experiment is not None else None,
        })
        manifest = {
            "schema_version": LIVE_RUNNER_SCHEMA_VERSION,
            "created_at": created_at,
            "run_identity": run_identity,
            "preflight_identity": preflight.get("preflight_identity"),
            "channel": config.channel,
            "config_identity": config.config_identity,
            "provider_execution_config": config.safe_dict(),
            "experiment_identity": experiment.identity if experiment is not None else None,
            "experiment_contract": experiment.safe_dict() if experiment is not None else None,
            "contract_artifacts": contract_artifacts,
            "acceptance_semantics": {
                "accepted_for_local_contract": ["strict_json", "authoritative_output_schema", "source_binding"],
                "semantic_correctness": "not_assessed",
                "production_adoption": "not_authorized",
            },
            "automatic_retry": False,
            "maximum_attempts": config.maximum_attempts,
        }
        _write_json(run_root / "metadata" / "run_manifest.json", manifest)
    ledger = _read_ledger(run_root)
    prior_unit_ids = {str(row.get("compilation_unit_id")) for row in prior_ledger}
    if prior_unit_ids.intersection(str(row.get("compilation_unit_id")) for row in ledger):
        raise SemanticLiveRunnerError("current run ledger overlaps prior consumed attempts")
    consumed_ledger = [*prior_ledger, *ledger]
    if experiment is not None and remaining:
        raise SemanticLiveRunnerError("B canary requires one explicit unit; --remaining is disabled")
    selected = _select_units(units, consumed_ledger, unit_id=unit_id, remaining=remaining)
    if experiment is not None:
        for candidate in selected:
            binding = candidate.get("ru_binding")
            if not isinstance(binding, Mapping) or binding.get("eligible_for_live_sample") is not True:
                raise SemanticLiveRunnerError("B canary unit is not live-sample/RU eligible")
            segment_count = candidate.get("segment_count")
            if (
                binding.get("ru_unbound_segment_count") != 0
                or binding.get("ru_bound_segment_count") != segment_count
                or binding.get("source_segment_count") != segment_count
            ):
                raise SemanticLiveRunnerError("B canary unit is not fully RU-bound")
            if not isinstance(candidate.get("serialized_input_chars"), int) or candidate["serialized_input_chars"] > 16_000:
                raise SemanticLiveRunnerError("B canary unit is oversized")
    if config.channel == "deepseek":
        if remaining:
            raise SemanticLiveRunnerError("DeepSeek formal canary requires one explicit next unattempted unit")
        consumed_ids = {str(row.get("compilation_unit_id")) for row in consumed_ledger}
        next_unattempted = next((str(row.get("compilation_unit_id")) for row in units if str(row.get("compilation_unit_id")) not in consumed_ids), None)
        if unit_id != next_unattempted:
            raise SemanticLiveRunnerError("DeepSeek formal canary must select the next unattempted frozen unit")
    if len(consumed_ledger) + len(selected) > config.maximum_attempts:
        raise SemanticLiveRunnerError("hard channel request ceiling would be exceeded")
    if not selected:
        return {"status": "no_remaining_units", "channel": config.channel, "attempted_requests": len(ledger), "prior_attempted_requests": len(prior_ledger), "cumulative_attempted_requests": len(consumed_ledger), "maximum_attempts": config.maximum_attempts}
    prompt_contract = dict(experiment.prompt_contract) if experiment is not None else _read_json(preflight_root / "prompt_contract.json")
    if preflight.get("semantic_output_schema_identity") != SEMANTIC_OUTPUT_SCHEMA_IDENTITY:
        raise SemanticLiveRunnerError("frozen semantic output schema identity is not the accepted schema")
    schema = semantic_output_schema()
    failures = 0
    payload_by_ordinal: dict[int, Mapping[str, Any]] = {}
    ordinal_field = "deepseek_request_ordinal" if config.channel == "deepseek" else "gemini_request_ordinal"
    for row, payload in zip(units, payload_rows):
        ordinal = int(row[ordinal_field])
        if semantic_input_identity(payload) != row.get("semantic_input_identity") or _sha(canonical_json_bytes(payload)) != row.get("payload_sha256"):
            raise SemanticLiveRunnerError(f"frozen payload identity mismatch at ordinal {ordinal}")
        payload_by_ordinal[ordinal] = payload
    for row in selected:
        ordinal_key = "deepseek_request_ordinal" if config.channel == "deepseek" else "gemini_request_ordinal"
        ordinal = int(row[ordinal_key])
        payload = payload_by_ordinal.get(ordinal)
        if not isinstance(payload, Mapping):
            raise SemanticLiveRunnerError(f"frozen provider payload missing for ordinal {ordinal}")
        request = SemanticProviderRequest(config.channel, str(row["compilation_unit_id"]), str(row["semantic_input_identity"]), payload, prompt_contract, schema, config, ordinal)
        if not callable(getattr(adapter, "build_request_body", None)):
            raise SemanticLiveRunnerError("provider adapter must implement build_request_body")
        request_body = adapter.build_request_body(request)
        if not isinstance(request_body, bytes) or not request_body:
            raise SemanticLiveRunnerError("provider adapter returned an invalid request body")
        request_path = run_root / "requests" / f"{ordinal:03d}-{request.compilation_unit_id}.json"
        if any(secret.encode("utf-8") in request_body for secret in secrets):
            raise SemanticLiveRunnerError("provider request body contains a configured secret")
        request_artifact = _relative_artifact(_write_bytes(request_path, request_body), run_root)
        request_identity = _sha(request_body)
        attempt_identity = sha256_json({
            "run_identity": manifest.get("run_identity"),
            "experiment_identity": experiment.identity if experiment is not None else None,
            "channel": config.channel,
            "compilation_unit_id": request.compilation_unit_id,
            "semantic_input_identity": request.semantic_input_identity,
            "request_ordinal": ordinal,
            "request_identity": request_identity,
        })
        issued = {
            "experiment_identity": experiment.identity if experiment is not None else None,
            "attempt_identity": attempt_identity,
            "channel": config.channel,
            "compilation_unit_id": request.compilation_unit_id,
            "semantic_input_identity": request.semantic_input_identity,
            "request_ordinal": ordinal,
            "attempted_request_count": len(ledger) + 1,
            "status": "issued",
            "provider_request_id": None,
            "transport_status": None,
            "latency_ms": None,
            "schema_parser_binding_disposition": "not_started",
            "request_artifact": request_artifact,
            "request_identity": request_identity,
            "request_bytes": len(request_body),
            "request_chars": len(request_body.decode("utf-8")),
            "segment_ids": list(row["segment_ids"]),
            "response_bytes": None,
            "response_chars": None,
            "usage": _usage(None),
            "charge": None,
            "billable": "UNKNOWN",
            "issued_at": _utc_now(),
            "response_received_at": None,
            "terminal_at": None,
        }
        ledger.append(issued)
        _write_ledger(run_root, ledger)
        started = time.monotonic()
        response: SemanticProviderResponse | None = None
        failure: dict[str, Any] | None = None
        try:
            response = adapter.invoke(request)
            if not isinstance(response, SemanticProviderResponse) or not isinstance(response.raw_response_bytes, bytes):
                raise SemanticLiveRunnerError("adapter returned an invalid normalized response")
            if response.provider_request_id is not None and not isinstance(response.provider_request_id, str):
                raise SemanticLiveRunnerError("provider request ID must be a string or null")
            if any(secret.encode("utf-8") in response.raw_response_bytes for secret in secrets):
                raise SemanticLiveRunnerError("raw response contains a configured secret and was not persisted")
            if response.provider_request_id is not None and any(secret in response.provider_request_id for secret in secrets):
                raise SemanticLiveRunnerError("provider request ID contains a configured secret")
            response_artifact = _relative_artifact(_write_bytes(run_root / "responses" / f"{ordinal:03d}-{request.compilation_unit_id}.bin", response.raw_response_bytes), run_root)
            response_headers = _safe_response_headers(response.response_headers, secrets)
            ledger[-1].update({
                "provider_request_id": response.provider_request_id,
                "transport_status": response.transport_status,
                "latency_ms": round((time.monotonic() - started) * 1000, 3),
                "response_artifact": response_artifact,
                "response_bytes": len(response.raw_response_bytes),
                "response_chars": _response_chars(response.raw_response_bytes),
                "response_received_at": _utc_now(),
                "finish_reason": response.finish_reason,
                "usage": _usage(response.usage),
                "charge": _safe_charge(response.charge),
                "billable": response.billable if isinstance(response.billable, bool) else "UNKNOWN",
            })
            if response_headers:
                ledger[-1]["response_headers"] = response_headers
            # Raw response plus transport metadata is durable before any local
            # extraction, parsing, schema, or source-binding step can fail.
            _write_ledger(run_root, ledger)
            normalized, content, parsed, diagnostics = _validate_raw_response(
                adapter, response.raw_response_bytes, expected_segment_ids=row["segment_ids"]
            )
            stem = f"{ordinal:03d}-{request.compilation_unit_id}"
            local_artifacts = _persist_local_validation(run_root, stem, diagnostics, content=content, parsed=parsed)
            accepted_artifact = _relative_artifact(_write_json(run_root / "parsed" / f"{stem}.json", normalized), run_root)
            ledger[-1].update({
                "status": "succeeded",
                "schema_parser_binding_disposition": "accepted_for_local_contract",
                "accepted_output_artifact": accepted_artifact,
                **local_artifacts,
                "terminal_at": _utc_now(),
            })
        except SemanticProviderTransportError as exc:
            failures += 1
            safe_request_id = exc.provider_request_id if isinstance(exc.provider_request_id, str) and not any(secret in exc.provider_request_id for secret in secrets) else None
            ledger[-1].update({"provider_request_id": safe_request_id, "transport_status": exc.transport_status, "usage": _usage(exc.usage), "charge": _safe_charge(exc.charge), "billable": exc.billable if isinstance(exc.billable, bool) else "UNKNOWN"})
            response_headers = _safe_response_headers(exc.response_headers, secrets)
            if response_headers:
                ledger[-1]["response_headers"] = response_headers
            if exc.raw_response_bytes is not None and not any(secret.encode("utf-8") in exc.raw_response_bytes for secret in secrets):
                response_artifact = _relative_artifact(_write_bytes(run_root / "responses" / f"{ordinal:03d}-{request.compilation_unit_id}.bin", exc.raw_response_bytes), run_root)
                ledger[-1].update({"response_artifact": response_artifact, "response_bytes": len(exc.raw_response_bytes), "response_chars": _response_chars(exc.raw_response_bytes), "response_received_at": _utc_now()})
                _write_ledger(run_root, ledger)
            ledger[-1].update({"status": "failed", "latency_ms": round((time.monotonic() - started) * 1000, 3), "schema_parser_binding_disposition": "transport_failed", "failure": {"code": _redact(exc.code, secrets)}, "terminal_at": _utc_now()})
            _write_json(run_root / "failures" / f"{ordinal:03d}-{request.compilation_unit_id}.json", {"code": _redact(exc.code, secrets)})
        except SemanticResponseValidationError as exc:
            failures += 1
            stem = f"{ordinal:03d}-{request.compilation_unit_id}"
            local_artifacts = _persist_local_validation(run_root, stem, exc.diagnostics, content=exc.content, parsed=exc.parsed)
            failure = {"code": type(exc).__name__, "layer": exc.layer, "message": _redact(str(exc), secrets)}
            ledger[-1].update({
                "status": "failed",
                "latency_ms": round((time.monotonic() - started) * 1000, 3),
                "schema_parser_binding_disposition": "rejected_fail_closed",
                "failure": failure,
                **local_artifacts,
                "terminal_at": _utc_now(),
            })
            _write_json(run_root / "failures" / f"{stem}.json", failure)
        except Exception as exc:
            failures += 1
            failure = {"code": type(exc).__name__, "message": _redact(str(exc), secrets)}
            ledger[-1].update({"status": "failed", "latency_ms": round((time.monotonic() - started) * 1000, 3), "schema_parser_binding_disposition": "rejected_fail_closed", "failure": failure, "terminal_at": _utc_now()})
            _write_json(run_root / "failures" / f"{ordinal:03d}-{request.compilation_unit_id}.json", failure)
        _write_ledger(run_root, ledger)
    status = "partial" if failures else "complete"
    summary = {"status": status, "channel": config.channel, "experiment_identity": experiment.identity if experiment is not None else None, "attempted_requests": len(ledger), "prior_attempted_requests": len(prior_ledger), "cumulative_attempted_requests": len(prior_ledger) + len(ledger), "succeeded": sum(row.get("status") == "succeeded" for row in ledger), "failed": sum(row.get("status") == "failed" for row in ledger), "maximum_attempts": config.maximum_attempts, "automatic_retry": False, "provider_calls_executed": len(selected), "network_calls_executed": len(selected)}
    atomic_write(run_root / "metadata" / "accounting.json", canonical_json_bytes(summary))
    return summary


def replay_response(
    run_root: Path,
    config: ChannelConfig,
    adapter: SemanticProviderAdapter,
    compilation_unit_id: str,
    *,
    experiment: SemanticExperimentContract | None = None,
) -> dict[str, Any]:
    """Replay one preserved raw response without invoking a provider."""

    root = Path(run_root)
    manifest = _read_json(root / "metadata" / "run_manifest.json")
    if manifest.get("channel") != config.channel or manifest.get("config_identity") != config.config_identity:
        raise SemanticLiveRunnerError("replay config does not match the isolated run root")
    expected_experiment_identity = experiment.identity if experiment is not None else None
    if manifest.get("experiment_identity") != expected_experiment_identity:
        raise SemanticLiveRunnerError("replay experiment does not match the isolated run root")
    rows = _read_ledger(root)
    matches = [row for row in rows if row.get("compilation_unit_id") == compilation_unit_id and row.get("status") in {"succeeded", "failed"}]
    if not matches:
        raise SemanticLiveRunnerError("no terminal attempt exists for replay")
    row = matches[-1]
    artifact = row.get("response_artifact")
    if not isinstance(artifact, Mapping):
        raise SemanticLiveRunnerError("terminal attempt has no preserved response")
    artifact_path = Path(str(artifact["path"]))
    if not artifact_path.is_absolute():
        artifact_path = Path(run_root) / artifact_path
    raw = artifact_path.read_bytes()
    if artifact.get("sha256") != _sha(raw) or artifact.get("byte_count") != len(raw):
        raise SemanticLiveRunnerError("preserved raw response artifact hash mismatch")
    try:
        normalized, _content, _parsed, diagnostics = _validate_raw_response(
            adapter, raw, expected_segment_ids=row.get("segment_ids", [])
        )
    except SemanticResponseValidationError as exc:
        return {
            "compilation_unit_id": compilation_unit_id,
            "raw_response_sha256": _sha(raw),
            "disposition": "rejected_fail_closed",
            "failure_layer": exc.layer,
            "validation": exc.diagnostics,
            "provider_calls_executed": 0,
            "network_calls_executed": 0,
        }
    return {
        "compilation_unit_id": compilation_unit_id,
        "raw_response_sha256": _sha(raw),
        "disposition": "accepted_for_local_contract",
        "parsed": normalized,
        "validation": diagnostics,
        "provider_calls_executed": 0,
        "network_calls_executed": 0,
    }


def run_b_zero_network_preflight(
    output_root: Path,
    config: ChannelConfig,
    *,
    experiment: SemanticExperimentContract | None = None,
) -> dict[str, Any]:
    """Exercise B request, persistence, validation, and replay inputs without invocation."""

    output_root = Path(output_root)
    if output_root.exists():
        raise SemanticLiveRunnerError("B zero-network preflight output root already exists")
    experiment = b_experiment_contract() if experiment is None else experiment
    if experiment.identity not in {b_experiment_contract().identity, b_v2_experiment_contract().identity}:
        raise SemanticLiveRunnerError("zero-network preflight requires a known B experiment contract")
    if config.structured_output_mode != "JSON_OBJECT":
        raise SemanticLiveRunnerError("B zero-network preflight requires JSON_OBJECT config")
    adapter = load_offline_adapter(config.adapter_factory, config)
    segment_ids = ("fixture-segment-event", "fixture-segment-empty")
    payload = {
        "schema_version": "phase05-w2-semantic-input-fixture-0.1",
        "segments": [
            {"segment_id": segment_ids[0], "text": "A meets B."},
            {"segment_id": segment_ids[1], "text": "Section heading."},
        ],
    }
    request = SemanticProviderRequest(
        channel=config.channel,
        compilation_unit_id="provider-free-fixture-unit",
        semantic_input_identity=semantic_input_identity(payload),
        payload=payload,
        prompt_contract=experiment.prompt_contract,
        output_schema=semantic_output_schema(),
        config=config,
        request_ordinal=0,
    )
    request_body = adapter.build_request_body(request)
    try:
        wire_request = json.loads(request_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SemanticLiveRunnerError("B preflight request is not valid JSON") from exc
    if not isinstance(wire_request, Mapping) or wire_request.get("response_format") != {"type": "json_object"} or wire_request.get("stream") is not False or "json_schema" in json.dumps(wire_request):
        raise SemanticLiveRunnerError("B preflight request does not implement the json_object contract")
    expected = {
        "schema_version": SEMANTIC_OUTPUT_SCHEMA_VERSION,
        "items": [{
            "local_id": "i1", "kind": "event", "label": "A meets B",
            "source_segment_ids": [segment_ids[0]], "topic_path": ["fixture"],
            "event_type": "meeting", "participants": ["A", "B"], "qualifiers": {},
        }],
        "segment_coverage": [
            {"segment_id": segment_ids[0], "disposition": "covered", "reason": None},
            {"segment_id": segment_ids[1], "disposition": "no_navigation_material", "reason": "heading only"},
        ],
    }
    if experiment.revision == B_V2_EXPERIMENT_REVISION:
        expected["items"] = list(b_v2_prompt_contract()["minimal_example"]["output"]["items"])
        expected["items"] = [
            {**item, "source_segment_ids": [segment_ids[0]]}
            for item in expected["items"]
        ]
        expected["items"][2]["topic_path"] = ["fixture"]
    raw = canonical_json_bytes({
        "id": "provider-free-fixture-response",
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": canonical_json_bytes(expected).decode("utf-8")}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
    })
    output_root.mkdir(parents=True)
    request_artifact = _relative_artifact(_write_bytes(output_root / "request.json", request_body), output_root)
    response_artifact = _relative_artifact(_write_bytes(output_root / "raw_response.json", raw), output_root)
    normalized, content, parsed, diagnostics = _validate_raw_response(adapter, raw, expected_segment_ids=segment_ids)
    if experiment.revision == B_V2_EXPERIMENT_REVISION:
        validate_b_v2_navigation_references(normalized)
    replayed, _replay_content, _replay_parsed, replay_diagnostics = _validate_raw_response(adapter, raw, expected_segment_ids=segment_ids)
    if replayed != normalized or replay_diagnostics.get("terminal_disposition") != "accepted_for_local_contract":
        raise SemanticLiveRunnerError("B provider-free replay is not deterministic")
    local_artifacts = _persist_local_validation(output_root, "provider-free-fixture", diagnostics, content=content, parsed=parsed)
    accepted_artifact = _relative_artifact(_write_json(output_root / "accepted.json", normalized), output_root)
    report = {
        "schema_version": "phase05-w2-b-zero-network-preflight-0.1",
        "status": "PASS",
        "formal_attempts_consumed": 0,
        "provider_calls_executed": 0,
        "network_calls_executed": 0,
        "experiment_identity": experiment.identity,
        "experiment_contract": experiment.safe_dict(),
        "config_identity": config.config_identity,
        "request_artifact": request_artifact,
        "raw_response_artifact": response_artifact,
        "accepted_output_artifact": accepted_artifact,
        "local_validation_artifacts": local_artifacts,
        "terminal_disposition": "accepted_for_local_contract",
        "offline_replay_verified": True,
        "semantic_correctness": "not_assessed",
        "production_adoption": "not_authorized",
    }
    _write_json(output_root / "preflight.json", report)
    return report


__all__ = [
    "ACCEPTED_PREFLIGHT_IDENTITY", "B_EXPERIMENT_REVISION", "B_PROMPT_VERSION", "B_REQUEST_CONTRACT_VERSION",
    "B_V2_EXPERIMENT_REVISION", "B_V2_PROMPT_VERSION",
    "CHANNELS", "CHANNEL_LIMITS", "CHANNEL_TRANSPORT_PROFILES", "ChannelConfig", "GEMINI_A_USER_AGENT", "LIVE_RUNNER_SCHEMA_VERSION",
    "OPENAI_CHAT_ADAPTER_FACTORY",
    "SemanticExperimentContract", "SemanticLiveRunnerError", "SemanticProviderTransportError", "SemanticProviderAdapter", "SemanticProviderRequest",
    "SemanticProviderResponse", "SemanticResponseValidationError", "TRANSPORT_RESPONSE_HEADER_ALLOWLIST", "b_experiment_contract",
    "b_prompt_contract", "b_request_contract", "b_v2_experiment_contract", "b_v2_prompt_contract",
    "load_adapter", "load_offline_adapter", "replay_response", "run_b_zero_network_preflight", "run_channel",
    "validate_b_v2_navigation_references",
]
