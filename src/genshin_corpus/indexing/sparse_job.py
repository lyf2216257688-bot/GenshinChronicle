"""Standalone P3 preflight/build/resume/validate. Full encoding requires explicit Run."""
import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import gc
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
import time
import traceback

from ..build_runtime import atomic_json
from ..evidence_io import digest, inside, read, require, sha, write
from .benchmark_io import (attempt, build_lock, disk_guard, event, lines, mapping, offline_only,
                           prevent_idle_sleep, ram_guard, resources, verify_receipt)
from .inputs import iter_inputs
from .sparse_full import (VERSION, StreamingSparseSession, build_stream, encode_shard, ensure_plan, validate_store)

CODE_FILES = ["indexing/sparse_job.py", "indexing/sparse_full.py", "indexing/sparse_trial.py",
              "indexing/benchmark_io.py", "indexing/benchmark_dense.py", "indexing/inputs.py", "indexing/schema.py",
              "retrieval/clean_based/source.py", "retrieval/clean_based/indexes.py", "retrieval/clean_based/trial.py",
              "build_runtime.py", "evidence_io.py", "collector/storage.py", "retrieval/local_qwen3_build.py"]


def settings(repo, config_path):
    config = read(config_path)
    require(config["version"] == "gc-sparse-build-job-1", "Unknown Sparse job config")
    for key, lower, upper in (("shard_rows", 1, 4096), ("max_batch", 1, 8), ("padded_token_budget", 1, 65536)):
        require(type(config[key]) is int and lower <= config[key] <= upper, "Invalid " + key)
    for key in ("minimum_available_ram_gib", "minimum_running_ram_gib", "minimum_free_gib", "minimum_free_vram_gib", "heartbeat_seconds"):
        require(type(config[key]) in {int, float} and 0 < config[key] < 1e6, "Invalid resource setting " + key)
    base = read(repo / config["retrieval_config"])
    require((repo / config["python"]).resolve() == Path(sys.executable).resolve(), "Use the pinned Sparse Python runtime")
    return config, base


def output_guard(repo, base, output, protected_extra=()):
    from ..retrieval.clean_based.indexes import selected
    selection, manifests = selected(repo, base)
    input_path = Path(manifests["dense"][1]["input_manifest"]).resolve()
    inputs = read(input_path)
    protected = [p.parent for p, _ in manifests.values()] + [input_path.parent,
        Path(inputs["source_manifests"]["chunk"]).parent, Path(inputs["source_manifests"]["clean"]).parent.parent,
        repo / selection["dense"]["weights_dir"], manifests["bm25f"][0].parent.parent, *protected_extra]
    # Also reject ancestors: output=data/retrieval must never become a job root.
    require(output.is_relative_to((repo / "data/retrieval").resolve()) and output != (repo / "data/retrieval").resolve(),
            "Sparse job output must be a child of data/retrieval")
    require(not any(output.is_relative_to(p.resolve()) or p.resolve().is_relative_to(output) for p in protected),
            "Output overlaps a selected source/model/index root")
    if output.exists() and not (output / "plan.json").exists():
        allowed = {"build.lock", "launch-logs", "runs", "events.jsonl", "preflight-attempts"}
        require(all(p.name in allowed for p in output.iterdir()), "Unrecognized output directory")
    return selection, manifests


def code_files(repo):
    return {"src/genshin_corpus/" + name: sha(repo / "src/genshin_corpus" / name) for name in CODE_FILES}


def source_stream(path):
    for i, row in enumerate(iter_inputs(path)):
        yield mapping(i, row), row["dense_text"]


