"""Live candidate V semantic research using verified DeepSeek Responses route.

Operator-authorized paid semantic research unit (2026-10-20).
No KG, no full-corpus build, no production change, no git commit/push.
All calls strictly audited with token accounting and cost tracking.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Mapping

from genshin_corpus.canonical.fingerprints import sha256_json, canonical_json_bytes
from genshin_corpus.retrieval.clue_state import VALID_GENRES, VALID_EPISTEMIC
from genshin_corpus.retrieval.navi_reading import VerifiedReadingSources, build_release_catalog, HYPOTHESIS, SCHEMA
from genshin_corpus.retrieval.navi_exploration import LocalExplorationTools, ExplorationError, response_chars
from genshin_corpus.rag.navi_v_experiment import ReadingSession, ReadingBudget, _once
from genshin_corpus.rag.navi_e2_experiment import E2SDK

# Pricing for deepseek-v4.1-flash via Jizhi (RMB per 1M tokens)
PRICE_INPUT_PER_1M = 0.14
PRICE_OUTPUT_PER_1M = 0.28


def load_codex_env() -> dict[str, str]:
    codex_env_path = Path(r"C:\Users\Lyf22\.codex\.env")
    env = dict(os.environ)
    if codex_env_path.exists():
        for line in codex_env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def calculate_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * PRICE_INPUT_PER_1M + output_tokens * PRICE_OUTPUT_PER_1M) / 1_000_000.0


def extract_json_block(text: str) -> dict[str, Any]:
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        try:
            return json.loads(text[first:last + 1])
        except Exception:
            pass
    return json.loads(text)


class LiveResearchRunner:
    def __init__(self, root: Path, sources: VerifiedReadingSources, catalog: dict[str, Any], env: dict[str, str],
                 *, mock_client: Any | None = None, max_budget_rmb: float = 0.10, max_tokens: int = 1_000_000):
        self.root = root
        self.sources = sources
        self.catalog = catalog
        self.env = env
        self.rounds_dir = self.root / "rounds"
        self.rounds_dir.mkdir(parents=True, exist_ok=True)
        self.mock_client = mock_client
        if mock_client is not None:
            self.client = mock_client
        else:
            self.client = E2SDK(route="jizhi", model="deepseek-v4.1-flash", environment=env, format_retry_enabled=False)
        self.cumulative_tokens = 0
        self.cumulative_input = 0
        self.cumulative_output = 0
        self.cumulative_cost = 0.0
        self.max_budget_rmb = max_budget_rmb
        self.max_tokens = max_tokens
        self.call_records: list[dict[str, Any]] = []

    def call_model(self, round_name: str, purpose: str, instructions: str, user_input: str) -> tuple[dict[str, Any], str]:
        stem = self.rounds_dir / round_name
        stem.mkdir(parents=True, exist_ok=False)
        run_identity = self.root.name

        body = {
            "model": "deepseek-v4.1-flash",
            "instructions": instructions,
            "input": user_input,
            "stream": True,
            "max_output_tokens": 4096,
        }

        print(f"\n[AI Call: {round_name}] Purpose: {purpose}", flush=True)
        print(f"Pre-call budget check: Cumulative tokens={self.cumulative_tokens}, Estimated Cost={self.cumulative_cost:.6f} RMB", flush=True)

        if self.cumulative_tokens >= self.max_tokens:
            raise ExplorationError(f"{self.max_tokens} Token budget reached")
        if self.cumulative_cost >= self.max_budget_rmb:
            raise ExplorationError(f"Cost ceiling reached ({self.max_budget_rmb:.2f} RMB)")

        terminal = self.client(stem, body, run_identity)
        if not terminal.get("complete"):
            raise ExplorationError(f"Model call failed in {round_name}: {terminal.get('error')}")

        usage = terminal.get("usage", {})
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        total = inp + out
        round_cost = calculate_cost(inp, out)

        self.cumulative_input += inp
        self.cumulative_output += out
        self.cumulative_tokens += total
        self.cumulative_cost += round_cost

        record = {
            "round": round_name,
            "purpose": purpose,
            "input_tokens": inp,
            "output_tokens": out,
            "total_tokens": total,
            "estimated_round_cost_rmb": round_cost,
            "cumulative_tokens": self.cumulative_tokens,
            "cumulative_estimated_cost_rmb": self.cumulative_cost,
            "cost_accounting_basis": "estimated_from_preset_rates",
            "latency_ms": terminal.get("latency_ms"),
            "model": terminal.get("wire_model"),
        }
        self.call_records.append(record)
        _once(stem / "round-accounting.json", record)

        print(f"[{round_name} Finished] Tokens: +{total} (inp={inp}, out={out}), Est. Cost: {round_cost:.6f} RMB, Cumulative Est. Cost: {self.cumulative_cost:.6f} RMB", flush=True)
        return terminal, terminal.get("content", "")


def build_final_report(
    session: ReadingSession,
    runner: LiveResearchRunner,
    output_root: Path,
    seed_receipt: dict[str, Any],
    hits: list[dict[str, Any]],
    data_r2: dict[str, Any],
    *,
    chosen_hit: dict[str, Any] | None = None,
    target_receipt: dict[str, Any] | None = None,
    action: str | None = None,
    closed_loop_verified: bool = False,
    rejection_reason: str | None = None,
) -> dict[str, Any]:
    coverage = session.coverage_summary()

    # Verify resume consistency
    resumed = ReadingSession.resume(runner.sources, runner.catalog, output_root / "session")
    resume_ok = (resumed.state == session.state)

    reading_receipts = [
        {
            "receipt_id": r["receipt_id"],
            "source": r["source"],
            "record_title": r["record_title"],
            "research_status": r["research_status"],
            "partial_batch": r.get("partial_batch", False),
            "unit_count": len(r.get("units", [])),
            "total_chars": sum(len(u.get("text", "")) for u in r.get("units", [])),
            "units": [
                {
                    "section_name": u.get("section_name", ""),
                    "section_ordinal": u.get("section_ordinal"),
                    "canonical_unit_ordinal": u.get("canonical_unit_ordinal"),
                    "unit_ids": u.get("unit_ids", []),
                    "char_count": len(u.get("text", "")),
                    "text_sha256": u.get("text_sha256"),
                    "parent_component": u.get("parent_component_key", ""),
                }
                for u in r.get("units", [])
            ],
        }
        for r in session.state["receipts"]
    ]

    candidates_view = [
        {
            "candidate_index": h["rank"],
            "unit_id": h["unit_id"],
            "content_id": h["canonical_address"]["source_identity_key"].split(":")[-1],
            "record_title": h["record_context"]["record_title"],
            "section_name": h["record_context"]["section_name"],
            "unit_kind": h["canonical_address"]["canonical_unit_kind"],
            "score": round(h["retrieval"]["score"], 4),
            "snippet": h["snippet"][:200],
        }
        for h in hits
    ]

    manifest_mode = session.manifest.get("decision_mode")
    if not isinstance(manifest_mode, str) or not manifest_mode.strip():
        report_decision_mode = "injected_recorded_or_fixture_decisions_only"
    else:
        report_decision_mode = manifest_mode.strip()

    if getattr(runner, "mock_client", None) is not None and report_decision_mode == "live_model_decisions":
        raise ExplorationError("mock_client run must not be marked as live_model_decisions")

    final_report = {
        "schema_version": "phase05-navi-v-live-research-0.2",
        "run_identity": output_root.name,
        "decision_mode": report_decision_mode,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model_route": {
            "model": "deepseek-v4.1-flash",
            "route": "jizhi",
            "base_url": "https://jizhiapi.site/v1",
        },
        "pricing": {
            "accounting_basis": "estimated_from_preset_rates",
            "basis_note": "费用系按预置基准单价（输入0.14/1M, 输出0.28/1M RMB）推算之估算值，非服务商真实对账凭证",
            "price_input_per_1m_rmb": PRICE_INPUT_PER_1M,
            "price_output_per_1m_rmb": PRICE_OUTPUT_PER_1M,
            "cumulative_tokens": runner.cumulative_tokens,
            "cumulative_input_tokens": runner.cumulative_input,
            "cumulative_output_tokens": runner.cumulative_output,
            "estimated_cost_rmb": round(runner.cumulative_cost, 6),
        },
        "calls": runner.call_records,
        "seed_task": {
            "source": seed_receipt["source"],
            "title": seed_receipt.get("record_title", "龙蛇藏归辑录"),
            "delivered_chars": sum(len(u["text"]) for u in seed_receipt.get("units", [])),
            "delivered_unit_ids": [uid for u in seed_receipt.get("units", []) for uid in u.get("unit_ids", [])],
        },
        "search_and_candidates": {
            "query": hits[0].get("query", "《日月前事》") if hits else data_r2.get("decision", {}).get("query", "《日月前事》"),
            "hit_count": len(hits),
            "candidates": candidates_view,
            "model_evaluations": data_r2.get("evaluations", []),
            "model_decision": data_r2.get("decision", {}),
        },
        "reading_receipts": reading_receipts,
        "reading_and_understanding": {
            "closed_loop_verified": closed_loop_verified,
            "rejection_reason": rejection_reason,
            "rejections_count": len(session.state.get("rejections", [])),
            "rejections": session.state.get("rejections", []),
            "chosen_hit": {
                "rank": chosen_hit["rank"] if chosen_hit else None,
                "record_title": chosen_hit["record_context"]["record_title"] if chosen_hit else None,
                "section_name": chosen_hit["record_context"]["section_name"] if chosen_hit else None,
                "unit_id": chosen_hit["unit_id"] if chosen_hit else None,
            } if chosen_hit else None,
            "delivered_receipt": target_receipt.get("receipt_id") if target_receipt else None,
            "final_summary": session.state["continuity"]["research_summary"],
            "active_clues_count": len(session.state["continuity"]["active_clues"]),
            "unresolved_questions": session.state.get("pending_questions", []),
        },
        "coverage_summary": coverage,
        "resume_verified": resume_ok,
    }

    _once(output_root / "live_research_report.json", final_report)
    print("\n" + "=" * 70, flush=True)
    print("RESEARCH EXPERIMENT RUN AUDIT SUMMARY", flush=True)
    print(f"Total Calls: {len(runner.call_records)}", flush=True)
    print(f"Total Tokens: {runner.cumulative_tokens} (inp={runner.cumulative_input}, out={runner.cumulative_output})", flush=True)
    print(f"Estimated Cost: {runner.cumulative_cost:.6f} RMB (accounting basis: estimated_from_preset_rates)", flush=True)
    print(f"Closed Loop Verified: {closed_loop_verified}", flush=True)
    if rejection_reason:
        print(f"Rejection / Stop Reason: {rejection_reason}", flush=True)
    print(f"Resume Verified: {resume_ok}", flush=True)
    print(f"Receipts Audited: {len(reading_receipts)}", flush=True)
    print(f"Report written to: {output_root / 'live_research_report.json'}", flush=True)
    print("=" * 70, flush=True)
    return final_report


def run_live_experiment(
    output_root: Path,
    *,
    mock_client: Any | None = None,
    env: dict[str, str] | None = None,
    sources: VerifiedReadingSources | None = None,
    catalog: dict[str, Any] | None = None,
    start_content_id: str = "3429",
    max_budget_rmb: float = 0.10,
    max_tokens: int = 1_000_000,
    decision_mode: str | None = None,
):
    output_root.mkdir(parents=True, exist_ok=False)
    env = env or (load_codex_env() if mock_client is None else {})

    # Verify credentials when not using mock client
    if mock_client is None:
        if not env.get("JIZHI_API_KEY") or not env.get("JIZHI_BASE_URL"):
            raise ExplorationError("JIZHI credentials missing from environment and .codex/.env")

    if decision_mode is None:
        effective_decision_mode = "mock_model_decisions" if mock_client is not None else "live_model_decisions"
    else:
        if not isinstance(decision_mode, str) or not decision_mode.strip():
            raise ExplorationError("explicit non-empty decision_mode required")
        effective_decision_mode = decision_mode.strip()
        if mock_client is not None and effective_decision_mode == "live_model_decisions":
            raise ExplorationError("mock_client run must not be marked as live_model_decisions")

    if sources is None or catalog is None:
        canonical_manifest = Path("data/canonical/mihoyo_obc/zh-cn/phase03-batch5b-p01eb-full-20260824/metadata/manifest.json")
        ru_manifest = Path("data/retrieval/p04-rag-production/phase03-batch5b-p01eb-full-20260824-pm02/ru/metadata/manifest.json")
        lexical_manifest = Path(".local/p04-block-a-closure-20260914-r2/field-aware-lexical/metadata/manifest.json")
        listing_path = Path("data/raw/mihoyo_obc/zh-cn/p01eb_full_20260824/responses/listings/44cb730c420480a0477b505ae68af508fb90f96cf0ec54c6ad16949dd427f13a.json")

        tools = LocalExplorationTools(canonical_manifest, ru_manifest, lexical_manifest)
        sources = VerifiedReadingSources(tools)
        listing_data = json.loads(listing_path.read_bytes())
        catalog = build_release_catalog(listing_data, source=tools.canonical["source"], locale=tools.canonical["locale"])

    # Report unit pricing and limits
    print("=" * 70, flush=True)
    print("Phase05 Navi Candidate V Live Semantic Research (DeepSeek)", flush=True)
    print(f"Model: deepseek-v4.1-flash via Jizhi (Responses SSE API)", flush=True)
    print(f"Pricing: Input={PRICE_INPUT_PER_1M} RMB/1M, Output={PRICE_OUTPUT_PER_1M} RMB/1M (estimated)", flush=True)
    print(f"Hard limits: Max tokens={max_tokens:,} | Max budget={max_budget_rmb:.2f} RMB | Max rounds=6", flush=True)
    print(f"Decision mode: {effective_decision_mode}", flush=True)
    print(f"Output root: {output_root}", flush=True)
    print("=" * 70, flush=True)

    # Initialize reading session on start content ID
    start_source = f"{catalog['source']}:{catalog['locale']}:{start_content_id}"
    session = ReadingSession.create(
        sources, catalog, output_root / "session", baseline_sources=[start_source],
        budget=ReadingBudget(max_actions=32, max_searches=10, max_pages=10),
        decision_mode=effective_decision_mode,
    )

    runner = LiveResearchRunner(output_root, sources, catalog, env, mock_client=mock_client,
                                max_budget_rmb=max_budget_rmb, max_tokens=max_tokens)

    # ----------------------------------------------------
    # STEP 1: Deliver Seed Task Text (3429) & Model Reading
    # ----------------------------------------------------
    seed_receipt = session.read_next()
    print(f"\n[Step 1] Delivered task 3429 body: {len(seed_receipt['units'])} units, chars={sum(len(u['text']) for u in seed_receipt['units'])}", flush=True)

    task_units_repr = []
    delivered_uids = []
    for u in seed_receipt["units"]:
        delivered_uids.extend(u["unit_ids"])
        task_units_repr.append({
            "section_name": u["section_name"],
            "unit_ids": u["unit_ids"],
            "text": u["text"],
            "char_count": len(u["text"]),
        })

    instructions_r1 = (
        "你是一名严格遵守证据主义的提瓦特白夜国/渊下宫历史文献研究员。\n"
        "你的任务是阅读给定的正式任务对话正文，分析其中提及的历史文献线索，建立第一批研究线索，并提出下一步最值得检索的文献名称。\n"
        "硬性规则：\n"
        "1. 提取线索时，clue 的 source_unit_ids 必须且只能从正文中实际出现的 unit_ids 中挑选，严禁编造或臆测 ID。\n"
        "2. 所有推论必须标记为假说，不作确定性终局定论。\n"
        "3. 输出必须是合法 JSON 对象，格式如下：\n"
        "{\n"
        '  "research_purpose": "本轮分析目的",\n'
        '  "clues": [\n'
        "    {\n"
        '      "clue_id": "clue_01",\n'
        '      "text": "提取的客观线索陈述",\n'
        '      "source_unit_ids": ["必须使用正文中给出的真实 RU ID"],\n'
        '      "source_genre": "official_duty_dialogue",\n'
        '      "epistemic_status": "duty_testimony",\n'
        '      "narrative_significance": "阐述该线索对理解白夜国历史的意义",\n'
        '      "uncertainty_note": "该线索存在的局限性或不确定性说明"\n'
        "    }\n"
        "  ],\n"
        '  "summary": "当前对任务对话中图书管理员陈述的综合理解总结",\n'
        '  "next_search": {\n'
        '    "query": "建议检索的文献或书目名称（例如书名）",\n'
        '    "reason": "为什么首先检索此文献"\n'
        "  }\n"
        "}\n"
    )

    user_input_r1 = (
        f"【已交付的任务正文：白夜国世界任务《龙蛇藏归辑录》（3429）图书管理员绘真对话】\n\n"
        + json.dumps(task_units_repr, ensure_ascii=False, indent=2)
    )

    terminal_r1, text_r1 = runner.call_model("round_01", "初始任务正文阅读与研究方向提议", instructions_r1, user_input_r1)
    data_r1 = extract_json_block(text_r1)

    # Commit step 1 understanding into session
    r1_updates = []
    for c in data_r1.get("clues", []):
        raw_uids = c.get("source_unit_ids", [])
        if not isinstance(raw_uids, list):
            raw_uids = []
        raw_uids = [str(uid) for uid in raw_uids if isinstance(uid, str)]
        # Strictly NO fallback to delivered_uids[:1]
        genre = c.get("source_genre", "official_duty_dialogue")
        if genre not in VALID_GENRES:
            genre = "unclassified"
        epistemic = c.get("epistemic_status", "duty_testimony")
        if epistemic not in VALID_EPISTEMIC:
            epistemic = "unclassified"
        clue_entry = {
            "clue_id": str(c.get("clue_id", f"clue_seed_{len(r1_updates)+1}")),
            "text": str(c.get("text", "初始线索")),
            "source_unit_ids": raw_uids,
            "source_genre": genre,
            "epistemic_status": epistemic,
            "narrative_significance": str(c.get("narrative_significance", "种子任务线索")),
            "uncertainty_note": str(c["uncertainty_note"]) if c.get("uncertainty_note") else None,
        }
        r1_updates.append({"operation": "add", "target_clue_id": None, "reason": "种子正文初步理解", "clue": clue_entry})

    summary_r1 = data_r1.get("summary", "完成种子任务正文初读。")
    res_r1 = session.understand(updates=r1_updates, summary=summary_r1, receipt_ids=[seed_receipt["receipt_id"]])
    if "accepted_updates" not in res_r1:
        rejection_reason = res_r1.get("reason", "unknown seed understanding rejection")
        print(f"[Step 1 FAILED] Seed understanding rejected by state machine: {rejection_reason}", flush=True)
        session.stop(f"Seed understanding rejected: {rejection_reason}")
        return build_final_report(session, runner, output_root, seed_receipt, [], {},
                                  closed_loop_verified=False, rejection_reason=rejection_reason)

    print(f"[Step 1 Complete] Committed {res_r1['accepted_updates']} clues into session understanding.", flush=True)

    # ----------------------------------------------------
    # STEP 2: Execute Real Retrieval on Model's Chosen Query
    # ----------------------------------------------------
    search_decision = data_r1.get("next_search", {})
    query = search_decision.get("query", "《日月前事》")
    print(f"\n[Step 2] Model autonomously selected query: {query!r} (Reason: {search_decision.get('reason')})", flush=True)

    topic_id = seed_receipt["topic_id"]
    search_res = session.search(query, topic_id=topic_id, top_k=10)
    hits = search_res["result"]["hits"]
    print(f"[Step 2 Retrieval] Found {len(hits)} candidates for {query!r}.", flush=True)

    candidates_view = []
    for h in hits:
        candidates_view.append({
            "candidate_index": h["rank"],
            "unit_id": h["unit_id"],
            "content_id": h["canonical_address"]["source_identity_key"].split(":")[-1],
            "record_title": h["record_context"]["record_title"],
            "section_name": h["record_context"]["section_name"],
            "unit_kind": h["canonical_address"]["canonical_unit_kind"],
            "score": round(h["retrieval"]["score"], 4),
            "snippet": h["snippet"][:200],
        })

    instructions_r2 = (
        "你是一名严格遵守证据主义的白夜国历史文献研究员。\n"
        "你刚刚对白夜国遗失文献发起了检索，检索引擎在全库正式语料中返回了 Top 10 真实候选。\n"
        "任务：\n"
        "1. 仔细鉴别这 10 个候选材料的来源性质：区分【原典正文】、【NPC职务证言/剧情对话】、【玩家/社区解读攻略】和【字面偶然匹配】。\n"
        "2. 为了研究该文献记载的提瓦特与白夜国远古历史，你决定阅读哪一个候选？\n"
        "   - 若发现合适的目标正文，选择 'read' 并指定 candidate_index 和对应 chosen_unit_id。\n"
        "   - 若这 10 个候选均不符合要求（例如都是旁证或二创），你可以选择 'search' 并给出新的检索词，或 'stop' 停止。\n"
        "3. 输出必须是合法 JSON 对象，格式如下：\n"
        "{\n"
        '  "research_purpose": "本轮候选甄别与选择目的",\n'
        '  "evaluations": [\n'
        "    {\n"
        '      "candidate_index": 1,\n'
        '      "source_nature": "primary_book / npc_testimony / community_guide / incidental",\n'
        '      "reason": "简短判定理由"\n'
        "    }\n"
        "  ],\n"
        '  "decision": {\n'
        '    "action": "read / search / stop",\n'
        '    "chosen_candidate_index": null,\n'
        '    "chosen_unit_id": null,\n'
        '    "selection_rationale": "为什么选择此项（或排除其他候选）的具体决策理由"\n'
        "  }\n"
        "}\n"
        "说明：若 action 为 'read'，必须将 chosen_candidate_index 设为目标候选的 candidate_index 整数（1-10），chosen_unit_id 设为其对应的真实 unit_id；若为 search 或 stop，则设为 null。示例中的 null 为占位符，请根据真实判断填写。"
    )

    user_input_r2 = (
        f"【你发起的检索词】: {query}\n\n"
        f"【全库真实 BM25 检索返回的 Top 10 候选】:\n"
        + json.dumps(candidates_view, ensure_ascii=False, indent=2)
    )

    terminal_r2, text_r2 = runner.call_model("round_02", "检索候选性质甄别与阅读决策", instructions_r2, user_input_r2)
    data_r2 = extract_json_block(text_r2)

    decision_r2 = data_r2.get("decision", {})
    action = decision_r2.get("action", "read")
    chosen_idx = decision_r2.get("chosen_candidate_index")
    chosen_uid = decision_r2.get("chosen_unit_id")
    rationale = decision_r2.get("selection_rationale", "")

    print(f"\n[Step 2 Model Decision]: action={action}, chosen_index={chosen_idx}, uid={chosen_uid}", flush=True)
    print(f"Decision rationale: {rationale}", flush=True)

    chosen_hit = None
    selection_error = None
    if action == "read":
        has_idx = chosen_idx is not None
        has_uid = chosen_uid is not None

        idx_error: str | None = None
        uid_error: str | None = None

        if has_idx:
            if isinstance(chosen_idx, bool) or not isinstance(chosen_idx, int):
                idx_error = f"Candidate index must be a strict integer (rejected {type(chosen_idx).__name__}): chosen_index={chosen_idx!r}"
            elif not (1 <= chosen_idx <= len(hits)):
                idx_error = f"Candidate index out of bounds: chosen_index={chosen_idx} (valid: 1..{len(hits)})"

        if has_uid:
            if not isinstance(chosen_uid, str) or not chosen_uid.strip():
                uid_error = f"Candidate unit_id must be a non-empty string, got {type(chosen_uid).__name__}: {chosen_uid!r}"
            elif not any(h["unit_id"] == chosen_uid for h in hits):
                uid_error = f"Candidate unit_id not found in hits: chosen_unit_id={chosen_uid!r}"

        if not has_idx and not has_uid:
            selection_error = "Action 'read' specified but neither chosen_candidate_index nor chosen_unit_id was provided."
        elif has_idx and has_uid:
            # When both index and unit_id are provided:
            # If EITHER is invalid/illegal, or if they conflict with each other, MUST Fail-closed!
            # Do NOT ignore an illegal index and degrade to unit_id.
            if idx_error and uid_error:
                selection_error = f"Both candidate index and unit_id are invalid: [index: {idx_error}] [unit_id: {uid_error}]"
            elif idx_error:
                selection_error = f"Candidate index invalid: {idx_error} (cannot fall back to unit_id {chosen_uid!r})"
            elif uid_error:
                selection_error = f"Candidate unit_id invalid: {uid_error} (cannot fall back to index {chosen_idx!r})"
            else:
                hit_by_idx = hits[chosen_idx - 1]
                if hit_by_idx["unit_id"] != chosen_uid:
                    selection_error = (
                        f"Candidate selection conflict: chosen_index={chosen_idx} points to unit_id={hit_by_idx['unit_id']!r}, "
                        f"but chosen_unit_id={chosen_uid!r}. Index and unit_id must point to the same candidate."
                    )
                else:
                    chosen_hit = hit_by_idx
        elif has_idx:
            if idx_error:
                selection_error = f"Candidate index invalid: {idx_error}"
            else:
                chosen_hit = hits[chosen_idx - 1]
        elif has_uid:
            if uid_error:
                selection_error = f"Candidate unit_id invalid: {uid_error}"
            else:
                chosen_hit = next(h for h in hits if h["unit_id"] == chosen_uid)

    if action == "read" and (not chosen_hit or selection_error):
        # Strictly reject unresolvable or conflicting candidate selection. NO silent fallback or preference!
        err_msg = (
            f"Candidate selection failed: {selection_error}"
            if selection_error
            else f"Candidate selection failed: chosen_index={chosen_idx}, chosen_uid={chosen_uid} could not be resolved from hits"
        )
        print(f"Error: {err_msg}", flush=True)
        session.stop(f"Rejected: {err_msg}")
        return build_final_report(session, runner, output_root, seed_receipt, hits, data_r2,
                                  action=action, closed_loop_verified=False, rejection_reason=err_msg)

    # ----------------------------------------------------
    # STEP 3: Deliver Target Body & Continuous Understanding Update
    # ----------------------------------------------------
    if action == "read" and chosen_hit:
        target_addr = chosen_hit["canonical_address"]
        target_source = target_addr["source_identity_key"]
        target_section = target_addr["section_ordinal"]

        print(f"\n[Step 3] Enqueueing chosen candidate: source={target_source}, anchor_section={target_section}", flush=True)
        session.enqueue(target_source, lane="related", topic_id=topic_id, anchor_section=target_section,
                        origin={"kind": "search_hit", "unit_id": chosen_hit["unit_id"]})

        target_receipt = session.read_next()
        delivered_body_chars = sum(len(u["text"]) for u in target_receipt.get("units", []))
        print(f"[Step 3 Delivered] Receipt {target_receipt.get('receipt_id')}: {len(target_receipt.get('units', []))} units, {delivered_body_chars} chars", flush=True)

        target_units_repr = []
        new_delivered_uids = []
        for u in target_receipt.get("units", []):
            new_delivered_uids.extend(u["unit_ids"])
            parent_comp = u.get("parent_component_key", "")
            sec_name = u.get("section_name", "")
            if "material_base_info" in parent_comp or "base_info" in parent_comp:
                content_nature = "obc_material_metadata_or_acquisition"
            elif sec_name:
                content_nature = f"primary_document_body ({sec_name})"
            else:
                content_nature = "carrier_or_supplementary_body"
            target_units_repr.append({
                "record_title": target_receipt["record_title"],
                "section_name": sec_name,
                "parent_component": parent_comp,
                "content_nature": content_nature,
                "unit_ids": u["unit_ids"],
                "text": u["text"],
                "char_count": len(u["text"]),
            })

        # Decision context & active clues from continuity
        active_clues = session.state["continuity"]["active_clues"]
        active_clue_ids = [c["clue_id"] for c in active_clues]
        existing_clues_repr = [
            {
                "clue_id": c["clue_id"],
                "text": c["text"],
                "source_genre": c["source_genre"],
                "epistemic_status": c["epistemic_status"],
                "narrative_significance": c["narrative_significance"],
            }
            for c in active_clues[-12:]
        ]
        next_suggested_id_num = len(session.state["continuity"]["clue_events"]) + 1
        suggested_next_id = f"clue_{next_suggested_id_num:02d}"

        instructions_r3 = (
            "你是一名严格遵守证据主义的白夜国历史文献研究员。\n"
            "系统已经交付了你所选材料的正式正文及相关内容。\n"
            "请阅读该正文，结合你在第一轮建立的初步理解，提出连续理解更新。\n"
            "规则：\n"
            "1. 线索ID唯一性约束（极其重要）：\n"
            f"   - 当前已有线索已占用 ID 列表为：{active_clue_ids}。\n"
            f"   - 每一个新增或更新事件中的 clue.clue_id 必须是全生命周期全局唯一的全新 ID（建议从 {suggested_next_id} 开始顺延命名），绝对严禁与已有 ID 重复！\n"
            "2. 操作类型（operation）规范：\n"
            "   - 'add': 针对新材料提取全新线索，target_clue_id 必须为 null，clue.clue_id 必须为全新 ID。\n"
            "   - 'supplement': 针对已有线索补充新证据，target_clue_id 必须指定为已有线索 ID（如 'clue_01'），clue.clue_id 仍必须是新分配的唯一 ID（如 '{suggested_next_id}'）。\n"
            "   - 'contrast': 发现新证据与已有线索存在观点冲突/叙事对照，target_clue_id 必须为已有线索 ID，clue.clue_id 为新分配唯一 ID。\n"
            "   - 'revise': 新证据推翻或修正已有线索，target_clue_id 必须为已有线索 ID，clue.clue_id 为新分配唯一 ID。\n"
            "3. 证据引用约束：\n"
            "   - clue.source_unit_ids 必须且只能从本次交付材料中出现的 unit_ids 中挑选，严禁编造或臆测！\n"
            "4. 来源性质鉴别（极其重要）：\n"
            "   - 本次交付材料中可能同时包含【古代文献原典正文】、【游戏内物品描述】与【OBC词条编辑性说明/获取攻略】。\n"
            "   - 严禁仅因处于同一页面就赋予相同的古籍叙事身份！\n"
            "   - 只有真正的古籍章节正文（如《日月前事》第二卷记录）才能标注 source_genre='ancient_chronicle_forbidden' 及 epistemic_status='ancient_official_record'。\n"
            "   - 对于游戏内物品说明或词条获取指引（如狭间之街图书馆书架获取等），必须标注其真实性质，不得作为古籍记载对待。\n"
            "5. 输出必须是合法 JSON 对象，格式如下：\n"
            "{\n"
            '  "research_purpose": "本轮正文研读与理解更新目的",\n'
            '  "clue_updates": [\n'
            "    {\n"
            '      "operation": "add",\n'
            '      "target_clue_id": null,\n'
            '      "reason": "更新原因",\n'
            '      "clue": {\n'
            f'        "clue_id": "{suggested_next_id}",\n'
            '        "text": "从正文中获取的核心知识与事实陈述",\n'
            '        "source_unit_ids": ["必须使用正文中给出的真实 RU ID"],\n'
            '        "source_genre": "ancient_chronicle_forbidden",\n'
            '        "epistemic_status": "ancient_official_record",\n'
            '        "narrative_significance": "该正文对揭示提瓦特与白夜国远古史的意义",\n'
            '        "uncertainty_note": "文本自身存在的寓言性或存疑之处"\n'
            "      }\n"
            "    }\n"
            "  ],\n"
            '  "updated_summary": "整合第一轮与本轮正文后的最新研究总结",\n'
            '  "unresolved_questions": ["接下来尚未解决的关键疑问"],\n'
            '  "next_action": {\n'
            '    "action": "stop",\n'
            '    "reason": "已完成对该文献的闭环研读与理解，达到本阶段研究目标"\n'
            "  }\n"
            "}\n"
        )

        user_input_r3 = (
            f"【上一轮研究总结】:\n{summary_r1}\n\n"
            f"【当前已有线索（已占用 ID 列表：{active_clue_ids}）】:\n"
            + json.dumps(existing_clues_repr, ensure_ascii=False, indent=2) + "\n\n"
            f"【本次正式交付的材料正文（来源：{target_receipt['record_title']}）】:\n"
            + json.dumps(target_units_repr, ensure_ascii=False, indent=2)
        )

        terminal_r3, text_r3 = runner.call_model("round_03", "目标正文研读与持续理解更新", instructions_r3, user_input_r3)
        data_r3 = extract_json_block(text_r3)

        r3_updates = []
        for upd in data_r3.get("clue_updates", []):
            c = upd.get("clue", {})
            raw_uids = c.get("source_unit_ids", [])
            if not isinstance(raw_uids, list):
                raw_uids = []
            raw_uids = [str(uid) for uid in raw_uids if isinstance(uid, str)]
            # Strictly NO fallback to new_delivered_uids[:1]
            genre = c.get("source_genre", "ancient_chronicle_forbidden")
            if genre not in VALID_GENRES:
                genre = "unclassified"
            epistemic = c.get("epistemic_status", "ancient_official_record")
            if epistemic not in VALID_EPISTEMIC:
                epistemic = "unclassified"
            clue_entry = {
                "clue_id": str(c.get("clue_id", "")),
                "text": str(c.get("text", "正文线索")),
                "source_unit_ids": raw_uids,
                "source_genre": genre,
                "epistemic_status": epistemic,
                "narrative_significance": str(c.get("narrative_significance", "正文历史意义")),
                "uncertainty_note": str(c["uncertainty_note"]) if c.get("uncertainty_note") else None,
            }
            r3_updates.append({
                "operation": upd.get("operation", "add"),
                "target_clue_id": upd.get("target_clue_id"),
                "reason": upd.get("reason", "正文研读更新"),
                "clue": clue_entry,
            })

        summary_r3 = data_r3.get("updated_summary", "完成目标文献正文研读。")
        questions_r3 = data_r3.get("unresolved_questions", [])

        res_r3 = session.understand(updates=r3_updates, summary=summary_r3, receipt_ids=[target_receipt["receipt_id"]],
                                    unresolved_questions=questions_r3)
        if "accepted_updates" not in res_r3:
            rejection_reason = res_r3.get("reason", "unknown understanding rejection")
            print(f"[Step 3 FAILED] Understanding rejected by state machine: {rejection_reason}", flush=True)
            session.stop(f"Understanding rejected: {rejection_reason}")
            return build_final_report(session, runner, output_root, seed_receipt, hits, data_r2,
                                      chosen_hit=chosen_hit, target_receipt=target_receipt,
                                      action=action, closed_loop_verified=False,
                                      rejection_reason=rejection_reason)

        accepted_count = res_r3["accepted_updates"]
        print(f"[Step 3 Complete] Committed {accepted_count} clue updates into session understanding.", flush=True)

        session.stop("Completed live AI semantic closed-loop research.")
        return build_final_report(session, runner, output_root, seed_receipt, hits, data_r2,
                                  chosen_hit=chosen_hit, target_receipt=target_receipt,
                                  action=action, closed_loop_verified=True)
    else:
        stop_reason = f"Model chose action '{action}' instead of read"
        session.stop(stop_reason)
        return build_final_report(session, runner, output_root, seed_receipt, hits, data_r2,
                                  action=action, closed_loop_verified=False, rejection_reason=stop_reason)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/retrieval/p05-navi-v-live-deepseek-20261020-r2"))
    parser.add_argument("--max-budget-rmb", type=float, default=0.10, help="Hard ceiling for estimated cost in RMB")
    parser.add_argument("--max-tokens", type=int, default=1_000_000, help="Hard ceiling for cumulative tokens")
    args = parser.parse_args(argv)
    run_live_experiment(args.output, max_budget_rmb=args.max_budget_rmb, max_tokens=args.max_tokens)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
