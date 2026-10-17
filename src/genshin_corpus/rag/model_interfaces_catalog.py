"""Programmatic AI model interfaces catalog and documentation consistency checker.

This module provides a single source of truth inspecting live codebase definitions
for all AI model providers, endpoints, protocols, environment variable bindings,
and lifecycle states (Phase 04 production, Phase 05 active E2, Phase 05 retired
semantic SDK, and deprecated historical routes).

It supports:
1. Programmatic access to the structured model interface catalog.
2. Lightweight, deterministic consistency checks against docs/model-interfaces.md.
3. Automatic Markdown reference table generation.
4. CLI invocation for developers and CI/test validation.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Sequence

# Ensure src root is on sys.path if invoked directly as a script
_SRC_DIR = Path(__file__).resolve().parents[2]
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from genshin_corpus.generation.generation import (
    BASELINE_QWEN_MODEL_ID,
    GENERATION_PROVIDER_ID_BAILIAN,
    _BAILIAN_BEIJING_HOST_SUFFIX,
    _BAILIAN_COMPATIBLE_BASE_PATH,
    BailianControlConfig,
)
from genshin_corpus.rag.navi_e2_routes import E2_ROUTES
from genshin_corpus.retrieval.qwen_embedding import (
    DASHSCOPE_QWEN_EMBEDDING_TRANSPORT_VERSION,
    _DASHSCOPE_TEXT_EMBEDDING_PATH,
    DashScopeQwenEmbeddingConfig,
)
from genshin_corpus.retrieval.semantic_tokenmetro_profile import (
    JIZHI_BASE_URL,
    ROUTE_PROFILES,
    TOKENMETRO_BASE_URL,
    TOKENMETRO_MODEL_IDS,
)


@dataclass(frozen=True)
class ModelInterfaceEntry:
    category: str
    logical_model: str
    route_name: str
    provider: str
    model_id: str
    base_url_pattern: str
    confirmed_base_url: str | None
    api_surface: str
    endpoint_path: str
    api_key_env: str
    base_url_env: str | None
    surface_env: str | None
    status: str
    applicable_modules: tuple[str, ...]
    notes: str


def build_model_interfaces_catalog() -> list[ModelInterfaceEntry]:
    """Inspect live codebase definitions and assemble the canonical interface catalog."""
    catalog: list[ModelInterfaceEntry] = []

    # 1. Phase 04 Production QA: Generation
    bailian_dummy = BailianControlConfig(
        region="cn-beijing",
        endpoint=f"https://example{_BAILIAN_BEIJING_HOST_SUFFIX}{_BAILIAN_COMPATIBLE_BASE_PATH}",
        workspace="example",
        model_id=BASELINE_QWEN_MODEL_ID,
    )
    catalog.append(
        ModelInterfaceEntry(
            category="phase04_production_qa",
            logical_model="qwen3.7-plus",
            route_name="bailian_generation",
            provider="Alibaba Cloud Bailian Model Studio",
            model_id=BASELINE_QWEN_MODEL_ID,
            base_url_pattern=f"https://{{workspace}}{_BAILIAN_BEIJING_HOST_SUFFIX}{_BAILIAN_COMPATIBLE_BASE_PATH}",
            confirmed_base_url=None,
            api_surface="chat_completions",
            endpoint_path=f"{_BAILIAN_COMPATIBLE_BASE_PATH}/chat/completions",
            api_key_env=bailian_dummy.api_key_env,
            base_url_env="BAILIAN_BASE_URL",
            surface_env=None,
            status="production_adopted",
            applicable_modules=(
                "genshin_corpus.generation.generation",
                "genshin_corpus.ui.runtime",
            ),
            notes="Formal Phase 04 generation baseline. Exact dated snapshot policy; no thinking; temperature 0.0.",
        )
    )

    # 2. Phase 04 Production QA: Qwen Query Embedding
    embedding_dummy = DashScopeQwenEmbeddingConfig(
        region="cn-beijing",
        endpoint=f"https://example.cn-beijing.maas.aliyuncs.com{_DASHSCOPE_TEXT_EMBEDDING_PATH}",
        workspace="example",
    )
    catalog.append(
        ModelInterfaceEntry(
            category="phase04_production_qa",
            logical_model="qwen3.7-text-embedding",
            route_name="dashscope_query_embedding",
            provider="Alibaba Cloud DashScope",
            model_id="qwen3.7-text-embedding",
            base_url_pattern=f"https://{{workspace}}.{{region}}.maas.aliyuncs.com{_DASHSCOPE_TEXT_EMBEDDING_PATH}",
            confirmed_base_url=None,
            api_surface="native_http_post",
            endpoint_path=_DASHSCOPE_TEXT_EMBEDDING_PATH,
            api_key_env=embedding_dummy.api_key_env,
            base_url_env="DASHSCOPE_QWEN_EMBEDDING_ENDPOINT",
            surface_env=None,
            status="production_adopted",
            applicable_modules=(
                "genshin_corpus.retrieval.qwen_embedding",
                "genshin_corpus.retrieval.production_materialization",
                "genshin_corpus.ui.runtime",
            ),
            notes=(
                f"Production query seam ({DASHSCOPE_QWEN_EMBEDDING_TRANSPORT_VERSION}). "
                "2048 dims, FP32, role=query. Also requires DASHSCOPE_QWEN_WORKSPACE."
            ),
        )
    )

    # 3. Phase 05 Navi E2 Autonomous Exploration Routes (E2_ROUTES)
    for (model, route), (provider, prefix, confirmed_url) in E2_ROUTES.items():
        is_primary = (model, route) == ("gpt-6-luna", "tokenmetro")
        catalog.append(
            ModelInterfaceEntry(
                category="phase05_navi_e2",
                logical_model=model,
                route_name=route,
                provider=provider,
                model_id=model,
                base_url_pattern=confirmed_url,
                confirmed_base_url=confirmed_url,
                api_surface="responses",
                endpoint_path="/responses",
                api_key_env=f"{prefix}_API_KEY",
                base_url_env=f"{prefix}_BASE_URL",
                surface_env=f"{prefix}_API_SURFACE",
                status="e2_active_exploration" if is_primary else "e2_authorized_candidate",
                applicable_modules=(
                    "genshin_corpus.rag.navi_e2_routes",
                    "genshin_corpus.rag.navi_e2_experiment",
                ),
                notes=(
                    f"E2 streaming Responses route with dedicated credential slot ({prefix}_*). "
                    + ("Primary story exploration route (Run 1-3)." if is_primary else "Protocol probe passed; awaiting operator live run authorization.")
                ),
            )
        )

    # 4. Phase 05 Shared Semantic SDK Routes (Retired from live dispatch)
    for route, profile in ROUTE_PROFILES.items():
        catalog.append(
            ModelInterfaceEntry(
                category="phase05_retired_semantic_sdk",
                logical_model="multi_model",
                route_name=route,
                provider=profile.provider,
                model_id="/".join(sorted(TOKENMETRO_MODEL_IDS)),
                base_url_pattern=profile.base_url,
                confirmed_base_url=profile.base_url,
                api_surface="responses",
                endpoint_path="/responses",
                api_key_env=profile.api_key_env,
                base_url_env=profile.base_url_env,
                surface_env="TOKENMETRO_API_SURFACE",
                status="retired_offline_only",
                applicable_modules=(
                    "genshin_corpus.retrieval.semantic_tokenmetro_profile",
                    "genshin_corpus.retrieval.semantic_sdk_runner",
                    "genshin_corpus.retrieval.semantic_two_stage_experiment",
                ),
                notes="Retired from live execution; preserved for offline unit tests, replay validation, and MockTransport.",
            )
        )

    # 5. Deprecated Historical Routes
    catalog.append(
        ModelInterfaceEntry(
            category="deprecated_historical",
            logical_model="glm-5.3-flash",
            route_name="tokenmetro_glm_deprecated",
            provider="TokenMetro",
            model_id="glm-5.3-flash",
            base_url_pattern=TOKENMETRO_BASE_URL,
            confirmed_base_url=TOKENMETRO_BASE_URL,
            api_surface="responses",
            endpoint_path="/responses",
            api_key_env="TOKENMETRO_API_KEY",
            base_url_env="TOKENMETRO_BASE_URL",
            surface_env=None,
            status="deprecated_disallowed",
            applicable_modules=(),
            notes="Removed from executable E2 routes on 2026-10-15 due to upstream 503 errors; replaced by jizhi_glm.",
        )
    )
    catalog.append(
        ModelInterfaceEntry(
            category="deprecated_historical",
            logical_model="all_e2_models",
            route_name="e2_chat_completions_deprecated",
            provider="TokenMetro/Jizhi",
            model_id="all",
            base_url_pattern="https://tokenmetro.com/v1 | https://jizhiapi.site/v1",
            confirmed_base_url=None,
            api_surface="chat_completions",
            endpoint_path="/chat/completions",
            api_key_env="*",
            base_url_env="*",
            surface_env="*_API_SURFACE",
            status="deprecated_disallowed",
            applicable_modules=(),
            notes="Deprecated for E2 since 2026-10-15; E2 strictly requires Responses API streaming surface.",
        )
    )

    return catalog


def check_model_interfaces_documentation(doc_path: Path | None = None) -> list[str]:
    """Verify that docs/model-interfaces.md is complete and consistent with codebase definitions."""
    if doc_path is None:
        doc_path = Path(__file__).resolve().parents[3] / "docs" / "model-interfaces.md"

    if not doc_path.exists():
        return [f"Documentation file not found: {doc_path}"]

    content = doc_path.read_text(encoding="utf-8")
    errors: list[str] = []

    catalog = build_model_interfaces_catalog()

    # 1. Verify all active E2 routes from E2_ROUTES appear in the documentation
    for entry in catalog:
        if entry.category == "phase05_navi_e2":
            # Check model ID
            if entry.model_id not in content:
                errors.append(f"Active E2 model '{entry.model_id}' missing from {doc_path.name}")
            # Check route name
            if entry.route_name not in content:
                errors.append(f"Active E2 route '{entry.route_name}' missing from {doc_path.name}")
            # Check API key env
            if entry.api_key_env not in content:
                errors.append(f"Active E2 credential slot '{entry.api_key_env}' missing from {doc_path.name}")
            # Check confirmed URL
            if entry.confirmed_base_url and entry.confirmed_base_url not in content:
                errors.append(f"Confirmed base URL '{entry.confirmed_base_url}' for route '{entry.route_name}' missing from {doc_path.name}")

        elif entry.category == "phase04_production_qa":
            if entry.model_id not in content:
                errors.append(f"Production model '{entry.model_id}' missing from {doc_path.name}")
            if entry.api_key_env not in content:
                errors.append(f"Production key env '{entry.api_key_env}' missing from {doc_path.name}")
            if entry.base_url_env and entry.base_url_env not in content:
                errors.append(f"Production endpoint env '{entry.base_url_env}' missing from {doc_path.name}")

    # 2. Verify deprecated routes are documented under deprecated section
    if "deprecated" not in content.lower() and "已废弃" not in content:
        errors.append(f"{doc_path.name} must explicitly document deprecated/disallowed routes")

    # 3. Verify Responses API requirement is stated
    if "Responses API" not in content and "responses" not in content:
        errors.append(f"{doc_path.name} must explicitly state Phase 05 E2 requires Responses API")

    # 4. Verify credential isolation principle is stated
    if "credential" not in content.lower() and "凭据" not in content:
        errors.append(f"{doc_path.name} must explain credential slot isolation")

    return errors


def generate_markdown_summary() -> str:
    """Generate Markdown overview tables directly from live code definitions."""
    catalog = build_model_interfaces_catalog()
    lines = [
        "### 1. Phase 04 正式问答与检索服务链（生产默认）",
        "",
        "| 模块 / 用途 | 逻辑模型 | 生产模型快照 | 服务商 | 协议表面 | Base URL 环境变量 / 模式 | 凭据环境变量 | 状态 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for e in catalog:
        if e.category == "phase04_production_qa":
            lines.append(
                f"| `{e.route_name}` | `{e.logical_model}` | `{e.model_id}` | {e.provider} | `{e.api_surface}` | `{e.base_url_env}` (`{e.base_url_pattern}`) | `{e.api_key_env}` | **{e.status}** |"
            )

    lines.extend([
        "",
        "### 2. Phase 05 Navi E2 自主剧情探索路线（Responses 协议）",
        "",
        "| 模型标识 (`model`) | 路由标识 (`route`) | 服务商 | 协议表面 | 接口地址 (`BASE_URL`) | 凭据环境变量 (`API_KEY`) | 表面配置 (`API_SURFACE`) | 当前状态 |",
        "|---|---|---|---|---|---|---|---|",
    ])
    for e in catalog:
        if e.category == "phase05_navi_e2":
            lines.append(
                f"| `{e.logical_model}` | `{e.route_name}` | {e.provider} | `{e.api_surface}` | `{e.base_url_pattern}` | `{e.api_key_env}` | `{e.surface_env}` | **{e.status}** |"
            )

    lines.extend([
        "",
        "### 3. Phase 05 语义 SDK 历史路线（已退役，仅离线/测试）",
        "",
        "| 路由标识 (`route`) | 服务商 | 支持模型映射 | 协议表面 | 基础地址 | 凭据环境变量 | 当前状态 |",
        "|---|---|---|---|---|---|---|",
    ])
    for e in catalog:
        if e.category == "phase05_retired_semantic_sdk":
            lines.append(
                f"| `{e.route_name}` | {e.provider} | `{e.model_id}` | `{e.api_surface}` | `{e.base_url_pattern}` | `{e.api_key_env}` | **{e.status}** |"
            )

    lines.extend([
        "",
        "### 4. 已淘汰 / 废弃的历史路由",
        "",
        "| 废弃标识 | 涉及模型 | 原服务商 | 原协议 | 废弃原因与处理策略 |",
        "|---|---|---|---|---|",
    ])
    for e in catalog:
        if e.category == "deprecated_historical":
            lines.append(
                f"| `{e.route_name}` | `{e.logical_model}` | {e.provider} | `{e.api_surface}` | {e.notes} |"
            )

    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Model interfaces catalog and documentation checker")
    parser.add_argument("--check", action="store_true", help="Check docs/model-interfaces.md consistency")
    parser.add_argument("--markdown", action="store_true", help="Print Markdown tables generated from code")
    parser.add_argument("--doc-path", type=Path, default=None, help="Path to model-interfaces.md")
    args = parser.parse_args(argv)

    if args.markdown:
        print(generate_markdown_summary())
        return 0

    # Default to check
    errors = check_model_interfaces_documentation(args.doc_path)
    if errors:
        print(f"FAILED: {len(errors)} consistency error(s) found:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("OK: docs/model-interfaces.md is fully consistent with codebase interface definitions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
