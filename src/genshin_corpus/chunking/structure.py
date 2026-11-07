"""Two Clean input adapters. Source text and derived display context are separate."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict
import re

from ..clean import CleanRef, iter_blocks, iter_text_values
from .core import identity
from .policy import (admission, normalized_label, MECHANICAL_SECTIONS,
                     TABLE_MECHANICAL_LABELS, TABLE_IDENTITY_LABELS, TABLE_MECHANICAL_TITLES)
from .dialogue import label, graph_order, analyze_graph, text_sources


def article_titles(children: list[dict]) -> dict[str, list[dict]]:
    """Repeated, fully centered, bracketed titles in one source HTML container."""
    titles = {}
    for child in children:
        text = label(child.get("value"))
        if child.get("kind") != "paragraph" or not re.fullmatch(r"《[^《》\n]+》(?:[^。\n]*《[^《》\n]+》)?", text.strip()):
            continue
        marks = [mark for mark in child["value"].get("marks", [])
                 if mark.get("kind") == "presentation"
                 and re.search(r"(?:^|;)\s*text-align\s*:\s*center\s*(?:;|$)", mark.get("style", ""), re.I)
                 and mark["range"][0] <= len(text) - len(text.lstrip())
                 and mark["range"][1] >= len(text.rstrip())]
        if marks:
            titles[child["block_id"]] = deepcopy(marks)
    return titles if len(titles) >= 2 else {}


def is_puzzle_fold(block: dict) -> bool:
    """Only explicitly labelled puzzle folds, with no separately labelled story."""
    if block.get("source_tag") != "details":
        return False
    summary = next((label(child.get("value")) for child in block.get("children", [])
                    if child.get("source_tag") == "summary"), "")
    if not re.fullmatch(r"[·•\s]*(?:解谜流程|解谜步骤|解谜攻略)(?:可以点击此处折叠栏查看)?[：:\s]*", summary):
        return False
    stack = list(block.get("children", []))
    while stack:
        child = stack.pop()
        # A nested section may mix in story or other unknown content. Keep the
        # entire fold without trying to classify its subheadings semantically.
        if child.get("kind") == "heading" or child.get("source_tag") == "details":
            return False
        stack.extend(child.get("children", []))
    return True


class _Adapter:
    def __init__(self, record: dict, blocks=None):
        self.record = record
        self.parents = {}
        self.audit = {}
        self.graphs = []
        self.blocks = blocks if blocks is not None else {block["block_id"]: block for block in iter_blocks(record)}
        self.document = record["document"]
        self.title = label(self.document.get("title"))
        self.channels = deepcopy(self.document["memberships"])
        self.channel_names = {label(node["name"]) for member in self.channels for node in member["path"]}
        self.section = {}
        self.component = ""
        self.task_context = []
        source_relations = {r["relation_id"]: r for r in record["relations"]}
        for block in self.blocks.values():
            if block.get("kind") != "field" or normalized_label(label(block.get("label"))) not in {
                    "前置任务", "后续任务", "触发条件", "起始NPC", "结束NPC"}:
                continue
            sources = list(text_sources(block.get("value"), record["observation_id"]))
            relation_ids = list(dict.fromkeys(r for source in sources for r in source["relation_ids"]))
            self.task_context.append({"label": label(block["label"]),
                "section_id": block["context"].get("section_id"),
                "sources": sources, "source_relations": [source_relations[r] for r in relation_ids]})

    def parent(self, key: str, labels: list[str], kind: str, context: dict | None = None) -> dict:
        parent_id = identity([self.record["observation_id"], self.section.get("section_id"), key])
        if parent_id not in self.parents:
            sections = self.record["sections"]
            section_index = next((i for i, s in enumerate(sections) if s["section_id"] == self.section.get("section_id")), None)
            neighbors = {"previous": sections[section_index - 1]["section_id"] if section_index else None,
                         "next": sections[section_index + 1]["section_id"]
                         if section_index is not None and section_index + 1 < len(sections) else None}
            self.parents[parent_id] = {
                "parent_id": parent_id, "observation_id": self.record["observation_id"],
                "content_id": self.record["source_item"]["content_id"],
                "section_id": self.section.get("section_id", "document"),
                "component_id": self.component, "source_container": key, "kind": kind,
                "adapter": "graph" if kind == "dialogue_group" else "structure",
                "labels": [self.title, label(self.section.get("title")), *labels],
                "context": {"memberships": self.channels,
                    "document_tags": deepcopy(self.document["tags"]),
                    "section_tags": deepcopy(self.section.get("tags", [])),
                    "layout_observations": deepcopy(self.section.get("layout_observations", [])),
                    "section_source_order": self.section.get("ordering"),
                    "order_semantics": "display_only",
                    "task_context": self.task_context,
                    "source_section_neighbors": neighbors,
                    "source_provenance": deepcopy(self.document["provenance"]),
                    "label_evidence": {"document_title": self.document.get("title"),
                                       "section_title": self.section.get("title")},
                    "label_sources": [*text_sources(self.document.get("title"), self.record["observation_id"]),
                                      *text_sources(self.section.get("title"), self.record["observation_id"]),
                                      *text_sources((context or {}).get("group_title"), self.record["observation_id"])],
                    **(context or {})},
                "units": [],
            }
        return self.parents[parent_id]

    def leaf(self, block: dict, parent: dict, fields: list[str], labels: list[str],
             extra: dict | None = None, force: tuple[str, str] | None = None):
        kind = block["kind"]
        decision, reason = force or admission(self.component, label(self.section.get("title")),
                                               fields, labels, kind, channel_names=self.channel_names)
        values = list(iter_text_values(self.blocks.get(block["block_id"], block)))
        if kind == "opaque" and not values:
            self.audit[(block["block_id"], "__block__")] = {
                "content_id": self.record["source_item"]["content_id"],
                "clean_ref": None, "block_id": block["block_id"],
                "section_id": self.section.get("section_id"), "component_id": self.component,
                "field_keys": fields, "field_labels": labels, "chars": len(str(block.get("value", ""))),
                "decision": "review" if block.get("value") else "resource",
                "reason": "unsupported_payload" if block.get("value") else "source_empty_component",
                "parent_id": None}
        parts = []
        for value in values:
            if value["value_pointer"] != "/value":
                continue
            text = value["text"]
            if isinstance(block.get("value"), dict) and block["value"].get("source_type") == "null":
                decision, reason = "context", "source_null_placeholder"
            # Exact source-written field labels in rich text are structurally usable.
            if decision == "include" and re.fullmatch(r"上线版本[：:]\s*\d+(?:\.\d+)*\s*", text):
                decision, reason = "exclude", "explicit_mechanical_label_in_text"
            quantity = r"[^\d*×\n。！？；：:]{1,40}\s*[*×]\s*\d+(?:\.\d+)?"
            quantity_text = re.sub(r"^达成奖励[：:]\s*", "", text.strip())
            quantity_scope = label(self.section.get("title")).strip() in {"任务奖励", "奖励", "商品列表", "商店"}
            if (decision == "include" and (quantity_scope or text.startswith(("达成奖励：", "达成奖励:")))
                    and re.fullmatch(r"(?:\s*" + quantity + r"\s*)+", quantity_text)):
                decision, reason = "exclude", "explicit_reward_quantity"
            if (decision == "include" and self.component == "material_base_info"
                    and "千星奇域" in self.channel_names
                    and "特殊" in {normalized_label(item) for item in labels}
                    and text.strip() in {"激活套装效果必须部件", "部件可调整"}):
                decision, reason = "exclude", "outfit_explicit_control_value"
            disposition = decision if text.strip() else "empty"
            ref = asdict(CleanRef(self.record["observation_id"], block["block_id"],
                                 "/value", 0, len(text)))
            key = (block["block_id"], "/value")
            if key in self.audit:
                raise ValueError("Source value processed twice")
            self.audit[key] = {
                "content_id": self.record["source_item"]["content_id"], "clean_ref": ref,
                "section_id": self.section.get("section_id", "document"),
                "component_id": self.component, "field_keys": fields, "field_labels": labels,
                "decision": disposition, "reason": reason, "chars": len(text),
                "parent_id": parent["parent_id"] if disposition == "include" else None,
            }
            if disposition == "include":
                human_label = next((item for item in reversed(labels) if item and not re.fullmatch(r"[A-Za-z0-9_/*:.-]+", item)), "")
                parts.append({"text": text, "clean_ref": ref, "field_keys": fields,
                              "field_labels": labels, "source_kind": kind,
                              "admission_reason": reason,
                              "display_prefix": f"【{human_label}】" if human_label else "", **(extra or {})})
        return parts

    def visit(self, value, parent: dict, fields=None, labels=None, extra=None, force=None):
        fields, labels, extra = fields or [], labels or [], extra or {}
        if isinstance(value, list):
            for child in value:
                self.visit(child, parent, fields, labels, extra, force)
            return
        if not isinstance(value, dict) or "kind" not in value:
            return
        kind = value["kind"]
        if not force and self.component == "collapse_panel" and "成就" in self.channel_names and is_puzzle_fold(value):
            force = ("exclude", "achievement_puzzle_fold")
        if kind in {"paragraph", "heading", "scalar", "opaque", "media", "separator"}:
            parts = self.leaf(value, parent, fields, labels, extra, force)
            if parts:
                parent["units"].append({"parts": parts, "boundary_before": kind == "heading"})
            return
        if kind == "field":
            field = value["field_key"]
            self.visit(value.get("label"), parent, fields, labels, extra,
                       ("context", "source_field_label"))
            self.visit(value.get("value"), parent, fields + [field],
                       labels + [label(value.get("label"))], extra, force)
            self.visit(value.get("children", []), parent, fields + [field], labels, extra, force)
            return
        if kind == "dialogue":
            self.dialogue(value)
            return
        if kind == "table":
            self.table(value, parent, fields, labels, force)
            return
        title = label(value.get("title"))
        if title and not extra:
            # An explicitly named object is a natural item, never joined to siblings.
            parent = self.parent(value["block_id"], [*parent["labels"][2:], title],
                                 "named_item", {"group_title": value["title"],
                                 "ancestor_parent_id": parent["parent_id"]})
            self.visit(value["title"], parent, fields, labels, extra,
                       ("context", "source_group_title"))
        children = value.get("children", value.get("blocks", []))
        titles = article_titles(children) if value.get("source_type") == "html" and not force else {}
        active_extra = extra
        # Preserve local HTML headings as subchapter parents. Heading depth is evidence.
        active = parent
        for child in children:
            if child.get("kind") == "heading":
                active_extra = extra
            if child["block_id"] in titles:
                title_ref = asdict(CleanRef(self.record["observation_id"], child["block_id"],
                                          "/value", 0, len(label(child["value"]))))
                active_extra = {**extra, "article_scope_id": identity([parent["parent_id"], child["block_id"]]),
                                "article_title": {"text": label(child["value"]), "clean_ref": title_ref},
                                "article_boundary_evidence": titles[child["block_id"]]}
            if child.get("kind") == "heading" and label(child.get("value")) and not force and not extra:
                active = self.parent(child["block_id"], [*parent["labels"][2:], label(child["value"])],
                                     "heading", {"ancestor_parent_id": parent["parent_id"],
                                     "heading_level": child.get("level")})
            if active["kind"] == "heading" and active is not parent:
                active.setdefault("readback_block_ids", []).append(child["block_id"])
            self.visit(child, active, fields, labels, active_extra, force)

    def table(self, block, parent, fields, labels, force):
        tab = next((label(child.get("value")) for child in block.get("children", [])
                    if child.get("field_key", "").endswith("/tab_name")), "")
        headers = [label(header) for header in block.get("header", [])]
        html_headers = {}
        for row in block.get("rows", []):
            for cell in row["cells"]:
                if cell.get("is_header"):
                    for column in range(cell["column_index"], cell["column_index"] + int(cell.get("colspan", 1))):
                        html_headers[column] = label(cell["blocks"])
        column_names = {normalized_label(name) for name in headers or html_headers.values() if name.strip()}
        table_titles = {normalized_label(name) for name in [tab, label(self.section.get("title")), *parent["labels"]]}
        mechanical_columns = TABLE_MECHANICAL_LABELS
        if self.component == "filter_table" and "原神OST" in self.channel_names:
            mechanical_columns = mechanical_columns | {"时长"}
        mechanical_table = bool(column_names) and (
            column_names <= mechanical_columns | TABLE_IDENTITY_LABELS
            and bool(column_names & mechanical_columns)
            or bool(table_titles & TABLE_MECHANICAL_TITLES)
                and column_names <= mechanical_columns | TABLE_IDENTITY_LABELS | {"角色解析"})
        parent = self.parent(block["block_id"], [*parent["labels"][2:], tab], "table",
                             {"ancestor_parent_id": parent["parent_id"],
                              "table_block_id": block["block_id"], "table_headers": headers,
                              "table_header_blocks": block.get("header", []),
                              "html_header_columns": html_headers})
        for header in block.get("header", []):
            self.visit(header, parent, fields, labels, force=("context", "table_header"))
        # Each row is a protected unit, with logical columns and spans kept on parts.
        for index, row in enumerate(block.get("rows", [])):
            before = len(parent["units"])
            row_label = label(row["cells"][0]["blocks"]) if row["cells"] else ""
            row_label = row_label if len(row_label) <= 80 else ""
            for cell in row["cells"]:
                column = cell.get("column_index", 0)
                column_label = headers[column] if column < len(headers) else html_headers.get(column, "")
                display_labels = [item for item in (row_label, column_label) if item]
                cell_extra = {"table_row": index, "column_index": column,
                              "rowspan": cell.get("rowspan", 1), "colspan": cell.get("colspan", 1),
                              "column_label": column_label, "row_label": row_label,
                              "row_label_blocks": [child["block_id"] for child in row["cells"][0]["blocks"]] if row["cells"] else [],
                              "display_prefix": "【" + " / ".join(display_labels) + "】" if display_labels else ""}
                cell_force = force
                if cell.get("is_header"):
                    cell_force = ("context", "html_table_header")
                elif not force and mechanical_table:
                    cell_force = (("context", "mechanical_table_identity") if normalized_label(column_label) in TABLE_IDENTITY_LABELS
                                  else ("exclude", "mechanical_table_column"))
                elif not force and normalized_label(column_label) in mechanical_columns:
                    cell_force = ("exclude", "mechanical_table_column")
                self.visit(cell["blocks"], parent, fields, labels + [column_label], cell_extra, cell_force)
            units = parent["units"][before:]
            parent["units"][before:] = ([{"parts": [part for unit in units for part in unit["parts"]],
                                         "boundary_before": True}] if units else [])
        self.visit(block.get("children", []), parent, fields, labels, force=force)

    def dialogue(self, block):
        for group in block["groups"]:
            analysis = analyze_graph(group, self.record["observation_id"])
            order = analysis["display_order"]
            parent = self.parent(block["block_id"] + "/" + group["group_id"], [], "dialogue_group",
                {"dialogue_block_id": block["block_id"], "group_id": group["group_id"],
                 "graph_issues": analysis["issues"], "order_semantics": "display_only"})
            nodes = {node["source_id"]: node for node in group["nodes"]}
            graph = {"parent_id": parent["parent_id"], "observation_id": self.record["observation_id"],
                     "content_id": self.record["source_item"]["content_id"],
                     "section_id": self.section["section_id"], "group": deepcopy(group), **analysis}
            for node_id in order:
                node = nodes[node_id]
                info = analysis["derived_nodes"][node_id]
                before = len(parent["units"])
                fields = sorted(node.get("fields", []), key=lambda field:
                    {"option": 0, "dialogue": 1}.get(field["field_key"].rsplit("/", 1)[-1], 2))
                option_sources = [source for field in fields if field["field_key"].endswith("/option")
                                  for source in text_sources(field["value"], self.record["observation_id"])
                                  if source["text"].strip()]
                for field in fields:
                    self.visit(field, parent, extra={"node_id": node_id,
                        "node_source_order": node["ordering"], "node_display_order": info["display_order"],
                        "speaker": node.get("speaker"), "condition_sources": info["condition_sources"],
                        "condition_status": info["condition_status"],
                        "source_annotations": info["source_annotations"],
                        "incoming_condition_sources": info["incoming_condition_sources"],
                        "incoming_node_ids": info["incoming_node_ids"], "outgoing_node_ids": info["outgoing_node_ids"],
                        "source_component_id": info["source_component_id"], "reachability": info["reachability"],
                        "branch_context": info["branch_context"], "branch_joins": info["branch_joins"],
                        "choice_point": info["choice_point"], "source_fork": info["source_fork"],
                        "option_context": option_sources})
                parts = [part for unit in parent["units"][before:] for part in unit["parts"]]
                markers = []
                if info["choice_point"]:
                    markers.append("【选择点】")
                elif info["source_fork"]:
                    markers.append("【源图分叉，关系待解释】")
                if any(branch[2] == "choice" for branch in info["branch_context"]):
                    markers.append("【并列选项，本次选择择一】")
                if any(join["kind"] == "choice" for join in info["branch_joins"]):
                    markers.append("【公共后续】")
                elif len(info["incoming_node_ids"]) > 1:
                    markers.append("【源图汇合】")
                incoming_scopes = {tuple(s["condition_id"] for s in entry["sources"])
                                   for entry in info["incoming_condition_sources"]}
                if len(incoming_scopes) > 1:
                    markers.append("【入边条件不同，见来源连接】")
                if info["condition_status"] == "unresolved":
                    markers.append("【条件或连接待解释】")
                node_units = []
                for index, part in enumerate(parts):
                    part["display_prefix"] = "".join(markers)
                    part["node_part_order"] = index
                    part["node_has_heading"] = any(p["source_kind"] == "heading" for p in parts)
                    if not node_units or node_units[-1]["parts"][-1].get("article_scope_id") != part.get("article_scope_id"):
                        node_units.append({"parts": [], "boundary_before": len(info["incoming_node_ids"]) != 1,
                            "hard_boundary_before": not node_units and (part["node_has_heading"]
                                or (not info["incoming_node_ids"] and node_id != group.get("root_id"))
                                or "cycle_unsequenced" in analysis["issues"])})
                    node_units[-1]["parts"].append(part)
                parent["units"][before:] = node_units
            node_parts = defaultdict(list)
            for unit in parent["units"]:
                for part in unit["parts"]:
                    node_parts[part["node_id"]].append(part)
            for node_id, parts in node_parts.items():
                info = analysis["derived_nodes"][node_id]
                if len(info["incoming_node_ids"]) != 1 or any(p["node_has_heading"] for p in parts):
                    continue
                predecessor = info["incoming_node_ids"][0]
                source_parts = [p for p in node_parts.get(predecessor, []) if p["field_keys"][-1].endswith("/dialogue")]
                if source_parts:
                    source = source_parts[-1]
                    text = source["text"]
                    start = 0
                    if len(text) > 140:
                        ends = [m.end() for m in re.finditer(r"[。！？!?]+[”’」』）)]*", text)]
                        start = next((end for end in ends if len(text) - end <= 140 and text[end:].strip()), len(text))
                    if text[start:].strip():
                        ref = {**source["clean_ref"], "char_start": source["clean_ref"]["char_start"] + start}
                        for part in parts:
                            part["upstream_context"] = [{"text": text[start:], "clean_ref": ref}]
            self.graphs.append(graph)
            graph["local_scopes"] = [{"local_scope_id": identity([parent["parent_id"], key]),
                                     "node_id": key, "ordering": nodes[key]["ordering"],
                                     "order_semantics": "source_array_only"} for key in order]
            self.visit(group.get("children", []), parent)
        fallback = self.parent(block["block_id"] + "/metadata", [], "dialogue_metadata")
        self.visit(block.get("children", []), fallback)

    def run(self):
        description = self.document.get("description")
        channel_names = {label(node["name"]) for member in self.channels for node in member["path"]}
        if description:
            parent = self.parent("document_description", [], "document_description")
            redundant = label(description).strip() == self.title.strip() and "成就" not in channel_names
            self.visit(description, parent, force=("context", "duplicate_title_description") if redundant
                       else ("include", "source_document_description"))
        for section in self.record["sections"]:
            self.section = section
            for index, component in enumerate(section.get("components", [])):
                self.component = component["component_id"]
                parent = self.parent(f"{section['section_id']}/c{index}", [], "section")
                parent["component_index"] = index
                self.visit(component["blocks"], parent)
        # Same named voice entry and exactly matching source paragraphs only.
        # Keep every occurrence and parent, with one candidate representation.
        voices = {}
        for parent in self.parents.values():
            if parent["component_id"] != "role_voice" or not parent["units"]:
                continue
            parts = [part for unit in parent["units"] for part in unit["parts"]]
            key = (parent["labels"][-1], tuple(part["text"] for part in parts))
            if key not in voices:
                voices[key] = parent
                continue
            original = voices[key]
            parent["exact_alias_of_parent_id"] = original["parent_id"]
            primary = [part for unit in original["units"] for part in unit["parts"]]
            for target, alias in zip(primary, parts):
                target.setdefault("alias_clean_refs", []).append(alias["clean_ref"])
        # Account for source values outside normal section bodies and unsupported shapes.
        for block in self.blocks.values():
            for value in iter_text_values(block):
                key = (block["block_id"], value["value_pointer"])
                if key in self.audit:
                    continue
                context = block["context"]
                is_body = bool(context.get("component_id")) and value["value_pointer"] == "/value"
                self.audit[key] = {"content_id": self.record["source_item"]["content_id"],
                    "clean_ref": asdict(CleanRef(self.record["observation_id"], block["block_id"],
                                                value["value_pointer"], 0, len(value["text"]))),
                    "decision": "review" if is_body and value["text"].strip() else "context",
                    "reason": "unhandled_source_shape" if is_body else "document_or_control_metadata",
                    "chars": len(value["text"]), "section_id": context.get("section_id"),
                    "component_id": context.get("component_id"), "field_keys": [context.get("field_key", "")],
                    "field_labels": context.get("field_labels", []), "parent_id": None}
        return {"parents": list(self.parents.values()), "decisions": list(self.audit.values()),
                "graphs": self.graphs}


def adapt_record(record: dict, *, blocks=None) -> dict:
    """Return natural parents, admitted source units, graph sidecars and dispositions."""
    return _Adapter(record, blocks).run()
