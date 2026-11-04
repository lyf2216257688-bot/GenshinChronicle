"""Phase04 V1 default final QA over the evidenced TokenMetro Chat route.

No Card/Link input or dependencies. Shared calls own attempts, reservations and
replay; this adapter owns raw-first HTTP and neutral Generation validation.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from genshin_corpus import model_calls
from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.evidence_io import digest, seal, sha, strict_json, write
from .generation import (
    GenerationConfigurationError, GenerationContractError, GenerationRequest,
    GenerationResult, _RejectRedirectHandler, generation_messages, validate_citations,
)

VERSION = "phase04-deepseek-qa-1"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4.1-flash-1017"
DEEPSEEK_BASE_URL = "https://tokenmetro.com/v1"
DEEPSEEK_LEDGER = Path("data/retrieval/p04-deepseek-request-ledger")
_RESPONSE_LIMIT = 8 * 1024 * 1024


@dataclass(frozen=True)
class DeepSeekGenerationConfig:
    run_root: Path
    max_requests: int
    max_tokens: int
    model_id: str = DEFAULT_DEEPSEEK_MODEL
    base_url: str = DEEPSEEK_BASE_URL
    max_output_tokens: int = 1024
    timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_root", Path(self.run_root).resolve())
        if self.base_url != DEEPSEEK_BASE_URL:
            raise GenerationConfigurationError("DeepSeek QA currently supports the evidenced TokenMetro base URL only")
        if not isinstance(self.model_id, str) or not re.fullmatch(r"deepseek-[a-zA-Z0-9.-]+", self.model_id):
            raise GenerationConfigurationError("DeepSeek requires an explicit safe model ID")
        for name in ("max_requests", "max_tokens", "max_output_tokens"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise GenerationConfigurationError(f"{name} must be a positive integer")
        if self.max_output_tokens > 32000:
            raise GenerationConfigurationError("DeepSeek output cap exceeds this adapter's 32000-token request-cap range")
        if type(self.timeout_seconds) not in (int, float) or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise GenerationConfigurationError("timeout_seconds must be positive and finite")

    def operating_config(self) -> dict[str, Any]:
        return {"version": VERSION, "provider": "tokenmetro", "api_surface": "chat_completions",
                "base_url": self.base_url, "model": self.model_id,
                "max_tokens": self.max_output_tokens, "temperature": 0.0,
                "thinking": {"type": "disabled"}, "stream": False,
                "timeout_seconds": self.timeout_seconds, "max_attempts": 1}


def _bytes(path: Path, body: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())


class DeepSeekGenerationProvider:
    """One explicitly authorized finite campaign; completed requests replay locally."""

    def __init__(self, config: DeepSeekGenerationConfig, *, api_key: str,
                 authorization: str, opener: Any = None, mock: bool = False) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise GenerationConfigurationError("DeepSeek requires TOKENMETRO_API_KEY")
        if not isinstance(authorization, str) or not authorization.strip():
            raise GenerationConfigurationError("DeepSeek live calls require this campaign's explicit authorization")
        if opener is not None and not mock:
            raise GenerationConfigurationError("Injected HTTP openers are restricted to offline mock execution")
        if mock and opener is None:
            raise GenerationConfigurationError("Offline mock execution requires an injected opener")
        self.config, self._key, self.authorization = config, api_key, authorization
        self._opener, self.mock = opener, mock
        self.provider_network_calls = 0

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> DeepSeekGenerationProvider:
        def required(name: str) -> str:
            value = environment.get(name, "")
            if not value.strip():
                raise GenerationConfigurationError(f"DeepSeek requires {name}")
            return value
        try:
            config = DeepSeekGenerationConfig(
                run_root=Path(required("GENSHIN_DEEPSEEK_RUN_ROOT")),
                max_requests=int(required("GENSHIN_DEEPSEEK_MAX_REQUESTS")),
                max_tokens=int(required("GENSHIN_DEEPSEEK_MAX_TOKENS")),
                model_id=environment.get("GENSHIN_DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL),
                base_url=environment.get("TOKENMETRO_BASE_URL", DEEPSEEK_BASE_URL),
                max_output_tokens=int(environment.get("GENSHIN_DEEPSEEK_MAX_OUTPUT_TOKENS", "1024")),
                timeout_seconds=float(environment.get("GENSHIN_DEEPSEEK_TIMEOUT_SECONDS", "120")),
            )
        except (TypeError, ValueError) as exc:
            raise GenerationConfigurationError("DeepSeek campaign configuration is missing or invalid") from exc
        return cls(config, api_key=required("TOKENMETRO_API_KEY"),
                   authorization=required("GENSHIN_DEEPSEEK_LIVE_AUTHORIZATION"))

    def invocation_payload(self, request: GenerationRequest) -> dict[str, Any]:
        return {"model": self.config.model_id, "stream": False,
                "messages": generation_messages(request), "temperature": 0.0,
                "max_tokens": self.config.max_output_tokens, "thinking": {"type": "disabled"}}

    def _assess(self, body: bytes, result: dict[str, Any]) -> None:
        obj = strict_json(body.decode("utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("INVALID_RESPONSE_OBJECT")
        usage = obj.get("usage")
        result["provider_reported_usage"] = usage if isinstance(usage, dict) else "UNKNOWN"
        if isinstance(usage, dict):
            normalized = {"input_tokens": usage.get("prompt_tokens"),
                          "output_tokens": usage.get("completion_tokens"), "total_tokens": usage.get("total_tokens")}
            counts = list(normalized.values())
            if all(type(n) is int and n >= 0 for n in counts) and counts[0] + counts[1] == counts[2]:
                result["usage"] = normalized
        if result["http_status"] != 200:
            raise ValueError("HTTP_REJECTED")
        if obj.get("model") != self.config.model_id or obj.get("object") != "chat.completion" or not isinstance(obj.get("id"), str) or not obj["id"] or obj.get("error"):
            raise ValueError("RESPONSE_IDENTITY_INVALID")
        choices = obj.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or choices[0].get("index") != 0:
            raise ValueError("AMBIGUOUS_CHOICE")
        choice = choices[0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("OUTPUT_TRUNCATED_OR_INVALID_FINISH")
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant" or message.get("tool_calls") or message.get("refusal"):
            raise ValueError("UNSUPPORTED_MESSAGE")
        text, reasoning = message.get("content"), message.get("reasoning_content")
        if isinstance(reasoning, str):
            result["reasoning_text"] = reasoning
        if not isinstance(text, str) or not text.strip():
            raise ValueError("EMPTY_ANSWER")
        if not isinstance(result["usage"], dict):
            raise ValueError("USAGE_INVALID_OR_MISSING")
        if result["usage"]["output_tokens"] > self.config.max_output_tokens:
            raise ValueError("OUTPUT_LIMIT_EXCEEDED")
        details = usage.get("completion_tokens_details") or {}
        if reasoning or message.get("thinking") or message.get("reasoning") or details.get("reasoning_tokens", 0):
            raise ValueError("THINKING_DISABLE_NOT_OBSERVED")
        result.update(final_text=text, provider_request_id=obj["id"])
        result["protocol_validation"] = {"status": "PASS"}

    def _attempt(self, root: Path, plan: dict[str, Any], request: GenerationRequest) -> dict[str, Any]:
        write(root / "plan.json", plan)
        body = canonical_json_bytes(plan["wire"])
        _bytes(root / "request.body.bin", body)
        write(root / "request-metadata.json", {"url": self.config.base_url + "/chat/completions",
              "method": "POST", "transport_retries": 0, "redirects": False, "trust_env": False})
        result: dict[str, Any] = {"state": "UNKNOWN", "usage": "UNKNOWN", "provider_reported_usage": "UNKNOWN",
            "http_status": None, "calls": 0, "actual_fee": "UNKNOWN", "error_codes": [],
            "protocol_validation": {"status": "NOT_COMPLETED"}, "content_validation": {"status": "NOT_COMPLETED"},
            "stream_validation": {"status": "NOT_APPLICABLE_NONSTREAM"}, "final_text": ""}
        response = None
        body_complete = False
        started = time.monotonic()
        try:
            # The same bytes are archived before dispatch; credentials exist only in memory.
            req = Request(self.config.base_url + "/chat/completions", data=body, method="POST",
                          headers={"Authorization": "Bearer " + self._key,
                                   "Content-Type": "application/json", "Accept-Encoding": "identity"})
            opener = self._opener or build_opener(ProxyHandler({}), _RejectRedirectHandler())
            self.provider_network_calls += 1
            result["calls"] = 1
            try:
                response = opener.open(req, timeout=self.config.timeout_seconds)
            except HTTPError as exc:
                response = exc  # Archive even failed HTTP responses before parsing.
            result["http_status"] = response.status if hasattr(response, "status") else response.code
            headers = {k: response.headers[k] for k in ("content-type", "content-encoding", "x-request-id", "retry-after") if k in response.headers}
            write(root / "http.json", {"status": result["http_status"], "headers": headers})
            size = 0
            with (root / "response.body.raw").open("xb") as handle:
                try:
                    while chunk := response.read(65536):
                        handle.write(chunk)
                        size += len(chunk)
                        if size > _RESPONSE_LIMIT:
                            raise ValueError("RESPONSE_SIZE_LIMIT")
                finally:
                    handle.flush()
                    os.fsync(handle.fileno())
            body_complete = True
            if headers.get("content-encoding", "identity") not in ("", "identity"):
                raise ValueError("UNSUPPORTED_CONTENT_ENCODING")
            self._assess((root / "response.body.raw").read_bytes(), result)
            validation = validate_citations(result["final_text"], request)
            result["citation_validation"] = validation.to_dict()
            if validation.integrity != "pass" or validation.coverage not in {"pass", "not_applicable"}:
                raise ValueError("CITATION_VALIDATION_FAILED")
            result["content_validation"] = {"status": "PASS"}
            result["state"] = "CONTENT_VERIFIED"
        except Exception as exc:
            result["failure_type"] = type(exc).__name__
            # Never persist exception messages that may contain credentials or provider echoes.
            known_codes = {"INVALID_RESPONSE_OBJECT", "HTTP_REJECTED", "RESPONSE_IDENTITY_INVALID", "AMBIGUOUS_CHOICE",
                           "OUTPUT_TRUNCATED_OR_INVALID_FINISH", "UNSUPPORTED_MESSAGE", "EMPTY_ANSWER",
                           "USAGE_INVALID_OR_MISSING", "OUTPUT_LIMIT_EXCEEDED", "THINKING_DISABLE_NOT_OBSERVED",
                           "RESPONSE_SIZE_LIMIT", "UNSUPPORTED_CONTENT_ENCODING", "CITATION_VALIDATION_FAILED"}
            result["error_codes"].append(str(exc) if type(exc) is ValueError and str(exc) in known_codes else type(exc).__name__)
            if result["http_status"] is None:
                result["request_count"] = "UNKNOWN"
            elif not body_complete:
                result["stream_validation"] = {"status": "FAIL", "reason": "INCOMPLETE_HTTP_BODY"}
            result["state"] = "PROTOCOL_OR_CONTENT_INVALID" if result["http_status"] is not None else "UNKNOWN"
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception as exc:
                    result["close_failure_type"] = type(exc).__name__
        result["elapsed_seconds"] = time.monotonic() - started
        _bytes(root / "reasoning.txt", result.pop("reasoning_text", "").encode("utf-8"))
        write(root / "trace.json", {"events": []})  # No claimed proof of pre-send non-transmission.
        write(root / "result.json", result)
        seal(root)
        return result

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if not isinstance(request, GenerationRequest):
            raise GenerationContractError("DeepSeek provider requires a GenerationRequest")
        wire = self.invocation_payload(request)
        config = self.config.operating_config()
        fp = digest([VERSION, self.config.base_url, wire, request.semantic_request_identity, self.mock])
        plan = {"version": VERSION, "fingerprint": fp, "wire": wire,
                "operating_config": config, "authorization": self.authorization,
                "semantic_request_identity": request.semantic_request_identity,
                "adapter_sha256": sha(Path(__file__)), "generation_sha256": sha(Path(__file__).with_name("generation.py")),
                "backend": "MOCK_NOT_MODEL_OUTPUT" if self.mock else "LIVE_NOT_HUMAN_REVIEWED"}
        root = self.config.run_root / "requests" / fp
        replayed = (root / "complete.json").exists()
        previous_calls = self.provider_network_calls
        ledger = self.config.run_root / "mock-ledger" if self.mock else DEEPSEEK_LEDGER
        batch = model_calls.Batch(self.config.run_root / "budget", max_requests=self.config.max_requests, max_tokens=self.config.max_tokens)
        result = model_calls.run(root, plan, ledger, lambda folder, _: self._attempt(folder, plan, request),
                                 channel=["tokenmetro", "chat_completions", self.config.model_id],
                                 policy=model_calls.Policy(max_attempts=1), batch=batch,
                                 token_allowance=len(canonical_json_bytes(wire)) + self.config.max_output_tokens)
        ok = result["state"] == "CONTENT_VERIFIED"
        validation = validate_citations(result["final_text"], request) if ok else None
        return GenerationResult(
            execution_status="succeeded" if ok else "provider_error" if result.get("http_status") != 200 else "response_invalid",
            answer_text=result["final_text"] if ok else None, citation_validation=validation,
            semantic_request_identity=request.semantic_request_identity, execution_config_identity=digest(config),
            request_audit=request.audit_projection(), provider_audit={
                "provider_id": "tokenmetro_deepseek", "control_config": config,
                "authorization": self.authorization, "raw_archive": str(root),
                "replayed": replayed, "new_transport_attempts": self.provider_network_calls - previous_calls,
                "raw_archive_sha256": sha(root / "complete.json"), "accounting": result["accounting"],
                "attempts": [{"status_code": result.get("http_status"), "usage": result.get("usage"),
                              "error_codes": result.get("error_codes", [])}],
                "authority": plan["backend"], "semantic_faithfulness": "not_evaluated"})