def bind_inputs(repo, base, output):
    """No GPU model load. Hash all actual input/source assets, validate route mapping."""
    from ..retrieval.clean_based.source import InputReader
    selection, manifests = output_guard(repo, base, output)
    path, dense = manifests["dense"]
    reader = InputReader(dense["input_manifest"])
    try:
        require(reader.bundle_id == selection["source"]["input_bundle_id"]
                and reader.manifest["selection"] is None
                and reader.manifest["counts"] == dense["counts"], "Full selected input binding differs")
        audit = dense["length_audit"]
        folder = inside(path.parent, audit["folder"])
        require(sha(folder / "complete.json") == audit["receipt_sha256"], "Dense length audit receipt changed")
        rec = verify_receipt(folder, digest({"input": reader.bundle_id, "space": dense["space_id"]}))
        require(rec["status"] == "PASS" and rec["exceeded"] == 0 and rec["rows"] == dense["counts"]["documents"],
                "Invalid selected no-truncation length audit")
        files = {str(p): sha(p) for p in reader.binding.files}
        files.update({str(p): sha(p) for p, _ in manifests.values()})
        files[str(repo / base["selection"])] = sha(repo / base["selection"])
        files[str(folder / "complete.json")] = sha(folder / "complete.json")
        files.update({str(folder / n): h for n, h in rec["files"].items()})
        return selection, dense, reader, {"input_manifest": str(reader.path), "files": files,
            "length_audit": {"folder": str(folder), "maximum": rec["maximum"], "rows": rec["rows"]}}
    except BaseException:
        reader.close()
        raise


def make_encoder(repo, base, selection, dense, *, reference=False):
    from .benchmark_dense import Encoder
    from .sparse_trial import SparseEncoder, official_kernel
    from ..retrieval.clean_based.indexes import model_spec
    kernel, metadata = (official_kernel(repo / base["sparse_reference"]["wheel"], base["sparse_reference"]["sha256"])
                        if reference else (None, None))
    backbone = Encoder(model_spec(selection, dense), repo)
    require(backbone.space_id == dense["space_id"], "Pinned Sparse backbone/tokenizer differs from selected Dense")
    return SparseEncoder(backbone, head_sha256=base["sparse_head_sha256"], reference=kernel), metadata


def gpu_guard(encoder, config):
    torch = encoder.torch
    require(torch.cuda.is_available(), "Pinned CUDA device unavailable")
    free, total = torch.cuda.mem_get_info()
    require(free >= config["minimum_free_vram_gib"] * 1024 ** 3,
            "Insufficient free CUDA memory before model load; close other GPU tasks and retry")
    return {"name": torch.cuda.get_device_name(), "free_bytes": free, "total_bytes": total}


def plan_for(repo, config, reader, source, encoder):
    model_files = {str(encoder.encoder.path / name): checksum for name, checksum in encoder.encoder.files.items()}
    model_files[str(encoder.path)] = encoder.contract["head_sha256"]
    source = {**source, "files": {**source["files"], **model_files}}
    return {"version": VERSION, "scope": "full-selected", "input_bundle_id": reader.bundle_id,
            "counts": reader.manifest["counts"], "source": source, "code_sha256": code_files(repo),
            "configuration": encoder.contract, "space_id": encoder.space_id,
            "vocabulary_size": int(encoder.encoder.tokenizer.vocab_size), "shard_rows": config["shard_rows"],
            "storage": {"format": "SciPy uncompressed CSR NPZ float32", "scipy": version("scipy")},
            "encoding_policy": "no truncation/pruning; deterministic kernel; bounded length-sort; logged CUDA OOM halving"}


def check_files(files):
    for name, checksum in files.items():
        require(sha(name) == checksum, "Bound source/model/code file changed: " + name)


def source_preview(reader, rows):
    previews = []
    for mapping_row in rows:
        row = reader.row(mapping_row["input_row"])
        chunk = reader.read_chunk(row)
        reader.read_parent(row)
        reader.read_clean(row)
        reader.read_graph(row)
        reader.read_natural_parent(row)
        previews.append({"input_row": mapping_row["input_row"], "chunk_id": chunk["chunk_id"],
                         "granularities": row["granularities"], "text": chunk["retrieval_text"],
                         "clean_record": row["clean_record"], "chunk_locator": row["chunk_locator"],
                         "parent_locator": row["parent_locator"], "source_item": row["source_item"]})
    return previews


