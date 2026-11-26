"""Compare the stdio evidence service with registered historical G5 first Packets."""
import argparse
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

from genshin_corpus.evidence_io import digest, inside, read, require, sha, write
from genshin_corpus.generation.clean_evidence import EvidenceReader, assemble
from genshin_corpus.mcp_offline.archive import Archive
from genshin_corpus.mcp_offline.study import load


@contextmanager
def connect(repo, name):
    command = [sys.executable, "-X", "utf8", "-B", "-m", "genshin_corpus.mcp_offline",
               "--repo", str(repo), "--audit-name", name]
    env = {k:v for k,v in os.environ.items() if k.upper() in {"PATH","SYSTEMROOT","WINDIR","TEMP","TMP"}}
    env["PYTHONPATH"] = str(repo / "src")
    with subprocess.Popen(command, cwd=repo, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE) as process:
        serial = 0
        def send(method, params, *, notification=False):
            nonlocal serial
            serial += 1
            message = {"jsonrpc":"2.0", "method":method, "params":params}
            if not notification:
                message["id"] = serial
            process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
            process.stdin.flush()
            if not notification:
                reply = json.loads(process.stdout.readline())
                require(reply.get("id") == serial and "result" in reply, "Protocol response failed")
                return reply["result"]
        negotiated = send("initialize", {"protocolVersion":"2025-11-25","capabilities":{},
                                         "clientInfo":{"name":"gc-offline-acceptance","version":"1"}})
        require(negotiated["protocolVersion"] == "2025-11-25", "Unexpected protocol revision")
        send("notifications/initialized", {}, notification=True)
        def call(tool, args, *, expect_error=False):
            result = send("tools/call", {"name":tool,"arguments":args})
            require(bool(result.get("isError")) == expect_error, "Unexpected tool status: " + tool)
            if not expect_error:
                value = json.loads(result["content"][0]["text"])
                require(value == result["structuredContent"], "Host text and structured results differ")
                return value
        try:
            yield call
        finally:
            process.stdin.close()
            require(process.wait(timeout=15) == 0, "Offline service did not shut down cleanly")


def compare_ranking(invoke, study, binding, group):
    rows, offset = [], 0
    while offset is not None:
        result = invoke("browse_candidates", query=group["query"]["query_id"], offset=offset)
        rows.extend(result["rows"])
        offset = result["next_offset"]
    expected = []
    for candidate in group["candidates"]:
        source = candidate["source"]
        key = digest(["gc-source-handle-1", group["input_bundle_id"], candidate["chunk_id"], digest(source)])
        expected.append((candidate["reranking"]["final_rank"], source["source_item"],
                         digest([binding, study, "source", key])))
    require([(r["rank"], r["source_item"], r["handle"]) for r in rows] == expected, "Candidate pages differ")
    return rows


