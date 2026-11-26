"""Freeze unretrieved questions and exact source anchors for a future paired evaluation."""

import argparse
from dataclasses import asdict
from pathlib import Path

from genshin_corpus.clean import CleanRef, iter_blocks, iter_text_values, resolve_clean_ref
from genshin_corpus.clean.reader import validate_record
from genshin_corpus.evidence_io import inside, read, require, seal, sha, write
from genshin_corpus.mcp_offline.archive import Archive


CASES = [
    ("H01", "503143", "交互文本", "二号初始能力样机",
     "枫丹的《「二号初始能力样机」研发计划》对野外巡弋与三层建筑尺寸以下的障碍分别列了什么指标？这是实测成绩还是研发要求？",
     [
         ("巡弋时探测并击败航迹两侧三倍铳枪交战距离单位上的所有魔物。", "三倍于铳枪交战距离单位上的所有魔物"),
         ("遇到三层建筑尺寸以下障碍时半小时内完成破障，继续前进。", "在半小时内完成破障，继续前进"),
         ("文件列的是要求，不能据此声称样机已达标。", "本型机关最终需要满足以下战斗与工程指标"),
     ]),
    ("H02", "501846", "交互文本", "拆封的书信",
     "采樵谷那封《拆封的书信》中，年轻管事希望老爷子退出怎样的工作，提供了什么帮助？能从信中确定他们的姓名吗？",
     [
         ("年轻人希望老人别再跟着爬上爬下受累。", "就请不要跟我们这些年轻人一起爬上爬下受累了"),
         ("几个管事凑了摩拉让他金盆洗手，提议凭手艺去璃月港做正经工作。", "我们几个管事的凑了些摩拉留给您"),
         ("书信可证实称谓与建议，姓名留待其他证据。", "小伙子们都长大了，我们都能独当一面了"),
     ]),
    ("H03", "2080", "丘丘语速查手册", "丘丘语速查手册",
     "按《丘丘语速查手册》，丘丘人对你说 Gusha 时能直接判定它想要果子吗？怎样区分可能意思，Mosi gusha 又有什么附带含义？",
     [
         ("只能根据语气区分索要果子、稻米、麦子和表达不满意。", "从语气判断是真的要果子、稻米、麦子还是表达不满意"),
         ("Mosi gusha 除了吃草之外也表达不开心。", "Mosi gusha除了吃草之外，也表达不开心"),
     ]),
    ("H04", "508182", "逸闻纪事对话", "三尸法",
     "兹白的逸闻里，她怎样解释七七吸入死气后的处境？符箓帮了什么忙，又为什么快耗尽？请保留是由谁判断的。",
     [
         ("兹白认为七七本是无魂之体，死气进入后如入无人之境。", "她本就是无魂之体，「死气」进入她体内后，就宛入无人之境"),
         ("符箓镇定神魂，否则她可能当场完全失魂丧志。", "这道符箓帮她镇定神魂"),
         ("兹白说符箓仙力原已十不存一，抵住死气又损耗，判断快要耗尽。", "这枚符箓的仙力马上就要耗尽了"),
         ("说明符箓已有历时损耗，保留说话人归属。", "里面的仙力早已十不存一"),
     ]),
]


def anchor(record, blocks, section, segment):
    positions = set()
    for bid, block in blocks.items():
        if block["context"].get("section_title") != section:
            continue
        for value in iter_text_values(block):
            text = value["text"]
            if segment in text:
                start = text.index(segment)
                positions.add((bid, value["value_pointer"], start, start + len(segment)))
    require(len(positions) == 1, "Source anchor is missing or ambiguous: " + segment)
    bid, pointer, start, end = positions.pop()
    ref = CleanRef(record["observation_id"], bid, pointer, start, end)
    resolved = resolve_clean_ref(ref, record=record, blocks=blocks)
    require(resolved["text"] == segment and bool(resolved["origins"]), "Source anchor changed")
    return {"clean_ref": asdict(ref), "text": segment, "origins": resolved["origins"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path.cwd()
    root = args.output.resolve()
    require(root.parent == repo / "data/generation/mcp-live-v1", "Use a separate MCP evaluation audit root")
    archive = Archive(repo)
    reader = archive.reader
    old = [repo / "data/generation/g7-70q-baseline-20261117/input/question-set.jsonl",
           repo / "scripts/retrieval/rerank_disagreement_cases.json"]
    previous_texts = [path.read_text(encoding="utf8") for path in old]
    by_content = {row["content_id"]: row for row in reader.records.values()}
    questions, scoring = [], {}
    for case, cid, section, topic, question, checks in CASES:
        require(all(topic not in text and question not in text for text in previous_texts),
                "Prior question set already mentions topic")
        entry = by_content[cid]
        path = inside(reader.clean_root, entry["record_path"])
        require(sha(path) == entry["record_sha256"], "Clean record changed")
        record = read(path)
        source, raw_run = record["source_item"], record["input"]["raw_run_id"]
        require(raw_run == archive.catalog["raw_run"], "Foreign Raw observation")
        validate_record(record, repo / "data/raw" / source["source"] / source["locale"] / raw_run)
        blocks = {block["block_id"]: block for block in iter_blocks(record)}
        questions.append({"id": case, "question": question})
        scoring[case] = {"source": source, "record_sha256": entry["record_sha256"], "section": section,
                         "required": [{"judgment": judgment, **anchor(record, blocks, section, segment)}
                                      for judgment, segment in checks]}
    archive.files.check()
    root.mkdir(parents=True, exist_ok=False)
    write(root / "questions.json", questions)
    write(root / "assessor-only.json", scoring)
    seal(root, {"kind": "mcp-agentic-pair-questions", "release": archive.catalog["raw_run"],
                "source_record_count": len(scoring), "judgment_count": sum(len(v["required"]) for v in scoring.values()),
                "tool_or_provider_queries": 0, "checked_prior_sets": {str(p.relative_to(repo)): sha(p) for p in old}})
    print(root / "complete.json")


if __name__ == "__main__":
    main()
