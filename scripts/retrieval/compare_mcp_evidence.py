"""Offline evidence comparison using G7 ranks and explicitly nominated source reads."""

import argparse
import json
from pathlib import Path

from genshin_corpus.evidence_io import digest, inside, read, require, sha, write
from genshin_corpus.generation.clean_evidence import EvidenceReader, assemble, validate_citations
from genshin_corpus.indexing.benchmark_io import offline_only
from genshin_corpus.mcp_live.study import BUDGET, FIRST_READS, assemble_live
from genshin_corpus.mcp_offline.archive import Archive
from genshin_corpus.mcp_offline.study import Studies


CASES = {
    "Q046": {
        "witnesses": [("blessing", 1, "我将祝福送予猎人的女儿"),
                      ("identity", 3, "我其实，是一只很久以前从枫丹流落至此的纯水精灵")],
        "rubric": "用角色故事定位祝福，用卡莉露在清泉镇的自述核对姓名。两处原文须分别归属。",
    },
    "Q060": {
        "witnesses": [("claimed_name", 4, "他说他叫做诺奇特"),
                      ("separate_disguise", 6, "派蒙：我们见到的「特诺约洛钦」居然是渊上假扮的吗？")],
        "rubric": "保留笔记作者的转述，区分特诺约洛钦与诺奇特梨服装。原文缺少同一人桥梁时保留不确定性。",
    },
    "Q068": {
        "witnesses": [("old_title", 1, "那时还被称作「悖谬」"),
                      ("first_person_origin", 43, "我只是「明晨之镜」的一个念头")],
        "rubric": "同时支持源体、第一人称自述与分出意志的曾用称号，保留区分和故事证据。",
    },
}


def pinned(root, ref):
    path = inside(root, ref["path"])
    require(sha(path) == ref["sha256"], "Historical artifact checksum failed")
    return read(path)


def projected(packet, limit=None):
    first = Studies.packet_view(packet, lambda _kind, key: key, 0, limit=limit)
    page = first["projection"]
    pages, parts = 1, []
    if "body" in page:
        body = page["body"]
    else:
        parts.append(page["partial_json_text"])
        while page["next_offset"] is not None:
            page = Studies.packet_view(packet, lambda _kind, key: key, page["next_offset"],
                                       limit=limit)["projection"]
            parts.append(page["partial_json_text"])
            pages += 1
        body = json.loads("".join(parts))
    require(digest(body) == first["projection"]["projection_sha256"] and body == packet["evidence"],
            "Tool projection lost source evidence")
    return body, pages


def coverage(packet, witnesses, receipts, body=None):
    validate_citations(packet, packet["citations"])
    out = {name: [] for name in witnesses}
    for entry in body if body is not None else packet["evidence"]:
        receipt = receipts[entry["receipt_id"]]
        source = receipt["source_snapshot"]["source_item"]
        for name, witness in witnesses.items():
            if source == witness["source_item"] and any(
                witness["text_segment"] in text["text"] and text["clean_ref"] == witness["clean_ref"]
                for text in receipt["fragments"][0]["texts"]
            ) and witness["text_segment"] in json.dumps(entry["content"], ensure_ascii=False):
                out[name].append(entry["citation_id"])
    return out


def compare(archive, root, row, spec):
    question_id = row["question_id"]
    record_dir = root / "records" / question_id
    historical = read(record_dir / "evidence.json")
    store = inside(root, row["store"])
    groups = [pinned(store, ref) for ref in historical["ranked_candidate_artifacts"]]
    ranked = groups[0]
    require(ranked["query"]["query_id"] == "original" and ranked["query"]["original_question"] == row["question"],
            "Unexpected original ranking")
    archive.reader.add(ranked)
    evidence = EvidenceReader(archive.reader, verified_parent_ranges=True)
    handles = list(evidence.register(ranked).values())
    first_reads = [evidence.read(handle) for handle in handles[:FIRST_READS]]
    witnesses, selected = {}, []
    for name, rank, segment in spec["witnesses"]:
        receipt = evidence.read(handles[rank - 1])
        fragment, = receipt["fragments"]
        source = next(text for text in fragment["texts"] if segment in text["text"])
        selected.append(fragment["fragment_id"])
        witnesses[name] = {"rank": rank, "chunk": receipt["chunk_id"], "receipt_id": receipt["receipt_id"],
                           "source_item": fragment["source_item"], "clean_ref": source["clean_ref"], "text_segment": segment}
    context = {"system": "依据原文，保留来源范围、说话者、条件和未知。",
               "question": row["question"], "conversation": {"query_ids": ["original"]}, "tools": [], "protocol_history": []}
    initial = assemble_live([ranked], first_reads, context)
    plain = assemble([ranked], list(evidence.receipts.values()), selection_ids=selected, context=context, budget=BUDGET)
    researched = assemble_live([ranked], list(evidence.receipts.values()), context, selected)
    body, pages = projected(researched)
    old_reads = {key: pinned(store, ref) for key, ref in historical["readback_artifacts"].items()}
    baseline = historical["packet"]
    decisions = {name: next(item for item in researched["dispositions"] if item["chunk_id"] == witness["chunk"])
                 for name, witness in witnesses.items()}
    return {
        "question": row["question"], "baseline_run_status": row["status"], "rubric": spec["rubric"],
        "ranked_inputs": historical["ranked_candidate_artifacts"], "original_candidates": len(handles),
        "witnesses": witnesses, "formal_final": coverage(baseline, witnesses, old_reads),
        "ordinary_mcp_first": coverage(initial, witnesses, evidence.receipts),
        "mcp_uncompressed_selection": coverage(plain, witnesses, evidence.receipts),
        "mcp_after_named_source_reads": coverage(researched, witnesses, evidence.receipts, body),
        "mcp_selection_dispositions": decisions,
        "formal_final_packet": baseline["packet_id"], "mcp_first_packet": initial["packet_id"],
        "mcp_selected_packet": researched["packet_id"], "mcp_selected_chars": len(json.dumps(body, ensure_ascii=False)),
        "projection_pages": pages, "semantic_support": "NOT_EVALUATED",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path.cwd()
    benchmark = repo / "data/generation/g7-70q-baseline-20261117"
    output = args.output.resolve()
    require(output.is_relative_to(repo / "data/generation/mcp-live-v1"), "Use a separate MCP audit root")
    with offline_only():
        rows = {r["question_id"]: r for r in (json.loads(line)
                for line in (benchmark / "index.jsonl").read_text(encoding="utf8").splitlines())}
        archive = Archive(repo)
        cases = {question: compare(archive, benchmark, rows[question], spec) for question, spec in CASES.items()}
        archive.files.check()
        write(output, {"status": "PASS", "scope": "historical-traces-with-explicit-expert-source-selection",
                       "benchmark_index_sha256": sha(benchmark / "index.jsonl"),
                       "remote_calls": 0, "host_model_visibility": "NOT_MEASURED", "cases": cases})
    print(output)


if __name__ == "__main__":
    main()
