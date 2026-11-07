"""Small source fixtures for V2 admission, source conditions and display semantics."""
from copy import deepcopy
import os
import subprocess
import sys
import unittest

from genshin_corpus.clean import CleanRef, iter_blocks, resolve_clean_ref
from genshin_corpus.chunking import adapt_record, chunk_parent, read_natural_parent
from genshin_corpus.chunking.core import ChunkProfile, retrieval_text
from tests.chunking.test_chunking import clean


def graph_record(options, edges, *, root="r", dialogue=None):
    return clean({"root_id": root, "contents": {key: {"option": option,
        "dialogue": (dialogue or {}).get(key, "<p>" + key + "的原文。</p>"), "icon": ""}
        for key, option in options.items()}, "child_ids": edges}, "interactive_dialogue")


def candidates(record, profile=ChunkProfile()):
    adapted = adapt_record(record)
    return adapted, [c for p in adapted["parents"] for c in chunk_parent(p, len, profile)]


class V2Tests(unittest.TestCase):
    def test_null_type_and_control_paths_do_not_delete_literal_words_or_years(self):
        adapted, chunks = candidates(clean({"empty": None, "year": 1900, "word": "null"}))
        parts = [p for c in chunks for p in c["parts"]]
        self.assertTrue(any(p["text"] == "1900" for p in parts))
        self.assertTrue(any(p["text"] == "null" and p["source_kind"] == "paragraph" for p in parts))
        self.assertTrue(any(d["reason"] == "source_null_placeholder" for d in adapted["decisions"]))

    def test_cave_quality_exclusion_is_scoped(self):
        _, chunks = candidates(clean({"rate": 4, "desc": "工匠留下的古国记载。"}, "cave_base_info", channel="洞天"))
        self.assertFalse(any(p["text"] == "4" for c in chunks for p in c["parts"]))
        self.assertIn("古国记载", "".join(c["body_text"] for c in chunks))
        _, other = candidates(clean({"rate": 4}))
        self.assertTrue(any(p["text"] == "4" for c in other for p in c["parts"]))

    def test_filter_controls_removed_but_story_rows_and_source_parent_preserved(self):
        record = clean({"options": [{"key": "地区", "value": ["筛选选项"]}],
            "table": {"header": ["名称", "描述"], "row": [["旧城", "古国的记载。"]],
                      "filter": [{"key": "地区", "value": ["筛选镜像"]}]}}, "filter_table")
        adapted, chunks = candidates(record)
        body = "".join(c["body_text"] for c in chunks)
        self.assertIn("古国的记载", body)
        self.assertNotIn("筛选", body)
        self.assertTrue(any(d["reason"] == "filter_control_metadata" for d in adapted["decisions"]))
        parent = next(p for p in adapted["parents"] if p["kind"] == "section")
        self.assertIn("筛选选项", str(read_natural_parent(parent, record)))

    def test_mechanism_table_identities_do_not_become_story_candidates(self):
        _, chunks = candidates(clean({"header": ["名称", "攻击力"], "row": [["测试武器", "500"]]}, "multi_table"))
        self.assertFalse(chunks)
        _, story = candidates(clean({"header": ["名称", "所属"], "row": [["人物甲", "古国组织"]]}, "multi_table"))
        self.assertIn("古国组织", "".join(c["body_text"] for c in story))

    def test_mixed_guide_retains_complete_prose_and_records_risk(self):
        text = "曾居高天的守望者。战斗中造成50%伤害。"
        adapted, chunks = candidates(clean({"rich_text": "<p>" + text + "</p>"}, title="攻略方法"))
        self.assertIn(text, "".join(c["body_text"] for c in chunks))
        self.assertTrue(any(d["reason"] == "mixed_or_uncertain" for d in adapted["decisions"]))

    def test_card_effect_field_does_not_hide_unknown_story_fields(self):
        _, chunks = candidates(clean({"list": [{"title": "牌名", "life": 3, "energy": 2,
            "desc": [{"key": "技能效果", "value": ["造成2点伤害。"]}],
            "future_story": "如香消，如雾散。"}]}, "card_skill"))
        body = "".join(c["body_text"] for c in chunks)
        self.assertNotIn("造成2点伤害", body)
        self.assertIn("如香消", body)

    def test_card_legacy_attribute_labels_are_scoped_controls(self):
        data = {"attr": [{"key": "充能：", "value": ["2点"]},
                         {"key": "攻击", "value": ["3"]}, {"key": "未知描述", "value": ["旧国故事。"]}]}
        _, chunks = candidates(clean(data, "card_base_info"))
        self.assertNotIn("2点", "".join(c["body_text"] for c in chunks))
        self.assertNotIn("3", "".join(c["body_text"] for c in chunks))
        self.assertIn("旧国故事", "".join(c["body_text"] for c in chunks))
        _, other = candidates(clean(data))
        self.assertIn("2点", "".join(c["body_text"] for c in other))

    def test_explicit_choice_join_and_display_numbers_are_separate_from_body(self):
        record = graph_record({"private_root_123": "", "a": "旅行者：非常顺手！", "b": "旅行者：马马虎虎。", "j": ""},
            {"private_root_123": ["a", "b"], "a": ["j"], "b": ["j"]}, root="private_root_123",
            dialogue={"private_root_123": "<p>用起来顺手吗？</p>", "a": "", "b": "", "j": "<p>共同回应。</p>"})
        before = deepcopy(record)
        adapted, chunks = candidates(record)
        graph = adapted["graphs"][0]
        self.assertEqual(record, before)
        self.assertEqual(graph["group"], next(b for b in iter_blocks(record) if b["kind"] == "dialogue")["groups"][0])
        for level in ("micro", "macro"):
            text = "\n".join(c["retrieval_text"] for c in chunks if level in c["granularities"])
            self.assertIn("并列选项，本次选择择一", text)
            self.assertIn("公共后续", text)
            self.assertEqual(text.count("共同回应。"), 1)
            self.assertNotIn("private_root_123", text)
        self.assertTrue(all(c["order_semantics"] == "display_only" for c in chunks))

    def test_option_tail_guards_propagate_and_do_not_mix_before_after(self):
        record = graph_record({"r": "", "a": "【姑娘是…】——完成魔神任务「旧事」前",
            "aa": "旅行者：我可以帮忙。", "b": "【姑娘是…】——完成魔神任务「旧事」后", "j": ""},
            {"r": ["a", "b"], "a": ["aa"], "aa": ["j"], "b": ["j"]})
        adapted, chunks = candidates(record)
        info = adapted["graphs"][0]["derived_nodes"]
        self.assertEqual(info["aa"]["condition_sources"][0]["basis"], "inherited")
        self.assertFalse(info["j"]["condition_sources"])
        self.assertEqual(info["j"]["condition_status"], "not_established")
        self.assertEqual(len(info["j"]["incoming_condition_sources"]), 2)
        for c in chunks:
            scopes = {tuple(s["condition_id"] for s in p.get("condition_sources", [])) for p in c["parts"]}
            self.assertEqual(len(scopes), 1)
        for source in info["a"]["condition_sources"]:
            self.assertEqual(resolve_clean_ref(CleanRef(**source["clean_ref"]), record=record)["text"], source["text"])

    def test_composite_guard_and_day_night_are_source_literals(self):
        record = graph_record({"r": "", "a": "若此前选择【甲】与【乙】触发", "b": "【晚安。】——【夜晚】"}, {"r": ["a", "b"]})
        info = adapt_record(record)["graphs"][0]["derived_nodes"]
        self.assertEqual(info["a"]["condition_status"], "unresolved")
        self.assertEqual(info["a"]["condition_sources"][0]["text"], "若此前选择【甲】与【乙】触发")
        self.assertEqual(info["b"]["condition_status"], "source_marked")

    def test_spoken_if_and_long_option_prose_are_kept_without_guessed_guard(self):
        record = graph_record({"r": "旅行者：如果是不开心的事…", "a": "宵宫：完成约定之后，我很高兴。" * 20}, {"r": ["a"]})
        adapted, chunks = candidates(record)
        self.assertFalse(adapted["graphs"][0]["derived_nodes"]["r"]["condition_sources"])
        self.assertFalse(adapted["graphs"][0]["derived_nodes"]["a"]["condition_sources"])
        self.assertIn("宵宫：完成约定之后", "".join(c["body_text"] for c in chunks))

    def test_unknown_fork_does_not_assert_exclusive_choices(self):
        _, chunks = candidates(graph_record({"r": "", "a": "场景甲", "b": "场景乙"}, {"r": ["a", "b"]}))
        text = "".join(c["retrieval_text"] for c in chunks)
        self.assertIn("源图分叉", text)
        self.assertNotIn("本次选择择一", text)

    def test_source_fork_context_is_visible_when_root_is_outside_candidate(self):
        adapted = adapt_record(graph_record({"r": "", "a": "场景甲", "b": "场景乙"}, {"r": ["a", "b"]}))
        parent = next(p for p in adapted["parents"] if p["kind"] == "dialogue_group")
        parts = [p for u in parent["units"] for p in u["parts"] if p["node_id"] == "a"]
        text = retrieval_text(parent, parts)
        self.assertIn("源图分支，发生关系待解释", text)
        self.assertNotIn("本次选择择一", text)

    def test_unreachable_classification_keeps_original_occurrences_and_edges(self):
        record = graph_record({"r": "", "a": "选项", "copy": "选项", "empty": ""}, {"r": ["a"]},
            dialogue={"a": "<p>同文。</p>", "copy": "<p>同文。</p>", "empty": ""})
        adapted, chunks = candidates(record)
        info = adapted["graphs"][0]["derived_nodes"]
        self.assertEqual(info["copy"]["reachability"], "isolated_in_valid_root_group")
        self.assertEqual(info["copy"]["same_text_as_reachable_node_ids"], ["a"])
        self.assertFalse(info["empty"]["has_text"])
        self.assertEqual(info["copy"]["condition_status"], "unresolved")
        self.assertFalse(any({"a", "copy"} <= {p.get("node_id") for p in c["parts"]} for c in chunks))
        missing = adapt_record(graph_record({"a": "甲", "b": "乙"}, {"a": ["b"]}, root="missing"))["graphs"][0]
        self.assertTrue(all(n["reachability"] == "declared_root_missing" for n in missing["derived_nodes"].values()))
        self.assertEqual(len(missing["group"]["edges"]), 1)

    def test_nested_branch_preserves_outer_choice_at_inner_join(self):
        options = {"r": "", "a": "旅行者：甲", "b": "旅行者：乙", "aa": "旅行者：甲一", "ab": "旅行者：甲二", "aj": "", "j": ""}
        adapted = adapt_record(graph_record(options, {"r": ["a", "b"], "a": ["aa", "ab"], "aa": ["aj"], "ab": ["aj"], "aj": ["j"], "b": ["j"]}))
        info = adapted["graphs"][0]["derived_nodes"]
        self.assertIn(("r", "a", "choice"), info["aj"]["branch_context"])
        self.assertFalse(info["j"]["branch_context"])

    def test_continuations_keep_option_refs_and_can_defer_optional_context(self):
        record = graph_record({"r": "旅行者：关于古国…"}, {}, dialogue={"r": "<p>古国留下的记载。</p>" * 80})
        _, chunks = candidates(record)
        later = [c for c in chunks if "节点内续片" in c["retrieval_text"]]
        self.assertTrue(later)
        self.assertTrue(all(c["parts"][0]["option_context"] for c in later))
        self.assertTrue(any("关于古国" in c["context_text"] for c in later))
        self.assertTrue(all(c["tokens"] <= (384 if "micro" in c["granularities"] else 1024) for c in chunks))

    def test_dual_display_orders_are_unique_and_source_context_has_refs(self):
        record = clean({"rich_text": "".join("<p>第" + str(i) + "段。" + "记载。" * (1 + i % 9) + "</p>" for i in range(40))})
        _, chunks = candidates(record, ChunkProfile("fixture", 45, 75, 100, 140))
        for level in ("micro", "macro"):
            orders = sorted(c["display_order_by_granularity"][level] for c in chunks if level in c["granularities"])
            self.assertEqual(orders, list(range(len(orders))))
        self.assertTrue(all(c["projection_roles"]["body_text"] == "quotable_source" for c in chunks))

    def test_unrecognized_annotation_is_unresolved_and_never_unconditional(self):
        adapted, chunks = candidates(graph_record({"r": "条件：来源没有解释的标志", "a": ""}, {"r": ["a"]}))
        info = adapted["graphs"][0]["derived_nodes"]
        self.assertEqual(info["r"]["condition_status"], "unresolved")
        self.assertEqual(info["a"]["condition_status"], "unresolved")
        self.assertNotIn("无条件", "".join(c["retrieval_text"] for c in chunks))

    def test_cycle_preserves_edges_and_disables_cross_node_condition_inheritance(self):
        record = graph_record({"r": "完成前置任务后触发", "a": ""}, {"r": ["a"], "a": ["r"]})
        adapted = adapt_record(record)
        graph = adapted["graphs"][0]
        self.assertIn("cycle_unsequenced", graph["issues"])
        self.assertEqual(len(graph["group"]["edges"]), 2)
        self.assertFalse(graph["derived_nodes"]["a"]["condition_sources"])

    def test_optional_context_deferral_preserves_source_and_budget(self):
        record = graph_record({"r": "旅行者：" + "长提问" * 30}, {}, dialogue={"r": "<p>记载。</p>" * 35})
        _, chunks = candidates(record, ChunkProfile("fixture", 45, 75, 100, 140))
        deferred = [c for c in chunks if c["parts"][0].get("optional_context_deferred")]
        self.assertTrue(deferred)
        self.assertTrue(all(c["parts"][0]["option_context"] for c in deferred))
        self.assertTrue(all(c["tokens"] <= (75 if "micro" in c["granularities"] else 140) for c in chunks))

    def test_task_links_and_source_section_neighbors_stay_metadata(self):
        record = clean({"list": [{"key": "前置任务", "value": [
            '<a href="/ys/obc/content/4690/detail" data-entry-id="4690">前章</a>']} ]}, "rich_base_info")
        adapted, chunks = candidates(record)
        task = next(p for p in adapted["parents"] if p["units"])["context"]["task_context"][0]
        self.assertEqual(task["sources"][0]["text"], "前章")
        self.assertTrue(any(r.get("target_content_id") == "4690" for r in task["source_relations"]))
        self.assertNotIn("target_content_id", "".join(c["retrieval_text"] for c in chunks))

    def test_unknown_story_table_duration_and_materials_are_retained(self):
        _, chunks = candidates(clean({"header": ["名称", "时长", "材料"],
            "row": [["古老仪式", "三日", "先王的信物"]]}, "multi_table"))
        text = "".join(c["body_text"] for c in chunks)
        self.assertIn("三日", text)
        self.assertIn("先王的信物", text)

    def test_complete_reward_quantity_format_is_scoped_and_mixed_prose_survives(self):
        adapted, chunks = candidates(clean({"rich_text": "<p>原石*60</p><p>先王赠予的原石*60，见证古国往事。</p>"}, title="任务奖励"))
        self.assertTrue(any(d["reason"] == "explicit_reward_quantity" for d in adapted["decisions"]))
        self.assertIn("古国往事", "".join(c["body_text"] for c in chunks))
        _, outside = candidates(clean({"rich_text": "<p>原石*60</p>"}, title="遗留记载"))
        self.assertIn("原石*60", outside[0]["body_text"])

    def test_multiple_fork_join_metadata_is_stable_across_hash_seeds(self):
        script = """
import json
from tests.chunking.test_v2 import graph_record
from genshin_corpus.chunking import adapt_record
r = graph_record({'r':'', 'a':'旅行者：甲', 'b':'旅行者：乙',
                  'aa':'旅行者：甲一', 'ab':'旅行者：甲二', 'j':''},
                 {'r':['a','b'], 'a':['aa','ab'], 'aa':['j'], 'ab':['j'], 'b':['j']})
print(json.dumps(adapt_record(r), ensure_ascii=True, sort_keys=True))
"""
        results = [subprocess.check_output([sys.executable, "-c", script],
                   env={**os.environ, "PYTHONHASHSEED": seed}) for seed in ("2", "17")]
        self.assertEqual(*results)


if __name__ == "__main__":
    unittest.main()
