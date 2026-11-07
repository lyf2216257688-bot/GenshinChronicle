"""Finite local pilot CLI. Tokenizers only, no model inference or serving hooks."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from functools import lru_cache
import json
from pathlib import Path
import time

from ..clean import CleanRef, iter_blocks, resolve_clean_ref, validate_record
from ..clean.models import pointer_get
from ..evidence_io import inside, read, sha, write
from .core import ChunkProfile, chunk_parent, identity, retrieval_text
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


def _validate_parts(record, adapted, chunks, profile):
    blocks = {block["block_id"]: block for block in iter_blocks(record)}
    coverage = {level: defaultdict(list) for level in ("micro", "macro")}
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
            source_contexts = part.get("upstream_context", []) + ([part["article_title"]] if part.get("article_title") else [])
            for source in source_contexts:
                ref = CleanRef(**source["clean_ref"])
                if ref.observation_id != record["observation_id"]:
                    raise ValueError("Cross-observation source context")
                value = pointer_get(blocks[ref.block_id], ref.value_pointer)
                if value["text"][ref.char_start:ref.char_end] != source["text"]:
                    raise ValueError("Source context mismatch")
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


def run_pilot(manifest_path: Path, selection_path: Path, qwen_path: Path,
              bge_path: Path, output: Path):
    from tokenizers import Tokenizer, __version__ as tokenizer_version

    started = time.monotonic()
    manifest = read(manifest_path)
    initial_manifest_hash = sha(manifest_path)
    selection = read(selection_path)
    initial_selection_hash = sha(selection_path)
    code_root = Path(__file__).parent
    initial_code_hashes = {path.name: sha(path) for path in code_root.glob("*.py")}
    initial_tokenizer_hashes = {str(path): sha(path) for path in (qwen_path, bge_path)}
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
    output = Path(output).resolve()
    source_root = manifest_path.resolve().parent.parent
    # A pilot is never allowed to write into Clean/Raw, even with an accidental CLI path.
    if output.is_relative_to(source_root) or any(p.name.lower() in {"raw", "clean"} for p in output.parents):
        raise ValueError("Output must be a fresh downstream directory")
    output.mkdir(parents=True, exist_ok=False)
    tokenizers = []
    for path in (qwen_path, bge_path):
        tokenizer = Tokenizer.from_file(str(path))
        tokenizer.no_truncation()
        tokenizer.no_padding()
        tokenizers.append(tokenizer)
    qwen, bge = tokenizers

    @lru_cache(maxsize=8192)
    def count(text):
        return len(qwen.encode(text, add_special_tokens=True).ids)

    @lru_cache(maxsize=8192)
    def bge_count(text):
        return len(bge.encode(text, add_special_tokens=True).ids)

    profiles = [ChunkProfile(**item) for item in selection["profiles"]]
    stats = {profile.name: {"tokens": defaultdict(list), "bge_tokens": [], "chunks": 0,
                           "split_reasons": Counter(), "retrieval_chars": 0} for profile in profiles}
    source_files = []
    records = []
    decisions = Counter()
    reasons = Counter()
    components = Counter()
    kinds = Counter()
    reviews = []
    official_ref_checks = 0
    paths = {profile.name: output / f"chunks-{profile.name}.jsonl" for profile in profiles}
    handles = {name: path.open("x", encoding="utf-8", newline="\n") for name, path in paths.items()}
    parent_count = graph_count = alias_parent_count = 0
    try:
        with (output / "parents.jsonl").open("x", encoding="utf-8", newline="\n") as parents_file, \
             (output / "graphs.jsonl").open("x", encoding="utf-8", newline="\n") as graphs_file, \
             (output / "admission.jsonl").open("x", encoding="utf-8", newline="\n") as audit_file:
            for cid in selected:
                entry = entries[cid]
                source = inside(source_root, entry["record_path"])
                before_hash = sha(source)
                if before_hash != entry["record_sha256"]:
                    raise ValueError("Selected Clean hash mismatch")
                record = read(source)
                validate_record(record)
                if (record["observation_id"], record["source_item"]["content_id"],
                    record["source_item"]["source"], record["source_item"]["locale"], record["input"]["raw_run_id"]) != (
                    entry["observation_id"], cid, manifest["source"], manifest["locale"], manifest["raw_run_id"]):
                    raise ValueError("Selected Clean identity/source mismatch")
                adapted = adapt_record(record)
                for parent in adapted["parents"]:
                    # Prove that even excluded source fields remain readable in the natural container.
                    read_natural_parent(parent, record)
                    parent["natural_text_qwen_tokens"] = count(retrieval_text(parent, [
                        part for unit in parent["units"] for part in unit["parts"]])) if parent["units"] else 0
                    _jsonl(parents_file, parent)
                    kinds[parent["kind"]] += 1
                parent_count += len(adapted["parents"])
                alias_parent_count += sum("exact_alias_of_parent_id" in parent for parent in adapted["parents"])
                for graph in adapted["graphs"]:
                    _jsonl(graphs_file, graph)
                graph_count += len(adapted["graphs"])
                for decision in adapted["decisions"]:
                    _jsonl(audit_file, decision)
                    decisions[decision["decision"]] += 1
                    reasons[decision["reason"]] += 1
                for section in record["sections"]:
                    components.update(component["component_id"] for component in section["components"])
                record_counts = {}
                for profile in profiles:
                    chunks = [chunk for parent in adapted["parents"] if "exact_alias_of_parent_id" not in parent for chunk in chunk_parent(
                        parent, count, profile,
                        secondary_fits=lambda text: bge_count(text) <= selection["bge_document_token_cap"])]
                    _validate_parts(record, adapted, chunks, profile)
                    for chunk in chunks:
                        chunk["bge_document_tokens"] = bge_count(chunk["retrieval_text"])
                        chunk["bge_probe_pair_tokens"] = len(bge.encode(
                            "这些文字记载了什么故事，人物的经历和前置任务条件有什么联系？",
                            chunk["retrieval_text"], add_special_tokens=True).ids)
                        _jsonl(handles[profile.name], chunk)
                        s = stats[profile.name]
                        s["chunks"] += 1
                        s["retrieval_chars"] += len(chunk["retrieval_text"])
                        s["bge_tokens"].append(chunk["bge_probe_pair_tokens"])
                        for level in chunk["granularities"]:
                            s["tokens"][level].append(chunk["tokens"])
                        s["split_reasons"].update(part.get("split_reason", "intact") for part in chunk["parts"])
                    record_counts[profile.name] = len(chunks)
                    if profile == profiles[0]:
                        # Cross-check the fast per-part range validation through the public reader.
                        for chunk in chunks[:2]:
                            for part in chunk["parts"][:2]:
                                resolved = resolve_clean_ref(CleanRef(**part["clean_ref"]), record=record)
                                if resolved["text"] != part["text"]:
                                    raise ValueError("Public CleanRef resolver disagrees")
                                official_ref_checks += 1
                        if cid in selection["review_content_ids"]:
                            candidates = [chunk for chunk in chunks if "macro" in chunk["granularities"]]
                            # Include complete first candidates, plus critical exact-source examples.
                            wanted = ("常识来说", "之前和提纳里处理过", "我们须弥特有", "枪这种武器", "兰罗摩", "保险")
                            chosen = candidates[:2] + [chunk for chunk in candidates if any(t in chunk["body_text"] for t in wanted)]
                            reviews.extend({"title": label(record["document"]["title"]), **chunk}
                                           for chunk in {c["chunk_id"]: c for c in chosen}.values())
                source_files.append({**entry, "before_sha256": before_hash, "after_sha256": sha(source)})
                if source_files[-1]["after_sha256"] != before_hash:
                    raise ValueError("Clean source changed during pilot")
                records.append({"content_id": cid, "title": label(record["document"]["title"]),
                                "parents": len(adapted["parents"]), "chunks": record_counts})
                print(json.dumps({"record": cid, "done": len(records), "chunks": record_counts}), flush=True)
    finally:
        for handle in handles.values():
            handle.close()
    report = {"schema_version": "gc-chunking-pilot-1", "status": "complete",
              "policy_version": POLICY_VERSION, "records": records, "record_count": len(records),
              "parents": parent_count, "graphs": graph_count, "component_counts": dict(components),
              "exact_voice_alias_parents": alias_parent_count,
              "parent_kinds": dict(kinds), "decisions": dict(decisions), "reasons": dict(reasons),
              "official_ref_checks": official_ref_checks, "all_admitted_ranges_checked": True,
              "profiles": {}, "elapsed_seconds": round(time.monotonic()-started, 3)}
    for profile in profiles:
        s = stats[profile.name]
        report["profiles"][profile.name] = {"config": profile.__dict__, "chunks": s["chunks"],
            "tokens": {key: distribution(value) for key, value in s["tokens"].items()},
            "bge_probe_pair_tokens": distribution(s["bge_tokens"]),
            "split_reasons": dict(s["split_reasons"]), "retrieval_chars": s["retrieval_chars"]}
    write(output / "report.json", report)
    write(output / "review.json", reviews)
    lines = ["# 双粒度分片试点实际样例", "", "2026-11-07。以下候选完整展示，正文与来源上下文分开。源连接标记为派生显示信息。", ""]
    for chunk in reviews:
        lines += [f"## {chunk['title']} · {chunk['section_id']} · {'/'.join(chunk['granularities'])}", "",
                  f"`{chunk['chunk_id']}`，Qwen {chunk['tokens']} Token，BGE问文对 {chunk['bge_probe_pair_tokens']} Token。", "",
                  f"上下文为 {chunk['context_text']}", "", chunk["retrieval_text"], "",
                  f"父层 `{chunk['parent_id']}`，{len(chunk['clean_refs'])}条CleanRef。", ""]
    (output / "review.md").write_text("\n".join(lines), encoding="utf-8")
    if sha(manifest_path) != initial_manifest_hash:
        raise ValueError("Clean manifest changed during pilot")
    if sha(selection_path) != initial_selection_hash or initial_code_hashes != {path.name: sha(path) for path in code_root.glob("*.py")}:
        raise ValueError("Selection or implementation changed during pilot")
    if initial_tokenizer_hashes != {str(path): sha(path) for path in (qwen_path, bge_path)}:
        raise ValueError("Tokenizer changed during pilot")
    write(output / "manifest.json", {"status": "complete", "clean_manifest": str(manifest_path.resolve()),
          "clean_manifest_sha256": sha(manifest_path), "selection": selection,
          "selection_sha256": sha(selection_path), "sources": source_files,
          "tokenizers": [{"path": str(path.resolve()), "sha256": sha(path)} for path in (qwen_path, bge_path)],
          "tokenizers_version": tokenizer_version,
          "code_hashes": {path.name: sha(path) for path in code_root.glob("*.py")},
          "artifacts": {path.name: sha(path) for path in output.iterdir() if path.is_file()}})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean-manifest", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--qwen-tokenizer", type=Path, required=True)
    parser.add_argument("--bge-tokenizer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_pilot(args.clean_manifest, args.selection, args.qwen_tokenizer, args.bge_tokenizer, args.output)
    print(json.dumps({"status": result["status"], "records": result["record_count"],
                      "chunks": {key: value["chunks"] for key, value in result["profiles"].items()}},
                     ensure_ascii=False))
