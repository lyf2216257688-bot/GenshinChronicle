"""Auditable environment inventory and operator-run finite migration witnesses."""
import argparse
from importlib import metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
import sysconfig

from .evidence_io import digest, read, require, seal, sha, verify, write
from .runtime_compatibility import VERSION, compatibility_code


def inventory(repo):
    known = [Path(sys.base_prefix), repo / ".local/python312-base"]
    worktrees = []
    for line in subprocess.check_output(["git", "worktree", "list", "--porcelain"], cwd=repo, text=True).splitlines():
        if line.startswith("worktree "):
            worktrees.append(Path(line[9:]))
    for tree in worktrees:
        for name in (".local", ".venv", "venv", "env"):
            root = tree / name
            if root.exists():
                known += [p.parent for p in root.rglob("pyvenv.cfg")]
    rows = []
    for root in sorted(set(p.resolve() for p in known), key=str):
        site = root / "Lib/site-packages"
        packages = {d.metadata["Name"]: d.version for d in metadata.distributions(path=[str(site)])}
        cfg = root / "pyvenv.cfg"
        rows.append({"path": str(root), "venv": cfg.read_text(encoding="utf-8") if cfg.exists() else None,
                     "packages": packages, "pth": {p.name: p.read_text(encoding="utf-8", errors="replace") for p in site.glob("*.pth")}})
    paths = []
    for folder in ("config", "scripts", "src", "tests"):
        for path in (repo / folder).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".ps1", ".json", ".cmd", ".bat", ".toml"}:
                text = path.read_text(encoding="utf-8", errors="replace")
                matches = [n for n, line in enumerate(text.splitlines(), 1) if any(s in line for s in
                           ("3.12", "python312", "embedding-runtime", "candidate-runtime", "gte-reranker-runtime"))]
                if matches:
                    paths.append({"path": str(path.relative_to(repo)), "lines": matches})
    return {"python": platform.python_version(), "gil_disabled": sysconfig.get_config_var("Py_GIL_DISABLED"),
            "environments": rows, "call_sites": paths, "worktrees": [str(p) for p in worktrees],
            "cleanup": "BLOCKED until manual model validation, activation and no-consumer audit",
            "process_and_scheduler_audit": "NOT_COMPLETED; inventory is not deletion authorization"}


def fresh_output(repo, output):
    require(output.resolve().parent == (repo / "data/retrieval").resolve(), "Use a new direct data/retrieval child")
    output.mkdir(parents=True, exist_ok=False)


def baseline_index_check(output, samples, vectors, stored, config):
    """Preserve the actual discrepancy before rejecting a failed old baseline."""
    import numpy as np
    actual = np.asarray(vectors)
    expected = np.asarray([stored[s["chunk_id"]] for s in samples])
    require(actual.shape == expected.shape and actual.ndim == 2 and len(actual) == len(samples)
            and bool(np.isfinite(actual).all()) and bool(np.isfinite(expected).all()), "Invalid baseline vectors")
    np.save(output / "stored-dense.npy", expected, allow_pickle=False)
    rows = []
    for sample, left, right in zip(samples, actual, expected):
        error = np.abs(left.astype(np.float64) - right.astype(np.float64))
        tolerance = config["atol"] + config["rtol"] * np.abs(right.astype(np.float64))
        failed = ~np.isclose(left, right, rtol=config["rtol"], atol=config["atol"])
        worst = int(np.argmax(error))
        norm = float(np.linalg.norm(left.astype(np.float64)) * np.linalg.norm(right.astype(np.float64)))
        require(norm > 0, "Zero baseline vector")
        rows.append({"input_row": sample["input_row"], "chunk_id": sample["chunk_id"],
                     "status": "FAIL" if bool(failed.any()) else "PASS",
                     "bitwise_equal": bool(np.array_equal(left, right)),
                     "failed_components": int(failed.sum()), "max_absolute_error": float(error.max()),
                     "max_tolerance_ratio": float((error / tolerance).max()),
                     "cosine": float(left.astype(np.float64) @ right.astype(np.float64) / norm),
                     "worst_component": {"index": worst, "baseline": float(left[worst]),
                                         "stored": float(right[worst]), "allowed_error": float(tolerance[worst])}})
    report = {"status": "PASS" if all(r["status"] == "PASS" for r in rows) else "FAIL",
              "scope": "same Python 3.12 baseline versus stored Dense rows; not a Python 3.14 comparison",
              "rtol": config["rtol"], "atol": config["atol"], "rows": rows,
              "failed_rows": sum(r["status"] == "FAIL" for r in rows),
              "max_absolute_error": max(r["max_absolute_error"] for r in rows),
              "stored_dense_sha256": sha(output / "stored-dense.npy")}
    write(output / "baseline-index-check.json", report)
    require(report["status"] == "PASS",
            f"Baseline does not reproduce stored Dense rows ({report['failed_rows']}/{len(rows)}); "
            f"diagnostics retained at {output / 'baseline-index-check.json'}; no compatibility admission")
    return report


