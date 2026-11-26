"""Verify old and candidate release bindings and an isolated rollback without new inference."""

import argparse
import os
from pathlib import Path

from genshin_corpus.evidence_io import inside, read, require, sha, write
from genshin_corpus.generation.clean_evidence import EvidenceReader, validate_citations
from genshin_corpus.indexing.benchmark_io import offline_only
from genshin_corpus.mcp_offline.archive import Archive
from genshin_corpus.mcp_offline.study import Studies
from genshin_corpus.retrieval.clean_based.snapshot import open_release, pin
from genshin_corpus.retrieval.clean_based.snapshot_source import SnapshotReader

from snapshot_acceptance import packet_view


ROOT = Path("data/retrieval/vcr-1126")
QUICK_SHA = "6bb423876647a9049ffb02a8d4a6fafdbf2044f824d7d0271b92fe1854b44d95"
DIFF_SHA = "48833b87601d76b9f0a27cf8335729a6adb2cce332f49c781beaa158b89b127b"


def read_pinned(path, expected):
    require(sha(path) == expected, "Artifact binding changed: " + str(path))
    return read(path)


def read_receipt(root, stage, quick=None):
    path = root / "receipts" / (stage + ".json")
    execution = quick or read(root / "quick-complete.json")
    receipt = read_pinned(path, quick["receipts"][stage]) if quick else read(path)
    require(receipt["stage"] == stage and receipt["execution_sha256"] == execution["execution_sha256"],
            "Foreign stage receipt")
    files = {}
    for key, ref in receipt["outputs"].items():
        path = inside(root, ref["path"])
        files[key] = read_pinned(path, ref["sha256"])
    return files, inside(root, receipt["outputs"]["main"]["path"]).parent


def select(path, kind, binding):
    candidate = path.with_suffix(".new")
    value = {"kind": kind, "artifact": binding}
    write(candidate, value)
    os.replace(candidate, path)
    require(read(path) == value, "Isolated selector was not committed")
    return value


def selected_release(selector):
    binding = selector["artifact"]
    path = Path(binding["path"])
    require(selector["kind"] == "release" and path.is_file() and sha(path) == binding["sha256"], "Foreign release pin")
    return open_release(binding["path"])


def replay_packet(studies, packet):
    return studies.call("get_packet", {"study": "rollback-witness", "packet_ref": packet["packet_ref"]}, lambda: None)


def require_rejection(call, expected):
    try:
        call()
    except ValueError as exc:
        require(expected in str(exc), "Unexpected rejection: " + str(exc))
    else:
        raise ValueError("Foreign input passed: " + expected)


def witness(reader, diff, cid, status, expected_effective):
    spec = diff[cid]
    require(spec["status"] == status, "Diff witness changed")
    number = next(int(row["input_row"]) for row in reader.source.rows if row["source_item"]["content_id"] == cid)
    doc = reader.document(number)
    row = doc["row"]
    clean = reader.read_clean(row)
    current, effective = (row["raw_observations"][kind] for kind in ("current", "effective"))
    require(current["archive"]["run_id"] == "p01eb_full_20261124"
            and effective["archive"]["run_id"] == expected_effective, "Observation identity differs")
    if cid == "1048":
        require(spec["locator_changed"] and current["locators"] != effective["locators"], "Missing locator witness")
    if cid == "1190":
        require("detail" in spec["changed_parts"], "Missing detail change witness")
    return {"content_id": cid, "status": status, "chunk": row["chunk_id"], "observation": clean["observation_id"],
            "current": current["archive"]["run_id"], "effective": effective["archive"]["run_id"]}


