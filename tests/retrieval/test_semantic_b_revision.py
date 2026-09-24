from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval.semantic_compiler_u1 import SEMANTIC_OUTPUT_SCHEMA_VERSION
from genshin_corpus.retrieval.semantic_live_runner import (
    B_EXPERIMENT_REVISION,
    ChannelConfig,
    SemanticProviderRequest,
    SemanticProviderResponse,
    b_experiment_contract,
    replay_response,
    run_b_zero_network_preflight,
    run_channel,
)
from genshin_corpus.retrieval.semantic_openai_chat_adapter import (
    OpenAIChatCompletionsAdapter,
    create_offline_adapter,
)


PREFLIGHT = Path("data/retrieval/p05-w2-live-preflight-20260923-r2")
PRIOR_GEMINI_A = Path("data/retrieval/p05-w2-live-canary-20260923-r1/gemini_a")


def _unit() -> dict:
    return json.loads((PREFLIGHT / "gemini_units.json").read_text(encoding="utf-8"))["items"][1]


def _valid_output() -> dict:
    segment_ids = _unit()["segment_ids"]
    return {
        "schema_version": SEMANTIC_OUTPUT_SCHEMA_VERSION,
        "items": [],
        "segment_coverage": [
            {"segment_id": segment_id, "disposition": "no_navigation_material", "reason": "fixture"}
            for segment_id in segment_ids
        ],
    }