def original_batches(rows, settings):
    """Reconstruct the unchanged encoder's shard-local order; never encode here."""
    require(type(settings["max_batch"]) is int and 0 < settings["max_batch"] <= 2
            and type(settings["padded_token_budget"]) is int and settings["padded_token_budget"] > 0,
            "Unsupported historical batching point")
    require(len({r["input_row"] for r in rows}) == len(rows)
            and all(type(r["tokens"]) is int and r["tokens"] > 0 for r in rows), "Invalid source batch rows")
    ordered = sorted(enumerate(rows), key=lambda item: (item[1]["tokens"], item[0]))
    groups, position = [], 0
    while position < len(ordered):
        take = 1
        while (take < settings["max_batch"] and position + take < len(ordered)
               and ordered[position + take][1]["tokens"] * (take + 1) <= settings["padded_token_budget"]):
            take += 1
        groups.append([r for _, r in ordered[position:position + take]])
        position += take
    return groups


def source_batch_plan(repo, path, manifest, samples):
    """Exact original document shapes, without loading a tokenizer or model."""
    from .evidence_io import inside
    from .indexing.benchmark_io import lines, verify_receipt
    experiment_path, events_path = path.parents[2] / "experiment.json", path.parent / "events.jsonl"
    experiment = read(experiment_path)
    require(experiment["code_sha256"]["src/genshin_corpus/indexing/benchmark_dense.py"]
            == sha(repo / "src/genshin_corpus/indexing/benchmark_dense.py"),
            "Historical encoder code differs; cannot reconstruct batches")
    require(not any(e.get("event") == "cuda_oom" for e in lines(events_path)),
            "Historical OOM changed batch sizes; do not guess original batches")
    wanted = {s["input_row"]: s["chunk_id"] for s in samples}
    require(len(samples) == len(wanted) == 32, "Replay uses the same 32 document anchors")
    groups, found = [], set()
    for entry in manifest["shards"]:
        folder = inside(path.parent, entry["folder"])
        require(sha(folder / "complete.json") == entry["receipt_sha256"], "Source shard receipt changed")
        rows = list(lines(folder / "rows.jsonl"))
        if not any(r["input_row"] in wanted for r in rows):
            continue
        verify_receipt(folder, manifest["binding"])
        for batch in original_batches(rows, experiment["configuration"]):
            anchors = [r for r in batch if r["input_row"] in wanted]
            if not anchors:
                continue
            require(all(r["chunk_id"] == wanted[r["input_row"]] for r in anchors), "Anchor/index mapping differs")
            found.update(r["input_row"] for r in anchors)
            groups.append([{k: r[k] for k in ("input_row", "chunk_id", "tokens", "dense_text_sha256")} for r in batch])
    require(found == set(wanted) and len(groups) <= 32, "Missing/unbounded replay groups")
    return {"batches": groups, "experiment_sha256": sha(experiment_path), "events_sha256": sha(events_path)}


