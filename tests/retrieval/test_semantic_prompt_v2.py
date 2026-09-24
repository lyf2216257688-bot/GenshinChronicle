from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval.semantic_compiler_u1 import (
    SEMANTIC_OUTPUT_SCHEMA_IDENTITY,
    SEMANTIC_OUTPUT_SCHEMA_VERSION,
    semantic_output_schema,
    validate_semantic_output_envelope,
)
from genshin_corpus.retrieval.semantic_live_runner import (
    B_V2_EXPERIMENT_REVISION,
    ChannelConfig,
    SemanticProviderRequest,
    b_experiment_contract,
    b_v2_experiment_contract,
    run_b_zero_network_preflight,
    validate_b_v2_navigation_references,
)
from genshin_corpus.retrieval.semantic_openai_chat_adapter import create_offline_adapter


ROOTS = {
    16: Path("data/retrieval/p05-w2-b-tokenmetro-gemini-canary-20260924-r2"),
    20: Path("data/retrieval/p05-w2-b-tokenmetro-gemini-quality-20260924-r1"),
    21: Path("data/retrieval/p05-w2-b-tokenmetro-gemini-quality-20260924-r2"),
}
REQUEST_SHA256 = {
    16: "82246a8d5fcf831fad65d6f0916258c6aa9d5bde0bb8742e9d1758c8267340d4",
    20: "221aa7350cf168f1ffbe63bf80cd72d6a2dbb27e2124cdb5118520554d1b116a",
    21: "77ee3baf6305e5284dca142c1a466e47f98fdcb843fd98191fb53591b4b5dc23",
}


def _frozen_case(ordinal: int) -> tuple[dict, dict, dict]:
    root = ROOTS[ordinal]
    ledger = json.loads((root / "metadata/request_ledger.jsonl").read_text(encoding="utf-8"))
    assert ledger["request_ordinal"] == ordinal
    artifacts = {}
    for name in ("request_artifact", "response_artifact", "accepted_output_artifact"):
        descriptor = ledger[name]
        body = (root / descriptor["path"]).read_bytes()
        assert len(body) == descriptor["byte_count"]
        assert hashlib.sha256(body).hexdigest() == descriptor["sha256"]
        artifacts[name] = body
    raw = artifacts["request_artifact"]
    assert ledger["request_artifact"]["sha256"] == REQUEST_SHA256[ordinal]
    request = json.loads(raw)
    payload = json.loads(next(message["content"] for message in request["messages"] if message["role"] == "user"))
    old_output = json.loads(artifacts["accepted_output_artifact"])
    return payload, old_output, ledger


def _item(local_id: str, kind: str, label: str, segment_id: str, **fields: object) -> dict:
    return {
        "local_id": local_id,
        "kind": kind,
        "label": label,
        "source_segment_ids": [segment_id],
        "topic_path": [],
        "qualifiers": fields.pop("qualifiers", {}),
        **fields,
    }


