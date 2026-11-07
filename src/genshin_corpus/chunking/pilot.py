"""Finite local pilot CLI. Tokenizers only, no model inference or serving hooks."""
from __future__ import annotations

import argparse
from collections import Counter, OrderedDict, defaultdict
import json
import os
from pathlib import Path
import time
from contextlib import contextmanager

from ..clean import CleanRef, iter_blocks, resolve_clean_ref, validate_record
from ..clean.models import pointer_get
from ..evidence_io import digest, inside, read, sha
from ..build_runtime import atomic_json, checked_receipt, record_jobs
from ..model_calls import locked
from .core import SCHEMA_VERSION, ChunkProfile, chunk_parent, identity, retrieval_text
from .policy import POLICY_VERSION
from .reader import read_natural_parent
from .structure import adapt_record, label


def distribution(values):
    values = sorted(values)
    if not values:
        return {"count": 0}
    return {"count": len(values), "min": values[0], "p50": values[(len(values)-1)//2],
            "p90": values[int((len(values)-1)*.9)], "p99": values[int((len(values)-1)*.99)],
            "max": values[-1], "sum": sum(values)}


def _jsonl(handle, value):
    handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def _validate_parts(record, adapted, chunks, profile, *, blocks=None):
    if blocks is None:
        blocks = {block["block_id"]: block for block in iter_blocks(record)}
    coverage = {level: defaultdict(list) for level in ("micro", "macro")}
    checked_contexts = set()

    def check_context(source):
        key = identity([source["clean_ref"], source["text"]])
        if key in checked_contexts:
            return
        ref = CleanRef(**source["clean_ref"])
        if ref.observation_id != record["observation_id"]:
            raise ValueError("Cross-observation source context")
        value = pointer_get(blocks[ref.block_id], ref.value_pointer)
        if value.get("text", value.get("lexeme"))[ref.char_start:ref.char_end] != source["text"]:
            raise ValueError("Source context mismatch")
        checked_contexts.add(key)

    relations = {r["relation_id"]: r for r in record["relations"]}
    for parent in adapted["parents"]:
        for source in parent["context"].get("label_sources", []):
            check_context(source)
        for task in parent["context"].get("task_context", []):
            for source in task["sources"]:
                check_context(source)
            if any(relations.get(r["relation_id"]) != r for r in task["source_relations"]):
                raise ValueError("Source task relation mismatch")
    for graph in adapted["graphs"]:
        if set(graph["derived_nodes"]) != {n["source_id"] for n in graph["group"]["nodes"]}:
            raise ValueError("Derived graph dropped a source node")
        for info in graph["derived_nodes"].values():
            for source in info["condition_sources"] + info["source_annotations"]:
                check_context(source)
    for chunk in chunks:
        hard = profile.micro_hard if "micro" in chunk["granularities"] else profile.macro_hard
        if chunk["tokens"] > hard:
            raise ValueError("Oversized chunk")
        for part in chunk["parts"]:
            for source_ref in [part["clean_ref"], *part.get("alias_clean_refs", [])]:
                ref = CleanRef(**source_ref)
                if ref.observation_id != record["observation_id"]:
                    raise ValueError("Cross-observation reference")
                value = pointer_get(blocks[ref.block_id], ref.value_pointer)
                text = value.get("text", value.get("lexeme"))
                if text[ref.char_start:ref.char_end] != part["text"]:
                    raise ValueError("Chunk source slice mismatch")
                for level in chunk["granularities"]:
                    coverage[level][(ref.block_id, ref.value_pointer)].append((ref.char_start, ref.char_end))
            source_contexts = (part.get("upstream_context", []) + part.get("option_context", [])
                               + part.get("condition_sources", []) + part.get("source_annotations", [])
                               + ([part["article_title"]] if part.get("article_title") else []))
            for source in source_contexts:
                check_context(source)
    for level in coverage:
        by_parent = defaultdict(list)
        for chunk in chunks:
            if level in chunk["granularities"]:
                by_parent[chunk["parent_id"]].append(chunk["display_order_by_granularity"][level])
        if any(sorted(orders) != list(range(len(orders))) for orders in by_parent.values()):
            raise ValueError("Invalid per-granularity display order")
    for decision in adapted["decisions"]:
        if decision["decision"] != "include":
            continue
        ref = decision["clean_ref"]
        for level in coverage:
            cursor = 0
            for start, end in sorted(coverage[level][(ref["block_id"], ref["value_pointer"])]):
                if start != cursor:
                    raise ValueError("Admitted source text omitted or duplicated within a granularity")
                cursor = end
            if cursor != ref["char_end"]:
                raise ValueError("Admitted source coverage incomplete")


class TokenCounter:
    """Exact tokenizer lengths with a bounded per-record text cache."""

    def __init__(self, tokenizer, max_chars=2_000_000):
        self.tokenizer = tokenizer
        self.max_chars = max_chars
        self.cache = OrderedDict()
        self.chars = 0
        self.calls = self.hits = 0

    def __call__(self, text):
        self.calls += 1
        if text in self.cache:
            self.hits += 1
            self.cache.move_to_end(text)
            return self.cache[text]
        value = len(self.tokenizer.encode(text, add_special_tokens=True).ids)
        if len(text) <= self.max_chars:
            while self.cache and (self.chars + len(text) > self.max_chars or len(self.cache) >= 8192):
                key, _ = self.cache.popitem(last=False)
                self.chars -= len(key)
            self.cache[text] = value
            self.chars += len(text)
        return value


@contextmanager
def _atomic_text(path, *, newline="\n"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("w", encoding="utf-8", newline=newline) as handle:
        yield handle
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


_chunk_worker = None
PROBE_QUERY = "这些文字记载了什么故事，人物的经历和前置任务条件有什么联系？"


def _init_chunk_worker(source_root, output, manifest_identity, selection, tokenizer_paths, binding):
    global _chunk_worker
    # Each process owns two tokenizer instances. Prevent an extra Rayon pool
    # per process from oversubscribing the requested CPU workers.
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["RAYON_NUM_THREADS"] = "1"
    from tokenizers import Tokenizer

    tokenizers = []
    try:
        for path in tokenizer_paths:
            tokenizer = Tokenizer.from_file(str(path))
            tokenizer.no_truncation()
            tokenizer.no_padding()
            tokenizers.append(tokenizer)
    except Exception as error:
        # Pool initializers must not keep crashing and respawning indefinitely.
        _chunk_worker = RuntimeError(f"Tokenizer initialization failed: {error}")
        return
    _chunk_worker = (Path(source_root), Path(output), manifest_identity, selection, tokenizers, binding)


def _chunk_record_job(entry):
    if isinstance(_chunk_worker, Exception):
        raise _chunk_worker
    _, output, _, _, _, _ = _chunk_worker
    with locked(output / "_records" / entry["observation_id"] / "work.lock"):
        return _build_chunk_record(entry)


def _build_chunk_record(entry):
    source_root, output, manifest_identity, selection, tokenizers, binding = _chunk_worker
    cid = entry["content_id"]
    source = inside(source_root, entry["record_path"])
    before_hash = sha(source)
    if before_hash != entry["record_sha256"]:
        raise ValueError("Selected Clean hash mismatch")
    shard = output / "_records" / entry["observation_id"]
    receipt_path = shard / "receipt.json"
    if receipt_path.exists():
        receipt = checked_receipt(receipt_path, output, binding)
        if receipt["summary"]["source"]["content_id"] != cid or receipt["summary"]["source"]["before_sha256"] != before_hash:
            raise ValueError("Chunk checkpoint source identity mismatch")
        return receipt["summary"]
    started = time.monotonic()
    record = read(source)
    validate_record(record)
    if (record["observation_id"], record["source_item"]["content_id"],
        record["source_item"]["source"], record["source_item"]["locale"], record["input"]["raw_run_id"]) != (
            entry["observation_id"], cid, *manifest_identity):
        raise ValueError("Selected Clean identity/source mismatch")
    blocks = {block["block_id"]: block for block in iter_blocks(record)}
    adapted = adapt_record(record, blocks=blocks)
    qwen, bge = tokenizers
    count, bge_count = TokenCounter(qwen), TokenCounter(bge)
    profiles = [ChunkProfile(**item) for item in selection["profiles"]]
    kinds = Counter()
    with _atomic_text(shard / "parents.jsonl") as handle:
        for parent in adapted["parents"]:
            read_natural_parent(parent, record, blocks=blocks)
            parent["natural_text_qwen_tokens"] = count(retrieval_text(parent, [
                part for unit in parent["units"] for part in unit["parts"]])) if parent["units"] else 0
            _jsonl(handle, parent)
            kinds[parent["kind"]] += 1
    for name, values in (("graphs.jsonl", adapted["graphs"]), ("admission.jsonl", adapted["decisions"])):
        with _atomic_text(shard / name) as handle:
            for value in values:
                _jsonl(handle, value)
    stats = {}
    record_counts = {}
    reviews = []
    official_ref_checks = 0
    # Pair post-processing only adds fixed special tokens. This exact identity
    # avoids normalizing and tokenizing every document a second time.
    pair_overhead = (bge_count(PROBE_QUERY) + bge.num_special_tokens_to_add(True)
                     - 2 * bge.num_special_tokens_to_add(False))
    for index, profile in enumerate(profiles):
        chunks = [chunk for parent in adapted["parents"] if "exact_alias_of_parent_id" not in parent
                  for chunk in chunk_parent(parent, count, profile,
                    secondary_fits=lambda text: bge_count(text) <= selection["bge_document_token_cap"])]
        _validate_parts(record, adapted, chunks, profile, blocks=blocks)
        s = {"tokens": defaultdict(list), "bge_tokens": [], "chunks": 0,
             "split_reasons": Counter(), "retrieval_chars": 0}
        with _atomic_text(shard / f"chunks-{profile.name}.jsonl") as handle:
            for chunk in chunks:
                chunk["bge_document_tokens"] = bge_count(chunk["retrieval_text"])
                chunk["bge_probe_pair_tokens"] = chunk["bge_document_tokens"] + pair_overhead
                _jsonl(handle, chunk)
                s["chunks"] += 1
                s["retrieval_chars"] += len(chunk["retrieval_text"])
                s["bge_tokens"].append(chunk["bge_probe_pair_tokens"])
                for level in chunk["granularities"]:
                    s["tokens"][level].append(chunk["tokens"])
                s["split_reasons"].update(part.get("split_reason", "intact") for part in chunk["parts"])
        stats[profile.name] = s
        record_counts[profile.name] = len(chunks)
        if index == 0:
            for chunk in chunks[:2]:
                for part in chunk["parts"][:2]:
                    resolved = resolve_clean_ref(CleanRef(**part["clean_ref"]), record=record)
                    if resolved["text"] != part["text"]:
                        raise ValueError("Public CleanRef resolver disagrees")
                    official_ref_checks += 1
            if cid in selection["review_content_ids"]:
                candidates = [chunk for chunk in chunks if "macro" in chunk["granularities"]]
                wanted = ("常识来说", "之前和提纳里处理过", "我们须弥特有", "枪这种武器", "兰罗摩", "保险")
                chosen = candidates[:2] + [chunk for chunk in candidates if any(t in chunk["body_text"] for t in wanted)]
                reviews.extend({"title": label(record["document"]["title"]), **chunk}
                               for chunk in {c["chunk_id"]: c for c in chosen}.values())
    atomic_json(shard / "review.json", reviews)
    if sha(source) != before_hash:
        raise ValueError("Clean source changed during chunk build")
    components = Counter(component["component_id"] for section in record["sections"] for component in section["components"])
    summary = {"record": {"content_id": cid, "title": label(record["document"]["title"]),
                         "parents": len(adapted["parents"]), "chunks": record_counts},
               "source": {**entry, "before_sha256": before_hash, "after_sha256": before_hash},
               "parents": len(adapted["parents"]), "graphs": len(adapted["graphs"]),
               "aliases": sum("exact_alias_of_parent_id" in p for p in adapted["parents"]),
               "kinds": kinds, "components": components,
               "decisions": Counter(d["decision"] for d in adapted["decisions"]),
               "reasons": Counter(d["reason"] for d in adapted["decisions"]),
               "official_ref_checks": official_ref_checks, "stats": stats,
               "elapsed_seconds": time.monotonic() - started,
               "tokenizer_cache": {"qwen_calls": count.calls, "qwen_hits": count.hits,
                                   "bge_calls": bge_count.calls, "bge_hits": bge_count.hits}}
    names = ["parents.jsonl", "graphs.jsonl", "admission.jsonl", "review.json"]
    names += [f"chunks-{p.name}.jsonl" for p in profiles]
    files = {str((shard / name).relative_to(output).as_posix()): sha(shard / name) for name in names}
    atomic_json(receipt_path, {"binding": binding, "summary": summary, "files": files})
    return summary


def run_pilot(manifest_path: Path, selection_path: Path, qwen_path: Path,
              bge_path: Path, output: Path, *, workers: int = 12, resume: bool = False):
    from tokenizers import __version__ as tokenizer_version

    if type(workers) is not int or workers < 1:
        raise ValueError("workers must be a positive integer")
    started = time.monotonic()
    manifest_path, selection_path = Path(manifest_path), Path(selection_path)
    manifest, selection = read(manifest_path), read(selection_path)
    initial_manifest_hash, initial_selection_hash = sha(manifest_path), sha(selection_path)
    code_root = Path(__file__).parent
    code_hashes = {path.name: sha(path) for path in code_root.glob("*.py")}
    supporting_paths = [code_root.parent / "build_runtime.py", *sorted((code_root.parent / "clean").glob("*.py"))]
    supporting_hashes = {str(path.relative_to(code_root.parent)): sha(path) for path in supporting_paths}
    tokenizer_paths = [Path(qwen_path).resolve(), Path(bge_path).resolve()]
    tokenizer_hashes = {str(path): sha(path) for path in tokenizer_paths}
    if manifest.get("status") != "complete" or manifest.get("schema_version") != "gc-clean-1":
        raise ValueError("Complete Clean V1 manifest required")
    if manifest["accounted_detail_count"] != len(manifest["records"]) or len(manifest["records"]) != manifest["input_detail_count"]:
        raise ValueError("Invalid Clean manifest accounting")
    selected = selection["content_ids"]
    if len(selected) != len(set(selected)) or not selected:
        raise ValueError("A finite unique selection is required")
    entries = {entry["content_id"]: entry for entry in manifest["records"]}
    if len(entries) != len(manifest["records"]) or set(selected) - entries.keys():
        raise ValueError("Duplicate manifest identity or absent selection")
    for cid in selected:
        observation = entries[cid].get("observation_id")
        if not isinstance(observation, str) or len(observation) != 64 or any(c not in "0123456789abcdef" for c in observation):
            raise ValueError("Invalid Clean observation filename identity")
    profiles = [ChunkProfile(**item) for item in selection["profiles"]]
    if not profiles or len({p.name for p in profiles}) != len(profiles) or any(
            not p.name.replace("_", "").replace("-", "").isalnum() for p in profiles):
        raise ValueError("Unique filesystem-safe chunk profile names required")
    if type(selection["bge_document_token_cap"]) is not int or selection["bge_document_token_cap"] < 1:
        raise ValueError("Positive BGE document cap required")
    output = Path(output).resolve()
    source_root = manifest_path.resolve().parent.parent
    if output.is_relative_to(source_root) or any(p.name.lower() in {"raw", "clean"} for p in [output, *output.parents]):
        raise ValueError("Output must be a downstream directory")
    if not resume:
        output.mkdir(parents=True, exist_ok=False)
    elif not output.is_dir() or not (output / "build-state.json").exists():
        raise ValueError("Resume requires an existing checkpointed build")
    binding_value = {"clean_manifest_sha256": initial_manifest_hash, "selection_sha256": initial_selection_hash,
                     "code_hashes": code_hashes, "supporting_code_hashes": supporting_hashes,
                     "tokenizers": tokenizer_hashes, "tokenizers_version": tokenizer_version}
    binding = digest(binding_value)
    with locked(output / "build.lock"):
        state_path = output / "build-state.json"
        if state_path.exists() and read(state_path)["binding"] != binding:
            raise ValueError("Chunk build binds different inputs, configuration or implementation")
        completed = {}

        def checkpoint(status, error=None):
            atomic_json(state_path, {"binding": binding, "inputs": binding_value, "status": status,
                        "completed_records": len(completed), "total_records": len(selected),
                        "workers": workers, "error": error})

        def verify_inputs():
            if sha(manifest_path) != initial_manifest_hash or sha(selection_path) != initial_selection_hash:
                raise ValueError("Clean manifest or selection changed during build")
            if code_hashes != {p.name: sha(p) for p in code_root.glob("*.py")} or any(
                    sha(p) != supporting_hashes[str(p.relative_to(code_root.parent))] for p in supporting_paths):
                raise ValueError("Implementation changed during build")
            if tokenizer_hashes != {str(p): sha(p) for p in tokenizer_paths}:
                raise ValueError("Tokenizer changed during build")
            for cid in selected:
                if sha(inside(source_root, entries[cid]["record_path"])) != entries[cid]["record_sha256"]:
                    raise ValueError("Clean source changed during build")

        if (output / "manifest.json").exists():
            old = read(output / "manifest.json")
            if old.get("build_binding") != binding or old.get("status") != "complete":
                raise ValueError("Existing final manifest conflicts with build")
            for name, checksum in old["artifacts"].items():
                if sha(inside(output, name)) != checksum:
                    raise ValueError("Completed Chunk artifact checksum mismatch")
            verify_inputs()
            state = read(state_path)
            if state["status"] != "complete":
                atomic_json(state_path, {**state, "status": "complete",
                                        "completed_records": len(selected), "error": None})
            return read(output / "report.json")
        checkpoint("partial")
        last_flush = time.monotonic()
        try:
            # Check completed receipts before starting tokenizers. Only pending
            # records enter the pool, keeping no-op resume small and fast.
            pending = []
            for cid in selected:
                entry = entries[cid]
                receipt_path = output / "_records" / entry["observation_id"] / "receipt.json"
                if receipt_path.exists():
                    receipt = checked_receipt(receipt_path, output, binding)
                    if receipt["summary"]["source"] != {**entry, "before_sha256": entry["record_sha256"], "after_sha256": entry["record_sha256"]}:
                        raise ValueError("Chunk checkpoint source mismatch")
                    completed[cid] = receipt["summary"]
                else:
                    pending.append(entry)
            if pending:
                manifest_identity = (manifest["source"], manifest["locale"], manifest["raw_run_id"])
                for summary in record_jobs(_chunk_record_job, pending, workers=workers,
                        initializer=_init_chunk_worker,
                        initargs=(source_root, output, manifest_identity, selection, tokenizer_paths, binding)):
                    cid = summary["record"]["content_id"]
                    completed[cid] = summary
                    if time.monotonic() - last_flush >= 2:
                        checkpoint("partial")
                        last_flush = time.monotonic()
                    print(json.dumps({"record": cid, "done": len(completed),
                                      "total": len(selected), "chunks": summary["record"]["chunks"]}), flush=True)
            if set(completed) != set(selected):
                raise ValueError("Chunk job accounting incomplete")
            # Revalidate worker receipts before deterministic streaming merge.
            artifact_names = ["parents.jsonl", "graphs.jsonl", "admission.jsonl"]
            artifact_names += [f"chunks-{p.name}.jsonl" for p in profiles]
            for cid in selected:
                checked_receipt(output / "_records" / entries[cid]["observation_id"] / "receipt.json", output, binding)
            for name in artifact_names:
                with _atomic_text(output / name) as target:
                    for cid in selected:
                        shard = output / "_records" / entries[cid]["observation_id"]
                        with (shard / name).open(encoding="utf-8") as source_file:
                            for line in source_file:
                                target.write(line)
            reviews = []
            totals = {name: Counter() for name in ("kinds", "components", "decisions", "reasons")}
            stats = {p.name: {"tokens": defaultdict(list), "bge_tokens": [], "chunks": 0,
                             "split_reasons": Counter(), "retrieval_chars": 0} for p in profiles}
            for cid in selected:
                summary = completed[cid]
                reviews.extend(read(output / "_records" / entries[cid]["observation_id"] / "review.json"))
                for name, counter in totals.items():
                    counter.update(summary[name])
                for name, s in stats.items():
                    other = summary["stats"][name]
                    for level, lengths in other["tokens"].items():
                        s["tokens"][level].extend(lengths)
                    s["bge_tokens"].extend(other["bge_tokens"])
                    s["split_reasons"].update(other["split_reasons"])
                    s["chunks"] += other["chunks"]
                    s["retrieval_chars"] += other["retrieval_chars"]
            report = {"schema_version": SCHEMA_VERSION, "status": "complete",
                      "policy_version": POLICY_VERSION, "records": [completed[cid]["record"] for cid in selected],
                      "record_count": len(selected), "parents": sum(s["parents"] for s in completed.values()),
                      "graphs": sum(s["graphs"] for s in completed.values()),
                      "component_counts": dict(totals["components"]),
                      "exact_voice_alias_parents": sum(s["aliases"] for s in completed.values()),
                      "parent_kinds": dict(totals["kinds"]), "decisions": dict(totals["decisions"]),
                      "reasons": dict(totals["reasons"]),
                      "official_ref_checks": sum(s["official_ref_checks"] for s in completed.values()),
                      "all_admitted_ranges_checked": True, "profiles": {},
                      "elapsed_seconds": round(time.monotonic()-started, 3)}
            for profile in profiles:
                s = stats[profile.name]
                report["profiles"][profile.name] = {"config": profile.__dict__, "chunks": s["chunks"],
                    "tokens": {key: distribution(value) for key, value in s["tokens"].items()},
                    "bge_probe_pair_tokens": distribution(s["bge_tokens"]),
                    "split_reasons": dict(s["split_reasons"]), "retrieval_chars": s["retrieval_chars"]}
            atomic_json(output / "report.json", report)
            atomic_json(output / "review.json", reviews)
            lines = ["# 双粒度分片试点实际样例", "", "2026-11-07。以下候选完整展示，正文与来源上下文分开。源连接标记为派生显示信息。", ""]
            for chunk in reviews:
                lines += [f"## {chunk['title']} · {chunk['section_id']} · {'/'.join(chunk['granularities'])}", "",
                          f"`{chunk['chunk_id']}`，Qwen {chunk['tokens']} Token，BGE问文对 {chunk['bge_probe_pair_tokens']} Token。", "",
                          f"上下文为 {chunk['context_text']}", "", chunk["retrieval_text"], "",
                          f"父层 `{chunk['parent_id']}`，{len(chunk['clean_refs'])}条CleanRef。", ""]
            with _atomic_text(output / "review.md", newline=None) as handle:
                handle.write("\n".join(lines))
            verify_inputs()
            artifact_names += ["report.json", "review.json", "review.md"]
            atomic_json(output / "manifest.json", {"schema_version": SCHEMA_VERSION, "status": "complete", "build_binding": binding,
                  "clean_manifest": str(manifest_path.resolve()), "clean_manifest_sha256": initial_manifest_hash,
                  "selection": selection, "selection_sha256": initial_selection_hash,
                  "sources": [completed[cid]["source"] for cid in selected],
                  "tokenizers": [{"path": str(path), "sha256": tokenizer_hashes[str(path)]} for path in tokenizer_paths],
                  "tokenizers_version": tokenizer_version, "code_hashes": code_hashes,
                  "artifacts": {name: sha(output / name) for name in artifact_names}})
            checkpoint("complete")
            return report
        except BaseException as error:
            checkpoint("partial", {"type": type(error).__name__, "message": str(error)})
            raise

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean-manifest", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--qwen-tokenizer", type=Path, required=True)
    parser.add_argument("--bge-tokenizer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    result = run_pilot(args.clean_manifest, args.selection, args.qwen_tokenizer, args.bge_tokenizer,
                       args.output, workers=args.workers, resume=args.resume)
    print(json.dumps({"status": result["status"], "records": result["record_count"],
                      "chunks": {key: value["chunks"] for key, value in result["profiles"].items()}},
                     ensure_ascii=False))