def replay_plan(repo, config):
    """Model-free plan for a bounded diagnostic, not a migration certificate."""
    from .retrieval.clean_based.indexes import selected
    source = repo / config["baseline"]
    encoded, check = read(source / "encoded.json"), read(source / "baseline-index-check.json")
    require(check["status"] == "FAIL" and check["rtol"] == config["rtol"] and check["atol"] == config["atol"],
            "Replay requires the fixed-tolerance failed baseline")
    require(all(config[k] == v for k, v in encoded["configuration"].items()), "Baseline configuration changed")
    current = compatibility_code(repo)
    require(all(current[k] == value for k, value in encoded["code"].items() if k != "runtime_migration.py"),
            "Encoder/source implementation changed since capture")
    require(all(sha(source / n) == h for n, h in encoded["encoded_files"].items())
            and sha(source / "stored-dense.npy") == check["stored_dense_sha256"], "Captured numbers changed")
    _, manifests = selected(repo, read(repo / config["retrieval_config"]))
    path, manifest = manifests["dense"]
    sparse = read(repo / config["sparse_root"] / "manifest.json")
    require(encoded["source_witness"]["source"] == {"dense": digest(manifest), "sparse": digest(sparse)}
            and encoded["contracts"]["dense"] == manifest["configuration"], "Source/index identity changed")
    batch_plan = source_batch_plan(repo, path, manifest, encoded["samples"][:config["sample_rows"]])
    groups = batch_plan["batches"]
    files = {n: sha(source / n) for n in ("capture-plan.json", "encoded.json", "dense.npy", "sparse.json",
                                        "stored-dense.npy", "baseline-index-check.json")}
    plan = {"scope": "original-batch-diagnostic-only", "source": str(source), "source_files": files,
            "index_manifest": str(path), "index_manifest_sha256": sha(path),
            **batch_plan,
            "current_code": current, "source_capture_code": encoded["code"], "configuration": config,
            "batches": groups, "new_forward_batches": sum(len(b) > 1 for b in groups),
            "new_forward_documents": sum(len(b) for b in groups if len(b) > 1),
            "migration_admitted": False}
    return encoded, manifest, plan


def document_jobs(reader, samples, plan):
    anchors = {s["input_row"]: s for s in samples}
    jobs = []
    for group in plan["batches"]:
        texts = []
        for item in group:
            row = reader.row(item["input_row"])
            require(all(row[k] == item[k] for k in ("chunk_id", "dense_text_sha256")), "Source row changed")
            if item["input_row"] in anchors:
                require(row["dense_text"] == anchors[item["input_row"]]["text"], "Anchor text changed")
            texts.append(row["dense_text"])
        jobs.append((group, texts))
    return jobs


def encode_document_jobs(encoder, jobs, samples):
    wanted = {s["input_row"] for s in samples}
    found = {}
    for group, texts in jobs:
        require(encoder.lengths(texts) == [r["tokens"] for r in group], "Original document token shape changed")
        values = encoder.encode(texts)
        require(len(values) == len(group), "Incomplete document batch")
        for row, value in zip(group, values):
            if row["input_row"] in wanted:
                require(row["input_row"] not in found, "Repeated document anchor")
                found[row["input_row"]] = value
    require(set(found) == wanted, "Missing document anchor")
    return found