class FixtureBAdapter(OpenAIChatCompletionsAdapter):
    def __init__(self, config: ChannelConfig, content: str | None, *, envelope: dict | None = None) -> None:
        super().__init__(config, "fixture-super-secret")
        self.content = content
        self.envelope = envelope
        self.calls = 0

    def invoke(self, request: SemanticProviderRequest) -> SemanticProviderResponse:
        self.calls += 1
        response = self.envelope
        if response is None:
            response = {
                "id": "fixture-b-request",
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": self.content}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            }
        return SemanticProviderResponse(
            canonical_json_bytes(response),
            transport_status=200,
            provider_request_id="fixture-b-request",
            usage={"input_tokens": 11, "output_tokens": 7},
            finish_reason="stop",
            response_headers={"content-type": "application/json", "authorization": "Bearer fixture-super-secret"},
        )


class SemanticBRevisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ChannelConfig.for_b_json_object("gemini_a", {})
        self.experiment = b_experiment_contract()
        self.environment = {self.config.api_key_env: "fixture-super-secret"}

    def _run(self, content: str | None, root: Path, *, envelope: dict | None = None) -> tuple[dict, dict, FixtureBAdapter]:
        adapter = FixtureBAdapter(self.config, content, envelope=envelope)
        result = run_channel(
            PREFLIGHT,
            root,
            self.config,
            adapter,
            unit_id=_unit()["compilation_unit_id"],
            environment=self.environment,
            prior_attempt_roots=(PRIOR_GEMINI_A,),
            experiment=self.experiment,
        )
        row = json.loads((root / "metadata/request_ledger.jsonl").read_text(encoding="utf-8"))
        return result, row, adapter

    def test_b_identity_and_request_contract_are_distinct_from_frozen_a(self) -> None:
        self.assertEqual(self.experiment.revision, B_EXPERIMENT_REVISION)
        self.assertEqual(self.experiment.identity, "44a410016feec821f5ab80f40509deb94bb956f9d12331ad076e8c26227dc161")
        self.assertEqual(self.experiment.prompt_identity, "6e5f4140318937fff598c9d8a509891e95519883cd31779e855c70c2b2041a6d")
        self.assertEqual(self.experiment.request_contract_identity, "b802b77d802178525077af5ccd8250bbf88dc70b9e9bf27f5bba96e423993f2d")
        self.assertEqual(self.config.structured_output_mode, "JSON_OBJECT")
        request = SemanticProviderRequest(
            "gemini_a", "fixture-unit", "fixture-input",
            {"segments": [{"segment_id": "s1", "text": "fixture"}]},
            self.experiment.prompt_contract, {"type": "object"}, self.config, 0,
        )
        body = json.loads(OpenAIChatCompletionsAdapter(self.config, "fixture-super-secret").build_request_body(request))
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertNotIn("json_schema", json.dumps(body))
        self.assertIs(body["stream"], False)
        self.assertNotIn("fixture-super-secret", json.dumps(body))
        self.assertNotEqual(self.config.config_identity, ChannelConfig.from_environment("gemini_a", {}).config_identity)

    def test_valid_b_output_is_accepted_only_for_local_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "run"
            result, row, _adapter = self._run(json.dumps(_valid_output()), root)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(row["schema_parser_binding_disposition"], "accepted_for_local_contract")
            validation = json.loads((root / row["validation_artifact"]["path"]).read_text(encoding="utf-8"))
            self.assertEqual(validation["terminal_disposition"], "accepted_for_local_contract")
            self.assertEqual(validation["semantic_correctness"], "not_assessed")
            manifest = json.loads((root / "metadata/run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["experiment_identity"], self.experiment.identity)
            self.assertEqual(manifest["experiment_contract"]["prompt_identity"], self.experiment.prompt_identity)
            self.assertEqual(manifest["experiment_contract"]["request_contract_identity"], self.experiment.request_contract_identity)

    def test_format_and_schema_failures_are_layered_and_fail_closed(self) -> None:
        valid = _valid_output()
        invalid_item = {
            "local_id": "i1", "kind": "event", "label": "event",
            "source_segment_ids": [_unit()["segment_ids"][0]], "topic_path": [],
        }
        cases = {
            "malformed": ("{", "strict_json_parse"),
            "fenced": ("```json\n" + json.dumps(valid) + "\n```", "strict_json_parse"),
            "prose": ("result: " + json.dumps(valid), "strict_json_parse"),
            "array": ("[]", "strict_json_parse"),
            "wrong_version": (json.dumps({**valid, "schema_version": "phase05-w2-semantic-input-0.2"}), "authoritative_schema_validation"),
            "missing_required": (json.dumps({**valid, "items": [invalid_item]}), "authoritative_schema_validation"),
            "extra_field": (json.dumps({**valid, "unexpected": True}), "authoritative_schema_validation"),
        }
        for name, (content, layer) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                result, row, _adapter = self._run(content, Path(temp) / "run")
                self.assertEqual(result["status"], "partial")
                self.assertEqual(row["schema_parser_binding_disposition"], "rejected_fail_closed")
                self.assertEqual(row["failure"]["layer"], layer)

    def test_source_binding_failures_are_fail_closed(self) -> None:
        valid = _valid_output()
        segment_id = _unit()["segment_ids"][0]
        item = {
            "local_id": "i1", "kind": "event", "label": "event", "source_segment_ids": [segment_id],
            "topic_path": [], "event_type": "event", "participants": [], "qualifiers": {},
        }
        cases = {
            "invalid_reference": {**valid, "items": [{**item, "source_segment_ids": ["not-supplied"]}]},
            "missing_coverage": {**valid, "segment_coverage": []},
            "duplicate_coverage": {**valid, "segment_coverage": valid["segment_coverage"] * 2},
            "item_on_noncovered": {**valid, "items": [item]},
        }
        for name, output in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                result, row, _adapter = self._run(json.dumps(output), Path(temp) / "run")
                self.assertEqual(result["status"], "partial")
                self.assertEqual(row["failure"]["layer"], "source_binding_validation")

    def test_envelope_extraction_failure_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result, row, _adapter = self._run(None, Path(temp) / "run", envelope={"id": "fixture", "choices": []})
            self.assertEqual(result["status"], "partial")
            self.assertEqual(row["failure"]["layer"], "content_extraction")

    def test_http_200_rejection_preserves_raw_metadata_and_replays_offline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "run"
            result, row, adapter = self._run("```json\n{}\n```", root)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(row["transport_status"], 200)
            self.assertEqual(row["finish_reason"], "stop")
            self.assertEqual(row["response_headers"], {"content-type": "application/json"})
            raw = (root / row["response_artifact"]["path"]).read_bytes()
            self.assertIn(b"```json", raw)
            replay = replay_response(root, self.config, create_offline_adapter(self.config), _unit()["compilation_unit_id"], experiment=self.experiment)
            self.assertEqual(replay["disposition"], "rejected_fail_closed")
            self.assertEqual(replay["failure_layer"], "strict_json_parse")
            self.assertEqual(replay["provider_calls_executed"], 0)
            self.assertEqual(replay["network_calls_executed"], 0)
            self.assertEqual(adapter.calls, 1)
            persisted = b"".join(path.read_bytes() for path in root.rglob("*") if path.is_file())
            self.assertNotIn(b"fixture-super-secret", persisted)
            self.assertNotIn(b"Authorization", persisted)

            with self.assertRaisesRegex(ValueError, "already has an issued attempt"):
                run_channel(
                    PREFLIGHT, root, self.config, adapter,
                    unit_id=_unit()["compilation_unit_id"], environment=self.environment,
                    prior_attempt_roots=(PRIOR_GEMINI_A,), experiment=self.experiment,
                )
            self.assertEqual(adapter.calls, 1)

    def test_b_canary_rejects_consumed_unit_and_remaining_mode_before_invocation(self) -> None:
        consumed_unit = json.loads((PREFLIGHT / "gemini_units.json").read_text(encoding="utf-8"))["items"][0]
        for selector in ("consumed", "remaining"):
            with self.subTest(selector=selector), tempfile.TemporaryDirectory() as temp:
                adapter = FixtureBAdapter(self.config, json.dumps(_valid_output()))
                kwargs = {"remaining": True} if selector == "remaining" else {"unit_id": consumed_unit["compilation_unit_id"]}
                with self.assertRaises(ValueError):
                    run_channel(
                        PREFLIGHT, Path(temp) / "run", self.config, adapter,
                        environment=self.environment, prior_attempt_roots=(PRIOR_GEMINI_A,),
                        experiment=self.experiment, **kwargs,
                    )
                self.assertEqual(adapter.calls, 0)

    def test_zero_network_preflight_exercises_full_local_chain_without_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            report = run_b_zero_network_preflight(Path(temp) / "preflight", self.config)
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["formal_attempts_consumed"], 0)
            self.assertEqual(report["provider_calls_executed"], 0)
            self.assertEqual(report["network_calls_executed"], 0)
            self.assertEqual(report["terminal_disposition"], "accepted_for_local_contract")
            self.assertIs(report["offline_replay_verified"], True)
            self.assertEqual(report["experiment_identity"], self.experiment.identity)


if __name__ == "__main__":
    unittest.main()
