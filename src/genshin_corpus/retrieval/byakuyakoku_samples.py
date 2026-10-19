"""Byakuyakoku Phase 05 deterministic sample materialization and local Navi/KG candidates.

Prepares:
1. 3-tier sample dataset: E2 read slices, full RUs, full Canonical sections.
2. E2 baseline findings and official verbatim excerpts.
3. Local Byakuyakoku KG candidates compiled via P05 SemanticBuild and build_views().
4. Local Byakuyakoku Navi 7-step lightweight context continuity state.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.retrieval.clue_state import (
    ClueEntry,
    ClueEvent,
    ContextContinuityState,
    SourceLedgerEntry,
)
from genshin_corpus.retrieval.retrieval_units import load_retrieval_units
from genshin_corpus.retrieval.semantic_compilation import (
    SemanticBuild,
    SemanticItem,
    SemanticStage,
    SourceBinding,
    build_views,
    write_semantic_build,
)


CORE_CONTENT_IDS = ("3429", "3470", "3226", "3611", "3227", "508883", "2403")

TARGET_SECTION_SPECS = (
    ("3429", 7, "剧情对话"),
    ("3470", 1, "常世国龙蛇传"),
    ("3470", 2, "日月前事"),
    ("3470", 3, "白夜国地理水文考 简介"),
    ("3470", 4, "白夜国地理水文考"),
    ("3226", 0, "资料描述"),
    ("3226", 1, "常世国龙蛇传"),
    ("3611", 3, "相关故事"),
    ("3227", 1, "珊瑚宫民间信仰初勘"),
    ("508883", 4, "重要事件"),
    ("508883", 10, "其他文本中提到的人"),
    ("2403", 13, "角色详细"),
)


def _get_canonical_unit_text(u: Mapping[str, Any]) -> str:
    val = u.get("value", {})
    if isinstance(val, dict):
        if "normalized_text" in val:
            return str(val["normalized_text"])
        d = val.get("decoded", {})
        if isinstance(d, dict):
            if "attr" in d and isinstance(d["attr"], list):
                lines = []
                for item in d["attr"]:
                    k = item.get("key", "")
                    v = " ".join(item.get("value", []))
                    lines.append(f"{k}: {v}")
                return "\n".join(lines)
            if "text" in d:
                return str(d["text"])
        if "raw" in val:
            return str(val["raw"])
    elif isinstance(val, str):
        return val
    return ""


def extract_e2_read_slices(e2_session_path: Path) -> list[dict[str, Any]]:
    """Extract the 16 unique RUs with the actual read window text seen during E2 runs."""
    e2_sess = json.loads(Path(e2_session_path).read_text(encoding="utf-8"))
    reads = e2_sess.get("read_sources", [])

    grouped: dict[str, dict[str, Any]] = {}
    for idx, r in enumerate(reads):
        uid = r["unit_id"]
        addr = r["source"]["canonical_address"]
        ctx = r["source"]["record_context"]
        cid = addr["source_identity_key"].split(":")[-1]
        text = r.get("ru_text", "")

        if uid not in grouped:
            grouped[uid] = {
                "unit_id": uid,
                "content_id": cid,
                "record_title": ctx.get("record_title", ""),
                "section_ordinal": addr["section_ordinal"],
                "section_name": ctx.get("section_name", ""),
                "canonical_unit_ordinal": addr["canonical_unit_ordinal"],
                "e2_ru_text": text,
                "e2_char_count": len(text),
                "e2_read_count": 1,
                "e2_read_indices": [idx],
                "e2_partial": bool(r.get("partial", False)),
                "e2_omitted_chars": int(r.get("omitted_chars", 0)),
                "canonical_address": addr,
                "lineage": r["source"].get("lineage", {}),
                "content_role": r["source"].get("content_role", {}),
                "sha256": sha256(text.encode("utf-8")).hexdigest(),
            }
        else:
            grouped[uid]["e2_read_count"] += 1
            grouped[uid]["e2_read_indices"].append(idx)
            # Retain maximum delivered text if multiple reads occurred
            if len(text) > grouped[uid]["e2_char_count"]:
                grouped[uid]["e2_ru_text"] = text
                grouped[uid]["e2_char_count"] = len(text)
                grouped[uid]["sha256"] = sha256(text.encode("utf-8")).hexdigest()

    result = list(grouped.values())
    result.sort(key=lambda r: (r["content_id"], r["section_ordinal"], r["canonical_unit_ordinal"]))
    return result


def extract_full_retrieval_units(ru_manifest_path: Path, unit_ids: Sequence[str]) -> list[dict[str, Any]]:
    """Load full authoritative RU texts from the production RU manifest."""
    _, rows = load_retrieval_units(Path(ru_manifest_path), selected_unit_ids=unit_ids)
    result = []
    for row in rows:
        source = row["source"]
        addr = source["canonical_address"]
        ctx = source["record_context"]
        cid = addr["source_identity_key"].split(":")[-1]
        text = row.get("retrieval_visible_text", "")
        result.append({
            "unit_id": row["unit_id"],
            "content_id": cid,
            "record_title": ctx.get("record_title", ""),
            "section_ordinal": addr["section_ordinal"],
            "section_name": ctx.get("section_name", ""),
            "canonical_unit_ordinal": addr["canonical_unit_ordinal"],
            "retrieval_visible_text": text,
            "char_count": len(text),
            "source": source,
            "canonical_address": addr,
            "lineage": source.get("lineage", {}),
            "content_role": source.get("content_role", {}),
            "retrieval_visible_text_sha256": sha256(text.encode("utf-8")).hexdigest(),
        })
    result.sort(key=lambda r: (r["content_id"], r["section_ordinal"], r["canonical_unit_ordinal"]))
    return result


def extract_full_sections(
    canonical_manifest_path: Path,
    target_sections: Sequence[tuple[str, int, str]] = TARGET_SECTION_SPECS,
) -> list[dict[str, Any]]:
    """Extract full Canonical sections for the target documents."""
    manifest = json.loads(Path(canonical_manifest_path).read_text(encoding="utf-8"))
    records_by_cid: dict[str, Mapping[str, Any]] = {}
    for r in manifest["records"]:
        key = r.get("parsed_identity_key", "")
        cid = key.split(":")[-1] if ":" in key else ""
        if cid in CORE_CONTENT_IDS:
            records_by_cid[cid] = json.loads(Path(r["canonical_record_path"]).read_text(encoding="utf-8"))

    result = []
    for cid, sec_ord, expected_name in target_sections:
        rec = records_by_cid.get(cid)
        if not rec:
            continue
        title = rec.get("record_metadata", {}).get("name", "")
        matching_sec = next((s for s in rec.get("sections", []) if s["ordinal"] == sec_ord), None)
        if not matching_sec:
            continue
        units = matching_sec.get("units", [])
        unit_details = []
        text_parts = []
        for u in units:
            utxt = _get_canonical_unit_text(u)
            text_parts.append(utxt)
            unit_details.append({
                "ordinal": u.get("ordinal"),
                "kind": u.get("kind"),
                "text": utxt,
                "char_count": len(utxt),
            })
        full_text = "\n\n".join(t for t in text_parts if t.strip())
        result.append({
            "section_id": f"{cid}:s{sec_ord}",
            "content_id": cid,
            "record_title": title,
            "section_ordinal": sec_ord,
            "section_name": matching_sec.get("source_metadata", {}).get("name", expected_name),
            "units_count": len(units),
            "char_count": len(full_text),
            "full_text": full_text,
            "units": unit_details,
            "sha256": sha256(full_text.encode("utf-8")).hexdigest(),
        })
    result.sort(key=lambda s: (s["content_id"], s["section_ordinal"]))
    return result


def build_e2_baseline_findings(e2_session_path: Path) -> dict[str, Any]:
    """Extract verbatim E2 model clues, final memory, and critical evaluation."""
    sess = json.loads(Path(e2_session_path).read_text(encoding="utf-8"))
    return {
        "e2_run_identity": sess.get("run_identity", ""),
        "checkpoint_identity": sess.get("checkpoint_identity", ""),
        "total_provider_calls": sess.get("provider_calls", 0),
        "accepted_decisions": sess.get("accepted_decisions", 0),
        "used_tokens": sess.get("used", {}).get("tokens", 0),
        "status": sess.get("status", ""),
        "verbatim_e2_clues": sess.get("pending_clues", []),
        "verbatim_e2_final_memory": sess.get("memory", {}),
        "methodology_evaluation": {
            "strengths": [
                "自主完成了跨7篇文献的网状检索，精准识别出绘真、日月前事、水文考、常世国龙蛇传、证誓之明瞳与民间信仰初勘的关联",
                "敏锐识别出《常世国龙蛇传》八重堂在售版为民间故事改编商业小说，与绘真对书封封面文字的质疑相呼应",
                "捕捉到神明第一人称死誓自述（《证誓之明瞳》）与海祇民间救赎传说之间的张力",
                "注意到了学者专著《珊瑚宫民间信仰初勘》中对封存史书的考证与'牺牲说'的主观推测性",
            ],
            "limitations": [
                "采用全称一刀切标签（unverified_hypothesis_not_fact），将小说、传说、古籍均压平为'低可信度/未证实'，忽视了神话故事保留早期宇宙观的真实剧情价值",
                "缺乏轻量级视角对照（contrast）机制，将不同述事立场的差异机械理解为潜在冲突，无法以并存互见的方式组织多重视角",
                "单轮探索在遇到截断和格式检查时容易过早终止，缺乏持续推进记忆与演化图谱的稳定机制",
            ],
        },
    }


def build_official_source_excerpts(
    full_rus: Sequence[Mapping[str, Any]],
    full_sections: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compile official verbatim excerpts mapped to the 6 story pillars."""
    return {
        "description": "白夜国核心剧情六大参考支柱的官方原文精选与出处映射",
        "pillars": {
            "pillar_1_aberaku_helios_and_sun_children": {
                "title": "阿倍良久造地下太阳与太阳之子傀儡政权",
                "primary_ru_ids": [
                    "0e59547688f1e8f64e695165d6b0f70a0965914de8b12f6de45cb6647694c5b6",
                    "fbd839c6d20c6935cef11aa901504367d6db90e6674012af8a04bede8995f15b",
                ],
                "primary_section_ids": ["3226:s1", "508883:s4"],
                "official_quotes": [
                    {
                        "source": "常世国龙蛇传 (3226:s1:u1)",
                        "text": "相传，阿倍良久被常世大神启开了智慧，因此才从不见太阳的渊下宫里掘出来了光。但是太阳之子嫉妒他的才华，把他囚禁直至他寿终。然而，太阳之子们却未曾想过，若非有他造出地下的太阳，哪会有自己呢。",
                    },
                    {
                        "source": "白夜国重要事件 (508883:s4:u0)",
                        "text": "第一任太阳之子为离可琉，他擅长建筑技艺，十分崇拜阿倍良久。然而，由于阿倍良久十分反对太阳之子的制度，在大日御舆建成后的第二年，权贵们营造了阿倍良久想要图谋王位的假象，离可琉为阿倍良久判下谋逆大罪，终生监禁于大日御舆之顶。",
                    },
                ],
            },
            "pillar_2_ema_lost_books_and_before_sun_and_moon": {
                "title": "绘真失书与《日月前事》禁书窃密案",
                "primary_ru_ids": [
                    "d9fed6511005d773cdb702580731656454c0f6d40685451dfe11ddbb1b45cfb8",
                    "6961332b1b0f292b1896fb9f72a8ffb844ddbf144bee2e1fc8b841f30d6f138b",
                ],
                "primary_section_ids": ["3429:s7", "3470:s2"],
                "official_quotes": [
                    {
                        "source": "龙蛇藏归辑录 (3429:s7:u3)",
                        "text": "绘真：目前没有追回的书分别是《常世国龙蛇传》、《白夜国水文考》、《光昼影底集》、宫地那波人的实验记录…\n绘真：啊，第五本丢失的书我们还是有点头绪的。\n绘真：《日月前事》这本书被盗，几个嫌疑人被关押起来了。就在大日御舆下面的悬空牢内。",
                    },
                    {
                        "source": "白夜国馆藏·日月前事 (3470:s2:u1)",
                        "text": "若无把一切按事实记写的胆量，哪里能成为常世大神的书记呢？\n我听到了门外盔甲的声音，我于此绝笔。",
                    },
                ],
            },
            "pillar_3_orobashi_flight_to_dark_sea": {
                "title": "奥罗巴斯败逃暗之外海与身负珊瑚",
                "primary_ru_ids": [
                    "05e788aceff16128d03709e10a04807e103771d6f9a013da4eec8112e82aa4d6",
                    "bff4d246165f8d5dc7f7ee8b7dd7c8d46e48f7b0bc93b6c96d71b7eda92ac281",
                ],
                "primary_section_ids": ["3611:s3", "508883:s4"],
                "official_quotes": [
                    {
                        "source": "证誓之明瞳 (3611:s3:u1)",
                        "text": "「我本就因无法胜过贵金之神与鸣神，才选择逃往未知之海。」\n「如果你们仍然期待着光明，那未来一定会再一次经历失去。」\n「我之身死并不足道，偷生之辱、渎名之耻——我已经受够。 」",
                    },
                    {
                        "source": "白夜国重要事件 (508883:s4:u3)",
                        "text": "在魔神战争当中失去所有的大蛇奥罗巴斯逃向暗之外海。在闯入暗之外海之时，他得到了礼赠，身上长出了珊瑚。因此对于深海的大蛇而言，身负珊瑚枝象征着力量；而失去珊瑚枝时，力量会相应地折损。后来，他在海渊当中，见到了深海中的弃民，决定引领这些人。",
                    },
                ],
            },
            "pillar_4_child_oath_and_serpent_eye": {
                "title": "小童寻花立誓与蛇瞳宝珠约定",
                "primary_ru_ids": [
                    "0e59547688f1e8f64e695165d6b0f70a0965914de8b12f6de45cb6647694c5b6",
                    "05e788aceff16128d03709e10a04807e103771d6f9a013da4eec8112e82aa4d6",
                ],
                "primary_section_ids": ["3226:s1", "3611:s3"],
                "official_quotes": [
                    {
                        "source": "常世国龙蛇传 (3226:s1:u1)",
                        "text": "「我乃渎身渎名之蛇神，虽有眷属百千，但所荫蔽之众已无一人。今日落入此界，与你相见，也算有缘。你虽非我民，但终是人子。有何愿望，但说无妨。」\n「试问，你能做我们渊下之民的神吗？」\n于是一人与一蛇，面对太阳之子的王权、境外的龙嗣侵攻，力挽狂澜之演义就此开幕。",
                    },
                    {
                        "source": "证誓之明瞳 (3611:s3:u1)",
                        "text": "巨蛇展示了一枚蛇瞳一般的宝珠，\n「那你便在此证誓明瞳之前立誓吧。」\n「我与珊瑚之眷属也是如此结盟的。」",
                    },
                ],
            },
            "pillar_5_coral_island_creation_and_migration": {
                "title": "珊瑚枝折断造岛与迁居海面",
                "primary_ru_ids": [
                    "7ceb1023dcbed8fdc8c355d53eb7e1275ba456d3a6ee73da83ed2f856c7215f4",
                    "e9f3aa8de26fb76700477f818275f093b544c8cf28e9a60d1dcd6db7d8caea13",
                    "c89d23c79d89c7d2a1dfe03abdaa7adc8b340404d48e2bc31bd6eeeb8428b3f5",
                ],
                "primary_section_ids": ["508883:s4", "3227:s1", "2403:s13"],
                "official_quotes": [
                    {
                        "source": "白夜国重要事件 (508883:s4:u4)",
                        "text": "传说大蛇在常夜的渊下国土中初次现身时，身上覆满了四色的珊瑚枝，散发着煌煌的荧光。它战胜了深海龙蜥、将身上的珊瑚枝折断，照亮海渊，并用珊瑚枝架起登高的阶梯，让它的子民们再次回到地面上，见到阳光。涡流盘旋，塑珊瑚而成海祇岛。它还留下一部分珊瑚玉枝镇压深海龙蜥。",
                    },
                    {
                        "source": "珊瑚宫心海 (2403:s13:u1)",
                        "text": "海祇岛的住民，曾生活在海底的渊下宫。\n魔神奥罗巴斯将他们带到地面，才有了海祇岛如今的文明。\n奥罗巴斯被雷神斩杀后，身体化作累累枯骨，怨念凝成崇神气息。",
                    },
                ],
            },
            "pillar_6_cultural_transformation_and_sealed_chronicles": {
                "title": "海祇文化改造与编年史封存海渊",
                "primary_ru_ids": [
                    "6be0d75b766fc5a89849736012bfb9a23700f29e744f782669d1b661c0ca255d",
                    "e9f3aa8de26fb76700477f818275f093b544c8cf28e9a60d1dcd6db7d8caea13",
                ],
                "primary_section_ids": ["508883:s4", "3227:s1"],
                "official_quotes": [
                    {
                        "source": "白夜国重要事件 (508883:s4:u5)",
                        "text": "海祇决心抹除海祇岛民众的过去。他下令将编年史封存在渊下宫之中，不得带出；又下令学习鸣神传统，改造海祇民众的姓氏。",
                    },
                    {
                        "source": "珊瑚宫民间信仰初勘 (3227:s1:u1)",
                        "text": "但是，这个斩海祇的传说故事还有另一个版本：\n曾经的海渊之民在渊下之时，拥有极为可靠的编年史。因为无日无夜，若不如此，就会忘记时间。但是这些都被大蛇下令封存于渊下宫之中，不得带出。\n曾经的海渊之民的名字甚至也不是如今的稻妻样式——如今的海祇民诸姓也是大蛇下令学习鸣神传统之后才出现的。",
                    },
                ],
            },
        },
    }