def p0_reference(repo, config, encoder, docs, vectors):
    from .sparse_trial import SparseSession
    accepted = read(repo / config["p0p2_accepted"])
    root = repo / config["p0p2_sparse"]
    key = str((root / "complete.json").relative_to((repo / config["p0p2_accepted"]).parent))
    require(sha(root / "complete.json") == accepted["accepted_artifacts"][key], "P0-P2 accepted receipt changed")
    complete = read(root / "complete.json")
    for name, checksum in complete["files"].items():
        require(sha(inside(root, name)) == checksum, "P0-P2 reference artifact changed")
    old = SparseSession(root / "sparse")
    require(old.manifest["space_id"] == encoder.space_id, "P0-P2 Sparse space changed")
    by_row = {}
    for matrix, rows in old.matrices:
        for i, row in enumerate(rows):
            lo, hi = matrix.indptr[i:i + 2]
            by_row[row["input_row"]] = dict(zip(map(int, matrix.indices[lo:hi]), map(float, matrix.data[lo:hi])))
    maximum, compared = 0., 0
    for doc, vector in zip(docs, vectors):
        if doc["input_row"] in by_row:
            original = by_row[doc["input_row"]]
            maximum = max(maximum, max((abs(vector.get(t, 0.) - original.get(t, 0.)) for t in set(vector) | set(original)), default=0.))
            compared += 1
    require(compared >= 16 and maximum <= .005, "P0-P2 numerical regression")
    return {"status": "PASS", "compared_rows": compared, "max_abs_weight_error": maximum, "tolerance": .005,
            "accepted_sha256": sha(repo / config["p0p2_accepted"]), "reference_space_id": encoder.space_id}


