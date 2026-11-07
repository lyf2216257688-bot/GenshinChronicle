"""One-off, source-bound recovery of the interrupted 2026-11-07 V2 build."""
from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

from ..build_runtime import atomic_json, checked_receipt
from ..clean import iter_blocks, validate_record
from ..clean.models import pointer_get
from ..evidence_io import digest, inside, read, require, sha, verify
from ..model_calls import locked
from .core import ChunkProfile, SCHEMA_VERSION
from .dialogue import label
from .pilot import (PROBE_QUERY, TokenCounter, _atomic_text, _jsonl, _validate_parts,
                    _write_exports, run_pilot)
from .structure import _complete_reward_quantities


SOURCE_BINDING = "3f1abab6186697906536bda76682b1cc8a1f27a35b20184a477fd5d47d0c7ce4"
PENDING_IDS = {"7370", "7233"}


def _node_dump(node):
    return ast.dump(node, include_attributes=False)


def _check_repair(snapshot, state):
    """Limit reuse to the reviewed matcher change and unchanged record builders."""
    code_root = Path(__file__).parent
    old_path = snapshot / "code/chunking/structure.py"
    original = ast.parse(old_path.read_text(encoding="utf-8"))

    class ReplaceMatcher(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            if node.name != "leaf":
                return self.generic_visit(node)
            for child in ast.walk(node):
                if isinstance(child, ast.Assign) and any(
                        isinstance(target, ast.Name) and target.id == "quantity" for target in child.targets):
                    self.quantity_assignment = child
                if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                        and child.func.attr == "fullmatch" and len(child.args) == 2
                        and isinstance(child.args[1], ast.Name) and child.args[1].id == "quantity_text"):
                    self.quantity_call = child
            return self.generic_visit(node)

        def visit_Assign(self, node):
            return None if node is getattr(self, "quantity_assignment", None) else self.generic_visit(node)

        def visit_Call(self, node):
            if node is getattr(self, "quantity_call", None):
                return ast.parse("_complete_reward_quantities(quantity_text)", mode="eval").body
            return self.generic_visit(node)

    transformer = ReplaceMatcher()
    expected = transformer.visit(original)
    require(hasattr(transformer, "quantity_assignment") and hasattr(transformer, "quantity_call"),
            "Original reward matcher not found")
    current = ast.parse((code_root / "structure.py").read_text(encoding="utf-8"))
    current.body = [node for node in current.body if not (
        isinstance(node, ast.FunctionDef) and node.name == "_complete_reward_quantities")]
    require(_node_dump(expected) == _node_dump(current), "Unreviewed structure change")
    old_pilot = ast.parse((snapshot / "code/chunking/pilot.py").read_text(encoding="utf-8"))
    new_pilot = ast.parse((code_root / "pilot.py").read_text(encoding="utf-8"))
    new_nodes = {node.name: node for node in new_pilot.body if hasattr(node, "name")}
    for node in old_pilot.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name not in {"run_pilot", "_chunk_record_job"}:
            require(_node_dump(node) == _node_dump(new_nodes.get(node.name)),
                    "Record construction or validation changed: " + node.name)
    for name, checksum in state["inputs"]["code_hashes"].items():
        require(sha(snapshot / "code/chunking" / name) == checksum, "Original code snapshot mismatch")
        if name not in {"structure.py", "pilot.py"}:
            require(sha(code_root / name) == checksum, "Unreviewed chunking change: " + name)
    require(set(path.name for path in code_root.glob("*.py")) == set(state["inputs"]["code_hashes"]) | {"recover.py"},
            "Unexpected chunking module")
    for name, checksum in state["inputs"]["supporting_code_hashes"].items():
        require(sha(snapshot / "code" / name) == checksum and sha(code_root.parent / name) == checksum,
                "Supporting code changed: " + name)


def _checked_source_receipt(root, entry, binding):
    path = root / "_records" / entry["observation_id"] / "receipt.json"
    receipt = checked_receipt(path, root, binding)
    require(receipt["summary"]["source"] == {
        **entry, "before_sha256": entry["record_sha256"], "after_sha256": entry["record_sha256"]},
        "Receipt source mismatch")
    expected = {f"_records/{entry['observation_id']}/{name}" for name in (
        "parents.jsonl", "graphs.jsonl", "admission.jsonl", "chunks-balanced.jsonl", "review.json")}
    require(set(receipt["files"]) == expected, "Unexpected receipt artifact set")
    return path, receipt


