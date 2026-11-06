from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from genshin_corpus.clean import (
    BlockSelector, CleanRef, clean_obc_detail, iter_blocks, iter_records,
    iter_text_values, render_record, resolve_clean_ref, validate_record,
)
from genshin_corpus.clean.obc import KNOWN_COMPONENTS
from genshin_corpus.clean.pipeline import build_clean_run, main
from genshin_corpus.clean.reader import OriginResolver
from genshin_corpus.model_calls import locked


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class CleanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gc-clean-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def record(self, data, component="collapse_panel", *, raw_data=False, extra_modules=None):
        modules = [{"id": "m1", "name": "原始章节", "components": [
            {"component_id": component, "data": data if raw_data else json.dumps(data, ensure_ascii=False)}]}]
        modules += extra_modules or []
        body = encoded({"retcode": 0, "data": {"page": {"id": "one", "name": "原始标题", "desc": "描述",
                                                       "modules": modules}}})
        path = self.root / "detail.json"
        path.write_bytes(body)
        result = clean_obc_detail(body, artifact_path=path.name, artifact_sha256=hashlib.sha256(body).hexdigest(),
                                  locale="fixture-locale", raw_run_id="fixture-run", content_id="one")
        validate_record(result, self.root)
        return result

    @staticmethod
    def texts(record, selector=None):
        return [text["text"] for block in iter_blocks(record, selector) for text in iter_text_values(block)]

    def raw_run(self, *, locale="fixture-locale", run_id="raw-one", filters=None):
        root = self.root / run_id
        paths = {}

        def response(kind, key, data):
            relative = f"responses/{kind}/{key}.json"
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            body = encoded(data)
            path.write_bytes(body)
            sha = hashlib.sha256(body).hexdigest()
            meta = {"kind": kind, "key": key, "sha256": sha, "ok": True, "status": 200}
            path.with_suffix(".meta.json").write_bytes(encoded(meta))
            paths[f"{kind}:{key}"] = {"raw": relative, "metadata": relative.replace(".json", ".meta.json"), "sha256": sha}

        assigned = filters or ["任务类型/世界任务", "获取方式/杂货店/NPC购买或兑换"]
        ext = json.dumps({"c_43": {"filter": {"text": json.dumps(assigned, ensure_ascii=False)}}}, ensure_ascii=False)
        rows = [{"content_id": cid, "title": "列表标题" + cid, "summary": "独有摘要" + cid, "ext": ext}
                for cid in ("one", "two")]
        response("map", "home-map", {"retcode": 0, "data": {"list": [
            {"id": 189, "name": "图鉴", "parent_id": 0, "list": [], "children": [
                {"id": 43, "name": "任务", "parent_id": 189, "list": [], "children": []}]}]}})
        response("listings", "189", {"retcode": 0, "data": {"list": [
            {"id": 189, "name": "图鉴", "list": [], "children": [
                {"id": 43, "name": "任务", "list": rows, "children": []}]}]}})
        for cid in ("one", "two"):
            response("details", cid, {"retcode": 0, "data": {"page": {
                "id": cid, "name": "资料" + cid, "desc": "原文描述",
                "ext": {"fe_ext": ext}, "modules": [{"id": "m", "name": "正文", "components": [
                    {"component_id": "collapse_panel", "data": json.dumps({"rich_text": "<p>完整正文😀</p>"})}]}]}}})
        metadata = root / "metadata"
        metadata.mkdir(parents=True, exist_ok=True)
        inventory = [{"content_id": cid, "channels": ["189", "43"]} for cid in ("one", "two")]
        (metadata / "inventory.json").write_bytes(encoded(inventory))
        manifest = {"source_system": "mihoyo_obc", "locale": locale, "run_id": run_id, "status": "complete",
                    "paths": paths, "details": [{**item, "status": "completed"} for item in inventory]}
        (metadata / "manifest.json").write_bytes(encoded(manifest))
        return root

    def build(self, raw, run="clean", **kwargs):
        return build_clean_run(raw_run_root=raw, clean_run_id=run, output_root=self.root / "out", **kwargs)

    def manifest_path(self, run="clean", locale="fixture-locale"):
        return self.root / "out/mihoyo_obc" / locale / run / "metadata/manifest.json"

    def test_all_observed_component_shapes_are_accounted(self):
        fixture = json.loads(Path("tests/fixtures/clean/obc-components.json").read_text(encoding="utf-8"))
        self.assertEqual({case["component_id"] for case in fixture["cases"]}, KNOWN_COMPONENTS)
        for case in fixture["cases"]:
            with self.subTest(component=case["component_id"]):
                record = self.record(case["data"], case["component_id"])
                coverage = record["coverage"]
                self.assertEqual(coverage["component_source_values"], coverage["accounted_component_values"])

    def test_text_entities_spaces_unicode_and_marks_roundtrip(self):
        record = self.record({"rich_text": '<p>A  B&amp;Ｃ😀<strong>不要</strong><sup>2</sup><s>旧</s><br>下一行</p>'})
        text = next(t for t in self.texts(record) if t.startswith("A  B"))
        self.assertEqual(text, "A  B&Ｃ😀不要2旧\n下一行")
        paragraph = next(b for b in iter_blocks(record) if b.get("value", {}).get("text", "").startswith("A  B"))
        self.assertTrue({"strong", "superscript", "strikethrough"}.issubset({m["kind"] for m in paragraph["value"]["marks"]}))

    def test_numeric_lexemes_empty_null_missing_and_zero_are_distinct(self):
        record = self.record('{"n":-0,"ratio":1.2300e+2,"zero":0,"empty":"","null":null,"arr":[],"obj":{}}', raw_data=True)
        texts = self.texts(record)
        for value in ("-0", "1.2300e+2", "0", "", "null"):
            self.assertIn(value, texts)
        self.assertEqual(record["coverage"]["component_source_values"], 7)

    def test_unknown_component_and_literal_placeholder_are_preserved(self):
        record = self.record({"unknown": "<Traveler>1 < 2</Traveler>", "new": {"x": ["证据"]}}, "new_component")
        self.assertIn("<Traveler>1 < 2</Traveler>", self.texts(record))
        self.assertEqual(record["coverage"]["status"], "preserved_unknown")
        self.assertIn("证据", self.texts(record))

    def test_unknown_attributes_comments_styles_and_hidden_content_survive(self):
        record = self.record({"rich_text": '<details><summary>标题</summary><p style="display:none;color:red" data-new="实际内容">隐藏文字</p><!--注释--></details>'})
        self.assertIn("隐藏文字", self.texts(record))
        extensions = record["extensions"]
        self.assertTrue(any(x.get("name") == "data-new" and x["value"] == "实际内容" for x in extensions))
        self.assertTrue(any(x.get("tag") == "#comment" for x in extensions))

    def test_html_table_retains_spans_and_nested_content(self):
        record = self.record({"rich_text": '<table><tr><th rowspan="2">项目</th><th>值</th></tr><tr><td><p>5.0%</p><ul><li>材料</li></ul></td></tr></table>'})
        table = next(b for b in iter_blocks(record) if b["kind"] == "table")
        self.assertEqual(table["rows"][0]["cells"][0]["rowspan"], "2")
        self.assertEqual(table["rows"][1]["cells"][0]["column_index"], 1)
        self.assertIn("5.0%", self.texts(record))

    def test_json_table_empty_header_single_cell_and_filter_are_preserved(self):
        record = self.record({"table": {"header": [""], "row": [["<p>完整叙述</p><hr><p>50（27无武器）</p>"]],
                                         "filter": [[["类型", "第一类"]]]}}, "filter_table")
        table = next(b for b in iter_blocks(record) if b["kind"] == "table")
        self.assertEqual((len(table["header"]), len(table["rows"]), len(table["rows"][0]["cells"])), (1, 1, 1))
        self.assertIn("第一类", self.texts(record))

    def test_media_entry_amount_and_invalid_href_keep_ownership(self):
        record = self.record({"rich_text": '<p><span data-entry-id="853" data-entry-amount="10" data-entry-name="材料" data-entry-link="无"><a href="%E6%97%A0">材料</a></span><audio><source src="a.mp3"></audio><video src="v.mp4" poster="p.png"></video><img src="i.png" alt="文字说明"></p>'})
        self.assertTrue(any(x["kind"] == "audio" and x["url"] == "a.mp3" for x in record["media"]))
        self.assertTrue(any(x["kind"] == "video" for x in record["media"]))
        relation = next(x for x in record["relations"] if x.get("target_content_id") == "853")
        self.assertIn({"name": "data-entry-amount", "value": "10", "origin_id": next(x["origin_id"] for x in relation["source_attributes"] if x["name"] == "data-entry-amount")}, relation["source_attributes"])
        self.assertEqual(relation["href"], "无")

    def test_prose_urls_remain_text(self):
        record = self.record({"rich_text": "文字 https://example.invalid/path"})
        self.assertIn("文字 https://example.invalid/path", self.texts(record))

    def test_absolute_href_preserves_link_without_guessing_content_identity(self):
        record = self.record({"rich_text": '<p><a href="https://example.invalid/ys/obc/content/853/detail">外链</a><a href="/ys/obc/content/853/detail?view=1">条目</a></p>'})
        self.assertIsNone(record["relations"][0]["target_content_id"])
        self.assertEqual(record["relations"][1]["target_content_id"], "853")

    def test_empty_resource_slots_preserve_empty_values(self):
        record = self.record({"img": "", "avatar_pc": "", "link": ""}, "role_base_info")
        self.assertEqual(record["media"], [])
        self.assertEqual(record["relations"], [])
        self.assertGreaterEqual(self.texts(record).count(""), 3)

    def test_top_and_listed_dialogues_both_survive(self):
        group = {"root_id": "r", "contents": {"r": {"option": "选项", "dialogue": "<p>甲：对白</p>", "icon": ""}}, "child_ids": {"r": []}}
        record = self.record({**group, "list": [group]}, "interactive_dialogue")
        self.assertEqual(record["coverage"]["dialogue_groups"], 2)
        self.assertEqual(record["coverage"]["dialogue_nodes"], 2)
        self.assertEqual(self.texts(record).count("甲：对白"), 2)

    def test_dialogue_multiple_parents_cycles_and_dangling_edges_survive(self):
        group = {"root_id": "r", "contents": {n: {"option": n, "dialogue": "", "icon": ""} for n in ("r", "a", "b", "c")},
                 "child_ids": {"r": ["a", "b"], "a": ["c"], "b": ["c"], "c": ["r", "missing"]}}
        record = self.record(group, "interactive_dialogue")
        self.assertEqual(record["coverage"]["dialogue_edges"], 6)
        codes = {d["code"] for d in record["diagnostics"]}
        self.assertTrue({"DIALOGUE_MULTIPLE_PARENT", "DIALOGUE_CYCLE", "DIALOGUE_DANGLING_EDGE"}.issubset(codes))
        dialogue = next(b for b in iter_blocks(record) if b["kind"] == "dialogue")
        self.assertTrue(all(n["speaker"] is None for n in dialogue["groups"][0]["nodes"]))

    def test_empty_component_and_malformed_json_have_explicit_results(self):
        for raw in ("", "{broken"):
            with self.subTest(raw=raw):
                record = self.record(raw, "interactive_dialogue", raw_data=True)
                opaque = next(b for b in iter_blocks(record) if b["kind"] == "opaque")
                self.assertEqual(opaque["value"], raw)
                self.assertEqual(record["coverage"]["disposition_opaque"], 1)

    def test_source_occurrences_are_not_text_deduplicated(self):
        record = self.record({"first": "同一句话", "second": "同一句话"})
        self.assertEqual(self.texts(record).count("同一句话"), 2)

    def test_field_selectors_inherit_source_labels(self):
        record = self.record({"attr": [{"key": "攻击力", "value": ["50（27无武器）"]},
                                       {"key": "暴击率", "value": ["5.0%"]}]}, "role_base_info")
        selected = self.texts(record, BlockSelector(field_labels=("攻击力",), component_ids=("role_base_info",)))
        self.assertIn("50（27无武器）", selected)
        self.assertNotIn("5.0%", selected)

    def test_unicode_clean_ref_and_segments(self):
        record = self.record({"rich_text": "<p>甲😀<strong>乙</strong>丙</p>"})
        block = next(b for b in iter_blocks(record) if b.get("value", {}).get("text") == "甲😀乙丙")
        ref = CleanRef(record["observation_id"], block["block_id"], "/value", 1, 3)
        resolved = resolve_clean_ref(ref, record=record, raw_run_root=self.root)
        self.assertEqual(resolved["text"], "😀乙")
        self.assertEqual(len(resolved["origins"]), 2)
        with self.assertRaises(ValueError):
            resolve_clean_ref(CleanRef("wrong", block["block_id"], "/value", 0, 1), record=record)

    def test_invalid_text_segment_and_missing_origin_are_rejected(self):
        record = self.record({"rich_text": "<p>原文</p>"})
        value = next(b["value"] for b in iter_blocks(record) if b.get("value", {}).get("text") == "原文")
        value["text"] = "改文"
        with self.assertRaises(ValueError):
            validate_record(record, self.root)

    def test_parser_keeps_plain_comparison(self):
        record = self.record({"rich_text": "1 < 2 > 0"})
        self.assertIn("1 < 2 > 0", self.texts(record))

    def test_deterministic_build_resume_tags_and_listing_identity(self):
        raw = self.raw_run()
        with patch("socket.socket", side_effect=AssertionError("No network")):
            first = self.build(raw)
        self.assertEqual(first["status"], "complete")
        with patch("genshin_corpus.clean.pipeline.clean_obc_detail", side_effect=AssertionError("Must reuse")):
            self.assertEqual(self.build(raw), first)
        records = list(iter_records(self.manifest_path()))
        record = records[0]
        self.assertEqual(record["source_item"]["locale"], "fixture-locale")
        self.assertEqual(record["document"]["listing_observations"][0]["channel_id"], "43")
        self.assertEqual(record["document"]["listing_observations"][0]["requested_channel_id"], "189")
        tag = next(t for t in record["document"]["tags"] if t["namespace"] == "source_filter" and t["key"] == "任务类型")
        self.assertEqual(tag["value"], "世界任务")
        self.assertEqual(len(tag["evidence"]), 2)
        slash = next(t for t in record["document"]["tags"] if t["key"] == "获取方式")
        self.assertEqual(slash["value"], "杂货店/NPC购买或兑换")
        self.assertEqual(record["document"]["provenance"]["state"], "unknown")

    def test_partial_manifest_and_record_are_recovered(self):
        raw = self.raw_run()
        first = self.build(raw)
        manifest = self.manifest_path()
        progress = {**first, "status": "partial", "records": first["records"][:1]}
        manifest.write_bytes(encoded(progress))
        resumed = self.build(raw)
        self.assertEqual(resumed["status"], "complete")
        self.assertTrue(all(e["reused"] for e in resumed["records"]))

    def test_prior_same_raw_run_is_reused_and_new_run_rebinds(self):
        raw = self.raw_run()
        first = self.build(raw)
        with patch("genshin_corpus.clean.pipeline.clean_obc_detail", side_effect=AssertionError("Must reuse")):
            reused = self.build(raw, "second", reuse_manifest=self.manifest_path())
        self.assertTrue(all(e["reused"] for e in reused["records"]))
        newer = self.raw_run(run_id="raw-two")
        result = self.build(newer, "third", reuse_manifest=self.manifest_path())
        self.assertNotEqual(first["records"][0]["observation_id"], result["records"][0]["observation_id"])

    def test_record_corruption_prevents_completed_reuse(self):
        raw = self.raw_run()
        result = self.build(raw)
        path = self.manifest_path().parent.parent / result["records"][0]["record_path"]
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaises(ValueError):
            self.build(raw)
        with self.assertRaises(ValueError):
            list(iter_records(self.manifest_path()))

    def test_raw_corruption_is_accounted_and_completed_run_stays_immutable(self):
        raw = self.raw_run()
        first = self.build(raw)
        original_manifest = self.manifest_path().read_bytes()
        detail = raw / "responses/details/one.json"
        detail.write_bytes(detail.read_bytes() + b" ")
        with self.assertRaises(ValueError):
            self.build(raw)
        self.assertEqual(self.manifest_path().read_bytes(), original_manifest)
        blocked = self.build(raw, "blocked")
        self.assertEqual(blocked["status"], "complete_with_errors")
        self.assertEqual(blocked["counts"]["blocked_integrity"], 1)
        with self.assertRaises(ValueError):
            list(iter_records(self.manifest_path("blocked")))
        self.assertEqual(len(list(iter_records(self.manifest_path("blocked"), allow_partial=True))), 1)

    def test_changed_selection_and_undiscovered_id_rejected(self):
        raw = self.raw_run()
        self.build(raw)
        with self.assertRaises(FileExistsError):
            self.build(raw, content_ids=["one"])
        with self.assertRaises(ValueError):
            self.build(raw, "other", content_ids=["unobserved"])

    def test_kernel_lock_blocks_second_writer(self):
        raw = self.raw_run()
        with locked(self.manifest_path().parent.parent / "build.lock"):
            with self.assertRaises(OSError):
                self.build(raw)

    def test_output_cannot_write_into_raw_input(self):
        raw = self.raw_run()
        with self.assertRaises(ValueError):
            build_clean_run(raw_run_root=raw, clean_run_id="nested", output_root=raw)
        self.assertFalse((raw / "mihoyo_obc").exists())

    def test_identity_hash_and_source_reference_fail_closed(self):
        body = encoded({"data": {"page": {"id": "wrong", "modules": []}}})
        with self.assertRaises(ValueError):
            clean_obc_detail(body, artifact_path="a.json", artifact_sha256=hashlib.sha256(body).hexdigest(),
                             locale="x", raw_run_id="r", content_id="expected")
        record = self.record({"x": "text"})
        (self.root / "detail.json").write_bytes(b"{}")
        with self.assertRaises(ValueError):
            OriginResolver(record, self.root).resolve(record["document"]["title"]["origin_id"])

    def test_renderer_and_cli_exports(self):
        raw = self.raw_run()
        self.build(raw)
        record = next(iter_records(self.manifest_path()))
        self.assertIn("完整正文😀", render_record(record))
        self.assertIn("完整正文😀", render_record(record, format="markdown"))
        with patch("builtins.print") as output:
            self.assertEqual(main(["export", "--manifest", str(self.manifest_path()), "--format", "jsonl", "--content-id", "one"]), 0)
        self.assertEqual(len(output.call_args_list), 1)

    def test_independent_run_validation_and_cli(self):
        raw = self.raw_run()
        self.build(raw)
        from genshin_corpus.clean.validation import validate_clean_run
        report = validate_clean_run(self.manifest_path(), raw_run_root=raw)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["records"], 2)
        self.assertEqual(report["counts"]["meaningful_html_text_nodes"], 2)
        with patch("builtins.print"):
            self.assertEqual(main(["validate", "--manifest", str(self.manifest_path()), "--raw-run-root", str(raw)]), 0)

    def test_group_titles_are_available_to_field_consumers(self):
        record = self.record({"list": [{"tab_name": "1级属性/突破所需材料", "attr": [
            {"key": "暴击率", "value": ["5.0%"]}]}]}, "role_ascension")
        block = next(b for b in iter_blocks(record, BlockSelector(field_labels=("暴击率",)))
                     if any(t["text"] == "5.0%" for t in iter_text_values(b)))
        self.assertIn("1级属性/突破所需材料", block["context"]["group_titles"])

    def test_links_are_references_and_css_emphasis_is_structured(self):
        record = self.record({"more_link": "https://example.invalid/guide", "rich_text": '<p><span style="font-weight:bold;text-decoration:line-through">原文</span></p>'})
        self.assertNotIn("https://example.invalid/guide", self.texts(record))
        self.assertTrue(any(r.get("href") == "https://example.invalid/guide" for r in record["relations"]))
        paragraph = next(b for b in iter_blocks(record) if b.get("value", {}).get("text") == "原文")
        self.assertTrue({"strong", "strikethrough"}.issubset({m["kind"] for m in paragraph["value"]["marks"]}))

    def test_unknown_field_shapes_remain_visible_with_diagnostics(self):
        record = self.record({"new_field": ["保留的新资料"], "rich_text": {"changed_shape": "资料"}})
        self.assertIn("保留的新资料", self.texts(record))
        self.assertEqual(record["coverage"]["status"], "preserved_unknown")
        self.assertTrue(any(d["code"] == "UNKNOWN_COMPONENT_FIELD_SHAPE" for d in record["diagnostics"]))

    def test_missing_component_data_and_nonarray_components_are_preserved(self):
        record = self.record({"rich_text": "正文"}, extra_modules=[
            {"id": "m2", "components": [{"component_id": "new", "unfamiliar": "保留"}]},
            {"id": "m3", "components": {"unexpected": "结构"}},
        ])
        self.assertIn("保留", self.texts(record))
        self.assertIn("结构", self.texts(record))
        self.assertEqual(record["coverage"]["status"], "preserved_unknown")

    def test_interrupted_write_resumes_with_unreceipted_record_verification(self):
        raw = self.raw_run()
        from genshin_corpus.clean.pipeline import atomic_write
        calls = 0
        def interrupt_after_record(path, body):
            nonlocal calls
            atomic_write(path, body)
            if Path(path).parent.name == "records":
                calls += 1
                if calls == 2:
                    raise KeyboardInterrupt("Fixture process interruption")
        with patch("genshin_corpus.clean.pipeline.atomic_write", side_effect=interrupt_after_record):
            with self.assertRaises(KeyboardInterrupt):
                self.build(raw)
        self.assertEqual(json.loads(self.manifest_path().read_bytes())["status"], "partial")
        self.assertEqual(self.build(raw)["status"], "complete")

    def test_conflicting_unreceipted_record_is_accounted(self):
        raw = self.raw_run()
        result = self.build(raw)
        manifest = self.manifest_path()
        progress = {**result, "status": "partial", "records": [], "accounted_detail_count": 0}
        manifest.write_bytes(encoded(progress))
        record_path = manifest.parent.parent / result["records"][0]["record_path"]
        record = json.loads(record_path.read_bytes())
        record["diagnostics"].append({"code": "INJECTED"})
        record_path.write_bytes(encoded(record))
        resumed = self.build(raw)
        self.assertEqual(resumed["counts"]["blocked_integrity"], 1)

    def test_changed_listing_rebuilds_bound_observations(self):
        raw = self.raw_run()
        first = self.build(raw)
        newer = self.raw_run(filters=["任务类型/魔神任务"])
        with self.assertRaises(FileExistsError):
            self.build(newer)
        second = self.build(newer, "changed", reuse_manifest=self.manifest_path())
        self.assertNotEqual(first["records"][0]["observation_id"], second["records"][0]["observation_id"])

    def test_reader_validates_manifest_coverage_and_reference_ownership(self):
        raw = self.raw_run()
        result = self.build(raw)
        record = next(iter_records(self.manifest_path()))
        block = next(b for b in iter_blocks(record) if "value" in b and isinstance(b["value"], dict) and "text" in b["value"])
        with self.assertRaises(ValueError):
            resolve_clean_ref(CleanRef(record["observation_id"], block["block_id"], "/context/document_title", 0, 1), record=record)
        changed = {**result, "records": result["records"][:1]}
        self.manifest_path().write_bytes(encoded(changed))
        with self.assertRaises(ValueError):
            list(iter_records(self.manifest_path()))

    def test_html_block_wrappers_keep_nested_tables(self):
        record = self.record({"rich_text": '<span><div><table><tr><td>单元格</td></tr></table></div></span>'})
        self.assertTrue(any(b["kind"] == "table" for b in iter_blocks(record)))
        self.assertIn("单元格", self.texts(record))

    def test_actual_task_and_source_filters_can_conflict_without_overwrite(self):
        raw = self.raw_run(filters=["任务类型/世界任务", "任务类型/魔神任务"])
        self.build(raw)
        record = next(iter_records(self.manifest_path()))
        tags = [t for t in record["document"]["tags"] if t["namespace"] == "source_filter" and t["key"] == "任务类型"]
        self.assertEqual({t["value"] for t in tags}, {"世界任务", "魔神任务"})
        self.assertTrue(any(d["code"] == "FILTER_MULTIPLE_VALUES" for d in record["diagnostics"]))


if __name__ == "__main__":
    unittest.main()