def preflight(repo, config, base, output):
    from ..retrieval.clean_based.trial import select_rows
    from .sparse_trial import csr_weights
    import numpy as np
    started = time.perf_counter()
    before = ram_guard(config)
    disk = disk_guard(output, config)
    folder = attempt(output / "preflight-attempts", "probe")
    event(output, "preflight_started", folder=str(folder))
    selection, dense, reader, source = bind_inputs(repo, base, output)
    encoder = None
    try:
        docs, sampling = select_rows(reader, Path(repo / selection["dense"]["manifest"]).parent, dense, 32)
        rows = [mapping(d["input_row"], d["row"]) for d in docs]
        texts = [d["row"]["dense_text"] for d in docs]
        encoder, reference = make_encoder(repo, base, selection, dense, reference=True)
        gpu = gpu_guard(encoder, config)
        plan = plan_for(repo, config, reader, source, encoder)
        if (output / "plan.json").exists():
            require(read(output / "plan.json") == plan, "Sparse preflight cannot change existing plan")
        # Verify the reused full tokenizer audit against every input identity/hash.
        audit_rows = iter(lines(Path(source["length_audit"]["folder"]) / "lengths.jsonl"))
        maximum, total = 0, 0
        for i, row in enumerate(iter_inputs(reader.path)):
            entry = next(audit_rows, None)
            require(entry is not None and entry["input_row"] == i
                    and all(entry[k] == row[k] for k in ("chunk_id", "dense_text_sha256"))
                    and type(entry["tokens"]) is int and 0 < entry["tokens"] <= encoder.encoder.contract["max_tokens"],
                    "Full input/length audit mismatch")
            maximum, total = max(maximum, entry["tokens"]), total + 1
        require(next(audit_rows, None) is None and maximum == source["length_audit"]["maximum"]
                and total == plan["counts"]["documents"], "Length audit coverage mismatch")
        encode_started = time.perf_counter()
        vectors = encode_shard(encoder, texts, config, folder, rows)
        encode_seconds = time.perf_counter() - encode_started
        for text in texts[:2]:
            encoder.encode([text], verify_reference=True)
        # Largest source included, plus unequal lengths and repeated/special tokens.
        special = ["旅行者旅行者 <s> </s> <pad> <unk>", texts[0]]
        together = encoder.encode(special, verify_reference=True)
        separate = [encoder.encode([text], verify_reference=True)[0] for text in special]
        delta = max((abs(a.get(t, 0.) - b.get(t, 0.)) for a, b in zip(together, separate) for t in set(a) | set(b)), default=0.)
        require(delta <= .005, "Sparse padding discrepancy")
        previous = p0_reference(repo, config, encoder, docs, vectors)
        probe_plan = {**plan, "scope": "finite-probe", "counts": {"documents": len(rows), **{
            g: sum(g in r["granularities"] for r in rows) for g in ("micro", "macro")}}, "shard_rows": 8,
            "probe_rows_sha256": digest(rows)}
        probe = folder / "index"
        calls = []
        def encode(text, current_rows):
            calls.extend(r["input_row"] for r in current_rows)
            return encode_shard(encoder, text, config, folder, current_rows)
        stream = lambda: iter(zip(rows, texts))
        build_stream(probe, probe_plan, stream(), encode, config=config, stop_after=1)
        saved = {str(p): sha(p) for p in probe.glob("shards/*/*") if p.is_file()}
        calls.clear()
        manifest = build_stream(probe, probe_plan, stream(), encode, config=config)
        require(not (set(r["input_row"] for r in rows[:8]) & set(calls)) and all(sha(p) == h for p, h in saved.items()),
                "Resume re-encoded/rewrote a committed shard")
        check = validate_store(probe, source=stream())
        session = StreamingSparseSession(probe)
        query_results = []
        for question in ("塔利雅的新颂歌灵感来自哪里？", "至冬先驱报", "旅行者 旅行者 <pad>"):
            query = encoder.encode([question], role="query", verify_reference=True)[0]
            found = session.search(query, space_id=encoder.space_id, top_k=10)
            for g in ("micro", "macro"):
                expected = [{**r, "score": sum(float(w) * v.get(t, 0.) for t, w in query.items())}
                            for r, v in zip(rows, vectors) if g in r["granularities"]]
                expected = sorted((r for r in expected if r["score"] > 0), key=lambda r: (-r["score"], r["input_row"]))[:10]
                require([r["chunk_id"] for r in found[g]] == [r["chunk_id"] for r in expected]
                        and np.allclose([r["score"] for r in found[g]], [r["score"] for r in expected], atol=1e-6, rtol=1e-5),
                        "Streaming Sparse search differs from independent dot")
            query_results.append({"question": question, "routes": found, "brute_dot": "PASS"})
        previews = source_preview(reader, rows)
        write(folder / "source-readback.json", previews)
        (folder / "source-cases.md").write_text("# P3 有限工程样本原文\n\n" + "\n\n".join(
            f"## input_row={p['input_row']} / {p['chunk_id']}\n\n~~~~text\n{p['text']}\n~~~~" for p in previews), encoding="utf-8")
        reader.binding.check()
        check_files(plan["source"]["files"])
        require(code_files(repo) == plan["code_sha256"], "Implementation changed during preflight")
        after = resources()
        require(after["available_ram_bytes"] >= config["minimum_running_ram_gib"] * 1024 ** 3, "RAM guard after probe")
        ensure_plan(output, plan)
        report = {"status": "PASS", "scope": "full input checks +32 source encodings; not full build or70Q",
                  "plan_binding": digest(plan), "utc": datetime.now(timezone.utc).isoformat(), "sampling": sampling,
                  "full_length_audit": {"rows": total, "maximum": maximum, "exceeded": 0},
                  "reference": reference, "official_kernel_checks": encoder.reference_checks,
                  "p0p2_comparison": previous, "padding_max_abs_weight_error": delta,
                  "resume": {"status": "PASS", "preserved_files": len(saved), "reencoded_committed_rows": 0},
                  "stored_validation": check, "probe_counts": manifest["counts"], "probe_nnz": manifest["nnz"],
                  "encoding_seconds": encode_seconds, "elapsed_seconds": time.perf_counter() - started,
                  "resources_before": before, "resources_after": after, "disk": disk,
                  "gpu_before_load": gpu,
                  "execution": config, "queries": query_results,
                  "sample_size_projection_only": {"nnz_per_row": manifest["nnz"] / len(rows),
                      "csr_payload_bytes_estimate": int(manifest["nnz"] / len(rows) * plan["counts"]["documents"] * 8)},
                  "limitations": ["structured sample; no reliable full-build ETA", "official kernel only; not full FlagEmbedding pipeline",
                                  "32 vectors initially encoded and re-encoded for recovery probe; no full corpus encoding"]}
        write(folder / "report.json", report)
        atomic_json(output / "preflight.json", {"folder": folder.relative_to(output).as_posix(),
                    "report_sha256": sha(folder / "report.json"), "plan_binding": digest(plan)})
        event(output, "preflight_pass", report=str(folder / "report.json"), counts=plan["counts"], resources_after=after)
        return report
    finally:
        reader.close()
        if encoder is not None:
            encoder.close()


