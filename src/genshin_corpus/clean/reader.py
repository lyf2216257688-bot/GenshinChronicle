"""Provider-free Clean readers, selectors, code point references and rendering."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..evidence_io import inside, read
from .markup import parse_tree, tree_node
from .models import BlockSelector, CleanRecord, CleanRef, SCHEMA_VERSION, dependencies, observation_id, pointer_get, pointer_token, source_json


def _walk(value: Any, pointer: str = ""):
    yield pointer, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, pointer + "/" + pointer_token(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, pointer + "/" + str(index))


def _label(value: Any) -> str:
    if isinstance(value, dict):
        if "text" in value:
            return value["text"]
        return "".join(_label(child) for key, child in value.items() if key in {"value", "children"})
    if isinstance(value, list):
        return "".join(_label(child) for child in value)
    return ""


def iter_blocks(record: dict[str, Any], selector: BlockSelector | None = None):
    """Yield structural blocks with inherited context, without mutating the record.

    Containers and children are separate blocks. iter_text_values yields only
    values owned by that block, so iterating both does not duplicate its text.
    """
    selector = selector or BlockSelector()
    document = record["document"]
    channels = {membership["channel_id"] for membership in document["memberships"]}
    for membership in document["memberships"]:
        channels.update(node["channel_id"] for node in membership["path"])

    def visit(value: Any, context: dict[str, Any]):
        if isinstance(value, dict):
            context = dict(context)
            if "section_id" in value:
                context["section_id"] = value["section_id"]
                context["section_title"] = _label(value.get("title"))
            if "component_id" in value and "blocks" in value:
                context["component_id"] = value["component_id"]
            if value.get("kind") == "group" and value.get("title") is not None:
                context["group_titles"] = context.get("group_titles", []) + [_label(value["title"])]
            context["local_tags"] = context.get("local_tags", []) + value.get("tags", [])
            if value.get("kind") == "field":
                context["field_key"] = value["field_key"]
                context["field_labels"] = context.get("field_labels", []) + [_label(value.get("label"))]
            if "block_id" in value:
                context["block_id"] = value["block_id"]
                tags = document["tags"] + context.get("local_tags", [])
                actual_tags = {(tag["namespace"], tag["key"], tag["value"]) for tag in tags}
                key = context.get("field_key", "")
                matches = (
                    (not selector.channel_ids or bool(channels.intersection(selector.channel_ids)))
                    and (not selector.component_ids or context.get("component_id") in selector.component_ids)
                    and (not selector.kinds or value["kind"] in selector.kinds)
                    and (not selector.field_keys or key in selector.field_keys)
                    and (not selector.field_prefixes or key.startswith(selector.field_prefixes))
                    and (not selector.field_labels or bool(set(context.get("field_labels", [])).intersection(selector.field_labels)))
                    and (not selector.tags or bool(actual_tags.intersection(selector.tags)))
                )
                if matches:
                    yield {**value, "context": {**context, "document_tags": document["tags"],
                                               "document_title": document.get("title")}}
            for child in value.values():
                yield from visit(child, context)
        elif isinstance(value, list):
            for child in value:
                yield from visit(child, context)

    yield from visit(document, {})
    yield from visit(record["sections"], {})
    yield from visit(record["extensions"], {})


def iter_text_values(block: dict[str, Any]):
    def visit(value: Any, pointer: str):
        if isinstance(value, dict):
            if pointer and "block_id" in value:
                return
            if "origin_id" in value and ("text" in value or "lexeme" in value):
                yield {"value_pointer": pointer, "text": value.get("text", value.get("lexeme")),
                       "origin_id": value["origin_id"], "segments": value.get("segments"),
                       "marks": value.get("marks", [])}
                return
            for key, child in value.items():
                if key not in {"context", "annotations"}:
                    yield from visit(child, pointer + "/" + pointer_token(key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from visit(child, pointer + "/" + str(index))
    yield from visit(block, "")


class OriginResolver:
    """One-record cache. Every artifact is hash checked before first use."""

    def __init__(self, record: dict[str, Any], raw_run_root: Path):
        self.record = record
        self.root = Path(raw_run_root)
        self.artifacts = {item["artifact_id"]: item for item in record["artifacts"]}
        self._cache: dict[tuple[str, str], Any] = {}
        self._trees: dict[tuple[str, str], Any] = {}
        self._resolving: set[str] = set()

    def resolve(self, origin_id: str) -> Any:
        result_key = ("origin", origin_id)
        if result_key in self._cache:
            return self._cache[result_key]
        if origin_id in self._resolving:
            raise ValueError("Cycle in Clean source locators")
        self._resolving.add(origin_id)
        try:
            return self._resolve(origin_id)
        finally:
            self._resolving.remove(origin_id)

    def _resolve(self, origin_id: str) -> Any:
        origin = self.record["origins"][origin_id]
        if "parent_origin_id" in origin:
            artifact_id = "origin:" + origin["parent_origin_id"]
            self._cache[(artifact_id, "")] = self.resolve(origin["parent_origin_id"])
        else:
            artifact_id = origin["artifact_id"]
        root_key = (artifact_id, "")
        if root_key not in self._cache:
            artifact = self.artifacts[artifact_id]
            body = inside(self.root, artifact["path"]).read_bytes()
            if hashlib.sha256(body).hexdigest() != artifact["sha256"]:
                raise ValueError("Raw artifact checksum mismatch")
            self._cache[root_key] = source_json(body.decode("utf-8"))
        value = self._cache[root_key]
        steps = []
        for step in origin["steps"]:
            previous_key = (artifact_id, json.dumps(steps, sort_keys=True))
            steps.append(step)
            key = (artifact_id, json.dumps(steps, sort_keys=True))
            if key in self._cache:
                value = self._cache[key]
                continue
            operation = step["op"]
            if operation == "pointer":
                value = pointer_get(value, step["path"])
            elif operation == "json_decode":
                value = source_json(value)
            elif operation == "json_key":
                if step["key"] not in value:
                    raise KeyError(step["key"])
                value = step["key"]
            elif operation in {"html_text", "html_attr", "html_node"}:
                if previous_key not in self._trees:
                    self._trees[previous_key] = parse_tree(value)
                node = tree_node(self._trees[previous_key], step["node_path"])
                if operation == "html_text":
                    value = node.text
                elif operation == "html_attr":
                    value = dict(node.attrs)[step["name"]]
                else:
                    value = node.to_dict()
            else:
                raise ValueError(f"Unknown Clean origin operation: {operation}")
            self._cache[key] = value
        self._cache[("origin", origin_id)] = value
        return value


def validate_record(record: dict[str, Any], raw_run_root: Path | None = None) -> dict[str, int]:
    if not isinstance(record, dict) or set(CleanRecord.__annotations__) - set(record):
        raise ValueError("Clean record is missing required fields")
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported Clean schema")
    if any(record["input"].get(key) != val for key, val in dependencies().items()):
        raise ValueError("Unsupported Clean extraction rules")
    expected_identity = observation_id(record["source_item"], record["input"]["raw_run_id"], record["input"]["dependency_fingerprint"])
    if record["observation_id"] != expected_identity:
        raise ValueError("Clean observation identity does not match its dependencies")
    origins = record["origins"]
    resolved_parents = set()
    for origin_id in origins:
        chain = set()
        current = origin_id
        while current is not None and current not in resolved_parents:
            if current in chain:
                raise ValueError("Cycle in Clean source locators")
            chain.add(current)
            if current not in origins:
                raise ValueError("Origin references absent parent")
            current = origins[current].get("parent_origin_id")
        resolved_parents.update(chain)
    artifact_ids = {item["artifact_id"] for item in record["artifacts"]}
    relation_ids = {item["relation_id"] for item in record["relations"]}
    media_ids = {item["media_id"] for item in record["media"]}
    if len(artifact_ids) != len(record["artifacts"]):
        raise ValueError("Duplicate artifact identity")
    for origin in origins.values():
        if "parent_origin_id" in origin:
            if origin["parent_origin_id"] not in origins:
                raise ValueError("Origin references absent parent")
        elif origin.get("artifact_id") not in artifact_ids:
            raise ValueError("Origin references absent artifact")
    resolver = OriginResolver(record, raw_run_root) if raw_run_root is not None else None
    if resolver:
        for origin_id in origins:
            resolver.resolve(origin_id)
    seen_blocks = set()
    text_count = 0
    for _, value in _walk(record):
        if not isinstance(value, dict):
            continue
        if "origin_id" in value and value["origin_id"] is not None and value["origin_id"] not in origins:
            raise ValueError("Value references absent origin")
        if "relation_id" in value and value["relation_id"] not in relation_ids:
            raise ValueError("Value references absent relation")
        if "media_id" in value and value["media_id"] not in media_ids:
            raise ValueError("Value references absent media")
        if "block_id" in value:
            if value["block_id"] in seen_blocks:
                raise ValueError("Duplicate block identity")
            seen_blocks.add(value["block_id"])
        if "namespace" in value and "evidence" in value:
            if not value["evidence"] or any(origin_id not in origins for origin_id in value["evidence"]):
                raise ValueError("Tag evidence is missing")
        if "origin_id" not in value or not ("text" in value or "lexeme" in value):
            continue
        text_count += 1
        text = value.get("text", value.get("lexeme"))
        if not isinstance(text, str):
            raise ValueError("Clean text value is not a string")
        if "segments" in value:
            offset = 0
            for segment in value["segments"]:
                start, end = segment["clean_range"]
                if start != offset or end < start or end > len(text):
                    raise ValueError("Text segments do not partition the value")
                if segment["origin_id"] is not None and resolver:
                    left, right = segment["source_range"]
                    source = resolver.resolve(segment["origin_id"])
                    if not isinstance(source, str) or not 0 <= left <= right <= len(source) or source[left:right] != text[start:end]:
                        raise ValueError("Text segment fails source round trip")
                elif segment["origin_id"] is None and not segment.get("rule_id"):
                    raise ValueError("Generated segment lacks a rule")
                offset = end
            if offset != len(text):
                raise ValueError("Text segment coverage is incomplete")
        elif resolver:
            source = resolver.resolve(value["origin_id"])
            expected = source if isinstance(source, str) else json.dumps(source, ensure_ascii=False)
            if expected != text:
                raise ValueError("Text value fails source round trip")
        for mark in value.get("marks", []):
            start, end = mark["range"]
            if not 0 <= start <= end <= len(text):
                raise ValueError("Invalid inline mark range")
    coverage = record["coverage"]
    if coverage.get("component_source_values", 0) != coverage.get("accounted_component_values", 0):
        raise ValueError("Incomplete component coverage")
    return {"blocks": len(seen_blocks), "text_values": text_count, "origins": len(origins)}


def iter_records(clean_manifest: Path, *, allow_partial: bool = False):
    manifest_path = Path(clean_manifest)
    manifest = read(manifest_path)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported Clean manifest")
    if not allow_partial and manifest.get("status") != "complete":
        raise ValueError("Clean manifest is not complete")
    if manifest.get("accounted_detail_count") != len(manifest["records"]):
        raise ValueError("Clean manifest accounting mismatch")
    if manifest["status"] == "complete" and len(manifest["records"]) != manifest["input_detail_count"]:
        raise ValueError("Complete Clean manifest omits inputs")
    root = manifest_path.parent.parent
    seen = set()
    for entry in manifest["records"]:
        if entry.get("status") == "blocked_integrity":
            if allow_partial:
                continue
            raise ValueError("Clean manifest contains a blocked record")
        body = inside(root, entry["record_path"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != entry["record_sha256"]:
            raise ValueError("Clean record checksum mismatch")
        record = json.loads(body)
        validate_record(record)
        if (record["source_item"]["source"], record["source_item"]["locale"], record["input"]["raw_run_id"]) != (
            manifest["source"], manifest["locale"], manifest["raw_run_id"]
        ):
            raise ValueError("Clean manifest source binding mismatch")
        if record["observation_id"] != entry["observation_id"] or record["source_item"]["content_id"] != entry["content_id"]:
            raise ValueError("Clean record identity mismatch")
        if record["observation_id"] in seen:
            raise ValueError("Duplicate Clean observation")
        seen.add(record["observation_id"])
        yield record


def resolve_clean_ref(clean_ref: CleanRef, *, record: dict[str, Any], raw_run_root: Path | None = None) -> dict[str, Any]:
    if clean_ref.observation_id != record["observation_id"]:
        raise ValueError("CleanRef observation mismatch")
    block = next((block for block in iter_blocks(record) if block["block_id"] == clean_ref.block_id), None)
    if block is None:
        raise ValueError("CleanRef block does not exist")
    block.pop("context", None)
    try:
        value = pointer_get(block, clean_ref.value_pointer)
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("CleanRef value does not exist in the source block") from error
    if not isinstance(value, dict) or "origin_id" not in value:
        raise ValueError("CleanRef must address a source text value")
    text = value.get("text", value.get("lexeme"))
    if not isinstance(text, str) or clean_ref.char_end > len(text):
        raise ValueError("CleanRef target or range invalid")
    origins = []
    if "segments" in value:
        for segment in value["segments"]:
            left = max(clean_ref.char_start, segment["clean_range"][0])
            right = min(clean_ref.char_end, segment["clean_range"][1])
            if left < right:
                origin_id = segment["origin_id"]
                item = {"clean_range": [left, right], "origin_id": origin_id}
                if origin_id is not None:
                    source_start = segment["source_range"][0] + left - segment["clean_range"][0]
                    item["source_range"] = [source_start, source_start + right - left]
                else:
                    item["rule_id"] = segment["rule_id"]
                origins.append(item)
    else:
        origins.append({"origin_id": value["origin_id"], "source_range": [clean_ref.char_start, clean_ref.char_end]})
    if raw_run_root is not None:
        validate_record(record, raw_run_root)
    return {"text": text[clean_ref.char_start:clean_ref.char_end], "origins": origins,
            "range_unit": "unicode_codepoint", "observation_id": record["observation_id"]}


def render_record(record: dict[str, Any], *, format: str = "text") -> str:
    if format not in {"text", "markdown"}:
        raise ValueError("Supported render formats are text and markdown")

    def render(value: Any) -> str:
        if isinstance(value, list):
            return "\n".join(render(child) for child in value)
        if not isinstance(value, dict):
            return ""
        if "text" in value:
            return value["text"]
        if "lexeme" in value:
            return value["lexeme"]
        kind = value.get("kind")
        if kind == "field":
            extras = render(value.get("children", []))
            return render(value["label"]) + "\t" + render(value["value"]) + ("\n" + extras if extras else "")
        if kind == "heading":
            prefix = "#" * value["level"] + " " if format == "markdown" else ""
            return prefix + render(value["value"])
        if kind == "media":
            return "[media " + value["media_id"] + "]"
        if kind == "separator":
            return "---"
        if kind == "opaque":
            return value["value"] if isinstance(value["value"], str) else json.dumps(value["value"], ensure_ascii=False)
        if kind == "table":
            rows = ["\t".join(render(cell) for cell in value.get("header", []))] if "header" in value else []
            rows += ["\t".join(render(cell["blocks"]) for cell in row["cells"]) for row in value["rows"]]
            return "\n".join(rows) + "\n" + render(value.get("children", []))
        if kind == "dialogue":
            lines = []
            for group in value["groups"]:
                lines.append(f"[group {group.get('group_id', '')} root {group.get('root_id', '')}]")
                for node in group.get("nodes", []):
                    lines.append("[node " + node["source_id"] + "]\n" + render(node.get("fields", node.get("value"))))
                lines.extend(f"[edge {edge['parent_id']} -> {edge['child_id']}]" for edge in group.get("edges", []))
                lines.append(render(group.get("children", [])))
            return "\n".join(lines) + render(value.get("children", []))
        if value.get("source_type") == "reference":
            return "[reference " + value["relation_id"] + "]"
        return "\n".join(render(value[key]) for key in ("title", "value", "children", "blocks", "components") if key in value)

    lines = [render(record["document"].get("title")), render(record["document"].get("description"))]
    for listing in record["document"]["listing_observations"]:
        for field in listing["fields"]:
            if _label(field.get("label")) in {"title", "summary", "alias_name", "article_user_name"}:
                lines.append(render(field))
    for section in record["sections"]:
        lines.append(render(section.get("title")))
        lines.append(render(section.get("components", section.get("blocks", []))))
    return "\n".join(lines)
