from __future__ import annotations

from dataclasses import replace
import gzip
import json
import tempfile
import unittest
from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.retrieval.semantic_compiler_u1 import SEMANTIC_OUTPUT_SCHEMA_VERSION
from genshin_corpus.retrieval.semantic_compiler_u1 import semantic_output_schema
from genshin_corpus.retrieval.semantic_live_runner import (
    ACCEPTED_PREFLIGHT_IDENTITY,
    ChannelConfig,
    GEMINI_A_USER_AGENT,
    SemanticProviderRequest,
    SemanticProviderResponse,
    SemanticProviderTransportError,
    SemanticLiveRunnerError,
    load_adapter,
    replay_response,
    run_channel,
)
from genshin_corpus.retrieval.semantic_openai_chat_adapter import (
    OpenAIChatCompletionsAdapter,
    create_adapter,
)


PREFLIGHT = Path("data/retrieval/p05-w2-live-preflight-20260923-r2")
FAILED_CANARY = Path("data/retrieval/p05-w2-live-canary-20260923-r1")


def _envelope(request: SemanticProviderRequest) -> dict:
    segment_ids = list(request.payload["segments"][0:])
    segment_ids = [str(item["segment_id"]) for item in segment_ids]
    return {
        "schema_version": SEMANTIC_OUTPUT_SCHEMA_VERSION,
        "items": [],
        "segment_coverage": [
            {"segment_id": segment_id, "disposition": "no_navigation_material", "reason": "fixture"}
            for segment_id in segment_ids
        ],
    }


class FakeAdapter:
    def __init__(self, *, malformed: bool = False) -> None:
        self.build_calls = 0
        self.calls = 0
        self.malformed = malformed

    def build_request_body(self, request: SemanticProviderRequest) -> bytes:
        self.build_calls += 1
        return canonical_json_bytes({"fake_model": request.config.model_alias, "payload": request.payload})

    def invoke(self, request: SemanticProviderRequest) -> SemanticProviderResponse:
        self.calls += 1
        body = b'{"malformed":true}' if self.malformed else canonical_json_bytes(_envelope(request))
        return SemanticProviderResponse(body, transport_status=200, provider_request_id=f"fake-{self.calls}", usage={"input_tokens": 12, "output_tokens": 4}, charge=0, billable=False)

    def parse_response(self, raw_response_bytes: bytes) -> dict:
        return json.loads(raw_response_bytes.decode("utf-8"))


class TransportErrorAdapter(FakeAdapter):
    def invoke(self, request: SemanticProviderRequest) -> SemanticProviderResponse:
        self.calls += 1
        raise SemanticProviderTransportError(
            "http_status_400",
            raw_response_bytes=b'{"error":{"message":"fixture rejection"}}',
            transport_status=400,
            provider_request_id="request-fixture",
            usage={"input_tokens": 7},
            response_headers={
                "content-type": "application/json",
                "server": "fixture-gateway",
                "cf-ray": "fixture-ray",
                "x-request-id": "fixture-request",
                "set-cookie": "fixture-cookie",
                "authorization": "Bearer fixture-secret",
                "x-debug": "fixture-secret",
            },
        )


class NoHeaderTransportErrorAdapter(FakeAdapter):
    def invoke(self, request: SemanticProviderRequest) -> SemanticProviderResponse:
        self.calls += 1
        raise SemanticProviderTransportError(
            "http_status_503",
            raw_response_bytes=b'{"error":"unavailable"}',
            transport_status=503,
        )


def _config(channel: str) -> ChannelConfig:
    if channel == "deepseek":
        provider, model, reasoning = "JizhiAPI", "DeepSeek V4.1 Flash", "no separate control configured"
    else:
        provider, model, reasoning = "Gemini", "Gemini 3.8 Flash", "Medium"
    return ChannelConfig(
        channel=channel,
        provider=provider,
        endpoint="https://example.invalid/provider",
        adapter_factory="tests.fixture:fake",
        auth_mode="fixture",
        api_key_env="TEST_P05_API_KEY",
        model_alias=model,
        transport_model="deepseek-v4.1-flash" if channel == "deepseek" else "gemini-3.8-flash",
        reasoning_or_thinking=reasoning,
        structured_output_mode="JSON_SCHEMA",
        generation_parameters={"temperature": 0.0},
        max_output_tokens=8192,
        timeout_seconds=120.0,
    )