def replay(repo, config):
    """Operator-only original-batch diagnosis. PASS never admits a migration."""
    import numpy as np
    from .indexing.benchmark_dense import Encoder
    from .indexing.benchmark_io import offline_only, resources
    from .retrieval.clean_based.indexes import selected, model_spec
    from .retrieval.clean_based.source import InputReader
    from .retrieval.clean_based.rerank_core import candidate_vectors
    require(platform.python_version() == "3.12.10" and not sysconfig.get_config_var("Py_GIL_DISABLED"),
            "Replay uses the unchanged regular-GIL Python 3.12 baseline")
    encoded, manifest, plan = replay_plan(repo, config)
    source = Path(plan["source"])
    vectors = np.load(source / "dense.npy", allow_pickle=False)
    require(vectors.shape == (34, 1024) and bool(np.isfinite(vectors).all()), "Invalid captured Dense values")
    samples = encoded["samples"][:32]
    stored = candidate_vectors(Path(plan["index_manifest"]), samples)
    require(np.array_equal(np.asarray([stored[s["chunk_id"]] for s in samples]),
                           np.load(source / "stored-dense.npy", allow_pickle=False)), "Stored reference changed")
    positions = {s["input_row"]: n for n, s in enumerate(samples)}
    batches, reused = [], []
    with InputReader(manifest["input_manifest"]) as reader:
        for group in plan["batches"]:
            if len(group) == 1:
                row = group[0]
                require(np.array_equal(vectors[positions[row["input_row"]]], stored[row["chunk_id"]]),
                        "A single-item source batch already differs; batch-only diagnosis is insufficient")
                reused.append(row["input_row"])
                continue
            texts = []
            for row in group:
                original = reader.row(row["input_row"])
                require(all(original[k] == row[k] for k in ("chunk_id", "dense_text_sha256")), "Source text changed")
                if row["input_row"] in positions:
                    require(original["dense_text"] == samples[positions[row["input_row"]]]["text"], "Captured text changed")
                texts.append(original["dense_text"])
            batches.append((group, texts))
    output = repo / config["replay_output"]
    fresh_output(repo, output)
    write(output / "plan.json", plan)
    result = vectors[:32].copy()
    with offline_only():
        selection, _ = selected(repo, read(repo / config["retrieval_config"]))
        encoder = Encoder(model_spec(selection, manifest), repo)
        require(encoder.contract == encoded["contracts"]["dense"], "Replay runtime/model differs from old baseline")
        for n, (group, texts) in enumerate(batches, 1):
            require(encoder.lengths(texts) == [r["tokens"] for r in group], "Historical token/padding shape changed")
            batch_vectors = encoder.encode(texts)
            require(batch_vectors.shape == (len(group), 1024) and bool(np.isfinite(batch_vectors).all()),
                    "Incomplete or invalid replay output")
            for row, vector in zip(group, batch_vectors):
                if row["input_row"] in positions:
                    result[positions[row["input_row"]]] = vector
            print(json.dumps({"event": "original_batch_replayed", "batch": n, "total": len(batches)}), flush=True)
        observation = resources()
    require(all(sha(source / name) == checksum for name, checksum in plan["source_files"].items()),
            "Source baseline changed during replay")
    np.save(output / "dense-replayed.npy", result, allow_pickle=False)
    try:
        baseline_index_check(output, samples, result, stored, config)
    finally:
        if (output / "baseline-index-check.json").exists():
            check = read(output / "baseline-index-check.json")
            summary = {"status": check["status"], "scope": plan["scope"], "migration_admitted": False,
                       "new_forward_batches": len(batches), "new_forward_documents": plan["new_forward_documents"],
                       "reused_single_item_rows": reused, "failed_rows": check["failed_rows"],
                       "resources": observation, "output": str(output)}
            write(output / "replay-summary.json", summary)
            seal(output, {"scope": plan["scope"], "status": check["status"], "migration_admitted": False})
    return summary


