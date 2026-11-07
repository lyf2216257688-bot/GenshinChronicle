"""Two Clean input adapters. Source text and derived display context are separate."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict
import heapq
import re

from ..clean import CleanRef, iter_blocks, iter_text_values
from .core import identity
from .policy import admission, normalized_label


def label(value) -> str:
    if isinstance(value, dict):
        if "text" in value:
            return value["text"]
        return label(value.get("value", value.get("children", [])))
    if isinstance(value, list):
        return "\n".join(text for child in value if (text := label(child)))
    return ""


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


def graph_order(group: dict) -> tuple[list[str], dict, dict, list[str]]:
    """Root/edge-order traversal supplies tie order. Joins wait for all parents."""
    nodes = {node["source_id"]: node for node in group["nodes"]}
    children = {node_id: [] for node_id in nodes}
    parents = {node_id: [] for node_id in nodes}
    issues = []
    for edge in group["edges"]:
        a, b = edge["parent_id"], edge["child_id"]
        if a not in nodes or b not in nodes:
            issues.append("dangling_edge")
            continue
        children[a].append(b)
        parents[b].append(a)
    rank = {}
    seeds = ([group["root_id"]] if group.get("root_id") in nodes else []) + list(nodes)
    for seed in seeds:
        stack = [seed]
        while stack:
            node_id = stack.pop()
            if node_id in rank:
                continue
            rank[node_id] = len(rank)
            stack.extend(reversed(children[node_id]))
    remaining = {key: len(value) for key, value in parents.items()}
    ready = [(rank[key], key) for key, degree in remaining.items() if degree == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        _, node_id = heapq.heappop(ready)
        order.append(node_id)
        for child in children[node_id]:
            remaining[child] -= 1
            if remaining[child] == 0:
                heapq.heappush(ready, (rank[child], child))
    if len(order) != len(nodes):
        issues.append("cycle_unsequenced")
        order.extend(key for key in nodes if key not in set(order))
    if nodes and group.get("root_id") not in nodes:
        issues.append("missing_root")
    return order, children, parents, sorted(set(issues))


class _Adapter:
    def __init__(self, record: dict):
        self.record = record
        self.parents = {}
        self.audit = {}
        self.graphs = []
        self.blocks = {block["block_id"]: block for block in iter_blocks(record)}
        self.document = record["document"]
        self.title = label(self.document.get("title"))
        self.channels = deepcopy(self.document["memberships"])
        self.channel_names = {label(node["name"]) for member in self.channels for node in member["path"]}
        self.section = {}
        self.component = ""

    def parent(self, key: str, labels: list[str], kind: str, context: dict | None = None) -> dict:
        parent_id = identity([self.record["observation_id"], self.section.get("section_id"), key])
        if parent_id not in self.parents:
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
                    "source_provenance": deepcopy(self.document["provenance"]),
                    "label_evidence": {"document_title": self.document.get("title"),
                                       "section_title": self.section.get("title")},
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
            # Exact source-written field labels in rich text are structurally usable.
            if decision == "include" and re.match(r"^(?:达成奖励|上线版本|使用效果)[：:]", text):
                decision, reason = "exclude", "explicit_mechanical_label_in_text"
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
                cell_force = ("context", "html_table_header") if cell.get("is_header") else force
                self.visit(cell["blocks"], parent, fields, labels + [column_label], cell_extra, cell_force)
            units = parent["units"][before:]
            parent["units"][before:] = ([{"parts": [part for unit in units for part in unit["parts"]],
                                         "boundary_before": True}] if units else [])
        self.visit(block.get("children", []), parent, fields, labels, force=force)

    def dialogue(self, block):
        for group in block["groups"]:
            order, children, incoming, issues = graph_order(group)
            parent = self.parent(block["block_id"] + "/" + group["group_id"], [], "dialogue_group",
                                 {"dialogue_block_id": block["block_id"], "group_id": group["group_id"],
                                  "graph_issues": issues})
            nodes = {node["source_id"]: node for node in group["nodes"]}
            ordinal = {node_id: i + 1 for i, node_id in enumerate(order)}
            conditions = {}
            graph = {"parent_id": parent["parent_id"], "observation_id": self.record["observation_id"],
                     "content_id": self.record["source_item"]["content_id"],
                     "section_id": self.section["section_id"], "group": deepcopy(group),
                     "display_order": order, "issues": issues}
            for node_id in order:
                node = nodes[node_id]
                predecessor_sets = [conditions.get(source, {}) for source in incoming[node_id]]
                inherited = dict(predecessor_sets[0]) if predecessor_sets else {}
                for candidate in predecessor_sets[1:]:
                    inherited = {key: value for key, value in inherited.items() if key in candidate}
                before = len(parent["units"])
                fields = sorted(node["fields"], key=lambda field: {"option": 0, "dialogue": 1}.get(field["field_key"].rsplit("/", 1)[-1], 2))
                for field in fields:
                    suffix = field["field_key"].rsplit("/", 1)[-1]
                    if suffix == "option":
                        text = label(field["value"])
                        if re.search(r"^(?:未完成|完成).*(?:触发|之后|以后|后)", text):
                            inherited[node_id] = {"text": text, "source_block_id": field["value"]["block_id"]}
                    self.visit(field, parent, extra={"node_id": node_id,
                               "node_source_order": node["ordering"], "speaker": node.get("speaker"),
                               "condition_sources": list(inherited.values())})
                conditions[node_id] = inherited
                units = parent["units"][before:]
                parts = [part for unit in units for part in unit["parts"]]
                next_nodes = [str(ordinal[child]) for child in children[node_id]]
                next_label = "、".join(next_nodes) if len(next_nodes) <= 8 else f"{len(next_nodes)}个后继，见连接表"
                prefix = f"【节点{ordinal[node_id]}" + (f" → {next_label}" if next_label else "") + "】"
                if inherited:
                    prefix += "【来源条件 " + " / ".join(item["text"] for item in inherited.values()) + "】"
                for part in parts:
                    part["display_prefix"] = prefix
                    part["node_has_heading"] = any(candidate["source_kind"] == "heading" for candidate in parts)
                # A source node may contain several articles. Preserve those scopes
                # before handing protected units to either granularity's packer.
                node_units = []
                for part in parts:
                    if not node_units or node_units[-1]["parts"][-1].get("article_scope_id") != part.get("article_scope_id"):
                        node_units.append({"parts": [], "boundary_before": len(incoming[node_id]) != 1,
                                           "hard_boundary_before": not node_units and any(p["source_kind"] == "heading" for p in parts)})
                    node_units[-1]["parts"].append(part)
                parent["units"][before:] = node_units
            node_parts = defaultdict(list)
            for unit in parent["units"]:
                for part in unit["parts"]:
                    node_parts[part["node_id"]].append(part)
            for node_id, parts in node_parts.items():
                has_option = any(part["field_keys"][-1].endswith("/option") for part in parts)
                if has_option and len(incoming[node_id]) == 1 and not any(part["node_has_heading"] for part in parts):
                    source_parts = [part for part in node_parts.get(incoming[node_id][0], [])
                                    if part["field_keys"][-1].endswith("/dialogue")]
                    # A bounded complete source paragraph, not a generated summary.
                    if source_parts and len(source_parts[-1]["text"]) <= 140:
                        source = {"text": source_parts[-1]["text"], "clean_ref": source_parts[-1]["clean_ref"]}
                        for part in parts:
                            part["upstream_context"] = [source]
            self.graphs.append(graph)
            graph["local_scopes"] = [{"local_scope_id": identity([parent["parent_id"], node_id]),
                                       "node_id": node_id, "ordering": nodes[node_id]["ordering"]}
                                      for node_id in order]
            self.visit(group.get("children", []), parent)
        fallback = self.parent(block["block_id"] + "/metadata", [], "dialogue_metadata")
        self.visit(block.get("children", []), fallback)

    def run(self):
        description = self.document.get("description")
        channel_names = {label(node["name"]) for member in self.channels for node in member["path"]}
        if description and (label(description) != self.title or "成就" in channel_names):
            parent = self.parent("document_description", [], "document_description")
            self.visit(description, parent, force=("include", "source_document_description"))
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


def adapt_record(record: dict) -> dict:
    """Return natural parents, admitted source units, graph sidecars and dispositions."""
    return _Adapter(record).run()