def build_byakuyakoku_kg_items(full_rus: Sequence[Mapping[str, Any]]) -> list[SemanticItem]:
    """Build deterministic local Byakuyakoku Knowledge Graph SemanticItems with verified bindings."""
    ru_map = {r["unit_id"]: r for r in full_rus}

    def b(uid: str) -> SourceBinding:
        r = ru_map[uid]
        return SourceBinding(
            unit_id=uid,
            canonical_address=r["canonical_address"],
            lineage=r["lineage"],
        )

    # Unit ID shortcuts
    u_dialogue = "d9fed6511005d773cdb702580731656454c0f6d40685451dfe11ddbb1b45cfb8"  # 3429:s7:u3 绘真
    u_sun_moon = "6961332b1b0f292b1896fb9f72a8ffb844ddbf144bee2e1fc8b841f30d6f138b"  # 3470:s2:u1 日月前事
    u_hydro = "ffdaf5bfbc41b755658f3b14db7c109075066eadd484e506860db4415138c3c7"     # 3470:s4:u1 水文考
    u_novel_text = "0e59547688f1e8f64e695165d6b0f70a0965914de8b12f6de45cb6647694c5b6" # 3226:s1:u1 龙蛇传正文
    u_novel_meta = "6fb5641569df1013c0bda1ded11d69ba82e2506b9493d12ee09255c3220b77ed" # 3226:s0:u0 龙蛇传描述
    u_oath = "05e788aceff16128d03709e10a04807e103771d6f9a013da4eec8112e82aa4d6"       # 3611:s3:u1 证誓故事
    u_treatise = "e9f3aa8de26fb76700477f818275f093b544c8cf28e9a60d1dcd6db7d8caea13"   # 3227:s1:u1 初勘
    u_timeline_0 = "fbd839c6d20c6935cef11aa901504367d6db90e6674012af8a04bede8995f15b" # 508883:s4:u0 黑暗与造日
    u_timeline_3 = "bff4d246165f8d5dc7f7ee8b7dd7c8d46e48f7b0bc93b6c96d71b7eda92ac281" # 508883:s4:u3 蛇神到来
    u_timeline_4 = "7ceb1023dcbed8fdc8c355d53eb7e1275ba456d3a6ee73da83ed2f856c7215f4" # 508883:s4:u4 折珊瑚枝
    u_timeline_5 = "6be0d75b766fc5a89849736012bfb9a23700f29e744f782669d1b661c0ca255d" # 508883:s4:u5 文化改造
    u_timeline_10 = "cadf35a74a452a81933b0a186850a77d8901869c8684de9592299d23b90e6105" # 508883:s10:u1 人物
    u_kokomi = "c89d23c79d89c7d2a1dfe03abdaa7adc8b340404d48e2bc31bd6eeeb8428b3f5"     # 2403:s13:u1 心海详细

    items: list[SemanticItem] = []

    # 1. Entities (Mentions with topic_path)
    items.append(SemanticItem(
        item_id="entity:orobashi",
        item_kind="mention",
        label="奥罗巴斯",
        source_bindings=(b(u_oath), b(u_timeline_3)),
        semantic_acceptance_status="curated_recorded",
        subject_ref="奥罗巴斯",
        topic_path=("白夜国", "神明与魔神"),
        qualifiers={"title": "海祇大御神", "alias": "远吕羽氏尊", "source_genre": "weapon_lore_divine_oath", "epistemic": "primary_divine_statement"},
    ))
    items.append(SemanticItem(
        item_id="entity:abaraku",
        item_kind="mention",
        label="阿倍良久",
        source_bindings=(b(u_novel_text), b(u_timeline_0)),
        semantic_acceptance_status="curated_recorded",
        subject_ref="阿倍良久",
        topic_path=("白夜国", "历史人物"),
        qualifiers={"title": "白夜国贤人", "achievement": "建造大日御舆", "source_genre": "ancient_chronicle_forbidden", "epistemic": "ancient_official_record"},
    ))
    items.append(SemanticItem(
        item_id="entity:ema",
        item_kind="mention",
        label="绘真",
        source_bindings=(b(u_dialogue), b(u_timeline_10)),
        semantic_acceptance_status="curated_recorded",
        subject_ref="绘真",
        topic_path=("白夜国", "历史人物"),
        qualifiers={"role": "末代图书管理员", "source_genre": "official_duty_dialogue", "epistemic": "duty_testimony"},
    ))
    items.append(SemanticItem(
        item_id="entity:kokomi",
        item_kind="mention",
        label="珊瑚宫心海",
        source_bindings=(b(u_kokomi),),
        semantic_acceptance_status="curated_recorded",
        subject_ref="珊瑚宫心海",
        topic_path=("海祇岛", "现任领袖"),
        qualifiers={"role": "现人神巫女", "source_genre": "character_lore", "epistemic": "official_character_definition"},
    ))
    items.append(SemanticItem(
        item_id="entity:sun_children",
        item_kind="mention",
        label="离可琉",
        source_bindings=(b(u_timeline_0), b(u_novel_text)),
        semantic_acceptance_status="curated_recorded",
        subject_ref="离可琉",
        topic_path=("白夜国", "政权"),
        qualifiers={"title": "初代太阳之子", "source_genre": "community_chronology", "epistemic": "community_synthesis"},
    ))
    items.append(SemanticItem(
        item_id="entity:vishap",
        item_kind="mention",
        label="深海龙蜥",
        source_bindings=(b(u_sun_moon), b(u_timeline_4)),
        semantic_acceptance_status="curated_recorded",
        subject_ref="深海龙蜥",
        topic_path=("白夜国", "原初生物"),
        qualifiers={"role": "海渊原初支配者", "source_genre": "ancient_chronicle_forbidden", "epistemic": "ancient_official_record"},
    ))
    items.append(SemanticItem(
        item_id="entity:helios",
        item_kind="mention",
        label="大日御舆",
        source_bindings=(b(u_timeline_0), b(u_novel_text)),
        semantic_acceptance_status="curated_recorded",
        subject_ref="大日御舆",
        topic_path=("白夜国", "古代遗迹"),
        qualifiers={"original_name": "赫利俄斯", "creator": "阿倍良久", "source_genre": "ancient_chronicle_forbidden", "epistemic": "ancient_official_record"},
    ))
    items.append(SemanticItem(
        item_id="entity:chronicle",
        item_kind="mention",
        label="渊下宫编年史",
        source_bindings=(b(u_timeline_5), b(u_treatise)),
        semantic_acceptance_status="curated_recorded",
        subject_ref="渊下宫编年史",
        topic_path=("白夜国", "历史典籍"),
        qualifiers={"fate": "被大蛇下令封存", "source_genre": "scholarly_conjecture", "epistemic": "scholarly_conjecture", "uncertainty": "学者初勘指明牺牲说属推测且记录两说"},
    ))

    # 2. Events (Historical anchor events)
    items.append(SemanticItem(
        item_id="event:build_helios",
        item_kind="event",
        label="建造大日御舆",
        source_bindings=(b(u_novel_text), b(u_timeline_0)),
        semantic_acceptance_status="curated_recorded",
        event_type="工程创举",
        participants=("阿倍良久",),
        topic_path=("白夜国", "历史事件"),
        qualifiers={"significance": "受常世大神启智，从不见太阳之深渊掘出光芒", "source_genre": "ancient_chronicle_forbidden", "epistemic": "ancient_official_record"},
    ))
    items.append(SemanticItem(
        item_id="event:imprison_abaraku",
        item_kind="event",
        label="囚禁阿倍良久",
        source_bindings=(b(u_timeline_0), b(u_novel_text)),
        semantic_acceptance_status="curated_recorded",
        event_type="政治迫害",
        participants=("离可琉", "阿倍良久"),
        topic_path=("白夜国", "历史事件"),
        qualifiers={"result": "贵族拥立幼帝，构陷阿倍良久谋逆致其终身监禁", "source_genre": "ancient_chronicle_forbidden", "epistemic": "ancient_official_record"},
    ))
    items.append(SemanticItem(
        item_id="event:ema_commissions_retrieval",
        item_kind="event",
        label="绘真通报失窃并委托追回馆藏",
        source_bindings=(b(u_dialogue), b(u_sun_moon)),
        semantic_acceptance_status="curated_recorded",
        event_type="通报与委托",
        participants=("绘真",),
        topic_path=("白夜国", "馆藏事件"),
        qualifiers={
            "duty_role": "末代图书管理员",
            "reported_loss": "《日月前事》等五部馆藏失窃",
            "suspects": "安贞等嫌疑人被关押于大日御舆悬空牢",
            "location": "大日御舆悬空牢",
            "source_genre": "official_duty_dialogue",
            "epistemic": "duty_testimony",
        },
    ))
    items.append(SemanticItem(
        item_id="event:orobashi_oath_with_child",
        item_kind="event",
        label="蛇神与小童立誓",
        source_bindings=(b(u_oath), b(u_novel_text)),
        semantic_acceptance_status="curated_recorded",
        event_type="神盟立约",
        participants=("奥罗巴斯",),
        topic_path=("白夜国", "神明盟约"),
        qualifiers={"relic": "证誓之明瞳", "motive": "败逃暗海履行天上之都誓言", "source_genre": "weapon_lore_divine_oath", "epistemic": "primary_divine_statement"},
    ))
    items.append(SemanticItem(
        item_id="event:create_watatsumi_island",
        item_kind="event",
        label="折珊瑚枝造海祇岛",
        source_bindings=(b(u_timeline_4), b(u_kokomi)),
        semantic_acceptance_status="curated_recorded",
        event_type="开辟家园",
        participants=("奥罗巴斯",),
        topic_path=("海祇岛", "创世传说"),
        qualifiers={"method": "折自身四色珊瑚枝照亮海渊并塑海祇岛，留玉枝镇压深海龙蜥", "source_genre": "folklore_adapted_fiction", "epistemic": "folklore_oral_tradition", "uncertainty": "民间传说造岛叙事与地理变迁综合"},
    ))
    items.append(SemanticItem(
        item_id="event:cultural_transformation_and_seal",
        item_kind="event",
        label="文化改造与封存编年史",
        source_bindings=(b(u_timeline_5), b(u_treatise)),
        semantic_acceptance_status="curated_recorded",
        event_type="制度改造",
        participants=("奥罗巴斯",),
        topic_path=("海祇岛", "文化改造"),
        qualifiers={"policy": "下令将编年史封存海渊不得带出，改学鸣神氏族", "source_genre": "scholarly_conjecture", "epistemic": "scholarly_conjecture"},
    ))

    # 3. Direct Relationships
    items.append(SemanticItem(
        item_id="rel:abaraku_builds_helios",
        item_kind="relation",
        label="阿倍良久建造大日御舆",
        source_bindings=(b(u_novel_text), b(u_timeline_0)),
        semantic_acceptance_status="curated_recorded",
        subject_ref="阿倍良久",
        object_ref="大日御舆",
        predicate="建造",
        topic_path=("白夜国", "人物关系"),
        qualifiers={"source_genre": "ancient_chronicle_forbidden", "epistemic": "ancient_official_record"},
    ))
    items.append(SemanticItem(
        item_id="rel:orobashi_defeats_vishap",
        item_kind="relation",
        label="奥罗巴斯战胜深海龙蜥",
        source_bindings=(b(u_timeline_4),),
        semantic_acceptance_status="curated_recorded",
        subject_ref="奥罗巴斯",
        object_ref="深海龙蜥",
        predicate="战胜并镇压",
        topic_path=("白夜国", "神魔征战"),
        qualifiers={"source_genre": "community_chronology", "epistemic": "community_synthesis"},
    ))
    items.append(SemanticItem(
        item_id="rel:orobashi_seals_chronicle",
        item_kind="relation",
        label="奥罗巴斯封存渊下宫编年史",
        source_bindings=(b(u_timeline_5), b(u_treatise)),
        semantic_acceptance_status="curated_recorded",
        subject_ref="奥罗巴斯",
        object_ref="渊下宫编年史",
        predicate="下令封存",
        topic_path=("海祇岛", "历史政策"),
        qualifiers={"source_genre": "scholarly_conjecture", "epistemic": "scholarly_conjecture", "uncertainty": "学者初勘辨析两说并存"},
    ))
    items.append(SemanticItem(
        item_id="rel:kokomi_worships_orobashi",
        item_kind="relation",
        label="珊瑚宫心海祭祀海祇大御神",
        source_bindings=(b(u_kokomi),),
        semantic_acceptance_status="curated_recorded",
        subject_ref="珊瑚宫心海",
        object_ref="奥罗巴斯",
        predicate="祭祀与继承信仰",
        topic_path=("海祇岛", "信仰传承"),
        qualifiers={"source_genre": "character_lore", "epistemic": "official_character_definition"},
    ))

    items.sort(key=lambda it: it.item_id)
    return items