def capture(repo, config, baseline=False):
    import numpy as np
    from .indexing.benchmark_dense import Encoder
    from .indexing.benchmark_io import offline_only, resources
    from .indexing.sparse_trial import SparseEncoder, official_kernel
    from .retrieval.clean_based.indexes import DenseSession, model_spec, selected
    from .retrieval.clean_based.source import InputReader
    from .retrieval.clean_based.trial import select_rows
    from .retrieval.clean_based.rerank_core import candidate_vectors
    from .indexing.sparse_full import StreamingSparseSession
    expected_python = "3.12.10" if baseline else "3.14.5"
    require(platform.python_version() == expected_python, "Wrong migration witness interpreter")
    require(not sysconfig.get_config_var("Py_GIL_DISABLED"), "Use regular-GIL CPython")
    base = read(repo / config["retrieval_config"])
    selection, manifests = selected(repo, base)
    dense_path, dense_manifest = manifests["dense"]
    sparse_manifest = read(repo / config["sparse_root"] / "manifest.json")
    output = repo / config["baseline" if baseline else "candidate"]
    if baseline:
        with InputReader(dense_manifest["input_manifest"]) as reader:
            documents, sampling = select_rows(reader, dense_path.parent, dense_manifest, config["sample_rows"])
            samples = [{"input_row": d["input_row"], "chunk_id": d["row"]["chunk_id"],
                        "text": d["row"]["dense_text"], "role": "document"} for d in documents]
            batch_plan = source_batch_plan(repo, dense_path, dense_manifest, samples)
            jobs = document_jobs(reader, samples, batch_plan)
        samples += [{"text": text, "role": "query"} for text in config["queries"]]
        source_witness = {"selection": sampling, "source": {k: digest(m) for k, m in
                          (("dense", dense_manifest), ("sparse", sparse_manifest))}, "dense_document_batches": batch_plan}
    else:
        require((repo / config["baseline"] / "complete.json").is_file(),
                "Baseline is incomplete or failed; inspect its diagnostics before Candidate")
        verify(repo / config["baseline"])
        original = read(repo / config["baseline"] / "witness.json")
        samples, source_witness = original["samples"], original["source_witness"]
        require(source_witness["source"] == {"dense": digest(dense_manifest), "sparse": digest(sparse_manifest)},
                "Source indexes changed")
        batch_plan = source_batch_plan(repo, dense_path, dense_manifest, samples[:config["sample_rows"]])
        require(batch_plan == source_witness["dense_document_batches"], "Document batching point changed")
        with InputReader(dense_manifest["input_manifest"]) as reader:
            jobs = document_jobs(reader, samples[:config["sample_rows"]], batch_plan)
    fresh_output(repo, output)
    write(output / "capture-plan.json", {"mode": "baseline" if baseline else "candidate",
        "samples": samples, "source_witness": source_witness, "configuration": config, "code": compatibility_code(repo)})
    with offline_only():
        encoder = Encoder(model_spec(selection, dense_manifest), repo)
        ref, ref_info = official_kernel(repo / base["sparse_reference"]["wheel"], base["sparse_reference"]["sha256"])
        sparse = SparseEncoder(encoder, head_sha256=base["sparse_head_sha256"], reference=ref)
        if baseline:
            require(encoder.space_id == dense_manifest["space_id"] and sparse.space_id == sparse_manifest["space_id"],
                    "Baseline differs from existing indexes")
        vectors, weights, tokens = [], [], []
        documents = encode_document_jobs(encoder, jobs, samples[:config["sample_rows"]])
        for n, item in enumerate(samples):
            text, role = item["text"], item["role"]
            tokens.append(encoder.tokenizer(encoder.project([text], role), padding=False, truncation=False)["input_ids"][0])
            vectors.append(documents[item["input_row"]] if role == "document" else encoder.encode([text], role=role)[0])
            weights.append(sparse.encode([text], role=role, verify_reference=True)[0])
            print(json.dumps({"event": "migration_sample", "sample": n + 1, "total": len(samples)}), flush=True)
        vectors = np.asarray(vectors)
        # Data first: a failed stored-index check must not lose all 34 forwards.
        np.save(output / "dense.npy", vectors, allow_pickle=False)
        write(output / "sparse.json", [{str(k): v for k, v in row.items()} for row in weights])
        encoded = {"samples": samples, "source_witness": source_witness, "tokens": tokens,
            "contracts": {"dense": encoder.contract, "sparse": sparse.contract},
            "official_sparse_kernel": bool(sparse.reference_checks) and all(c["max_abs_error"] <= 1e-6
                and c["status"] == "PASS" for c in sparse.reference_checks),
            "reference_checks": sparse.reference_checks, "reference": ref_info, "resources": resources(),
            "configuration": config, "code": compatibility_code(repo),
            "encoded_files": {name: sha(output / name) for name in ("dense.npy", "sparse.json")}}
        write(output / "encoded.json", encoded)
        if baseline:
            stored = candidate_vectors(dense_path, samples[:config["sample_rows"]])
            baseline_index_check(output, samples[:config["sample_rows"]], vectors[:config["sample_rows"]], stored, config)
        # Only the migration probe may compare new vectors against old-index results.
        # Actual query admission requires a sealed certificate, never this bypass.
        rankings = {"dense": [], "sparse": []}
        for kind, session in (("dense", DenseSession(dense_path)),
                              ("sparse", StreamingSparseSession(repo / config["sparse_root"]))):
            for n in range(config["sample_rows"], len(samples)):
                vector = vectors[n] if kind == "dense" else weights[n]
                found = session.search(vector, space_id=session.manifest["space_id"], top_k=10)
                rankings[kind].append({g: [r["chunk_id"] for r in values] for g, values in found.items()})
            if hasattr(session, "close"):
                session.close()
        witness = {**encoded, "rankings": rankings}
        write(output / "witness.json", witness)
        seal(output, {"scope": "finite-runtime-migration", "python": expected_python})
    return {"status": "complete", "scope": "finite-runtime-migration", "output": str(output)}