def verify(repo, output):
    require(output.parent == repo / "data/retrieval" and output.name.startswith("release-binding-"),
            "Use an isolated release-binding-* audit root")
    output.mkdir(exist_ok=False)
    official = repo / "config/index-selected-v1.json"
    original = pin(official)
    root = repo / ROOT
    quick = read_pinned(root / "quick-complete.json", QUICK_SHA)
    require(quick["status"] == "PASS" and len(quick["receipts"]) == 36, "Quick is not complete")
    dep = quick["asset_dependencies"]
    dependencies = read_pinned(root / dep["path"], dep["sha256"])
    reg = dependencies["arms"]["incremental"]["release"]
    require(Path(reg["path"]) == root / "incremental/indexes/release.json", "Unexpected candidate release")

    pointer = output / "active.json"
    old_pointer = select(pointer, "legacy", original)
    history = Archive(repo)
    studies = Studies(history, output / "legacy-studies")
    old_packet = studies.call("open_study", {"study": "rollback-witness", "request_id": "open", "case": "Q001"}, lambda: None)
    old_view = replay_packet(studies, old_packet)
    require(bool(old_packet["citation_ids"]), "Historical initial Packet has no citations")

    selected = select(pointer, "release", reg)
    release, source, _, binding = selected_release(selected)
    reader = SnapshotReader(source)
    s0, partial = read_receipt(root, "review-Q001")
    s0_result = s0["main"]
    s0_run = read(partial / "new-search/run.json")
    quick_s0, _ = read_receipt(root, "quick-01-S0-left", quick)
    s2, _ = read_receipt(root, "quick-01-S2-left", quick)
    require(s0_result["status"] == "PASS" and s0_result["remote_calls"] == 0, "S0 audit changed")
    require(s0_run["release"] == quick_s0["main"]["release"] == s2["main"]["release"] == reg
            and s0_run["remote_calls"] == quick_s0["main"]["remote_calls"] == s2["main"]["remote_calls"] == 0,
            "Retrieval traces belong to a different release")
    require(set(s0_run["usage"]) == set(quick_s0["main"]["usage"]) == {"bm25f", "dense"}
            and set(s2["main"]["usage"]) == {"bm25f", "dense", "sparse"},
            "S0 or S2 route presence differs")
    packet_data = read_pinned(partial / "packets.json", s0_result["packets_sha256"])
    packet = packet_data["new"]
    require(all(row["input_bundle_id"] == reader.bundle_id for row in packet_data["new_receipts"]),
            "New Packet receipt crossed release boundaries")
    require(packet_view(packet, packet_data["new_receipts"]) == s0_result["packet"]["new"], "New Packet differs")
    validate_citations(packet, list(packet["citations"]))
    require(bool(packet["citations"]), "New initial Packet has no citations")
    s2_ranked = s2["ranked"]
    require(s2_ranked["input_bundle_id"] == reader.bundle_id, "S2 bundle differs")
    evidence = EvidenceReader(reader, verified_parent_ranges=True)
    handles = evidence.register(s2_ranked)
    require(bool(handles) and evidence.read(next(iter(handles.values()))) == s2["evidence"], "S2 source replay differs")

    delta = read_pinned(repo / "data/retrieval/incremental-raw-diff-20261125.json", DIFF_SHA)
    keyed = {row["source_item"]["content_id"]: row for row in delta["records"]}
    removed = sorted(cid for cid, row in keyed.items() if row["status"] == "removed")
    require(len(removed) == 6 and all(cid not in source.clean for cid in removed)
            and not {row["source_item"]["content_id"] for row in source.rows}.intersection(removed),
            "Removed source leaked into current inventory or index rows")
    witnesses = [witness(reader, keyed, "1048", "unchanged", "p01eb_full_20260824"),
                 witness(reader, keyed, "1190", "modified", "p01eb_full_20261124"),
                 witness(reader, keyed, "509698", "added", "p01eb_full_20261124")]
    binding.check()
    foreign = dependencies["arms"]["full"]["manifests"]["vectors"]
    mixed = output / "mixed-release.json"
    write(mixed, {**release, "vectors": foreign})
    require_rejection(lambda: open_release(mixed), "Release index/input mismatch")
    require_rejection(lambda: selected_release({**selected, "artifact": {**reg, "sha256": "0" * 64}}), "Foreign release pin")
    missing = output / "missing-release.json"
    require_rejection(lambda: selected_release({"kind": "release", "artifact": {**reg, "path": str(missing)}}), "Foreign release pin")

    require(select(pointer, "legacy", original) == old_pointer and pin(official) == original, "Rollback selector differs")
    after = Studies(Archive(repo), output / "legacy-studies")
    require(replay_packet(after, old_packet) == old_view, "Historical study changed after rollback")
    require(selected_release(selected)[0] == release, "Candidate session binding lost after rollback")
    require(reader.read_clean(reader.document(int(s2_ranked["candidates"][0]["input_row"]))["row"])["source_item"]
            == s2_ranked["candidates"][0]["source"]["source_item"], "Candidate citation unavailable after rollback")
    write(output / "result.json", {"status": "PASS", "scope": "isolated-release-binding-and-rollback",
                                   "quick_sha256": QUICK_SHA, "candidate": selected, "legacy": old_pointer,
                                   "new_citations": len(packet["citations"]), "legacy_citations": len(old_packet["citation_ids"]),
                                   "query_runs": {"S0": "quick-01-S0-left", "S2": "quick-01-S2-left",
                                                  "s0_packet": "review-Q001"},
                                   "fresh_query_count": 0, "witnesses": witnesses, "removed_absent": removed,
                                   "remote_calls": 0})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with offline_only():
        verify(args.repo.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
