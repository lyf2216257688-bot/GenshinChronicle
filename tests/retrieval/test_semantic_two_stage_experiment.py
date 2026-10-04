from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.retrieval.semantic_compiler_u1 import semantic_input_identity
from genshin_corpus.retrieval.semantic_live_runner import b_v4_experiment_contract
from genshin_corpus.retrieval.semantic_two_stage_experiment import (
    build_curation_payload, build_two_stage_manifest, load_two_stage_manifest,
    atomic_inventory_prompt_contract, build_atomic_inventory_manifest,
    two_stage_curator_prompt_contract, validate_atomic_inventory, validate_grouped_navigation,
)
from genshin_corpus.retrieval.semantic_sdk_runner import run_sdk_route_pair, audit_sdk_route_integrity


class TwoStageExperimentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = {"schema_version": "fixture", "segments": [{"segment_id": "s1", "text": "A waits"}]}
        self.inventory = {
            "schema_version": "phase05-w2-semantic-output-0.1", "items": [],
            "segment_coverage": [{"segment_id": "s1", "disposition": "covered", "reason": None}],
        }
        self.unit = {"compilation_unit_id": "unit1", "payload": self.payload,
                     "semantic_input_identity": semantic_input_identity(self.payload), "segment_ids": ["s1"]}
        contract = {"source_identity": "fixture-source",
                    "operating_point": {"model": "deepseek-v4.1-flash", "max_tokens": 300000,
                                        "timeout_seconds": 900, "automatic_retry": False,
                                        "generation_parameters": {}}}
        self.gate = {"identity": sha256_json(contract), "contract": contract}
        self.gate_path = self.root / "gate.json"
        self.gate_path.write_bytes(canonical_json_bytes(self.gate))
        self.output_path = self.root / "canonical_output.json"
        self.output_path.write_bytes(canonical_json_bytes(self.inventory))
        self.request_path = self.root / "request.json"
        self.request_path.write_bytes(canonical_json_bytes({"messages": [
            {"role": "system", "content": canonical_json_bytes(b_v4_experiment_contract().prompt_contract).decode()},
            {"role": "user", "content": canonical_json_bytes(self.payload).decode()},
        ]}))
        self.artifact = {"path": self.output_path.as_posix(), "sha256": sha256(self.output_path.read_bytes()).hexdigest()}
        self.loader = patch("genshin_corpus.retrieval.semantic_two_stage_experiment.frozen_v5_gate_units",
                            return_value=(self.gate, [self.unit]))
        self.loader.start()
        self.addCleanup(self.loader.stop)

    def freeze(self):
        return build_two_stage_manifest(gate_manifest_path=self.gate_path, gate_manifest=self.gate,
                                        inventory_artifacts=[self.artifact], point1_only=True)

    def test_historical_prompt_identity_remains_reproducible(self):
        old = two_stage_curator_prompt_contract("phase05-w2-two-stage-curator-prompt-0.2")
        self.assertEqual(sha256_json(old), "4965eebb5e77c28869feaf70fd910a3b004bb626b90fc258d048abe49d8eafea")
        self.assertNotEqual(sha256_json(old), sha256_json(two_stage_curator_prompt_contract()))

    def test_payload_preserves_original_and_isolates_mutations(self):
        original = deepcopy(self.payload)
        curated = build_curation_payload(self.payload, self.inventory)
        self.assertEqual(curated["segments"], original["segments"])
        self.assertEqual(curated["two_stage_context"]["inventory_role"], "candidate_only")
        self.assertNotEqual(semantic_input_identity(curated), semantic_input_identity(original))
        curated["segments"][0]["text"] = "mutated"
        curated["two_stage_context"]["inventory"]["items"].append({})
        self.assertEqual(self.payload, original)
        self.assertEqual(self.inventory["items"], [])

    def test_inventory_wrong_source_fails_closed(self):
        self.inventory["segment_coverage"][0]["segment_id"] = "other-source"
        with self.assertRaises(ValueError):
            build_curation_payload(self.payload, self.inventory)

    def test_inventory_producer_source_and_hash_are_verified(self):
        self.artifact["sha256"] = "wrong"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.freeze()
        self.artifact["sha256"] = sha256(self.output_path.read_bytes()).hexdigest()
        request = json.loads(self.request_path.read_bytes())
        request["messages"][1]["content"] = "{}"
        self.request_path.write_bytes(canonical_json_bytes(request))
        with self.assertRaisesRegex(ValueError, "producer prompt/source"):
            self.freeze()

    def test_reload_and_config_tamper_fail_closed_even_with_rehashed_identity(self):
        freeze = self.freeze()
        path = self.root / "freeze.json"
        path.write_bytes(canonical_json_bytes(freeze))
        self.assertEqual(load_two_stage_manifest(path), freeze)
        freeze["contract"]["operating_point"] = {"max_tokens": 123}
        freeze["identity"] = sha256_json(freeze["contract"])
        path.write_bytes(canonical_json_bytes(freeze))
        with self.assertRaisesRegex(ValueError, "config mismatch"):
            load_two_stage_manifest(path)

    def test_inventory_tamper_after_freeze_fails_closed(self):
        path = self.root / "freeze.json"
        path.write_bytes(canonical_json_bytes(self.freeze()))
        self.output_path.write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            load_two_stage_manifest(path)

    def test_two_stage_side_input_uses_formal_runner_and_offline_replay(self):
        frozen = self.freeze()
        contract = frozen["contract"]
        calls = []

        def handler(request):
            body = json.loads(request.content)
            calls.append(body)
            self.assertEqual(body["max_tokens"], 300000)
            self.assertEqual(json.loads(body["messages"][1]["content"])["segments"], self.payload["segments"])
            self.assertIn("two_stage_context", json.loads(body["messages"][1]["content"]))
            chunk = {"id": "mock", "object": "chat.completion.chunk", "model": "deepseek-v4.1-flash",
                     "choices": [{"index": 0, "delta": {"content": json.dumps(self.inventory)}, "finish_reason": "stop"}],
                     "usage": {"prompt_tokens": 1, "completion_tokens": 2}}
            stream = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream)

        result = run_sdk_route_pair(
            self.root / "mock-run", units=contract["stage_b"]["units"],
            prompt=contract["stage_b"]["prompt"], prompt_identity=contract["stage_b"]["prompt_identity"],
            source_identity=frozen["identity"], max_tokens=300000, timeout_seconds=900,
            environment={"TOKENMETRO_API_KEY": "mock-secret", "TOKENMETRO_BASE_URL": "https://tokenmetro.com/v1",
                         "JIZHI_BASE_URL": "https://jizhiapi.site/v1"},
            transports={"tokenmetro": httpx.MockTransport(handler)},
        )
        self.assertEqual((result["accepted_units"], len(calls)), (1, 1))
        audit = audit_sdk_route_integrity(self.root / "mock-run")
        self.assertEqual(audit["status"], "PASS")
        self.assertEqual((audit["provider_calls_executed"], audit["network_calls_executed"]), (0, 0))

    def grouped_fixture(self):
        self.inventory["items"] = [{"local_id": "f1", "kind": "fact", "label": "A waits",
                                    "source_segment_ids": ["s1"], "topic_path": [], "qualifiers": {}}]
        payload = build_curation_payload(self.payload, self.inventory)
        output = deepcopy(self.inventory)
        output["items"] = [
            {"local_id": "g1", "kind": "topic", "label": "Waiting", "source_segment_ids": ["s1"], "topic_path": [],
             "qualifiers": {"role": "navigation_group", "group_kind": "state", "member_ids": ["f1"],
                            "annotations": [{"member_ids": ["f1"], "source_quote": "A waits", "qualifiers": {}}]}},
            {"local_id": "audit", "kind": "topic", "label": "Review", "source_segment_ids": ["s1"], "topic_path": [],
             "qualifiers": {"role": "inventory_accounting", "omissions": [], "missing_source_claims": []}},
        ]
        return payload, output

    def test_grouped_view_requires_complete_nonoverlapping_inventory_accounting(self):
        payload, output = self.grouped_fixture()
        self.assertEqual(validate_grouped_navigation(output, payload)["status"], "PASS")
        output["items"][1]["qualifiers"]["omissions"] = [{"member_id": "f1", "disposition": "decorative_only", "reason": "x"}]
        with self.assertRaisesRegex(ValueError, "duplicate inventory omission"):
            validate_grouped_navigation(output, payload)
        output["items"] = output["items"][1:]
        output["items"][0]["qualifiers"]["omissions"] = []
        with self.assertRaisesRegex(ValueError, "accounting is incomplete"):
            validate_grouped_navigation(output, payload)

    def test_grouped_view_rejects_wrong_quote_global_qualifier_and_missing_recall(self):
        for defect, message in [("quote", "original source"), ("global", "group-wide"), ("recall", "recall gate")]:
            payload, output = self.grouped_fixture()
            if defect == "quote":
                output["items"][0]["qualifiers"]["annotations"][0]["source_quote"] = "A leaves"
            elif defect == "global":
                output["items"][0]["qualifiers"]["polarity"] = "negative"
            else:
                output["items"][1]["qualifiers"]["missing_source_claims"] = ["missing substantive claim"]
            with self.assertRaisesRegex(ValueError, message):
                validate_grouped_navigation(output, payload)

    def test_atomic_inventory_requires_one_source_quote_and_allowed_scope(self):
        payload = {"schema_version": "fixture", "segments": [{"segment_id": "s1", "text": "A waits; A leaves"}]}
        good = {"schema_version": "phase05-w2-semantic-output-0.1", "items": [
            {"local_id": "f1", "kind": "fact", "label": "A waits", "source_segment_ids": ["s1"],
             "topic_path": [], "qualifiers": {"source_quote": "A waits", "modality": "reported"}},
            {"local_id": "f2", "kind": "fact", "label": "A leaves", "source_segment_ids": ["s1"],
             "topic_path": [], "qualifiers": {"source_quote": "A leaves", "polarity": "negative"}},
        ], "segment_coverage": [{"segment_id": "s1", "disposition": "covered", "reason": None}]}
        self.assertEqual(validate_atomic_inventory(good, payload)["item_count"], 2)
        repeated = deepcopy(good)
        repeated["items"][1]["qualifiers"]["source_quote"] = "A waits"
        self.assertEqual(validate_atomic_inventory(repeated, payload)["duplicate_quote_items"], 1)
        bad = deepcopy(good)
        del bad["items"][0]["qualifiers"]["source_quote"]
        with self.assertRaisesRegex(ValueError, "lacks source_quote"):
            validate_atomic_inventory(bad, payload)
        bad = deepcopy(good)
        bad["items"][0]["qualifiers"]["negation"] = "x"
        with self.assertRaisesRegex(ValueError, "unsupported qualifier"):
            validate_atomic_inventory(bad, payload)
        bad = deepcopy(good)
        bad["items"][0]["qualifiers"]["source_quote"] = "not in source"
        with self.assertRaisesRegex(ValueError, "not in source"):
            validate_atomic_inventory(bad, payload)

    def test_atomic_quote_accepts_original_decoded_rich_text(self):
        payload = {"schema_version": "fixture", "segments": [{
            "segment_id": "s1", "value": {"decoded": {"rich_text":
                "<p>这些奇异的封印似乎是因<strong>甘露</strong>的力量而结成的</p>"}}}]}
        output = {"schema_version": "phase05-w2-semantic-output-0.1", "items": [
            {"local_id": "f1", "kind": "fact", "label": "封印似乎由甘露结成",
             "source_segment_ids": ["s1"], "topic_path": [],
             "qualifiers": {"source_quote": "封印似乎是因甘露的力量而结成的",
                            "modality": "tentative"}},
            {"local_id": "f2", "kind": "fact", "label": "封印似乎由甘露结成",
             "source_segment_ids": ["s1"], "topic_path": [],
             "qualifiers": {"source_quote": "封印似乎是因<strong>甘露</strong>的力量而结成的",
                            "modality": "tentative"}},
        ], "segment_coverage": [{"segment_id": "s1", "disposition": "covered", "reason": None}]}
        self.assertEqual(validate_atomic_inventory(output, payload)["status"], "PASS")

    def test_atomic_prompt_is_distinct_and_manifest_binds_gate(self):
        prompt = atomic_inventory_prompt_contract()
        self.assertEqual(prompt["version"], "phase05-w2-atomic-inventory-prompt-0.7")
        old_prompt = atomic_inventory_prompt_contract("phase05-w2-atomic-inventory-prompt-0.1")
        self.assertEqual(sha256_json(old_prompt), "1080ff4972897321cbbe0bf09bbf70400c082b78acd31df4f8a6b38de500af4d")
        v04 = atomic_inventory_prompt_contract("phase05-w2-atomic-inventory-prompt-0.4")
        self.assertEqual(sha256_json(v04), "41b9c87f405d8680c612600ab8634c1ec49225677ff80ec9a5defdab678b14a1")
        v05 = atomic_inventory_prompt_contract("phase05-w2-atomic-inventory-prompt-0.5")
        self.assertEqual(sha256_json(v05), "bd86f479734a740abd4fa5f49b61e11e80f8fbf8d80160143af0fc0ee95b5d00")
        v06 = atomic_inventory_prompt_contract("phase05-w2-atomic-inventory-prompt-0.6")
        self.assertEqual(sha256_json(v06), "ef871d99403defb1876771582530eb5aacc6aad9a501295126b652298d01d12f")
        self.assertIn("one independently stated proposition", prompt["task"])
        rules = " ".join(prompt["extraction_rules"])
        self.assertIn("unknown identity", rules)
        self.assertIn("modality=rhetorical", rules)
        self.assertIn("two items", rules)
        self.assertIn("unknown_identity", rules)
        self.assertIn("unknown_target", rules)
        self.assertIn("不认识它们", rules)
        self.assertIn("stone wall pillars are wrapped with gold", rules)
        self.assertNotIn("stone wall pillars are wrapped with gold", " ".join(v04["extraction_rules"]))
        self.assertIn("purpose clause", rules)
        self.assertIn("not the action's actor", rules)
        self.assertNotIn("A purpose clause such as", " ".join(v05["extraction_rules"]))
        self.assertNotIn("if/when/purpose clause", rules)
        self.assertIn("modality=self_corrected", rules)
        self.assertIn("modality=conditional", rules)
        self.assertNotIn("modality=self_corrected", " ".join(v06["extraction_rules"]))
        manifest = build_atomic_inventory_manifest(
            gate_manifest_path=self.gate_path, gate_manifest=self.gate, unit_indexes=(0,))
        self.assertEqual(manifest["contract"]["prompt"], prompt)
        self.assertEqual(manifest["contract"]["units"][0]["payload"], self.payload)
        self.assertEqual(manifest["contract"]["operating_point"]["max_tokens"], 300000)


if __name__ == "__main__":
    unittest.main()
