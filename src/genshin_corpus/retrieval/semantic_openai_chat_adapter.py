"""Legacy direct-HTTP P05 transport for historical replay and diagnostics."""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from genshin_corpus.canonical.fingerprints import canonical_json_bytes

from .semantic_live_runner import (
    CHANNEL_TRANSPORT_PROFILES,
    TRANSPORT_RESPONSE_HEADER_ALLOWLIST,
    ChannelConfig,
    SemanticLiveRunnerError,
    SemanticProviderRequest,
    SemanticProviderResponse,
    SemanticProviderTransportError,
)


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _usage(response: Mapping[str, Any]) -> dict[str, Any] | None:
    usage = _mapping(response.get("usage"))
    if usage is None:
        return None
    prompt_details = _mapping(usage.get("prompt_tokens_details")) or _mapping(usage.get("input_tokens_details")) or {}
    completion_details = _mapping(usage.get("completion_tokens_details")) or _mapping(usage.get("output_tokens_details")) or {}
    candidates = {
        "input_tokens": (usage.get("prompt_tokens"), usage.get("input_tokens")),
        "output_tokens": (usage.get("completion_tokens"), usage.get("output_tokens")),
        "cached_tokens": (prompt_details.get("cached_tokens"), usage.get("cached_tokens")),
        "reasoning_tokens": (completion_details.get("reasoning_tokens"), usage.get("reasoning_tokens")),
    }
    normalized: dict[str, Any] = {}
    for name, values in candidates.items():
        for value in values:
            if value is not None:
                normalized[name] = value
                break
    return normalized


def _response_metadata(raw: bytes, header_request_id: str | None) -> tuple[str | None, dict[str, Any] | None]:
    try:
        response = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return header_request_id, None
    if not isinstance(response, Mapping):
        return header_request_id, None
    request_id = response.get("id") if isinstance(response.get("id"), str) else header_request_id
    return request_id, _usage(response)


def _finish_reason(raw: bytes) -> str | None:
    try:
        response = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(response, Mapping):
        return None
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        return None
    value = choices[0].get("finish_reason")
    return value if isinstance(value, str) else None