def verify_plan(repo, config, base, output):
    plan = read(output / "plan.json")
    require(plan["scope"] == "full-selected" and plan["shard_rows"] == config["shard_rows"]
            and plan["code_sha256"] == code_files(repo), "Build implementation/shard strategy changed; new plan required")
    pointer = read(output / "preflight.json")
    report_path = inside(output, pointer["folder"]) / "report.json"
    require(pointer["plan_binding"] == digest(plan) and sha(report_path) == pointer["report_sha256"]
            and read(report_path)["status"] == "PASS" and read(report_path)["plan_binding"] == digest(plan), "No valid preflight")
    check_files(plan["source"]["files"])
    selection, manifests = output_guard(repo, base, output)
    dense = manifests["dense"][1]
    require(plan["input_bundle_id"] == selection["source"]["input_bundle_id"]
            and Path(plan["source"]["input_manifest"]).resolve() == Path(dense["input_manifest"]).resolve()
            and plan["counts"] == dense["counts"], "Selected full input changed")
    return plan, selection, dense


def run_build(repo, config, base, output, mode):
    plan, selection, dense = verify_plan(repo, config, base, output)
    if mode == "run":
        require(not (output / "shards").exists() and not (output / "manifest.json").exists(), "Existing build: use Resume")
    ram_guard(config)
    disk_guard(output, config)
    encoder = None
    try:
        encoder, _ = make_encoder(repo, base, selection, dense)
        require(encoder.space_id == plan["space_id"] and int(encoder.encoder.tokenizer.vocab_size) == plan["vocabulary_size"],
                "Build encoder space changed")
        gpu_guard(encoder, config)
        event(output, "build_started", mode=mode, execution=config, counts=plan["counts"])
        def publication_guard():
            check_files(plan["source"]["files"])
            require(code_files(repo) == plan["code_sha256"], "Code changed during build")
        with prevent_idle_sleep():
            result = build_stream(output, plan, source_stream(plan["source"]["input_manifest"]),
                lambda texts, rows: encode_shard(encoder, texts, config, output, rows), config=config,
                before_publish=publication_guard)
        return result
    finally:
        if encoder is not None:
            encoder.close()


