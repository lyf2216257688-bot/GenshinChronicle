"""Inspect checked 20261124 MCP records without running retrieval or a model."""

import argparse
from contextlib import closing
from pathlib import Path
import sqlite3

from genshin_corpus.evidence_io import inside, read, require, sha, write
from genshin_corpus.generation.clean_evidence import validate_citations
from genshin_corpus.mcp_live.study import assemble_live
from genshin_corpus.mcp_offline.study import load
from genshin_corpus.rag.clean_research_answer import bind_answer


def archived_study(root, audit, study):
    audit = inside(root, audit)
    with closing(sqlite3.connect((audit / "studies.sqlite").as_uri() + "?mode=ro", uri=True)) as db:
        binding, state = db.execute("SELECT binding,state FROM studies WHERE id=?", (study,)).fetchone()
        state = load(db, state)
        groups = [load(db, item["ref"]) for item in state["groups"].values()]
        packets = {ref: load(db, key) for ref, key in state["packets"].items()}
        receipts = {rid: load(db, key) for rid, key in state["reads"].items()}
    require(state["release"] == "20261124" and all(r["source_validation"] == "PASS" for r in receipts.values()),
            "Expected verified 20261124 receipts")
    scope = {item["query"]["query_id"]: item for item in groups}
    for path in audit.glob("queries/*/run.json"):
        run = read(path)
        query = run["query"]
        if query["session_id"] == study:
            group = scope.pop(query["query_id"])
            require(run["binding"] == binding and read(path.with_name("ranked.json")) == group,
                    "Archived ranking and study differ")
    require(not scope, "Missing ranked query audit")
    for packet in packets.values():
        validate_citations(packet, packet["citations"])
    return state, groups, packets, receipts


def anchors(packet, receipts, source_id, text):
    return [entry["citation_id"] for entry in packet["evidence"]
            if (fragment := receipts[entry["receipt_id"]]["fragments"][0])["source_item"]["content_id"] == source_id
            and any(text in span["text"] for span in fragment["texts"])]


def run(root, repo):
    cases = {}
    for case, study, source, quotes in (
        ("H01", "fontaine-two-ability-prototype", "503143",
         ("最终需要满足以下战斗与工程指标", "三倍于铳枪交战距离单位上的所有魔物", "半小时内完成破障")),
        ("H02", "caoqiaogu-letter", "501846", ("赵铁牛，敬启", "老爷子")),
        ("H04", "zibai-yiwen-qiqi", "508182", ("仙力早已十不存一", "这道符箓里的仙力也损耗了不少")),
    ):
        state, groups, packets, receipts = archived_study(root, "dsh-production", study)
        first = next(iter(packets.values()))
        matches = {quote: anchors(first, receipts, source, quote) for quote in quotes}
        require(all(matches.values()), f"Lost a first Packet anchor for {case}")
        cases[case] = {"question": state["question"], "query_ids": list(state["groups"]),
                       "first_packet_id": first["packet_id"], "first_packet_quotes": matches}
        if case == "H01":
            final = packets[state["current"]]
            require(anchors(final, receipts, source, "点位1") == ["E06"], "Unexpected historical H01 citation")
            _, _, entered = bind_answer(final, "游戏内没有任何一处记载[E06]")
            require(entered["status"] == "PASS" and entered["semantic_support"] == "NOT_EVALUATED",
                    "Binding should be reported separately from semantic support")
            cases[case]["historical_e06"] = {"binding": entered, "supports_claim": False}
        if case == "H02":
            _, _, entered = bind_answer(first, "写信人署名赵铁牛【" + matches[quotes[0]][0] + "】")
            require(entered["status"] == "PASS", "Bracket citation failed")
            cases[case]["bracket_citation"] = entered
    state, groups, packets, receipts = archived_study(root, "storage-adoption-dsh-20261126", "dsh-live")
    first = next(iter(packets.values()))
    story = "那时还被称作「悖谬」"
    voice = "我只是「明晨之镜」的一个念头"
    old_ids = anchors(first, receipts, "505631", story)
    require(old_ids and not anchors(first, receipts, "505631", voice), "Unexpected starting evidence")
    selection = [fragment["fragment_id"] for receipt in receipts.values() for fragment in receipt["fragments"]
                 if receipt["scope"] == "chunk" and any(voice in span["text"] for span in fragment["texts"])]
    require(len(selection) == 1, "Expected one verified voice Chunk")
    keep = [first["citations"][cid]["fragment_id"] for cid in old_ids]
    context = {"system": "依据已核验原文作答，保留来源范围、说话归属、条件和未知状态。",
               "question": state["question"], "conversation": {"query_ids": list(state["groups"])},
               "tools": [], "protocol_history": []}
    final = assemble_live(groups, list(receipts.values()), context, selection, preserve=keep)
    validate_citations(final, final["citations"])
    coverage = {quote: anchors(final, receipts, "505631", quote) for quote in (story, voice)}
    require(all(coverage.values()), "Story and voice did not survive joint admission")
    cases["Q068-related"] = {"question": state["question"], "first_packet_id": first["packet_id"],
                             "first_story_citations": old_ids, "final_packet_id": final["packet_id"],
                             "final_quotes": coverage, "preserved_fragments": keep}
    release = read(repo / "config/mcp-live-v1.json")["releases"]["20261124"]["artifact"]
    require(sha(inside(repo, release["path"])) == release["sha256"], "20261124 Release changed")
    return {"release": release, "scope": "verified archived MCP read receipts and Packet replay",
            "remote_model_calls": 0, "model_visibility": "NOT_MEASURED", "cases": cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path.cwd().resolve()
    root = repo / "data/generation/mcp-live-v1"
    out = args.output.resolve()
    require(out.is_relative_to(root) and not out.exists(), "Use a new path under the MCP data root")
    write(out, run(root, repo))
    print(out)


if __name__ == "__main__":
    main()
