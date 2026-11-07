"""Bounded V2.1 admission regressions and source-preservation controls."""
from copy import deepcopy
import unittest

from genshin_corpus.chunking import adapt_record, read_natural_parent
from genshin_corpus.chunking.pilot import _validate_parts
from genshin_corpus.chunking.core import ChunkProfile
from tests.chunking.test_chunking import clean
from tests.chunking.test_v2 import candidates, graph_record


class V21Tests(unittest.TestCase):
    def test_display_placeholders_do_not_become_candidates(self):
        record = clean({"list": [{"key": "功能", "value": ["提瓦特大地图", "套装展示", "部件展示", "暂无", "-"]},
                                {"key": "性别", "value": ["男"]},
                                {"key": "职业", "value": ["年老的学者"]},
                                {"key": "心声", "value": ["唉…记忆都去哪里了…"]}]}, "npc_base_info")
        before = deepcopy(record)
        adapted, chunks = candidates(record)
        text = "\n".join(c["body_text"] for c in chunks)
        self.assertNotIn("提瓦特大地图", text)
        self.assertNotIn("暂无", text)
        self.assertIn("男", text)
        self.assertIn("年老的学者", text)
        self.assertIn("唉…记忆都去哪里了…", text)
        self.assertEqual(record, before)
        self.assertEqual(sum(d["reason"] == "source_display_placeholder" for d in adapted["decisions"]), 5)

    def test_numeric_dialogue_and_spoken_placeholder_words_stay(self):
        record = graph_record({"r": "2", "a": "1"}, {"r": ["a"]},
                              dialogue={"r": "<p>7</p>", "a": "<p>无</p>"})
        adapted, chunks = candidates(record)
        text = "\n".join(c["body_text"] for c in chunks)
        for word in ["1", "2", "7", "无"]:
            self.assertIn(word, text)
        _validate_parts(record, adapted, chunks, ChunkProfile())

    def test_unknown_scalar_and_year_remain(self):
        _, chunks = candidates(clean({"year": 1900, "rich_text": "<p>1900</p><p>暂无归期。</p>"}))
        self.assertIn("1900", "\n".join(c["body_text"] for c in chunks))
        self.assertIn("暂无归期。", "\n".join(c["body_text"] for c in chunks))

    def test_numeric_title_requires_wholly_numeric_generic_body(self):
        for text, expected in [("<p>23</p><p>132</p>", False),
                               ("<p>1900</p><p>这一年，他回到故乡。</p>", True)]:
            record = clean({"rich_text":text,"year":1900})
            record['document']['title']['text'] = '233'
            adapted, chunks = candidates(record)
            numeric_parts = [p['text'] for c in chunks for p in c['parts']
                             if p['field_keys'] and p['field_keys'][-1]=='collapse_panel:/rich_text']
            self.assertEqual(bool(numeric_parts),expected)
            self.assertTrue(any(p['text']=='1900' and p['source_kind']=='scalar' for c in chunks for p in c['parts']))

    def test_audio_fold_removed_while_conditions_and_graph_stay(self):
        audio = '<details><summary>语音</summary><p>汉语：[当前音频暂无文件名]</p><p>英语：[当前音频暂无文件名]</p></details>'
        record = graph_record({"r": "【晚安，北斗。】——【夜晚】", "a": "旅行者：再见。"}, {"r": ["a"]},
                              dialogue={"r": "<p>晚安，旅行者。</p><p>脑袋里少想点事情，休息得好点。</p>" + audio,
                                        "a": "<p>改天见啊。</p>"})
        before = deepcopy(record)
        adapted, chunks = candidates(record)
        for c in chunks:
            self.assertNotIn("当前音频", c["retrieval_text"])
            self.assertNotIn("\n语音\n", c["retrieval_text"])
            self.assertFalse(any(p["text"].strip() == "语音" for p in c["parts"]))
        self.assertIn("脑袋里少想点事情", "\n".join(c["body_text"] for c in chunks))
        self.assertEqual(record, before)
        info = adapted["graphs"][0]["derived_nodes"]
        self.assertTrue(info["a"]["condition_sources"])
        self.assertEqual(info["r"]["outgoing_node_ids"], ["a"])
        parent = next(p for p in adapted["parents"] if p["kind"] == "dialogue_group")
        self.assertIn("当前音频暂无文件名", str(read_natural_parent(parent, record)))
        _validate_parts(record, adapted, chunks, ChunkProfile())

    def test_audio_fold_with_unknown_prose_keeps_prose(self):
        record = graph_record({"r": ""}, {}, dialogue={"r":
            '<details><summary>语音</summary><p>这段语音记录着古国的故事。</p>'
            '<p>汉语：[当前音频暂无文件名]</p></details>'})
        _, chunks = candidates(record)
        self.assertIn("这段语音记录着古国的故事。", "\n".join(c["body_text"] for c in chunks))

    def test_audio_fold_with_table_keeps_unknown_structure(self):
        record = graph_record({"r":""}, {}, dialogue={"r":
            '<details><summary>语音</summary><p>汉语：[当前音频暂无文件名]</p>'
            '<table><tr><td>村民口述的古国往事。</td></tr></table></details>'})
        _, chunks = candidates(record)
        self.assertIn("村民口述的古国往事。", "\n".join(c["body_text"] for c in chunks))

    def test_talent_templates_are_scoped_and_mixed_values_stay(self):
        attack = "普通攻击\n进行至多五段的连续剑击。\n重击\n消耗一定体力，瞬间向前方挥出两剑。"
        dispatch = "在挪德卡莱执行探索派遣任务时，消耗的时间缩短25%。"
        lore = "此外，作为「秘闻馆」的雇员，雅珂达似乎可以通过某种方式获取各方的情报，而那夏镇的某些势力似乎对这些情报很有兴趣…"
        data = {"list": [{"title": "普通攻击", "desc": attack},
                         {"title": "固有天赋", "desc": "<p>" + dispatch + "</p><p>" + lore + "</p>"},
                         {"title": "混合", "desc": dispatch + lore}]}
        adapted, chunks = candidates(clean(data, "role_talent"))
        text = "\n".join(c["body_text"] for c in chunks)
        self.assertNotIn("连续剑击", text)
        self.assertIn(lore, text)
        self.assertIn(dispatch + lore, text)
        self.assertEqual(sum(d["reason"] == "explicit_talent_mechanics_template" for d in adapted["decisions"]), 2)
        _, other = candidates(clean(data))
        self.assertIn(attack, "\n".join(c["body_text"] for c in other))

    def test_guide_fields_do_not_delete_description_or_quest_conditions(self):
        _, chunks = candidates(clean({"attr": [{"key": "天赋加点推荐", "value": ["A＞E≥Q"]},
            {"key": "武器推荐", "value": ["图莱杜拉的回忆"]},
            {"key": "描述", "value": ["传说中，命之座的图形反映着人的气质。"]},
            {"key": "触发条件", "value": ["完成前置任务后"]}]}, "material_base_info"))
        text = "\n".join(c["body_text"] for c in chunks)
        self.assertNotIn("A＞E≥Q", text)
        self.assertNotIn("图莱杜拉", text)
        self.assertIn("传说中", text)
        self.assertIn("完成前置任务后", text)

    def test_food_effect_scope_and_story(self):
        data = {"attr": [{"key": "使用效果", "value": ["恢复100点体力。"]},
                         {"key": "描述", "value": ["经过派蒙苦思冥想改良后的兰那罗料理。"]}]}
        _, chunks = candidates(clean(data, "material_base_info", channel="食物"))
        text = "\n".join(c["body_text"] for c in chunks)
        self.assertNotIn("恢复100点", text)
        self.assertIn("兰那罗料理", text)
        _, other = candidates(clean(data, "material_base_info", channel="故事"))
        self.assertIn("恢复100点", "\n".join(c["body_text"] for c in other))

    def test_constellation_upgrade_template_keeps_mixed_narrative(self):
        data = {"header": [""], "row": [["<p>罪缘</p><p>元素爆发极恶技·灭[详情]的技能等级提高3级。</p>"
            "<p>至多提升至15级。</p><p>星辰记载着故乡的传说。元素爆发的技能等级提高3级。</p>"]]}
        adapted, chunks = candidates(clean(data, "multi_table", title="命之座"))
        self.assertEqual(sum(d["reason"] == "explicit_constellation_mechanics_template" for d in adapted["decisions"]), 2)
        self.assertIn("星辰记载着故乡的传说。元素爆发的技能等级提高3级。", "\n".join(c["body_text"] for c in chunks))

    def test_mechanical_table_columns_and_unknown_story_column(self):
        _, chunks = candidates(clean({"header": ["料理名", "加工材料", "料理效果（美味）"],
            "row": [["五宝腌菜", "白萝卜*2", "恢复100点体力。"]]}, "multi_table"))
        self.assertFalse(chunks)
        _, chunks = candidates(clean({"header": ["名称", "祝福效果", "描述"],
            "row": [["古国", "愿大家归来。", "村民的古老传说。"]]}, "multi_table"))
        text = "\n".join(c["body_text"] for c in chunks)
        self.assertIn("愿大家归来", text)
        self.assertIn("古老传说", text)

    def test_weapon_effect_with_observed_narrative_introduction_stays_complete(self):
        mixed = '抗争的践行之歌：飘游风中的「千年的大乐章」的一部分。造成的伤害提高10%；触发元素反应时，角色获得一枚奋起之符。'
        record = clean({'header':['详细属性','副词条属性','武器特效词缀（精炼1阶）'],
                        'row':[['苍古自由之誓','元素精通: 43',mixed],
                               ['纯机制武器','暴击率: 4.8%','攻击力提升20%。']]},'filter_table')
        adapted,chunks = candidates(record)
        text='\n'.join(c['body_text'] for c in chunks)
        self.assertIn(mixed,text)
        self.assertNotIn('攻击力提升20%。',text)
        self.assertTrue(any(d['reason']=='mixed_weapon_effect_description' for d in adapted['decisions']))

    def test_reward_table_requires_both_inventory_header_and_numeric_body(self):
        table = '<table><tr><td>冒险等阶</td><td>原石</td></tr><tr><td>12~15</td><td>*10</td></tr></table>'
        _, chunks = candidates(clean({"rich_text": table}, title="任务奖励"))
        self.assertFalse(chunks)
        _, story = candidates(clean({"rich_text": table.replace('*10', '先王赠予的礼物。')}, title="任务奖励"))
        self.assertIn("先王赠予", "\n".join(c["body_text"] for c in story))

    def test_mechanical_tables_with_image_and_potion_columns(self):
        for headers, values in [(["时间", "祝福名称", "祝福效果", "图片"],
                                 ["8月", "攻辉之月", "释放冲击波。", "……"]),
                                (["药剂名", "合成材料", "使用效果"],
                                 ["治草药剂", "晶核*1", "草元素抗性提升25%。"])]:
            _, chunks = candidates(clean({"header":headers,"row":[values]},"multi_table"))
            self.assertFalse(chunks)
        _, story = candidates(clean({"header":["名称","使用效果","描述"],
            "row":[["先王之药","记忆复苏。","故乡的故事。"]]},"multi_table"))
        self.assertIn("记忆复苏。", "\n".join(c["body_text"] for c in story))

    def test_editorial_recommendation_and_distinct_source_duplicates(self):
        text = "玩家攻略推荐：【V2.2攻略】#探索解谜#雾海纪行主线day3【观测枢】\t作者：猫冬"
        record = clean({"rich_text": "<p>凯瑟琳似乎有新的委托。</p><p>" + text + "</p>"})
        _, chunks = candidates(record)
        self.assertNotIn("玩家攻略推荐", "\n".join(c["retrieval_text"] for c in chunks))
        self.assertIn("新的委托", "\n".join(c["body_text"] for c in chunks))
        record = graph_record({"r": "", "a": "旅行者：甲", "b": "旅行者：乙"}, {"r": ["a", "b"]},
                              dialogue={"r": "", "a": "<p>相同回应。</p>", "b": "<p>相同回应。</p>"})
        adapted, chunks = candidates(record)
        for level in ['micro', 'macro']:
            self.assertEqual(sum(p['text'] == '相同回应。' for c in chunks if level in c['granularities'] for p in c['parts']), 2)
        _validate_parts(record, adapted, chunks, ChunkProfile())