def validate_job(repo, config, base, output):
    from ..retrieval.clean_based.source import InputReader
    plan, selection, dense = verify_plan(repo, config, base, output)
    # Tokenizer/snapshot only: no GPU load or document encoding in Validate.
    encoder = None
    try:
        encoder, _ = make_encoder(repo, base, selection, dense)
        require(encoder.space_id == plan["space_id"], "Validation model/tokenizer/head changed")
        report = validate_store(output, source=source_stream(plan["source"]["input_manifest"]))
        with InputReader(plan["source"]["input_manifest"]) as reader:
            from ..retrieval.clean_based.trial import select_rows
            docs, sampling = select_rows(reader, (repo / selection["dense"]["manifest"]).parent, dense, 16)
            previews = source_preview(reader, [mapping(d["input_row"], d["row"]) for d in docs])
        folder = attempt(output / "validations", "validate")
        write(folder / "source-readback.json", previews)
        report.update(scope="mechanical/source readback; not relevance/adoption", sampling=sampling,
                      plan_binding=digest(plan), resources=resources(), utc=datetime.now(timezone.utc).isoformat())
        write(folder / "report.json", report)
        atomic_json(output / "validation.json", {"folder": folder.relative_to(output).as_posix(),
                    "report_sha256": sha(folder / "report.json"), "manifest_sha256": sha(output / "manifest.json")})
        event(output, "validation_pass", report=str(folder / "report.json"), counts=report["counts"])
        return report
    finally:
        if encoder is not None:
            encoder.close()


class Tee:
    def __init__(self, console, log):
        self.console, self.log = console, log
    def write(self, body):
        self.log.write(body)
        self.log.flush()
        self.console.write(body)
        self.console.flush()
        return len(body)
    def flush(self):
        self.log.flush()
        self.console.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preflight", "run", "resume", "validate", "status"))
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[3]))
    parser.add_argument("--config", default="config/sparse-build-v1.json")
    parser.add_argument("--output")
    parser.add_argument("--max-batch", type=int, choices=range(1, 9))
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    config_path = (repo / args.config).resolve()
    config, base = settings(repo, config_path)
    if args.max_batch is not None:
        config["max_batch"] = args.max_batch
    output = (repo / (args.output or config["output"])).resolve()
    output_guard(repo, base, output, [(repo / config["p0p2_accepted"]).parent])
    if args.mode == "status":
        value = {name: read(output / name) for name in ("progress.json", "preflight.json", "validation.json") if (output / name).exists()}
        if (output / "events.jsonl").exists():
            # Bounded tail, including last resource snapshot and failure/interruption.
            with (output / "events.jsonl").open("rb") as handle:
                handle.seek(max(0, (output / "events.jsonl").stat().st_size - 65536))
                tail = handle.read().decode("utf-8", errors="replace").splitlines()
            value["last_event"] = json.loads(tail[-1]) if tail else None
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    with build_lock(output), offline_only():
        folder = attempt(output / "runs", args.mode)
        with (folder / "stdout.log").open("x", encoding="utf-8") as outlog, (folder / "stderr.log").open("x", encoding="utf-8") as errlog:
            with redirect_stdout(Tee(sys.stdout, outlog)), redirect_stderr(Tee(sys.stderr, errlog)):
                try:
                    event(output, "invocation_started", mode=args.mode, config_sha256=sha(config_path), run_folder=str(folder))
                    if args.mode == "preflight":
                        result = preflight(repo, config, base, output)
                    elif args.mode in {"run", "resume"}:
                        result = run_build(repo, config, base, output, args.mode)
                    else:
                        result = validate_job(repo, config, base, output)
                    write(folder / "result.json", {"status": "PASS", "mode": args.mode,
                          "result_sha256": digest(result), "resources": resources()})
                except BaseException as exc:
                    failure = {"mode": args.mode, "type": type(exc).__name__, "message": str(exc),
                               "resources": resources(), "utc": datetime.now(timezone.utc).isoformat()}
                    write(folder / "failure.json", failure)
                    event(output, "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed", **failure)
                    traceback.print_exc()
                    return 130 if isinstance(exc, KeyboardInterrupt) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