def compare(repo, config):
    import numpy as np
    roots = {k: repo / config[k] for k in ("baseline", "candidate")}
    for root in roots.values():
        require((root / "complete.json").is_file(), "Migration witness incomplete or failed: " + str(root))
        verify(root)
    a, b = [read(roots[k] / "witness.json") for k in ("baseline", "candidate")]
    baseline_check = read(roots["baseline"] / "baseline-index-check.json")
    require(baseline_check["status"] == "PASS" and baseline_check["rtol"] == config["rtol"]
            and baseline_check["atol"] == config["atol"], "Stored-index baseline check did not pass")
    require(a["configuration"] == b["configuration"] == config and a["samples"] == b["samples"]
            and a["source_witness"] == b["source_witness"] and a["code"] == b["code"] == compatibility_code(repo),
            "Migration witness binding differs")
    va, vb = [np.load(roots[k] / "dense.npy", allow_pickle=False) for k in ("baseline", "candidate")]
    wa, wb = [read(roots[k] / "sparse.json") for k in ("baseline", "candidate")]
    require(va.shape == vb.shape == (len(a["samples"]), 1024)
            and bool(np.isfinite(va).all()) and bool(np.isfinite(vb).all()), "Invalid Dense witness")
    sparse_ok = len(wa) == len(wb) == len(a["samples"])
    for left, right in zip(wa, wb):
        sparse_ok = sparse_ok and left.keys() == right.keys() and all(np.isfinite(v) for v in [*left.values(), *right.values()])
        sparse_ok = sparse_ok and np.allclose([left[k] for k in left], [right[k] for k in left], rtol=config["rtol"], atol=config["atol"])
    checks = {"tokens": a["tokens"] == b["tokens"], "dense": bool(np.allclose(va, vb, rtol=config["rtol"], atol=config["atol"])),
              "sparse": bool(sparse_ok), "rankings": a["rankings"] == b["rankings"],
              "official_sparse_kernel": a["official_sparse_kernel"] is True and b["official_sparse_kernel"] is True}
    from .retrieval.clean_based.indexes import selected
    _, manifests = selected(repo, read(repo / config["retrieval_config"]))
    source = {"dense": manifests["dense"][1], "sparse": read(repo / config["sparse_root"] / "manifest.json")}
    require(a["source_witness"]["source"] == {k: digest(v) for k, v in source.items()}, "Indexes changed after witness capture")
    for kind in source:
        require(a["contracts"][kind] == source[kind]["configuration"], "Baseline contract differs from source index")
        if kind == "sparse":
            require({k: v for k, v in a["contracts"][kind].items() if k != "backbone"}
                    == {k: v for k, v in b["contracts"][kind].items() if k != "backbone"},
                    "Sparse representation changed")
        old = a["contracts"][kind] if kind == "dense" else a["contracts"][kind]["backbone"]
        new = b["contracts"][kind] if kind == "dense" else b["contracts"][kind]["backbone"]
        require(old["python"] == "3.12.10" and new["python"] == "3.14.5"
                and {k: v for k, v in old.items() if k != "python"}
                == {k: v for k, v in new.items() if k != "python"}, "Unexpected model/dependency change")
    spaces = {k: {"source": source[k]["space_id"], "target": digest(b["contracts"][k]),
                  "manifest_digest": digest(source[k])} for k in source}
    report = {"version": VERSION, "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
        "spaces": spaces, "code": compatibility_code(repo), "dense_bitwise_equal": bool(np.array_equal(va, vb)),
        "dense_max_absolute_error": float(np.max(np.abs(va - vb))), "rtol": config["rtol"], "atol": config["atol"],
        "scope": "32 fixed documents and two engineering queries; not semantic or corpus-wide acceptance",
        **{k: {"path": str(v.resolve()), "complete_sha256": sha(v / "complete.json")} for k, v in roots.items()}}
    output = repo / config["certificate"]
    fresh_output(repo, output)
    write(output / "report.json", report)
    seal(output, {"status": report["status"]})
    require(report["status"] == "PASS", "Migration failed; preserve old runtimes and indexes")
    from .runtime_compatibility import index_space
    for kind in source:
        index_space(repo, {"runtime_compatibility": config["certificate"]}, source[kind], b["contracts"][kind], kind)
    return report


