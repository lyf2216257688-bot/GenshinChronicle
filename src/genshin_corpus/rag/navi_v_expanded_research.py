# -*- coding: utf-8 -*-
"""
Phase05 Navi Candidate V Expanded Live Semantic Research Runner.

Validates in a single continuous session:
1. Version queue automatic progression (3429 -> 3428 in baseline lane)
2. Related material exploration and Return to Queue (baseline -> related -> baseline)
3. Cross-task storyline understanding (inheritance, supplement, contrast, revision)
4. Source nature distinction and anti-noise truthfulness
5. Bounded soft budget management and complete checkpoint/coverage recovery
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from genshin_corpus.canonical.fingerprints import sha256_json
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


class ExpandedResearchRunner:
    def __init__(self, root: Path, sources: VerifiedReadingSources, catalog: dict[str, Any], env: dict[str, str],
                 *, mock_client: Any | None = None, max_budget_rmb: float = 0.05, max_tokens: int = 100_000):
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
            self.client = E2SDK(route="jizhi", model="deepseek-v4.1-flash", environment=env,
                                mode="research", max_attempts=5, format_retry_enabled=False)
        self.cumulative_tokens = 0
        self.cumulative_input = 0
        self.cumulative_output = 0
        self.cumulative_cost = 0.0
        self.max_budget_rmb = max_budget_rmb
        self.max_tokens = max_tokens
        self.call_records: list[dict[str, Any]] = []

    def call_model(self, round_name: str, purpose: str, instructions: str, user_input: str) -> tuple[dict[str, Any], str]:
        stem = self.rounds_dir / round_name
        terminal_file = stem / "terminal.json"
        accounting_file = stem / "round-accounting.json"

        # Check if attempt already completed and saved on disk
        if terminal_file.exists():
            try:
                terminal = json.loads(terminal_file.read_text(encoding="utf-8"))
                if terminal.get("complete"):
                    record = None
                    if accounting_file.exists():
                        record = json.loads(accounting_file.read_text(encoding="utf-8"))
                    else:
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
                        _once(accounting_file, record)
                    if record:
                        self.cumulative_tokens = record["cumulative_tokens"]
                        self.cumulative_cost = record["cumulative_estimated_cost_rmb"]
                        self.call_records.append(record)
                    print(f"\n[AI Call: {round_name} (RESUMED)] Reused completed model output from disk. Tokens: {record.get('total_tokens')}", flush=True)
                    return terminal, terminal.get("content", "")
            except Exception:
                pass

        stem.mkdir(parents=True, exist_ok=False)
        run_identity = self.root.name

        body = {
            "model": "deepseek-v4.1-flash",
            "instructions": instructions,
            "input": user_input,
            "stream": True,
            "max_output_tokens": 8192,
        }

        print(f"\n[AI Call: {round_name}] Purpose: {purpose}", flush=True)
        print(f"Pre-call budget check: Cumulative tokens={self.cumulative_tokens}, Estimated Cost={self.cumulative_cost:.6f} RMB (Soft ceiling: {self.max_budget_rmb:.2f} RMB / {self.max_tokens:,} tokens)", flush=True)

        # Soft budget warning check (allows last request to complete, prevents runaway infinite loops)
        if self.cumulative_tokens >= self.max_tokens:
            raise ExplorationError(f"Soft token budget reached ({self.max_tokens:,} tokens)")
        if self.cumulative_cost >= self.max_budget_rmb:
            raise ExplorationError(f"Soft cost ceiling reached ({self.max_budget_rmb:.4f} RMB)")

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


def build_expanded_report(
    session: ReadingSession,
    runner: ExpandedResearchRunner,
    output_root: Path,
    baseline_receipts: list[dict[str, Any]],
    related_receipts: list[dict[str, Any]],
    search_events: list[dict[str, Any]],
    *,
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
        for r in session.state.get("receipts", [])
    ]

    manifest_mode = session.manifest.get("decision_mode")
    if not isinstance(manifest_mode, str) or not manifest_mode.strip():
        report_decision_mode = "injected_recorded_or_fixture_decisions_only"
    else:
        report_decision_mode = manifest_mode.strip()

    if getattr(runner, "mock_client", None) is not None and report_decision_mode == "live_model_decisions":
        raise ExplorationError("mock_client run must not be marked as live_model_decisions")

    clues = session.state["continuity"]["active_clues"]
    events = session.state["continuity"]["clue_events"]
    operations_count = {
        "add": sum(1 for e in events if e.get("operation") == "add"),
        "supplement": sum(1 for e in events if e.get("operation") == "supplement"),
        "contrast": sum(1 for e in events if e.get("operation") == "contrast"),
        "revise": sum(1 for e in events if e.get("operation") == "revise"),
    }

    final_report = {
        "schema_version": "phase05-navi-v-expanded-research-0.1",
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
            "soft_budget_rmb": runner.max_budget_rmb,
            "soft_token_budget": runner.max_tokens,
        },
        "calls": runner.call_records,
        "baseline_progression": {
            "baseline_sources": session.manifest["baseline_sources"],
            "tasks_delivered_count": len(baseline_receipts),
            "receipt_ids": [r["receipt_id"] for r in baseline_receipts],
            "version_progression_verified": len(baseline_receipts) >= 2,
        },
        "lane_return": {
            "related_delivered_count": len(related_receipts),
            "receipt_ids": [r["receipt_id"] for r in related_receipts],
            "lane_return_verified": len(related_receipts) >= 1 and len(baseline_receipts) >= 2,
        },
        "search_and_candidates": search_events,
        "reading_receipts": reading_receipts,
        "reading_and_understanding": {
            "closed_loop_verified": closed_loop_verified,
            "rejection_reason": rejection_reason,
            "rejections_count": len(session.state.get("rejections", [])),
            "rejections": session.state.get("rejections", []),
            "operations_count": operations_count,
            "cross_task_operations_count": operations_count["supplement"] + operations_count["contrast"] + operations_count["revise"],
            "final_summary": session.state["continuity"]["research_summary"],
            "active_clues_count": len(clues),
            "active_clues": clues,
            "unresolved_questions": session.state.get("pending_questions", []),
        },
        "coverage_summary": coverage,
        "resume_verified": resume_ok,
    }

    _once(output_root / "expanded_research_report.json", final_report)
    print("\n" + "=" * 70, flush=True)
    print("EXPANDED RESEARCH EXPERIMENT AUDIT SUMMARY", flush=True)
    print(f"Total Calls: {len(runner.call_records)}", flush=True)
    print(f"Total Tokens: {runner.cumulative_tokens} (inp={runner.cumulative_input}, out={runner.cumulative_output})", flush=True)
    print(f"Estimated Cost: {runner.cumulative_cost:.6f} RMB (soft budget: {runner.max_budget_rmb:.2f} RMB)", flush=True)
    print(f"Version Progression Verified: {final_report['baseline_progression']['version_progression_verified']}", flush=True)
    print(f"Lane Return Verified: {final_report['lane_return']['lane_return_verified']}", flush=True)
    print(f"Cross-Task Operations: {final_report['reading_and_understanding']['cross_task_operations_count']} (supp={operations_count['supplement']}, cont={operations_count['contrast']}, rev={operations_count['revise']})", flush=True)
    print(f"Closed Loop Verified: {closed_loop_verified}", flush=True)
    if rejection_reason:
        print(f"Rejection / Stop Reason: {rejection_reason}", flush=True)
    print(f"Resume Verified: {resume_ok}", flush=True)
    print(f"Report written to: {output_root / 'expanded_research_report.json'}", flush=True)
    print("=" * 70, flush=True)
    return final_report


def run_expanded_experiment(
    output_root: Path,
    *,
    mock_client: Any | None = None,
    env: dict[str, str] | None = None,
    sources: VerifiedReadingSources | None = None,
    catalog: dict[str, Any] | None = None,
    baseline_content_ids: Sequence[str] = ("3429", "3428"),
    max_budget_rmb: float = 0.05,
    max_tokens: int = 100_000,
    decision_mode: str | None = None,
):
    env = env or (load_codex_env() if mock_client is None else {})

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

    print("=" * 70, flush=True)
    print("Phase05 Navi Candidate V Expanded Semantic Research (DeepSeek)", flush=True)
    print(f"Model: deepseek-v4.1-flash via Jizhi (Responses SSE API)", flush=True)
    print(f"Pricing: Input={PRICE_INPUT_PER_1M} RMB/1M, Output={PRICE_OUTPUT_PER_1M} RMB/1M (estimated)", flush=True)
    print(f"Soft budget ceiling: Max tokens={max_tokens:,} | Max budget={max_budget_rmb:.2f} RMB", flush=True)
    print(f"Decision mode: {effective_decision_mode}", flush=True)
    print(f"Baseline sources: {baseline_content_ids}", flush=True)
    print(f"Output root: {output_root}", flush=True)
    print("=" * 70, flush=True)

    # Initialize reading session on multi-task baseline (supports session resumption)
    session_dir = output_root / "session"
    if session_dir.exists():
        session = ReadingSession.resume(sources, catalog, session_dir)
        print(f"\n[Resume] Successfully resumed ReadingSession from {session_dir} at action={session.state['used']['actions']}", flush=True)
    else:
        output_root.mkdir(parents=True, exist_ok=False)
        baseline_sources = [f"{catalog['source']}:{catalog['locale']}:{cid}" for cid in baseline_content_ids]
        session = ReadingSession.create(
            sources, catalog, session_dir, baseline_sources=baseline_sources,
            budget=ReadingBudget(max_actions=32, max_searches=10, max_pages=10),
            decision_mode=effective_decision_mode,
        )

    runner = ExpandedResearchRunner(output_root, sources, catalog, env, mock_client=mock_client,
                                    max_budget_rmb=max_budget_rmb, max_tokens=max_tokens)

    baseline_receipts: list[dict[str, Any]] = []
    related_receipts: list[dict[str, Any]] = []
    search_events: list[dict[str, Any]] = []

    # ----------------------------------------------------
    # STAGE 1: Deliver Baseline Task 1 (3429: 《龙蛇藏归辑录》)
    # ----------------------------------------------------
    if session.state["receipts"]:
        r1_receipt = session.state["receipts"][0]
        print(f"\n[Stage 1 (Resumed)] Reusing delivered Task 1 ({r1_receipt['record_title']}): {len(r1_receipt['units'])} units", flush=True)
    else:
        r1_receipt = session.read_next()
        print(f"\n[Stage 1] Delivered Task 1 ({r1_receipt['record_title']}): {len(r1_receipt['units'])} units, chars={sum(len(u['text']) for u in r1_receipt['units'])}", flush=True)
    baseline_receipts.append(r1_receipt)

    task1_units_repr = [
        {"section_name": u["section_name"], "unit_ids": u["unit_ids"], "text": u["text"]}
        for u in r1_receipt["units"]
    ]

    instructions_r1 = (
        "你是一名严格遵守证据主义的白夜国历史文献研究员。\n"
        "任务：\n"
        "1. 仔细阅读刚刚交付的官方世界任务正文；\n"
        "2. 提取 3-4 条核心背景线索，陈述客观精炼（注意：该材料为NPC职务工作对话，必须标注 source_genre='official_duty_dialogue' 与 epistemic_status='duty_testimony'）；\n"
        "3. 指出正文中提及但尚未归还的失落古籍或重要历史疑问，提出下一步最值得通过检索查找的线索名称。\n"
        "4. 输出必须是合法 JSON 对象，格式如下：\n"
        "{\n"
        '  "research_purpose": "本轮任务正文阅读目的",\n'
        '  "clues": [\n'
        "    {\n"
        '      "clue_id": "clue_01",\n'
        '      "text": "提取的核心客观知识陈述",\n'
        '      "source_unit_ids": ["必须使用正文中给出的真实 RU ID"],\n'
        '      "source_genre": "official_duty_dialogue",\n'
        '      "epistemic_status": "duty_testimony",\n'
        '      "narrative_significance": "该线索对理解白夜国历史的意义",\n'
        '      "uncertainty_note": "存疑或局限性说明"\n'
        "    }\n"
        "  ],\n"
        '  "summary": "初步理解摘要",\n'
        '  "next_search": {\n'
        '    "query": "下一步检索关键词（例如提及的古籍名称或关键历史实体）",\n'
        '    "reason": "检索理由"\n'
        "  }\n"
        "}\n"
    )

    user_input_r1 = f"【已交付的任务正文（来源：{r1_receipt['record_title']}）】:\n\n" + json.dumps(task1_units_repr, ensure_ascii=False, indent=2)
    terminal_r1, text_r1 = runner.call_model("round_01", "初始任务3429正文研读与线索提取", instructions_r1, user_input_r1)
    data_r1 = extract_json_block(text_r1)

    r1_updates = []
    for c in data_r1.get("clues", []):
        raw_uids = c.get("source_unit_ids", [])
        if not isinstance(raw_uids, list):
            raw_uids = []
        raw_uids = [str(uid) for uid in raw_uids if isinstance(uid, str)]
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
            "narrative_significance": str(c.get("narrative_significance", "任务线索")),
            "uncertainty_note": str(c["uncertainty_note"]) if c.get("uncertainty_note") else None,
        }
        r1_updates.append({"operation": "add", "target_clue_id": None, "reason": "任务3429初步理解", "clue": clue_entry})

    if r1_receipt["research_status"] == "pending_understanding":
        summary_r1 = data_r1.get("summary", "完成任务3429初读。")
        res_r1 = session.understand(updates=r1_updates, summary=summary_r1, receipt_ids=[r1_receipt["receipt_id"]])
        if "accepted_updates" not in res_r1:
            rejection_reason = res_r1.get("reason", "task 1 understanding rejection")
            session.stop(f"Task 1 rejected: {rejection_reason}")
            return build_expanded_report(session, runner, output_root, baseline_receipts, related_receipts, search_events,
                                         closed_loop_verified=False, rejection_reason=rejection_reason)
        print(f"[Stage 1 Complete] Committed {res_r1['accepted_updates']} clues into session understanding.", flush=True)
    else:
        print(f"[Stage 1 Resumed] Receipt {r1_receipt['receipt_id']} already processed in session.", flush=True)

    # ----------------------------------------------------
    # STAGE 2: Search on Model's Chosen Query & Candidate Selection
    # ----------------------------------------------------
    search_decision = data_r1.get("next_search", {})
    query = search_decision.get("query", "《常世国龙蛇传》")
    print(f"\n[Stage 2] Model query: {query!r} (Reason: {search_decision.get('reason')})", flush=True)

    topic_id = r1_receipt["topic_id"]
    if session.state["searches"]:
        last_search = session.state["searches"][-1]
        hits = last_search["result"]["hits"]
        print(f"[Stage 2 Retrieval Resumed] Reused search from session: {len(hits)} candidates.", flush=True)
    else:
        search_res = session.search(query, topic_id=topic_id, top_k=10)
        hits = search_res["result"]["hits"]
        print(f"[Stage 2 Retrieval] Found {len(hits)} candidates for {query!r}.", flush=True)

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

    search_events.append({
        "query": query,
        "hit_count": len(hits),
        "candidates": candidates_view,
    })

    instructions_r2 = (
        "你是一名严格遵守证据主义的白夜国历史文献研究员。\n"
        "检索引擎在全库正式语料中返回了 Top 10 真实候选。\n"
        "任务：\n"
        "1. 仔细鉴别这 10 个候选材料的来源性质：区分【原典正文】、【NPC职务证言/剧情对话】、【玩家/社区解读攻略】和【字面偶然匹配】。\n"
        "2. 为了研究该背景资料，你决定阅读哪一个候选？\n"
        "   - 若发现合适的目标正文，选择 'read' 并指定 candidate_index 整数和对应 chosen_unit_id。\n"
        "   - 严格约束：chosen_candidate_index 必须为严格整数（1-10，严禁布尔值或浮点数），且必须与 chosen_unit_id 指向同一候选！\n"
        "   - 若候选均不符合要求，可选择 'search' 并给出新检索词，或 'stop' 停止。\n"
        "3. 输出必须是合法 JSON 对象：\n"
        "{\n"
        '  "research_purpose": "候选甄别与选择目的",\n'
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
        '    "selection_rationale": "具体决策理由"\n'
        "  }\n"
        "}\n"
        "说明：示例中 null 为占位符，若 action='read' 请填写真实对应整数 index 与真实 unit_id。"
    )

    user_input_r2 = f"【你发起的检索词】: {query}\n\n【Top 10 真实候选】:\n" + json.dumps(candidates_view, ensure_ascii=False, indent=2)
    terminal_r2, text_r2 = runner.call_model("round_02", "检索候选性质甄别与阅读决策", instructions_r2, user_input_r2)
    data_r2 = extract_json_block(text_r2)

    decision_r2 = data_r2.get("decision", {})
    action = decision_r2.get("action", "read")
    chosen_idx = decision_r2.get("chosen_candidate_index")
    chosen_uid = decision_r2.get("chosen_unit_id")
    rationale = decision_r2.get("selection_rationale", "")

    print(f"\n[Stage 2 Decision]: action={action}, chosen_index={chosen_idx}, uid={chosen_uid}", flush=True)

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
        err_msg = (
            f"Candidate selection failed: {selection_error}"
            if selection_error
            else f"Candidate selection failed: chosen_index={chosen_idx}, chosen_uid={chosen_uid} could not be resolved from hits"
        )
        print(f"Error: {err_msg}", flush=True)
        session.stop(f"Rejected: {err_msg}")
        return build_expanded_report(session, runner, output_root, baseline_receipts, related_receipts, search_events,
                                     closed_loop_verified=False, rejection_reason=err_msg)

    # ----------------------------------------------------
    # STAGE 3: Deliver Related Candidate (Lane='related')
    # ----------------------------------------------------
    if action == "read" and chosen_hit:
        target_addr = chosen_hit["canonical_address"]
        target_source = target_addr["source_identity_key"]
        target_section = target_addr["section_ordinal"]

        if len(session.state["receipts"]) >= 2:
            related_receipt = session.state["receipts"][1]
            print(f"[Stage 3 Resumed] Related candidate already enqueued and delivered: Receipt {related_receipt['receipt_id']}", flush=True)
        else:
            print(f"\n[Stage 3] Enqueueing related candidate into 'related' lane: source={target_source}, section={target_section}", flush=True)
            session.enqueue(target_source, lane="related", topic_id=topic_id, anchor_section=target_section,
                            origin={"kind": "search_hit", "unit_id": chosen_hit["unit_id"]})
            related_receipt = session.read_next()
        related_receipts.append(related_receipt)
        delivered_body_chars = sum(len(u["text"]) for u in related_receipt.get("units", []))
        print(f"[Stage 3 Delivered Related] Receipt {related_receipt.get('receipt_id')}: {len(related_receipt.get('units', []))} units, {delivered_body_chars} chars", flush=True)

        target_units_repr = []
        for u in related_receipt.get("units", []):
            parent_comp = u.get("parent_component_key", "")
            sec_name = u.get("section_name", "")
            if "material_base_info" in parent_comp or "base_info" in parent_comp:
                content_nature = "obc_material_metadata_or_acquisition"
            elif sec_name:
                content_nature = f"primary_document_body ({sec_name})"
            else:
                content_nature = "carrier_or_supplementary_body"
            target_units_repr.append({
                "record_title": related_receipt["record_title"],
                "section_name": sec_name,
                "parent_component": parent_comp,
                "content_nature": content_nature,
                "unit_ids": u["unit_ids"],
                "text": u["text"],
                "char_count": len(u["text"]),
            })

        active_clues = session.state["continuity"]["active_clues"]
        active_clue_ids = [c["clue_id"] for c in active_clues]
        existing_clues_repr = [
            {"clue_id": c["clue_id"], "text": c["text"], "source_genre": c["source_genre"], "epistemic_status": c["epistemic_status"]}
            for c in active_clues
        ]
        next_suggested_id_num = len(session.state["continuity"]["clue_events"]) + 1
        suggested_next_id = f"clue_{next_suggested_id_num:02d}"

        instructions_r3 = (
            "你是一名严格遵守证据主义的白夜国历史文献研究员。\n"
            "系统交付了你在探索中选定的旁支参考资料正文。\n"
            "任务：\n"
            f"1. 线索ID唯一性约束：已有线索ID为 {active_clue_ids}，新线索自身必须分配全新顺延唯一ID（建议从 {suggested_next_id} 起顺延），严禁与已有 ID 重复！\n"
            "2. 提取 2-4 条关键线索更新，陈述客观精炼；操作类型规范：\n"
            "   - 'add': 提取新材料中的独立知识，target_clue_id 必须为 null；\n"
            "   - 'supplement': 针对前置线索补充新证据，target_clue_id 必须指定为已有线索ID；\n"
            "   - 'contrast': 发现叙事对照或观点差异，target_clue_id 必须指定为已有线索ID。\n"
            "3. 来源性质鉴别：对词条获取指引与古籍正文严格区分，不得混为一谈。\n"
            "4. 输出格式（合法JSON）：\n"
            "{\n"
            '  "research_purpose": "本轮研读目的",\n'
            '  "clue_updates": [\n'
            "    {\n"
            '      "operation": "add / supplement / contrast",\n'
            '      "target_clue_id": null,\n'
            '      "reason": "更新原因",\n'
            '      "clue": {\n'
            '        "clue_id": "全新唯一ID",\n'
            '        "text": "知识陈述",\n'
            '        "source_unit_ids": ["必须在本次交付材料unit_ids中"],\n'
            '        "source_genre": "ancient_chronicle_forbidden / folklore_adapted_fiction / official_duty_dialogue",\n'
            '        "epistemic_status": "ancient_official_record / folklore_oral_tradition / duty_testimony",\n'
            '        "narrative_significance": "叙事意义",\n'
            '        "uncertainty_note": "局限性或寓言性说明"\n'
            "      }\n"
            "    }\n"
            "  ],\n"
            '  "updated_summary": "整合最新研究总结",\n'
            '  "unresolved_questions": ["未解决的疑问"]\n'
            "}\n"
        )

        user_input_r3 = (
            f"【上一轮研究总结】:\n{session.state['continuity']['research_summary']}\n\n"
            f"【已有线索列表】:\n" + json.dumps(existing_clues_repr, ensure_ascii=False, indent=2) + "\n\n"
            f"【本次交付的旁支参考材料正文（来源：{related_receipt['record_title']}）】:\n"
            + json.dumps(target_units_repr, ensure_ascii=False, indent=2)
        )

        terminal_r3, text_r3 = runner.call_model("round_03", "旁支材料研读与知识补充", instructions_r3, user_input_r3)
        data_r3 = extract_json_block(text_r3)

        r3_updates = []
        for upd in data_r3.get("clue_updates", []):
            clue = upd.get("clue", {})
            raw_uids = clue.get("source_unit_ids", [])
            if not isinstance(raw_uids, list):
                raw_uids = []
            raw_uids = [str(uid) for uid in raw_uids if isinstance(uid, str)]
            genre = clue.get("source_genre", "unclassified")
            if genre not in VALID_GENRES:
                genre = "unclassified"
            epistemic = clue.get("epistemic_status", "unclassified")
            if epistemic not in VALID_EPISTEMIC:
                epistemic = "unclassified"
            clue_entry = {
                "clue_id": str(clue.get("clue_id", "")),
                "text": str(clue.get("text", "探索线索")),
                "source_unit_ids": raw_uids,
                "source_genre": genre,
                "epistemic_status": epistemic,
                "narrative_significance": str(clue.get("narrative_significance", "旁支文献意义")),
                "uncertainty_note": str(clue["uncertainty_note"]) if clue.get("uncertainty_note") else None,
            }
            target_id = upd.get("target_clue_id")
            if target_id is not None and (target_id == "" or str(target_id).strip().lower() == "null"):
                target_id = None
            op = upd.get("operation", "add")
            if op == "add":
                target_id = None
            r3_updates.append({
                "operation": op,
                "target_clue_id": target_id,
                "reason": str(upd.get("reason", "补充理解")),
                "clue": clue_entry,
            })

        if related_receipt["research_status"] == "pending_understanding":
            summary_r3 = data_r3.get("updated_summary", "完成旁支材料研读。")
            questions_r3 = data_r3.get("unresolved_questions", [])
            res_r3 = session.understand(updates=r3_updates, summary=summary_r3,
                                        receipt_ids=[related_receipt["receipt_id"]],
                                        unresolved_questions=questions_r3)
            if "accepted_updates" not in res_r3:
                rejection_reason = res_r3.get("reason", "stage 3 understanding rejection")
                session.stop(f"Stage 3 rejected: {rejection_reason}")
                return build_expanded_report(session, runner, output_root, baseline_receipts, related_receipts, search_events,
                                             closed_loop_verified=False, rejection_reason=rejection_reason)
            print(f"[Stage 3 Complete] Committed {res_r3['accepted_updates']} clue updates into session.", flush=True)
        else:
            print(f"[Stage 3 Resumed] Receipt {related_receipt['receipt_id']} already processed in session.", flush=True)

    # ----------------------------------------------------
    # STAGE 4: Return to Queue & Deliver Baseline Task 2 (3428: 《止水之潘》)
    # ----------------------------------------------------
    print(f"\n[Stage 4] Returning to baseline queue; advancing to next baseline task...", flush=True)
    r4_receipt = session.read_next()
    baseline_receipts.append(r4_receipt)
    print(f"[Stage 4 Delivered Baseline Task 2] Receipt {r4_receipt['receipt_id']} ({r4_receipt['record_title']}): {len(r4_receipt['units'])} units, chars={sum(len(u['text']) for u in r4_receipt['units'])}", flush=True)

    task2_units_repr = [
        {"section_name": u["section_name"], "unit_ids": u["unit_ids"], "text": u["text"], "char_count": len(u["text"])}
        for u in r4_receipt["units"]
    ]

    active_clues_s4 = session.state["continuity"]["active_clues"]
    active_clue_ids_s4 = [c["clue_id"] for c in active_clues_s4]
    existing_clues_repr_s4 = [
        {"clue_id": c["clue_id"], "text": c["text"], "source_genre": c["source_genre"], "epistemic_status": c["epistemic_status"]}
        for c in active_clues_s4
    ]
    next_suggested_id_num_s4 = len(session.state["continuity"]["clue_events"]) + 1
    suggested_next_id_s4 = f"clue_{next_suggested_id_num_s4:02d}"

    instructions_r4 = (
        "你是一名严格遵守证据主义的白夜国历史文献研究员。\n"
        "你已成功返回版本主线队列！系统现在交付了后续世界任务《止水之潘》的正文。\n"
        "该任务记录了旅行者在海祇岛与巫女露子对话、解除月浴渊祭坛封印、准备开启渊下宫入口的经历。\n"
        "任务（重点：跨任务剧情语义理解）：\n"
        "1. 仔细对比《龙蛇藏归辑录》（渊下宫内部图书馆/末日残影绘真）与本次《止水之潘》（海祇岛地表/现代珊瑚宫巫女露子）的叙事关联。\n"
        "2. 提取 2-4 条跨任务线索更新，陈述客观精炼；跨任务理解更新规范（极其重要）：\n"
        f"   - 已有线索ID为 {active_clue_ids_s4}。\n"
        f"   - 新线索自身必须分配全新顺延唯一ID（建议从 {suggested_next_id_s4} 起顺延），严禁与已有 ID 重复！\n"
        "   - 必须尝试使用 'supplement'（补充前置关于渊下宫封印或海祇岛信仰的线索）或 'contrast'（对比地表巫女的仪式操作与地下残影的被困记忆）。\n"
        f"   - 若操作为 'supplement' 或 'contrast'，target_clue_id 必须指定为前置线索的已有 ID（必须属于 {active_clue_ids_s4}）；\n"
        "   - 若操作为 'add'，target_clue_id 必须为 null。\n"
        "   - 该任务为现代巫女实务对话，标注 source_genre='official_duty_dialogue' 与 epistemic_status='duty_testimony'。\n"
        "3. 输出格式（合法JSON）：\n"
        "{\n"
        '  "research_purpose": "跨任务剧情理解与线索演进目的",\n'
        '  "clue_updates": [\n'
        "    {\n"
        '      "operation": "supplement / contrast / add",\n'
        '      "target_clue_id": "针对已有线索填入其ID（如操作为add则为null）",\n'
        '      "reason": "跨任务更新原因",\n'
        '      "clue": {\n'
        '        "clue_id": "顺延全新唯一ID",\n'
        '        "text": "提取的跨任务新事实",\n'
        '        "source_unit_ids": ["必须在本次交付的止水之潘unit_ids中"],\n'
        '        "source_genre": "official_duty_dialogue",\n'
        '        "epistemic_status": "duty_testimony",\n'
        '        "narrative_significance": "该事实对串联地表与地下白夜国历史的意义",\n'
        '        "uncertainty_note": "存疑之处"\n'
        "      }\n"
        "    }\n"
        "  ],\n"
        '  "updated_summary": "整合地表与地下全景的综合研究总结",\n'
        '  "unresolved_questions": ["跨任务研究后的关键留存问题"]\n'
        "}\n"
    )

    user_input_r4 = (
        f"【已积累的研究总结】:\n{session.state['continuity']['research_summary']}\n\n"
        f"【已有跨任务前置线索列表】:\n" + json.dumps(existing_clues_repr_s4, ensure_ascii=False, indent=2) + "\n\n"
        f"【本次交付的版本主线新任务正文（来源：《止水之潘》）】:\n"
        + json.dumps(task2_units_repr, ensure_ascii=False, indent=2)
    )

    terminal_r4, text_r4 = runner.call_model("round_04", "跨任务剧情理解与继承补充", instructions_r4, user_input_r4)
    data_r4 = extract_json_block(text_r4)

    r4_updates = []
    for upd in data_r4.get("clue_updates", []):
        clue = upd.get("clue", {})
        raw_uids = clue.get("source_unit_ids", [])
        if not isinstance(raw_uids, list):
            raw_uids = []
        raw_uids = [str(uid) for uid in raw_uids if isinstance(uid, str)]
        genre = clue.get("source_genre", "official_duty_dialogue")
        if genre not in VALID_GENRES:
            genre = "unclassified"
        epistemic = clue.get("epistemic_status", "duty_testimony")
        if epistemic not in VALID_EPISTEMIC:
            epistemic = "unclassified"
        clue_entry = {
            "clue_id": str(clue.get("clue_id", "")),
            "text": str(clue.get("text", "跨任务线索")),
            "source_unit_ids": raw_uids,
            "source_genre": genre,
            "epistemic_status": epistemic,
            "narrative_significance": str(clue.get("narrative_significance", "跨任务语义关联")),
            "uncertainty_note": str(clue["uncertainty_note"]) if clue.get("uncertainty_note") else None,
        }
        target_id = upd.get("target_clue_id")
        if target_id is not None and (target_id == "" or str(target_id).strip().lower() == "null"):
            target_id = None
        op = upd.get("operation", "add")
        if op == "add":
            target_id = None
        r4_updates.append({
            "operation": op,
            "target_clue_id": target_id,
            "reason": str(upd.get("reason", "跨任务理解更新")),
            "clue": clue_entry,
        })

    summary_r4 = data_r4.get("updated_summary", "完成《止水之潘》研读及跨任务线索整合。")
    questions_r4 = data_r4.get("unresolved_questions", [])
    res_r4 = session.understand(updates=r4_updates, summary=summary_r4,
                                receipt_ids=[r4_receipt["receipt_id"]],
                                unresolved_questions=questions_r4)
    if "accepted_updates" not in res_r4:
        rejection_reason = res_r4.get("reason", "stage 4 understanding rejection")
        session.stop(f"Stage 4 rejected: {rejection_reason}")
        return build_expanded_report(session, runner, output_root, baseline_receipts, related_receipts, search_events,
                                     closed_loop_verified=False, rejection_reason=rejection_reason)
    print(f"[Stage 4 Complete] Committed {res_r4['accepted_updates']} cross-task clue updates into session.", flush=True)

    # ----------------------------------------------------
    # STAGE 5: Synthesis & Normal Completion
    # ----------------------------------------------------
    stop_reason = "Completed multi-task version progression and cross-task understanding."
    session.stop(stop_reason)
    return build_expanded_report(session, runner, output_root, baseline_receipts, related_receipts, search_events,
                                 closed_loop_verified=True, rejection_reason=None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/retrieval/p05-navi-v-live-deepseek-20261020-r5-expanded"))
    parser.add_argument("--max-budget-rmb", type=float, default=0.05, help="Soft ceiling for estimated cost in RMB")
    parser.add_argument("--max-tokens", type=int, default=100_000, help="Soft ceiling for cumulative tokens")
    args = parser.parse_args(argv)
    run_expanded_experiment(args.output, max_budget_rmb=args.max_budget_rmb, max_tokens=args.max_tokens)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