def build_byakuyakoku_navi_state(
    full_rus: Sequence[Mapping[str, Any]],
    scope_level: str = "full_section",
) -> ContextContinuityState:
    """Build the deterministic 7-step lightweight context continuity state for Byakuyakoku.

    Supports 'full_section', 'full_ru', or 'slice' scope levels to allow comparing
    context-size differences against methodology differences.
    """
    if scope_level not in {"slice", "full_ru", "full_section"}:
        raise ValueError(f"scope_level {scope_level} not supported")

    # Step 1: 3429
    u1 = "d9fed6511005d773cdb702580731656454c0f6d40685451dfe11ddbb1b45cfb8"
    l1 = SourceLedgerEntry(u1, "3429", "龙蛇藏归辑录", "剧情对话", scope_level, "official_duty_dialogue", 1, 1, 1)
    c1 = ClueEntry(
        "clue:3429:001",
        "末代图书管理员绘真委托旅行者找回5本未归还馆藏（《常世国龙蛇传》《水文考》《光昼影底集》《实验记录》《日月前事》）；其中《日月前事》被盗，安贞等嫌疑人被关押于大日御舆悬空牢；绘真对旅行者出示的《常世国龙蛇传》封面异样且文字不识表示困惑与怀疑。",
        (u1,),
        "official_duty_dialogue",
        "duty_testimony",
        "确立了白夜国禁书失窃案的实务调查起点与关键线索人物安贞",
    )
    ev1 = ClueEvent("ev:001", 1, "add", c1)

    # Step 2: 3470
    u2 = "6961332b1b0f292b1896fb9f72a8ffb844ddbf144bee2e1fc8b841f30d6f138b"
    u_hydro = "ffdaf5bfbc41b755658f3b14db7c109075066eadd484e506860db4415138c3c7"
    l2_1 = SourceLedgerEntry(u2, "3470", "白夜国馆藏", "日月前事", scope_level, "ancient_chronicle_forbidden", 1, 2, 2)
    l2_2 = SourceLedgerEntry(u_hydro, "3470", "白夜国馆藏", "白夜国地理水文考", scope_level, "ancient_chronicle_forbidden", 1, 2, 2)
    c2 = ClueEntry(
        "clue:3470:001",
        "《白夜国馆藏》中收录的《日月前事》乃常世大神书记官绝笔编年史，记载原初时代天地创生、深海龙蜥肆虐以及大日御舆建成的历史，确系绘真所指的被盗禁书原典。",
        (u2,),
        "ancient_chronicle_forbidden",
        "ancient_official_record",
        "印证了绘真指出的失窃核心禁书《日月前事》的真实书写者与白夜国原初历史记载",
    )
    ev2 = ClueEvent("ev:002", 2, "supplement", c2, target_clue_id="clue:3429:001", reason="读入古籍原典，补全了失窃禁书的书写背景与原初创世内容")

    c3 = ClueEntry(
        "clue:3470:002",
        "《白夜国地理水文考》卷三明确告诫后人：天中无日月的海渊生活乃异常状态；文中还记录了古代海渊居民曾崇拜幻想出来的大蛇'奥罗巴洛斯'，与后来的大御神形成历史前置映射。",
        (u_hydro,),
        "ancient_chronicle_forbidden",
        "ancient_official_record",
        "揭示白夜国对海渊黑暗环境的异常性认知及古代大蛇信仰渊源",
    )
    ev3 = ClueEvent("ev:003", 2, "add", c3)

    # Step 3: 3226
    u_novel_meta = "6fb5641569df1013c0bda1ded11d69ba82e2506b9493d12ee09255c3220b77ed"
    u_novel_text = "0e59547688f1e8f64e695165d6b0f70a0965914de8b12f6de45cb6647694c5b6"
    l3_1 = SourceLedgerEntry(u_novel_meta, "3226", "常世国龙蛇传", "资料描述", scope_level, "folklore_adapted_fiction", 1, 3, 3)
    l3_2 = SourceLedgerEntry(u_novel_text, "3226", "常世国龙蛇传", "常世国龙蛇传", scope_level, "folklore_adapted_fiction", 1, 3, 3)
    c4 = ClueEntry(
        "clue:3226:001",
        "《常世国龙蛇传》资料页载明其系'取材自海祇岛民间故事的小说'，由鸣神岛商人誊抄转写带回鸣神岛八重堂再版，完美解释了绘真为何辨认其非渊下宫统一馆藏封面且文字不识。",
        (u_novel_meta,),
        "folklore_adapted_fiction",
        "folklore_oral_tradition",
        "消除绘真对旅行者所携图书'封面不识'的疑虑，确证八重堂流通本源自海祇民间故事改编",
        uncertainty_note="属商业出版物，存在后世文学润色与改编，保留了海祇口头传说母题，不能视作严谨编年史",
    )
    ev4 = ClueEvent("ev:004", 3, "supplement", c4, target_clue_id="clue:3429:001", reason="通过商品小说背景解释了绘真对版本封面的质疑")

    c5 = ClueEntry(
        "clue:3226:002",
        "《常世国龙蛇传》小说正文描写贤人阿倍良久造地下太阳被太阳之子囚禁，小童探三隅遇渎身渎名之大蛇，大蛇应允成为神明与小童共抗王权龙嗣，具备浓厚民间演义色彩。",
        (u_novel_text,),
        "folklore_adapted_fiction",
        "folklore_oral_tradition",
        "保存了海祇民间对于阿倍良久造日被囚与大蛇降临初次相遇的神怪文学母题",
        uncertainty_note="神怪文学演义笔法，核心人物与情节有原典映射，但对话细节含文学戏剧化成分",
    )
    ev5 = ClueEvent("ev:005", 3, "add", c5)

    # Step 4: 3611
    u_oath = "05e788aceff16128d03709e10a04807e103771d6f9a013da4eec8112e82aa4d6"
    l4 = SourceLedgerEntry(u_oath, "3611", "证誓之明瞳", "相关故事", scope_level, "weapon_lore_divine_oath", 1, 4, 4)
    c6 = ClueEntry(
        "clue:3611:001",
        "武器《证誓之明瞳》铭文为纯白大蛇第一人称自述：直陈因不胜贵金之神与鸣神败逃暗海，怀偷生渎名之耻；令小童立誓并展示蛇瞳宝珠；明确陈述带民前往海面是为'履行与天上之都的誓言'。",
        (u_oath,),
        "weapon_lore_divine_oath",
        "primary_divine_statement",
        "神明第一人称自述揭示了大蛇逃亡海渊的战败屈辱与迁居海面的死誓天命动机",
    )
    ev6 = ClueEvent(
        "ev:006",
        4,
        "contrast",
        c6,
        target_clue_id="clue:3226:002",
        reason="神明自身第一人称自述（战败逃亡、渎名死誓、履行天都誓言）与民间小说（小童寻花请神、英雄演义）构成多重视角对照，两者互见互补而非简单否定",
    )

    # Step 5: 3227
    u_treatise = "e9f3aa8de26fb76700477f818275f093b544c8cf28e9a60d1dcd6db7d8caea13"
    l5 = SourceLedgerEntry(u_treatise, "3227", "珊瑚宫民间信仰初勘", "珊瑚宫民间信仰初勘", scope_level, "scholarly_conjecture", 1, 5, 5)
    if scope_level == "slice":
        c7 = ClueEntry(
            "clue:3227:001",
            "《珊瑚宫民间信仰初勘》片段记录了斩海祇传说故事的'另一个版本'：海渊之民在渊下时拥有极为可靠的编年史，但被大蛇下令封存于渊下宫不得带出，海祇民诸姓亦依大蛇之令改学鸣神样式；并传闻大御神得天京传谕引颈就戮；作者评述此隐喻'事实'的版本与隐喻'意识'的传说相比被视为无关紧要的稗史，且藏书封存海渊难见天日。",
            (u_treatise,),
            "scholarly_conjecture",
            "scholarly_conjecture",
            "切片文本明确记录了大蛇封存编年史、学习鸣神姓氏与天京传谕的野史版本，作者自述藏书难见天日",
            uncertainty_note="切片文本仅展示斩海祇传说的'另一个版本'及作者对藏书难见天日的感叹，未展开前文完整的牺牲说与东征考据",
        )
        c7_reason = "学者专著片段以理性笔触展示了大蛇封存编年史、改学鸣神传统以及天京传谕之说，为理解神明战败与后世历史提供了野史维度对照"
    else:
        c7 = ClueEntry(
            "clue:3227:001",
            "学者考据专著《珊瑚宫民间信仰初勘》完整对勘两重传说：其一为广为接受的'折珊瑚枝救民登天'慈悲传说，作者考据指出其缺乏史料，东征牺牲动机纯属后人发现巫女卜辞后的推测；其二为另一流传版本，称海渊编年史被大蛇下令封存，海祇民改学鸣神氏族，并传天京传谕引颈就戮，作者自评此说为隐喻事实的稗史。",
            (u_treatise,),
            "scholarly_conjecture",
            "scholarly_conjecture",
            "学者完整考据著作揭示了海祇官方记忆的建构性与两重叙述传统（牺牲说推测 vs 封存野史）的客观对勘",
            uncertainty_note="学者明确自述缺乏可靠文字史料，指出牺牲说为后人推测，封存编年史说流传为野史",
        )
        c7_reason = "学者专著从客观考据角度对神明誓约和民间传说进行了理性审视，指明缺乏可靠文字史料与两说并存事实"

    ev7 = ClueEvent(
        "ev:007",
        5,
        "contrast",
        c7,
        target_clue_id="clue:3611:001",
        reason=c7_reason,
    )

    # Step 6: 508883
    u_t4 = "7ceb1023dcbed8fdc8c355d53eb7e1275ba456d3a6ee73da83ed2f856c7215f4"
    u_t5 = "6be0d75b766fc5a89849736012bfb9a23700f29e744f782669d1b661c0ca255d"
    l6_1 = SourceLedgerEntry(u_t4, "508883", "白夜国", "重要事件", scope_level, "community_chronology", 1, 6, 6)
    l6_2 = SourceLedgerEntry(u_t5, "508883", "白夜国", "重要事件", scope_level, "community_chronology", 1, 6, 6)
    if scope_level == "slice":
        c8_text = (
            "白夜国历史时间线记录：大蛇初现渊下时身覆四色珊瑚枝，战胜深海龙蜥并折断珊瑚枝架梯升天、"
            "塑海祇岛，留珊瑚玉枝镇压龙蜥；随后决心抹除海民过去，下令将编年史封存于渊下宫不得带出，并推行鸣神传统改造海祇诸姓。"
        )
    else:
        c8_text = (
            "白夜国历史时间线确证：大蛇在魔神战争失去所有逃向暗海，身负珊瑚枝获新生；下令抹除民众海渊过去，"
            "将编年史封存在渊下宫不得带出，并推行鸣神传统姓氏改造；后折断四色珊瑚枝架梯升天塑海祇岛。"
        )

    c8 = ClueEntry(
        "clue:508883:001",
        c8_text,
        (u_t4, u_t5),
        "community_chronology",
        "community_synthesis",
        "梳理了从造岛救民到文化重构与编年史封存的完整历史行动逻辑",
        uncertainty_note="后人综合整理叙述，融合了传说与已知事件，非原始一手文献",
    )
    ev8 = ClueEvent(
        "ev:008",
        6,
        "supplement",
        c8,
        target_clue_id="clue:3227:001",
        reason="时间线综合梳理了学者初勘中提及的'封存编年史'与'折珊瑚枝造岛'的先后逻辑关系",
    )

    # Step 7: 2403
    u_kokomi = "c89d23c79d89c7d2a1dfe03abdaa7adc8b340404d48e2bc31bd6eeeb8428b3f5"
    l7 = SourceLedgerEntry(u_kokomi, "2403", "珊瑚宫心海", "角色详细", scope_level, "character_lore", 1, 7, 7)
    c9 = ClueEntry(
        "clue:2403:001",
        "官方角色资料定性：海祇先民由奥罗巴斯自渊下宫带至地面建立文明；后大蛇于魔神战争被雷神斩杀，其枯骨怨念化为祟神，成为后世海祇领袖背负的深沉历史宿命。",
        (u_kokomi,),
        "character_lore",
        "official_character_definition",
        "官方定性确立了渊下宫迁民海面至魔神战死祟神遗留的后世定局",
    )
    ev9 = ClueEvent(
        "ev:009",
        7,
        "supplement",
        c9,
        target_clue_id="clue:508883:001",
        reason="衔接了白夜国迁居海面与后世海祇岛鸣神抗争的历史结局",
    )

    if scope_level == "slice":
        summary = (
            "【本地确定性整理样例·切片上下文】基于 E2 探索实际读取的 16 个局部 RU 切片构建轻量级上下文演化状态："
            "绘真委托指出《日月前事》禁书失窃案；八重堂小说《常世国龙蛇传》揭示八重堂商业版背景并保存阿倍良久造日与小童请神母题；"
            "武器《证誓之明瞳》呈现大蛇战败逃亡与履行天上死誓的神明第一人称自述；"
            "学者专著《初勘》切片揭示编年史被封存海渊与改学鸣神氏族的野史记载；"
            "白夜国编年与心海角色切片最终串联起大蛇造岛、抹除过去、封存史书及战死化为祟神的脉络。"
            "各叙述视角多维并存互见，严格限定于切片实际可见字面，未发生跨段落材料越界。"
        )
    else:
        summary = (
            "【本地确定性整理样例·完整 Section 上下文】基于 7 篇核心文献完整 Canonical Section 构建轻量级上下文演化状态："
            "绘真委托点明了《日月前事》禁书失窃案；八重堂小说《常世国龙蛇传》解释了版本封面差异并保存阿倍良久造日与小童请神的神话原型；"
            "武器铭文《证誓之明瞳》呈现了大蛇败逃暗海、偷生渎名与天上死誓的神明自身第一人称真相；"
            "学者考据《初勘》完整对勘了折枝救民牺牲推测与封存编年史野史两说；"
            "白夜国编年与心海角色资料最终将大蛇造岛、文化改造、封存史书及战死化为祟神的整体脉络合流。"
            "多重视角互为映衬、多维并存，展现了完整文献上下文支持下的深度综合理解。"
        )

    state = ContextContinuityState(
        step=7,
        research_summary=summary,
        source_ledger=(l1, l2_1, l2_2, l3_1, l3_2, l4, l5, l6_1, l6_2, l7),
        clue_events=(ev1, ev2, ev3, ev4, ev5, ev6, ev7, ev8, ev9),
    )
    return state