def _review_output(ordinal: int, payload: dict) -> tuple[dict, dict[str, tuple[str, str]]]:
    if ordinal == 16:
        s = "seg-4ae2d9e34cc4f0c445e7"
        items = [_item("f1", "fact", "剧团属于科洛列夫茨基剧院", s)]
        witnesses = {"f1": (s, "其下的剧团")}
    elif ordinal == 20:
        s = "seg-4aea64302839bf07fff8"
        items = [
            _item("e1", "event", "盗宝团招募人手寻找秘宝", s, event_type="寻宝", participants=[], qualifiers={"attribution": "日志初段"}),
            _item("e2", "event", "后续笔迹声称带走魔王宝藏", s, event_type="表面收获", participants=[], qualifiers={"attribution": "后续笔迹", "modality": "reported"}),
            _item("e3", "event", "航行中迷失海雾", s, event_type="迷航", participants=[], qualifiers={"attribution": "后续笔迹", "modality": "reported"}),
            _item("e4", "event", "后续笔迹否定宝藏并称其为石头", s, event_type="发现反转", participants=[], qualifiers={"attribution": "后续笔迹", "polarity": "negated"}),
        ]
        witnesses = {
            "e1": (s, "为了寻找秘宝"),
            "e2": (s, "带走了不少魔王的宝藏"),
            "e3": (s, "依然没看到陆地，到处都是无边的海雾"),
            "e4": (s, "这些根本不是宝藏，只是石头"),
        }
    else:
        opening = "seg-1f2cf0e11be7a2f56e28"
        conclusion = "seg-32c05d483af07183dbd6"
        items = [
            _item("m1", "mention", "亲水生物", conclusion),
            _item("m2", "mention", "富含水元素的水草", conclusion),
            _item("m3", "mention", "奇怪物件", opening),
            _item("m4", "mention", "水岩龙蜥", opening),
            _item("r1", "relation", "海迪夫推测亲水生物食用水草提升力量", conclusion,
                  subject_ref="m1", object_ref="m2", predicate="食用以提升力量",
                  qualifiers={"attribution": "海迪夫", "modality": "tentative"}),
            _item("r2", "relation", "奇怪物件并未变成岩龙蜥", opening,
                  subject_ref="m3", object_ref="m4", predicate="变成",
                  qualifiers={"attribution": "海迪夫", "polarity": "negated"}),
        ]
        witnesses = {
            "m1": (conclusion, "亲水生物"),
            "m2": (conclusion, "富含水元素的水草"),
            "m3": (opening, "奇怪的物件"),
            "m4": (opening, "水岩龙蜥"),
            "r1": (conclusion, "靠食用这种富含水元素的水草来提升自己的力量"),
            "r2": (opening, "不是那个东西变成的"),
        }
    output = {
        "schema_version": SEMANTIC_OUTPUT_SCHEMA_VERSION,
        "items": items,
        "segment_coverage": [
            {"segment_id": segment["segment_id"], "disposition": "covered", "reason": None}
            for segment in payload["segments"]
        ],
    }
    return output, witnesses


