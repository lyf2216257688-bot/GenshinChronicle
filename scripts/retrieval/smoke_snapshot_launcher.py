"""Fault-inject the real launcher using a separately sealed miniature corpus."""

import argparse
import json
from pathlib import Path
import subprocess
from time import monotonic, sleep

import psutil
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import WhitespaceSplit

from genshin_corpus.chunking.pilot import run_pilot
from genshin_corpus.chunking.snapshot_ranges import register_flat
from genshin_corpus.clean.pipeline import build_clean_run
from genshin_corpus.evidence_io import digest, read, require, sha, write
from genshin_corpus.retrieval.clean_based.snapshot import open_release
from genshin_corpus.retrieval.clean_based.snapshot_source import SnapshotReader
from snapshot_acceptance import first_packet, _registered, register_cases
from snapshot_full_run import DIFF, publish_execution
from tests.clean import test_snapshot_diff as fixtures


def launch(repo, scratch):
    root = scratch.with_name("vcs-" + scratch.name.rsplit("-", 1)[-1])
    frozen = scratch / "sealed"
    factory = fixtures.SnapshotDiffTests()
    factory.root = scratch / "fixture"
    old = factory.collect("old", {10: list(range(100, 116)), 20: [201, 202]})
    new = factory.collect("new", {10: [301, *range(100, 116)], 20: [201]}, changed_detail=201)
    clean_id = "smoke-base"
    build_clean_run(raw_run_root=old, output_root=scratch / "old-clean", clean_run_id=clean_id, workers=1)
    clean = scratch / "old-clean/mihoyo_obc/zh-cn" / clean_id / "metadata/manifest.json"
    token = scratch / "tokenizer.json"
    encoder = Tokenizer(WordLevel(vocab={"[UNK]": 0}, unk_token="[UNK]"))
    encoder.pre_tokenizer = WhitespaceSplit()
    encoder.save(str(token))
    selection = read(repo / "config/chunking-full-20261107-balanced.json")
    selection.update(content_ids=[*(str(i) for i in range(100, 116)), "201", "202"], review_content_ids=[])
    write(scratch / "selection.json", selection)
    run_pilot(clean, scratch / "selection.json", token, token, scratch / "legacy-chunks")
    ranges = scratch / "ranges.json"
    write(ranges, register_flat(scratch / "legacy-chunks/manifest.json"))
    dataset = {k: p.relative_to(repo).as_posix() for k, p in {"old": old, "new": new, "old_clean": clean, "ranges": ranges}.items()}
    dataset["rules"] = "chunking-full-20261107-balanced.json"
    pins = {path.relative_to(repo).as_posix(): sha(path)
            for path in (ranges, clean, old / "metadata/manifest.json", new / "metadata/manifest.json")}
    publish_execution(repo, frozen, dataset, pins, [{"path": str(token), "sha256": sha(token)}] * 2,
                      ["旧国工匠的故事是什么？"], [], smoke=True)
    seal = sha(frozen / "complete.json")
    script = repo / "scripts/retrieval/run-incremental-corpus.ps1"
    options = ["pwsh", "-NoProfile", "-File", script, "-Execution", frozen, "-Output", root, "-SealSha256", seal, "-Mode"]

    def command(mode, expected=0):
        log = scratch / (mode + ".log")
        with log.open("xb") as handle:
            result = subprocess.run([*options, mode], cwd=repo, stdout=handle, stderr=subprocess.STDOUT, timeout=660)
        require(result.returncode == expected, f"{mode} returned {result.returncode}. See {log}")

    command("Preflight")
    require(not root.exists(), "Preflight created a build root")
    progress = root / "progress.jsonl"
    with (scratch / "Run-interrupted.log").open("xb") as handle:
        process = subprocess.Popen([*options, "Run"], cwd=repo, stdout=handle, stderr=subprocess.STDOUT)
        controller = None
        try:
            deadline = monotonic() + 100
            while monotonic() < deadline and process.poll() is None:
                if progress.exists():
                    lines = progress.read_text(encoding="utf8").splitlines()
                    for line in reversed(lines):
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if event["event"] == "started" and event["stage"] == "incremental-vectors":
                            controller = psutil.Process(event["controller_pid"])
                            break
                if controller is not None:
                    break
                sleep(.2)
            require(controller is not None, "Smoke did not reach the interruption point")
            sleep(.6)
            descendants = controller.children(recursive=True)
            controller.kill()
            require(process.wait(timeout=40) != 0, "Killed controller reported success")
            _, alive = psutil.wait_procs(descendants, timeout=10)
            require(not alive, "Interrupted process tree still has running descendants")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    receipt = root / "receipts/incremental-clean.json"
    earlier = sha(receipt)
    require(not (root / "receipts/incremental-vectors.json").exists(), "Interruption happened after stage completion")
    command("Resume")
    require(sha(receipt) == earlier and read(root / "quick-complete.json")["status"] == "PASS", "Resume lost completed work")
    command("Validate")
    verify_run(repo, scratch, root, earlier)


def verify_run(repo, scratch, root, earlier):
    accepted = read(root / "quick-complete.json")
    require(accepted["status"] == "PASS", "Quick acceptance is incomplete")
    events = [json.loads(line) for line in (root / "progress.jsonl").read_text(encoding="utf8").splitlines()]
    require(any(e["event"] == "reused" and e.get("stage") == "incremental-clean" for e in events)
            and any(e["event"] == "heartbeat" and e["resources"]["disk_free_bytes"] > 0 for e in events),
            "Expected reuse and resource progress events")
    archived = register_cases(repo, repo / DIFF)[0]
    _, _, _, context, settings = _registered(repo, archived)
    packets, projections = [], []
    for side, release in zip(("left", "right"), accepted["releases"], strict=True):
        bind = read(root / "receipts" / f"quick-01-S0-{side}.json")
        ranked = read(root / bind["outputs"]["ranked"]["path"])
        _, snapshot, _, _ = open_release(release)
        packet, _, citation_check = first_packet(SnapshotReader(snapshot), ranked, context, settings)
        require(citation_check["status"] == "PASS" and packet["evidence"], "Smoke Packet is invalid or empty")
        require(packet["input_group_digests"] == [digest(ranked)], "Packet group binding differs")
        packets.append(packet["packet_id"])
        projections.append({**{k: v for k, v in packet.items() if k not in {"packet_id", "input_group_digests", "citations"}},
                            "citations": {name: {k: v for k, v in row.items() if k != "packet_id"}
                                          for name, row in packet["citations"].items()}})
    require(projections[0] == projections[1], "Smoke initial Packet evidence or dispositions differ across builds")
    write(scratch / "report.json", {"status": "PASS", "execution_sha256": accepted["execution_sha256"],
          "steps": ["Preflight", "Run interrupted", "Resume", "Validate"], "reused_completed_receipt": earlier,
          "packet_ids": packets, "packet_projection_sha256": digest(projections[0]),
          "orphan_descendants": 0, "progress_events": len(events), "remote_calls": 0})
    print(scratch / "report.json", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--verify-existing", action="store_true")
    args = parser.parse_args()
    repo = Path.cwd().resolve()
    scratch = args.output.resolve()
    require(scratch.parent == repo / "data/retrieval" and scratch.name.startswith("incremental-smoke-"), "Use an isolated smoke root")
    if args.verify_existing:
        root = scratch.with_name("vcs-" + scratch.name.rsplit("-", 1)[-1])
        verify_run(repo, scratch, root, sha(root / "receipts/incremental-clean.json"))
    else:
        scratch.mkdir(parents=True, exist_ok=False)
        launch(repo, scratch)
