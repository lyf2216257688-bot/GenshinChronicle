"""Byakuyakoku Phase 05 sequential continuous understanding experiment.

Verifies whether a model in a continuous context can incrementally understand Byakuyakoku lore
by reading newly provided official materials across 7 sequential steps, forming, supplementing,
cross-referencing, and when necessary revising existing clues, producing an authentic Navi
narrative understanding result.

Zero retry on format error/timeout/UNKNOWN; max 7 real provider requests; token limit <= 100,000.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

import httpx
import openai

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.rag.navi_e2_routes import e2_route
from genshin_corpus.retrieval.clue_state import (
    CLUE_STATE_SCHEMA_VERSION,
    ClueEntry,
    ClueEvent,
    ContextContinuityState,
    SourceLedgerEntry,
    VALID_EPISTEMIC,
    VALID_GENRES,
    VALID_OPERATIONS,
)
from genshin_corpus.retrieval.navi_exploration import ExplorationError
from genshin_corpus.retrieval.semantic_sdk_runner import (
    _DoneTrackingStream,
    _JsonlArtifactWriter,
    _ResponsesDecisionText,
    _chunk_mapping,
    _normalize_usage,
    _responses_event_parts,
)


DOC_STEPS: tuple[dict[str, Any], ...] = (
    {
        "step": 1,
        "content_id": "3429",
        "title": "龙蛇藏归辑录",
        "section_ordinals": [7],
        "source_genre": "official_duty_dialogue",
        "epistemic_status": "duty_testimony",
        "doc_role_description": "白夜国末代图书管理员绘真实务对话，交代失窃馆藏、嫌疑人安贞及对流通本《常世国龙蛇传》封面异样的困惑",
    },
    {
        "step": 2,
        "content_id": "3470",
        "title": "白夜国馆藏",
        "section_ordinals": [1, 2, 3, 4],
        "source_genre": "ancient_chronicle_forbidden",
        "epistemic_status": "ancient_official_record",
        "doc_role_description": "常世大神书记官绝笔编年史《日月前事》、水文考与馆藏记录，记录原初时代天地创生、阿倍良久造日与深海龙蜥肆虐",
    },
    {
        "step": 3,
        "content_id": "3226",
        "title": "常世国龙蛇传",
        "section_ordinals": [0, 1],
        "source_genre": "folklore_adapted_fiction",
        "epistemic_status": "folklore_oral_tradition",
        "doc_role_description": "鸣神岛八重堂再版商业小说，资料页揭示改编背景（解开绘真封面困惑），正文保存阿倍良久造日被囚与小童遇蛇神母题",
    },
    {
        "step": 4,
        "content_id": "3611",
        "title": "证誓之明瞳",
        "section_ordinals": [3],
        "source_genre": "weapon_lore_divine_oath",
        "epistemic_status": "primary_divine_statement",
        "doc_role_description": "大蛇奥罗巴斯第一人称死誓自述，承认战败逃亡与受够偷生之耻，展示蛇瞳宝珠命小童立誓并履行天上死誓",
    },
    {
        "step": 5,
        "content_id": "3227",
        "title": "珊瑚宫民间信仰初勘",
        "section_ordinals": [1],
        "source_genre": "scholarly_conjecture",
        "epistemic_status": "scholarly_conjecture",
        "doc_role_description": "学者考据专著，对勘救民牺牲推测传说与封存海渊编年史/改学鸣神氏族之野史",
    },
    {
        "step": 6,
        "content_id": "508883",
        "title": "白夜国",
        "section_ordinals": [4, 10],
        "source_genre": "community_chronology",
        "epistemic_status": "community_synthesis",
        "doc_role_description": "白夜国重要事件历史时间线与人物总录，梳理造日、囚贤、蛇神到来、造岛、封存史书到东征陨落",
    },
    {
        "step": 7,
        "content_id": "2403",
        "title": "珊瑚宫心海",
        "section_ordinals": [13],
        "source_genre": "character_lore",
        "epistemic_status": "official_character_definition",
        "doc_role_description": "现任海祇领袖官方角色设定，确立迁民至海面、魔神战死、祟神遗留的后世定局与历史继承",
    },
)

CONTINUITY_SYSTEM_PROMPT = """你是一名严谨的原神世界观与白夜国（渊下宫）官方剧情研究员。
你的任务是在7步连续材料阅读中，逐步构建对白夜国历史剧情的完整、深入、有条理的连续理解。
每一轮你将阅读一批新的官方一手或设定资料，结合上一轮的工作记忆与已有线索，进行综合分析。

【核心原则】
1. 剧情理解为主：重点理解剧情脉络、人物动机、历史事件经过、跨文献关联及未解疑问。
2. 尊重官方材料立场：对于游戏内出现的传说、古籍、小说、碑铭与人物证言，不作过度虚无主义怀疑。它们均来自游戏官方文本，代表不同的叙事视角（如NPC实务记忆、古代编年史官绝笔、民间文学改编母题、神明自身第一人称自述、后世学者批判考据、百科时间线整理、角色设定定性）。
3. 补充与对照：不同视角之间的差异，优先视为有价值的“补充”（supplement）或“多重视角对照”（contrast），而非简单粗暴地互相否定（不要轻易把神话当假话一笔抹杀，也不要把不同叙述强行归为谎言）。只有在后文明确推翻或纠正先前错误理解时才使用“修订”（revise）或“废弃”（supersede）。
4. 允许自然推断：允许基于文本做出合乎逻辑的剧情推断，重点避免凭空捏造不存在的来源、人物张冠李戴（例如将图书管理员绘真说成偷书贼，将深海龙蜥说成造岛者等重大错误）或颠倒因果。
5. 独立理解：请完全凭借你自身对官方原文的理解来提炼线索与总结脉络，不要依赖预设模板。

