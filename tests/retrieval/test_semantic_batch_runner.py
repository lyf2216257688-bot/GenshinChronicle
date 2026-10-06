from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from genshin_corpus.retrieval.semantic_batch_runner import (
    ACCEPTED,
    LOCAL_REJECT,
    PENDING,
    TERMINAL_FAILURE,
    UNKNOWN,
    AttemptOutcome,
    aggregate_status,
    classify_execution,
    create_rerun,
    create_resume,
    offline_audit,
    prepare_batch,
    run_batch,
)
from genshin_corpus.retrieval.semantic_batch_runner import _RunLock
from genshin_corpus.retrieval.semantic_compiler_u1 import semantic_input_identity
from genshin_corpus.canonical.fingerprints import sha256_json
from genshin_corpus.retrieval.semantic_sdk_runner import make_sdk_batch_executor
from genshin_corpus.retrieval.semantic_tokenmetro_profile import route_profile
from genshin_corpus.retrieval.semantic_compiler_u1 import SEMANTIC_OUTPUT_SCHEMA_IDENTITY, SEMANTIC_OUTPUT_SCHEMA_VERSION
from genshin_corpus.retrieval.__main__ import _semantic_batch_main
import genshin_corpus.retrieval.semantic_batch_runner as semantic_batch_runner_module


def unit(name: str, *, omitted: bool = False) -> dict:
    payload = {"schema_version": "fixture", "segments": [] if omitted else [{"segment_id": name, "text": "source"}]}
    return {
        "compilation_unit_id": name,
        "semantic_input_identity": semantic_input_identity(payload),
        "payload": payload,
        "segment_ids": [] if omitted else [name],
        **({"provider_omitted": True, "omission_reason": "pure_media_map_desc"} if omitted else {}),
    }


class SemanticBatchRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "batch"

    def make_root(self) -> None:
        route_identity = route_profile("tokenmetro", {"TOKENMETRO_BASE_URL": "https://tokenmetro.fixture/v1"}).config_identity
        prepare_batch(self.root, units=[unit("u1"), unit("u2"), unit("media", omitted=True)], route="tokenmetro",
                      contract={"model": "deepseek-v4.1-flash", "prompt_identity": sha256_json({"version": "fixture"}),
                                "prompt_sha256": sha256_json({"version": "fixture"}), "source_identity": "source",
                                "schema_identity": SEMANTIC_OUTPUT_SCHEMA_IDENTITY, "route": "tokenmetro",
                                "route_config_identity": route_identity, "api_surface": "chat_completions"})

    def test_generic_http_errors_are_unknown(self) -> None:
        for status in (400, 429, 500, 503):
            self.assertEqual(classify_execution(issued=True, http_status=status, stream_complete=True),
                             (UNKNOWN, "unknown", "generic_provider_response"))
        self.assertEqual(classify_execution(issued=True, stream_complete=False),
                         (UNKNOWN, "unknown", "missing_or_incomplete_terminal"))

    def test_only_verified_pre_generation_reject_is_terminal(self) -> None:
        self.assertEqual(classify_execution(issued=True, provider_semantics="pre_generation_reject",
                                            http_status=403, stream_complete=True),
                         (TERMINAL_FAILURE, "not_started", "pre_generation_reject"))

    def test_continuation_is_immutable_and_resume_only_pending(self) -> None:
        self.make_root()

        def execute(u, _stem, _manifest):
            if u["compilation_unit_id"] == "u1":
                return AttemptOutcome(ACCEPTED, "complete")
            return AttemptOutcome(UNKNOWN, "unknown", failure_class="timeout")

        run_batch(self.root, workers=1, executor=execute, max_units=1)
        before = (self.root / "manifest.json").read_bytes()
        child = create_resume(self.root)
        child_manifest = json.loads((child / "manifest.json").read_bytes())
        self.assertEqual(child_manifest["parent_root"], str(self.root))
        self.assertEqual(child_manifest["selected_unit_ids"], ["u2"])
        self.assertEqual((self.root / "manifest.json").read_bytes(), before)
        self.assertEqual(aggregate_status(child)["lineage_head"], str(child))
        self.assertEqual(aggregate_status(self.root)["lineage_head"], str(child))
        self.assertTrue(aggregate_status(self.root)["units"]["u2"]["selected_in_lineage"])
        with self.assertRaisesRegex(ValueError, "current continuation has not been executed"):
            create_resume(self.root)

    def test_unknown_requires_explicit_rerun_child(self) -> None:
        self.make_root()

        run_batch(self.root, workers=1, executor=lambda *_: AttemptOutcome(UNKNOWN, "unknown", failure_class="503"), max_units=1)
        child = create_rerun(self.root, unit_ids=["u1"], states=[UNKNOWN])
        self.assertEqual(json.loads((child / "manifest.json").read_bytes())["mode"], "rerun")
        self.assertEqual(json.loads((child / "manifest.json").read_bytes())["selected_unit_ids"], ["u1"])
        continuation = create_resume(self.root)
        self.assertEqual(json.loads((continuation / "manifest.json").read_bytes())["selected_unit_ids"], ["u2"])

    def test_runtime_budget_pending_can_resume_from_current_head(self) -> None:
        self.make_root()
        first = run_batch(self.root, workers=1, executor=lambda *_: AttemptOutcome(ACCEPTED, "complete"), request_budget=1)
        self.assertEqual(first["states"].get(PENDING), 1)
        child = create_resume(self.root)
        self.assertEqual(json.loads((child / "manifest.json").read_bytes())["selected_unit_ids"], ["u2"])

    def test_multi_generation_resume_keeps_selected_but_never_issued_units(self) -> None:
        self.make_root()
        run_batch(self.root, workers=1, executor=lambda *_: AttemptOutcome(ACCEPTED, "complete"), request_budget=0)
        first = create_resume(self.root)
        def execute(u, stem, _manifest):
            (stem / "issued.json").write_text(json.dumps({
                "unit_id": u["compilation_unit_id"], "attempt_number": 1,
            }), encoding="utf-8")
            return AttemptOutcome(ACCEPTED, "complete")
        run_batch(first, workers=1, executor=execute, request_budget=1)

        second = create_resume(self.root)
        manifest = json.loads((second / "manifest.json").read_bytes())
        self.assertEqual(manifest["parent_root"], str(first))
        self.assertEqual(manifest["selected_unit_ids"], ["u2"])
        self.assertTrue(aggregate_status(second)["units"]["u1"]["attempted"])

    def test_executed_root_cannot_be_reused_for_new_attempts(self) -> None:
        self.make_root()
        run_batch(self.root, workers=1, executor=lambda *_: AttemptOutcome(ACCEPTED, "complete"), max_units=1)
        with self.assertRaisesRegex(ValueError, "already been executed"):
            run_batch(self.root, workers=1, executor=lambda *_: AttemptOutcome(ACCEPTED, "complete"), max_units=1)

    def test_parent_cannot_run_after_continuation_child_is_prepared(self) -> None:
        self.make_root()
        child = create_resume(self.root)
        self.assertTrue(child.exists())
        with self.assertRaisesRegex(ValueError, "current lineage head"):
            run_batch(self.root, workers=1, executor=lambda *_: AttemptOutcome(ACCEPTED, "complete"))

    def test_unknown_stops_dispatch_of_next_unit_by_default(self) -> None:
        self.make_root()
        seen = []
        def execute(u, *_):
            seen.append(u["compilation_unit_id"])
            return AttemptOutcome(UNKNOWN, "unknown", failure_class="timeout")
        result = run_batch(self.root, workers=1, executor=execute)
        self.assertEqual(seen, ["u1"])
        self.assertEqual(result["states"].get(PENDING), 1)

    def test_cli_stops_dispatch_on_unknown_without_extra_flag(self) -> None:
        self.make_root()
        outcomes = Path(self.tmp.name) / "outcomes.json"
        outcomes.write_text(json.dumps({
            "u1": {"disposition": UNKNOWN, "execution_certainty": "unknown", "failure_class": "timeout"},
            "u2": {"disposition": ACCEPTED, "execution_certainty": "complete"},
        }), encoding="utf-8")

        self.assertEqual(_semantic_batch_main([
            "run", "--root", str(self.root), "--outcome-map", str(outcomes),
        ]), 0)
        status = aggregate_status(self.root)
        self.assertEqual(status["units"]["u1"]["state"], UNKNOWN)
        self.assertEqual(status["units"]["u2"]["state"], PENDING)

    def test_rerun_requires_explicit_selection_and_is_separate_from_resume_lineage(self) -> None:
        self.make_root()
        run_batch(self.root, workers=1, executor=lambda *_: AttemptOutcome(UNKNOWN, "unknown", failure_class="503"), max_units=1)
        with self.assertRaisesRegex(ValueError, "requires explicit"):
            create_rerun(self.root)
        rerun = create_rerun(self.root, unit_ids=["u1"], route="jizhi")
        self.assertEqual(json.loads((rerun / "manifest.json").read_bytes())["lineage_roots"], [str(rerun)])
        self.assertEqual(aggregate_status(rerun)["logical_units"], 1)
        continuation = create_resume(self.root)
        self.assertEqual(json.loads((continuation / "manifest.json").read_bytes())["route"], "tokenmetro")

    def test_rerun_route_override_is_frozen_and_checked_before_provider_io(self) -> None:
        self.make_root()
        run_batch(self.root, workers=1, executor=lambda *_: AttemptOutcome(UNKNOWN, "unknown"), max_units=1)
        rerun = create_rerun(self.root, unit_ids=["u1"], route="jizhi")
        manifest = json.loads((rerun / "manifest.json").read_bytes())
        expected = route_profile("jizhi").config_identity
        self.assertEqual(manifest["contract"]["route_config_identity"], expected)
        self.assertEqual(manifest["contract"]["api_surface"], "chat_completions")

        executor = make_sdk_batch_executor(
            route="jizhi", model="deepseek-v4.1-flash", prompt={"version": "fixture"},
            prompt_identity=sha256_json({"version": "fixture"}), source_identity="source",
            environment={"JIZHI_BASE_URL": "https://changed.fixture/v1", "JIZHI_API_KEY": "secret"},
            transport=httpx.MockTransport(lambda request: self.fail("provider called")))
        result = run_batch(rerun, workers=1, executor=executor)
        self.assertEqual(result["states"].get(LOCAL_REJECT), 1)

    def test_execution_freshness_is_rechecked_while_holding_active_lock(self) -> None:
        self.make_root()
        original = semantic_batch_runner_module._assert_fresh_execution_root
        first_checked = threading.Event()
        release_first = threading.Event()
        calls = []
        errors = []

        def guarded(root):
            original(root)
            if threading.current_thread().name == "first-run":
                first_checked.set()
                self.assertTrue(release_first.wait(5))

        def execute(*_):
            calls.append(threading.current_thread().name)
            return AttemptOutcome(ACCEPTED, "complete")

        def invoke():
            try:
                run_batch(self.root, workers=1, executor=execute, max_units=1)
            except Exception as exc:
                errors.append(exc)

        with patch.object(semantic_batch_runner_module, "_assert_fresh_execution_root", side_effect=guarded):
            first = threading.Thread(target=invoke, name="first-run")
            first.start()
            self.assertTrue(first_checked.wait(5))
            second = threading.Thread(target=invoke, name="second-run")
            second.start()
            second.join(5)
            release_first.set()
            first.join(5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], (RuntimeError, ValueError))
        self.assertRegex(str(errors[0]), "already active|already been executed")

    def test_resume_and_rerun_reject_active_lineage(self) -> None:
        self.make_root()
        with _RunLock(self.root):
            with self.assertRaisesRegex(RuntimeError, "active"):
                create_resume(self.root)
            with self.assertRaisesRegex(RuntimeError, "active"):
                create_rerun(self.root, unit_ids=["u1"])

    def test_sdk_executor_rejects_prompt_identity_mismatch_before_provider_io(self) -> None:
        self.root = Path(self.tmp.name) / "sdk-contract"
        self.make_root()
        executor = make_sdk_batch_executor(
            route="tokenmetro", model="deepseek-v4.1-flash", prompt={"version": "changed"},
            prompt_identity=sha256_json({"version": "changed"}), source_identity="source",
            environment={"TOKENMETRO_BASE_URL": "https://tokenmetro.fixture/v1", "TOKENMETRO_API_KEY": "tokenmetro-secret"},
            transport=httpx.MockTransport(lambda request: self.fail("provider called")))
        result = run_batch(self.root, workers=1, executor=executor, max_units=1)
        self.assertEqual(result["states"].get(LOCAL_REJECT), 1)

    def test_cli_prepare_and_status_are_provider_free(self) -> None:
        units_path = Path(self.tmp.name) / "units.json"
        units_path.write_text(json.dumps([unit("u1")]), encoding="utf-8")
        self.assertEqual(_semantic_batch_main([
            "prepare", "--root", str(self.root), "--units", str(units_path), "--route", "tokenmetro",
        ]), 0)
        self.assertEqual(_semantic_batch_main(["status", "--root", str(self.root)]), 0)

    def test_local_reject_requires_complete_evidence(self) -> None:
        self.make_root()
        run_batch(self.root, workers=1, executor=lambda *_: AttemptOutcome(LOCAL_REJECT, "complete", failure_class="schema"), max_units=1)
        status = aggregate_status(self.root)
        self.assertEqual(status["states"][LOCAL_REJECT], 1)

    def test_omitted_is_not_pending(self) -> None:
        self.make_root()
        status = aggregate_status(self.root)
        self.assertEqual(status["states"].get("provider_omitted"), 1)
        self.assertEqual(status["states"].get(PENDING), 2)

    def test_concurrency_writes_disjoint_attempts_and_audit_is_offline(self) -> None:
        prepare_batch(self.root, units=[unit(f"u{i}") for i in range(20)], route="tokenmetro")

        def execute(u, _stem, _manifest):
            time.sleep(0.001)
            return AttemptOutcome(ACCEPTED, "complete")

        result = run_batch(self.root, workers=6, executor=execute)
        self.assertEqual(result["states"].get(ACCEPTED), 20)
        self.assertEqual(offline_audit(self.root)["status"], "INCOMPLETE")
        self.assertTrue((self.root / "run.lock").exists())
        with _RunLock(self.root):
            pass

    def test_explicit_sdk_executor_preserves_stream_evidence(self) -> None:
        self.root = Path(self.tmp.name) / "sdk-batch"
        self.make_root()
        output = {"schema_version": SEMANTIC_OUTPUT_SCHEMA_VERSION, "items": [],
                  "segment_coverage": [{"segment_id": "u1", "disposition": "covered", "reason": None}]}
        rows = [
            {"id": "fixture", "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
            {"id": "fixture", "choices": [{"index": 0, "delta": {"content": json.dumps(output)}, "finish_reason": None}]},
            {"id": "fixture", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        stream = b"".join(b"data: " + json.dumps(row).encode() + b"\n\n" for row in rows) + b"data: [DONE]\n\n"
        executor = make_sdk_batch_executor(
            route="tokenmetro", model="deepseek-v4.1-flash", prompt={"version": "fixture"},
            prompt_identity=sha256_json({"version": "fixture"}), source_identity="source", environment={
                "TOKENMETRO_BASE_URL": "https://tokenmetro.fixture/v1",
                "TOKENMETRO_API_KEY": "tokenmetro-secret",
            }, transport=httpx.MockTransport(lambda request: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=stream, request=request)))
        result = run_batch(self.root, workers=1, executor=executor, max_units=1)
        self.assertEqual(result["states"].get(ACCEPTED), 1)
        attempt = next((self.root / "units").rglob("attempt-001"))
        terminal = json.loads((attempt / "terminal.json").read_bytes())
        self.assertEqual(terminal["disposition"], ACCEPTED)
        self.assertTrue((attempt / "stream.jsonl.gz").exists())
        self.assertEqual(offline_audit(self.root)["status"], "PASS")

    def test_explicit_sdk_executor_keeps_generic_503_unknown(self) -> None:
        self.root = Path(self.tmp.name) / "sdk-503"
        self.make_root()
        executor = make_sdk_batch_executor(
            route="tokenmetro", model="deepseek-v4.1-flash", prompt={"version": "fixture"},
            prompt_identity=sha256_json({"version": "fixture"}), source_identity="source", environment={
                "TOKENMETRO_BASE_URL": "https://tokenmetro.fixture/v1",
                "TOKENMETRO_API_KEY": "tokenmetro-secret",
            }, transport=httpx.MockTransport(lambda request: httpx.Response(
                503, text="temporarily unavailable", request=request)))
        result = run_batch(self.root, workers=1, executor=executor, max_units=1)
        self.assertEqual(result["states"].get(UNKNOWN), 1)
        self.assertTrue((self.root / "errors.jsonl").exists())
        self.assertIn("execution_unknown", (self.root / "error.log").read_text(encoding="utf-8"))

    def test_sdk_preflight_failure_is_local_reject_before_issued(self) -> None:
        self.root = Path(self.tmp.name) / "sdk-local"
        self.root.parent.mkdir(parents=True, exist_ok=True)
        # Build the root with a valid manifest, then tamper only the selected
        # unit payload identity to exercise the pre-I/O boundary.
        prepare_batch(self.root, units=[unit("u1")], route="tokenmetro",
                      contract={"model": "deepseek-v4.1-flash", "prompt_identity": sha256_json({"version": "fixture"}),
                                "prompt_sha256": sha256_json({"version": "fixture"}), "source_identity": "source",
                                "schema_identity": SEMANTIC_OUTPUT_SCHEMA_IDENTITY, "route": "tokenmetro"})
        manifest_path = self.root / "manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["units"][0]["semantic_input_identity"] = "tampered"
        manifest["identity"] = __import__("genshin_corpus.canonical.fingerprints", fromlist=["sha256_json"]).sha256_json(
            {key: value for key, value in manifest.items() if key != "identity"})
        manifest_path.write_bytes(json.dumps(manifest).encode())
        executor = make_sdk_batch_executor(
            route="tokenmetro", model="deepseek-v4.1-flash", prompt={"version": "fixture"},
            prompt_identity=sha256_json({"version": "fixture"}), environment={
                "TOKENMETRO_BASE_URL": "https://tokenmetro.fixture/v1",
                "TOKENMETRO_API_KEY": "tokenmetro-secret",
            })
        result = run_batch(self.root, workers=1, executor=executor, max_units=1)
        self.assertEqual(result["states"].get(LOCAL_REJECT), 1)
        attempt = next((self.root / "units").rglob("attempt-001"))
        self.assertFalse((attempt / "issued.json").exists())

    def test_executor_exception_before_issued_is_local_reject(self) -> None:
        self.make_root()
        result = run_batch(self.root, workers=1, executor=lambda *_: (_ for _ in ()).throw(ValueError("bad request")), max_units=1)
        self.assertEqual(result["states"].get(LOCAL_REJECT), 1)


if __name__ == "__main__":
    unittest.main()