def materialize_all(
    *,
    e2_session_path: Path,
    ru_manifest_path: Path,
    canonical_manifest_path: Path,
    output_dir: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Execute complete deterministic materialization of Byakuyakoku local samples, KG, and Navi states."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Tier 1: E2 Read Slices
    slices = extract_e2_read_slices(e2_session_path)
    slices_body = canonical_json_bytes({"schema_version": "phase05-byakuyakoku-slices-0.1", "slices": slices})
    slices_path = output_dir / "e2_read_slices.json"
    atomic_write(slices_path, slices_body)

    # 2. Tier 2: Full Retrieval Units
    target_uids = [s["unit_id"] for s in slices]
    full_rus = extract_full_retrieval_units(ru_manifest_path, target_uids)
    rus_body = canonical_json_bytes({"schema_version": "phase05-byakuyakoku-full-rus-0.1", "retrieval_units": full_rus})
    rus_path = output_dir / "full_retrieval_units.json"
    atomic_write(rus_path, rus_body)

    # 3. Tier 3: Full Sections
    full_sections = extract_full_sections(canonical_manifest_path)
    sections_body = canonical_json_bytes({"schema_version": "phase05-byakuyakoku-full-sections-0.1", "sections": full_sections})
    sections_path = output_dir / "full_sections.json"
    atomic_write(sections_path, sections_body)

    # 4. E2 Baseline Findings & Official Excerpts
    e2_baseline = build_e2_baseline_findings(e2_session_path)
    e2_body = canonical_json_bytes(e2_baseline)
    atomic_write(output_dir / "e2_baseline_findings.json", e2_body)

    excerpts = build_official_source_excerpts(full_rus, full_sections)
    excerpts_body = canonical_json_bytes(excerpts)
    atomic_write(output_dir / "official_source_excerpts.json", excerpts_body)

    # 5. Deterministic Local Knowledge Graph Build & Views
    kg_items = build_byakuyakoku_kg_items(full_rus)
    ru_index = {r["unit_id"]: r for r in full_rus}
    stage = SemanticStage("stage:kg_curated", "byakuyakoku-local-kg", "byakuyakoku-production-ru", "complete")
    kg_build = SemanticBuild.from_items(
        input_identity="phase05-byakuyakoku-sample-0.1",
        stages=[stage],
        items=kg_items,
        ru_index=ru_index,
    )
    kg_views = build_views(kg_build)
    kg_dir = output_dir / "kg_build"
    if overwrite and kg_dir.exists():
        import shutil
        shutil.rmtree(kg_dir)
    kg_artifacts = write_semantic_build(kg_dir, kg_build, kg_views)

    # 6. Deterministic Local Navi Continuity State (Full Section Context & Slice Context)
    navi_state = build_byakuyakoku_navi_state(full_rus, scope_level="full_section")
    navi_state.validate_references(known_unit_ids=set(target_uids))
    navi_body = canonical_json_bytes(navi_state.to_dict())
    atomic_write(output_dir / "navi_continuity_state.json", navi_body)

    navi_slice_state = build_byakuyakoku_navi_state(full_rus, scope_level="slice")
    navi_slice_state.validate_references(known_unit_ids=set(target_uids))
    navi_slice_body = canonical_json_bytes(navi_slice_state.to_dict())
    atomic_write(output_dir / "navi_continuity_slice_state.json", navi_slice_body)

    # 7. Sample Manifest
    manifest = {
        "schema_version": "phase05-byakuyakoku-samples-manifest-0.1",
        "description": "白夜国核心 7 篇文献三层确定性样本与局部 Navi/KG 候选成果",
        "sample_nature": "local_deterministic_curated_sample",
        "methodology_disclaimer": (
            "目前 9 条 Navi 线索与 18 项 KG 候选属于本地确定性整理样例，用于验证数据契约、"
            "三层样本结构与轻量状态演化机制；它们并非新的在线模型语义抽取结果，"
            "亦不代表探索式 Navi (B1) 与给定材料式 Navi/KG (B2) 的方法质量对照已经完成。"
        ),
        "b1_b2_comparison_status": "in_progress_baseline_prepared_evaluation_pending",
        "core_documents_count": len(CORE_CONTENT_IDS),
        "target_content_ids": list(CORE_CONTENT_IDS),
        "tier1_read_slices_count": len(slices),
        "tier2_full_rus_count": len(full_rus),
        "tier3_full_sections_count": len(full_sections),
        "kg_build": {
            "build_identity": kg_build.build_identity,
            "sample_nature": "local_deterministic_curated_sample",
            "active_items": len(kg_build.active_items),
            "graph_nodes": len(kg_views.graph.nodes),
            "graph_edges": len(kg_views.graph.edges),
            "hierarchy_nodes": len(kg_views.hierarchy.nodes),
        },
        "navi_continuity": {
            "sample_nature": "local_deterministic_curated_sample",
            "steps": navi_state.step,
            "clue_events": len(navi_state.clue_events),
            "active_clues": len(navi_state.derive_active_clues()),
            "ledger_entries": len(navi_state.source_ledger),
        },
        "artifact_hashes": {
            "e2_read_slices.json": sha256(slices_body).hexdigest(),
            "full_retrieval_units.json": sha256(rus_body).hexdigest(),
            "full_sections.json": sha256(sections_body).hexdigest(),
            "e2_baseline_findings.json": sha256(e2_body).hexdigest(),
            "official_source_excerpts.json": sha256(excerpts_body).hexdigest(),
            "navi_continuity_state.json": sha256(navi_body).hexdigest(),
            "navi_continuity_slice_state.json": sha256(navi_slice_body).hexdigest(),
        },
    }
    manifest_body = canonical_json_bytes(manifest)
    atomic_write(output_dir / "sample_manifest.json", manifest_body)

    return {
        "manifest": manifest,
        "kg_artifacts": kg_artifacts,
        "output_dir": str(output_dir),
    }