def _check_admission(record, shard):
    """Compare affected values with their original completed admission decisions."""
    blocks = None
    checked = 0
    sections = {section["section_id"]: label(section.get("title")).strip() for section in record["sections"]}
    with (shard / "admission.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            decision = json.loads(line)
            if decision["decision"] != "include" and decision["reason"] != "explicit_reward_quantity":
                continue
            ref = decision.get("clean_ref")
            if ref is None or ref["value_pointer"] != "/value":
                continue
            if blocks is None:
                blocks = {block["block_id"]: block for block in iter_blocks(record)}
            block = blocks[ref["block_id"]]
            if block["kind"] not in {"paragraph", "heading", "scalar", "opaque", "media", "separator"}:
                continue
            value = pointer_get(block, ref["value_pointer"])
            text = value.get("text", value.get("lexeme"))
            scoped = sections.get(decision.get("section_id")) in {"任务奖励", "奖励", "商品列表", "商店"}
            if not scoped and not text.startswith(("达成奖励：", "达成奖励:")):
                continue
            normalized = text.strip()
            if normalized.startswith(("达成奖励：", "达成奖励:")):
                normalized = normalized[5:].lstrip()
            matched = _complete_reward_quantities(normalized)
            require(matched == (decision["reason"] == "explicit_reward_quantity"),
                    f"Reward admission changed: {record['source_item']['content_id']} {ref}")
            checked += 1
    return checked


def recover(source_run, output, manifest_path, selection_path, qwen_path, bge_path, *, workers=2, resume=False):
    from tokenizers import __version__ as tokenizer_version

    source_run, output = Path(source_run).resolve(), Path(output).resolve()
    manifest_path, selection_path = Path(manifest_path).resolve(), Path(selection_path).resolve()
    require(not output.is_relative_to(source_run) and not source_run.is_relative_to(output), "Run roots overlap")
    require(not output.is_relative_to(manifest_path.parent.parent) and not any(
        path.name.lower() in {"raw", "clean"} for path in [output, *output.parents]), "Invalid recovery output")
    snapshot = output / "evidence/original"
    verify(snapshot)
    source_info, state = read(snapshot / "source.json"), read(snapshot / "build-state.json")
    require(str(source_run) == source_info["source_run"], "Different source run")
    require(state["binding"] == SOURCE_BINDING == digest(state["inputs"]), "Unapproved source binding")
    require(sha(source_run / "build-state.json") == source_info["original_state_sha256"], "Source state changed")
    require(sha(manifest_path) == state["inputs"]["clean_manifest_sha256"], "Clean manifest changed")
    require(sha(selection_path) == state["inputs"]["selection_sha256"], "Selection changed")
    require(tokenizer_version == state["inputs"]["tokenizers_version"], "Tokenizer runtime changed")
    for path in (qwen_path, bge_path):
        require(sha(path) == state["inputs"]["tokenizers"].get(str(Path(path).resolve())), "Tokenizer changed")
    _check_repair(snapshot, state)
    manifest, selection = read(manifest_path), read(selection_path)
    require(manifest["status"] == "complete", "Incomplete Clean input")
    entries = {entry["content_id"]: entry for entry in manifest["records"]}
    selected = selection["content_ids"]
    require(len(selected) == len(set(selected)) == len(entries) == 16437, "Unexpected full selection")
    require(set(selected) == set(entries), "Selection identity mismatch")
    require(selection["profiles"] == [ChunkProfile().__dict__] and selection["bge_document_token_cap"] == 1792,
            "Unexpected profile")
    code_paths = [*Path(__file__).parent.glob("*.py")]
    code_hashes = {path.name: sha(path) for path in code_paths}
    recovery_binding = digest({"original_binding": SOURCE_BINDING,
        "original_state_sha256": source_info["original_state_sha256"], "code_hashes": code_hashes,
        "clean_manifest_sha256": sha(manifest_path), "selection_sha256": sha(selection_path)})
    started = time.monotonic()
    with locked(source_run / "build.lock"), locked(output / "build.lock"):
        state_path = output / "recovery-state.json"
        require(resume or not state_path.exists(), "Existing recovery requires --resume")
        if state_path.exists():
            require(read(state_path)["binding"] == recovery_binding, "Recovery implementation or inputs changed")

        def checkpoint(stage, error=None):
            atomic_json(state_path, {"status": "complete" if stage == "complete" else "partial",
                "stage": stage, "binding": recovery_binding, "error": error})
            print(json.dumps({"event": "recovery_stage", "stage": stage, "error": error}), flush=True)

        if (output / "manifest.json").exists():
            existing = read(output / "manifest.json")
            require(existing["build_binding"] == recovery_binding, "Conflicting final manifest")
            validate_exports(output)
            checkpoint("complete")
            return read(output / "report.json")
        try:
            checkpoint("verify_original")
            completed, shards, origins, pending = {}, {}, [], []
            affected_values = 0
            required_bytes = 0
            for index, cid in enumerate(selected, 1):
                entry = entries[cid]
                clean_path = inside(manifest_path.parent.parent, entry["record_path"])
                require(sha(clean_path) == entry["record_sha256"], "Clean record changed: " + cid)
                receipt_path = source_run / "_records" / entry["observation_id"] / "receipt.json"
                if not receipt_path.exists():
                    pending.append(cid)
                    continue
                receipt_path, receipt = _checked_source_receipt(source_run, entry, SOURCE_BINDING)
                shard = receipt_path.parent
                affected_values += _check_admission(read(clean_path), shard)
                completed[cid], shards[cid] = receipt["summary"], shard
                origins.append({"content_id": cid, "observation_id": entry["observation_id"],
                    "disposition": "reused", "build_binding": SOURCE_BINDING, "source_run": str(source_run),
                    "receipt_path": str(receipt_path.relative_to(source_run).as_posix()),
                    "receipt_sha256": sha(receipt_path), "files": receipt["files"]})
                required_bytes += sum(inside(source_run, name).stat().st_size for name in receipt["files"])
                if index % 1000 == 0:
                    print(json.dumps({"event": "verified_original", "records": index}), flush=True)
            require(set(pending) == PENDING_IDS, "Unexpected missing records")
            require(len(completed) == state["completed_records"] == 16435, "Original accounting mismatch")
            require(shutil.disk_usage(output).free >= required_bytes + 512 * 1024**2, "Insufficient merge space")
            atomic_json(output / "evidence/compatibility.json", {"status": "PASS", "original_binding": SOURCE_BINDING,
                "recovery_binding": recovery_binding, "affected_values_checked": affected_values,
                "reused_records": len(completed), "pending_ids": pending,
                "proof": "Unchanged record constructors and supporting code; exact matcher AST substitution; admission comparison"})
            for name in ("structure.py", "pilot.py"):
                before = (snapshot / "code/chunking" / name).read_text(encoding="utf-8")
                after = (Path(__file__).parent / name).read_text(encoding="utf-8")
                with _atomic_text(output / "evidence" / (name + ".diff")) as handle:
                    handle.writelines(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                                         fromfile="original/" + name, tofile="repaired/" + name))
            checkpoint("build_pending")
            pending_selection = {**selection, "content_ids": pending,
                                 "review_content_ids": [cid for cid in selection["review_content_ids"] if cid in pending]}
            pending_config = output / "pending-selection.json"
            if pending_config.exists():
                require(read(pending_config) == pending_selection, "Pending selection changed")
            else:
                atomic_json(pending_config, pending_selection)
            pending_root = output / "pending"
            run_pilot(manifest_path, pending_config, qwen_path, bge_path, pending_root,
                      workers=workers, resume=pending_root.exists())
            pending_manifest = read(pending_root / "manifest.json")
            for cid in pending:
                path, receipt = _checked_source_receipt(pending_root, entries[cid], pending_manifest["build_binding"])
                completed[cid], shards[cid] = receipt["summary"], path.parent
                origins.append({"content_id": cid, "observation_id": entries[cid]["observation_id"],
                    "disposition": "rebuilt", "build_binding": receipt["binding"], "source_run": "pending",
                    "receipt_path": path.relative_to(pending_root).as_posix(),
                    "receipt_sha256": sha(path), "files": receipt["files"]})
            origins_by_id = {origin["content_id"]: origin for origin in origins}
            with _atomic_text(output / "record-origins.jsonl") as handle:
                for cid in selected:
                    _jsonl(handle, origins_by_id[cid])
            checkpoint("merge")
            profiles = [ChunkProfile(**profile) for profile in selection["profiles"]]
            report, artifacts = _write_exports(output, selection, completed, shards, profiles, started)
            # Recheck every source receipt/artifact after merging, including partial-run evidence.
            checkpoint("verify_merge_inputs")
            for cid in selected:
                root = source_run if cid not in PENDING_IDS else pending_root
                origin = origins_by_id[cid]
                path, _ = _checked_source_receipt(root, entries[cid], origin["build_binding"])
                require(sha(path) == origin["receipt_sha256"], "Receipt changed during recovery")
                require(sha(inside(manifest_path.parent.parent, entries[cid]["record_path"])) == entries[cid]["record_sha256"],
                        "Clean record changed during recovery")
            require(sha(source_run / "build-state.json") == source_info["original_state_sha256"], "Original state changed")
            require(sha(manifest_path) == state["inputs"]["clean_manifest_sha256"] and
                    sha(selection_path) == state["inputs"]["selection_sha256"], "Input changed during recovery")
            require(code_hashes == {path.name: sha(path) for path in Path(__file__).parent.glob("*.py")}, "Code changed during recovery")
            _check_repair(snapshot, state)
            for path in (qwen_path, bge_path):
                require(sha(path) == state["inputs"]["tokenizers"][str(Path(path).resolve())], "Tokenizer changed during recovery")
            artifacts += ["record-origins.jsonl", "pending-selection.json", "pending/manifest.json",
                          "evidence/compatibility.json", "evidence/structure.py.diff", "evidence/pilot.py.diff"]
            artifacts += [path.relative_to(output).as_posix() for path in sorted(snapshot.rglob("*")) if path.is_file()]
            final = {"schema_version": SCHEMA_VERSION, "status": "complete", "build_binding": recovery_binding,
                "clean_manifest": str(manifest_path), "clean_manifest_sha256": sha(manifest_path),
                "selection": selection, "selection_sha256": sha(selection_path),
                "sources": [completed[cid]["source"] for cid in selected],
                "tokenizers": pending_manifest["tokenizers"], "tokenizers_version": tokenizer_version,
                "code_hashes": code_hashes, "code_hashes_role": "recovery_coordinator_and_repaired_target",
                "recovery": {"original_inputs": state["inputs"], "original_binding": SOURCE_BINDING,
                    "rebuilt_inputs": read(pending_root / "build-state.json")["inputs"],
                    "rebuilt_binding": pending_manifest["build_binding"], "reused_records": 16435, "rebuilt_records": 2,
                    "record_origins": "record-origins.jsonl", "index_inputs_are_local": True},
                "artifacts": {name: sha(output / name) for name in artifacts}}
            atomic_json(output / "completion-candidate.json", final)
            checkpoint("validate_exports")
            validation = validate_exports(output, manifest_name="completion-candidate.json", full=True)
            atomic_json(output / "validation.json", validation)
            final["artifacts"]["validation.json"] = sha(output / "validation.json")
            atomic_json(output / "completion-candidate.json", final)
            (output / "completion-candidate.json").replace(output / "manifest.json")
            checkpoint("complete")
            return report
        except BaseException as error:
            checkpoint("failed", {"type": type(error).__name__, "message": str(error)})
            raise


