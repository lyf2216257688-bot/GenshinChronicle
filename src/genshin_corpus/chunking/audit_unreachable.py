"""Classify existing source-graph occurrences without constructing any chunks."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from ..build_runtime import atomic_json
from ..evidence_io import read, sha
from .dialogue import analyze_graph
from .pilot import _atomic_text, _jsonl


def classify(manifest_path: Path, output: Path):
    manifest_path, output = Path(manifest_path), Path(output).resolve()
    manifest = read(manifest_path)
    graph_path = manifest_path.parent / "graphs.jsonl"
    clean_manifest = Path(manifest["clean_manifest"])
    if output.is_relative_to(clean_manifest.resolve().parent.parent) or any(
            p.name.lower() in {"raw", "clean"} for p in [output, *output.parents]):
        raise ValueError("Classification output must be a downstream directory")
    code_paths = sorted(Path(__file__).parent.glob("*.py"))
    code_hashes = {p.name: sha(p) for p in code_paths}
    before = {"manifest": sha(manifest_path), "graphs": sha(graph_path), "clean_manifest": sha(clean_manifest)}
    if manifest.get("status") != "complete" or before["graphs"] != manifest["artifacts"]["graphs.jsonl"]:
        raise ValueError("Completed source graph artifact with matching hash required")
    if before["clean_manifest"] != manifest["clean_manifest_sha256"]:
        raise ValueError("Clean manifest binding mismatch")
    output.mkdir(parents=True, exist_ok=False)
    categories, flags = Counter(), Counter()
    groups = total_nodes = 0
    with _atomic_text(output / "nodes.jsonl") as handle:
        with graph_path.open(encoding="utf-8") as source:
            for line in source:
                graph = json.loads(line)
                group = graph["group"]
                analysis = analyze_graph(group, graph["observation_id"])
                groups += 1
                total_nodes += len(group["nodes"])
                incident = defaultdict(list)
                for edge in group["edges"]:
                    incident[edge["parent_id"]].append(edge)
                    if edge["child_id"] != edge["parent_id"]:
                        incident[edge["child_id"]].append(edge)
                for node_id in analysis["display_order"]:
                    info = analysis["derived_nodes"][node_id]
                    if info["reachability"] == "reachable":
                        continue
                    categories[info["reachability"]] += 1
                    flags["no_option_or_dialogue_text"] += not info["has_text"]
                    flags["nonempty_text_equal_to_reachable"] += bool(info["same_text_as_reachable_node_ids"])
                    _jsonl(handle, {"observation_id": graph["observation_id"], "content_id": graph["content_id"],
                        "section_id": graph["section_id"], "group_id": group["group_id"],
                        "parent_id": graph["parent_id"], "source_node_id": node_id,
                        "group_origin_id": group["origin_id"], "declared_root_id": group.get("root_id"),
                        "incident_source_edges": incident[node_id], **info})
    if before != {"manifest": sha(manifest_path), "graphs": sha(graph_path), "clean_manifest": sha(clean_manifest)}:
        raise ValueError("Classification inputs changed")
    if code_hashes != {p.name: sha(p) for p in code_paths}:
        raise ValueError("Classification implementation changed")
    report = {"status": "complete", "schema_version": "gc-unreachable-audit-1",
        "source_manifest": str(manifest_path.resolve()), "input_hashes": before,
        "analysis_code_sha256": sha(Path(__file__).with_name("dialogue.py")),
        "audit_code_sha256": sha(Path(__file__)), "groups": groups, "total_source_nodes": total_nodes,
        "code_hashes": code_hashes,
        "unreachable_nodes": sum(categories.values()), "categories": dict(categories), "flags": dict(flags),
        "order_semantics": "display_only", "classification_basis": "existing_source_edges_only",
        "artifacts": {"nodes.jsonl": sha(output / "nodes.jsonl")}}
    atomic_json(output / "manifest.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(classify(args.manifest, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