def _request(config: ChannelConfig) -> SemanticProviderRequest:
    return SemanticProviderRequest(
        channel=config.channel,
        compilation_unit_id="fixture-unit",
        semantic_input_identity="fixture-input",
        payload={"segments": [{"segment_id": "segment-1", "text": "fixture"}]},
        prompt_contract={"version": "fixture", "instruction": "Return the schema."},
        output_schema={"type": "object", "required": ["items"]},
        config=config,
        request_ordinal=0,
    )


def _frozen_request(channel: str) -> SemanticProviderRequest:
    unit_file = "deepseek_units.json" if channel == "deepseek" else "gemini_units.json"
    payload_file = "deepseek_payloads.jsonl.gz" if channel == "deepseek" else "gemini_payloads.jsonl.gz"
    unit = json.loads((PREFLIGHT / unit_file).read_text(encoding="utf-8"))["items"][0]
    with gzip.open(PREFLIGHT / payload_file, "rt", encoding="utf-8") as handle:
        payload = json.loads(next(handle))
    return SemanticProviderRequest(
        channel=channel,
        compilation_unit_id=unit["compilation_unit_id"],
        semantic_input_identity=unit["semantic_input_identity"],
        payload=payload,
        prompt_contract=json.loads((PREFLIGHT / "prompt_contract.json").read_text(encoding="utf-8")),
        output_schema=semantic_output_schema(),
        config=ChannelConfig.from_environment(channel, {}),
        request_ordinal=0,
    )


class FakeHTTPResponse:
    def __init__(self, body: bytes, *, status: int = 200, headers: Message | None = None) -> None:
        self.body = body
        self.status = status
        self.headers = headers or Message()

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body

    def getcode(self) -> int:
        return self.status


