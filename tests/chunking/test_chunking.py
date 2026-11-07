"""Small synthetic source fixtures for the specific Clean chunking regressions."""
from copy import deepcopy
import hashlib
import json
import unittest

from genshin_corpus.clean import CleanRef, clean_obc_detail, resolve_clean_ref, iter_blocks
from genshin_corpus.chunking import ChunkProfile, adapt_record, chunk_parent, read_natural_parent
from genshin_corpus.chunking.core import retrieval_text
from genshin_corpus.chunking.structure import graph_order


def clean(data, component="collapse_panel", title="原始章节", *, channel=None):
    body = json.dumps({"retcode": 0, "data": {"page": {"id": "one", "name": "原始标题",
        "desc": "原始标题", "modules": [{"name": title, "components": [{
            "component_id": component, "data": json.dumps(data, ensure_ascii=False)}]}]}}},
        ensure_ascii=False).encode()
    discovery = None
    if channel:
        channel_body = json.dumps({"membership": 1, "channel": {"id": 1, "name": channel, "parent_id": 0}},
                                  ensure_ascii=False).encode()
        discovery = {"artifacts": {"discovery": {"body": channel_body, "path": "channel.json",
                      "sha256": hashlib.sha256(channel_body).hexdigest(), "kind": "map"}},
                     "memberships": [{"channel_id": "1", "artifact_name": "discovery", "pointer": "/membership"}],
                     "channel_nodes": {"1": {"name": channel, "parent_id": "0", "artifact_name": "discovery", "pointer": "/channel"}}}
    return clean_obc_detail(body, artifact_path="fixture.json", artifact_sha256=hashlib.sha256(body).hexdigest(),
                            locale="fixture-locale", raw_run_id="fixture-run", content_id="one", discovery=discovery)


