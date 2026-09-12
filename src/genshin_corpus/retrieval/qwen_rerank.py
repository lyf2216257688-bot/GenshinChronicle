"""DashScope adapter for the provider-neutral reranking contract."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import math
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json

from .reranking import RerankRequest, RerankScore, RerankingError, Reranker


QWEN_RERANK_MODEL_ID = "qwen3.7-text-rerank"
DASHSCOPE_QWEN_RERANK_TRANSPORT_VERSION = "phase04-rag-qwen37-dashscope-rerank-0.1"
_PATH = "/api/v1/services/rerank/text-rerank/text-rerank"
_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


class QwenRerankTransportError(RerankingError):
    def __init__(self, code: str, *, status_code: int | None = None, request_id: str | None = None, raw_response: bytes = b"") -> None:
        super().__init__(code)
        self.code, self.status_code, self.request_id, self.raw_response = code, status_code, request_id, raw_response


@dataclass(frozen=True)
class DashScopeQwenRerankConfig:
    region: str
    endpoint: str
    workspace: str
    timeout_seconds: float = 60.0
    api_key_env: str = "DASHSCOPE_API_KEY"

    def __post_init__(self) -> None:
        if self.region != "cn-beijing" or not _SAFE.fullmatch(self.workspace):
            raise QwenRerankTransportError("InvalidConfiguration")
        if not _SAFE.fullmatch(self.api_key_env) or not math.isfinite(float(self.timeout_seconds)) or self.timeout_seconds <= 0:
            raise QwenRerankTransportError("InvalidConfiguration")
        self.normalized_endpoint()

    def normalized_endpoint(self) -> str:
        parsed = urlsplit(self.endpoint)
        expected = f"{self.workspace}.{self.region}.maas.aliyuncs.com".lower()
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != expected or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None, 443) or parsed.path.rstrip("/") != _PATH:
            raise QwenRerankTransportError("InvalidConfiguration")
        return urlunsplit(("https", expected, _PATH, "", ""))

    def identity_projection(self) -> dict[str, Any]:
        return {"provider": "dashscope", "transport_contract_version": DASHSCOPE_QWEN_RERANK_TRANSPORT_VERSION, "model": QWEN_RERANK_MODEL_ID, "endpoint": self.normalized_endpoint()}

    @property
    def configuration_identity(self) -> str:
        return sha256_json(self.identity_projection())


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


class DashScopeQwenRerankTransport:
    def __init__(self, config: DashScopeQwenRerankConfig, api_key: str, *, opener: Any | None = None) -> None:
        if not api_key:
            raise QwenRerankTransportError("MissingCredential")
        self.config, self.api_key = config, api_key
        self.last_response_metadata: dict[str, Any] = {}
        self._opener = opener or build_opener(_RejectRedirectHandler())

    @classmethod
    def from_environment(cls, config: DashScopeQwenRerankConfig, *, environment: Mapping[str, str] | None = None, opener: Any | None = None) -> "DashScopeQwenRerankTransport":
        values = os.environ if environment is None else environment
        return cls(config, values.get(config.api_key_env, ""), opener=opener)

    @staticmethod
    def wire_payload(request: RerankRequest) -> dict[str, Any]:
        if not isinstance(request, RerankRequest):
            raise QwenRerankTransportError("InvalidRequest")
        return {"model": QWEN_RERANK_MODEL_ID, "input": {"query": request.query, "documents": [row.text for row in request.candidates]}, "parameters": {"return_documents": False}}

    def rerank(self, request: RerankRequest) -> tuple[RerankScore, ...]:
        body = canonical_json_bytes(self.wire_payload(request))
        req = Request(self.config.normalized_endpoint(), data=body, method="POST", headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self._opener.open(req, timeout=float(self.config.timeout_seconds)) as response:
                raw, status, headers = response.read(), getattr(response, "status", response.getcode()), response.headers
        except HTTPError as exc:
            raw = exc.read()
            raise QwenRerankTransportError("HTTPError", status_code=exc.code, request_id=exc.headers.get("x-acs-request-id") if exc.headers else None, raw_response=raw) from None
        except URLError:
            raise QwenRerankTransportError("TransportConnectionError") from None
        request_id = None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise QwenRerankTransportError("MalformedResponse", status_code=status, raw_response=raw) from None
        if not isinstance(payload, Mapping):
            raise QwenRerankTransportError("MalformedResponse", status_code=status, raw_response=raw)
        if isinstance(payload.get("request_id"), str):
            request_id = payload["request_id"]
        self.last_response_metadata = {
            "request_id": request_id,
            "usage": dict(payload["usage"]) if isinstance(payload.get("usage"), Mapping) else None,
            "model": payload.get("model") if isinstance(payload.get("model"), str) else QWEN_RERANK_MODEL_ID,
            "status_code": status,
        }
        output = payload.get("output")
        results = output.get("results") if isinstance(output, Mapping) else None
        if not isinstance(results, list) or len(results) != len(request.candidates):
            raise QwenRerankTransportError("MalformedResponse", status_code=status, request_id=request_id, raw_response=raw)
        scores: list[RerankScore] = []
        for item in results:
            if not isinstance(item, Mapping) or not isinstance(item.get("index"), int) or isinstance(item.get("index"), bool) or not isinstance(item.get("relevance_score"), (int, float)):
                raise QwenRerankTransportError("MalformedResponse", status_code=status, request_id=request_id, raw_response=raw)
            index = item["index"]
            if not 0 <= index < len(request.candidates):
                raise QwenRerankTransportError("MalformedResponse", status_code=status, request_id=request_id, raw_response=raw)
            scores.append(RerankScore(request.candidates[index].unit_id, index + 1, float(item["relevance_score"]), 0))
        from .reranking import stable_rank_scores
        return stable_rank_scores(request, scores)


def qwen_rerank_endpoint_from_bailian_base(base_url: str, workspace: str, region: str = "cn-beijing") -> str:
    """Derive the workspace-owned synchronous text-rerank endpoint."""
    _ = base_url
    return f"https://{workspace}.{region}.maas.aliyuncs.com{_PATH}"