def compare_supplement(repo, root, case, query, original, golden, fresh, source, end):
    # Direct calls through normal Generation primitives. Only the frozen-row file adapter is shared.
    archive = Archive(repo)
    ref = case["groups"][query]
    supplemental = archive.pinned(ref)
    groups = [original, supplemental]
    evidence = EvidenceReader(archive.reader, verified_parent_ranges=True)
    for group in groups:
        archive.reader.add(group)
        handles = evidence.register(group)
        for handle in list(handles.values())[:6]:
            evidence.read(handle, "chunk")
    context = {"system": "按来源范围使用证据，保留条件与未知状态。",
               "question": case["question"], "tools": [], "protocol_history": [],
               "conversation": {"frozen_queries": [g["query"] for g in groups]}}
    budget = golden["policy"]["budget"]
    after_follow = assemble(groups, list(evidence.receipts.values()), context=context, budget=budget)
    parent = evidence.read(handles[supplemental["candidates"][0]["chunk_id"]], "parent")
    fragment_id = parent["fragments"][0]["fragment_id"]
    final = assemble(groups, list(evidence.receipts.values()), selection_ids=[fragment_id], context=context, budget=budget)
    require(parent["receipt_id"] == source["receipt_id"] and fragment_id == source["fragment_id"],
            "Direct parent source differs")
    with closing(sqlite3.connect(root / "studies.sqlite")) as db:
        require(load(db, digest(parent)) == parent, "Persisted parent receipt differs")
        for direct, returned in ((after_follow, fresh), (final, end)):
            require(direct["packet_id"] == returned["packet_id"] and load(db, digest(direct)) == direct,
                    "Direct supplemental Packet differs")
    return supplemental


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--repo", type=Path, default=Path.cwd())
    cli.add_argument("--audit-name", required=True)
    args = cli.parse_args()
    repo = args.repo.resolve()
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,48}", args.audit_name), "Invalid audit name")
    root = inside(repo, "data/generation/mcp-offline-v1/" + args.audit_name)
    root.mkdir(parents=True, exist_ok=False)
    catalog = read(repo / "config/mcp-offline-v1.json")
    report = {"protocol":"2025-11-25","model_dispatches":0,"new_retrieval_or_scoring":0,
              "semantic_acceptance":"NOT_EVALUATED","cases":[]}
    with connect(repo, args.audit_name) as call:
        available = call("list_cases", {})
        report["binding"] = available["binding"]
        for case in catalog["cases"]:
            study = case["id"]
            def invoke(name, **kwargs):
                return call(name, {"study":study, **kwargs})
            first = invoke("open_study", request_id="start", case=study)
            ref = case["initial_sources"]
            path = inside(repo, ref["path"])
            require(sha(path) == ref["sha256"], "Unregistered golden Packet")
            golden = read(path)["packet"]
            require(first["packet_id"] == golden["packet_id"]
                    and first["projection"]["body"] == golden["evidence"], "First host-visible evidence differs")
            with closing(sqlite3.connect(root / "studies.sqlite")) as db:
                state = load(db, db.execute("SELECT state FROM studies WHERE id=?", (study,)).fetchone()[0])
                require(load(db, state["packet"]) == golden, "Direct and MCP dispositions or bindings differ")
            ranked_ref = case["groups"]["original"]
            path = inside(repo, ranked_ref["path"])
            require(sha(path) == ranked_ref["sha256"], "Ranking changed")
            ranked = read(path)
            rows = compare_ranking(invoke, study, report["binding"], ranked)
            checked = invoke("check_answer", packet_ref=first["packet_ref"], answer=" ".join("["+i+"]" for i in golden["citations"]))
            require(checked["citations"] == golden["citations"], "Host citations differ")
            item = {"case":study,"ranked":len(rows),"ranked_sha256":ranked_ref["sha256"],
                    "first_packet":first["packet_id"],"first_citations":len(golden["citations"]),
                    "ordered_candidates":"PASS","complete_first_packet":"PASS","text_projection":"PASS"}
            if first["available_queries"]:
                choice = first["available_queries"][0]
                fresh = invoke("follow_query", request_id="follow", query=choice["query"], gap=choice["gap"],
                               parent_query=choice["parent_queries"][0], evidence_handles=choice["evidence_handles"])
                call("get_packet", {"study":study,"packet_ref":first["packet_ref"]}, expect_error=True)
                row = invoke("browse_candidates", query=choice["query"])["rows"][0]
                source = invoke("read_source", request_id="read", handle=row["handle"], scope="parent")
                end = invoke("assemble_packet", request_id="finish", fragment_ids=[source["fragment_id"]])
                supplement = compare_supplement(repo, root, case, choice["query"], ranked, golden, fresh, source, end)
                secondary_rows = compare_ranking(invoke, study, report["binding"], supplement)
                item.update(follow_packet=fresh["packet_id"], parent_receipt=source["receipt_id"],
                            final_packet=end["packet_id"], final_status=end["status"], stale_packet="rejected",
                            follow_ranked=len(secondary_rows), direct_parent_and_both_packets="PASS")
            report["cases"].append(item)
    with connect(repo, args.audit_name) as call:
        for item in report["cases"]:
            study = item["case"]
            recovered = call("request_status", {"study":study,"request_id":"start"})
            retried = call("open_study", {"study":study,"case":study,"request_id":"start"})
            require(recovered == {"status":"committed","result":retried}
                    and retried["packet_id"] == item["first_packet"], "Reconnect replay differs")
    report["committed_recovery"] = "PASS"
    write(root / "acceptance.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