def _flat_records(output, name, origins):
    """Check record ordering and original shard bytes using only the flat export."""
    with (output / name).open("rb") as handle:
        line = handle.readline()
        parsed = json.loads(line) if line else None
        for origin in origins:
            values, checksum = [], hashlib.sha256()
            while parsed is not None and parsed["content_id"] == origin["content_id"]:
                checksum.update(line)
                values.append(parsed)
                line = handle.readline()
                parsed = json.loads(line) if line else None
            expected = origin["files"][f"_records/{origin['observation_id']}/{name}"]
            require(checksum.hexdigest() == expected, "Merged shard bytes/order mismatch: " + name)
            yield values
        require(parsed is None, "Extra records in flat export: " + name)


def validate_exports(output, *, manifest_name="manifest.json", full=False):
    """Validate local index-facing artifacts without opening the interrupted run."""
    output = Path(output)
    manifest = read(output / manifest_name)
    require(manifest["status"] == "complete", "Incomplete recovered manifest")
    for name, checksum in manifest["artifacts"].items():
        require(sha(inside(output, name)) == checksum, "Recovered artifact mismatch: " + name)
    report = read(output / "report.json")
    selected = manifest["selection"]["content_ids"]
    require([row["content_id"] for row in report["records"]] == selected, "Report order mismatch")
    with (output / manifest["recovery"]["record_origins"]).open(encoding="utf-8") as handle:
        origins = [json.loads(line) for line in handle]
    require([row["content_id"] for row in origins] == selected, "Origin accounting mismatch")
    require(sum(row["disposition"] == "reused" for row in origins) == manifest["recovery"]["reused_records"], "Reuse accounting mismatch")
    require(sum(row["disposition"] == "rebuilt" for row in origins) == manifest["recovery"]["rebuilt_records"], "Rebuild accounting mismatch")
    result = {"status": "PASS", "records": len(selected), "chunks": 0, "parents": 0, "graphs": 0,
              "admission": 0, "flat_shard_hashes_checked": 0, "full_source_checks": full}
    clean_manifest = None
    if full:
        from tokenizers import Tokenizer

        clean_path = Path(manifest["clean_manifest"])
        require(sha(clean_path) == manifest["clean_manifest_sha256"], "Clean manifest mismatch")
        clean_manifest = read(clean_path)
        entries = {entry["content_id"]: entry for entry in clean_manifest["records"]}
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        tokenizers = []
        for item in manifest["tokenizers"]:
            require(sha(Path(item["path"])) == item["sha256"], "Tokenizer mismatch")
            tokenizer = Tokenizer.from_file(item["path"])
            tokenizer.no_truncation()
            tokenizer.no_padding()
            tokenizers.append(tokenizer)
        qwen, bge = tokenizers
        profile = ChunkProfile(**manifest["selection"]["profiles"][0])
        result.update({"qwen_max": 0, "bge_max": 0, "source_refs": 0})
    streams = [_flat_records(output, name, origins) for name in (
        "parents.jsonl", "graphs.jsonl", "admission.jsonl", "chunks-balanced.jsonl")]
    for index, (parents, graphs, decisions, chunks) in enumerate(zip(*streams, strict=True)):
        for key, values in (("parents", parents), ("graphs", graphs), ("admission", decisions), ("chunks", chunks)):
            result[key] += len(values)
        result["flat_shard_hashes_checked"] += 4
        if full:
            entry = entries[selected[index]]
            record_path = inside(clean_path.parent.parent, entry["record_path"])
            require(sha(record_path) == entry["record_sha256"], "Clean record mismatch")
            record = read(record_path)
            validate_record(record)
            blocks = {block["block_id"]: block for block in iter_blocks(record)}
            _validate_parts(record, {"parents": parents, "graphs": graphs, "decisions": decisions},
                            chunks, profile, blocks=blocks)
            parent_map = {parent["parent_id"]: parent for parent in parents}
            for graph in graphs:
                parent = parent_map[graph["parent_id"]]
                source = blocks[parent["context"]["dialogue_block_id"]]
                require(graph["group"] == next(group for group in source["groups"]
                        if group["group_id"] == parent["context"]["group_id"]), "Source graph changed")
            count, secondary_count = TokenCounter(qwen), TokenCounter(bge)
            pair_overhead = (secondary_count(PROBE_QUERY) + bge.num_special_tokens_to_add(True)
                             - 2 * bge.num_special_tokens_to_add(False))
            for chunk in chunks:
                length, secondary = count(chunk["retrieval_text"]), secondary_count(chunk["retrieval_text"])
                require(length == chunk["tokens"], "Qwen token mismatch")
                require(secondary == chunk["bge_document_tokens"] <= manifest["selection"]["bge_document_token_cap"],
                        "BGE token mismatch or overflow")
                require(secondary + pair_overhead == chunk["bge_probe_pair_tokens"], "BGE pair token mismatch")
                result["qwen_max"] = max(result["qwen_max"], length)
                result["bge_max"] = max(result["bge_max"], secondary)
                result["source_refs"] += len(chunk["clean_refs"]) + len(chunk["alias_clean_refs"])
        if (index + 1) % 1000 == 0:
            print(json.dumps({"event": "validated_exports", "records": index + 1, "full": full}), flush=True)
    require(result["chunks"] == report["profiles"]["balanced"]["chunks"] and
            result["parents"] == report["parents"] and result["graphs"] == report["graphs"], "Export counts mismatch")
    require(result["records"] == report["record_count"] == len(manifest["sources"]), "Record count mismatch")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--source-run", type=Path)
    parser.add_argument("--clean-manifest", type=Path)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--qwen-tokenizer", type=Path)
    parser.add_argument("--bge-tokenizer", type=Path)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        result = validate_exports(args.output)
    else:
        if any(value is None for value in (args.source_run, args.clean_manifest, args.selection,
                                           args.qwen_tokenizer, args.bge_tokenizer)):
            parser.error("Recovery requires source run, Clean manifest, selection and both tokenizers")
        report = recover(args.source_run, args.output, args.clean_manifest, args.selection,
                         args.qwen_tokenizer, args.bge_tokenizer, workers=args.workers, resume=args.resume)
        result = {"status": report["status"], "records": report["record_count"],
                  "chunks": report["profiles"]["balanced"]["chunks"]}
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
