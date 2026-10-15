from __future__ import annotations

import json
import io
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
import genshin_corpus.retrieval.__main__ as retrieval_main
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
        route_identity = route_profile("tokenmetro", {"TOKENMETRO_BASE_URL": "https://tokenmetro.fixture/v1",
                                                       "TOKENMETRO_API_SURFACE": "chat_completions"}).config_identity
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

    def test_unrelated_sibling_manifest_is_ignored_but_damaged_child_fails_closed(self) -> None:
        self.make_root()
        unrelated = self.root.parent / "historical-other-format"
        unrelated.mkdir()
        (unrelated / "manifest.json").write_text(json.dumps({"mode": None, "run_id": "legacy"}), encoding="utf-8")

        self.assertEqual(aggregate_status(self.root)["logical_units"], 3)
        self.assertEqual(__import__("genshin_corpus.retrieval.semantic_batch_runner", fromlist=["dry_run"]).dry_run(self.root)["status"], "READY")

        child = create_resume(self.root)
        child_manifest = json.loads((child / "manifest.json").read_bytes())
        child_manifest.pop("identity")
        (child / "manifest.json").write_text(json.dumps(child_manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "identity"):
            aggregate_status(self.root)

    def test_interrupt_drains_inflight_and_leaves_remaining_pending(self) -> None:
        self.make_root()
        started = threading.Event()
        release = threading.Event()
        seen = []

        def execute(unit, *_):
            seen.append(unit["compilation_unit_id"])
            started.set()
            self.assertTrue(release.wait(5))
            return AttemptOutcome(ACCEPTED, "complete")

        def interrupt_wait(futures, *args, **kwargs):
            self.assertTrue(started.wait(5))
            release.set()
            raise KeyboardInterrupt

        with patch.object(semantic_batch_runner_module, "wait", side_effect=interrupt_wait):
            result = run_batch(self.root, workers=1, executor=execute)

        self.assertEqual(seen, ["u1"])
        self.assertEqual(result["status"], "interrupted")
        self.assertTrue(result["interruption_requested"])
        self.assertTrue(result["resume_available"])
        self.assertEqual(result["next_action"], "resume")
        self.assertEqual(result["states"].get(ACCEPTED), 1)
        self.assertEqual(result["states"].get(PENDING), 1)
        self.assertEqual(aggregate_status(self.root)["states"].get(ACCEPTED), 1)
        self.assertEqual(offline_audit(self.root)["status"], "INCOMPLETE")
        self.assertEqual((self.root / "status.json").exists(), True)

    def test_interrupt_workers_gt_one_does_not_submit_after_cancel(self) -> None:
        self.root = Path(self.tmp.name) / "batch-workers"
        prepare_batch(self.root, units=[unit(f"u{i}") for i in range(8)], route="tokenmetro")
        started = []
        release = threading.Event()

        def execute(u, *_):
            started.append(u["compilation_unit_id"])
            self.assertTrue(release.wait(5))
            return AttemptOutcome(ACCEPTED, "complete")

        def interrupt_wait(*_args, **_kwargs):
            deadline = time.monotonic() + 5
            while len(started) < 6 and time.monotonic() < deadline:
                time.sleep(0.001)
            release.set()
            raise KeyboardInterrupt

        with patch.object(semantic_batch_runner_module, "wait", side_effect=interrupt_wait):
            result = run_batch(self.root, workers=6, executor=execute)

        self.assertEqual(len(started), 6)
        self.assertEqual(result["status"], "interrupted")
        self.assertTrue(result["resume_available"])
        self.assertEqual(result["states"].get(ACCEPTED), 6)
        self.assertEqual(result["states"].get(PENDING), 2)

    def test_late_drained_unknown_is_harvested_once_into_indexes(self) -> None:
        self.make_root()
        started = threading.Event()
        release = threading.Event()
        drain_started = threading.Event()
        original_pool = semantic_batch_runner_module.ThreadPoolExecutor

        class DrainPool(original_pool):
            def shutdown(self, wait=True, *, cancel_futures=False):
                drain_started.set()
                release.set()
                return super().shutdown(wait=wait, cancel_futures=cancel_futures)

        def execute(unit, stem, _manifest):
            (stem / "issued.json").write_text(json.dumps({
                "unit_id": unit["compilation_unit_id"], "attempt_number": 1,
            }), encoding="utf-8")
            started.set()
            self.assertTrue(release.wait(5))
            return AttemptOutcome(UNKNOWN, "unknown", failure_class="timeout", error_message="late")

        def interrupt_wait(*_args, **_kwargs):
            self.assertTrue(started.wait(5))
            self.assertFalse(drain_started.is_set())
            raise KeyboardInterrupt

        with patch.object(semantic_batch_runner_module, "ThreadPoolExecutor", DrainPool), \
                patch.object(semantic_batch_runner_module, "wait", side_effect=interrupt_wait):
            result = run_batch(self.root, workers=1, executor=execute)

        self.assertTrue(drain_started.is_set())
        self.assertEqual(result["states"].get(UNKNOWN), 1)
        self.assertEqual(result["states"].get(PENDING), 1)
        progress = [json.loads(line) for line in (self.root / "progress.jsonl").read_text(encoding="utf-8").splitlines()]
        finished = [row for row in progress if row.get("event") == "unit_finished"]
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["state"], UNKNOWN)
        errors = (self.root / "errors.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(errors), 1)
        self.assertIn("execution_unknown", (self.root / "error.log").read_text(encoding="utf-8"))
        self.assertEqual(aggregate_status(self.root)["states"].get(UNKNOWN), 1)

    def test_cli_interrupt_returns_130_without_traceback(self) -> None:
        self.make_root()
        started = threading.Event()
        release = threading.Event()

        def execute(*_):
            started.set()
            self.assertTrue(release.wait(5))
            return AttemptOutcome(ACCEPTED, "complete")

        original_loader = retrieval_main._load_batch_executor
        retrieval_main._load_batch_executor = lambda *_args, **_kwargs: execute
        output = io.StringIO()
        error = io.StringIO()

        def interrupt_wait(*_args, **_kwargs):
            self.assertTrue(started.wait(5))
            release.set()
            raise KeyboardInterrupt

        try:
            with patch.object(semantic_batch_runner_module, "wait", side_effect=interrupt_wait):
                with redirect_stdout(output), redirect_stderr(error):
                    code = _semantic_batch_main(["run", "--root", str(self.root), "--executor", "fixture:execute"])
        finally:
            retrieval_main._load_batch_executor = original_loader

        self.assertEqual(code, 130)
        rendered = output.getvalue()
        self.assertIn('"interruption_requested":true', rendered)
        self.assertIn('"status":"interrupted"', rendered)
        self.assertIn('"lineage_head"', rendered)
        self.assertIn('"next_action":"resume"', rendered)
        self.assertNotIn("Traceback", rendered + error.getvalue())

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
                "TOKENMETRO_API_SURFACE": "chat_completions",
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
                "TOKENMETRO_API_SURFACE": "chat_completions",
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

    def sdk_executor(self, handler):
        return make_sdk_batch_executor(
            route="tokenmetro", model="deepseek-v4.1-flash", prompt={"version": "fixture"},
            prompt_identity=sha256_json({"version": "fixture"}), source_identity="source",
            environment={"TOKENMETRO_BASE_URL": "https://tokenmetro.fixture/v1",
                         "TOKENMETRO_API_KEY": "tokenmetro-secret",
                         "TOKENMETRO_API_SURFACE": "chat_completions"},
            transport=httpx.MockTransport(handler))

    def sdk_success(self, request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        output = {"schema_version": SEMANTIC_OUTPUT_SCHEMA_VERSION, "items": [],
                  "segment_coverage": [{"segment_id": s["segment_id"], "disposition": "covered", "reason": None}
                                       for s in payload["segments"]]}
        rows = [{"id": "fixture", "choices": [{"index": 0, "delta": {"content": json.dumps(output)}, "finish_reason": None}]},
                {"id": "fixture", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
        stream = b"".join(b"data: " + json.dumps(row).encode() + b"\n\n" for row in rows) + b"data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream, request=request)

    def sdk_rate_limit(self, request, **kwargs):
        return httpx.Response(429, json={"error": {
            "message": "inference exceeds tpm/rpm limit",
            "type": "rate_limit_error",
            "param": "",
            "code": "RateLimitExceeded.EndpointTPMExceeded",
        }},
                              request=request, **kwargs)

    def test_pacing_single_worker_and_real_sdk_dispatch(self):
        self.make_root()
        starts = []
        def handler(request):
            starts.append(time.monotonic())
            return self.sdk_success(request)
        result = run_batch(self.root, workers=1, executor=self.sdk_executor(handler),
                           min_dispatch_interval_seconds=0.08)
        self.assertEqual(result["status"], "complete")
        self.assertGreaterEqual(starts[1] - starts[0], 0.075)
        self.assertEqual(result["provider_attempts_this_invocation"], 2)

    def test_zero_interval_and_pacing_allow_six_and_twelve_workers_inflight(self):
        for workers, interval in ((6, 0.0), (12, 0.0), (6, 0.08), (12, 0.08)):
            with self.subTest(workers=workers, interval=interval):
                self.root = Path(self.tmp.name) / f"workers-{workers}-{interval}"
                self.make_root()
                starts = []
                both_started = threading.Event()
                lock = threading.Lock()
                def handler(request):
                    with lock:
                        starts.append(time.monotonic())
                        if len(starts) == 2:
                            both_started.set()
                    self.assertTrue(both_started.wait(5), "executor became serial")
                    return self.sdk_success(request)
                result = run_batch(self.root, workers=workers, executor=self.sdk_executor(handler),
                                   min_dispatch_interval_seconds=interval)
                self.assertEqual(result["status"], "complete")
                if interval:
                    self.assertGreaterEqual(starts[1] - starts[0], interval - 0.005)
                self.assertEqual(offline_audit(self.root)["provider_attempts_total"], 2)

    def test_safe_429_retries_preserve_all_attempts_and_accounting(self):
        self.make_root()
        calls = []
        def handler(request):
            calls.append(time.monotonic())
            return self.sdk_rate_limit(request) if len(calls) < 3 else self.sdk_success(request)
        result = run_batch(self.root, workers=1, executor=self.sdk_executor(handler), max_units=1,
                           request_budget=3, rate_limit_max_retries=2,
                           rate_limit_backoff_initial_seconds=0.02, rate_limit_backoff_cap_seconds=0.1)
        self.assertEqual((len(calls), result["provider_attempts_this_invocation"]), (3, 3))
        attempts = sorted((self.root / "units").rglob("attempt-*"))
        self.assertEqual(len(attempts), 3)
        decisions = [json.loads((p / "retry.json").read_bytes()) for p in attempts]
        self.assertEqual([d["decision"] for d in decisions], ["retry_scheduled", "retry_scheduled", "not_eligible"])
        self.assertEqual([d["delay_seconds"] for d in decisions], [0.02, 0.04, 0.0])
        audit = offline_audit(self.root)
        self.assertEqual((audit["status"], audit["provider_attempts_total"], audit["automatic_retries_total"]), ("PASS", 3, 2))
        progress = [json.loads(line) for line in (self.root / "progress.jsonl").read_bytes().splitlines()]
        self.assertEqual(len([r for r in progress if r["event"] == "attempt_finished"]), 3)

    def test_retry_exhaustion_stops_before_next_unit(self):
        self.make_root()
        calls = []
        def handler(request):
            calls.append(request)
            return self.sdk_rate_limit(request)
        result = run_batch(self.root, workers=1, executor=self.sdk_executor(handler),
                           rate_limit_max_retries=1, rate_limit_backoff_initial_seconds=0)
        self.assertEqual((len(calls), result["status"], result["states"].get(PENDING)), (2, "blocked", 1))
        self.assertEqual(aggregate_status(self.root)["units"]["u1"]["state"], TERMINAL_FAILURE)
        self.assertEqual(offline_audit(self.root)["automatic_retries_total"], 1)
        child = create_resume(self.root)
        self.assertEqual(json.loads((child / "manifest.json").read_bytes())["selected_unit_ids"], ["u2"])

    def test_retries_share_request_budget_under_concurrency(self):
        for workers in (1, 6, 12):
            self.root = Path(self.tmp.name) / f"retry-budget-{workers}"
            self.make_root()
            calls = []
            def handler(request):
                calls.append(request)
                return self.sdk_rate_limit(request)
            result = run_batch(self.root, workers=workers, executor=self.sdk_executor(handler),
                               request_budget=2, rate_limit_max_retries=4,
                               rate_limit_backoff_initial_seconds=0)
            self.assertEqual((len(calls), result["provider_attempts_this_invocation"]), (2, 2))
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(offline_audit(self.root)["provider_attempts_total"], 2)

    def test_retry_after_cap_and_fallback_backoff(self):
        self.make_root()
        calls = []
        def handler(request):
            calls.append(time.monotonic())
            return self.sdk_rate_limit(request, headers={"retry-after": "999"}) if len(calls) == 1 else self.sdk_success(request)
        run_batch(self.root, workers=1, executor=self.sdk_executor(handler), max_units=1,
                  rate_limit_max_retries=1, rate_limit_backoff_cap_seconds=0.08)
        first = next((self.root / "units").rglob("attempt-001"))
        decision = json.loads((first / "retry.json").read_bytes())
        self.assertEqual((decision["delay_source"], decision["delay_seconds"]), ("retry_after", 0.08))
        self.assertGreaterEqual(calls[1] - calls[0], 0.08)

    def test_ambiguous_429_timeout_connection_and_partial_stream_do_not_retry(self):
        cases = [lambda r: httpx.Response(429, json={"error": {"code": "unknown_rate_limit"}}, request=r),
                 lambda r: httpx.Response(429, json={"error": {"code": "RateLimitExceeded.EndpointTPMExceeded"}, "usage": {}}, request=r),
                 lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("after send", request=r)),
                 lambda r: (_ for _ in ()).throw(httpx.ConnectError("ambiguous", request=r)),
                 lambda r: httpx.Response(200, headers={"content-type": "text/event-stream"},
                                          content=b'data: {"choices":[{"index":0,"delta":{"content":"partial"}}]}\n\n', request=r)]
        for index, response in enumerate(cases):
            self.root = Path(self.tmp.name) / f"unknown-{index}"
            self.make_root()
            calls = []
            def handler(request):
                calls.append(request)
                return response(request)
            result = run_batch(self.root, workers=1, executor=self.sdk_executor(handler), rate_limit_max_retries=4)
            self.assertEqual((len(calls), result["status"], result["states"].get(UNKNOWN)), (1, "stopped_unknown", 1))
            self.assertEqual(offline_audit(self.root)["automatic_retries_total"], 0)
            child = create_resume(self.root)
            self.assertEqual(json.loads((child / "manifest.json").read_bytes())["selected_unit_ids"], ["u2"])

    def test_wall_clock_includes_pacing_and_retry_wait(self):
        for retry in (False, True):
            self.root = Path(self.tmp.name) / f"wall-{retry}"
            self.make_root()
            calls = []
            def handler(request):
                calls.append(request)
                return self.sdk_rate_limit(request) if retry else self.sdk_success(request)
            result = run_batch(self.root, workers=1, executor=self.sdk_executor(handler),
                               wall_clock_seconds=0.2, min_dispatch_interval_seconds=10,
                               rate_limit_max_retries=2, rate_limit_backoff_initial_seconds=10)
            self.assertEqual(len(calls), 1)
            self.assertEqual(result["states"].get(PENDING), 1)

    def test_interrupt_during_pacing_and_backoff_returns_130(self):
        for retry in (False, True):
            self.root = Path(self.tmp.name) / f"interrupt-wait-{retry}"
            self.make_root()
            calls = []
            waiting = threading.Event()
            original_acquire = semantic_batch_runner_module._DispatchGate.acquire
            def acquire(gate):
                if gate.calls:
                    waiting.set()
                return original_acquire(gate)
            def handler(request):
                calls.append(request)
                return self.sdk_rate_limit(request) if retry else self.sdk_success(request)
            original_wait = semantic_batch_runner_module.wait
            def interrupt(futures, **kwargs):
                if waiting.is_set():
                    raise KeyboardInterrupt
                return original_wait(futures, **kwargs)
            output = io.StringIO()
            with patch.object(semantic_batch_runner_module._DispatchGate, "acquire", acquire), \
                    patch.object(semantic_batch_runner_module, "wait", side_effect=interrupt), \
                    patch.object(retrieval_main, "_load_batch_executor", return_value=self.sdk_executor(handler)), \
                    redirect_stdout(output):
                code = _semantic_batch_main(["run", "--root", str(self.root), "--executor", "fixture:execute",
                                            "--min-dispatch-interval-seconds", "10", "--rate-limit-max-retries", "2",
                                            "--rate-limit-backoff-initial-seconds", "10"])
            self.assertEqual((code, len(calls)), (130, 1))
            self.assertIn('"status":"interrupted"', output.getvalue())
            child = create_resume(self.root)
            self.assertEqual(json.loads((child / "manifest.json").read_bytes())["selected_unit_ids"], ["u2"])

    def test_execution_configuration_is_immutable_and_continuation_can_change_point(self):
        self.make_root()
        run_batch(self.root, workers=1, executor=self.sdk_executor(self.sdk_success), request_budget=1,
                  min_dispatch_interval_seconds=0.08, rate_limit_max_retries=2)
        config_bytes = (self.root / "execution_config.json").read_bytes()
        config = json.loads(config_bytes)
        self.assertEqual(config["contract"]["rate_limit_retry"]["max_retries"], 2)
        child = create_resume(self.root)
        run_batch(child, workers=12, executor=self.sdk_executor(self.sdk_success), min_dispatch_interval_seconds=0)
        self.assertEqual((self.root / "execution_config.json").read_bytes(), config_bytes)
        self.assertEqual(json.loads((child / "execution_config.json").read_bytes())["contract"]["workers"], 12)
        self.assertEqual(offline_audit(child)["status"], "PASS")

    def test_rate_control_invalid_values_fail_before_execution(self):
        self.make_root()
        for options in ({"min_dispatch_interval_seconds": -1}, {"min_dispatch_interval_seconds": float("nan")},
                        {"rate_limit_max_retries": 5}, {"rate_limit_backoff_multiplier": 0.5},
                        {"rate_limit_backoff_cap_seconds": float("inf")}):
            with self.assertRaises(ValueError):
                run_batch(self.root, executor=lambda *_: self.fail("executor called"), **options)
        self.assertFalse((self.root / "execution_config.json").exists())

    def test_zero_request_budget_never_enters_sdk_transport(self):
        self.make_root()
        result = run_batch(self.root, workers=12,
                           executor=self.sdk_executor(lambda _: self.fail("provider called")),
                           request_budget=0, rate_limit_max_retries=4)
        self.assertEqual((result["provider_attempts_this_invocation"], result["states"][PENDING]), (0, 2))

    def test_rate_limit_exhaustion_cancels_workers_waiting_for_dispatch(self):
        self.make_root()
        calls = []
        def handler(request):
            calls.append(request)
            return self.sdk_rate_limit(request)
        result = run_batch(self.root, workers=6, executor=self.sdk_executor(handler),
                           min_dispatch_interval_seconds=10, rate_limit_max_retries=0)
        self.assertEqual((len(calls), result["status"], result["states"][PENDING]), (1, "blocked", 1))

    def test_execution_configuration_change_is_rejected_after_freeze(self):
        self.make_root()
        with patch.object(semantic_batch_runner_module, "_append_jsonl", side_effect=OSError("before run start")):
            with self.assertRaises(OSError):
                run_batch(self.root, executor=self.sdk_executor(self.sdk_success),
                          min_dispatch_interval_seconds=1)
        with self.assertRaisesRegex(ValueError, "immutable execution configuration changed"):
            run_batch(self.root, executor=self.sdk_executor(self.sdk_success), min_dispatch_interval_seconds=0)

    def test_audit_rejects_retry_dispatch_and_accounting_tampering(self):
        mutations = ("config", "missing_config", "ordinal", "retry", "status")
        for mutation in mutations:
            self.root = Path(self.tmp.name) / f"tamper-{mutation}"
            self.make_root()
            calls = []
            def handler(request):
                calls.append(request)
                return self.sdk_rate_limit(request) if len(calls) == 1 else self.sdk_success(request)
            run_batch(self.root, executor=self.sdk_executor(handler), max_units=1,
                      rate_limit_max_retries=1, rate_limit_backoff_initial_seconds=0)
            first = next((self.root / "units").rglob("attempt-001"))
            if mutation == "missing_config":
                (self.root / "execution_config.json").unlink()
            else:
                path = {"config": self.root / "execution_config.json", "ordinal": first / "dispatch.json",
                        "retry": first / "retry.json", "status": self.root / "status.json"}[mutation]
                value = json.loads(path.read_bytes())
                if mutation == "config":
                    value["contract"]["workers"] = 12
                elif mutation == "ordinal":
                    value["provider_call_ordinal"] = 999
                elif mutation == "retry":
                    value["safe"] = False
                else:
                    value["provider_attempts_this_invocation"] = 1
                path.write_bytes(json.dumps(value).encode())
            with self.assertRaises(ValueError):
                offline_audit(self.root)

    def test_responses_batch_safe_retry_keeps_explicit_route_and_surface(self):
        environment = {"TOKENMETRO_BASE_URL": "https://tokenmetro.fixture/v1",
                       "TOKENMETRO_API_KEY": "tokenmetro-secret", "TOKENMETRO_API_SURFACE": "responses"}
        prompt = {"version": "fixture"}
        profile = route_profile("tokenmetro", environment)
        prepare_batch(self.root, units=[unit("u1")], route="tokenmetro", contract={
            "model": profile.model_id, "prompt_identity": sha256_json(prompt), "prompt_sha256": sha256_json(prompt),
            "source_identity": "source", "schema_identity": SEMANTIC_OUTPUT_SCHEMA_IDENTITY,
            "route": "tokenmetro", "api_surface": "responses", "route_config_identity": profile.config_identity})
        calls = []
        def handler(request):
            self.assertEqual(str(request.url), "https://tokenmetro.fixture/v1/responses")
            body = json.loads(request.content)
            self.assertEqual(set(body), {"model", "instructions", "input", "max_output_tokens", "stream"})
            calls.append(request)
            if len(calls) < 3:
                return self.sdk_rate_limit(request)
            output = {"schema_version": SEMANTIC_OUTPUT_SCHEMA_VERSION, "items": [],
                      "segment_coverage": [{"segment_id": "u1", "disposition": "covered", "reason": None}]}
            rows = [{"type": "response.output_text.delta", "delta": json.dumps(output)},
                    {"type": "response.completed", "response": {"id": "fixture", "status": "completed"}}]
            stream = b"".join(b"data: " + json.dumps(row).encode() + b"\n\n" for row in rows) + b"data: [DONE]\n\n"
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream, request=request)
        executor = make_sdk_batch_executor(route="tokenmetro", model=profile.model_id, prompt=prompt,
                                           prompt_identity=sha256_json(prompt), source_identity="source",
                                           environment=environment, transport=httpx.MockTransport(handler))
        result = run_batch(self.root, executor=executor, rate_limit_max_retries=2,
                           rate_limit_backoff_initial_seconds=0, request_budget=3)
        self.assertEqual((result["status"], result["provider_attempts_this_invocation"]), ("complete", 3))
        self.assertEqual(offline_audit(self.root)["automatic_retries_total"], 2)


if __name__ == "__main__":
    unittest.main()
