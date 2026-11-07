"""One structure-first, token-bounded packer for both retrieval granularities."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Callable

SCHEMA_VERSION = "gc-chunking-pilot-2"


def identity(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChunkProfile:
    name: str = "balanced"
    micro_target: int = 240
    micro_hard: int = 384
    macro_target: int = 720
    macro_hard: int = 1024

    def __post_init__(self):
        if not 0 < self.micro_target <= self.micro_hard < self.macro_target <= self.macro_hard:
            raise ValueError("Expected increasing positive micro/macro token budgets")


def part_text(parts: list[dict]) -> str:
    lines = []
    previous = None
    previous_source_branch = None
    for part in parts:
        prefix = part.get("display_prefix", "")
        key = (part.get("node_id"), prefix)
        source_branch = tuple(tuple(branch) for branch in part.get("branch_context", []) if branch[2] == "source_fork")
        if source_branch and source_branch != previous_source_branch:
            lines.append("【源图分支，发生关系待解释】")
        if key != previous:
            for source in part.get("condition_sources", []):
                if not contains_ref(parts, source["clean_ref"]):
                    lines.append("【来源条件】" + source["text"])
        if prefix and key != previous:
            lines.append(prefix)
        lines.append(part["text"])
        previous = key
        previous_source_branch = source_branch
    return "\n".join(lines)


def context_text(parent: dict, parts=None) -> str:
    # Source-derived labels stay separate from quoted source body.
    labels = list(dict.fromkeys(label for label in parent["labels"] if label))
    header = " / ".join(labels)
    sources = projected_context_sources(parts) if parts is not None else []
    return "\n".join([header, *("【" + s["role"] + "】" + s["text"] for s in sources)]).strip()


def contains_ref(parts: list[dict], ref: dict) -> bool:
    return any(p["clean_ref"]["block_id"] == ref["block_id"]
               and p["clean_ref"]["value_pointer"] == ref["value_pointer"]
               and p["clean_ref"]["char_start"] <= ref["char_start"]
               and p["clean_ref"]["char_end"] >= ref["char_end"] for p in parts)


def projected_context_sources(parts):
    sources = []
    if not parts:
        return sources
    first = parts[0]
    if not first.get("optional_context_deferred"):
        sources += [{**s, "role": "来源语境"} for s in first.get("upstream_context", []) + first.get("option_context", [])
                    if len(s["text"]) <= 140]
    if first.get("article_title"):
        sources.append({**first["article_title"], "role": "文章标题"})
    sources += [{**s, "role": "来源条件"} for p in parts for s in p.get("condition_sources", [])]
    return list({identity([s["clean_ref"], s["role"]]): s for s in sources
                 if not contains_ref(parts, s["clean_ref"])}.values())


def retrieval_text(parent: dict, parts: list[dict], *, include_optional=True) -> str:
    header = context_text(parent)
    contexts = []
    if parts and include_optional and not parts[0].get("optional_context_deferred"):
        contexts = [source for source in parts[0].get("upstream_context", []) + parts[0].get("option_context", [])
                    if len(source["text"]) <= 140 and not contains_ref(parts, source["clean_ref"])]
    contexts = list({identity(source["clean_ref"]): source for source in contexts}.values())
    upstream = "".join("【来源语境】" + source["text"] + "\n" for source in contexts)
    article = parts[0].get("article_title") if parts else None
    article_context = ("【文章标题】" + article["text"] + "\n"
                       if article and not contains_ref(parts, article["clean_ref"]) else "")
    continuation = ("【节点内续片】\n" if parts and parts[0].get("node_id") and
                    (parts[0].get("node_part_order", 0) > 0 or parts[0]["clean_ref"]["char_start"] > 0) else "")
    return (header + "\n" if header else "") + upstream + article_context + continuation + part_text(parts)


def scope_key(part):
    """Empty labels remain an explicit scope, with no unconditional meaning."""
    return (tuple(sorted(s["condition_id"] for s in part.get("condition_sources", []))),
            part.get("condition_status", "not_established"), part.get("source_component_id"))


def _slice_part(part: dict, start: int, end: int, reason: str) -> dict:
    result = {**part, "text": part["text"][start:end], "split_reason": reason}
    ref = dict(part["clean_ref"])
    ref["char_start"] += start
    ref["char_end"] = part["clean_ref"]["char_start"] + end
    result["clean_ref"] = ref
    result["alias_clean_refs"] = []
    for alias in part.get("alias_clean_refs", []):
        alias_ref = dict(alias)
        alias_ref["char_start"] += start
        alias_ref["char_end"] = alias["char_start"] + end
        result["alias_clean_refs"].append(alias_ref)
    return result


def split_long(part: dict, fits: Callable[[list[dict]], bool]) -> list[dict]:
    """Split only an oversized source paragraph. Never decode token slices."""
    if fits([part]):
        return [part]
    text = part["text"]
    sentence_ends = [match.end() for match in re.finditer(r'[。！？!?]+[”’」』】）)]*|\n+', text)]
    result = []
    start = 0
    while start < len(text):
        low, high = start + 1, len(text)
        best = start
        while low <= high:
            middle = (low + high) // 2
            candidate = _slice_part(part, start, middle, "hard_range")
            if fits([candidate]):
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        if best == start:
            raise ValueError("Context plus one code point exceeds token budget")
        # Only the last fitting sentence end is used. Search from the end,
        # preserving the previous choice even for non-monotonic token counts.
        boundary = next((end for end in reversed(sentence_ends) if start < end <= best
                         and fits([_slice_part(part, start, end, "sentence")])), None)
        end = boundary if boundary is not None else best
        reason = "sentence" if boundary is not None else "hard_range"
        result.append(_slice_part(part, start, end, reason))
        start = end
    return result


def pack(parent: dict, count: Callable[[str], int], target: int, hard: int,
         secondary_fits: Callable[[str], bool] | None = None) -> list[list[dict]]:
    def fits(parts):
        text = retrieval_text(parent, parts, include_optional=False)
        return count(text) <= hard and (secondary_fits is None or secondary_fits(text))

    units = []
    for unit in parent["units"]:
        # Protect a complete node, table row or paragraph while it fits.
        if fits(unit["parts"]):
            units.append(unit)
        else:
            first_fragment = True
            for part in unit["parts"]:
                for fragment in split_long(part, fits):
                    units.append({**unit, "parts": [fragment], "split_unit": True,
                                  "hard_boundary_before": first_fragment and unit.get("hard_boundary_before", False),
                                  "heading_start": part.get("source_kind") == "heading"})
                    first_fragment = False
    chunks = []
    current = []
    current_hard_boundary = False
    for unit in units:
        parts = unit["parts"]
        boundary = unit.get("boundary_before", False)
        scope_groups = {scope_key(part) for part in current + parts}
        different_conditions = len(scope_groups) > 1
        different_articles = len({part.get("article_scope_id") for part in current + parts}) > 1
        heading_start = unit.get("heading_start", False) and current and current[-1].get("source_kind") != "heading"
        if current and (not fits(current + parts) or
                        different_conditions or different_articles or heading_start or unit.get("hard_boundary_before", False) or
                        (boundary and count(retrieval_text(parent, current, include_optional=False)) >= target)):
            chunks.append(current)
            current = []
        if not current:
            current_hard_boundary = unit.get("hard_boundary_before", False)
        current += parts
        if count(retrieval_text(parent, current, include_optional=False)) >= target:
            chunks.append(current)
            current = []
    if current:
        # A small tail can join the preceding pack if the hard limit permits.
        scopes = {scope_key(part) for part in (chunks[-1] if chunks else []) + current}
        starts_new_heading = (current[0].get("node_has_heading") and chunks and
                              chunks[-1][-1].get("node_id") != current[0].get("node_id"))
        starts_new_heading = starts_new_heading or current[0].get("source_kind") == "heading"
        article_scopes = {part.get("article_scope_id") for part in (chunks[-1] if chunks else []) + current}
        if chunks and not current_hard_boundary and len(scopes) <= 1 and len(article_scopes) <= 1 and not starts_new_heading and count(retrieval_text(parent, current, include_optional=False)) < target // 3 and fits(chunks[-1] + current):
            chunks[-1] += current
        else:
            chunks.append(current)
    result = []
    for parts in chunks:
        text = retrieval_text(parent, parts)
        if count(text) > hard or secondary_fits is not None and not secondary_fits(text):
            parts = [{**parts[0], "optional_context_deferred": True}, *parts[1:]]
        result.append(parts)
    return result


def chunk_parent(parent: dict, count: Callable[[str], int],
                 profile: ChunkProfile = ChunkProfile(), *,
                 secondary_fits: Callable[[str], bool] | None = None) -> list[dict]:
    if not parent["units"]:
        return []
    micro = pack(parent, count, profile.micro_target, profile.micro_hard, secondary_fits)
    macro = pack(parent, count, profile.macro_target, profile.macro_hard, secondary_fits)
    by_coverage = {}
    for level, packs in (("micro", micro), ("macro", macro)):
        for order, parts in enumerate(packs):
            coverage = [part["clean_ref"] for part in parts]
            visible = retrieval_text(parent, parts)
            key = identity([coverage, visible])
            if key in by_coverage:
                by_coverage[key]["granularities"].append(level)
                by_coverage[key]["display_order_by_granularity"][level] = order
                continue
            chunk = {
                "schema_version": SCHEMA_VERSION,
                "chunk_id": identity([SCHEMA_VERSION, parent["parent_id"], profile.__dict__,
                                      coverage, visible, [part.get("alias_clean_refs", []) for part in parts]]),
                "parent_id": parent["parent_id"], "observation_id": parent["observation_id"],
                "content_id": parent["content_id"], "section_id": parent["section_id"],
                "component_id": parent["component_id"], "granularities": [level],
                "order": order, "parts": parts, "clean_refs": coverage,
                "display_order_by_granularity": {level: order}, "order_semantics": "display_only",
                "alias_clean_refs": [ref for part in parts for ref in part.get("alias_clean_refs", [])],
                "context": parent["context"], "context_text": context_text(parent, parts),
                "context_sources": projected_context_sources(parts),
                "projection_roles": {"body_text": "quotable_source", "context_text": "source_context",
                                     "parts.display_prefix": "derived_structural_label",
                                     "parts.branch_context": "source_graph_structure",
                                     "order_and_graph_ids": "structure_only"},
                "body_text": "\n".join(part["text"] for part in parts), "retrieval_text": visible,
                "tokens": count(visible), "adapter": parent["adapter"],
                "article_scope_ids": list(dict.fromkeys(part["article_scope_id"] for part in parts if part.get("article_scope_id"))),
                "local_scope_ids": list(dict.fromkeys(identity([parent["parent_id"],
                    part.get("node_id", part.get("table_row", "parent"))]) for part in parts)),
            }
            by_coverage[key] = chunk
    result = list(by_coverage.values())
    by_value = defaultdict(list)
    overlaps = defaultdict(set)
    for chunk in result:
        for ref in chunk["clean_refs"]:
            key = (ref["block_id"], ref["value_pointer"])
            for other, start, end in by_value[key]:
                if ref["char_start"] < end and start < ref["char_end"] and other != chunk["chunk_id"]:
                    overlaps[other].add(chunk["chunk_id"])
                    overlaps[chunk["chunk_id"]].add(other)
            by_value[key].append((chunk["chunk_id"], ref["char_start"], ref["char_end"]))
    for chunk in result:
        chunk["overlapping_chunk_ids"] = sorted(overlaps[chunk["chunk_id"]])
    return result