class SemanticPromptV2Tests(unittest.TestCase):
    def test_new_identity_reuses_authoritative_schema_and_keeps_old_b_frozen(self) -> None:
        old = b_experiment_contract()
        v2 = b_v2_experiment_contract()
        self.assertEqual(old.identity, "44a410016feec821f5ab80f40509deb94bb956f9d12331ad076e8c26227dc161")
        self.assertEqual(old.prompt_identity, "6e5f4140318937fff598c9d8a509891e95519883cd31779e855c70c2b2041a6d")
        self.assertEqual(v2.revision, B_V2_EXPERIMENT_REVISION)
        self.assertEqual(v2.prompt_identity, "25ebc483c5dfcfe9b3dc8ddd01001f75e98ff4c2b5327aa789a3af84a068003e")
        self.assertEqual(v2.identity, "50f7219556771d8698bc5f85f464bd58d2bdd84c5bbea748178ad45db7255e98")
        self.assertEqual(v2.request_contract_identity, old.request_contract_identity)
        self.assertEqual(v2.output_schema_identity, old.output_schema_identity)
        self.assertEqual(v2.output_schema_identity, SEMANTIC_OUTPUT_SCHEMA_IDENTITY)

    def test_same_v2_contract_serializes_for_three_model_channels_without_invocation(self) -> None:
        prompt = b_v2_experiment_contract().prompt_contract
        for ordinal in ROOTS:
            payload, _old_output, _ledger = _frozen_case(ordinal)
            system_messages = set()
            user_messages = set()
            for channel in ("gemini_b", "deepseek", "glm"):
                with self.subTest(ordinal=ordinal, channel=channel):
                    config = ChannelConfig.for_b_json_object(channel, {})
                    adapter = create_offline_adapter(config)
                    request = SemanticProviderRequest(channel, f"frozen-{ordinal}", f"input-{ordinal}", payload, prompt, semantic_output_schema(), config, ordinal)
                    body = json.loads(adapter.build_request_body(request))
                    self.assertEqual(body["response_format"], {"type": "json_object"})
                    self.assertIs(body["stream"], False)
                    self.assertNotIn("json_schema", body)
                    system_messages.add(body["messages"][0]["content"])
                    user_messages.add(body["messages"][1]["content"])
            self.assertEqual(system_messages, {canonical_json_bytes(prompt).decode("utf-8")})
            self.assertEqual(user_messages, {canonical_json_bytes(payload).decode("utf-8")})

    def test_frozen_inputs_support_narrow_navigation_review_records(self) -> None:
        for ordinal in ROOTS:
            with self.subTest(ordinal=ordinal):
                payload, old_output, _ledger = _frozen_case(ordinal)
                review_output, witnesses = _review_output(ordinal, payload)
                normalized = validate_semantic_output_envelope(review_output, expected_segment_ids=[s["segment_id"] for s in payload["segments"]])
                validate_b_v2_navigation_references(normalized)
                by_id = {item["local_id"]: item for item in normalized["items"]}
                by_segment = {segment["segment_id"]: segment["value"] for segment in payload["segments"]}
                self.assertEqual(set(by_id), set(witnesses))
                for item_id, (segment_id, phrase) in witnesses.items():
                    self.assertIn(phrase, by_segment[segment_id]["text"])
                    self.assertEqual(by_id[item_id]["source_segment_ids"], [segment_id])
                for item in normalized["items"]:
                    if item["kind"] == "relation":
                        self.assertIn(item["subject_ref"], by_id)
                        self.assertIn(item["object_ref"], by_id)
                        self.assertIn(by_id[item["subject_ref"]]["kind"], {"mention", "event"})
                        self.assertIn(by_id[item["object_ref"]]["kind"], {"mention", "event"})
                        self.assertNotEqual(item["predicate"], "related_to")
                    if item["kind"] == "event":
                        self.assertTrue(all(by_id[ref]["kind"] == "mention" for ref in item["participants"]))
                if ordinal == 16:
                    self.assertEqual([item["kind"] for item in normalized["items"]], ["fact"])
                if ordinal == 20:
                    self.assertFalse(any(item["kind"] == "event" for item in old_output["items"]))
                    self.assertEqual(sum(item["kind"] == "event" for item in normalized["items"]), 4)
                    self.assertIn("negated", {item["qualifiers"].get("polarity") for item in normalized["items"]})
                if ordinal == 21:
                    self.assertFalse(any(item["kind"] == "relation" for item in old_output["items"]))
                    self.assertEqual(sum(item["kind"] == "relation" for item in normalized["items"]), 2)
                    self.assertIn("tentative", {item["qualifiers"].get("modality") for item in normalized["items"]})
                self.assertEqual({entry["disposition"] for entry in old_output["segment_coverage"]}, {"covered"})

    def test_local_reference_review_rejects_names_and_generic_links(self) -> None:
        payload, _old_output, _ledger = _frozen_case(21)
        review_output, _witnesses = _review_output(21, payload)
        items = review_output["items"]
        relation = next(item for item in items if item["local_id"] == "r1")
        for changed, message in (
            ({**relation, "subject_ref": "亲水生物"}, "resolve locally"),
            ({**relation, "predicate": "related_to"}, "explicit predicate"),
        ):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, message):
                candidate = {**review_output, "items": [changed if item is relation else item for item in items]}
                normalized = validate_semantic_output_envelope(candidate, expected_segment_ids=[s["segment_id"] for s in payload["segments"]])
                validate_b_v2_navigation_references(normalized)

    def test_v2_zero_network_preflight_uses_local_ids_and_offline_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            for channel in ("gemini_b", "deepseek", "glm"):
                with self.subTest(channel=channel):
                    config = ChannelConfig.for_b_json_object(channel, {})
                    root = Path(temp) / channel
                    report = run_b_zero_network_preflight(root, config, experiment=b_v2_experiment_contract())
                    self.assertEqual(report["status"], "PASS")
                    self.assertEqual(report["formal_attempts_consumed"], 0)
                    self.assertEqual(report["provider_calls_executed"], 0)
                    self.assertEqual(report["network_calls_executed"], 0)
                    self.assertTrue(report["offline_replay_verified"])
                    output = json.loads((root / "accepted.json").read_text(encoding="utf-8"))
                    mentions = {item["local_id"] for item in output["items"] if item["kind"] == "mention"}
                    event = next(item for item in output["items"] if item["kind"] == "event")
                    self.assertEqual(set(event["participants"]), mentions)


if __name__ == "__main__":
    unittest.main()