def _response_headers(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    result: dict[str, str] = {}
    for name in TRANSPORT_RESPONSE_HEADER_ALLOWLIST:
        value = headers.get(name)
        if isinstance(value, str) and value:
            result[name] = value
    return result


class OpenAIChatCompletionsAdapter:
    """One-attempt transport with no retry, fallback, or dialect guessing."""

    def __init__(self, config: ChannelConfig, api_key: str, *, opener: Callable[..., Any] | None = None,
                 allow_legacy_live: bool = False) -> None:
        self._config = config
        self._api_key = api_key
        self._opener = urlopen if opener is None else opener
        self._allow_legacy_live = allow_legacy_live is True

    @property
    def legacy_live_enabled(self) -> bool:
        return self._allow_legacy_live

    def build_request_body(self, request: SemanticProviderRequest) -> bytes:
        if request.config != self._config:
            raise SemanticLiveRunnerError("adapter request config mismatch")
        profile = CHANNEL_TRANSPORT_PROFILES[self._config.channel]
        if dict(self._config.generation_parameters) != {"temperature": 0.0}:
            raise SemanticLiveRunnerError("OpenAI-compatible adapter requires the frozen temperature setting")
        body: dict[str, Any] = {
            "model": self._config.transport_model,
            "messages": [
                {"role": "system", "content": canonical_json_bytes(request.prompt_contract).decode("utf-8")},
                {"role": "user", "content": canonical_json_bytes(request.payload).decode("utf-8")},
            ],
            "stream": False,
            "temperature": 0.0,
            "max_tokens": self._config.max_output_tokens,
        }
        if self._config.structured_output_mode == "JSON_SCHEMA":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "phase05_semantic_output",
                    "strict": True,
                    "schema": dict(request.output_schema),
                },
            }
        elif self._config.structured_output_mode == "JSON_OBJECT":
            body["response_format"] = {"type": "json_object"}
        else:
            raise SemanticLiveRunnerError("unsupported structured-output mode")
        reasoning_effort = profile["reasoning_effort"]
        if reasoning_effort is not None:
            body["reasoning_effort"] = reasoning_effort
        return canonical_json_bytes(body)

    def invoke(self, request: SemanticProviderRequest) -> SemanticProviderResponse:
        if not self._allow_legacy_live:
            raise SemanticLiveRunnerError("legacy direct-HTTP live invocation requires explicit opt-in")
        body = self.build_request_body(request)
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        if self._config.user_agent is not None:
            headers["User-Agent"] = self._config.user_agent
        http_request = Request(
            f"{self._config.endpoint.rstrip('/')}/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with self._opener(http_request, timeout=self._config.timeout_seconds) as response:
                raw = response.read()
                status = getattr(response, "status", None) or response.getcode()
                header_request_id = response.headers.get("x-request-id") if response.headers is not None else None
                response_headers = _response_headers(response.headers)
        except HTTPError as exc:
            header_request_id = exc.headers.get("x-request-id") if exc.headers is not None else None
            response_headers = _response_headers(exc.headers)
            try:
                raw = exc.read()
            finally:
                exc.close()
            provider_request_id, usage = _response_metadata(raw, header_request_id)
            raise SemanticProviderTransportError(
                f"http_status_{exc.code}",
                raw_response_bytes=raw,
                transport_status=exc.code,
                provider_request_id=provider_request_id,
                usage=usage,
                response_headers=response_headers,
            ) from None
        except (URLError, TimeoutError, OSError):
            raise SemanticProviderTransportError("transport_connection_failure") from None
        if not isinstance(status, int) or not 200 <= status < 300:
            provider_request_id, usage = _response_metadata(raw, header_request_id)
            raise SemanticProviderTransportError(
                f"http_status_{status}",
                raw_response_bytes=raw,
                transport_status=status,
                provider_request_id=provider_request_id,
                usage=usage,
                response_headers=_response_headers(response.headers),
            )
        provider_request_id, usage = _response_metadata(raw, header_request_id)
        finish_reason = _finish_reason(raw)
        return SemanticProviderResponse(
            raw_response_bytes=raw,
            transport_status=status,
            provider_request_id=provider_request_id,
            usage=usage,
            finish_reason=finish_reason,
            response_headers=response_headers,
        )

    def extract_content(self, raw_response_bytes: bytes) -> str:
        try:
            response = json.loads(raw_response_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SemanticLiveRunnerError("provider response is not valid JSON") from exc
        if not isinstance(response, Mapping):
            raise SemanticLiveRunnerError("provider response is not a JSON object")
        if "error" in response:
            raise SemanticLiveRunnerError("provider response contains an API error")
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise SemanticLiveRunnerError("provider response has no Chat Completions choice")
        message = choices[0].get("message")
        if not isinstance(message, Mapping) or not isinstance(message.get("content"), str) or not message["content"].strip():
            raise SemanticLiveRunnerError("provider response has no semantic output content")
        return message["content"]

    def parse_content(self, content: str) -> Mapping[str, Any]:
        try:
            semantic_output = json.loads(content)
        except json.JSONDecodeError as exc:
            raise SemanticLiveRunnerError("provider semantic output content is not valid JSON") from exc
        if not isinstance(semantic_output, Mapping):
            raise SemanticLiveRunnerError("provider semantic output is not a JSON object")
        return semantic_output

    def parse_response(self, raw_response_bytes: bytes) -> Mapping[str, Any]:
        return self.parse_content(self.extract_content(raw_response_bytes))


class OfflineOpenAIChatCompletionsAdapter(OpenAIChatCompletionsAdapter):
    """Request/parser implementation whose invocation seam is permanently closed."""

    def __init__(self, config: ChannelConfig) -> None:
        super().__init__(config, "offline-no-secret")

    def invoke(self, request: SemanticProviderRequest) -> SemanticProviderResponse:
        raise SemanticLiveRunnerError("offline adapter cannot invoke a provider")


def create_adapter(config: ChannelConfig, environment: Mapping[str, str], *,
                   allow_legacy_live: bool = False) -> OpenAIChatCompletionsAdapter:
    """Create a legacy adapter; live invocation requires explicit opt-in."""

    profile = CHANNEL_TRANSPORT_PROFILES.get(config.channel)
    if profile is None:
        raise SemanticLiveRunnerError("unsupported OpenAI-compatible semantic channel")
    if config.endpoint != profile["endpoint"] or config.transport_model != profile["transport_model"] or config.user_agent != profile["user_agent"]:
        raise SemanticLiveRunnerError("endpoint, transport model, or user agent differs from the authorized channel profile")
    if config.auth_mode != profile["auth_mode"]:
        raise SemanticLiveRunnerError("OpenAI-compatible semantic channels require Bearer auth")
    if config.structured_output_mode not in {"JSON_SCHEMA", "JSON_OBJECT"}:
        raise SemanticLiveRunnerError("OpenAI-compatible semantic channel has an unsupported output mode")
    api_key = environment.get(config.api_key_env)
    if not isinstance(api_key, str) or not api_key:
        raise SemanticLiveRunnerError(f"{config.api_key_env} must be set immediately before a provider request")
    return OpenAIChatCompletionsAdapter(config, api_key, allow_legacy_live=allow_legacy_live)


def create_offline_adapter(config: ChannelConfig) -> OfflineOpenAIChatCompletionsAdapter:
    """Create request/parser logic without credentials or network capability."""

    profile = CHANNEL_TRANSPORT_PROFILES.get(config.channel)
    if profile is None or config.endpoint != profile["endpoint"] or config.transport_model != profile["transport_model"] or config.user_agent != profile["user_agent"]:
        raise SemanticLiveRunnerError("offline adapter config differs from the authorized channel profile")
    return OfflineOpenAIChatCompletionsAdapter(config)


__all__ = ["OfflineOpenAIChatCompletionsAdapter", "OpenAIChatCompletionsAdapter", "create_adapter", "create_offline_adapter"]
