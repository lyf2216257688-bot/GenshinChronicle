"""Independent bounded/full build checks against the selected Raw occurrences."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .markup import parse_tree
from .models import SourceNumber, pointer_token, source_json, source_leaves
from .pipeline import RawRun
from .reader import iter_blocks, iter_records, iter_text_values, validate_record


def _html_occurrences(node, path=(), hidden=False):
    hidden = hidden or node.tag in {"script", "style", "#comment", "#declaration", "#instruction"}
    if node.text is not None and node.text.strip() and not hidden:
        yield path
    for index, child in enumerate(node.children):
        yield from _html_occurrences(child, path + (index,), hidden)


def _table_nodes(node) -> int:
    return int(node.tag == "table") + sum(_table_nodes(child) for child in node.children)


def _inspect_source(value, base, expected, tables, path=""):
    if isinstance(value, dict):
        if isinstance(value.get("header"), list) and isinstance(value.get("row"), list) and all(isinstance(row, list) for row in value["row"]):
            tables["json"] += 1
        for key, child in value.items():
            _inspect_source(child, base, expected, tables, path + "/" + pointer_token(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _inspect_source(child, base, expected, tables, path + "/" + str(index))
    elif isinstance(value, str) and not isinstance(value, SourceNumber) and "<" in value and ">" in value:
        tree = parse_tree(value)
        tables["html"] += _table_nodes(tree)
        for node_path in _html_occurrences(tree):
            expected[(base, path, node_path)] += 1


def _actual_html_occurrences(record):
    cache = {}

    def steps(origin_id):
        if origin_id not in cache:
            origin = record["origins"][origin_id]
            parent = steps(origin["parent_origin_id"]) if "parent_origin_id" in origin else []
            cache[origin_id] = parent + origin["steps"]
        return cache[origin_id]

    actual = Counter()
    for block in iter_blocks(record):
        for value in iter_text_values(block):
            for segment in value["segments"] or []:
                left, right = segment["clean_range"]
                if not segment["origin_id"] or not value["text"][left:right].strip():
                    continue
                groups = [""]
                node_path = None
                for step in steps(segment["origin_id"]):
                    if step["op"] == "pointer":
                        groups[-1] += step["path"]
                    elif step["op"] == "json_decode":
                        groups.append("")
                    elif step["op"] == "html_text":
                        node_path = tuple(step["node_path"])
                if node_path is not None and len(groups) == 2 and groups[0].startswith("/data/page/modules/"):
                    actual[(groups[0], groups[1], node_path)] += 1
    return actual


def validate_clean_run(clean_manifest: Path, *, raw_run_root: Path) -> dict[str, Any]:
    raw = RawRun(raw_run_root)
    totals = Counter()
    types = set()
    checks = []
    for record in iter_records(clean_manifest):
        validate_record(record, raw.root)
        content_id = record["source_item"]["content_id"]
        page = source_json(raw.response("details:" + content_id)["body"].decode("utf-8"))["data"]["page"]
        expected = Counter()
        tables = Counter()
        source_values = 0
        expected_graphs = []
        for module_index, module in enumerate(page["modules"]):
            for component_index, component in enumerate(module["components"]):
                types.add(component["component_id"])
                totals["components"] += 1
                source_data = component["data"]
                try:
                    data = source_json(source_data) if isinstance(source_data, str) else source_data
                except ValueError:
                    totals["source_empty_or_opaque"] += 1
                    source_values += 1
                    continue
                source_values += len(list(source_leaves(data)))
                base = f"/data/page/modules/{module_index}/components/{component_index}/data"
                _inspect_source(data, base, expected, tables)
                if component["component_id"] == "interactive_dialogue" and isinstance(data, dict):
                    groups = [group for group in data.get("list", []) or [] if isinstance(group, dict)]
                    if any(data.get(key) for key in ("root_id", "contents", "child_ids")):
                        groups.insert(0, data)
                    totals["dialogue_groups"] += len(groups)
                    for group in groups:
                        contents = group.get("contents") or {}
                        adjacency = group.get("child_ids") or {}
                        if not isinstance(contents, dict) or not isinstance(adjacency, dict):
                            raise ValueError(f"Unsupported Raw dialogue shape for {content_id}")
                        edges = [(parent, str(child), index) for parent, children in adjacency.items()
                                 for index, child in enumerate(children)]
                        totals["dialogue_nodes"] += len(contents)
                        totals["dialogue_edges"] += len(edges)
                        expected_graphs.append((group.get("root_id"), list(contents), edges))
        if expected != _actual_html_occurrences(record):
            raise ValueError(f"HTML text occurrence coverage mismatch for {content_id}")
        actual_tables = Counter(block["table_format"] for block in iter_blocks(record) if block["kind"] == "table")
        if actual_tables != tables:
            raise ValueError(f"Table coverage mismatch for {content_id}")
        actual_graphs = []
        for block in iter_blocks(record):
            if block["kind"] == "dialogue":
                for group in block["groups"]:
                    if "group_id" in group:
                        actual_graphs.append((group["root_id"], [node["source_id"] for node in group["nodes"]],
                                              [(edge["parent_id"], edge["child_id"], edge["ordering"]) for edge in group["edges"]]))
        if actual_graphs != expected_graphs:
            raise ValueError(f"Dialogue identity or edge-order mismatch for {content_id}")
        if record["coverage"]["accounted_component_values"] != source_values:
            raise ValueError(f"Source value coverage mismatch for {content_id}")
        totals["source_values"] += source_values
        totals["meaningful_html_text_nodes"] += sum(expected.values())
        totals["html_tables"] += tables["html"]
        totals["json_tables"] += tables["json"]
        checks.append({"content_id": content_id, "status": "PASS", "source_values": source_values,
                       "html_text_nodes": sum(expected.values()), "tables": dict(tables)})
    return {"status": "PASS", "records": len(checks), "component_types": sorted(types),
            "counts": dict(totals), "record_checks": checks}
