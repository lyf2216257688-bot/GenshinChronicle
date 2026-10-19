"""Unit tests for Phase 05 lightweight context continuity and clue evolution."""

import unittest

from genshin_corpus.retrieval.clue_state import (
    ClueEntry,
    ClueEvent,
    ClueStateError,
    ContextContinuityState,
    SourceLedgerEntry,
)


class TestClueContinuityState(unittest.TestCase):

    def setUp(self) -> None:
        self.u1 = "d9fed6511005d773cdb702580731656454c0f6d40685451dfe11ddbb1b45cfb8"
        self.u2 = "6961332b1b0f292b1896fb9f72a8ffb844ddbf144bee2e1fc8b841f30d6f138b"
        self.u3 = "05e788aceff16128d03709e10a04807e103771d6f9a013da4eec8112e82aa4d6"
        self.u4 = "0e59547688f1e8f64e695165d6b0f70a0965914de8b12f6de45cb6647694c5b6"

    def test_add_and_derive_active_clues(self) -> None:
        c1 = ClueEntry(
            clue_id="clue:3429:001",
            text="绘真列出遗失的馆藏五本书籍，并透露《日月前事》被盗，嫌疑人关在悬空牢。",
            source_unit_ids=(self.u1,),
            source_genre="official_duty_dialogue",
            epistemic_status="duty_testimony",
            narrative_significance="提供了调查渊下宫历史事件的关键委托与线索起点",
        )
        ev1 = ClueEvent(
            event_id="ev:001",
            step=1,
            operation="add",
            clue=c1,
        )
        ledger_entry = SourceLedgerEntry(
            unit_id=self.u1,
            content_id="3429",
            record_title="龙蛇藏归辑录",
            section_name="剧情对话",
            scope_level="full_section",
            source_genre="official_duty_dialogue",
        )
        state = ContextContinuityState(
            step=1,
            research_summary="调查开始于末代图书管理员绘真的失窃书目委托。",
            source_ledger=(ledger_entry,),
            clue_events=(ev1,),
        )
        state.validate_references(known_unit_ids={self.u1})
        active = state.derive_active_clues()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].clue_id, "clue:3429:001")
        self.assertEqual(active[0].status, "active")

    def test_multi_perspective_contrast_preserves_both(self) -> None:
        """Requirement 2 & 3: Different narrative perspectives contrast without invalidating each other."""
        c_fiction = ClueEntry(
            clue_id="clue:3226:001",
            text="《常世国龙蛇传》小说记载小童寻龙骨花遇大蛇，大蛇自称渎身渎名并答应做神明。",
            source_unit_ids=(self.u4,),
            source_genre="folklore_adapted_fiction",
            epistemic_status="folklore_oral_tradition",
            narrative_significance="记录了海祇民间故事口头母题与早期蛇神相遇叙事",
        )
        ev1 = ClueEvent(event_id="ev:001", step=1, operation="add", clue=c_fiction)

        c_divine = ClueEntry(
            clue_id="clue:3611:001",
            text="武器《证誓之明瞳》铭文中纯白大蛇自述败于贵金之神与鸣神逃亡暗海，向小童立誓并称带民前往海面是为履行与天上之都的誓言。",
            source_unit_ids=(self.u3,),
            source_genre="weapon_lore_divine_oath",
            epistemic_status="primary_divine_statement",
            narrative_significance="神明第一人称自陈，展示了与民间小说演义不同的战败耻辱与天命誓约视角",
        )
        ev2 = ClueEvent(
            event_id="ev:002",
            step=2,
            operation="contrast",
            clue=c_divine,
            target_clue_id="clue:3226:001",
            reason="民间传说小说视角的艺术神怪演义与神明自身第一人称死誓自述形成叙事对照，两重视角并存互见",
        )

        state = ContextContinuityState(
            step=2,
            research_summary="对比民间小说与武器神明铭文，揭示大蛇到来与迁居的多视角复杂性。",
            source_ledger=(),
            clue_events=(ev1, ev2),
        )
        active = state.derive_active_clues()
        # BOTH clues must remain active in the derived set!
        self.assertEqual(len(active), 2)
        active_ids = {c.clue_id for c in active}
        self.assertIn("clue:3226:001", active_ids)
        self.assertIn("clue:3611:001", active_ids)

        c1_derived = next(c for c in active if c.clue_id == "clue:3226:001")
        c2_derived = next(c for c in active if c.clue_id == "clue:3611:001")
        self.assertEqual(c1_derived.status, "contrasted")
        self.assertEqual(c2_derived.status, "active")

    def test_supplement_and_revision(self) -> None:
        c1 = ClueEntry(
            clue_id="clue:3429:001",
            text="绘真提到《日月前事》被盗，安贞被关在大日御舆悬空牢。",
            source_unit_ids=(self.u1,),
            source_genre="official_duty_dialogue",
            epistemic_status="duty_testimony",
            narrative_significance="指出被盗禁书下落",
        )
        ev1 = ClueEvent(event_id="ev:001", step=1, operation="add", clue=c1)

        c2 = ClueEntry(
            clue_id="clue:3470:001",
            text="《白夜国馆藏》中收录的《日月前事》为常世大神书记绝笔，记载了白夜国在原初黑暗年代与深海龙蜥的战争。",
            source_unit_ids=(self.u2,),
            source_genre="ancient_chronicle_forbidden",
            epistemic_status="ancient_official_record",
            narrative_significance="确证了绘真所说的禁书原典核心内容",
        )
        ev2 = ClueEvent(
            event_id="ev:002",
            step=2,
            operation="supplement",
            clue=c2,
            target_clue_id="clue:3429:001",
            reason="读入古籍原典，补充了失窃禁书的书写者与真实记载内容",
        )

        state = ContextContinuityState(
            step=2,
            research_summary="从绘真口头证言推进到古籍原本《日月前事》。",
            source_ledger=(),
            clue_events=(ev1, ev2),
        )
        active = state.derive_active_clues()
        self.assertEqual(len(active), 2)
        target = next(c for c in active if c.clue_id == "clue:3429:001")
        self.assertEqual(target.status, "supplemented")

    def test_revise_replaces_target_with_active_clue(self) -> None:
        """Requirement 2: revise operation replaces target in active set with revised clue."""
        c1 = ClueEntry(
            clue_id="clue:initial:001",
            text="大蛇奥罗巴斯在魔神战争期间降临渊下宫。",
            source_unit_ids=(self.u1,),
            source_genre="official_duty_dialogue",
            epistemic_status="duty_testimony",
            narrative_significance="初步记录大蛇到来时点",
        )
        ev1 = ClueEvent(event_id="ev:001", step=1, operation="add", clue=c1)

        c1_revised = ClueEntry(
            clue_id="clue:revised:001",
            text="大蛇奥罗巴斯因不胜贵金之神与鸣神败逃暗海，后折珊瑚枝照亮海渊并立誓。",
            source_unit_ids=(self.u3,),
            source_genre="weapon_lore_divine_oath",
            epistemic_status="primary_divine_statement",
            narrative_significance="依据武器誓约精确修订降临背景与动机",
        )
        ev2 = ClueEvent(
            event_id="ev:002",
            step=2,
            operation="revise",
            clue=c1_revised,
            target_clue_id="clue:initial:001",
            reason="第一人称武器铭文提供了战败逃亡与立誓的具体背景，修订前期粗略表述",
        )

        state = ContextContinuityState(
            step=2,
            research_summary="修订线索",
            source_ledger=(),
            clue_events=(ev1, ev2),
        )
        active = state.derive_active_clues()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].clue_id, "clue:revised:001")
        self.assertEqual(active[0].status, "active")

    def test_supersede_and_invalidate_make_target_inactive(self) -> None:
        c1 = ClueEntry(
            clue_id="clue:hypothesis:001",
            text="初步假说：大蛇是在和平状态下偶然坠入海渊。",
            source_unit_ids=(self.u1,),
            source_genre="official_duty_dialogue",
            epistemic_status="duty_testimony",
            narrative_significance="调查初期假说",
        )
        ev1 = ClueEvent(event_id="ev:001", step=1, operation="add", clue=c1)

        c2 = ClueEntry(
            clue_id="clue:3611:002",
            text="武器《证誓之明瞳》证实大蛇因不胜贵金之神与鸣神逃亡暗海，而非和平偶然进入。",
            source_unit_ids=(self.u3,),
            source_genre="weapon_lore_divine_oath",
            epistemic_status="primary_divine_statement",
            narrative_significance="神明第一人称自述确证战败逃亡背景",
        )
        ev2 = ClueEvent(
            event_id="ev:002",
            step=2,
            operation="supersede",
            clue=c2,
            target_clue_id="clue:hypothesis:001",
            reason="神明直接自述确凿推翻了和平偶然坠落假说",
        )

        state = ContextContinuityState(
            step=2,
            research_summary="修正初步假说",
            source_ledger=(),
            clue_events=(ev1, ev2),
        )
        active = state.derive_active_clues()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].clue_id, "clue:3611:002")

    def test_invalidate_lifecycle(self) -> None:
        c1 = ClueEntry(
            clue_id="clue:rumor:001",
            text="坊间传闻：阿倍良久系因贪图王位被斩杀。",
            source_unit_ids=(self.u1,),
            source_genre="official_duty_dialogue",
            epistemic_status="duty_testimony",
            narrative_significance="早期流言",
        )
        ev1 = ClueEvent(event_id="ev:001", step=1, operation="add", clue=c1)

        c_inv = ClueEntry(
            clue_id="clue:refutation:001",
            text="官方正史与《日月前事》证实阿倍良久遭贵族陷害并终身监禁，绝非谋逆被斩。",
            source_unit_ids=(self.u2,),
            source_genre="ancient_chronicle_forbidden",
            epistemic_status="ancient_official_record",
            narrative_significance="证伪假传闻",
        )
        ev2 = ClueEvent(
            event_id="ev:002",
            step=2,
            operation="invalidate",
            clue=c_inv,
            target_clue_id="clue:rumor:001",
            reason="史料确凿证明谋逆说属贵族陷害虚构",
        )
        state = ContextContinuityState(
            step=2,
            research_summary="证伪测试",
            source_ledger=(),
            clue_events=(ev1, ev2),
        )
        active = state.derive_active_clues()
        self.assertEqual(len(active), 0)

    def test_cannot_target_inactive_clue(self) -> None:
        c1 = ClueEntry(
            clue_id="clue:dead:001",
            text="已废弃线索",
            source_unit_ids=(self.u1,),
            source_genre="official_duty_dialogue",
            epistemic_status="duty_testimony",
            narrative_significance="测试",
        )
        ev1 = ClueEvent(event_id="ev:001", step=1, operation="add", clue=c1)

        c2 = ClueEntry(
            clue_id="clue:alive:002",
            text="替代线索",
            source_unit_ids=(self.u2,),
            source_genre="ancient_chronicle_forbidden",
            epistemic_status="ancient_official_record",
            narrative_significance="替代测试",
        )
        ev2 = ClueEvent(
            event_id="ev:002",
            step=2,
            operation="supersede",
            clue=c2,
            target_clue_id="clue:dead:001",
            reason="废弃",
        )

        c3 = ClueEntry(
            clue_id="clue:invalid_op:003",
            text="试图补充已废弃线索",
            source_unit_ids=(self.u3,),
            source_genre="weapon_lore_divine_oath",
            epistemic_status="primary_divine_statement",
            narrative_significance="非法补充",
        )
        ev3 = ClueEvent(
            event_id="ev:003",
            step=3,
            operation="supplement",
            clue=c3,
            target_clue_id="clue:dead:001",
            reason="尝试补充",
        )

        state = ContextContinuityState(
            step=3,
            research_summary="非法跃迁测试",
            source_ledger=(),
            clue_events=(ev1, ev2, ev3),
        )
        with self.assertRaises(ClueStateError):
            state.derive_active_clues()

    def test_validation_errors(self) -> None:
        c1 = ClueEntry(
            clue_id="clue:001",
            text="测试线索",
            source_unit_ids=("unknown_unit_id",),
            source_genre="official_duty_dialogue",
            epistemic_status="duty_testimony",
            narrative_significance="测试",
        )
        ev1 = ClueEvent(event_id="ev:001", step=1, operation="add", clue=c1)
        state = ContextContinuityState(step=1, research_summary="test", source_ledger=(), clue_events=(ev1,))
        with self.assertRaises(ClueStateError):
            state.validate_references(known_unit_ids={"real_unit_id"})

        # Missing target clue for contrast
        with self.assertRaises(ClueStateError):
            ClueEvent(event_id="ev:002", step=2, operation="contrast", clue=c1, target_clue_id=None, reason="test")

        # Self-targeting error
        c_self = ClueEntry(
            clue_id="clue:self:001",
            text="自指测试",
            source_unit_ids=(self.u1,),
            source_genre="official_duty_dialogue",
            epistemic_status="duty_testimony",
            narrative_significance="测试",
        )
        ev_self = ClueEvent(
            event_id="ev:010",
            step=1,
            operation="supplement",
            clue=c_self,
            target_clue_id="clue:self:001",
            reason="自指",
        )
        state_self = ContextContinuityState(step=1, research_summary="test", source_ledger=(), clue_events=(ev_self,))
        with self.assertRaises(ClueStateError):
            state_self.validate_references()

        # Duplicate event_id error
        ev_dup1 = ClueEvent(event_id="ev:dup", step=1, operation="add", clue=c_self)
        c_other = ClueEntry(
            clue_id="clue:other:001",
            text="其他测试",
            source_unit_ids=(self.u1,),
            source_genre="official_duty_dialogue",
            epistemic_status="duty_testimony",
            narrative_significance="测试",
        )
        ev_dup2 = ClueEvent(event_id="ev:dup", step=2, operation="add", clue=c_other)
        state_dup = ContextContinuityState(step=2, research_summary="test", source_ledger=(), clue_events=(ev_dup1, ev_dup2))
        with self.assertRaises(ClueStateError):
            state_dup.validate_references()

        # Duplicate clue_id across events
        ev_clue_dup1 = ClueEvent(event_id="ev:c1", step=1, operation="add", clue=c_self)
        ev_clue_dup2 = ClueEvent(event_id="ev:c2", step=2, operation="supplement", clue=c_self, target_clue_id="clue:self:001", reason="dup")
        state_clue_dup = ContextContinuityState(step=2, research_summary="test", source_ledger=(), clue_events=(ev_clue_dup1, ev_clue_dup2))
        with self.assertRaises(ClueStateError):
            state_clue_dup.validate_references()

    def test_serialization_round_trip(self) -> None:
        c1 = ClueEntry(
            clue_id="clue:001",
            text="测试线索内容",
            source_unit_ids=(self.u1,),
            source_genre="official_duty_dialogue",
            epistemic_status="duty_testimony",
            narrative_significance="剧情价值描述",
        )
        ev1 = ClueEvent(event_id="ev:001", step=1, operation="add", clue=c1)
        ledger_entry = SourceLedgerEntry(
            unit_id=self.u1,
            content_id="3429",
            record_title="龙蛇藏归辑录",
            section_name="剧情对话",
            scope_level="slice",
            source_genre="official_duty_dialogue",
        )
        state = ContextContinuityState(
            step=1,
            research_summary="序列化测试",
            source_ledger=(ledger_entry,),
            clue_events=(ev1,),
        )
        data = state.to_dict()
        restored = ContextContinuityState.from_dict(data)
        self.assertEqual(restored.step, state.step)
        self.assertEqual(restored.research_summary, state.research_summary)
        self.assertEqual(len(restored.source_ledger), 1)
        self.assertEqual(len(restored.clue_events), 1)
        self.assertEqual(restored.clue_events[0].clue.clue_id, "clue:001")


if __name__ == "__main__":
    unittest.main()
