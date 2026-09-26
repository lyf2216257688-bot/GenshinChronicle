"""Explicit Phase 05 provider route profiles.

The semantic compiler owns neither provider selection nor fallback policy.  The
small profiles in this module are the persisted, secret-free description of the
two OpenAI-compatible DeepSeek routes used by the SDK runner.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping
from urllib.parse import urlsplit

from genshin_corpus.canonical.fingerprints import sha256_json

TOKENMETRO_BASE_URL = "https://tokenmetro.com/v1"
JIZHI_BASE_URL = "https://jizhiapi.site/v1"
TOKENMETRO_MODEL_IDS = {
    "gemini": "gemini-3.8-flash",
    "deepseek": "deepseek-v4.1-flash",
    "glm": "glm-5.3-flash",
}


@dataclass(frozen=True)
class SemanticRouteProfile:
    """A runtime route description that never contains a credential."""

    route: str
    provider: str
    model_id: str
    base_url: str
    base_url_env: str
    api_key_env: str
    api_surface: str = "chat_completions"

    def __post_init__(self) -> None:
        if self.route not in {"tokenmetro", "jizhi"}:
            raise ValueError("unsupported semantic route")
        if self.api_surface != "chat_completions":
            raise ValueError("only the Chat Completions route is currently authorized")
        if not self.model_id or not self.provider:
            raise ValueError("route provider and model are required")
        parsed = urlsplit(self.base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("route base URL must be an explicit URL without credentials, query, or fragment")
        if not self.base_url_env or not self.api_key_env:
            raise ValueError("route environment variable names are required")

    @property
    def config_identity(self) -> str:
        return sha256_json(self.safe_dict())

    def safe_dict(self) -> dict[str, str]:
        return {
            "route": self.route,
            "provider": self.provider,
            "model_id": self.model_id,
            "base_url": self.base_url,
            "base_url_env": self.base_url_env,
            "api_key_env": self.api_key_env,
            "api_surface": self.api_surface,
        }


ROUTE_PROFILES: Mapping[str, SemanticRouteProfile] = {
    "tokenmetro": SemanticRouteProfile(
        route="tokenmetro", provider="TokenMetro", model_id="deepseek-v4.1-flash",
        base_url=TOKENMETRO_BASE_URL, base_url_env="TOKENMETRO_BASE_URL",
        api_key_env="TOKENMETRO_API_KEY",
    ),
    "jizhi": SemanticRouteProfile(
        route="jizhi", provider="Jizhi", model_id="deepseek-v4.1-flash",
        base_url=JIZHI_BASE_URL, base_url_env="JIZHI_BASE_URL",
        api_key_env="JIZHI_API_KEY",
    ),
}


def route_profile(route: str, environment: Mapping[str, str] | None = None, *, require_environment: bool = False) -> SemanticRouteProfile:
    """Resolve a route using runtime URL configuration without reading secrets."""

    try:
        template = ROUTE_PROFILES[route]
    except KeyError as exc:
        raise ValueError(f"unsupported semantic route: {route}") from exc
    values = os.environ if environment is None else environment
    configured = values.get(template.base_url_env)
    if configured is None or configured == "":
        if require_environment:
            raise ValueError(f"{template.base_url_env} is required for route {route}")
        configured = template.base_url
    return SemanticRouteProfile(
        route=template.route, provider=template.provider, model_id=template.model_id,
        base_url=configured.rstrip("/"), base_url_env=template.base_url_env,
        api_key_env=template.api_key_env, api_surface=template.api_surface,
    )


def tokenmetro_model_id(name: str) -> str:
    try:
        return TOKENMETRO_MODEL_IDS[name]
    except KeyError as exc:
        raise ValueError(f"unsupported TokenMetro model: {name}") from exc


__all__ = [
    "JIZHI_BASE_URL", "ROUTE_PROFILES", "SemanticRouteProfile", "TOKENMETRO_BASE_URL",
    "TOKENMETRO_MODEL_IDS", "route_profile", "tokenmetro_model_id",
]