class ChunkingTests(unittest.TestCase):
    def chunks(self, record, profile=ChunkProfile("fixture", 45, 75, 100, 140)):
        adapted = adapt_record(record)
        return adapted, [chunk for parent in adapted["parents"] for chunk in chunk_parent(parent, len, profile)]

    def test_short_natural_parent_has_one_dual_granularity_candidate(self):
        _, chunks = self.chunks(clean({"rich_text": "<p>甲😀乙。</p>"}))
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["granularities"], ["micro", "macro"])

    def test_long_paragraph_source_ranges_cover_unicode_exactly(self):
        text = "原句😀完整。" * 100
        record = clean({"rich_text": "<p>" + text + "</p>"})
        _, chunks = self.chunks(record)
        for level in ("micro", "macro"):
            parts = [part for chunk in chunks if level in chunk["granularities"] for part in chunk["parts"]]
            self.assertEqual("".join(part["text"] for part in parts), text)
            for part in parts:
                self.assertEqual(resolve_clean_ref(CleanRef(**part["clean_ref"]), record=record)["text"], part["text"])

    def test_no_cross_section_merge_and_preserve_headings(self):
        adapted, chunks = self.chunks(clean({"rich_text": "<h2>第一节</h2><p>第一段。</p><h2>第二节</h2><p>第二段。</p>"}))
        self.assertEqual(len([p for p in adapted["parents"] if p["units"]]), 2)
        self.assertFalse(any("第一段" in c["body_text"] and "第二段" in c["body_text"] for c in chunks))

    def test_mechanics_excluded_but_talent_desc_retained(self):
        record = clean({"list": [{"tab_name": "天赋", "title": "招式名", "desc": "<p>满月的传说。造成50%伤害。</p>",
                                  "attr": [{"key": "攻击力", "value": ["500"]}]}]}, "role_talent")
        adapted, chunks = self.chunks(record)
        self.assertIn("满月的传说", "".join(c["body_text"] for c in chunks))
        self.assertNotIn("500", "".join(c["body_text"] for c in chunks))
        self.assertTrue(any(d["decision"] == "exclude" for d in adapted["decisions"]))

    def test_unknown_field_kept_and_source_unchanged(self):
        record = clean({"future_field": "未知来源的故事。"})
        before = deepcopy(record)
        _, chunks = self.chunks(record)
        self.assertIn("未知来源的故事", chunks[0]["body_text"])
        self.assertEqual(before, record)

    def test_graph_uses_edges_with_root_not_first_and_join_once(self):
        data = {"root_id": "r", "contents": {n: {"option": n, "dialogue": "<p>" + n + "台词</p>", "icon": ""}
                 for n in ("a", "r", "b", "join")},
                "child_ids": {"r": ["a", "b"], "a": ["join"], "b": ["join"]}}
        record = clean(data, "interactive_dialogue")
        adapted = adapt_record(record)
        self.assertEqual(adapted["graphs"][0]["display_order"], ["r", "a", "b", "join"])
        parent = next(p for p in adapted["parents"] if p["kind"] == "dialogue_group")
        self.assertEqual(sum(part["text"] == "join台词" for unit in parent["units"] for part in unit["parts"]), 1)
        self.assertIn("源图汇合", retrieval_text(parent, [part for unit in parent["units"] for part in unit["parts"]]))
        self.assertEqual(adapted["graphs"][0]["group"]["edges"], next(
            b for b in iter_blocks(record)
            if b["kind"] == "dialogue")["groups"][0]["edges"])

    def test_completion_conditions_propagate_locally_then_clear_at_join(self):
        data = {"root_id": "r", "contents": {n: {"option": "", "dialogue": "<p>正文</p>", "icon": ""}
                 for n in ("r", "a", "b", "aa", "bb", "j")},
                "child_ids": {"r": ["a", "b"], "a": ["aa"], "b": ["bb"], "aa": ["j"], "bb": ["j"]}}
        data["contents"]["a"]["option"] = "完成前置任务后触发"
        data["contents"]["b"]["option"] = "未完成前置任务后触发"
        adapted = adapt_record(clean(data, "interactive_dialogue"))
        parts = [part for p in adapted["parents"] for u in p["units"] for part in u["parts"]]
        self.assertTrue(next(p for p in parts if p["node_id"] == "aa")["condition_sources"])
        self.assertFalse(next(p for p in parts if p["node_id"] == "j")["condition_sources"])
        self.assertIn("未完成", next(p for p in parts if p["node_id"] == "bb")["condition_sources"][0]["text"])

    def test_missing_root_cycle_dangling_do_not_drop_nodes(self):
        group = {"root_id": "missing", "nodes": [{"source_id": "a"}, {"source_id": "b"}],
                 "edges": [{"parent_id": "a", "child_id": "b"}, {"parent_id": "b", "child_id": "a"},
                           {"parent_id": "a", "child_id": "missing"}]}
        order, _, _, issues = graph_order(group)
        self.assertEqual(set(order), {"a", "b"})
        self.assertEqual(set(issues), {"missing_root", "cycle_unsequenced", "dangling_edge"})

    def test_macro_and_micro_independent_with_overlap_links(self):
        _, chunks = self.chunks(clean({"rich_text": "".join(f"<p>第{i}个完整段落。包含故事。</p>" for i in range(15))}))
        self.assertTrue(any(c["granularities"] == ["micro"] for c in chunks))
        self.assertTrue(any(c["granularities"] == ["macro"] for c in chunks))
        self.assertTrue(any(c["overlapping_chunk_ids"] for c in chunks))

    def test_secondary_token_budget_and_impossible_context(self):
        adapted = adapt_record(clean({"rich_text": "<p>长文字。" + "甲" * 200 + "</p>"}))
        parent = next(p for p in adapted["parents"] if p["units"])
        chunks = chunk_parent(parent, len, ChunkProfile("fixture", 30, 60, 100, 140),
                              secondary_fits=lambda text: len(text) <= 50)
        self.assertTrue(all(len(c["retrieval_text"]) <= 50 for c in chunks))
        with self.assertRaises(ValueError):
            chunk_parent(parent, len, secondary_fits=lambda text: False)

    def test_field_labels_and_table_columns_are_preserved(self):
        _, chunks = self.chunks(clean({"rich_text": "<table><tr><th>名称</th><th>记载</th></tr>"
            "<tr><td>古老日记</td><td>完整故事。</td></tr></table>"}))
        parts = [part for c in chunks for part in c["parts"]]
        self.assertTrue(any(part.get("column_label") == "记载" for part in parts))

    def test_profile_validation(self):
        with self.assertRaises(ValueError):
            ChunkProfile("bad", 10, 100, 90, 120)

    def test_voice_exact_aliases_keep_all_source_occurrences(self):
        data = {"list": [{"tab_name": language, "table": [{"name": "初次见面", "content": "相同原文。", "audio_name": language + ".mp3"}]}
                         for language in ("汉语", "日语")]}
        record = clean(data, "role_voice")
        adapted = adapt_record(record)
        parents = [p for p in adapted["parents"] if p["units"]]
        self.assertEqual(len(parents), 2)
        self.assertEqual(parents[1]["exact_alias_of_parent_id"], parents[0]["parent_id"])
        chunks = chunk_parent(parents[0], len)
        self.assertEqual(len(chunks[0]["alias_clean_refs"]), 1)
        self.assertNotIn(".mp3", chunks[0]["retrieval_text"])
        self.assertEqual(resolve_clean_ref(CleanRef(**chunks[0]["alias_clean_refs"][0]), record=record)["text"], "相同原文。")

    def test_full_natural_parent_includes_excluded_fields(self):
        record = clean({"attr": [{"key": "攻击力", "value": ["500"]},
                                 {"key": "所属", "value": ["原文组织"]}]}, "role_base_info")
        adapted = adapt_record(record)
        parent = next(p for p in adapted["parents"] if p["kind"] == "section")
        self.assertIn("500", json.dumps(read_natural_parent(parent, record), ensure_ascii=False))
        with self.assertRaises(ValueError):
            read_natural_parent({**parent, "observation_id": "wrong"}, record)

    def test_unknown_scalar_is_not_assumed_to_be_a_game_statistic(self):
        _, chunks = self.chunks(clean({"year": 1900}))
        self.assertIn("1900", chunks[0]["body_text"])

    def test_distinct_explicit_conditions_do_not_share_a_candidate(self):
        record = clean({"root_id": "r", "contents": {
            "r": {"option": "", "dialogue": "<p>共同提问。</p>", "icon": ""},
            "a": {"option": "完成前置任务后触发", "dialogue": "<p>已完成台词。</p>", "icon": ""},
            "b": {"option": "未完成前置任务后触发", "dialogue": "<p>未完成台词。</p>", "icon": ""}},
            "child_ids": {"r": ["a", "b"]}}, "interactive_dialogue")
        adapted = adapt_record(record)
        chunks = [chunk for parent in adapted["parents"] for chunk in chunk_parent(parent, len)]
        self.assertFalse(any("已完成台词" in chunk["body_text"] and "未完成台词" in chunk["body_text"] for chunk in chunks))

    def test_repeated_build_is_deterministic(self):
        record = clean({"rich_text": "<p>同一原文。</p><p>第二段。</p>"})
        self.assertEqual(self.chunks(record), self.chunks(record))

    def test_achievement_fold_excludes_only_explicit_puzzle_container(self):
        record = clean({"rich_text": "<p>成就背后的历史。</p>"
            "<details><summary>· 解谜流程可以点击此处折叠栏查看</summary><p>移动晶体并开启宝箱。</p></details>"
            "<details><summary>相关故事</summary><p>故人的约定。</p></details>"}, title="补充说明", channel="成就")
        before = deepcopy(record)
        adapted, chunks = self.chunks(record)
        text = "".join(c["body_text"] for c in chunks)
        self.assertNotIn("移动晶体", text)
        self.assertIn("历史", text)
        self.assertIn("故人的约定", text)
        self.assertTrue(any(d["reason"] == "achievement_puzzle_fold" for d in adapted["decisions"]))
        parent = next(p for p in adapted["parents"] if p["kind"] == "section")
        self.assertIn("移动晶体", json.dumps(read_natural_parent(parent, record), ensure_ascii=False))
        self.assertEqual(record, before)

    def test_explicit_story_subheading_keeps_mixed_puzzle_fold(self):
        for content in ("<h3>相关故事</h3><p>古国往事。</p>",
                        "<h3>未知用途的小节</h3><p>古国往事。</p>",
                        "<details><summary>故事</summary><p>古国往事。</p></details>"):
            with self.subTest(content=content):
                _, chunks = self.chunks(clean({"rich_text": "<details><summary>解谜流程</summary>"
                    "<p>操作说明。</p>" + content + "</details>"}, channel="成就"))
                self.assertIn("古国往事", "".join(c["body_text"] for c in chunks))

    def test_ambiguous_fold_and_nonachievement_text_remain_admitted(self):
        for channel, summary in (("成就", "解谜流程与相关故事"), ("书籍", "解谜流程")):
            with self.subTest(channel=channel):
                _, chunks = self.chunks(clean({"rich_text": f"<details><summary>{summary}</summary>"
                    "<p>包含传说的混合正文。</p></details>"}, channel=channel))
                self.assertIn("传说", "".join(c["body_text"] for c in chunks))

    def test_outfit_fields_use_channel_scope_and_exact_control_values(self):
        data = {"attr": [{"key": key, "value": [text]} for key, text in (
            (" 部件数量：\t", "9"), ("适用性别", "女"), ("色系", "红"),
            ("部件/色系", "鞋/红"), ("特殊", "部件可调整"),
            ("特殊", "这件衣装来自古国，部件可调整。"), ("描述", "染色工艺与古国往事。"))]}
        record = clean(data, "material_base_info", channel="千星奇域")
        adapted, chunks = self.chunks(record)
        self.assertFalse(any(p["text"] == "9" for c in chunks for p in c["parts"]))
        self.assertFalse(any(p["text"] == "部件可调整" for c in chunks for p in c["parts"]))
        self.assertIn("这件衣装来自古国", "".join(c["body_text"] for c in chunks))
        self.assertIn("染色工艺", "".join(c["body_text"] for c in chunks))
        _, other = self.chunks(clean(data, "material_base_info", channel="书籍"))
        self.assertTrue(any(p["text"] == "9" for c in other for p in c["parts"]))

    def test_furnishing_labels_ignore_final_colon_and_preserve_craft_prose(self):
        data = {"rich_text": "<p>以榫卯工艺制作，经历战乱的守卫曾持此盾。</p>",
                "attr": [{"key": "摆设获取途径：", "value": ["制作"]},
                         {"key": "摆设图纸获取途径:\t", "value": ["宝箱奖励"]}]}
        _, chunks = self.chunks(clean(data, "good_desc", channel="洞天"))
        self.assertNotIn("宝箱奖励", "".join(c["body_text"] for c in chunks))
        self.assertIn("榫卯工艺制作", "".join(c["body_text"] for c in chunks))
        _, attrs = self.chunks(clean({"list": [{"key": "洞天仙力\t", "value": ["60"]},
            {"key": "相关历史", "value": ["古国之盾"]}]}, "rich_base_info", channel="洞天"))
        self.assertFalse(any(p["text"] == "60" for c in attrs for p in c["parts"]))
        self.assertIn("古国之盾", "".join(c["body_text"] for c in attrs))

    def test_formatted_articles_preserve_both_granularities_and_tail_boundary(self):
        record = clean({"root_id": "r", "contents": {"r": {"option": "种类七", "dialogue":
            '<h1>报纸</h1><p style="text-align: center">《甲文》</p>'
            '<p>甲文完整正文。</p><p style="text-align:center;">《乙文》</p><p>乙文末段。</p>',
            "icon": ""}}, "child_ids": {}}, "interactive_dialogue")
        before = deepcopy(record)
        adapted, chunks = self.chunks(record)
        for chunk in chunks:
            self.assertLessEqual(len(chunk["article_scope_ids"]), 1)
            self.assertFalse("甲文" in chunk["body_text"] and "乙文" in chunk["body_text"])
        for level in ("micro", "macro"):
            parts = [p for c in chunks if level in c["granularities"] for p in c["parts"]]
            self.assertEqual(sum(p["text"] == "乙文末段。" for p in parts), 1)
            for part in parts:
                self.assertEqual(resolve_clean_ref(CleanRef(**part["clean_ref"]), record=record)["text"], part["text"])
        self.assertEqual(before, record)

    def test_article_continuation_repeats_source_title_as_context(self):
        record = clean({"rich_text": '<p style="text-align:center">《甲文》</p>'
            + "<p>甲文的一段记载。</p>" * 20
            + '<p style="text-align:center">《乙文》</p><p>乙文末段。</p>'})
        _, chunks = self.chunks(record)
        continuations = [c for c in chunks if c["parts"][0].get("article_title")
                         and c["parts"][0]["clean_ref"]["block_id"] != c["parts"][0]["article_title"]["clean_ref"]["block_id"]]
        self.assertTrue(continuations)
        for chunk in continuations:
            self.assertIn("【文章标题】", chunk["retrieval_text"])
            title = chunk["parts"][0]["article_title"]
            self.assertEqual(resolve_clean_ref(CleanRef(**title["clean_ref"]), record=record)["text"], title["text"])

    def test_unformatted_book_brackets_and_single_centered_title_are_not_articles(self):
        for rich_text in ('<p>《甲文》</p><p>正文。</p><p>《乙文》</p>',
                          '<p style="text-align:center">《甲文》</p><p>正文提到《乙文》。</p>'):
            with self.subTest(rich_text=rich_text):
                _, chunks = self.chunks(clean({"rich_text": rich_text}))
                self.assertFalse(any(c["article_scope_ids"] for c in chunks))

    def test_source_heading_ends_a_preceding_article_scope(self):
        _, chunks = self.chunks(clean({"rich_text": '<p style="text-align:center">《甲文》</p><p>甲文正文。</p>'
            '<p style="text-align:center">《乙文》</p><p>乙文正文。</p><h2>下一章</h2><p>新章正文。</p>'}))
        later = [c for c in chunks if "新章正文" in c["body_text"]]
        self.assertTrue(later)
        self.assertTrue(all(not c["article_scope_ids"] for c in later))


if __name__ == "__main__":
    unittest.main()