class SemanticLiveRunnerTests(unittest.TestCase):
    def test_exact_accepted_preflight_identity_is_required_before_adapter_use(self) -> None:
        accepted = json.loads((PREFLIGHT / "preflight.json").read_text(encoding="utf-8"))
        self.assertEqual(accepted["preflight_identity"], ACCEPTED_PREFLIGHT_IDENTITY)
        unit_id = json.loads((PREFLIGHT / "gemini_units.json").read_text(encoding="utf-8"))["items"][0]["compilation_unit_id"]

        accepted_adapter = FakeAdapter()
        with tempfile.TemporaryDirectory() as temp:
            result = run_channel(PREFLIGHT, Path(temp), _config("gemini_a"), accepted_adapter, unit_id=unit_id, environment={"TEST_P05_API_KEY": "fixture-secret"})
            self.assertEqual(result["status"], "complete")
            self.assertEqual(accepted_adapter.build_calls, 1)
            self.assertEqual(accepted_adapter.calls, 1)

        forged = dict(accepted)
        forged["review_fixture_variant"] = "self-consistent-but-not-accepted"
        forged["preflight_identity"] = sha256_json({key: value for key, value in forged.items() if key not in {"preflight_identity", "artifacts"}})
        self.assertNotEqual(forged["preflight_identity"], ACCEPTED_PREFLIGHT_IDENTITY)
        self.assertEqual(
            forged["preflight_identity"],
            sha256_json({key: value for key, value in forged.items() if key not in {"preflight_identity", "artifacts"}}),
        )

        rejected_adapter = FakeAdapter()
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp)
            forged_root = temp_root / "forged-preflight"
            forged_root.mkdir()
            (forged_root / "preflight.json").write_bytes(canonical_json_bytes(forged))
            run_root = temp_root / "run"
            with self.assertRaisesRegex(SemanticLiveRunnerError, "exact accepted frozen identity"):
                run_channel(forged_root, run_root, _config("gemini_a"), rejected_adapter, unit_id=unit_id, environment={"TEST_P05_API_KEY": "fixture-secret"})
            self.assertEqual(rejected_adapter.build_calls, 0)
            self.assertEqual(rejected_adapter.calls, 0)
            self.assertFalse(run_root.exists())

    def test_authorized_channel_profiles_preserve_alias_and_map_transport_models(self) -> None:
        expected = {
            "gemini_a": ("https://jizhiapi.site/v1", "gemini-3.8-flash", "Gemini 3.8 Flash"),
            "gemini_b": ("https://tokenmetro.com/v1", "gemini-3.8-flash", "Gemini 3.8 Flash"),
            "deepseek": ("https://jizhiapi.site/v1", "deepseek-v4.1-flash", "DeepSeek V4.1 Flash"),
            "glm": ("https://tokenmetro.com/v1", "glm-5.3-flash", "GLM 5.3 Flash"),
        }
        for channel, values in expected.items():
            config = ChannelConfig.from_environment(channel, {})
            self.assertEqual((config.endpoint, config.transport_model, config.model_alias), values)
            self.assertEqual(config.auth_mode, "bearer")
        self.assertEqual(ChannelConfig.from_environment("gemini_a", {}).user_agent, GEMINI_A_USER_AGENT)
        self.assertIsNone(ChannelConfig.from_environment("gemini_b", {}).user_agent)
        self.assertEqual(ChannelConfig.from_environment("deepseek", {}).provider, "JizhiAPI")
        self.assertEqual(ChannelConfig.from_environment("deepseek", {}).user_agent, GEMINI_A_USER_AGENT)
        glm = ChannelConfig.from_environment("glm", {})
        self.assertEqual(glm.provider, "TokenMetro")
        self.assertEqual(glm.api_key_env, "TOKENMETRO_API_KEY")
        self.assertIsNone(glm.user_agent)
        self.assertEqual(glm.config_identity, "aea6c1420f4e7a433a4d44bf62fba94cb629586ed2d5e5db52988750963b4739")
        self.assertEqual(ChannelConfig.from_environment("gemini_a", {}).config_identity, "217cbdbd33b3d0c7e3bffb0b8cdf19e3781feb4a3ea3a63219cf2e8bc9bd0314")
        self.assertEqual(ChannelConfig.from_environment("gemini_b", {}).config_identity, "df0fd4f7c5c0d204eda1eeae943032b69f7257f16b794d18339d5962c173de38")
        self.assertEqual(ChannelConfig.from_environment("deepseek", {}).config_identity, "b5017f94683768b4a518521d6d35cba420532ef77e70cc61b8a0e1482b56abce")
        with self.assertRaisesRegex(SemanticLiveRunnerError, "authorized channel transport profile"):
            ChannelConfig.from_environment("gemini_b", {"GENSHIN_P05_GEMINI_B_ENDPOINT": "https://other.invalid/v1"})

    def test_openai_request_is_non_streaming_structured_and_contains_no_secret(self) -> None:
        secret = "fixture-super-secret"
        for channel in ("gemini_a", "gemini_b", "deepseek", "glm"):
            config = ChannelConfig.from_environment(channel, {})
            adapter = OpenAIChatCompletionsAdapter(config, secret)
            body_bytes = adapter.build_request_body(_request(config))
            body = json.loads(body_bytes)
            self.assertEqual(body["model"], config.transport_model)
            self.assertIs(body["stream"], False)
            self.assertEqual(body["response_format"]["type"], "json_schema")
            self.assertTrue(body["response_format"]["json_schema"]["strict"])
            self.assertNotIn(secret.encode(), body_bytes)
            if channel in {"deepseek", "glm"}:
                self.assertNotIn("reasoning_effort", body)
            else:
                self.assertEqual(body["reasoning_effort"], "medium")

    def test_frozen_canary_request_json_is_structurally_unchanged(self) -> None:
        canary_root = Path("data/retrieval/p05-w2-live-canary-20260923-r1")
        for channel in ("gemini_a", "gemini_b", "deepseek"):
            request = _frozen_request(channel)
            actual = json.loads(OpenAIChatCompletionsAdapter(request.config, "fixture-secret").build_request_body(request))
            ledger = json.loads((canary_root / channel / "metadata/request_ledger.jsonl").read_text(encoding="utf-8"))
            expected = json.loads((canary_root / channel / ledger["request_artifact"]["path"]).read_text(encoding="utf-8"))
            self.assertEqual(actual, expected, channel)

    def test_invoke_uses_bearer_header_and_normalizes_available_usage(self) -> None:
        secret = "fixture-super-secret"
        captured: dict[str, object] = {}
        envelope = {
            "schema_version": SEMANTIC_OUTPUT_SCHEMA_VERSION,
            "items": [],
            "segment_coverage": [{"segment_id": "segment-1", "disposition": "no_navigation_material", "reason": "fixture"}],
        }
        raw = canonical_json_bytes({
            "id": "chatcmpl-fixture",
            "choices": [{"message": {"content": json.dumps(envelope)}}],
            "usage": {
                "prompt_tokens": 21,
                "completion_tokens": 8,
                "prompt_tokens_details": {"cached_tokens": 5},
                "completion_tokens_details": {"reasoning_tokens": 3},
            },
        })

        def opener(request: object, *, timeout: float) -> FakeHTTPResponse:
            captured.update(url=request.full_url, authorization=request.get_header("Authorization"), user_agent=request.get_header("User-agent"), data=request.data, timeout=timeout)
            return FakeHTTPResponse(raw)

        config = ChannelConfig.from_environment("gemini_a", {})
        adapter = OpenAIChatCompletionsAdapter(config, secret, opener=opener, allow_legacy_live=True)
        response = adapter.invoke(_request(config))
        self.assertEqual(captured["url"], "https://jizhiapi.site/v1/chat/completions")
        self.assertEqual(captured["authorization"], f"Bearer {secret}")
        self.assertEqual(captured["user_agent"], GEMINI_A_USER_AGENT)
        self.assertNotIn(secret.encode(), captured["data"])
        self.assertEqual(response.provider_request_id, "chatcmpl-fixture")
        self.assertEqual(response.usage, {"input_tokens": 21, "output_tokens": 8, "cached_tokens": 5, "reasoning_tokens": 3})
        self.assertEqual(adapter.parse_response(raw), envelope)

        captured.clear()
        deepseek_config = ChannelConfig.from_environment("deepseek", {})
        deepseek_adapter = OpenAIChatCompletionsAdapter(deepseek_config, secret, opener=opener, allow_legacy_live=True)
        deepseek_adapter.invoke(_request(deepseek_config))
        self.assertEqual(captured["url"], "https://jizhiapi.site/v1/chat/completions")
        self.assertEqual(captured["authorization"], f"Bearer {secret}")
        self.assertEqual(captured["user_agent"], GEMINI_A_USER_AGENT)

    def test_adapter_fails_closed_on_http_malformed_and_missing_output(self) -> None:
        config = ChannelConfig.from_environment("gemini_b", {})
        error_body = b'{"error":{"message":"rejected"}}'

        response_headers = Message()
        response_headers["Content-Type"] = "application/json"
        response_headers["Server"] = "fixture-gateway"
        response_headers["CF-Ray"] = "fixture-ray"
        response_headers["X-Request-ID"] = "fixture-request"
        response_headers["Set-Cookie"] = "fixture-cookie"
        response_headers["Authorization"] = "Bearer fixture-secret"
        response_headers["X-Debug"] = "fixture-secret"

        def http_error(*_args: object, **_kwargs: object) -> FakeHTTPResponse:
            raise HTTPError("https://tokenmetro.com/v1/chat/completions", 400, "bad request", response_headers, BytesIO(error_body))

        adapter = OpenAIChatCompletionsAdapter(config, "fixture-secret", opener=http_error, allow_legacy_live=True)
        with self.assertRaisesRegex(SemanticProviderTransportError, "http_status_400") as raised:
            adapter.invoke(_request(config))
        self.assertEqual(raised.exception.raw_response_bytes, error_body)
        self.assertEqual(raised.exception.response_headers, {
            "content-type": "application/json",
            "server": "fixture-gateway",
            "cf-ray": "fixture-ray",
            "x-request-id": "fixture-request",
        })
        with self.assertRaisesRegex(SemanticLiveRunnerError, "not valid JSON"):
            adapter.parse_response(b"not-json")
        with self.assertRaisesRegex(SemanticLiveRunnerError, "no semantic output content"):
            adapter.parse_response(canonical_json_bytes({"choices": [{"message": {"content": ""}}]}))
        with self.assertRaisesRegex(SemanticLiveRunnerError, "API error"):
            adapter.parse_response(error_body)

    def test_factory_requires_runtime_secret_and_run_artifacts_do_not_persist_it(self) -> None:
        config = ChannelConfig.from_environment("gemini_a", {})
        with self.assertRaisesRegex(SemanticLiveRunnerError, config.api_key_env):
            create_adapter(config, {})
        secret = "fixture-super-secret"
        loaded = load_adapter(config.adapter_factory, config, {config.api_key_env: secret})
        self.assertIsInstance(loaded, OpenAIChatCompletionsAdapter)
        with self.assertRaisesRegex(SemanticLiveRunnerError, "authorized channel profile"):
            create_adapter(replace(config, transport_model="unauthorized-model"), {config.api_key_env: secret})
        units = json.loads((PREFLIGHT / "gemini_units.json").read_text(encoding="utf-8"))["items"]
        adapter = FakeAdapter()
        with tempfile.TemporaryDirectory() as temp:
            run_channel(PREFLIGHT, Path(temp), config, adapter, unit_id=units[0]["compilation_unit_id"], environment={config.api_key_env: secret})
            for path in Path(temp).rglob("*"):
                if path.is_file():
                    self.assertNotIn(secret.encode(), path.read_bytes(), str(path))

    def test_canary_persists_raw_response_and_replays_without_invocation(self) -> None:
        units = json.loads((PREFLIGHT / "gemini_units.json").read_text(encoding="utf-8"))["items"]
        unit_id = units[0]["compilation_unit_id"]
        adapter = FakeAdapter()
        with tempfile.TemporaryDirectory() as temp:
            result = run_channel(PREFLIGHT, Path(temp), _config("gemini_a"), adapter, unit_id=unit_id, environment={"TEST_P05_API_KEY": "fixture-secret"})
            self.assertEqual(result["status"], "complete")
            self.assertEqual(adapter.calls, 1)
            ledger = (Path(temp) / "metadata/request_ledger.jsonl").read_text(encoding="utf-8")
            row = json.loads(ledger)
            self.assertEqual(row["attempted_request_count"], 1)
            self.assertEqual(row["status"], "succeeded")
            self.assertEqual(row["usage"], {"input_tokens": 12, "output_tokens": 4, "cached_tokens": "UNKNOWN", "reasoning_tokens": "UNKNOWN"})
            self.assertTrue((Path(temp) / row["response_artifact"]["path"]).exists())
            replay = replay_response(Path(temp), _config("gemini_a"), adapter, unit_id)
            self.assertEqual(replay["provider_calls_executed"], 0)
            self.assertEqual(adapter.calls, 1)

            with self.assertRaisesRegex(SemanticLiveRunnerError, "already has an issued attempt"):
                run_channel(PREFLIGHT, Path(temp), _config("gemini_a"), adapter, unit_id=unit_id, environment={"TEST_P05_API_KEY": "fixture-secret"})

    def test_malformed_response_is_preserved_and_fail_closed(self) -> None:
        unit_id = json.loads((PREFLIGHT / "gemini_units.json").read_text(encoding="utf-8"))["items"][1]["compilation_unit_id"]
        adapter = FakeAdapter(malformed=True)
        with tempfile.TemporaryDirectory() as temp:
            result = run_channel(PREFLIGHT, Path(temp), _config("gemini_b"), adapter, unit_id=unit_id, environment={"TEST_P05_API_KEY": "fixture-secret"})
            self.assertEqual(result["status"], "partial")
            row = json.loads((Path(temp) / "metadata/request_ledger.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(row["status"], "failed")
            self.assertEqual(row["schema_parser_binding_disposition"], "rejected_fail_closed")
            self.assertIn("response_artifact", row)
            self.assertTrue((Path(temp) / row["response_artifact"]["path"]).exists())

    def test_transport_error_response_is_preserved_and_accounted(self) -> None:
        unit_id = json.loads((PREFLIGHT / "gemini_units.json").read_text(encoding="utf-8"))["items"][2]["compilation_unit_id"]
        adapter = TransportErrorAdapter()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = run_channel(PREFLIGHT, root, _config("gemini_b"), adapter, unit_id=unit_id, environment={"TEST_P05_API_KEY": "fixture-secret"})
            self.assertEqual(result["status"], "partial")
            row = json.loads((root / "metadata/request_ledger.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(row["schema_parser_binding_disposition"], "transport_failed")
            self.assertEqual(row["failure"], {"code": "http_status_400"})
            self.assertEqual(row["usage"], {"input_tokens": 7, "output_tokens": "UNKNOWN", "cached_tokens": "UNKNOWN", "reasoning_tokens": "UNKNOWN"})
            self.assertEqual(row["response_headers"], {
                "content-type": "application/json",
                "server": "fixture-gateway",
                "cf-ray": "fixture-ray",
                "x-request-id": "fixture-request",
            })
            self.assertEqual((root / row["response_artifact"]["path"]).read_bytes(), b'{"error":{"message":"fixture rejection"}}')
            persisted = b"".join(path.read_bytes() for path in root.rglob("*") if path.is_file())
            self.assertNotIn(b"fixture-cookie", persisted)
            self.assertNotIn(b"Bearer fixture-secret", persisted)
            self.assertNotIn(b"x-debug", persisted.lower())

        unit_id = json.loads((PREFLIGHT / "gemini_units.json").read_text(encoding="utf-8"))["items"][3]["compilation_unit_id"]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_channel(PREFLIGHT, root, _config("gemini_b"), NoHeaderTransportErrorAdapter(), unit_id=unit_id, environment={"TEST_P05_API_KEY": "fixture-secret"})
            row = json.loads((root / "metadata/request_ledger.jsonl").read_text(encoding="utf-8"))
            self.assertNotIn("response_headers", row)

    def test_remaining_uses_hard_ceiling_and_does_not_retry_failed_units(self) -> None:
        units = json.loads((PREFLIGHT / "deepseek_units.json").read_text(encoding="utf-8"))["items"]
        consumed_id = units[0]["compilation_unit_id"]
        next_id = units[1]["compilation_unit_id"]
        config = ChannelConfig.from_environment("deepseek", {})
        environment = {config.api_key_env: "deepseek-fixture-secret"}
        adapter = FakeAdapter()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(SemanticLiveRunnerError, "prior attempt evidence"):
                run_channel(PREFLIGHT, root, config, adapter, unit_id=next_id, environment=environment)
            with self.assertRaisesRegex(SemanticLiveRunnerError, "already has an issued attempt"):
                run_channel(PREFLIGHT, root, config, adapter, unit_id=consumed_id, environment=environment, prior_attempt_roots=(FAILED_CANARY / "deepseek",))
            with self.assertRaisesRegex(SemanticLiveRunnerError, "next unattempted frozen unit"):
                run_channel(PREFLIGHT, root, config, adapter, unit_id=units[2]["compilation_unit_id"], environment=environment, prior_attempt_roots=(FAILED_CANARY / "deepseek",))
            with self.assertRaisesRegex(SemanticLiveRunnerError, "one explicit next unattempted unit"):
                run_channel(PREFLIGHT, root, config, adapter, remaining=True, environment=environment, prior_attempt_roots=(FAILED_CANARY / "deepseek",))
            self.assertEqual(adapter.calls, 0)
            result = run_channel(PREFLIGHT, root, config, adapter, unit_id=next_id, environment=environment, prior_attempt_roots=(FAILED_CANARY / "deepseek",))
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["attempted_requests"], 1)
            self.assertEqual(result["prior_attempted_requests"], 1)
            self.assertEqual(result["cumulative_attempted_requests"], 2)
            row = json.loads((root / "metadata/request_ledger.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(row["compilation_unit_id"], next_id)
            self.assertEqual(row["request_ordinal"], 1)
            persisted = b"".join(path.read_bytes() for path in root.rglob("*") if path.is_file())
            self.assertNotIn(b"deepseek-fixture-secret", persisted)

    def test_gemini_channel_ceiling_is_thirty(self) -> None:
        adapter = FakeAdapter()
        with tempfile.TemporaryDirectory() as temp:
            result = run_channel(PREFLIGHT, Path(temp), _config("gemini_a"), adapter, remaining=True, environment={"TEST_P05_API_KEY": "fixture-secret"})
            self.assertEqual(result["attempted_requests"], 30)
            self.assertEqual(result["maximum_attempts"], 30)
            self.assertEqual(adapter.calls, 30)


if __name__ == "__main__":
    unittest.main()
