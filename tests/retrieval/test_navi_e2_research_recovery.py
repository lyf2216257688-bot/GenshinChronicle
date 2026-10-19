"""tests/retrieval/test_navi_e2_research_recovery.py
Comprehensive Opaque-Box E2E Test Suite for Phase 05 E2 Research Mode & Broadened Error Recovery.

Methodology: Category-Partition + Boundary Value Analysis + Combinatorial + Real-World Workload Testing.
Strictly 100% offline testing with zero live network calls, zero credentials, zero paid API charges.

Covers all 4 Tiers from TEST_INFRA.md:
- Tier 1: Feature Coverage (F1 through F9, >=5 tests per feature area)
- Tier 2: Boundary & Corner Cases (>=5 tests)
- Tier 3: Cross-Feature Combinations (Pairwise, >=5 tests)
- Tier 4: Realistic Application Scenarios (>=5 multi-round scenarios)
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import errno
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
import genshin_corpus.rag.navi_e2_experiment as e2_mod
from genshin_corpus.rag.navi_e2_experiment import (
    E2Budget, E2ResearchBudget, HYPOTHESIS, run_exploration, once, E2SDK,
    body_chars, model_result, lineage_accounting, short_payload,
    SINGLE_ACTION_PROMPT, PROMPT, execute_action, verify_response_attempts,
    reconcile_provider_accounting,
)
from genshin_corpus.rag.navi_e2_routes import E2_ROUTES
from genshin_corpus.retrieval.navi_exploration import (
    ExplorationBudget, ExplorationBudgetExceeded, ExplorationError,
    LocalExplorationTools, response_chars,
)
from tests.responses_fixture import single_message_events
from tests.retrieval.test_navi_exploration import FIXTURE


def decision(actions, memory="fixture hypothesis", rationale="fixture choice"):
    return {
        "actions": actions,
        "memory": {"status": HYPOTHESIS, "text": memory},
        "rationale": rationale,
    }


def make_response(
    model="gpt-6-luna",
    *,
    usage=None,
    status="completed",
    event="response.completed",
    text=None,
    reasoning="summary",
):
    chunks = [
        {"type": "response.reasoning_summary_text.delta", "delta": reasoning},
        {
            "type": "response.output_text.delta",
            "delta": json.dumps(decision([{"action": "stop", "reason": "fixture stop"}]))
            if text is None
            else text,
        },
        {
            "type": event,
            "response": {
                "id": "resp_fixture",
                "model": model,
                "status": status,
                "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}
                if usage is None
                else usage,
            },
        },
    ]
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content="".join(
            "event: " + c["type"] + "\ndata: " + json.dumps(c) + "\n\n"
            for c in single_message_events(chunks)
        ),
    )


class FakeDecisions:
    def __init__(self, decisions, complete=True, usage=True, config=None):
        self.config = config or {
            "route": {"model_id": "fake", "route": "fake_route"},
            "adapter": "fake_recorded_not_real",
        }
        self.decisions = list(decisions)
        self.complete = complete
        self.usage = usage
        self.calls = 0

    def __call__(self, stem, body, identity, **kwargs):
        stem.mkdir(parents=True, exist_ok=True)
        once(stem / "request.json", body)
        once(stem / "wire-request.json", body)
        once(stem / "issued.json", {"run_identity": identity, "request_identity": sha256_json(body)})
        dec = (
            self.decisions[self.calls]
            if self.calls < len(self.decisions)
            else decision([{"action": "stop", "reason": "exhausted decisions"}])
        )
        content = json.dumps(dec) if isinstance(dec, (dict, list)) else str(dec)
        result = {
            "calls": 1,
            "complete": self.complete,
            "decision_complete": self.complete,
            "finish_reason": "stop" if self.complete else "length",
            "content": content,
            "usage": {"total_tokens": 200, "input_tokens": 100, "output_tokens": 100}
            if self.usage
            else "UNKNOWN",
        }
        result["terminal_identity"] = sha256_json(result)
        once(stem / "terminal.json", result)
        self.calls += 1
        return result


from genshin_corpus.retrieval import candidate_retrieval as lexical
from genshin_corpus.retrieval import retrieval_units as ru


class E2RecoveryBase(unittest.TestCase):
    """Base class providing checked-in Canonical corpus, index, tools, and mock environments."""

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.canonical_path = self.root / "canonical/metadata/manifest.json"
        self.canonical_path.parent.mkdir(parents=True)
        record = json.loads(FIXTURE.read_text(encoding="utf-8"))
        records = []
        self.record_paths = []
        for index in range(2):
            copied = deepcopy(record)
            copied["record_id"] = f"record-{index}"
            copied["source_identity"]["components"]["content_id"] = f"fixture-{index}"
            copied["source_identity"]["key"] = f"mihoyo_obc:zh-cn:fixture-{index}"
            copied["record_metadata"]["name"] = f"Library {index}"
            for section in copied["sections"]:
                for unit in section["units"]:
                    if unit["kind"] == "rich_text":
                        unit["value"]["normalized_text"] = f"Library {index} says: a legend is not established history."
            path = self.root / f"canonical/record-{index}.json"
            body = canonical_json_bytes(copied)
            path.write_bytes(body)
            self.record_paths.append(path)
            records.append({
                "record_id": copied["record_id"],
                "canonical_record_path": str(path),
                "canonical_record_sha256": sha256(body).hexdigest(),
                "canonical_status": "canonical",
            })
        manifest = {
            "status": "complete", "canonical_run_id": "e1-fixture", "source": "mihoyo_obc", "locale": "zh-cn",
            "input_record_count": 2, "accounted_record_count": 2, "input_integrity_failure_count": 0,
            "dependencies": {"canonical_versions": record["versions"]}, "records": records,
        }
        self.canonical_path.write_bytes(canonical_json_bytes(manifest))
        ru.build_retrieval_units(
            self.canonical_path,
            self.root / "ru",
            config=ru.RetrievalUnitBuildConfig(text_fragment_chars=1200, structured_fragment_chars=1200),
        )
        self.ru_path = self.root / "ru/metadata/manifest.json"
        _, rows = ru.load_retrieval_units(self.ru_path)
        self.index = {r["unit_id"]: r for r in rows}
        self.rich = [r for r in rows if r["content_type"] == "rich_text"]
        self.uid = self.rich[0]["unit_id"]
        self.other = next(r["unit_id"] for r in self.rich if r["source"]["canonical_address"]["record_id"] == "record-1")
        lexical.build_lexical_index(self.ru_path, self.root / "lexical")
        self.lexical_path = self.root / "lexical/metadata/manifest.json"
        self.tools = LocalExplorationTools(self.canonical_path, self.ru_path, self.lexical_path)
        self.question = "What does the library claim?"

        self.env = {
            "TOKENMETRO_BASE_URL": "https://example.invalid/v1",
            "TOKENMETRO_API_KEY": "fixture-secret",
            "NAVI_QWEN_BASE_URL": "https://example.invalid/v1",
            "NAVI_QWEN_API_KEY": "qwen-secret",
            "JIZHI_BASE_URL": "https://example.invalid/v1",
            "JIZHI_API_KEY": "jizhi-secret",
            "NAVI_GLM_BASE_URL": "https://example.invalid/v1",
            "NAVI_GLM_API_KEY": "glm-secret",
        }
        self.body = {
            "model": "gpt-6-luna",
            "instructions": "test",
            "input": "test",
            "stream": True,
            "max_output_tokens": 100,
        }

    def run_e2(self, provider, name="e2_exp", budget=None, **kwargs):
        return run_exploration(
            self.tools,
            start_ru=self.uid,
            output_root=self.root / name,
            provider=provider,
            question=self.question,
            budget=budget or E2Budget(),
            **kwargs,
        )

    def sdk(self, handler, route="tokenmetro", model="gpt-6-luna", **kwargs):
        return E2SDK(
            route,
            model=model,
            environment=self.env,
            transport=httpx.MockTransport(handler),
            **kwargs,
        )


# ==============================================================================
# TIER 1 - FEATURE COVERAGE (F1 to F9)
# ==============================================================================


class TestF1UnifiedResearchBudget(E2RecoveryBase):
    """F1: Unified Research Budget Profile across all 4 routes with zero model branching."""

    def test_f1_01_all_four_routes_share_identical_research_budget_defaults(self):
        """All 4 routes accept E2ResearchBudget with exact identical defaults and limits."""
        b = E2ResearchBudget()
        self.assertEqual(b.rounds, 20)
        self.assertEqual(b.searches, 30)
        self.assertEqual(b.reads, 50)
        self.assertEqual(b.pages, 30)
        self.assertEqual(b.canonical_chars, 100000)
        self.assertEqual(b.total_tokens, 250000)
        self.assertEqual(b.output_tokens, 8192)
        self.assertEqual(b.tool_response_chars, 500000)
        self.assertEqual(b.profile, "research")

        for model, route in E2_ROUTES:
            with self.subTest(model=model, route=route):
                fake = FakeDecisions(
                    [decision([{"action": "stop", "reason": f"test on {model}"}])],
                    config={"route": {"model_id": model, "route": route}, "adapter": "fake"},
                )
                state = self.run_e2(fake, name=f"e2_route_{model}", budget=b)
                self.assertEqual(state["status"], "stopped")
                self.assertEqual(state["budget"]["profile"], "research")
                self.assertEqual(state["budget"]["rounds"], 20)

    def test_f1_02_custom_research_budget_within_ceiling_accepted_across_routes(self):
        """Custom research limits within bounds are universally accepted."""
        custom_budget = E2ResearchBudget.research(
            rounds=30,
            searches=50,
            reads=100,
            pages=40,
            canonical_chars=200000,
            total_tokens=500000,
            tool_response_chars=800000,
        )
        self.assertEqual(custom_budget.rounds, 30)
        self.assertEqual(custom_budget.reads, 100)
        self.assertEqual(custom_budget.profile, "research")

        fake = FakeDecisions(
            [decision([{"action": "stop", "reason": "custom budget test"}])],
            config={"route": {"model_id": "qwen3.8-flash", "route": "tokenmetro_qwen"}, "adapter": "fake"},
        )
        state = self.run_e2(fake, name="e2_custom_research", budget=custom_budget)
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["budget"]["rounds"], 30)
        self.assertEqual(state["budget"]["reads"], 100)

    def test_f1_03_research_budget_ceiling_guards_enforce_bounded_execution(self):
        """Exceeding hard bounds under research profile raises ExplorationError."""
        valid_kw = dict(
            rounds=20, searches=30, reads=50, pages=30,
            canonical_chars=100000, total_tokens=250000,
            output_tokens=8192, tool_response_chars=500000, profile="research",
        )
        # Exceeding rounds (max 50)
        with self.assertRaises(ExplorationError):
            E2Budget(**dict(valid_kw, rounds=51))
        # Exceeding searches (max 100)
        with self.assertRaises(ExplorationError):
            E2Budget(**dict(valid_kw, searches=101))
        # Exceeding reads (max 200)
        with self.assertRaises(ExplorationError):
            E2Budget(**dict(valid_kw, reads=201))
        # Exceeding pages (max 100)
        with self.assertRaises(ExplorationError):
            E2Budget(**dict(valid_kw, pages=101))
        # Exceeding canonical_chars (max 500,000)
        with self.assertRaises(ExplorationError):
            E2Budget(**dict(valid_kw, canonical_chars=500001))
        # Exceeding total_tokens (max 2,000,000)
        with self.assertRaises(ExplorationError):
            E2Budget(**dict(valid_kw, total_tokens=2000001))
        # Exceeding output_tokens (max 8,192)
        with self.assertRaises(ExplorationError):
            E2Budget(**dict(valid_kw, output_tokens=8193))
        # Non-positive values fail
        with self.assertRaises(ExplorationError):
            E2Budget(**dict(valid_kw, rounds=0))

    def test_f1_04_standard_mode_budget_remains_frozen_and_independent(self):
        """Standard budget profile remains completely frozen and rejects research-sized limits."""
        std = E2Budget()
        self.assertEqual(std.profile, "standard")
        self.assertEqual(std.rounds, 6)
        self.assertEqual(std.searches, 8)
        self.assertEqual(std.reads, 12)
        self.assertEqual(std.pages, 8)
        self.assertEqual(std.canonical_chars, 20000)
        self.assertEqual(std.total_tokens, 100000)

        # Standard ceilings remain strictly enforced
        with self.assertRaises(ExplorationError):
            E2Budget(rounds=7)
        with self.assertRaises(ExplorationError):
            E2Budget(reads=13)
        with self.assertRaises(ExplorationError):
            E2Budget(canonical_chars=20001)

    def test_f1_05_session_identity_and_checkpoints_record_budget_profile(self):
        """Session checkpoint, config.json and terminal records persist research budget profile."""
        b = E2ResearchBudget()
        fake = FakeDecisions([decision([{"action": "stop", "reason": "checkpoint check"}])])
        state = self.run_e2(fake, name="e2_checkpoint_check", budget=b)

        conf_path = self.root / "e2_checkpoint_check/config.json"
        self.assertTrue(conf_path.exists())
        conf = json.loads(conf_path.read_text(encoding="utf-8"))
        self.assertEqual(conf["budget"]["profile"], "research")
        self.assertEqual(conf["budget"]["rounds"], 20)

        sess_path = self.root / "e2_checkpoint_check/session.json"
        sess = json.loads(sess_path.read_text(encoding="utf-8"))
        self.assertEqual(sess["budget"]["profile"], "research")

        ckpt_id = state["checkpoint_identity"]
        ckpt_path = self.root / f"e2_checkpoint_check/checkpoints/{ckpt_id}.json"
        self.assertTrue(ckpt_path.exists())
        ckpt = json.loads(ckpt_path.read_text(encoding="utf-8"))
        self.assertEqual(ckpt["budget"]["profile"], "research")


class TestF2PromptGuidance(E2RecoveryBase):
    """F2: Adaptive Read Capacity Guidance in Prompts while standard PROMPT remains untouched."""

    def test_f2_01_research_prompt_contains_recommended_capacity_guidance(self):
        """RESEARCH_PROMPT contains 4,000~8,000+ chars, 2M limit, metadata overhead, and retry guidance."""
        self.assertTrue(hasattr(e2_mod, "RESEARCH_PROMPT"), "RESEARCH_PROMPT must be defined in navi_e2_experiment")
        prompt = getattr(e2_mod, "RESEARCH_PROMPT")
        self.assertIn("unit_id", prompt["tools"]["read"])
        self.assertIn("model_round", prompt["tools"]["read"])
        self.assertNotIn("hit:", json.dumps(prompt, ensure_ascii=False))

        read_desc = prompt["tools"]["read"]
        self.assertIn("4,000~8,000+", read_desc)
        self.assertIn("2,000,000", read_desc)
        self.assertIn("2,000+", read_desc)
        self.assertIn("read_budget_too_small", read_desc)
        self.assertIn("min_required_max_chars", read_desc)

    def test_f2_02_research_single_action_prompt_contains_matching_guidance(self):
        """RESEARCH_SINGLE_ACTION_PROMPT contains matching single-action guidance."""
        self.assertTrue(
            hasattr(e2_mod, "RESEARCH_SINGLE_ACTION_PROMPT"),
            "RESEARCH_SINGLE_ACTION_PROMPT must be defined",
        )
        prompt = getattr(e2_mod, "RESEARCH_SINGLE_ACTION_PROMPT")
        limits = prompt.get("limits", "")
        limits_str = limits[0] if isinstance(limits, (list, tuple)) else str(limits)
        read_desc = prompt["tools"]["read"]
        combined = limits_str + " " + read_desc
        self.assertIn("4,000~8,000+", combined)
        self.assertIn("2,000,000", combined)
        self.assertIn("2,000+", combined)
        self.assertIn("read_budget_too_small", combined)
        self.assertIn("min_required_max_chars", combined)

    def test_f2_03_standard_prompts_remain_frozen_without_research_guidance(self):
        """Standard PROMPT and SINGLE_ACTION_PROMPT do not contain research guidance."""
        for p in (PROMPT, SINGLE_ACTION_PROMPT):
            dump = json.dumps(p, ensure_ascii=False)
            self.assertNotIn("4,000~8,000+", dump)
            self.assertNotIn("read_budget_too_small", dump)
            self.assertNotIn("min_required_max_chars", dump)

    def test_f2_04_run_exploration_dynamically_selects_research_prompt_in_batch_mode(self):
        """run_exploration selects RESEARCH_PROMPT when budget.profile == 'research'."""
        if not hasattr(e2_mod, "RESEARCH_PROMPT"):
            self.skipTest("RESEARCH_PROMPT not defined")
        res_prompt = getattr(e2_mod, "RESEARCH_PROMPT")

        fake = FakeDecisions([decision([{"action": "stop", "reason": "check prompt"}])])
        self.run_e2(fake, name="e2_prompt_res", budget=E2ResearchBudget())
        conf_res = json.loads((self.root / "e2_prompt_res/config.json").read_text(encoding="utf-8"))
        self.assertEqual(canonical_json_bytes(conf_res["prompt"]), canonical_json_bytes(res_prompt))

        fake2 = FakeDecisions([decision([{"action": "stop", "reason": "check prompt"}])])
        self.run_e2(fake2, name="e2_prompt_std", budget=E2Budget())
        conf_std = json.loads((self.root / "e2_prompt_std/config.json").read_text(encoding="utf-8"))
        self.assertEqual(conf_std["prompt"], PROMPT)

    def test_f2_05_run_exploration_dynamically_selects_research_single_action_prompt_in_continuation(self):
        """run_exploration selects RESEARCH_SINGLE_ACTION_PROMPT for single action continuation under research."""
        if not hasattr(e2_mod, "RESEARCH_SINGLE_ACTION_PROMPT"):
            self.skipTest("RESEARCH_SINGLE_ACTION_PROMPT not defined")
        res_single_prompt = getattr(e2_mod, "RESEARCH_SINGLE_ACTION_PROMPT")

        # Create parent session that stopped due to token reservation ceiling
        parent = self.run_e2(FakeDecisions([]), name="e2_parent_res", budget=E2ResearchBudget(total_tokens=100))
        self.assertEqual(parent["status"], "budget_exhausted")

        # Child continuation with research budget and single_action_continuation
        child = run_exploration(
            self.tools,
            start_ru=self.uid,
            output_root=self.root / "e2_child_res",
            provider=FakeDecisions([{"action": "stop", "reason": "done single action"}]),
            question=self.question,
            parent_root=self.root / "e2_parent_res",
            single_action_continuation=True,
            budget=E2ResearchBudget(total_tokens=100, output_tokens=4096),
        )
        conf_child = json.loads((self.root / "e2_child_res/config.json").read_text(encoding="utf-8"))
        self.assertEqual(canonical_json_bytes(conf_child["prompt"]), canonical_json_bytes(res_single_prompt))

    def test_f2_06_e2sdk_attempt_detects_research_single_action_prompt(self):
        """E2SDK._attempt recognizes RESEARCH_SINGLE_ACTION_PROMPT as single_action decision_mode."""
        if not hasattr(e2_mod, "RESEARCH_SINGLE_ACTION_PROMPT"):
            self.skipTest("RESEARCH_SINGLE_ACTION_PROMPT not defined")
        single_res_json = canonical_json_bytes(getattr(e2_mod, "RESEARCH_SINGLE_ACTION_PROMPT")).decode("utf-8")

        act = {"action": "stop", "reason": "single action prompt detected"}
        resp = make_response(text=json.dumps(act))
        sdk = self.sdk(lambda r: resp)
        stem = self.root / "rounds/00"
        stem.mkdir(parents=True, exist_ok=True)
        res = sdk._attempt(
            stem / "attempts/01",
            {"model": "gpt-6-luna", "instructions": single_res_json, "input": "test", "stream": True, "max_output_tokens": 100},
            "run_test",
        )
        self.assertTrue(res["complete"])
        self.assertTrue(res["decision_complete"])


class TestF3NonFatalReadCapacity(E2RecoveryBase):
    """F3: Small read budget requests returning read_budget_too_small with min_required_max_chars."""

    def test_f3_01_small_max_chars_returns_read_budget_too_small_dict(self):
        """Small max_chars on tools.read returns structured non-fatal error dict."""
        res = self.tools.read(self.uid, max_chars=10, fail_on_metadata_budget=False)
        self.assertIsInstance(res, dict)
        self.assertEqual(res.get("error"), "read_budget_too_small")
        self.assertEqual(res.get("requested_max_chars"), 10)
        self.assertGreater(res.get("min_required_max_chars"), 10)
        self.assertEqual(res.get("unit_id"), self.uid)
        self.assertIn("source", res)

    def test_f3_02_read_budget_too_small_does_not_abort_session(self):
        """read_budget_too_small does not crash run_exploration with fatal budget_exhausted."""
        fake = FakeDecisions([
            decision([
                {"action": "read", "unit_id": self.uid, "max_chars": 15},
                {"action": "stop", "reason": "graceful continuation after small read"},
            ]),
        ])
        state = self.run_e2(fake, name="e2_small_read_non_fatal", budget=E2ResearchBudget())
        self.assertEqual(state["status"], "stopped")
        read_events = [e for e in state["events"] if e["action"]["action"] == "read"]
        # Seed read + 1 attempted read with error
        self.assertEqual(len(read_events), 2)
        self.assertEqual(read_events[1]["result"].get("error"), "read_budget_too_small")

    def test_f3_03_resource_accounting_charges_reads_and_tool_chars_only(self):
        """Small read error charges read count and tool_response_chars, but 0 canonical_chars."""
        fake = FakeDecisions([
            decision([
                {"action": "read", "unit_id": self.uid, "max_chars": 20},
                {"action": "stop", "reason": "verify accounting"},
            ]),
        ])
        state = self.run_e2(fake, name="e2_accounting_check", budget=E2ResearchBudget())
        read_events = [e for e in state["events"] if e["action"]["action"] == "read"]
        seed_chars = body_chars(read_events[0]["result"])

        self.assertEqual(state["used"]["reads"], 2)  # seed + 1 small read
        # Canonical body chars only charged for seed read
        self.assertEqual(state["used"]["canonical_chars"], seed_chars)
        # Tool response chars increased by the delivered error result
        err_chars = response_chars(read_events[1]["result"])
        self.assertGreaterEqual(state["used"]["tool_response_chars"], err_chars)
        # Read sources and pages did not record the failed read
        self.assertEqual(len(state["read_sources"]), 1)

    def test_f3_04_model_adapts_with_min_required_max_chars_in_subsequent_action(self):
        """Model observes min_required_max_chars and adapts in round 2 to succeed."""
        err_probe = self.tools.read(self.uid, max_chars=10, fail_on_metadata_budget=False)
        required_chars = err_probe["min_required_max_chars"]

        fake = FakeDecisions([
            decision([{"action": "read", "unit_id": self.uid, "max_chars": 10}]),
            decision([
                {"action": "read", "unit_id": self.uid, "max_chars": required_chars + 500},
                {"action": "clue", "text": "successfully read after adapting", "source_unit_ids": [self.uid]},
                {"action": "stop", "reason": "adaptation verified"},
            ]),
        ])
        state = self.run_e2(fake, name="e2_adapt_round2", budget=E2ResearchBudget())
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["model_rounds"], 2)
        self.assertEqual(len(state["pending_clues"]), 1)
        self.assertEqual(state["pending_clues"][0]["source_unit_ids"], [self.uid])

    def test_f3_05_read_budget_too_small_with_fail_on_metadata_budget_true_raises_exception(self):
        """Strict mode fail_on_metadata_budget=True raises ExplorationBudgetExceeded."""
        with self.assertRaises(ExplorationBudgetExceeded) as ctx:
            self.tools.read(self.uid, max_chars=10, fail_on_metadata_budget=True)
        self.assertEqual(str(ctx.exception), "read metadata cannot fit context budget")
        self.assertGreater(ctx.exception.min_required_chars, 10)


class TestF4BroadenedAttemptCeiling(E2RecoveryBase):
    """F4: Configurable attempt limit up to 5 attempts in research mode with fail-closed stopping."""

    def test_f4_01_research_mode_defaults_to_5_max_attempts(self):
        """E2SDK under research mode defaults max_attempts to 5."""
        sdk = self.sdk(lambda r: make_response(), mode="research")
        self.assertEqual(sdk.max_attempts, 5)
        self.assertEqual(sdk.config["max_attempts"], 5)

    def test_f4_02_research_mode_supports_configurable_attempts_1_to_5(self):
        """E2SDK under research mode accepts any max_attempts between 1 and 5."""
        for limit in (1, 2, 3, 4, 5):
            with self.subTest(limit=limit):
                sdk = self.sdk(lambda r: make_response(), mode="research", max_attempts=limit)
                self.assertEqual(sdk.max_attempts, limit)
                self.assertEqual(sdk.config["max_attempts"], limit)

    def test_f4_03_research_mode_rejects_attempts_outside_1_to_5(self):
        """E2SDK under research mode rejects attempts < 1 or > 5."""
        for bad in (0, 6, -1, 10, True):
            with self.subTest(bad=bad), self.assertRaises(ExplorationError):
                self.sdk(lambda r: make_response(), mode="research", max_attempts=bad)

    def test_f4_04_standard_mode_strictly_retains_1_to_3_attempts(self):
        """E2SDK under standard mode enforces strict 1..3 attempts."""
        sdk_std = self.sdk(lambda r: make_response(), mode="standard")
        self.assertEqual(sdk_std.max_attempts, 3)
        with self.assertRaises(ExplorationError):
            self.sdk(lambda r: make_response(), mode="standard", max_attempts=4)
        with self.assertRaises(ExplorationError):
            self.sdk(lambda r: make_response(), mode="standard", max_attempts=5)

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f4_05_fail_closed_stopping_at_attempt_ceiling(self, sleep):
        """When retries persist to ceiling, halts fail-closed on attempt 5 without issuing attempt 6."""
        seen = []
        def handler(req):
            seen.append(req)
            return httpx.Response(500, json={"error": {"code": "server_error", "message": "persistent 500"}})

        sdk = self.sdk(handler, mode="research", max_attempts=5)
        stem = self.root / "rounds/00"
        res = sdk(stem, self.body, "run_attempt_ceiling", attempt_limit=5)
        self.assertEqual(res["calls"], 5)
        self.assertEqual(res["retry_calls"], 4)
        self.assertFalse(res["complete"])
        self.assertFalse(res["decision_complete"])
        self.assertEqual(len(seen), 5)
        self.assertFalse((stem / "attempts/06").exists())


class TestF5NetworkProviderRecovery(E2RecoveryBase):
    """F5: Network and provider error recovery (SSL disconnects, timeouts, 429 across routes, 5xx)."""

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f5_01_research_retry_recovers_from_read_timeout(self, sleep):
        """ReadTimeout on attempt 1 retries and recovers on attempt 2."""
        calls = []
        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                raise httpx.ReadTimeout("socket timeout on attempt 1", request=req)
            return make_response()

        sdk = self.sdk(handler, mode="research")
        stem = self.root / "rounds/00"
        res = sdk(stem, self.body, "run_timeout_recovery")
        self.assertEqual(res["calls"], 2)
        self.assertEqual(res["retry_calls"], 1)
        self.assertTrue(res["complete"])
        self.assertTrue(res["decision_complete"])

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f5_02_research_retry_recovers_from_ssl_connection_reset(self, sleep):
        """ConnectionResetError (wrapped in ConnectError) retries and recovers on attempt 2."""
        calls = []
        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                raise httpx.ConnectError("SSL connection reset by peer", request=req)
            return make_response()

        sdk = self.sdk(handler, mode="research")
        stem = self.root / "rounds/00"
        res = sdk(stem, self.body, "run_ssl_recovery")
        self.assertEqual(res["calls"], 2)
        self.assertEqual(res["retry_calls"], 1)
        self.assertTrue(res["complete"])

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f5_03_research_retry_recovers_from_http_429_on_non_tokenmetro_route(self, sleep):
        """HTTP 429 on jizhi route (DeepSeek) retries and recovers on attempt 2."""
        calls = []
        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                return httpx.Response(429, json={"error": {"code": "rate_limit_exceeded", "message": "rate limit"}})
            return make_response(model="deepseek-v4.1-flash")

        sdk = self.sdk(handler, route="jizhi", model="deepseek-v4.1-flash", mode="research")
        stem = self.root / "rounds/00"
        body = dict(self.body, model="deepseek-v4.1-flash")
        res = sdk(stem, body, "run_429_jizhi")
        self.assertEqual(res["calls"], 2)
        self.assertEqual(res["retry_calls"], 1)
        self.assertTrue(res["complete"])

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f5_04_research_retry_recovers_from_http_500_502_503_524_server_errors(self, sleep):
        """HTTP 500, 502, 503, 524 server errors retry and recover on attempt 2."""
        for status in (500, 502, 503, 524):
            with self.subTest(status=status):
                calls = []
                def handler(req):
                    calls.append(req)
                    if len(calls) == 1:
                        return httpx.Response(status, json={"error": {"code": f"error_{status}", "message": "temp fail"}})
                    return make_response()

                stem = self.root / f"rounds_status_{status}/00"
                sdk = self.sdk(handler, mode="research")
                res = sdk(stem, self.body, f"run_server_error_{status}")
                self.assertEqual(res["calls"], 2)
                self.assertEqual(res["retry_calls"], 1)
                self.assertTrue(res["complete"])

    def test_f5_05_standard_mode_rejects_network_and_5xx_retry(self):
        """In standard mode, network transport errors do not retry, halting immediately."""
        calls = []
        def handler(req):
            calls.append(req)
            raise httpx.ReadTimeout("standard mode network error", request=req)

        sdk = self.sdk(handler, mode="standard")
        stem = self.root / "rounds/00"
        res = sdk(stem, self.body, "run_std_no_retry")
        self.assertEqual(res["calls"], 1)
        self.assertEqual(res["retry_calls"], 0)
        self.assertFalse(res["retry_eligible"])
        self.assertEqual(res["execution_state"], "EXECUTION UNKNOWN")

    def test_f5_06_http_401_client_rejection_halts_immediately_without_retry(self):
        """HTTP 401 Unauthorized halts immediately as REJECTED without retry in research mode."""
        calls = []
        def handler(req):
            calls.append(req)
            return httpx.Response(401, json={"error": {"code": "invalid_api_key", "message": "Incorrect API key"}})

        sdk = self.sdk(handler, mode="research")
        stem = self.root / "rounds_401/00"
        res = sdk(stem, self.body, "run_401_no_retry")
        self.assertEqual(res["calls"], 1)
        self.assertEqual(res["retry_calls"], 0)
        self.assertFalse(res["retry_eligible"])
        self.assertEqual(res["execution_state"], "REJECTED")

    def test_f5_07_http_403_client_rejection_halts_immediately_without_retry(self):
        """HTTP 403 Forbidden halts immediately as REJECTED without retry in research mode."""
        calls = []
        def handler(req):
            calls.append(req)
            return httpx.Response(403, json={"error": {"code": "permission_denied", "message": "Access denied"}})

        sdk = self.sdk(handler, mode="research")
        stem = self.root / "rounds_403/00"
        res = sdk(stem, self.body, "run_403_no_retry")
        self.assertEqual(res["calls"], 1)
        self.assertEqual(res["retry_calls"], 0)
        self.assertFalse(res["retry_eligible"])
        self.assertEqual(res["execution_state"], "REJECTED")

    def test_f5_08_classifier_rejects_auth_and_permission_errors_even_if_diagnostics_present(self):
        """Classifier gives precedence to client rejections over transport diagnostics."""
        from genshin_corpus.retrieval.semantic_sdk_runner import research_retry_classifier

        # Case A: 401 with diagnostics attached - MUST be REJECTED, not retried
        probe_401 = {"http_status": 401, "stream_chunk_count": 0, "visible_chars": 0, "sdk_error_type": "AuthenticationError"}
        err_401 = {"type": "AuthenticationError", "diagnostics": {"exceptions": [{"type": "openai.AuthenticationError"}]}}
        eligible_401, state_401 = research_retry_classifier(probe_401, b"{}", error=err_401)
        self.assertFalse(eligible_401)
        self.assertEqual(state_401, "REJECTED")

        # Case B: 403 with diagnostics attached - MUST be REJECTED, not retried
        probe_403 = {"http_status": 403, "stream_chunk_count": 0, "visible_chars": 0, "sdk_error_type": "PermissionDeniedError"}
        err_403 = {"type": "PermissionDeniedError", "diagnostics": {"exceptions": [{"type": "openai.PermissionDeniedError"}]}}
        eligible_403, state_403 = research_retry_classifier(probe_403, b"{}", error=err_403)
        self.assertFalse(eligible_403)
        self.assertEqual(state_403, "REJECTED")

        # Case C: Genuine network error with diagnostics - MUST be retry-eligible EXECUTION UNKNOWN
        probe_net = {"http_status": None, "stream_chunk_count": 0, "visible_chars": 0, "sdk_error_type": "APIConnectionError"}
        err_net = {"type": "APIConnectionError", "diagnostics": {"exceptions": [{"type": "openai.APIConnectionError"}]}}
        eligible_net, state_net = research_retry_classifier(probe_net, None, error=err_net)
        self.assertTrue(eligible_net)
        self.assertEqual(state_net, "EXECUTION UNKNOWN")


class TestF6DuplicateBillingRisk(E2RecoveryBase):
    """F6: Duplicate billing risk tracking across attempts, rounds, and session accounting."""

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f6_01_uncertain_execution_state_sets_duplicate_billing_risk_true(self, sleep):
        """Retrying from EXECUTION UNKNOWN sets duplicate_billing_risk=True."""
        calls = []
        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                raise httpx.ReadTimeout("timeout on wire", request=req)
            return make_response()

        sdk = self.sdk(handler, mode="research")
        stem = self.root / "rounds/00"
        res = sdk(stem, self.body, "run_risk_true")

        self.assertTrue(res["duplicate_billing_risk"])
        retry_path = stem / "attempts/01/retry.json"
        self.assertTrue(retry_path.exists())
        retry_data = json.loads(retry_path.read_text(encoding="utf-8"))
        self.assertTrue(retry_data["duplicate_billing_risk"])

        att2_term = json.loads((stem / "attempts/02/terminal.json").read_text(encoding="utf-8"))
        self.assertTrue(att2_term["duplicate_billing_risk"])

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f6_02_pre_generation_429_sets_duplicate_billing_risk_false(self, sleep):
        """Retrying from NOT_STARTED (pre-generation 429) sets duplicate_billing_risk=False."""
        calls = []
        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                return httpx.Response(429, json={"error": {"code": "RateLimitExceeded.EndpointTPMExceeded"}})
            return make_response()

        sdk = self.sdk(handler, mode="research")
        stem = self.root / "rounds/00"
        res = sdk(stem, self.body, "run_risk_false")

        self.assertFalse(res["duplicate_billing_risk"])
        retry_data = json.loads((stem / "attempts/01/retry.json").read_text(encoding="utf-8"))
        self.assertFalse(retry_data["duplicate_billing_risk"])

    def test_f6_03_session_accounting_aggregates_duplicate_billing_risk(self):
        """reconcile_provider_accounting sets session duplicate_billing_risk=True when risk occurred."""
        round_stem = self.root / "session_risk/rounds/00"
        round_stem.mkdir(parents=True, exist_ok=True)
        att1 = round_stem / "attempts/01"
        att1.mkdir(parents=True)
        once(att1 / "issued.json", {"run_identity": "r1", "request_identity": "req1"})
        once(att1 / "request.json", {"test": 1})
        once(att1 / "wire-request.json", {"test": 1})
        term1 = {"execution_state": "EXECUTION UNKNOWN", "duplicate_billing_risk": False, "run_identity": "r1"}
        term1["terminal_identity"] = sha256_json(term1)
        once(att1 / "terminal.json", term1)

        att2 = round_stem / "attempts/02"
        att2.mkdir(parents=True)
        once(att2 / "issued.json", {"run_identity": "r1", "request_identity": "req2"})
        once(att2 / "request.json", {"test": 2})
        once(att2 / "wire-request.json", {"test": 2})
        term2 = {"execution_state": "RESOLVED", "duplicate_billing_risk": True, "run_identity": "r1", "usage": {"total_tokens": 100}}
        term2["terminal_identity"] = sha256_json(term2)
        once(att2 / "terminal.json", term2)

        round_term = {
            "calls": 2, "retry_calls": 1, "run_identity": "r1",
            "attempt_identities": [term1["terminal_identity"], term2["terminal_identity"]],
            "duplicate_billing_risk": True,
        }
        round_term["terminal_identity"] = sha256_json(round_term)
        once(round_stem / "terminal.json", round_term)

        state = {"used": {"tokens": 0}, "events": []}
        reconcile_provider_accounting(self.root / "session_risk", state)

        self.assertTrue(state.get("duplicate_billing_risk"))
        self.assertTrue(state["provider_audit"].get("duplicate_billing_risk"))
        self.assertGreaterEqual(state["provider_audit"].get("duplicate_billing_risk_calls", 0), 1)

    def test_f6_04_session_accounting_zero_risk_for_clean_or_pregen_runs(self):
        """Clean exploration session records duplicate_billing_risk=False."""
        fake = FakeDecisions([decision([{"action": "stop", "reason": "clean run"}])])
        state = self.run_e2(fake, name="e2_clean_risk", budget=E2ResearchBudget())
        self.assertFalse(state.get("duplicate_billing_risk", False))
        self.assertFalse(state["provider_audit"].get("duplicate_billing_risk", False))
        self.assertEqual(state["provider_audit"].get("duplicate_billing_risk_calls", 0), 0)

    def test_f6_05_duplicate_billing_risk_preserved_across_checkpoint_save_and_resume(self):
        """duplicate_billing_risk flag is preserved in immutable checkpoints."""
        fake = FakeDecisions([decision([{"action": "stop", "reason": "checkpoint risk"}])])
        state = self.run_e2(fake, name="e2_ckpt_risk", budget=E2ResearchBudget())
        state["duplicate_billing_risk"] = True
        state["provider_audit"]["duplicate_billing_risk"] = True

        from genshin_corpus.rag.navi_e2_experiment import _save
        _save(self.root / "e2_ckpt_risk", state)

        saved_sess = json.loads((self.root / "e2_ckpt_risk/session.json").read_text(encoding="utf-8"))
        self.assertTrue(saved_sess["duplicate_billing_risk"])


class TestF7ModelOutputFormatRecovery(E2RecoveryBase):
    """F7: Model output format error recovery with structured re-prompt within attempt limit."""

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f7_01_format_error_malformed_json_triggers_correction_feedback_and_recovers(self, sleep):
        """Malformed JSON prose triggers format error retry and recovers on attempt 2."""
        calls = []
        valid_dec = decision([{"action": "stop", "reason": "recovered from malformed json"}])

        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                return make_response(text="I will now proceed to search the archives for information.")
            return make_response(text=json.dumps(valid_dec))

        sdk = self.sdk(handler, mode="research")
        stem = self.root / "rounds/00"
        res = sdk(stem, self.body, "run_format_recovery")

        self.assertEqual(res["calls"], 2)
        self.assertEqual(res["retry_calls"], 1)
        self.assertTrue(res["decision_complete"])
        retry_path = stem / "attempts/01/retry.json"
        self.assertTrue(retry_path.exists())
        retry_info = json.loads(retry_path.read_text(encoding="utf-8"))
        self.assertEqual(retry_info.get("retry_reason"), "format_error")

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f7_02_format_error_missing_schema_keys_triggers_correction(self, sleep):
        """JSON missing required top-level schema keys triggers format correction."""
        calls = []
        invalid_schema = {"actions": [{"action": "stop", "reason": "missing memory"}]}
        valid_schema = decision([{"action": "stop", "reason": "schema complete"}])

        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                return make_response(text=json.dumps(invalid_schema))
            return make_response(text=json.dumps(valid_schema))

        sdk = self.sdk(handler, mode="research")
        stem = self.root / "rounds/00"
        res = sdk(stem, self.body, "run_schema_recovery")

        self.assertEqual(res["calls"], 2)
        self.assertTrue(res["decision_complete"])

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f7_03_format_error_retry_has_zero_duplicate_billing_risk(self, sleep):
        """Format error retry has duplicate_billing_risk=False since HTTP request completed."""
        calls = []
        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                return make_response(text="not json")
            return make_response()

        sdk = self.sdk(handler, mode="research")
        stem = self.root / "rounds/00"
        res = sdk(stem, self.body, "run_format_zero_risk")

        self.assertFalse(res["duplicate_billing_risk"])
        retry_info = json.loads((stem / "attempts/01/retry.json").read_text(encoding="utf-8"))
        self.assertFalse(retry_info["duplicate_billing_risk"])

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f7_04_format_error_reprompt_contains_structured_feedback_in_input(self, sleep):
        """Attempt 2 request contains format_correction feedback with schema instructions."""
        calls = []
        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                return make_response(text="bad formatting")
            return make_response()

        sdk = self.sdk(handler, mode="research")
        stem = self.root / "rounds_f7_04/00"
        body = dict(self.body, input=json.dumps({"question": "test format prompt"}))
        sdk(stem, body, "run_feedback_check")

        req2_path = stem / "attempts/02/request.json"
        self.assertTrue(req2_path.exists())
        req2 = json.loads(req2_path.read_text(encoding="utf-8"))
        input_data = req2["input"]
        self.assertTrue(
            "format_correction" in input_data
            or "[FORMAT_CORRECTION_FEEDBACK]" in input_data
            or "model_output_format_error" in input_data
        )

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f7_05_consecutive_format_errors_reach_limit_and_fail_closed(self, sleep):
        """5 consecutive format errors reach attempt limit and halt fail-closed."""
        sdk = self.sdk(lambda r: make_response(text="continuous bad format"), mode="research", max_attempts=5)
        stem = self.root / "rounds_f7_05/00"
        res = sdk(stem, self.body, "run_format_exhaustion", attempt_limit=5)

        self.assertEqual(res["calls"], 5)
        self.assertFalse(res["decision_complete"])
        self.assertFalse((stem / "attempts/06").exists())


class TestF8NonFatalToolErrorInvalidRU(E2RecoveryBase):
    """F8: Non-fatal tool error recovery for invalid/unauthorized RU IDs in research mode."""

    def test_f8_01_unauthorized_ru_id_returns_structured_error_dict(self):
        """Reading unauthorized RU ID in research mode returns structured error dict."""
        unauthorized_id = self.other  # valid in corpus, but not in state['available_ids']
        fake = FakeDecisions([
            decision([
                {"action": "read", "unit_id": unauthorized_id, "max_chars": 20000},
                {"action": "stop", "reason": "unauthorized recovery test"},
            ]),
        ])
        state = self.run_e2(fake, name="e2_unauth_ru", budget=E2ResearchBudget())
        self.assertEqual(state["status"], "stopped")
        events = state["events"]
        # Seed read + 1 unauthorized read
        self.assertEqual(events[1]["result"].get("error"), "unauthorized_or_invalid_unit_id")
        self.assertEqual(events[1]["result"].get("requested_unit_id"), unauthorized_id)

    def test_f8_02_unknown_ru_id_in_corpus_returns_structured_error_dict(self):
        """Reading an unknown RU ID in research mode returns non-fatal error dict."""
        fake = FakeDecisions([
            decision([
                {"action": "read", "unit_id": "non_existent_ru_hash_xyz", "max_chars": 20000},
                {"action": "stop", "reason": "unknown ru test"},
            ]),
        ])
        state = self.run_e2(fake, name="e2_unknown_ru", budget=E2ResearchBudget())
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["events"][1]["result"].get("error"), "unauthorized_or_invalid_unit_id")

    def test_f8_03_invalid_search_hit_reference_returns_structured_error_dict(self):
        """Invalid search hit reference in research mode returns non-fatal error dict."""
        fake = FakeDecisions([
            decision([
                {"action": "read", "hit": {"round": 999, "index": 0}, "max_chars": 20000},
                {"action": "stop", "reason": "invalid hit test"},
            ]),
        ])
        state = self.run_e2(fake, name="e2_invalid_hit", budget=E2ResearchBudget())
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["events"][1]["result"].get("error"), "unauthorized_or_invalid_unit_id")

    def test_f8_04_standard_mode_unauthorized_ru_id_raises_fatal_exploration_error(self):
        """Standard mode retains strict fail-closed termination on unauthorized RU ID."""
        fake = FakeDecisions([
            decision([{"action": "read", "unit_id": self.other, "max_chars": 20000}]),
        ])
        state = self.run_e2(fake, name="e2_std_unauth", budget=E2Budget())
        self.assertEqual(state["status"], "partial")

    def test_f8_05_unauthorized_ru_id_resource_accounting_and_no_state_contamination(self):
        """Failed RU read charges read count and tool chars, but 0 canonical chars and no pages."""
        fake = FakeDecisions([
            decision([
                {"action": "read", "unit_id": "hallucinated_ru_id", "max_chars": 20000},
                {"action": "stop", "reason": "check accounting"},
            ]),
        ])
        state = self.run_e2(fake, name="e2_unauth_accounting", budget=E2ResearchBudget())
        read_events = [e for e in state["events"] if e["action"]["action"] == "read"]
        seed_chars = body_chars(read_events[0]["result"])

        self.assertEqual(state["used"]["reads"], 2)
        self.assertEqual(state["used"]["canonical_chars"], seed_chars)
        self.assertEqual(len(state["pages"]), 1)
        self.assertEqual(len(state["read_sources"]), 1)
        self.assertNotIn("hallucinated_ru_id", state["available_ids"])

    def test_f8_06_underlying_ru_io_or_integrity_failure_fails_closed_without_disguise(self):
        """Underlying I/O, hash mismatch, or snapshot integrity error must raise ExplorationError fail-closed."""
        # Mock _load_selected_ru to simulate an underlying artifact hash mismatch
        fake = FakeDecisions([
            decision([{"action": "read", "unit_id": self.uid, "max_chars": 20000}]),
        ])
        with patch.object(self.tools, "_load_selected_ru", side_effect=ExplorationError("Retrieval Unit artifact SHA-256 mismatch")):
            state = self.run_e2(fake, name="e2_underlying_corrupt", budget=E2ResearchBudget())
            self.assertEqual(state["status"], "partial")
            self.assertEqual(state.get("error", {}).get("type"), "ExplorationError")
            self.assertIn("Retrieval Unit artifact SHA-256 mismatch", state.get("error", {}).get("message", ""))

    def test_f8_07_tools_read_underlying_integrity_error_fails_closed(self):
        """tools.read fails-closed with ExplorationError when encountering underlying integrity error."""
        with patch.object(self.tools, "_load_selected_ru", side_effect=ExplorationError("input manifest changed during experiment")):
            with self.assertRaises(ExplorationError) as ctx:
                self.tools.read(self.uid, max_chars=20000, fail_on_unknown_ru=False)
            self.assertEqual(str(ctx.exception), "input manifest changed during experiment")

    def test_f8_08_load_selected_ru_internal_key_error_fails_closed_without_disguise(self):
        """Internal KeyError in _load_selected_ru fails closed and is NOT disguised as unknown RU."""
        fake = FakeDecisions([
            decision([{"action": "read", "unit_id": self.uid, "max_chars": 20000}]),
        ])
        with patch.object(self.tools, "_load_selected_ru", side_effect=KeyError("corrupted_internal_field")):
            state = self.run_e2(fake, name="e2_keyerror_corrupt", budget=E2ResearchBudget())
            self.assertEqual(state["status"], "partial")
            self.assertEqual(state.get("error", {}).get("type"), "KeyError")
            self.assertIn("corrupted_internal_field", state.get("error", {}).get("message", ""))

    def test_f8_09_tools_read_internal_type_error_fails_closed_without_retry(self):
        """Internal TypeError in tools.read fails closed without being caught as interface mismatch or retried."""
        read_calls = []
        def broken_read(*args, **kwargs):
            read_calls.append(kwargs)
            raise TypeError("internal bug: unsupported operand type for +")

        fake = FakeDecisions([
            decision([{"action": "read", "unit_id": self.uid, "max_chars": 20000}]),
        ])
        with patch.object(self.tools, "read", side_effect=broken_read):
            state = self.run_e2(fake, name="e2_typeerror_corrupt", budget=E2ResearchBudget())
            self.assertEqual(state["status"], "partial")
            self.assertEqual(state.get("error", {}).get("type"), "TypeError")
            self.assertEqual(len(read_calls), 1)  # Strictly called once, no second dispatch

    def test_f8_10_tools_read_absent_ru_identities_returns_error_cleanly(self):
        """tools.read on non-existent RU returns structured error when fail_on_unknown_ru=False."""
        result = self.tools.read("definitely_non_existent_ru_id", max_chars=20000, fail_on_unknown_ru=False)
        self.assertEqual(result.get("error"), "unauthorized_or_invalid_unit_id")
        self.assertEqual(result.get("message"), "unknown Retrieval Unit")
        self.assertEqual(result.get("requested_unit_id"), "definitely_non_existent_ru_id")


class TestF9AuditLogCheckpointIntegrity(E2RecoveryBase):
    """F9: Audit log, attempt logs, retry.json and checkpoint cryptographic integrity."""

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f9_01_verify_response_attempts_validates_multi_attempt_hash_chain(self, sleep):
        """verify_response_attempts validates complete hash chain across multiple attempts."""
        calls = []
        def handler(req):
            calls.append(req)
            if len(calls) < 3:
                return httpx.Response(500, json={"error": {"code": "server_error"}})
            return make_response()

        sdk = self.sdk(handler, mode="research")
        stem = self.root / "f9_01/rounds/00"
        sdk(stem, self.body, "run_integrity_check")

        # Must pass without raising ExplorationError
        verify_response_attempts(stem)

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f9_02_tampered_retry_json_fails_integrity_verification(self, sleep):
        """Tampering with same_model in retry.json fails closed."""
        calls = []
        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                return httpx.Response(500, json={"error": {"code": "server_error"}})
            return make_response()

        sdk = self.sdk(handler, mode="research")
        stem = self.root / "f9_02/rounds/00"
        sdk(stem, self.body, "run_tamper_test")

        retry_p = stem / "attempts/01/retry.json"
        d = json.loads(retry_p.read_text(encoding="utf-8"))
        d["same_model"] = "tampered_model"
        retry_p.write_text(json.dumps(d), encoding="utf-8")

        with self.assertRaises(ExplorationError):
            verify_response_attempts(stem)

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f9_03_missing_retry_json_on_intermediate_attempt_fails(self, sleep):
        """Deleting retry.json on an intermediate attempt fails integrity check."""
        calls = []
        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                return httpx.Response(500, json={"error": {"code": "server_error"}})
            return make_response()

        sdk = self.sdk(handler, mode="research")
        stem = self.root / "f9_03/rounds/00"
        sdk(stem, self.body, "run_missing_retry")

        (stem / "attempts/01/retry.json").unlink()
        with self.assertRaises(ExplorationError):
            verify_response_attempts(stem)

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f9_04_unexpected_retry_json_on_final_attempt_fails(self, sleep):
        """Placing a retry.json on the final successful attempt fails integrity check."""
        calls = []
        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                return httpx.Response(500, json={"error": {"code": "server_error"}})
            return make_response()

        sdk = self.sdk(handler, mode="research")
        stem = self.root / "f9_04/rounds/00"
        sdk(stem, self.body, "run_unexpected_final_retry")

        once(stem / "attempts/02/retry.json", {"same_model": "gpt-6-luna", "same_route": "tokenmetro", "next_attempt": 3})
        with self.assertRaises(ExplorationError):
            verify_response_attempts(stem)

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f9_05_aggregate_duplicate_billing_risk_mismatch_fails(self, sleep):
        """Discrepancy between attempt duplicate_billing_risk and aggregate terminal fails."""
        calls = []
        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                raise httpx.ReadTimeout("timeout", request=req)
            return make_response()

        sdk = self.sdk(handler, mode="research")
        stem = self.root / "f9_05/rounds/00"
        sdk(stem, self.body, "run_aggregate_risk_mismatch")

        term_p = stem / "terminal.json"
        term = json.loads(term_p.read_text(encoding="utf-8"))
        term["duplicate_billing_risk"] = False  # Tamper with aggregate
        term["terminal_identity"] = sha256_json({k: v for k, v in term.items() if k != "terminal_identity"})
        term_p.write_text(json.dumps(term), encoding="utf-8")

        with self.assertRaises(ExplorationError):
            verify_response_attempts(stem)

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f9_06_retry_identity_tampering_in_retry_json_fails_integrity(self, sleep):
        """Tampering with delay_seconds or retry_identity in retry.json fails verify_response_attempts."""
        calls = []
        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                return httpx.Response(500, json={"error": {"code": "server_error"}})
            return make_response()

        sdk = self.sdk(handler, mode="research")
        stem = self.root / "f9_06/rounds/00"
        sdk(stem, self.body, "run_retry_id_tamper")

        retry_p = stem / "attempts/01/retry.json"
        retry_obj = json.loads(retry_p.read_text(encoding="utf-8"))
        self.assertIn("retry_identity", retry_obj)
        # Tamper with delay without updating identity
        retry_obj["delay_seconds"] = 999
        retry_p.write_text(json.dumps(retry_obj), encoding="utf-8")
        with self.assertRaises(ExplorationError):
            verify_response_attempts(stem)

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f9_07_aggregate_retry_identities_mismatch_fails_integrity(self, sleep):
        """Mismatched retry_identities in aggregate terminal fails verify_response_attempts."""
        calls = []
        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                return httpx.Response(500, json={"error": {"code": "server_error"}})
            return make_response()

        sdk = self.sdk(handler, mode="research")
        stem = self.root / "f9_07/rounds/00"
        sdk(stem, self.body, "run_agg_retry_id_mismatch")

        term_p = stem / "terminal.json"
        term = json.loads(term_p.read_text(encoding="utf-8"))
        self.assertIn("retry_identities", term)
        term["retry_identities"] = ["tampered_hash_value_here"]
        term["terminal_identity"] = sha256_json({k: v for k, v in term.items() if k != "terminal_identity"})
        term_p.write_text(json.dumps(term), encoding="utf-8")
        with self.assertRaises(ExplorationError):
            verify_response_attempts(stem)

    def test_f9_08_e2sdk_config_records_mode_and_format_retry_and_prevents_tampering(self):
        """E2SDK.config contains mode and format_retry_enabled; changing them breaks config identity."""
        sdk = E2SDK(
            route="tokenmetro",
            model="gpt-6-luna",
            mode="research",
            format_retry_enabled=True,
            environment=self.env,
            transport=httpx.MockTransport(lambda req: make_response()),
        )
        self.assertEqual(sdk.config.get("mode"), "research")
        self.assertTrue(sdk.config.get("format_retry_enabled"))

        # Mutating config breaks immutable check on call
        sdk.config["format_retry_enabled"] = False
        stem = self.root / "f9_08/rounds/00"
        with self.assertRaisesRegex(ExplorationError, "immutable provider configuration changed"):
            sdk(stem, self.body, "run_tampered_config")

    def test_f9_09_run_exploration_rejects_provider_mode_budget_profile_mismatch(self):
        """run_exploration halts fail-closed if provider mode does not match budget profile."""
        sdk_standard = E2SDK(
            route="tokenmetro",
            model="gpt-6-luna",
            mode="standard",
            environment=self.env,
            transport=httpx.MockTransport(lambda req: make_response()),
        )
        with self.assertRaisesRegex(ExplorationError, "provider mode 'standard' does not match budget profile 'research'"):
            run_exploration(
                self.tools,
                start_ru=self.uid,
                output_root=self.root / "f9_09_mismatch",
                provider=sdk_standard,
                question=self.question,
                budget=E2ResearchBudget(),
            )

        sdk_research = E2SDK(
            route="tokenmetro",
            model="gpt-6-luna",
            mode="research",
            environment=self.env,
            transport=httpx.MockTransport(lambda req: make_response()),
        )
        with self.assertRaisesRegex(ExplorationError, "provider mode 'research' does not match budget profile 'standard'"):
            run_exploration(
                self.tools,
                start_ru=self.uid,
                output_root=self.root / "f9_09_mismatch_std",
                provider=sdk_research,
                question=self.question,
                budget=E2Budget(),
            )

    def test_f9_10_generic_429_and_504_gateway_timeout_classified_as_execution_unknown(self):
        """Generic 429 (without TPM pre-gen proof) and HTTP 504 are EXECUTION UNKNOWN with billing risk."""
        from genshin_corpus.retrieval.semantic_sdk_runner import research_retry_classifier
        # Generic 429 without TPM error body
        probe_429 = {"http_status": 429, "stream_chunk_count": 0}
        retry_429, state_429 = research_retry_classifier(probe_429, b'{"error": "rate_limited"}')
        self.assertTrue(retry_429)
        self.assertEqual(state_429, "EXECUTION UNKNOWN")

        # HTTP 504 Gateway Timeout
        probe_504 = {"http_status": 504, "stream_chunk_count": 0}
        retry_504, state_504 = research_retry_classifier(probe_504, None)
        self.assertTrue(retry_504)
        self.assertEqual(state_504, "EXECUTION UNKNOWN")

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f9_11_token_budget_clamping_caps_attempt_limit_near_ceiling(self, sleep):
        """When total token budget approaches ceiling, attempt_limit is clamped to prevent reservation overflow."""
        observed_attempt_limits = []
        calls = []

        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                return httpx.Response(500, json={"error": {"code": "server_error"}})
            return make_response()

        class ObservingE2SDK(E2SDK):
            def __call__(self, stem, body, run_identity, *, decision_mode=None, attempt_limit=None):
                observed_attempt_limits.append(attempt_limit)
                return super().__call__(stem, body, run_identity, decision_mode=decision_mode, attempt_limit=attempt_limit)

        sdk = ObservingE2SDK(
            route="tokenmetro",
            model="gpt-6-luna",
            mode="research",
            environment=self.env,
            transport=httpx.MockTransport(handler),
        )

        # Output tokens 8192 means reserve is ~10,800 tokens
        # Setting total_tokens=15000 ensures (total_tokens - used) // reserve == 1
        tight_budget = E2ResearchBudget.research(total_tokens=15000, rounds=5)
        state = run_exploration(
            self.tools,
            start_ru=self.uid,
            output_root=self.root / "f9_11_clamping",
            provider=sdk,
            question=self.question,
            budget=tight_budget,
        )
        self.assertEqual(state["status"], "partial")
        self.assertEqual(len(calls), 1)  # Retry prevented because attempt_limit clamped to 1
        self.assertTrue(len(observed_attempt_limits) >= 1)
        self.assertEqual(observed_attempt_limits[0], 1)

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f9_12_consecutive_unknown_attempts_track_unknown_calls_and_billing_risk_without_corrupting_known_tokens(self, sleep):
        """Consecutive UNKNOWN attempts without reliable usage accumulate risk without corrupting known token totals."""
        calls = []
        def handler(req):
            calls.append(req)
            # Attempt 1: 504 Gateway Timeout (EXECUTION UNKNOWN, no usage)
            if len(calls) == 1:
                return httpx.Response(504, json={"error": {"code": "gateway_timeout"}})
            # Attempt 2: 500 Internal Server Error (EXECUTION UNKNOWN, no usage)
            elif len(calls) == 2:
                return httpx.Response(500, json={"error": {"code": "internal_error"}})
            # Attempt 3: Success with usage 120 tokens
            return make_response(usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})

        sdk = self.sdk(handler, mode="research")
        state = self.run_e2(sdk, name="f9_12_consecutive_unknown", budget=E2ResearchBudget())
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["provider_calls"], 3)
        self.assertEqual(state["model_rounds"], 3)
        self.assertEqual(state["used"]["tokens"], 120)  # Known tokens accurately charged
        audit = state["provider_audit"]
        self.assertEqual(audit["known_total_tokens"], 120)
        self.assertEqual(audit["usage_unknown_calls"], 2)  # 2 failed attempts had unknown usage
        self.assertTrue(audit["duplicate_billing_risk"])
        self.assertEqual(audit["duplicate_billing_risk_calls"], 2)
        self.assertEqual(audit["actual_cost"], "UNKNOWN")
        self.assertEqual(audit["currency"], "UNKNOWN")

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_f9_13_all_unknown_attempts_exhausted_halts_session_with_unknown_cost(self, sleep):
        """When 5 consecutive attempts all fail with EXECUTION UNKNOWN, session halts fail-closed with unknown cost."""
        def handler(req):
            return httpx.Response(500, json={"error": {"code": "server_down"}})

        sdk = self.sdk(handler, mode="research")
        state = self.run_e2(sdk, name="f9_13_exhausted_unknown", budget=E2ResearchBudget())
        self.assertEqual(state["status"], "partial")
        self.assertIn("provider partial/failure", state["error"]["message"])

        stem = self.root / "f9_13_exhausted_unknown/rounds/00"
        self.assertTrue((stem / "terminal.json").exists())
        term = json.loads((stem / "terminal.json").read_text(encoding="utf-8"))
        self.assertEqual(term["calls"], 5)
        self.assertEqual(term["retry_calls"], 4)
        self.assertTrue(term["duplicate_billing_risk"])
        self.assertEqual(len(term["attempt_identities"]), 5)
        self.assertEqual(len(term["retry_identities"]), 4)
        verify_response_attempts(stem)


# ==============================================================================
# TIER 2 - BOUNDARY & CORNER CASES (>=5 tests)
# ==============================================================================


class TestTier2BoundaryCornerCases(E2RecoveryBase):
    """Tier 2: Boundary value analysis, resource stress, and ceilings."""

    def test_tier2_01_single_read_max_chars_at_2m_ceiling(self):
        """max_chars=2,000,000 succeeds; max_chars=2,000,001 fails closed with read limit."""
        res_2m = self.tools.read(self.uid, max_chars=2_000_000)
        self.assertEqual(res_2m["unit_id"], self.uid)

        with self.assertRaises(ExplorationError):
            self.tools.read(self.uid, max_chars=2_000_001)

        fake_over = FakeDecisions([
            decision([
                {"action": "read", "unit_id": self.uid, "max_chars": 2_000_001},
                {"action": "stop", "reason": "over limit"},
            ]),
        ])
        state = self.run_e2(fake_over, name="e2_over_2m", budget=E2ResearchBudget())
        self.assertEqual(state["status"], "partial")
        self.assertEqual(state["error"]["message"], "read limit")

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_tier2_02_exactly_at_attempt_limit_passing_on_attempt_5(self, sleep):
        """Passes on exactly attempt 5 (boundary of max 5 attempts in research mode)."""
        calls = []
        def handler(req):
            calls.append(req)
            if len(calls) < 5:
                return httpx.Response(503, json={"error": {"code": "service_unavailable"}})
            return make_response()

        sdk = self.sdk(handler, mode="research", max_attempts=5)
        stem = self.root / "rounds/00"
        res = sdk(stem, self.body, "run_attempt_5_boundary", attempt_limit=5)

        self.assertEqual(res["calls"], 5)
        self.assertEqual(res["retry_calls"], 4)
        self.assertTrue(res["complete"])
        self.assertTrue(res["decision_complete"])
        verify_response_attempts(stem)

    def test_tier2_03_approaching_canonical_body_character_ceiling(self):
        """Approaching canonical body chars ceiling halts safely with budget_exhausted."""
        # Standard seed read consumes some chars; set ceiling to seed_chars + 1
        fake_seed = FakeDecisions([decision([{"action": "stop", "reason": "probe"}])])
        state_seed = self.run_e2(fake_seed, name="e2_seed_probe", budget=E2ResearchBudget())
        seed_chars = state_seed["used"]["canonical_chars"]

        tight_budget = E2ResearchBudget.research(canonical_chars=seed_chars + 1)
        fake_exceed = FakeDecisions([
            decision([{"action": "read", "unit_id": self.uid, "max_chars": 20000}]),
        ])
        state_exceed = self.run_e2(fake_exceed, name="e2_body_exhausted", budget=tight_budget)
        self.assertEqual(state_exceed["status"], "budget_exhausted")
        self.assertEqual(state_exceed["termination"], "Canonical body limit")

    def test_tier2_04_approaching_token_reservation_ceiling(self):
        """Pre-dispatch token reservation check halts cleanly before issuing call."""
        tight_token_budget = E2ResearchBudget.research(total_tokens=100)
        fake = FakeDecisions([])
        state = self.run_e2(fake, name="e2_token_ceiling", budget=tight_token_budget)
        self.assertEqual(state["status"], "budget_exhausted")
        self.assertEqual(fake.calls, 0)

    def test_tier2_05_multiple_consecutive_tool_errors_in_single_round(self):
        """A single batch decision containing multiple consecutive tool errors executes cleanly."""
        fake = FakeDecisions([
            decision([
                {"action": "read", "unit_id": self.uid, "max_chars": 10},
                {"action": "read", "unit_id": "unauth_1", "max_chars": 20000},
                {"action": "read", "unit_id": self.uid, "max_chars": 15},
                {"action": "read", "unit_id": "unauth_2", "max_chars": 20000},
                {"action": "search", "query": "library", "top_k": 2},
                {"action": "stop", "reason": "multiple errors handled"},
            ]),
        ])
        state = self.run_e2(fake, name="e2_multi_errors_batch", budget=E2ResearchBudget())
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["used"]["reads"], 5)  # seed + 4 error reads
        self.assertEqual(state["used"]["searches"], 1)
        self.assertGreaterEqual(len(state["available_ids"]), 2)


# ==============================================================================
# TIER 3 - CROSS-FEATURE COMBINATIONS (Pairwise)
# ==============================================================================


class TestTier3CrossFeatureCombinations(E2RecoveryBase):
    """Tier 3: Pairwise interactions across network, format, tool errors, and budget adaptation."""

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_tier3_01_intermittent_network_drop_combined_with_read_budget_adaptation(self, sleep):
        """Network drop in round 1 followed by read adaptation in round 2."""
        calls = []
        err_probe = self.tools.read(self.uid, max_chars=10, fail_on_metadata_budget=False)
        req_chars = err_probe["min_required_max_chars"]

        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                raise httpx.ReadTimeout("network glitch on round 1 attempt 1", request=req)
            elif len(calls) == 2:
                # Round 1 returns decision with small read
                return make_response(text=json.dumps(decision([{"action": "read", "unit_id": self.uid, "max_chars": 10}])))
            else:
                # Round 2 adapts read capacity
                return make_response(text=json.dumps(decision([
                    {"action": "read", "unit_id": self.uid, "max_chars": req_chars + 500},
                    {"action": "clue", "text": "cross-feature verified clue", "source_unit_ids": [self.uid]},
                    {"action": "stop", "reason": "combined workflow complete"},
                ])))

        sdk = self.sdk(handler, mode="research")
        state = self.run_e2(sdk, name="e2_network_plus_adapt", budget=E2ResearchBudget())
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["accepted_decisions"], 2)
        self.assertEqual(state["model_rounds"], 3)
        self.assertTrue(state["duplicate_billing_risk"])
        self.assertEqual(len(state["pending_clues"]), 1)

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_tier3_02_format_error_retry_followed_by_ssl_drop_followed_by_success(self, sleep):
        """Attempt 1: Format error; Attempt 2: SSL drop; Attempt 3: Success."""
        calls = []
        valid_dec = decision([{"action": "stop", "reason": "format then ssl then success"}])

        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                return make_response(text="non-json raw text")
            elif len(calls) == 2:
                raise httpx.ConnectError("SSL drop on attempt 2", request=req)
            return make_response(text=json.dumps(valid_dec))

        sdk = self.sdk(handler, mode="research")
        stem = self.root / "rounds_t3_02/00"
        res = sdk(stem, self.body, "run_format_ssl_success")

        self.assertEqual(res["calls"], 3)
        self.assertEqual(res["retry_calls"], 2)
        self.assertTrue(res["decision_complete"])
        self.assertTrue(res["duplicate_billing_risk"])  # Set by attempt 2 SSL drop
        verify_response_attempts(stem)

    def test_tier3_03_tool_error_followed_by_valid_search_and_successful_read(self):
        """Model encounters unauthorized RU error, searches to discover real RU, and reads successfully."""
        fake = FakeDecisions([
            # Round 1: Model tries unauthorized read
            decision([{"action": "read", "unit_id": self.other, "max_chars": 20000}]),
            # Round 2: Model recovers by searching
            decision([{"action": "search", "query": "Library 1", "top_k": 2}]),
            # Round 3: Model reads discovered RU and adds clue
            decision([
                {"action": "read", "unit_id": self.other, "max_chars": 20000},
                {"action": "clue", "text": "library 1 verified clue", "source_unit_ids": [self.other]},
                {"action": "stop", "reason": "recovered via search"},
            ]),
        ])
        state = self.run_e2(fake, name="e2_tool_err_search_read", budget=E2ResearchBudget())
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["model_rounds"], 3)
        self.assertEqual(len(state["pending_clues"]), 1)
        self.assertEqual(state["pending_clues"][0]["source_unit_ids"], [self.other])

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_tier3_04_pre_generation_429_followed_by_format_error_followed_by_success(self, sleep):
        """Attempt 1: 429 pregen; Attempt 2: Format error; Attempt 3: Valid -> duplicate_billing_risk is False."""
        calls = []
        valid_dec = decision([{"action": "stop", "reason": "429 then format then success"}])

        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                return httpx.Response(429, json={"error": {"code": "RateLimitExceeded.EndpointTPMExceeded"}})
            elif len(calls) == 2:
                return make_response(text="invalid format json")
            return make_response(text=json.dumps(valid_dec))

        sdk = self.sdk(handler, mode="research")
        stem = self.root / "rounds_t3_04/00"
        res = sdk(stem, self.body, "run_pregen_format_success")

        self.assertEqual(res["calls"], 3)
        self.assertEqual(res["retry_calls"], 2)
        self.assertTrue(res["decision_complete"])
        self.assertFalse(res["duplicate_billing_risk"])  # Neither was EXECUTION UNKNOWN
        verify_response_attempts(stem)

    def test_tier3_05_large_metadata_envelope_under_deepseek_route_with_budget_profile(self):
        """DeepSeek route executing under research budget handles large metadata recovery."""
        record = json.loads(FIXTURE.read_text(encoding="utf-8"))
        for s in record["sections"]:
            for u in s["units"]:
                u["provenance"] = {"trace": "t" * 500, "note": "n" * 500}

        rec_path = self.root / "canonical/rec_large.json"
        rec_path.write_bytes(canonical_json_bytes(record))
        tools = self.tools

        err = tools.read(self.uid, max_chars=100, fail_on_metadata_budget=False)
        self.assertEqual(err["error"], "read_budget_too_small")
        min_req = err["min_required_max_chars"]

        fake = FakeDecisions(
            [
                decision([{"action": "read", "unit_id": self.uid, "max_chars": 100}]),
                decision([
                    {"action": "read", "unit_id": self.uid, "max_chars": min_req + 200},
                    {"action": "clue", "text": "deepseek route large envelope clue", "source_unit_ids": [self.uid]},
                    {"action": "stop", "reason": "deepseek large envelope complete"},
                ]),
            ],
            config={"route": {"model_id": "deepseek-v4.1-flash", "route": "jizhi"}, "adapter": "fake"},
        )
        state = self.run_e2(fake, name="e2_deepseek_large_env", budget=E2ResearchBudget())
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["model_rounds"], 2)
        self.assertEqual(len(state["pending_clues"]), 1)


# ==============================================================================
# TIER 4 - REALISTIC APPLICATION SCENARIOS (>=5 multi-round scenarios)
# ==============================================================================


class TestTier4RealisticScenarios(E2RecoveryBase):
    """Tier 4: End-to-end multi-round realistic autonomous storyline exploration scenarios."""

    def test_tier4_01_scenario_storyline_exploration_with_read_budget_adaptation(self):
        """Scenario 1: Full storyline exploration recovering from initial small read capacity."""
        # Seed read automatically executed -> Round 1: search -> Round 2: small read -> Round 3: adapt & clue -> stop
        err_probe = self.tools.read(self.uid, max_chars=50, fail_on_metadata_budget=False)
        min_req = err_probe["min_required_max_chars"]

        fake = FakeDecisions([
            decision([{"action": "search", "query": "library legend", "top_k": 2}]),
            decision([{"action": "read", "unit_id": self.uid, "max_chars": 50}]),
            decision([
                {"action": "read", "unit_id": self.uid, "max_chars": min_req + 1000},
                {"action": "clue", "text": "legend is not established history", "source_unit_ids": [self.uid]},
            ]),
            decision([{"action": "stop", "reason": "storyline exploration objective achieved"}]),
        ])

        state = self.run_e2(fake, name="e2_scenario_storyline_adapt", budget=E2ResearchBudget())
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["model_rounds"], 4)
        self.assertEqual(len(state["pending_clues"]), 1)
        self.assertEqual(state["pending_clues"][0]["status"], HYPOTHESIS)
        self.assertEqual(state["checkpoint_identity"], sha256_json({k: v for k, v in state.items() if k != "checkpoint_identity"}))

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_tier4_02_scenario_intermittent_network_drops_and_429_recovery(self, sleep):
        """Scenario 2: Multi-round session recovering from both SSL disconnect and HTTP 429."""
        call_counter = [0]
        def handler(req):
            call_counter[0] += 1
            cnt = call_counter[0]
            # Round 1 attempt 1 fails with SSL disconnect
            if cnt == 1:
                raise httpx.ConnectError("transient SSL disconnect", request=req)
            # Round 1 attempt 2 succeeds with search
            elif cnt == 2:
                return make_response(text=json.dumps(decision([{"action": "search", "query": "history", "top_k": 2}])))
            # Round 2 attempt 1 fails with HTTP 429
            elif cnt == 3:
                return httpx.Response(429, json={"error": {"code": "rate_limit_exceeded"}})
            # Round 2 attempt 2 succeeds with clue and stop
            elif cnt == 4:
                return make_response(text=json.dumps(decision([
                    {"action": "clue", "text": "history recovered under glitches", "source_unit_ids": [self.uid]},
                    {"action": "stop", "reason": "completed under intermittent faults"},
                ])))
            return make_response()

        sdk = self.sdk(handler, mode="research")
        state = self.run_e2(sdk, name="e2_scenario_network_faults", budget=E2ResearchBudget())
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["accepted_decisions"], 2)
        self.assertEqual(state["model_rounds"], 4)
        self.assertTrue(state["duplicate_billing_risk"])

    def test_tier4_03_scenario_bad_ru_hallucination_and_recovery_via_search(self):
        """Scenario 3: Model hallucinates a bad RU ID, receives error, recovers via search, and completes."""
        fake = FakeDecisions([
            # Round 1: Model attempts to read non-existent RU
            decision([{"action": "read", "unit_id": "mihoyo_obc:zh-cn:hallucinated_item_999", "max_chars": 20000}]),
            # Round 2: Model recognizes error, searches for library
            decision([{"action": "search", "query": "Library 0", "top_k": 2}]),
            # Round 3: Model reads real discovered RU, adds clue, and stops
            decision([
                {"action": "read", "unit_id": self.uid, "max_chars": 20000},
                {"action": "clue", "text": "discovered library 0 after hallucination error", "source_unit_ids": [self.uid]},
                {"action": "stop", "reason": "hallucination recovered successfully"},
            ]),
        ])

        state = self.run_e2(fake, name="e2_scenario_hallucination_recovery", budget=E2ResearchBudget())
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["model_rounds"], 3)
        self.assertEqual(len(state["pending_clues"]), 1)
        self.assertEqual(state["events"][1]["result"]["error"], "unauthorized_or_invalid_unit_id")
        self.assertNotIn("error", state["events"][3]["result"])

    @patch("genshin_corpus.rag.navi_e2_experiment.time.sleep")
    def test_tier4_04_scenario_format_glitch_in_round1_small_read_in_round2_success(self, sleep):
        """Scenario 4: Format glitch in round 1, small read in round 2, adapting in round 3 to success."""
        call_counter = [0]
        err_probe = self.tools.read(self.uid, max_chars=20, fail_on_metadata_budget=False)
        req_chars = err_probe["min_required_max_chars"]

        def handler(req):
            call_counter[0] += 1
            cnt = call_counter[0]
            # Round 1 attempt 1: format glitch (unclosed JSON)
            if cnt == 1:
                return make_response(text="{'actions': [unclosed json")
            # Round 1 attempt 2: corrected format with search
            elif cnt == 2:
                return make_response(text=json.dumps(decision([{"action": "search", "query": "library", "top_k": 2}])))
            # Round 2: small read
            elif cnt == 3:
                return make_response(text=json.dumps(decision([{"action": "read", "unit_id": self.uid, "max_chars": 20}])))
            # Round 3: adapted read + clue + stop
            else:
                return make_response(text=json.dumps(decision([
                    {"action": "read", "unit_id": self.uid, "max_chars": req_chars + 500},
                    {"action": "clue", "text": "format and small read resolved", "source_unit_ids": [self.uid]},
                    {"action": "stop", "reason": "scenario 4 complete"},
                ])))

        sdk = self.sdk(handler, mode="research")
        state = self.run_e2(sdk, name="e2_scenario_format_and_read", budget=E2ResearchBudget())
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["accepted_decisions"], 3)
        self.assertEqual(state["model_rounds"], 4)
        self.assertEqual(len(state["pending_clues"]), 1)

    def test_tier4_05_scenario_full_exploration_approaching_research_budget_ceilings(self):
        """Scenario 5: Multi-round exploration cleanly approaching configured research ceilings."""
        # Configured budget: rounds=3
        budget = E2ResearchBudget.research(rounds=3, searches=5, reads=5)
        fake = FakeDecisions([
            decision([{"action": "search", "query": "library", "top_k": 2}]),
            decision([{"action": "read", "unit_id": self.uid, "max_chars": 20000}]),
            decision([{"action": "clue", "text": "round 3 clue", "source_unit_ids": [self.uid]}]),  # Does not stop
        ])

        state = self.run_e2(fake, name="e2_scenario_budget_approach", budget=budget)
        self.assertEqual(state["status"], "round_budget_exhausted")
        self.assertEqual(state["accepted_decisions"], 3)
        self.assertEqual(state["model_rounds"], 3)
        self.assertEqual(fake.calls, 3)

        # Checkpoints and accounting remain clean and verified
        self.assertEqual(state["used"]["searches"], 1)
        self.assertEqual(state["used"]["reads"], 2)  # seed + 1
        self.assertTrue((self.root / "e2_scenario_budget_approach/session.json").exists())
        self.assertTrue((self.root / "e2_scenario_budget_approach/checkpoints").exists())


if __name__ == "__main__":
    unittest.main()
