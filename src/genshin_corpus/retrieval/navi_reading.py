"""Opt-in candidate V reading catalog and source-bound batch preparation.

These are research derivatives, not a quest DAG, evidence identity, or selector.
All body text is read from verified Canonical occurrences and resolved through
the existing RU projection. No provider transport or semantic classification.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from genshin_corpus.canonical.fingerprints import sha256_json
from . import retrieval_units as ru
from .navi_exploration import ExplorationError, LocalExplorationTools

SCHEMA = "phase05-navi-v-reading-0.1"
HYPOTHESIS = "unverified_hypothesis_not_fact"


def text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExplorationError(f"invalid {label}")
    return value


def filter_tags(ext: str, channel_id: str) -> list[str]:
    value = json.loads(ext).get(f"c_{channel_id}", {}).get("filter", {}).get("text", "[]")
    tags = json.loads(value)
    if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        raise ExplorationError("invalid source filter tags")
    return tags


def build_release_catalog(listing: Mapping[str, Any], *, source: str, locale: str,
                          channel_id: str = "43") -> dict[str, Any]:
    """Use the archived official filter order; ties preserve Listing position."""
    groups = [g for g in listing["data"]["list"] if str(g["id"]) == channel_id]
    if len(groups) != 1:
        raise ExplorationError("ambiguous or absent quest channel")
    group = groups[0]
    attributes = json.loads(group["ch_ext"])
    filters = [json.loads(a["value"]) for a in attributes if a["attribute_key"] == "filter"]
    versions = [f for fs in filters for f in fs if f["label"] == "版本号"]
    if len(versions) != 1:
        raise ExplorationError("ambiguous or absent version order")
    order = [text(v["label"], "version label") for v in versions[0]["children"]]
    if not order or len(order) != len(set(order)):
        raise ExplorationError("invalid source version order")
    queued, pending, seen = [], [], set()
    type_counts, combinations, version_counts = Counter(), Counter(), Counter()
    for position, item in enumerate(group["list"]):
        cid = str(item["content_id"])
        key = f"{source}:{locale}:{cid}"
        if key in seen:
            raise ExplorationError("duplicate Listing source identity")
        seen.add(key)
        row = {"source_identity_key": key, "content_id": cid, "title": item["title"],
               "listing_position": position,
               "source_pointer": f"/data/list/{listing['data']['list'].index(group)}/list/{position}/ext"}
        try:
            tags = filter_tags(item["ext"], channel_id)
            labels = [t.split("/", 1)[1] for t in tags if t.startswith("版本号/")]
            types = sorted({t.split("/", 1)[1] for t in tags if t.startswith("任务类型/")})
            row.update(source_tags=tags, task_types=types, version_labels=labels)
            type_counts.update(types)
            combinations[" + ".join(types) or "(missing)"] += 1
            if len(labels) != 1:
                row["reason"] = "missing_version" if not labels else "conflicting_versions"
            elif labels[0] not in order:
                row["reason"] = "unknown_version"
            else:
                row.update(version=labels[0], version_ordinal=order.index(labels[0]))
                version_counts[labels[0]] += 1
                queued.append(row)
                continue
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            row["reason"] = "invalid_filter"
            row["error_type"] = type(exc).__name__
        pending.append(row)
    queued.sort(key=lambda r: (r["version_ordinal"], r["listing_position"]))
    result = {
        "schema_version": SCHEMA, "source": source, "locale": locale,
        "listing_value_sha256": sha256_json(listing), "version_order": order,
        "ordering_caveat": "source version labels; ties are presentation order, not chronology or prerequisites",
        "queue": queued, "pending_metadata": pending,
        "counts": {"pages": len(seen), "queued": len(queued), "pending_metadata": len(pending),
                   "version_labels": dict(version_counts), "type_occurrences": dict(type_counts),
                   "type_combinations": dict(combinations)},
    }
    result["catalog_identity"] = sha256_json(result)
    return result


def _dialogue_order(units: list[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], list[dict[str, Any]]]:
    """Traverse explicit source edges; retain branches, diagnostics and orphans."""
    rich = [u for u in units if u["kind"] == "rich_text"]
    node_rank, graphs = {}, []
    for unit in units:
        if unit["kind"] != "dialogue_graph":
            continue
        for group in unit["value"].get("groups", []):
            nodes = {n["source_id"]: n for n in group["nodes"]}
            adjacency = defaultdict(list)
            for edge in group["edges"]:
                adjacency[edge["parent_id"]].append((edge["ordering"], edge["child_id"]))
            visited, ordered = set(), []

            def visit(nid: str) -> None:
                stack = [nid]
                while stack:
                    current = stack.pop()
                    if current in visited or current not in nodes:
                        continue
                    visited.add(current)
                    ordered.append(current)
                    stack.extend(child for _, child in reversed(sorted(adjacency[current])))

            visit(group.get("root_id"))
            orphans = [n for n in nodes if n not in visited]
            for node in orphans:
                visit(node)
            graph_number = len(graphs)
            for rank, nid in enumerate(ordered):
                node_rank[f"rich_text:/contents/{nid}/dialogue"] = (graph_number, rank)
            graphs.append({
                "root_id": group.get("root_id"), "source_path": group.get("source_path"),
                "nodes": [{"source_id": n["source_id"], "option": n.get("option")}
                          for n in group["nodes"]],
                "edges": [{"parent_id": e["parent_id"], "child_id": e["child_id"],
                           "ordering": e["ordering"]} for e in group["edges"]],
                "diagnostics": group.get("diagnostics", []), "unreachable_node_ids": orphans,
                "display_order": ordered,
                "caveat": "source topology only; branches are not one asserted game timeline",
            })
    def sort_key(u):
        sid = u.get("metadata", {}).get("parsed_identity", {}).get("components", {}).get("source_id")
        return (*node_rank.get(sid, (len(graphs), u["ordinal"])), u["ordinal"])
    return sorted(rich, key=sort_key), graphs


def partition_record(record: Mapping[str, Any], rows: list[Mapping[str, Any]], *,
                     snapshot_identity: str, max_batch_chars: int = 8000,
                     anchor_section: int | None = None) -> dict[str, Any]:
    """Partition by verified source layout; never equate a module group to a quest."""
    if not isinstance(max_batch_chars, int) or isinstance(max_batch_chars, bool) or max_batch_chars <= 0:
        raise ExplorationError("invalid batch body limit")
    metadata = record["record_metadata"]
    memberships = metadata.get("channel_memberships", [])
    is_quest = any(str(c.get("channel_id") if isinstance(c, dict) else c) == "43" for c in memberships)
    # Parsed membership representation is a list of source channel IDs.
    grouped, issues = defaultdict(list), []
    template = metadata.get("source_template_layout", {})
    placements = {}
    for ti, tab in enumerate(template.get("tab", [])):
        for gi, group in enumerate(tab["module_group"]):
            for pi, module in enumerate(group["module"]):
                placements[(ti, gi, pi)] = (
                    str(tab["tab_id"]), str(group["module_group_id"]), str(module["id"]))
    for section in record["sections"]:
        observations = section["source_metadata"].get("layout_observations", [])
        if len(observations) != 1:
            issues.append({"section_ordinal": section["ordinal"], "reason": "missing_or_ambiguous_layout"})
            continue
        observation = observations[0]
        placement = (observation["tab_index"], observation["group_index"], observation["placement_index"])
        expected = (str(observation["tab_id"]), str(observation["group_id"]),
                    str(section["source_metadata"].get("source_module_id")))
        if placements.get(placement) != expected:
            issues.append({"section_ordinal": section["ordinal"], "reason": "layout_template_mismatch",
                           "observation": observation})
            continue
        key = (observation["tab_index"], observation["group_index"],
               str(observation["tab_id"]), str(observation["group_id"]))
        grouped[key].append((observation["placement_index"], section))
    if anchor_section is not None:
        selected = [key for key, sections in grouped.items()
                    if any(s["ordinal"] == anchor_section for _, s in sections)]
        if len(selected) != 1:
            raise ExplorationError("anchor section lacks an unambiguous source layout")
        grouped = {key: grouped[key] for key in selected}
    by_unit = defaultdict(list)
    for row in rows:
        address = row["source"]["canonical_address"]
        if address["record_id"] != record["record_id"]:
            raise ExplorationError("RU belongs to a different record")
        if address["canonical_unit_kind"] == "rich_text":
            by_unit[(address["section_ordinal"], address["canonical_unit_ordinal"])].append(row)
    groups, batches, deferred, dispositions = [], [], [], []
    for key, sections in sorted(grouped.items()):
        ordered_units, graph_context, names = [], [], []
        for _, section in sorted(sections, key=lambda v: (v[0], v[1]["ordinal"])):
            name = section["source_metadata"].get("name", "")
            names.append({"section_ordinal": section["ordinal"], "name": name})
            for context in section["component_contexts"]:
                children = [u for u in section["units"] if u["parent_component_key"] == context["observation_key"]]
                eligible = (not is_quest or context["source_component_id"] == "interactive_dialogue"
                            or (name == "剧情对话" and context["source_component_id"] == "collapse_panel"))
                rich, graphs = _dialogue_order(children)
                graph_context.extend({"section_ordinal": section["ordinal"], **g} for g in graphs)
                for unit in children:
                    if unit["kind"] != "rich_text" or not eligible:
                        dispositions.append({"section_ordinal": section["ordinal"], "unit_ordinal": unit["ordinal"],
                                             "kind": unit["kind"], "reason": "structural_carrier_or_outside_reading_body"})
                if not eligible:
                    continue
                for unit in rich:
                    body = unit["value"].get("normalized_text")
                    if not isinstance(body, str):
                        raise ExplorationError("unknown rich-text representation")
                    address = (section["ordinal"], unit["ordinal"])
                    if not body:
                        dispositions.append({"section_ordinal": address[0], "unit_ordinal": address[1], "reason": "empty_text"})
                        continue
                    fragments = sorted(by_unit[address], key=lambda r: r["fragment_selector"].get("fragment_index", 0))
                    if not fragments or "".join(r["retrieval_visible_text"] for r in fragments) != body:
                        raise ExplorationError("incomplete official RU mapping for Canonical body")
                    occurrence = {
                        "record_id": record["record_id"], "section_ordinal": address[0],
                        "canonical_unit_ordinal": address[1], "parent_component_key": unit["parent_component_key"],
                        "unit_ids": [r["unit_id"] for r in fragments], "text": body,
                        "text_sha256": sha256(body.encode("utf-8")).hexdigest(),
                        "fragments": [{"unit_id": r["unit_id"], "fragment_selector": r["fragment_selector"]}
                                      for r in fragments],
                        "source_identity_key": record["source_identity"]["key"],
                        "section_name": name, "content_role": unit["content_role"], "provenance": unit["provenance"],
                        "links": unit["value"].get("links", []),
                        "entry_references": unit["value"].get("entry_references", []),
                        "lineage": unit["lineage"],
                    }
                    occurrence["coverage_key"] = sha256_json({
                        "snapshot": snapshot_identity, "record_id": record["record_id"],
                        "section": address[0], "unit": address[1],
                    })
                    ordered_units.append(occurrence)
        group_id = f"{record['record_id']}:tab:{key[2]}:group:{key[3]}"
        group = {"group_id": group_id, "layout_order": list(key[:2]), "sections": names,
                 "graph_context": graph_context, "eligible_occurrences": len(ordered_units),
                 "caveat": "source presentation group, not a verified game quest or temporal relation"}
        groups.append(group)
        chunks, current, current_chars = [], [], 0
        for occurrence in ordered_units:
            size = len(occurrence["text"])
            if size > max_batch_chars:
                if current:
                    chunks.append(current)
                    current, current_chars = [], 0
                deferred.append({"group_id": group_id, "coverage_key": occurrence["coverage_key"],
                                 "unit_ids": occurrence["unit_ids"], "reason": "oversized_canonical_unit",
                                 "required_body_chars": size})
                continue
            if current and current_chars + size > max_batch_chars:
                chunks.append(current)
                current, current_chars = [], 0
            current.append(occurrence)
            current_chars += size
        if current:
            chunks.append(current)
        for index, chunk in enumerate(chunks):
            batch = {"group_id": group_id, "part": index, "record_id": record["record_id"],
                     "source_identity_key": record["source_identity"]["key"],
                     "record_title": metadata["name"], "units": chunk,
                     "body_chars": sum(len(o["text"]) for o in chunk), "group_context": group}
            batch["batch_id"] = sha256_json({"snapshot": snapshot_identity, "group": group_id,
                                            "occurrences": [o["coverage_key"] for o in chunk]})
            batches.append(batch)
    return {"record_id": record["record_id"], "is_quest": is_quest, "groups": groups,
            "batches": batches, "deferred": deferred, "issues": issues, "dispositions": dispositions,
            "coverage_scope": "eligible bodies of selected source layout groups; not whole-corpus semantic coverage"}


class VerifiedReadingSources:
    """Reuse E1 snapshot binding, official scoped loader and Canonical projector."""

    def __init__(self, tools: LocalExplorationTools):
        self.tools = tools
        self.identity = tools.identity
        self.by_source = {e["parsed_identity_key"]: e for e in tools.entries}
        if len(self.by_source) != len(tools.entries):
            raise ExplorationError("duplicate source identity in snapshot")
        self._records: dict[str, tuple[Mapping[str, Any], list[Mapping[str, Any]]]] = {}

    def record(self, source_identity_key: str):
        self.tools.verify_snapshot()
        entry = self.by_source.get(source_identity_key)
        if entry is None:
            raise ExplorationError("source absent from bound Canonical manifest")
        path = Path(entry["canonical_record_path"]).resolve()
        if not path.is_relative_to(self.tools.canonical_path.parent.parent):
            raise ExplorationError("Canonical record escapes snapshot")
        if sha256(path.read_bytes()).hexdigest() != entry["canonical_record_sha256"]:
            raise ExplorationError("Canonical record changed")
        if source_identity_key not in self._records:
            record = ru._load_record(entry, self.tools.entries.index(entry))
            if record["source_identity"]["key"] != source_identity_key:
                raise ExplorationError("Canonical source identity mismatch")
            projection = self.tools.ru_manifest["projection_config"]
            config = ru.RetrievalUnitBuildConfig(text_fragment_chars=projection["text_fragment_chars"],
                                                structured_fragment_chars=projection["structured_fragment_chars"])
            projected, _ = ru._validate_record(record, entry, self.tools.entries.index(entry), config,
                                               source=self.tools.canonical["source"], locale=self.tools.canonical["locale"])
            _, rows = ru.load_retrieval_units(self.tools.ru_path, selected_record_ids=[entry["record_id"]])
            if {r["unit_id"]: r for r in rows} != {r["unit_id"]: r for r in projected}:
                raise ExplorationError("RU snapshot differs from Canonical projection")
            self._records[source_identity_key] = (record, rows)
        return deepcopy(self._records[source_identity_key])

    def plan(self, source_identity_key: str, *, max_batch_chars=8000, anchor_section=None):
        record, rows = self.record(source_identity_key)
        return partition_record(record, rows, snapshot_identity=self.identity,
                                max_batch_chars=max_batch_chars, anchor_section=anchor_section)

    def search(self, query: str, *, top_k=5):
        return self.tools.search(query, top_k=top_k)