【输出格式要求】
你必须且仅输出一个纯 JSON 对象（不要使用 markdown 标记或任何包裹文字）：
{
  "step_understanding": "本步对当前材料的深度叙事理解：涵盖新材料揭示的剧情核心、人物动机、与已有文献的联系以及情节推进。",
  "clue_updates": [
    {
      "operation": "add|supplement|contrast|revise|supersede",
      "clue_id": "唯一线索标识，例如 clue_1_01",
      "target_clue_id": "如果是 supplement/contrast/revise/supersede，填被指向的前序线索ID；如果是 add，填 null",
      "text": "精炼且包含关键实体的剧情线索事实陈述",
      "source_reference": "本线索的主要出处，如《龙蛇藏归辑录》剧情对话",
      "source_genre": "official_duty_dialogue|ancient_chronicle_forbidden|folklore_adapted_fiction|weapon_lore_divine_oath|scholarly_conjecture|community_chronology|character_lore",
      "epistemic_status": "duty_testimony|ancient_official_record|folklore_oral_tradition|primary_divine_statement|scholarly_conjecture|community_synthesis|official_character_definition",
      "narrative_significance": "该线索在白夜国整体剧情叙事中的价值与定位",
      "reason": "为何新增此线索，或为何对前序线索进行补充/对照/修订的具体理由",
      "uncertainty_note": "若存在叙事限制、文学加工、民间流传或学者推测成分，在此客观说明（无则写 null）"
    }
  ],
  "unresolved_questions": [
    "当前仍未解开的剧情谜团或需要后续材料印证的问题"
  ],
  "updated_summary": "截止当前步骤的白夜国完整研究摘要（将新知与既有脉络有机融合）"
}"""


def load_runtime_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Load process environment with optional local .codex/.env fallback for API keys."""
    merged = dict(os.environ if env is None else env)
    codex_env = Path.home() / ".codex" / ".env"
    if codex_env.exists():
        for line in codex_env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip("'\"")
            if k not in merged:
                merged[k] = v
    return merged


def once(path: Path, data: Any) -> None:
    if path.exists():
        raise ExplorationError(f"file already exists: {path}")
    body = canonical_json_bytes(data) if not isinstance(data, (bytes, str)) else (
        data.encode("utf-8") if isinstance(data, str) else data
    )
    atomic_write(path, body)


def load_sections_from_sample(
    full_sections_path: Path,
    target_specs: Sequence[tuple[str, int]],
) -> list[dict[str, Any]]:
    """Load specific full sections from materialized full_sections.json."""
    data = json.loads(Path(full_sections_path).read_text(encoding="utf-8"))
    sections = data.get("sections", [])
    sec_map = {(s["content_id"], s["section_ordinal"]): s for s in sections}
    matched = []
    for cid, ord_num in target_specs:
        sec = sec_map.get((cid, ord_num))
        if not sec:
            raise ExplorationError(f"section not found in full_sections: {cid}:s{ord_num}")
        matched.append(sec)
    return matched


def build_step_input(
    step: int,
    step_spec: Mapping[str, Any],
    sections: Sequence[Mapping[str, Any]],
    prev_state: ContextContinuityState | None,
    unresolved_questions: Sequence[str] | None,
) -> dict[str, Any]:
    """Build the clean model input payload containing current materials and continuous context."""
    materials_summary = []
    for s in sections:
        materials_summary.append({
            "section_id": s["section_id"],
            "content_id": s["content_id"],
            "record_title": s["record_title"],
            "section_ordinal": s["section_ordinal"],
            "section_name": s["section_name"],
            "char_count": s["char_count"],
            "source_genre": step_spec["source_genre"],
            "epistemic_status": step_spec["epistemic_status"],
            "full_text": s["full_text"],
        })

    prev_summary = prev_state.research_summary if prev_state else "无（本步为初始研究起点）"
    active_clues_payload = []
    if prev_state:
        for c in prev_state.derive_active_clues():
            active_clues_payload.append({
                "clue_id": c.clue_id,
                "text": c.text,
                "source_genre": c.source_genre,
                "epistemic_status": c.epistemic_status,
                "narrative_significance": c.narrative_significance,
                "uncertainty_note": c.uncertainty_note,
                "status": c.status,
            })

    ledger_payload = []
    if prev_state:
        for e in prev_state.source_ledger:
            ledger_payload.append(e.to_dict())

    unresolved = list(unresolved_questions or [
        "白夜国（渊下宫）究竟从何而来，大日御舆由谁建造并遭遇何等变故？",
        "大蛇奥罗巴斯为何进入海渊，与海渊之民立下了何种誓约？",
        "失窃的《日月前事》等古籍记录了怎样的原初历史与秘密？",
        "白夜国先民是如何迁徙至海祇岛的，为何古老编年史难见天日？",
    ])

    return {
        "step": step,
        "doc_title": step_spec["title"],
        "doc_role_description": step_spec["doc_role_description"],
        "current_materials": materials_summary,
        "previous_research_summary": prev_summary,
        "current_active_clues": active_clues_payload,
        "read_sources_ledger": ledger_payload,
        "unresolved_questions_and_revision_targets": unresolved,
    }


