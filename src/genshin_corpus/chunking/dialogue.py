"""Source graph observations and bounded local labels, without a task state model."""
from collections import defaultdict
from dataclasses import asdict
import heapq
import re

from ..clean import CleanRef
from .core import identity


def label(value):
    if isinstance(value, dict):
        if "text" in value:
            return value["text"]
        return label(value.get("value", value.get("children", [])))
    if isinstance(value, list):
        return "\n".join(text for child in value if (text := label(child)))
    return ""


def text_sources(value, observation_id):
    """Yield owned source values, including exact locators for graph annotations."""
    if isinstance(value, list):
        for child in value:
            yield from text_sources(child, observation_id)
    elif isinstance(value, dict):
        if value.get("kind") in {"paragraph", "heading", "scalar"}:
            source = value["value"]
            text = source.get("text", source.get("lexeme", ""))
            yield {"text": text, "clean_ref": asdict(CleanRef(
                observation_id, value["block_id"], "/value", 0, len(text))),
                "relation_ids": [mark["relation_id"] for mark in source.get("marks", []) if "relation_id" in mark]}
        else:
            for key, child in value.items():
                if key != "context":
                    yield from text_sources(child, observation_id)


def graph_order(group):
    """Stable display order only. Source edges do not establish an event timeline."""
    nodes = {node["source_id"]: node for node in group["nodes"]}
    children = {key: [] for key in nodes}
    incoming = {key: [] for key in nodes}
    issues = []
    for edge in group["edges"]:
        a, b = edge["parent_id"], edge["child_id"]
        if a not in nodes or b not in nodes:
            issues.append("dangling_edge")
            continue
        children[a].append(b)
        incoming[b].append(a)
    rank = {}
    seeds = ([group["root_id"]] if group.get("root_id") in nodes else []) + list(nodes)
    for seed in seeds:
        stack = [seed]
        while stack:
            key = stack.pop()
            if key in rank:
                continue
            rank[key] = len(rank)
            stack.extend(reversed(children[key]))
    remaining = {key: len(value) for key, value in incoming.items()}
    ready = [(rank[key], key) for key, degree in remaining.items() if degree == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        _, key = heapq.heappop(ready)
        order.append(key)
        for child in children[key]:
            remaining[child] -= 1
            if remaining[child] == 0:
                heapq.heappush(ready, (rank[child], child))
    if len(order) != len(nodes):
        issues.append("cycle_unsequenced")
        ordered = set(order)
        order.extend(key for key in nodes if key not in ordered)
    if nodes and group.get("root_id") not in nodes:
        issues.append("missing_root")
    return order, children, incoming, sorted(set(issues))


def condition_annotations(node, observation_id):
    guards, notes = [], []
    for field in node.get("fields", []):
        suffix = field["field_key"].rsplit("/", 1)[-1]
        if suffix not in {"option", "dialogue"}:
            continue
        for source in text_sources(field["value"], observation_id):
            original = source["text"]
            text = original.strip()
            offset = len(original) - len(original.lstrip())
            if not text:
                continue
            if suffix == "dialogue":
                if re.fullmatch(r"[（(].*(?:该对话|触发|任务期间|任务完成).*[）)]", text, re.S):
                    notes.append({**source, "role": "unresolved_source_note"})
                continue
            tail = re.match(r"^(?:【[^\n]+?】|（[^\n]+?）)\s*——\s*(.+)$", text, re.S)
            if tail:
                offset += tail.start(1)
                text = tail[1]
                if not re.search(r"完成|持有|结算|首次|非首次|白天|夜晚|期间|次日", text):
                    notes.append({**source, "role": "source_action_note"})
                    continue
                known = True
            else:
                # Restrict matching to source annotations. Spoken prose can
                # contain the same words and must remain ordinary source text.
                text = text.lstrip("（(【").rstrip("）)】")
                offset = original.find(text)
                known = bool(re.match(r"^(?:未完成|尚未完成|已完成|完成).*(?:前|后|触发|期间|时|开场)", text)
                    or re.match(r"^(?:若此前|若已完成|如果没有|首次接取|再次接取|第.+次接取|接取).*(?:触发|期间|开场)", text)
                    or text in {"白天", "夜晚"})
            if not known:
                if not (re.match(r"^[^：:\n]{0,65}(?:触发|任务期间|完成.+前|完成.+后)$", text)
                        or re.match(r"^(?:触发条件|前置条件|条件)[：:]", text)):
                    continue
            ref = {**source["clean_ref"], "char_start": offset, "char_end": offset + len(text)}
            composite = bool(re.search(r"且|或|同时|与【|与第|并且", text))
            guards.append({"condition_id": identity([ref, "source_condition"]), "text": text,
                "source_block_id": ref["block_id"], "clean_ref": ref,
                "interpretation": "source_literal" if known and not composite else "unresolved_literal"})
    return guards, notes


def analyze_graph(group, observation_id):
    """Keep all source occurrences, classify reachability and label local paths."""
    order, children, incoming, issues = graph_order(group)
    nodes = {node["source_id"]: node for node in group["nodes"]}
    reachable = set()
    stack = [group.get("root_id")]
    while stack:
        key = stack.pop()
        if key in reachable or key not in nodes:
            continue
        reachable.add(key)
        stack.extend(children[key])
    components = {}
    for seed in order:
        if seed in components:
            continue
        stack = [seed]
        while stack:
            key = stack.pop()
            if key in components:
                continue
            components[key] = seed
            stack.extend(children[key] + incoming[key])
    annotations = {key: condition_annotations(node, observation_id) for key, node in nodes.items()}
    options = {key: next((label(f["value"]) for f in node.get("fields", [])
                         if f["field_key"].endswith("/option")), "") for key, node in nodes.items()}
    choices = {key for key in order if len(children[key]) > 1 and all(
        not annotations[child][0] and bool(re.fullmatch(r"旅行者[：:][^\n]+|【[^\n]+】(?:——[^\n]+)?", options[child]))
        for child in children[key])}
    signatures = {key: tuple(sorted((f["field_key"].rsplit("/", 1)[-1], label(f["value"]))
                  for f in node.get("fields", []) if f["field_key"].rsplit("/", 1)[-1] in {"option", "dialogue"}))
                  for key, node in nodes.items()}
    signature_nodes = defaultdict(list)
    for key in order:
        if key in reachable:
            signature_nodes[signatures[key]].append(key)
    derived = {}
    for display_order, key in enumerate(order):
        node = nodes[key]
        predecessor_guards = [derived[p]["condition_sources"] for p in incoming[key] if p in derived]
        predecessor_branches = []
        if "cycle_unsequenced" in issues:
            predecessor_guards = []
        common = {g["condition_id"]: g for g in predecessor_guards[0]} if predecessor_guards else {}
        for guards in predecessor_guards[1:]:
            ids = {g["condition_id"] for g in guards}
            common = {k: v for k, v in common.items() if k in ids}
        direct, notes = annotations[key]
        guards = [{**g, "basis": "inherited"} for g in common.values()]
        guards += [{**g, "basis": "direct"} for g in direct]
        for predecessor in incoming[key]:
            branches = set(derived.get(predecessor, {}).get("branch_context", []))
            if len(children[predecessor]) > 1:
                branches.add((predecessor, key, "choice" if predecessor in choices else "source_fork"))
            predecessor_branches.append(branches)
        common_branches = set.intersection(*predecessor_branches) if predecessor_branches else set()
        branch_inputs = defaultdict(set)
        for branches in predecessor_branches:
            for fork, entry, kind in branches:
                branch_inputs[(fork, kind)].add(entry)
        joins = [{"fork_id": fork, "kind": kind} for (fork, kind), entries in sorted(branch_inputs.items()) if len(entries) > 1]
        classification = "reachable"
        if key not in reachable:
            if group.get("root_id") not in nodes:
                classification = "declared_root_missing"
            elif not children[key] and not incoming[key]:
                classification = "isolated_in_valid_root_group"
            else:
                classification = "unreachable_connected_region"
        reasons = []
        if classification != "reachable":
            reasons.append(classification)
        if "cycle_unsequenced" in issues:
            reasons.append("cycle_unsequenced")
        if any(g["interpretation"] == "unresolved_literal" for g in guards) or any(
                n["role"] == "unresolved_source_note" for n in notes):
            reasons.append("unresolved_source_annotation")
        # Unknown annotations on a predecessor stay unknown downstream.
        if any(derived.get(p, {}).get("condition_status") == "unresolved" for p in incoming[key]):
            reasons.append("unresolved_incoming_context")
        status = "unresolved" if reasons else "source_marked" if guards else "not_established"
        derived[key] = {"display_order": display_order, "source_order": node["ordering"],
            "order_semantics": "display_only", "incoming_node_ids": incoming[key], "outgoing_node_ids": children[key],
            "source_component_id": components[key], "reachability": classification,
            "node_origin_id": node.get("origin_id"), "condition_status": status, "condition_reasons": reasons,
            "condition_sources": guards, "source_annotations": notes,
            "incoming_condition_sources": [{"node_id": p, "sources": derived.get(p, {}).get("condition_sources", [])}
                                           for p in incoming[key]],
            "branch_context": sorted(common_branches), "branch_joins": joins, "choice_point": key in choices,
            "source_fork": len(children[key]) > 1, "has_text": any(t.strip() for _, t in signatures[key]),
            "same_text_as_reachable_node_ids": signature_nodes[signatures[key]] if any(t.strip() for _, t in signatures[key]) and key not in reachable else [],
            "field_clean_refs": [s["clean_ref"] for f in node.get("fields", [])
                                 if f["field_key"].rsplit("/", 1)[-1] in {"option", "dialogue"}
                                 for s in text_sources(f["value"], observation_id)]}
    return {"display_order": order, "issues": issues, "derived_nodes": derived}