def activate(repo, config):
    from .runtime_compatibility import index_space
    from .retrieval.clean_based.indexes import selected
    base = read(repo / config["retrieval_config"])
    _, manifests = selected(repo, base)
    candidate = read(repo / config["candidate"] / "witness.json")
    source = {"dense": manifests["dense"][1], "sparse": read(repo / config["sparse_root"] / "manifest.json")}
    for kind in source:
        index_space(repo, {"runtime_compatibility": config["certificate"]}, source[kind], candidate["contracts"][kind], kind)
    require(platform.python_version() == "3.14.5", "Activate from Python 3.14.5")
    # Check actual installed encoder identity, not only the saved candidate witness.
    from .indexing.benchmark_dense import Encoder
    from .retrieval.clean_based.indexes import model_spec
    selection, _ = selected(repo, base)
    actual = Encoder(model_spec(selection, source["dense"]), repo)
    require(actual.contract == candidate["contracts"]["dense"], "Installed runtime changed since validation")
    from .retrieval.clean_based.rerank_job import load_plan, verified_completion
    rerank_config = read(repo / "config/clean-reranking-v1.json")
    _, rerank_plan = load_plan(repo, rerank_config, repo / rerank_config["output"])
    smoke_root = repo / rerank_config["smoke_output"]
    smoke_plan = read(smoke_root / "plan.json")
    require(smoke_plan["base_plan_binding"] == digest(rerank_plan), "Reranker Smoke plan differs")
    require(verified_completion(smoke_root, smoke_plan)["scope"] == "engineering-smoke", "Validate real Reranker Smoke first")
    from .build_runtime import atomic_json
    backup = repo / ".local/runtime-py314/activation-backup"
    backup.mkdir(parents=True, exist_ok=False)
    changes = {config["retrieval_config"]: {"dense_python": config["python"], "runtime_compatibility": config["certificate"]},
               "config/sparse-build-v1.json": {"python": config["python"]},
               "config/recall-experiment-v1.json": {"python": config["python"]}}
    for name, updates in changes.items():
        value = read(repo / name)
        write(backup / Path(name).name, value)
        atomic_json(repo / name, {**value, **updates})
    return {"status": "activated", "cleanup": "NOT_PERFORMED; no-consumer audit still required",
            "historical_jobs": "Changed configs cannot Resume historical plans; completed artifacts remain immutable"}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("inventory", "baseline", "candidate", "compare", "activate", "replay"))
    parser.add_argument("--repo", default=".")
    parser.add_argument("--config", default="config/python314-migration-v1.json")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    config = read(repo / args.config)
    require(config["version"] == "gc-python314-migration-1" and config["sample_rows"] == 32
            and config["rtol"] == 1e-5 and config["atol"] == 1e-6, "Unapproved migration operating point")
    if args.mode == "inventory":
        result = inventory(repo)
        if args.output:
            write(repo / args.output, result)
    elif args.mode in {"baseline", "candidate"}: result = capture(repo, config, args.mode == "baseline")
    elif args.mode == "compare": result = compare(repo, config)
    elif args.mode == "replay": result = replay(repo, config)
    else: result = activate(repo, config)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
