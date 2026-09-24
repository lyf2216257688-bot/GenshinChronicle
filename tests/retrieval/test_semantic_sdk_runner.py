from __future__ import annotations

from contextlib import redirect_stderr
from io import StringIO
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from genshin_corpus.retrieval.__main__ import _semantic_live_run_main
from genshin_corpus.retrieval.semantic_compiler_u1 import SEMANTIC_OUTPUT_SCHEMA_VERSION, semantic_input_identity
from genshin_corpus.retrieval.semantic_live_runner import ChannelConfig, SemanticProviderRequest, load_adapter, run_channel
from genshin_corpus.retrieval.semantic_openai_chat_adapter import OpenAIChatCompletionsAdapter, create_adapter
from genshin_corpus.retrieval.semantic_sdk_runner import run_sdk_units
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


if __name__ == "__main__":
    unittest.main()