DEFAULT_COMPONENT_TO_RU: dict[str, str] = {
    "2403:s13:u1": "c89d23c79d89c7d2a1dfe03abdaa7adc8b340404d48e2bc31bd6eeeb8428b3f5",
    "3226:s0:u0": "6fb5641569df1013c0bda1ded11d69ba82e2506b9493d12ee09255c3220b77ed",
    "3226:s1:u0": "5ad405b18631565905776b3bf974db2a84d1360eea3881882058bd05a42c909e",
    "3226:s1:u1": "0e59547688f1e8f64e695165d6b0f70a0965914de8b12f6de45cb6647694c5b6",
    "3227:s1:u1": "e9f3aa8de26fb76700477f818275f093b544c8cf28e9a60d1dcd6db7d8caea13",
    "3429:s7:u3": "d9fed6511005d773cdb702580731656454c0f6d40685451dfe11ddbb1b45cfb8",
    "3470:s1:u1": "bb1b9f9614c358ec0e0650e1ca12afa90fc92efa61c75a98d3b7b39dc7b79707",
    "3470:s2:u1": "6961332b1b0f292b1896fb9f72a8ffb844ddbf144bee2e1fc8b841f30d6f138b",
    "3470:s3:u2": "834db7c2ae4682bc2c2909a1a0acd56629d67bc4b20f43bba9cf6b8fe660aeac",
    "3470:s4:u1": "ffdaf5bfbc41b755658f3b14db7c109075066eadd484e506860db4415138c3c7",
    "3611:s3:u1": "05e788aceff16128d03709e10a04807e103771d6f9a013da4eec8112e82aa4d6",
    "508883:s4:u0": "fbd839c6d20c6935cef11aa901504367d6db90e6674012af8a04bede8995f15b",
    "508883:s4:u3": "bff4d246165f8d5dc7f7ee8b7dd7c8d46e48f7b0bc93b6c96d71b7eda92ac281",
    "508883:s4:u4": "7ceb1023dcbed8fdc8c355d53eb7e1275ba456d3a6ee73da83ed2f856c7215f4",
    "508883:s4:u5": "6be0d75b766fc5a89849736012bfb9a23700f29e744f782669d1b661c0ca255d",
    "508883:s10:u1": "cadf35a74a452a81933b0a186850a77d8901869c8684de9592299d23b90e6105",
}


def split_source_reference(ref_str: str) -> list[str]:
    """Split compound source references into distinct source segments."""
    text = (ref_str or "").strip()
    if not text:
        return []
    # Normalize Chinese separators where 与 links two work titles
    text = text.replace("》与《", "》；《").replace("与《", "；《")
    if "；" in text:
        return [p.strip() for p in text.split("；") if p.strip()]
    if ";" in text:
        return [p.strip() for p in text.split(";") if p.strip()]
    return [text]


