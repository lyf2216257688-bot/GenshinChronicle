from __future__ import annotations

import hashlib
import json
import unittest

from genshin_corpus.parser.contracts import RawRef
from genshin_corpus.parser.dialogue import parse_dialogue_graph
from genshin_corpus.parser.obc.adapter import parse_obc_detail


class DialogueParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.component_path = "/data/page/modules/0/components/0"
        self.ref = RawRef(
            source="mihoyo_obc",
            locale="zh-cn",
            run_id="fixture-run",
            artifact_kind="details",
            artifact_path="responses/details/dialogue.json",
            artifact_sha256="a" * 64,
            content_id="dialogue-fixture",
            json_pointer=self.component_path,
        )

    def test_list_group_preserves_node_edge_order_and_traceability(self) -> None:
        graph = {
            "contents": None,
            "list": [{
                "root_id": "root",
                "child_ids": {"root": ["left", "right"], "left": ["shared"], "right": ["shared"]},
                "contents": {
                    "root": {"option": "开始", "dialogue": "<p>阿罗夏：你好</p>", "icon": "i", "extra": {"x": 1}},
                    "left": {"option": "左", "dialogue": "左文本", "icon": ""},
                    "right": {"option": "右", "dialogue": "右文本", "icon": ""},
                    "shared": {"option": "共同", "dialogue": "共同文本", "icon": ""},
                },
            }],
        }
        result = parse_dialogue_graph(graph, component_ref=self.ref)
        group = result.groups[0]
        self.assertEqual([node.source_id for node in group.nodes], ["root", "left", "right", "shared"])
        self.assertEqual([(edge.parent_id, edge.child_id) for edge in group.edges], [("root", "left"), ("root", "right"), ("left", "shared"), ("right", "shared")])
        self.assertEqual(group.nodes[0].raw_ref.embedded_json_pointer, "/list/0/contents/root")
        self.assertEqual(group.edges[1].raw_ref.embedded_json_pointer, "/list/0/child_ids/root/1")
        self.assertEqual(group.nodes[0].raw_fields, {"extra": {"x": 1}})
        self.assertIsNone(group.nodes[0].speaker)
        self.assertIsNotNone(group.nodes[0].dialogue_rich_text)
        self.assertTrue(any(item.code == "DIALOGUE_MULTIPLE_PARENT" for item in group.diagnostics))

    def test_direct_group_reports_orphan_dangling_cycle_and_root_diagnostics(self) -> None:
        graph = {
            "root_id": "a",
            "child_ids": {"a": ["b", "dangling"], "b": ["a"]},
            "contents": {"a": {}, "b": {}, "orphan": {}},
        }
        result = parse_dialogue_graph(graph, component_ref=self.ref)
        codes = {item.code for item in result.diagnostics}
        self.assertIn("DIALOGUE_DANGLING_EDGE", codes)
        self.assertIn("DIALOGUE_ORPHAN_NODE", codes)
        self.assertIn("DIALOGUE_CYCLE", codes)

    def test_unknown_group_and_empty_root_are_preserved(self) -> None:
        result = parse_dialogue_graph({"list": ["unsupported-group", {"root_id": "", "contents": {}, "child_ids": {}}]}, component_ref=self.ref)
        self.assertEqual(result.groups[0].raw_fields["raw_value"], "unsupported-group")
        self.assertIn("DIALOGUE_GROUP_NOT_OBJECT", {item.code for item in result.groups[0].diagnostics})
        self.assertIn("DIALOGUE_ROOT_EMPTY", {item.code for item in result.groups[1].diagnostics})

    def test_adapter_adds_dialogue_unit_and_keeps_classification_separate(self) -> None:
        data = {
            "root_id": "r",
            "child_ids": {"r": []},
            "contents": {"r": {"option": "", "dialogue": "文本", "icon": ""}},
        }
        payload = {"data": {"page": {"id": "dialogue-fixture", "name": "fixture", "modules": [{"id": "m", "components": [{"component_id": "interactive_dialogue", "data": json.dumps(data, ensure_ascii=False)}]}]}}}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ref = RawRef(
            source=self.ref.source,
            locale=self.ref.locale,
            run_id=self.ref.run_id,
            artifact_kind=self.ref.artifact_kind,
            artifact_path=self.ref.artifact_path,
            artifact_sha256=hashlib.sha256(body).hexdigest(),
            content_id=self.ref.content_id,
        )
        component = parse_obc_detail(body, raw_ref=ref, content_id="dialogue-fixture").modules[0].components[0]
        graph_unit = next(unit for unit in component.units if unit.value.__class__.__name__ == "DialogueGraph")
        self.assertEqual(graph_unit.metadata.content_role.labels, ("dialogue",))
        self.assertEqual(graph_unit.metadata.provenance.state, "unknown")
        self.assertEqual(graph_unit.value.groups[0].root_id, "r")


if __name__ == "__main__":
    unittest.main()
