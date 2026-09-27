from __future__ import annotations

from contextlib import redirect_stderr
from io import StringIO
import json
import gzip
import tempfile
import unittest
from pathlib import Path

import httpx

from genshin_corpus.retrieval.__main__ import _semantic_live_run_main
from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.retrieval.semantic_compiler_u1 import SEMANTIC_OUTPUT_SCHEMA_VERSION, semantic_input_identity
from genshin_corpus.retrieval.semantic_live_runner import ChannelConfig, SemanticProviderRequest, load_adapter, run_channel
from genshin_corpus.retrieval.semantic_openai_chat_adapter import OpenAIChatCompletionsAdapter, create_adapter
from genshin_corpus.retrieval.semantic_sdk_runner import (
    _policy_403_pre_generation,
    audit_sdk_route_integrity,
    replay_sdk_route_attempt,
    run_sdk_route_canary,
    run_sdk_route_pair,
    run_sdk_units,
)
from genshin_corpus.retrieval.semantic_tokenmetro_profile import route_profile
from genshin_corpus.retrieval.semantic_v2_comparison import run_one as legacy_comparison_run_one


def unit(name: str) -> dict:
    payload = {"schema_version": "fixture", "segments": [{"segment_id": name, "text": "source"}]}
    return {"compilation_unit_id": name, "semantic_input_identity": semantic_input_identity(payload),
            "payload": payload, "segment_ids": [name]}


def success(name: str) -> httpx.Response:
    content = {"schema_version": SEMANTIC_OUTPUT_SCHEMA_VERSION, "items": [],
               "segment_coverage": [{"segment_id": name, "disposition": "covered", "reason": None}]}
    return httpx.Response(200, json={"id": "provider-id", "choices": [{"finish_reason": "stop",
                          "message": {"role": "assistant", "content": json.dumps(content)}}],
                          "usage": {"prompt_tokens": 1, "completion_tokens": 2}})


class SdkRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir="D:/GenshinChronicle")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "run"

    def run_units(self, responses, units=None, *, retries=2, delays=None):
        calls = []

        def handler(request):
            body = json.loads(request.content)
            self.assertEqual(set(body), {"model", "messages", "max_tokens"})
            calls.append(body)
            response = responses.pop(0)
            return response(request) if callable(response) else response

        result = run_sdk_units(self.root, model="glm-5.3-flash", units=units or [unit("s1")],
                               prompt={"version": "v2"}, prompt_identity="fixture-v2",
                               api_key="fixture-secret", transport=httpx.MockTransport(handler),
                               max_retries=retries, sleep=(delays.append if delays is not None else lambda _: None))
        return result, calls

    def attempt_dirs(self, name="s1"):
        import hashlib
        key = hashlib.sha256(name.encode()).hexdigest()
        return sorted((self.root / "units" / key).glob("attempt-*"))

    def test_503_retry_success_and_failed_attempt_preserved(self):
        result, calls = self.run_units([httpx.Response(503, text="temporarily unavailable"), success("s1")])
        self.assertEqual((result["status"], len(calls)), ("complete", 2))
        first, second = self.attempt_dirs()
        self.assertEqual(json.loads((first / "terminal.json").read_bytes())["retry_classification"], "transient")
        self.assertEqual((first / "raw_response.bin").read_bytes(), b"temporarily unavailable")
        self.assertEqual(json.loads((second / "terminal.json").read_bytes())["disposition"], "accepted_for_local_contract")
        self.assertEqual(self.run_units([], retries=2)[0]["provider_attempts_this_invocation"], 0)

    def test_429_retry_after_is_bounded(self):
        delays = []
        result, calls = self.run_units([httpx.Response(429, headers={"retry-after": "120"}), success("s1")], delays=delays)
        self.assertEqual((result["status"], len(calls), delays), ("complete", 2, [30.0]))

    def test_default_retry_budget_pauses_after_three_attempts(self):
        responses = [httpx.Response(503) for _ in range(3)] + [success("s1")]
        result, calls = self.run_units(responses)
        self.assertEqual((result["status"], len(calls), result["provider_attempts_total"]),
                         ("paused_transient_outage", 3, 3))
        self.assertEqual(len(responses), 1)
        self.assertEqual(len(self.attempt_dirs()), 3)

    def test_hard_retry_cap_fails_before_io(self):
        with self.assertRaisesRegex(ValueError, "invalid SDK operating point"):
            self.run_units([], retries=5)
        self.assertFalse(self.root.exists())

    def test_403_does_not_retry(self):
        result, calls = self.run_units([httpx.Response(403, text="forbidden")])
        self.assertEqual((result["status"], len(calls)), ("blocked", 1))
        self.assertEqual(len(self.attempt_dirs()), 1)

    def test_length_does_not_retry(self):
        response = httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "{"}}]})
        result, calls = self.run_units([response])
        self.assertEqual((result["status"], len(calls)), ("blocked", 1))
        self.assertEqual(json.loads((self.attempt_dirs()[0] / "terminal.json").read_bytes())["disposition"], "output_budget_blocked")

    def test_malformed_json_does_not_retry(self):
        response = httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "{"}}]})
        result, calls = self.run_units([response])
        self.assertEqual((result["status"], len(calls)), ("blocked", 1))
        self.assertEqual(json.loads((self.attempt_dirs()[0] / "terminal.json").read_bytes())["disposition"], "local_validation_failed")

    def test_connection_failure_retry(self):
        def disconnect(_request):
            raise httpx.ConnectError("offline fixture")

        result, calls = self.run_units([disconnect, success("s1")])
        self.assertEqual((result["status"], len(calls)), ("complete", 2))
        self.assertEqual(json.loads((self.attempt_dirs()[0] / "terminal.json").read_bytes())["retry_classification"], "transient")

    def test_partial_resume_only_pending_and_identity_guard(self):
        rows = [unit("s1"), unit("s2")]
        first, calls = self.run_units([success("s1"), httpx.Response(503)], rows, retries=0)
        self.assertEqual((first["status"], len(calls)), ("paused_transient_outage", 2))
        second, calls = self.run_units([success("s2")], rows, retries=2)
        self.assertEqual((second["status"], len(calls), second["provider_attempts_this_invocation"]), ("complete", 1, 1))
        self.assertEqual(len(self.attempt_dirs("s1")), 1)
        self.assertEqual(len(self.attempt_dirs("s2")), 2)
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            run_sdk_units(self.root, model="glm-5.3-flash", units=rows, prompt={"version": "changed"},
                          prompt_identity="fixture-v2", api_key="fixture-secret")

    def test_unresolved_attempt_fails_closed(self):
        self.run_units([success("s1")])
        (self.attempt_dirs()[0] / "terminal.json").unlink()
        with self.assertRaisesRegex(ValueError, "unresolved issued"):
            self.run_units([])

    def test_tampered_attempt_fails_closed(self):
        self.run_units([success("s1")])
        (self.attempt_dirs()[0] / "raw_response.bin").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "integrity mismatch"):
            self.run_units([])

    def test_jsonl_stream_tamper_and_full_integrity_audit(self):
        environment = self._route_environment()
        result = run_sdk_route_pair(
            self.root, units=[unit("s1")], prompt={"version": "v2"},
            prompt_identity="fixture-v2", source_identity="fixture-source",
            environment=environment,
            transports={"tokenmetro": httpx.MockTransport(lambda request: httpx.Response(
                200, headers={"content-type": "text/event-stream"},
                content=self._stream_body(), request=request))},
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(audit_sdk_route_integrity(self.root)["accepted_attempts"], 1)
        terminal = json.loads((self.root / "units" / __import__("hashlib").sha256(b"s1").hexdigest() / "attempt-001" / "terminal.json").read_bytes())
        descriptor = terminal["artifacts"]["stream"]
        self.assertEqual(descriptor["format"], "jsonl-gzip-1")
        self.assertTrue(descriptor["path"].endswith("stream.jsonl.gz"))
        self.assertFalse((self.root / "units" / __import__("hashlib").sha256(b"s1").hexdigest() / "attempt-001" / "stream.jsonl").exists())
        stream = self.root / descriptor["path"]
        self.assertEqual(len(gzip.decompress(stream.read_bytes()).splitlines()), descriptor["chunk_count"])
        stream.write_bytes(stream.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "integrity mismatch"):
            run_sdk_route_pair(
                self.root, units=[unit("s1")], prompt={"version": "v2"},
                prompt_identity="fixture-v2", source_identity="fixture-source",
                environment=environment,
            )

    def test_missing_checkpoint_is_rebuilt_without_provider_calls(self):
        environment = self._route_environment()
        calls = []

        def primary(request):
            calls.append(True)
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=self._stream_body(), request=request)

        kwargs = {
            "units": [unit("s1")], "prompt": {"version": "v2"},
            "prompt_identity": "fixture-v2", "source_identity": "fixture-source",
            "environment": environment,
            "transports": {"tokenmetro": httpx.MockTransport(primary)},
        }
        run_sdk_route_pair(self.root, **kwargs)
        (self.root / "checkpoint.json").unlink()
        resumed = run_sdk_route_pair(self.root, **kwargs)
        self.assertEqual(resumed["provider_attempts_this_invocation"], 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(audit_sdk_route_integrity(self.root)["status"], "PASS")

    def test_stale_checkpoint_identity_fails_closed(self):
        environment = self._route_environment()
        run_sdk_route_pair(
            self.root, units=[unit("s1")], prompt={"version": "v2"},
            prompt_identity="fixture-v2", source_identity="fixture-source",
            environment=environment,
            transports={"tokenmetro": httpx.MockTransport(lambda request: httpx.Response(
                200, headers={"content-type": "text/event-stream"},
                content=self._stream_body(), request=request))},
        )
        checkpoint = json.loads((self.root / "checkpoint.json").read_bytes())
        checkpoint["provider_attempts_total"] = 0
        (self.root / "checkpoint.json").write_bytes(json.dumps(checkpoint).encode())
        with self.assertRaisesRegex(ValueError, "checkpoint identity or history mismatch"):
            run_sdk_route_pair(
                self.root, units=[unit("s1")], prompt={"version": "v2"},
                prompt_identity="fixture-v2", source_identity="fixture-source",
                environment=environment,
            )

    def test_single_route_checkpoint_behind_recovers_without_duplicate_call(self):
        first, calls = self.run_units([success("s1")])
        self.assertEqual(first["status"], "complete")
        checkpoint = json.loads((self.root / "checkpoint.json").read_bytes())
        checkpoint.update({"status": "partial", "accepted_units": 0,
                           "provider_attempts_total": 0, "provider_attempts_this_invocation": 0,
                           "unit_states": {"s1": "pending"}, "attempt_counts": {"s1": 0}})
        checkpoint["identity"] = sha256_json({key: value for key, value in checkpoint.items() if key != "identity"})
        (self.root / "checkpoint.json").write_bytes(canonical_json_bytes(checkpoint))
        resumed, resumed_calls = self.run_units([])
        self.assertEqual((resumed["status"], resumed["provider_attempts_this_invocation"], len(resumed_calls)),
                         ("complete", 0, 0))
        repaired = json.loads((self.root / "checkpoint.json").read_bytes())
        self.assertEqual(repaired["attempt_counts"], {"s1": 1})

    def test_legacy_single_route_checkpoint_uses_global_count_for_retryable_prefix(self):
        first, _calls = self.run_units([httpx.Response(503), httpx.Response(503)], retries=1)
        self.assertEqual((first["status"], first["provider_attempts_total"]), ("paused_transient_outage", 2))
        checkpoint = json.loads((self.root / "checkpoint.json").read_bytes())
        checkpoint.pop("attempt_counts")
        checkpoint["identity"] = sha256_json({key: value for key, value in checkpoint.items() if key != "identity"})
        (self.root / "checkpoint.json").write_bytes(canonical_json_bytes(checkpoint))

        resumed, calls = self.run_units([success("s1")], retries=0)
        self.assertEqual((resumed["status"], resumed["provider_attempts_total"], len(calls)),
                         ("complete", 3, 1))

    def test_single_route_checkpoint_prefix_count_divergence_fails_closed(self):
        rows = [unit("s1"), unit("s2")]
        first, _calls = self.run_units([httpx.Response(503), httpx.Response(503)], rows, retries=1)
        self.assertEqual(first["provider_attempts_total"], 2)
        checkpoint = json.loads((self.root / "checkpoint.json").read_bytes())
        checkpoint["provider_attempts_total"] = 0
        checkpoint["identity"] = sha256_json({key: value for key, value in checkpoint.items() if key != "identity"})
        (self.root / "checkpoint.json").write_bytes(canonical_json_bytes(checkpoint))
        with self.assertRaisesRegex(ValueError, "checkpoint identity or history mismatch"):
            self.run_units([], units=rows, retries=0)

    def test_route_pair_checkpoint_behind_after_primary_success_does_not_reissue(self):
        environment = self._route_environment()
        calls = []

        def primary(request):
            calls.append("tokenmetro")
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=self._stream_body(), request=request)

        kwargs = {
            "units": [unit("s1")], "prompt": {"version": "v2"},
            "prompt_identity": "fixture-v2", "source_identity": "fixture-source",
            "environment": environment,
            "transports": {"tokenmetro": httpx.MockTransport(primary)},
        }
        run_sdk_route_pair(self.root, **kwargs)
        checkpoint = json.loads((self.root / "checkpoint.json").read_bytes())
        checkpoint.update({"status": "partial", "accepted_units": 0,
                           "provider_attempts_total": 0, "provider_attempts_this_invocation": 0,
                           "unit_states": {"s1": "pending"}, "route_history": {"s1": []}})
        checkpoint["counters"] = {
            "tokenmetro_attempts": 0, "tokenmetro_successes": 0,
            "tokenmetro_policy_403": 0, "jizhi_fallback_issued": 0,
            "jizhi_fallback_successes": 0, "jizhi_fallback_failures": 0,
            "stopped_indeterminate": 0, "accepted_primary": 0, "accepted_fallback": 0,
        }
        checkpoint["usage_by_route"] = {
            "tokenmetro": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0,
                           "unknown_usage_attempts": 0, "reported_credit": 0, "unknown_credit_attempts": 0},
            "jizhi": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0,
                      "unknown_usage_attempts": 0, "reported_credit": 0, "unknown_credit_attempts": 0},
        }
        checkpoint["identity"] = sha256_json({key: value for key, value in checkpoint.items() if key != "identity"})
        (self.root / "checkpoint.json").write_bytes(canonical_json_bytes(checkpoint))

        def unexpected(request):
            raise AssertionError("completed primary was reissued")

        resumed = run_sdk_route_pair(self.root, **{**kwargs,
            "transports": {"tokenmetro": httpx.MockTransport(unexpected)}})
        self.assertEqual(resumed["provider_attempts_this_invocation"], 0)
        self.assertEqual(calls, ["tokenmetro"])

    def test_route_pair_checkpoint_behind_after_policy_primary_continues_at_fallback(self):
        environment = self._route_environment()
        primary_only_environment = dict(environment)
        primary_only_environment.pop("JIZHI_API_KEY")
        calls = []

        def primary(request):
            calls.append("tokenmetro")
            return httpx.Response(403, json={"error": {"type": "content_policy_violation", "code": None}}, request=request)

        def fallback(request):
            calls.append("jizhi")
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=self._stream_body(), request=request)

        kwargs = {
            "units": [unit("s1")], "prompt": {"version": "v2"},
            "prompt_identity": "fixture-v2", "source_identity": "fixture-source",
            "transports": {"tokenmetro": httpx.MockTransport(primary), "jizhi": httpx.MockTransport(fallback)},
        }
        first = run_sdk_route_pair(self.root, environment=primary_only_environment, **kwargs)
        self.assertEqual(first["status"], "blocked_missing_fallback_credential")
        checkpoint = json.loads((self.root / "checkpoint.json").read_bytes())
        checkpoint.update({"status": "partial", "accepted_units": 0,
                           "provider_attempts_total": 0, "provider_attempts_this_invocation": 0,
                           "unit_states": {"s1": "pending"}, "route_history": {"s1": []}})
        checkpoint["counters"] = {
            "tokenmetro_attempts": 0, "tokenmetro_successes": 0,
            "tokenmetro_policy_403": 0, "jizhi_fallback_issued": 0,
            "jizhi_fallback_successes": 0, "jizhi_fallback_failures": 0,
            "stopped_indeterminate": 0, "accepted_primary": 0, "accepted_fallback": 0,
        }
        checkpoint["usage_by_route"] = {
            route: {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0,
                    "unknown_usage_attempts": 0, "reported_credit": 0, "unknown_credit_attempts": 0}
            for route in ("tokenmetro", "jizhi")
        }
        checkpoint["identity"] = sha256_json({key: value for key, value in checkpoint.items() if key != "identity"})
        (self.root / "checkpoint.json").write_bytes(canonical_json_bytes(checkpoint))

        def unexpected_primary(request):
            raise AssertionError("known policy failure was reissued")

        resumed = run_sdk_route_pair(self.root, environment=environment, **{
            **kwargs, "transports": {"tokenmetro": httpx.MockTransport(unexpected_primary),
                                     "jizhi": httpx.MockTransport(fallback)}})
        self.assertEqual(resumed["status"], "complete")
        self.assertEqual(resumed["provider_attempts_this_invocation"], 1)
        self.assertEqual(calls, ["tokenmetro", "jizhi"])

    def test_route_pair_unresolved_issued_attempt_fails_before_provider_io(self):
        environment = self._route_environment()
        calls = []

        def primary(request):
            calls.append("tokenmetro")
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=self._stream_body(), request=request)

        kwargs = {
            "units": [unit("s1")], "prompt": {"version": "v2"},
            "prompt_identity": "fixture-v2", "source_identity": "fixture-source",
            "environment": environment,
            "transports": {"tokenmetro": httpx.MockTransport(primary)},
        }
        run_sdk_route_pair(self.root, **kwargs)
        (self.attempt_dirs()[0] / "terminal.json").unlink()

        def unexpected(request):
            raise AssertionError("unresolved provider execution was replayed")

        with self.assertRaisesRegex(ValueError, "unresolved issued attempt"):
            run_sdk_route_pair(self.root, **{**kwargs,
                "transports": {"tokenmetro": httpx.MockTransport(unexpected),
                               "jizhi": httpx.MockTransport(unexpected)}})
        self.assertEqual(calls, ["tokenmetro"])

    def test_route_pair_checkpoint_behind_after_fallback_does_not_reissue(self):
        environment = self._route_environment()
        calls = []

        def primary(request):
            calls.append("tokenmetro")
            return httpx.Response(403, json={"error": {"type": "content_policy_violation", "code": None}}, request=request)

        def fallback(request):
            calls.append("jizhi")
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=self._stream_body(), request=request)

        kwargs = {
            "units": [unit("s1")], "prompt": {"version": "v2"},
            "prompt_identity": "fixture-v2", "source_identity": "fixture-source",
            "environment": environment,
            "transports": {"tokenmetro": httpx.MockTransport(primary), "jizhi": httpx.MockTransport(fallback)},
        }
        run_sdk_route_pair(self.root, **kwargs)
        checkpoint = json.loads((self.root / "checkpoint.json").read_bytes())
        checkpoint.update({"status": "partial", "accepted_units": 0,
                           "provider_attempts_total": 1, "provider_attempts_this_invocation": 1,
                           "unit_states": {"s1": "fallback_pending"}, "route_history": {"s1": ["tokenmetro"]}})
        checkpoint["counters"].update({"jizhi_fallback_issued": 0, "jizhi_fallback_successes": 0,
                                       "jizhi_fallback_failures": 0, "accepted_fallback": 0})
        checkpoint["usage_by_route"]["jizhi"] = {
            "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0,
            "unknown_usage_attempts": 0, "reported_credit": 0, "unknown_credit_attempts": 0,
        }
        checkpoint["identity"] = sha256_json({key: value for key, value in checkpoint.items() if key != "identity"})
        (self.root / "checkpoint.json").write_bytes(canonical_json_bytes(checkpoint))

        def unexpected(request):
            raise AssertionError("completed route attempt was reissued")

        resumed = run_sdk_route_pair(self.root, **{**kwargs,
            "transports": {"tokenmetro": httpx.MockTransport(unexpected), "jizhi": httpx.MockTransport(unexpected)}})
        self.assertEqual(resumed["provider_attempts_this_invocation"], 0)
        self.assertEqual(calls, ["tokenmetro", "jizhi"])

    def test_route_pair_checkpoint_prefix_summary_divergence_fails_closed(self):
        environment = self._route_environment()
        run_sdk_route_pair(
            self.root, units=[unit("s1")], prompt={"version": "v2"},
            prompt_identity="fixture-v2", source_identity="fixture-source",
            environment=environment,
            transports={"tokenmetro": httpx.MockTransport(lambda request: httpx.Response(
                200, headers={"content-type": "text/event-stream"},
                content=self._stream_body(), request=request))},
        )
        checkpoint = json.loads((self.root / "checkpoint.json").read_bytes())
        checkpoint["route_history"] = {"s1": []}
        checkpoint["unit_states"] = {"s1": "pending"}
        checkpoint["accepted_units"] = 0
        checkpoint["provider_attempts_total"] = 0
        checkpoint["counters"]["tokenmetro_successes"] = 0
        checkpoint["counters"]["accepted_primary"] = 0
        checkpoint["usage_by_route"]["tokenmetro"]["input_tokens"] = 0
        checkpoint["usage_by_route"]["tokenmetro"]["output_tokens"] = 0
        checkpoint["identity"] = sha256_json({key: value for key, value in checkpoint.items() if key != "identity"})
        (self.root / "checkpoint.json").write_bytes(canonical_json_bytes(checkpoint))
        with self.assertRaisesRegex(ValueError, "route checkpoint identity or history mismatch"):
            run_sdk_route_pair(
                self.root, units=[unit("s1")], prompt={"version": "v2"},
                prompt_identity="fixture-v2", source_identity="fixture-source",
                environment=environment,
            )

    def test_audit_rebinds_request_and_wire_to_manifest_identity(self):
        environment = self._route_environment()
        run_sdk_route_pair(
            self.root, units=[unit("s1")], prompt={"version": "v2"},
            prompt_identity="fixture-v2", source_identity="fixture-source",
            environment=environment,
            transports={"tokenmetro": httpx.MockTransport(lambda request: httpx.Response(
                200, headers={"content-type": "text/event-stream"},
                content=self._stream_body(), request=request))},
        )
        attempt = self.attempt_dirs()[0]
        terminal = json.loads((attempt / "terminal.json").read_bytes())
        mutated = json.loads((self.root / terminal["artifacts"]["request"]["path"]).read_bytes())
        mutated["model"] = "changed-after-freeze"
        body = canonical_json_bytes(mutated)
        for name in ("request", "wire_request"):
            descriptor = terminal["artifacts"][name]
            path = self.root / descriptor["path"]
            path.write_bytes(body)
            descriptor.update({"sha256": __import__("hashlib").sha256(body).hexdigest(), "byte_count": len(body)})
        (attempt / "terminal.json").write_bytes(canonical_json_bytes(terminal))
        with self.assertRaisesRegex(ValueError, "wire request identity changed"):
            audit_sdk_route_integrity(self.root)

    def test_audit_rejects_fallback_after_non_policy_primary(self):
        environment = self._route_environment()

        def primary(request):
            return httpx.Response(403, json={"error": {"type": "content_policy_violation", "code": None}}, request=request)

        def fallback(request):
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=self._stream_body(), request=request)

        run_sdk_route_pair(
            self.root, units=[unit("s1")], prompt={"version": "v2"},
            prompt_identity="fixture-v2", source_identity="fixture-source",
            environment=environment,
            transports={"tokenmetro": httpx.MockTransport(primary), "jizhi": httpx.MockTransport(fallback)},
        )
        attempt = self.attempt_dirs()[0]
        terminal = json.loads((attempt / "terminal.json").read_bytes())
        terminal.update({"disposition": "transport_failure", "fallback_eligible": False,
                         "fallback_decision": "not_eligible"})
        (attempt / "terminal.json").write_bytes(canonical_json_bytes(terminal))
        with self.assertRaisesRegex(ValueError, "fallback lacks a qualifying primary policy failure"):
            audit_sdk_route_integrity(self.root)

    def test_legacy_cli_requires_explicit_selector(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            _semantic_live_run_main(["--channel", "glm", "--preflight-root", "unused",
                                     "--run-root", "unused", "--unit-id", "unused"])

    def test_programmatic_legacy_live_requires_explicit_opt_in(self):
        config = ChannelConfig.from_environment("gemini_b", {})
        invoked = []

        def opener(*_args, **_kwargs):
            invoked.append(True)
            raise AssertionError("network path must not run")

        adapter = OpenAIChatCompletionsAdapter(config, "fixture-secret", opener=opener)
        request = SemanticProviderRequest(config.channel, "fixture", "fixture-input", {}, {}, {}, config, 0)
        with self.assertRaisesRegex(ValueError, "explicit opt-in"):
            adapter.invoke(request)
        with self.assertRaisesRegex(ValueError, "explicit opt-in"):
            run_channel(Path("unused"), self.root, config, adapter)
        self.assertFalse(self.root.exists())
        self.assertEqual(invoked, [])
        self.assertFalse(create_adapter(config, {config.api_key_env: "fixture-secret"}).legacy_live_enabled)
        self.assertTrue(load_adapter(config.adapter_factory, config,
                                     {config.api_key_env: "fixture-secret"},
                                     allow_legacy_live=True).legacy_live_enabled)
        with self.assertRaisesRegex(ValueError, "explicit opt-in"):
            legacy_comparison_run_one(self.root, "gemini", 16)

    def _route_environment(self):
        return {
            "TOKENMETRO_BASE_URL": "https://tokenmetro.fixture/v1",
            "JIZHI_BASE_URL": "https://jizhi.fixture/v1",
            "TOKENMETRO_API_KEY": "tokenmetro-secret",
            "JIZHI_API_KEY": "jizhi-secret",
        }

    def _stream_body(self, name="s1", *, reasoning="", finish="stop"):
        content = {
            "schema_version": SEMANTIC_OUTPUT_SCHEMA_VERSION,
            "items": [],
            "segment_coverage": [{"segment_id": name, "disposition": "covered", "reason": None}],
        }
        rows = [
            {"id": "stream-fixture", "choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": reasoning}, "finish_reason": None}]},
            {"id": "stream-fixture", "choices": [{"index": 0, "delta": {"content": json.dumps(content)}, "finish_reason": None}]},
            {"id": "stream-fixture", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
            {"id": "stream-fixture", "choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 4}},
        ]
        return b"".join(b"data: " + json.dumps(row).encode() + b"\n\n" for row in rows) + b"data: [DONE]\n\n"

    def test_route_profiles_use_runtime_urls_without_secrets(self):
        environment = self._route_environment()
        tokenmetro = route_profile("tokenmetro", environment, require_environment=True)
        jizhi = route_profile("jizhi", environment, require_environment=True)
        self.assertEqual(tokenmetro.base_url, environment["TOKENMETRO_BASE_URL"])
        self.assertEqual(jizhi.base_url, environment["JIZHI_BASE_URL"])
        self.assertEqual(tokenmetro.model_id, jizhi.model_id)
        self.assertNotIn("secret", json.dumps(tokenmetro.safe_dict()))
        with self.assertRaisesRegex(ValueError, "JIZHI_BASE_URL"):
            route_profile("jizhi", {"JIZHI_API_KEY": "present"}, require_environment=True)
        for bad_url in (
            "https://user:pass@jizhi.fixture/v1",
            "https://jizhi.fixture/v1?token=secret",
            "https://jizhi.fixture/v1#secret",
        ):
            with self.subTest(bad_url=bad_url), self.assertRaises(ValueError):
                route_profile("jizhi", {"JIZHI_BASE_URL": bad_url}, require_environment=True)

    def test_route_pair_requires_primary_runtime_key_before_io(self):
        environment = self._route_environment()
        environment.pop("TOKENMETRO_API_KEY")
        with self.assertRaisesRegex(ValueError, "TOKENMETRO_API_KEY"):
            run_sdk_route_pair(
                self.root, units=[unit("s1")], prompt={"version": "v2"},
                prompt_identity="fixture-v2", source_identity="fixture-source",
                environment=environment,
            )

    def test_observed_tokenmetro_policy_403_shape_is_allowlisted(self):
        # The frozen ordinal21 response uses error.type and a null error.code.
        raw = b'{"error":{"message":"fixture","type":"content_policy_violation","param":null,"code":null}}'
        self.assertTrue(_policy_403_pre_generation(
            raw, 403, chunk_count=0, reasoning_chars=0, visible_chars=0,
            usage="UNKNOWN", finish_reason=None,
        ))
        self.assertFalse(_policy_403_pre_generation(
            raw, 403, chunk_count=1, reasoning_chars=0, visible_chars=0,
            usage="UNKNOWN", finish_reason=None,
        ))
        self.assertFalse(_policy_403_pre_generation(
            b'{"error":{"type":"content_policy_violation"}}', 403,
            chunk_count=0, reasoning_chars=0, visible_chars=0,
            usage="UNKNOWN", finish_reason=None,
        ))

    def test_policy_403_creates_one_jizhi_fallback_with_same_logical_request(self):
        environment = self._route_environment()
        bodies = {}

        def primary(request):
            bodies["tokenmetro"] = json.loads(request.content)
            return httpx.Response(403, json={"error": {"code": "content_policy_violation", "message": "fixture"}}, request=request)

        def fallback(request):
            bodies["jizhi"] = json.loads(request.content)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=self._stream_body(), request=request)

        result = run_sdk_route_pair(
            self.root, units=[unit("s1")], prompt={"version": "v2"}, prompt_identity="fixture-v2",
            source_identity="fixture-source", environment=environment,
            transports={"tokenmetro": httpx.MockTransport(primary), "jizhi": httpx.MockTransport(fallback)},
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["route_history"]["s1"], ["tokenmetro", "jizhi"])
        self.assertEqual(result["counters"]["tokenmetro_policy_403"], 1)
        self.assertEqual(result["counters"]["accepted_fallback"], 1)
        self.assertEqual(bodies["tokenmetro"], bodies["jizhi"])
        attempts = self.attempt_dirs()
        self.assertEqual(len(attempts), 2)
        primary_terminal = json.loads((attempts[0] / "terminal.json").read_bytes())
        fallback_terminal = json.loads((attempts[1] / "terminal.json").read_bytes())
        self.assertEqual(primary_terminal["disposition"], "primary_policy_403_pre_generation")
        self.assertEqual(fallback_terminal["disposition"], "accepted_for_local_contract")
        stream_descriptor = fallback_terminal["artifacts"]["stream"]
        self.assertEqual(stream_descriptor["chunk_count"], fallback_terminal["stream_chunk_count"])
        self.assertTrue((self.root / stream_descriptor["path"]).is_file())
        self.assertEqual(list(attempts[1].glob("chunk-*.json")), [])
        replay = replay_sdk_route_attempt(self.root, "s1", 2, ["s1"])
        self.assertEqual(replay["network_calls_executed"], 0)
        self.assertEqual(replay["status"], "PASS")
        persisted = b"".join(path.read_bytes() for path in self.root.rglob("*") if path.is_file())
        self.assertNotIn(b"tokenmetro-secret", persisted)
        self.assertNotIn(b"jizhi-secret", persisted)

    def test_generic_403_does_not_fallback(self):
        environment = self._route_environment()
        called = []

        def primary(request):
            called.append("tokenmetro")
            return httpx.Response(403, json={"error": {"message": "forbidden"}}, request=request)

        def fallback(request):
            called.append("jizhi")
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=self._stream_body(), request=request)

        result = run_sdk_route_pair(
            self.root, units=[unit("s1")], prompt={"version": "v2"}, prompt_identity="fixture-v2",
            source_identity="fixture-source", environment=environment,
            transports={"tokenmetro": httpx.MockTransport(primary), "jizhi": httpx.MockTransport(fallback)},
        )
        self.assertEqual(result["status"], "stopped_unknown_execution")
        self.assertEqual(called, ["tokenmetro"])
        self.assertEqual(len(self.attempt_dirs()), 1)

    def test_stream_incomplete_stops_without_fallback(self):
        environment = self._route_environment()
        called = []

        def primary(request):
            called.append("tokenmetro")
            body = b'data: ' + json.dumps({"id": "partial", "choices": [{"index": 0, "delta": {"reasoning_content": "partial", "content": "{"}, "finish_reason": None}]}).encode() + b"\n\n"
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body, request=request)

        def fallback(request):
            called.append("jizhi")
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=self._stream_body(), request=request)

        result = run_sdk_route_pair(
            self.root, units=[unit("s1")], prompt={"version": "v2"}, prompt_identity="fixture-v2",
            source_identity="fixture-source", environment=environment,
            transports={"tokenmetro": httpx.MockTransport(primary), "jizhi": httpx.MockTransport(fallback)},
        )
        self.assertEqual(result["status"], "stopped_unknown_execution")
        self.assertEqual(called, ["tokenmetro"])
        terminal = json.loads((self.attempt_dirs()[0] / "terminal.json").read_bytes())
        self.assertEqual(terminal["execution_state"], "unknown")
        self.assertFalse(terminal["stream_complete"])

    def test_stream_error_event_preserves_safe_diagnostics_without_fallback(self):
        environment = self._route_environment()
        called = []

        def primary(request):
            called.append("tokenmetro")
            events = [
                {"choices": [{"index": 0, "delta": {"reasoning_content": "partial"}, "finish_reason": None}]},
                {"error": {"message": "Upstream response stream ended before completion",
                           "code": "upstream_stream_incomplete", "type": "provider_stream_error",
                           "param": "tokenmetro-secret"}},
            ]
            body = b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=body, request=request)

        def fallback(request):
            called.append("jizhi")
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=self._stream_body(), request=request)

        result = run_sdk_route_pair(
            self.root, units=[unit("s1")], prompt={"version": "v2"},
            prompt_identity="fixture-v2", source_identity="fixture-source",
            environment=environment,
            transports={"tokenmetro": httpx.MockTransport(primary), "jizhi": httpx.MockTransport(fallback)},
        )
        self.assertEqual(result["status"], "stopped_unknown_execution")
        self.assertEqual(called, ["tokenmetro"])
        self.assertEqual(result["provider_attempts_total"], 1)
        terminal = json.loads((self.attempt_dirs()[0] / "terminal.json").read_bytes())
        self.assertEqual(terminal["http_status"], 200)
        self.assertEqual(terminal["stream_chunk_count"], 1)
        self.assertEqual(terminal["disposition"], "transport_failure")
        self.assertFalse(terminal["fallback_eligible"])
        error = json.loads((self.attempt_dirs()[0] / "sdk_error.json").read_bytes())
        self.assertEqual(error["type"], "APIError")
        self.assertEqual(error["origin"], "sse_error_event")
        self.assertEqual(error["provider_error_code"], "upstream_stream_incomplete")
        self.assertEqual(error["provider_error_type"], "provider_stream_error")
        self.assertEqual(error["provider_error_param"], "[REDACTED]")
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"tokenmetro-secret", path.read_bytes())
                self.assertNotIn(b"jizhi-secret", path.read_bytes())

    def test_stream_chunks_are_immutable_before_next_network_segment(self):
        environment = self._route_environment()
        key = __import__("hashlib").sha256(b"s1").hexdigest()
        first_chunk = self.root / "units" / key / "attempt-001" / "stream.jsonl"
        observed = []

        class SegmentedStream(httpx.SyncByteStream):
            def __iter__(self):
                events = [part + b"\n\n" for part in self_body.split(b"\n\n") if part]
                for index, event in enumerate(events):
                    if index == 1:
                        observed.append(first_chunk.is_file())
                    yield event

        self_body = self._stream_body()

        def primary(request):
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  stream=SegmentedStream(), request=request)

        result = run_sdk_route_pair(
            self.root, units=[unit("s1")], prompt={"version": "v2"},
            prompt_identity="fixture-v2", source_identity="fixture-source",
            environment=environment,
            transports={"tokenmetro": httpx.MockTransport(primary)},
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(observed, [True])
        terminal = json.loads((self.attempt_dirs()[0] / "terminal.json").read_bytes())
        self.assertTrue(terminal["stream_complete"])

    def test_timeout_stops_without_fallback(self):
        environment = self._route_environment()
        called = []

        def primary(request):
            called.append("tokenmetro")
            raise httpx.ReadTimeout("fixture timeout", request=request)

        def fallback(request):
            called.append("jizhi")
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=self._stream_body(), request=request)

        result = run_sdk_route_pair(
            self.root, units=[unit("s1")], prompt={"version": "v2"}, prompt_identity="fixture-v2",
            source_identity="fixture-source", environment=environment,
            transports={"tokenmetro": httpx.MockTransport(primary), "jizhi": httpx.MockTransport(fallback)},
        )
        self.assertEqual(result["status"], "stopped_unknown_execution")
        self.assertEqual(called, ["tokenmetro"])

    def test_connection_reset_stops_without_fallback(self):
        environment = self._route_environment()
        called = []

        def primary(request):
            called.append("tokenmetro")
            raise httpx.ReadError("fixture connection reset", request=request)

        def fallback(request):
            called.append("jizhi")
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=self._stream_body(), request=request)

        result = run_sdk_route_pair(
            self.root, units=[unit("s1")], prompt={"version": "v2"}, prompt_identity="fixture-v2",
            source_identity="fixture-source", environment=environment,
            transports={"tokenmetro": httpx.MockTransport(primary), "jizhi": httpx.MockTransport(fallback)},
        )
        self.assertEqual(result["status"], "stopped_unknown_execution")
        self.assertEqual(called, ["tokenmetro"])

    def test_explicit_route_canaries_share_request_identity_without_fallback(self):
        environment = self._route_environment()
        bodies = []

        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=self._stream_body(), request=request)

        first = run_sdk_route_canary(
            self.root / "tokenmetro", route="tokenmetro", units=[unit("s1")],
            prompt={"version": "v2"}, prompt_identity="fixture-v2", source_identity="fixture-source",
            environment=environment, transport=httpx.MockTransport(handler),
        )
        second = run_sdk_route_canary(
            self.root / "jizhi", route="jizhi", units=[unit("s1")],
            prompt={"version": "v2"}, prompt_identity="fixture-v2", source_identity="fixture-source",
            environment=environment, transport=httpx.MockTransport(handler),
        )
        self.assertEqual(first["disposition"], "accepted_for_local_contract")
        self.assertEqual(second["disposition"], "accepted_for_local_contract")
        self.assertEqual(first["request_identity"], second["request_identity"])
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(first["usage"]["prompt_tokens"], 3)
        self.assertEqual(first["usage"]["completion_tokens"], 4)
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"tokenmetro-secret", path.read_bytes())
                self.assertNotIn(b"jizhi-secret", path.read_bytes())

    def test_route_pair_rejects_ambiguous_http_and_local_failures(self):
        environment = self._route_environment()
        cases = [
            ("rate_limit", httpx.Response(429, text="busy"), "transport_failure"),
            ("server_error", httpx.Response(503, text="busy"), "transport_failure"),
            ("ordinary_403", httpx.Response(403, json={"error": {"code": "other"}}), "transport_failure"),
            ("unauthorized", httpx.Response(401, text="unauthorized"), "transport_failure"),
            ("missing_route", httpx.Response(404, text="missing"), "transport_failure"),
            ("length", httpx.Response(200, headers={"content-type": "text/event-stream"},
                                      content=self._stream_body(finish="length")), "output_budget_blocked"),
            ("missing_done", httpx.Response(200, headers={"content-type": "text/event-stream"},
                                            content=self._stream_body().replace(b"data: [DONE]\n\n", b"")), "transport_failure"),
            ("bad_json", httpx.Response(200, headers={"content-type": "text/event-stream"},
                                        content=b'data: {"choices":[{"delta":{"content":"{"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'), "local_validation_failed"),
        ]
        for name, response, expected_disposition in cases:
            with self.subTest(name=name):
                called = []

                def primary(request):
                    called.append("tokenmetro")
                    return response

                def fallback(request):
                    called.append("jizhi")
                    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                          content=self._stream_body(), request=request)

                result = run_sdk_route_pair(
                    self.root / name, units=[unit("s1")], prompt={"version": "v2"},
                    prompt_identity="fixture-v2", source_identity="fixture-source",
                    environment=environment,
                    transports={"tokenmetro": httpx.MockTransport(primary), "jizhi": httpx.MockTransport(fallback)},
                )
                self.assertEqual(called, ["tokenmetro"])
                self.assertEqual(result["provider_attempts_total"], 1)
                key = __import__("hashlib").sha256(b"s1").hexdigest()
                terminal = json.loads((self.root / name / "units" / key / "attempt-001" / "terminal.json").read_bytes())
                self.assertEqual(terminal["disposition"], expected_disposition)

    def test_route_pair_resume_skips_success_and_rejects_changed_identity(self):
        environment = self._route_environment()
        calls = []

        def primary(request):
            calls.append("tokenmetro")
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=self._stream_body(), request=request)

        kwargs = {
            "units": [unit("s1")], "prompt": {"version": "v2"},
            "prompt_identity": "fixture-v2", "source_identity": "fixture-source",
            "environment": environment,
            "transports": {"tokenmetro": httpx.MockTransport(primary)},
        }
        first = run_sdk_route_pair(self.root, **kwargs)
        second = run_sdk_route_pair(self.root, **kwargs)
        self.assertEqual(first["status"], "complete")
        self.assertEqual(second["provider_attempts_this_invocation"], 0)
        self.assertEqual(calls, ["tokenmetro"])
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            run_sdk_route_pair(self.root, **{**kwargs, "source_identity": "changed"})

    def test_policy_primary_resumes_once_when_fallback_credential_arrives(self):
        environment = self._route_environment()
        initial = dict(environment)
        initial.pop("JIZHI_API_KEY")
        called = []

        def primary(request):
            called.append("tokenmetro")
            return httpx.Response(403, json={"error": {"type": "content_policy_violation", "code": None}}, request=request)

        def fallback(request):
            called.append("jizhi")
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=self._stream_body(), request=request)

        common = {
            "units": [unit("s1")], "prompt": {"version": "v2"},
            "prompt_identity": "fixture-v2", "source_identity": "fixture-source",
            "transports": {"tokenmetro": httpx.MockTransport(primary), "jizhi": httpx.MockTransport(fallback)},
        }
        first = run_sdk_route_pair(self.root, environment=initial, **common)
        self.assertEqual(first["status"], "blocked_missing_fallback_credential")
        self.assertEqual(first["provider_attempts_total"], 1)
        second = run_sdk_route_pair(self.root, environment=environment, **common)
        self.assertEqual(second["status"], "complete")
        self.assertEqual(called, ["tokenmetro", "jizhi"])
        self.assertEqual(second["provider_attempts_total"], 2)
        self.assertEqual(second["counters"]["accepted_fallback"], 1)
        again = run_sdk_route_pair(self.root, environment=environment, **common)
        self.assertEqual(again["provider_attempts_this_invocation"], 0)

    def test_fallback_failure_is_terminal_and_not_reissued(self):
        environment = self._route_environment()
        called = []

        def primary(request):
            called.append("tokenmetro")
            return httpx.Response(403, json={"error": {"code": "content_policy_violation"}}, request=request)

        def fallback(request):
            called.append("jizhi")
            return httpx.Response(503, text="unavailable", request=request)

        kwargs = {
            "units": [unit("s1")], "prompt": {"version": "v2"},
            "prompt_identity": "fixture-v2", "source_identity": "fixture-source",
            "environment": environment,
            "transports": {"tokenmetro": httpx.MockTransport(primary), "jizhi": httpx.MockTransport(fallback)},
        }
        first = run_sdk_route_pair(self.root, **kwargs)
        second = run_sdk_route_pair(self.root, **kwargs)
        self.assertEqual(first["status"], "blocked")
        self.assertEqual(second["provider_attempts_this_invocation"], 0)
        self.assertEqual(called, ["tokenmetro", "jizhi"])
        self.assertEqual(first["counters"]["jizhi_fallback_failures"], 1)


if __name__ == "__main__":
    unittest.main()