def resolve_source_segment(
    seg: str,
    clue_text: str = "",
    comp_to_ru: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve a single source reference segment into Section ID, component key, and production RU ID."""
    seg = (seg or "").strip()
    ru_map = dict(DEFAULT_COMPONENT_TO_RU)
    if comp_to_ru:
        ru_map.update(comp_to_ru)

    res: dict[str, Any] = {
        "source_segment": seg,
        "section_id": None,
        "local_component_key": None,
        "production_ru_id": None,
        "resolution_status": "unresolved",
        "notes": "",
    }
    if not seg:
        return res

    # 1. 2403 珊瑚宫心海 (角色详细)
    if "珊瑚宫心海" in seg or "角色详细" in seg:
        res["section_id"] = "2403:s13"
        res["local_component_key"] = "2403:s13:u1"
        res["production_ru_id"] = ru_map.get("2403:s13:u1")
        res["resolution_status"] = "resolved"
        res["notes"] = "Matched 珊瑚宫心海角色详细"
        return res

    # 2. 508883 白夜国百科
    if "508883" in seg or "百科条目" in seg or "《白夜国》" in seg:
        if "其他文本中提到的人" in seg:
            res["section_id"] = "508883:s10"
            res["local_component_key"] = "508883:s10:u1"
            res["production_ru_id"] = ru_map.get("508883:s10:u1")
            res["resolution_status"] = "resolved"
            res["notes"] = "Matched 百科条目其他文本中提到的人"
            return res
        elif "重要事件" in seg:
            res["section_id"] = "508883:s4"
            if "奥罗巴斯" in seg:
                res["local_component_key"] = "508883:s4:u3"
                res["production_ru_id"] = ru_map.get("508883:s4:u3")
                res["resolution_status"] = "resolved"
                res["notes"] = "Matched 重要事件·奥罗巴斯 (u3)"
            elif "塑造海祇岛" in seg or "海祇岛" in seg:
                res["local_component_key"] = "508883:s4:u4"
                res["production_ru_id"] = ru_map.get("508883:s4:u4")
                res["resolution_status"] = "resolved"
                res["notes"] = "Matched 重要事件·塑造海祇岛 (u4)"
            elif "改造文化" in seg:
                res["local_component_key"] = "508883:s4:u5"
                res["production_ru_id"] = ru_map.get("508883:s4:u5")
                res["resolution_status"] = "resolved"
                res["notes"] = "Matched 重要事件·改造文化 (u5)"
            elif "离可琉" in seg:
                res["local_component_key"] = "508883:s4:u2"
                res["production_ru_id"] = None
                res["resolution_status"] = "section_only"
                res["notes"] = "Matched 重要事件·离可琉 (Canonical u2; not in production RU index)"
            else:
                res["local_component_key"] = None
                res["production_ru_id"] = None
                res["resolution_status"] = "section_only"
                res["notes"] = "Matched 重要事件 (section level)"
            return res

    # 3. 3227 珊瑚宫民间信仰初勘
    if "3227" in seg or "民间信仰初勘" in seg or "珊瑚宫民间信仰" in seg:
        res["section_id"] = "3227:s1"
        res["local_component_key"] = "3227:s1:u1"
        res["production_ru_id"] = ru_map.get("3227:s1:u1")
        res["resolution_status"] = "resolved"
        res["notes"] = "Matched 《珊瑚宫民间信仰初勘》"
        return res

    # 4. 3611 证誓之明瞳
    if "3611" in seg or "证誓之明瞳" in seg:
        res["section_id"] = "3611:s3"
        res["local_component_key"] = "3611:s3:u1"
        res["production_ru_id"] = ru_map.get("3611:s3:u1")
        res["resolution_status"] = "resolved"
        res["notes"] = "Matched 《证誓之明瞳》武器故事"
        return res

    # 5. 3226 常世国龙蛇传 (民间小说 / 资料页)
    if ("3226" in seg or "常世国龙蛇传" in seg) and ("资料页" in seg or "正文" in seg or "小说" in seg or "八重堂" in seg):
        if "资料页" in seg:
            res["section_id"] = "3226:s0"
            res["local_component_key"] = "3226:s0:u0"
            res["production_ru_id"] = ru_map.get("3226:s0:u0")
            res["resolution_status"] = "resolved"
            res["notes"] = "Matched 《常世国龙蛇传》八重堂资料页"
            return res
        elif "正文" in seg:
            res["section_id"] = "3226:s1"
            res["local_component_key"] = "3226:s1:u1"
            res["production_ru_id"] = ru_map.get("3226:s1:u1")
            res["resolution_status"] = "resolved"
            res["notes"] = "Matched 《常世国龙蛇传》正文"
            return res

    # 6. 3470 白夜国馆藏
    if "3470" in seg or "白夜国馆藏" in seg or "日月前事" in seg or "水文考" in seg or "地理水文考" in seg or ("常世国龙蛇传" in seg and "第一卷" in seg):
        if "日月前事" in seg:
            res["section_id"] = "3470:s2"
            res["local_component_key"] = "3470:s2:u1"
            res["production_ru_id"] = ru_map.get("3470:s2:u1")
            res["resolution_status"] = "resolved"
            res["notes"] = "Matched 《日月前事》第二卷"
            return res
        elif "常世国龙蛇传" in seg and ("第一卷" in seg or "馆藏" in seg):
            res["section_id"] = "3470:s1"
            res["local_component_key"] = "3470:s1:u1"
            res["production_ru_id"] = ru_map.get("3470:s1:u1")
            res["resolution_status"] = "resolved"
            res["notes"] = "Matched 《常世国龙蛇传》第一卷 (馆藏古本)"
            return res
        elif "描述" in seg or "第三卷描述" in seg or "简介" in seg:
            res["section_id"] = "3470:s3"
            res["local_component_key"] = "3470:s3:u2"
            res["production_ru_id"] = ru_map.get("3470:s3:u2")
            res["resolution_status"] = "resolved"
            res["notes"] = "Matched 《白夜国水文考》馆藏描述"
            return res
        elif "水文考" in seg or "地理水文考" in seg:
            res["section_id"] = "3470:s4"
            res["local_component_key"] = "3470:s4:u1"
            res["production_ru_id"] = ru_map.get("3470:s4:u1")
            res["resolution_status"] = "resolved"
            res["notes"] = "Matched 《白夜国地理水文考》正文"
            return res

    # 7. 3429 龙蛇藏归辑录
    if "3429" in seg or "龙蛇藏归辑录" in seg or "绘真" in seg:
        res["section_id"] = "3429:s7"
        if any(k in clue_text for k in ["五本", "被盗", "安贞", "悬空牢", "封面", "统一封面", "文字"]):
            res["local_component_key"] = "3429:s7:u3"
            res["production_ru_id"] = ru_map.get("3429:s7:u3")
            res["resolution_status"] = "resolved"
            res["notes"] = "Matched 龙蛇藏归辑录剧情对话 (u3: 绘真通报失书与验书)"
        elif any(k in clue_text for k in ["常世之灵", "常世大神", "搬迁至海平面", "最后一日", "海渊灵草", "生鱼料理"]):
            res["local_component_key"] = "3429:s7:u2"
            res["production_ru_id"] = None
            res["resolution_status"] = "section_only"
            res["notes"] = "Matched 龙蛇藏归辑录剧情对话 (u2: 绘真最后一日残影/愿景; not in production RU index)"
        else:
            res["local_component_key"] = None
            res["production_ru_id"] = None
            res["resolution_status"] = "section_only"
            res["notes"] = "Matched 龙蛇藏归辑录剧情对话 (section level)"
        return res

    return res


def resolve_clue_sources(
    source_reference: str,
    clue_text: str = "",
    comp_to_ru: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve full clue source reference into structured section, component, and RU bindings."""
    segments = split_source_reference(source_reference)
    resolved_segs = [resolve_source_segment(seg, clue_text, comp_to_ru) for seg in segments]

    section_ids = [s["section_id"] for s in resolved_segs if s["section_id"]]
    component_keys = [s["local_component_key"] for s in resolved_segs if s["local_component_key"]]
    production_rus = [s["production_ru_id"] for s in resolved_segs if s["production_ru_id"]]

    if all(s["resolution_status"] == "resolved" for s in resolved_segs):
        overall = "resolved"
    elif any(s["resolution_status"] in ("resolved", "section_only") for s in resolved_segs):
        overall = "section_only" if not production_rus else "partially_resolved"
    else:
        overall = "unresolved"

    return {
        "source_reference": source_reference,
        "resolved_segments": resolved_segs,
        "section_ids": section_ids,
        "local_component_keys": component_keys,
        "production_ru_ids": production_rus,
        "overall_status": overall,
    }


def parse_model_output(
    raw_text: str,
    step: int,
    step_spec: Mapping[str, Any],
    sections: Sequence[Mapping[str, Any]],
    prev_state: ContextContinuityState | None,
) -> tuple[dict[str, Any], ContextContinuityState]:
    """Parse raw JSON output from model, validate clue transitions, and construct updated state."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ExplorationError(f"Model output is not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ExplorationError("Model output JSON must be an object")

    step_understanding = parsed.get("step_understanding", "")
    clue_updates = parsed.get("clue_updates", [])
    unresolved_questions = parsed.get("unresolved_questions", [])
    updated_summary = parsed.get("updated_summary", "")

    if not isinstance(step_understanding, str) or not step_understanding.strip():
        step_understanding = f"Step {step} understanding extracted from {step_spec['title']}."
    if not isinstance(updated_summary, str) or not updated_summary.strip():
        updated_summary = step_understanding

    # Collect known clue IDs so far
    prev_clues_map = {c.clue_id: c for c in prev_state.derive_active_clues()} if prev_state else {}
    known_clue_ids = set()
    if prev_state:
        for ev in prev_state.clue_events:
            known_clue_ids.add(ev.clue.clue_id)

    # Unit IDs for current step
    section_unit_ids = []
    for s in sections:
        # Use deterministic unit id synthesis based on section id if unit hashes are not present
        sid = s["section_id"]
        for u in s.get("units", []):
            section_unit_ids.append(f"{sid}:u{u['ordinal']}")
        if not s.get("units"):
            section_unit_ids.append(f"{sid}:u0")

    clue_events = list(prev_state.clue_events) if prev_state else []
    step_events_count = 0

    for idx, item in enumerate(clue_updates):
        if not isinstance(item, dict):
            continue
        op = str(item.get("operation", "add")).lower()
        if op not in VALID_OPERATIONS:
            op = "add"

        cid = str(item.get("clue_id", f"clue_step{step}_{idx+1}"))
        if cid in known_clue_ids:
            cid = f"{cid}_s{step}_{idx+1}"
        known_clue_ids.add(cid)

        target_cid = item.get("target_clue_id")
        if target_cid is not None:
            target_cid = str(target_cid).strip()
            if target_cid not in prev_clues_map:
                # If target not found in active clues, fall back to add to prevent corruption
                op = "add"
                target_cid = None

        if op == "add":
            target_cid = None

        genre = str(item.get("source_genre", step_spec["source_genre"]))
        if genre not in VALID_GENRES:
            genre = step_spec["source_genre"]

        epistemic = str(item.get("epistemic_status", step_spec["epistemic_status"]))
        if epistemic not in VALID_EPISTEMIC:
            epistemic = step_spec["epistemic_status"]

        text = str(item.get("text", "")).strip()
        if not text:
            continue

        sig = str(item.get("narrative_significance", f"Step {step} clue from {step_spec['title']}")).strip()
        unc = item.get("uncertainty_note")
        unc_str = str(unc).strip() if unc else None

        reason = str(item.get("reason", f"Discovered in step {step} from {step_spec['title']}")).strip()
        if op in {"supplement", "contrast", "revise", "supersede"} and not reason:
            reason = f"Step {step} correlation with {target_cid}"

        # Resolve source reference deterministically, falling back to section-level rather than arbitrary component binding
        source_ref = str(item.get("source_reference", "")).strip()
        resolved_src = resolve_clue_sources(source_ref, text)
        if resolved_src["production_ru_ids"]:
            uids = tuple(resolved_src["production_ru_ids"])
        elif resolved_src["local_component_keys"]:
            uids = tuple(resolved_src["local_component_keys"])
        elif resolved_src["section_ids"]:
            uids = tuple(resolved_src["section_ids"])
        elif len(sections) == 1:
            uids = (f"{sections[0]['section_id']}:u0",)
        elif sections:
            uids = tuple(s["section_id"] for s in sections)
        else:
            uids = ("unresolved",)

        entry = ClueEntry(
            clue_id=cid,
            text=text,
            source_unit_ids=uids,
            source_genre=genre,
            epistemic_status=epistemic,
            narrative_significance=sig,
            uncertainty_note=unc_str,
        )

        ev = ClueEvent(
            event_id=f"ev:s{step}:{idx+1}",
            step=step,
            operation=op,
            clue=entry,
            target_clue_id=target_cid,
            reason=reason if op != "add" else None,
        )
        clue_events.append(ev)
        step_events_count += 1

    # Update source ledger
    ledger = list(prev_state.source_ledger) if prev_state else []
    existing_cids = {e.content_id for e in ledger}
    cid = step_spec["content_id"]
    for s in sections:
        sid = s["section_id"]
        sec_name = s["section_name"]
        primary_uid = f"{sid}:u0"
        if not any(e.unit_id == primary_uid for e in ledger):
            entry = SourceLedgerEntry(
                unit_id=primary_uid,
                content_id=cid,
                record_title=s["record_title"],
                section_name=sec_name,
                scope_level="full_section",
                source_genre=step_spec["source_genre"],
                read_count=1,
                first_step=step,
                last_step=step,
            )
            ledger.append(entry)

    updated_state = ContextContinuityState(
        step=step,
        research_summary=updated_summary,
        source_ledger=tuple(ledger),
        clue_events=tuple(clue_events),
    )
    # Validate integrity
    updated_state.derive_active_clues()

    return parsed, updated_state


class ContinuitySDK:
    """Dispatches a single step request using native Responses API with full stream audit."""

    def __init__(
        self,
        *,
        model: str = "deepseek-v4.1-flash",
        route: str = "jizhi",
        transport: httpx.BaseTransport | None = None,
        env: Mapping[str, str] | None = None,
    ):
        self.env = load_runtime_env(env)
        self.profile = e2_route(model, route, self.env, offline=transport is not None)
        self.transport = transport

    def dispatch(
        self,
        step_dir: Path,
        body: Mapping[str, Any],
        run_identity: str,
    ) -> dict[str, Any]:
        """Dispatch exactly 1 attempt; no auto retry, full audit logs."""
        step_dir = Path(step_dir)
        step_dir.mkdir(parents=True, exist_ok=True)
        key = self.env[self.profile.api_key_env]

        once(step_dir / "request.json", dict(body))

        observation = {"calls": 0, "http_status": None, "headers": {}}
        started = time.monotonic()
        content: list[str] = []
        reasoning: list[str] = []
        usage: Any = None
        finish: str | None = None
        decision_text = _ResponsesDecisionText()
        response_terminal = False
        response_status: str | None = None
        response_id: str | None = None
        returned_models: set[str] = set()
        error: Any = None

        writer = _JsonlArtifactWriter(step_dir / "stream.jsonl", step_dir.parent)

        def sensitive(val: str) -> bool:
            return bool(key and key in val)

        def redact(val: str) -> str:
            return val.replace(key, "[REDACTED]") if key else val

        def request_hook(req: httpx.Request) -> None:
            observation["calls"] += 1
            if observation["calls"] != 1:
                raise ExplorationError("duplicate wire dispatch detected")
            wire_body = json.loads(req.content)
            once(step_dir / "issued.json", {
                "run_identity": run_identity,
                "request_identity": sha256_json(dict(body)),
                "issued_at": datetime.now(timezone.utc).isoformat(),
            })
            once(step_dir / "wire-request.json", wire_body)
            once(step_dir / "request-metadata.json", {
                "url": str(req.url),
                "method": req.method,
                "headers": {n: req.headers[n] for n in ("content-type", "accept") if n in req.headers},
            })

        def response_hook(res: httpx.Response) -> None:
            observation["http_status"] = res.status_code
            observation["headers"] = {
                n: redact(res.headers[n])
                for n in ("retry-after", "x-request-id", "content-type") if n in res.headers
            }
            if res.status_code != 200:
                raw = res.read()
                if sensitive(raw.decode("utf-8", errors="replace")):
                    raise ExplorationError("credential echo in error response")
                atomic_write(step_dir / "raw-error.bin", raw)

        try:
            with httpx.Client(
                transport=self.transport,
                event_hooks={"request": [request_hook], "response": [response_hook]},
            ) as http_client:
                with openai.OpenAI(
                    base_url=self.profile.base_url,
                    api_key=key,
                    max_retries=0,
                    timeout=180,
                    http_client=http_client,
                ) as client:
                    for chunk in client.responses.create(**body):
                        data = _chunk_mapping(chunk)
                        if sensitive(json.dumps(data)):
                            raise ExplorationError("credential echo in stream chunk")
                        writer.append(data)
                        decision_text.observe(data)
                        resp = data.get("response") if isinstance(data.get("response"), Mapping) else {}
                        if isinstance(resp.get("model"), str):
                            returned_models.add(resp["model"])

                        value, thought, event_finish, event_usage, event_id, is_term = _responses_event_parts(data)
                        if value:
                            content.append(value)
                        if thought:
                            reasoning.append(thought)
                        if event_finish is not None:
                            finish = event_finish
                        if is_term:
                            usage = event_usage
                            response_terminal = True
                            response_status = resp.get("status")
                            response_id = event_id
        except Exception as exc:
            error = {"type": type(exc).__name__, "message": redact(str(exc))[:1500]}

        visible_text = "".join(content)
        if response_terminal and error is None:
            try:
                visible_text = decision_text.text()
            except ValueError as exc:
                error = {"type": "ResponsesDecisionTextError", "message": str(exc)}

        if isinstance(usage, Mapping):
            usage = dict(usage)
            inp = usage.get("input_tokens", usage.get("prompt_tokens"))
            out = usage.get("output_tokens", usage.get("completion_tokens"))
            if "total_tokens" not in usage and isinstance(inp, int) and isinstance(out, int):
                usage["total_tokens"] = inp + out

        complete = bool(response_terminal and finish == "stop" and error is None and visible_text.strip())
        terminal = {
            "run_identity": run_identity,
            "calls": observation["calls"],
            "http_status": observation["http_status"],
            "finish_reason": finish,
            "complete": complete,
            "error": error,
            "execution_state": "RESOLVED" if response_terminal else "EXECUTION UNKNOWN",
            "logical_model": self.profile.logical_model,
            "route": self.profile.route,
            "returned_models": sorted(returned_models),
            "response_id": response_id,
            "response_status": response_status,
            "content": visible_text,
            "reasoning": "".join(reasoning),
            "usage": usage or "UNKNOWN",
            "usage_normalized": _normalize_usage(usage),
            "latency_ms": round((time.monotonic() - started) * 1000, 3),
            "actual_cost": "UNKNOWN",
            "currency": "UNKNOWN",
            "stream": writer.descriptor(),
        }
        terminal["terminal_identity"] = sha256_json(terminal)
        once(step_dir / "terminal.json", terminal)

        # Also write uncompressed model_output_raw.txt for clarity
        atomic_write(step_dir / "model_output_raw.txt", visible_text.encode("utf-8"))

        return terminal


def run_byakuyakoku_continuity_experiment(
    *,
    output_root: Path,
    full_sections_path: Path = Path("data/retrieval/p05-w2-byakuyakoku-samples/full_sections.json"),
    model: str = "deepseek-v4.1-flash",
    route: str = "jizhi",
    max_requests: int = 7,
    token_ceiling: int = 100000,
    sdk: ContinuitySDK | None = None,
    custom_steps: Sequence[Mapping[str, Any]] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Execute the sequential continuous understanding experiment across 7 steps."""
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    steps_to_run = custom_steps or DOC_STEPS
    if len(steps_to_run) > max_requests:
        raise ExplorationError(f"steps count {len(steps_to_run)} exceeds max_requests {max_requests}")

    config = {
        "experiment": "p05_byakuyakoku_continuity",
        "schema_version": "phase05-byakuyakoku-continuity-0.1",
        "model": model,
        "route": route,
        "max_requests": max_requests,
        "token_ceiling": token_ceiling,
        "total_steps": len(steps_to_run),
        "steps_plan": [
            {
                "step": s["step"],
                "content_id": s["content_id"],
                "title": s["title"],
                "section_ordinals": list(s["section_ordinals"]),
                "source_genre": s["source_genre"],
                "epistemic_status": s["epistemic_status"],
            }
            for s in steps_to_run
        ],
    }
    run_identity = sha256_json(config)
    once(root / "config.json", config)

    if sdk is None:
        sdk = ContinuitySDK(model=model, route=route, env=environment)

    cumulative_tokens = 0
    total_calls = 0
    step_records = []
    prev_state: ContextContinuityState | None = None
    unresolved_questions: list[str] | None = None
    terminal_status = "completed"
    failure_detail: dict[str, Any] | None = None

    for step_spec in steps_to_run:
        step_num = step_spec["step"]
        step_dir = root / f"steps/step_{step_num:02d}"

        # 1. Budget pre-check
        if total_calls >= max_requests:
            terminal_status = "stopped_request_limit"
            break
        if cumulative_tokens >= token_ceiling:
            terminal_status = "stopped_budget_reached"
            break

        # 2. Load authentic full sections for this step
        target_specs = [(step_spec["content_id"], ord_num) for ord_num in step_spec["section_ordinals"]]
        sections = load_sections_from_sample(full_sections_path, target_specs)

        # 3. Build step input
        step_input = build_step_input(
            step=step_num,
            step_spec=step_spec,
            sections=sections,
            prev_state=prev_state,
            unresolved_questions=unresolved_questions,
        )

        input_json_str = canonical_json_bytes(step_input).decode("utf-8")
        est_tokens = len(input_json_str) // 2
        if cumulative_tokens + est_tokens > token_ceiling:
            terminal_status = "stopped_budget_reached"
            failure_detail = {
                "step": step_num,
                "reason": f"Estimated input tokens ({est_tokens}) + used ({cumulative_tokens}) would exceed ceiling ({token_ceiling})",
            }
            break

        body = {
            "model": model,
            "instructions": CONTINUITY_SYSTEM_PROMPT,
            "input": input_json_str,
            "stream": True,
            "max_output_tokens": 4096,
        }

        # 4. Dispatch request
        total_calls += 1
        terminal = sdk.dispatch(step_dir, body, run_identity)

        usage = terminal.get("usage", {})
        used_step_tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
        if isinstance(used_step_tokens, int) and not isinstance(used_step_tokens, bool):
            cumulative_tokens += used_step_tokens

        if not terminal.get("complete") or terminal.get("error"):
            terminal_status = "partial_error"
            failure_detail = {
                "step": step_num,
                "error": terminal.get("error"),
                "http_status": terminal.get("http_status"),
                "execution_state": terminal.get("execution_state"),
            }
            break

        # 5. Parse output and update continuity state
        raw_text = terminal.get("content", "")
        try:
            parsed, updated_state = parse_model_output(
                raw_text=raw_text,
                step=step_num,
                step_spec=step_spec,
                sections=sections,
                prev_state=prev_state,
            )
        except Exception as exc:
            terminal_status = "partial_parse_error"
            failure_detail = {
                "step": step_num,
                "parse_error": str(exc),
                "raw_text_prefix": raw_text[:500],
            }
            break

        # Persist step artifacts
        once(step_dir / "parsed_output.json", parsed)
        once(step_dir / "step_continuity_state.json", updated_state.to_dict())

        step_records.append({
            "step": step_num,
            "title": step_spec["title"],
            "content_id": step_spec["content_id"],
            "sections_count": len(sections),
            "step_tokens": used_step_tokens,
            "cumulative_tokens": cumulative_tokens,
            "active_clues_count": len(updated_state.derive_active_clues()),
            "clue_events_count": len(updated_state.clue_events),
            "latency_ms": terminal.get("latency_ms"),
        })

        prev_state = updated_state
        unresolved_questions = parsed.get("unresolved_questions")

    # 6. Final artifacts
    session = {
        "run_identity": run_identity,
        "experiment": "p05_byakuyakoku_continuity",
        "status": terminal_status,
        "completed_steps": len(step_records),
        "total_steps": len(steps_to_run),
        "total_provider_calls": total_calls,
        "known_total_tokens": cumulative_tokens,
        "token_ceiling": token_ceiling,
        "failure_detail": failure_detail,
        "step_records": step_records,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    session["session_identity"] = sha256_json(session)
    once(root / "session.json", session)

    if prev_state:
        final_state_dict = prev_state.to_dict()
        once(root / "final_continuity_state.json", final_state_dict)

    return {
        "run_identity": run_identity,
        "status": terminal_status,
        "total_calls": total_calls,
        "cumulative_tokens": cumulative_tokens,
        "step_records": step_records,
        "output_root": str(root),
        "final_state": prev_state.to_dict() if prev_state else None,
    }


def generate_derived_continuity_artifacts(
    run_root: Path | str,
    comp_to_ru: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate derived_source_index.json and derived_continuity_state.json from experiment run root.

    Preserves original final_continuity_state.json and steps/* intact.
    Differentiates:
    - Section ID (e.g. 3470:s4)
    - Local chapter component key (e.g. 3470:s4:u1)
    - Production retrieval RU ID (e.g. ffdaf5bfbc41...)
    """
    root = Path(run_root).resolve()
    final_path = root / "final_continuity_state.json"
    if not final_path.exists():
        raise ExplorationError(f"final_continuity_state.json not found in {root}")

    orig_state_dict = json.loads(final_path.read_text(encoding="utf-8"))
    config_dict = json.loads((root / "config.json").read_text(encoding="utf-8")) if (root / "config.json").exists() else {}

    new_events = []
    source_index_clues = []

    for ev_dict in orig_state_dict.get("clue_events", []):
        clue_dict = ev_dict["clue"]
        cid = clue_dict["clue_id"]
        step = ev_dict["step"]

        step_parsed_path = root / f"steps/step_{step:02d}/parsed_output.json"
        matched_clue = None
        if step_parsed_path.exists():
            step_parsed = json.loads(step_parsed_path.read_text(encoding="utf-8"))
            matched_clue = next((c for c in step_parsed.get("clue_updates", []) if c.get("clue_id") == cid), None)

        ref = matched_clue.get("source_reference", "") if matched_clue else ""
        resolved = resolve_clue_sources(ref, clue_dict.get("text", ""), comp_to_ru=comp_to_ru)

        if resolved["production_ru_ids"]:
            uids = tuple(resolved["production_ru_ids"])
        elif resolved["local_component_keys"]:
            uids = tuple(resolved["local_component_keys"])
        elif resolved["section_ids"]:
            uids = tuple(resolved["section_ids"])
        else:
            uids = ("unresolved",)

        resolved_item = dict(resolved)
        resolved_item["clue_id"] = cid
        resolved_item["step"] = step
        resolved_item["assigned_source_unit_ids"] = list(uids)
        source_index_clues.append(resolved_item)

        updated_clue = ClueEntry(
            clue_id=cid,
            text=clue_dict["text"],
            source_unit_ids=uids,
            source_genre=clue_dict["source_genre"],
            epistemic_status=clue_dict["epistemic_status"],
            narrative_significance=clue_dict["narrative_significance"],
            uncertainty_note=clue_dict.get("uncertainty_note"),
            status=clue_dict.get("status", "active"),
        )

        updated_event = ClueEvent(
            event_id=ev_dict["event_id"],
            step=step,
            operation=ev_dict["operation"],
            clue=updated_clue,
            target_clue_id=ev_dict.get("target_clue_id"),
            reason=ev_dict.get("reason"),
        )
        new_events.append(updated_event)

    ledger_entries = [SourceLedgerEntry.from_dict(e) for e in orig_state_dict.get("source_ledger", [])]

    derived_state = ContextContinuityState(
        step=orig_state_dict["step"],
        research_summary=orig_state_dict["research_summary"],
        source_ledger=tuple(ledger_entries),
        clue_events=tuple(new_events),
    )
    # Validate integrity
    derived_state.derive_active_clues()

    status_counts = {}
    for item in source_index_clues:
        st = item.get("overall_status", "unresolved")
        status_counts[st] = status_counts.get(st, 0) + 1

    derived_index_dict = {
        "schema_version": "phase05-byakuyakoku-derived-source-index-0.1",
        "run_identity": config_dict.get("run_identity", orig_state_dict.get("run_identity", "")),
        "total_clues": len(source_index_clues),
        "status_summary": status_counts,
        "clues": source_index_clues,
    }

    derived_state_dict = derived_state.to_dict()

    derived_index_path = root / "derived_source_index.json"
    derived_state_path = root / "derived_continuity_state.json"

    with open(derived_index_path, "w", encoding="utf-8") as f:
        json.dump(derived_index_dict, f, ensure_ascii=False, indent=2)
    with open(derived_state_path, "w", encoding="utf-8") as f:
        json.dump(derived_state_dict, f, ensure_ascii=False, indent=2)

    return derived_index_dict, derived_state_dict

