"""One structure-first, token-bounded packer for both retrieval granularities."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Callable


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
    for part in parts:
        prefix = part.get("display_prefix", "")
        if prefix and prefix != previous:
            lines.append(prefix)
        lines.append(part["text"])
        previous = prefix
    return "\n".join(lines)


def context_text(parent: dict) -> str:
    # Source-derived labels stay separate from quoted source body.
    labels = list(dict.fromkeys(label for label in parent["labels"] if label))
    return " / ".join(labels)


def retrieval_text(parent: dict, parts: list[dict]) -> str:
    header = context_text(parent)
    contexts = []
    owned_blocks = {part["clean_ref"]["block_id"] for part in parts}
    if parts:
        contexts = [source for source in parts[0].get("upstream_context", [])
                    if source["clean_ref"]["block_id"] not in owned_blocks]
    upstream = "".join("【上接原文】" + source["text"] + "\n" for source in contexts)
    article = parts[0].get("article_title") if parts else None
    article_context = ("【文章标题】" + article["text"] + "\n"
                       if article and article["clean_ref"]["block_id"] not in owned_blocks else "")
    return (header + "\n" if header else "") + upstream + article_context + part_text(parts)


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
        boundaries = [end for end in sentence_ends if start < end <= best
                      and fits([_slice_part(part, start, end, "sentence")])]
        end = boundaries[-1] if boundaries else best
        reason = "sentence" if boundaries else "hard_range"
        result.append(_slice_part(part, start, end, reason))
        start = end
    return result


def pack(parent: dict, count: Callable[[str], int], target: int, hard: int,
         secondary_fits: Callable[[str], bool] | None = None) -> list[list[dict]]:
    def fits(parts):
        text = retrieval_text(parent, parts)
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
    for unit in units:
        parts = unit["parts"]
        boundary = unit.get("boundary_before", False)
        scope_groups = {frozenset(source["source_block_id"] for source in part.get("condition_sources", []))
                        for part in current + parts if part.get("condition_sources")}
        different_conditions = len(scope_groups) > 1
        different_articles = len({part.get("article_scope_id") for part in current + parts}) > 1
        heading_start = unit.get("heading_start", False) and current and current[-1].get("source_kind") != "heading"
        if current and (not fits(current + parts) or
                        different_conditions or different_articles or heading_start or unit.get("hard_boundary_before", False) or
                        (boundary and count(retrieval_text(parent, current)) >= target)):
            chunks.append(current)
            current = []
        current += parts
        if count(retrieval_text(parent, current)) >= target:
            chunks.append(current)
            current = []
    if current:
        # A small tail can join the preceding pack if the hard limit permits.
        scopes = {frozenset(source["source_block_id"] for source in part.get("condition_sources", []))
                  for part in (chunks[-1] if chunks else []) + current if part.get("condition_sources")}
        starts_new_heading = (current[0].get("node_has_heading") and chunks and
                              chunks[-1][-1].get("node_id") != current[0].get("node_id"))
        starts_new_heading = starts_new_heading or current[0].get("source_kind") == "heading"
        article_scopes = {part.get("article_scope_id") for part in (chunks[-1] if chunks else []) + current}
        if chunks and len(scopes) <= 1 and len(article_scopes) <= 1 and not starts_new_heading and count(retrieval_text(parent, current)) < target // 3 and fits(chunks[-1] + current):
            chunks[-1] += current
        else:
            chunks.append(current)
    return chunks


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
            key = identity(coverage)
            if key in by_coverage:
                by_coverage[key]["granularities"].append(level)
                continue
            visible = retrieval_text(parent, parts)
            chunk = {
                "chunk_id": identity(["gc-chunking-pilot-1", parent["parent_id"], profile.__dict__,
                                      coverage, visible, [part.get("alias_clean_refs", []) for part in parts]]),
                "parent_id": parent["parent_id"], "observation_id": parent["observation_id"],
                "content_id": parent["content_id"], "section_id": parent["section_id"],
                "component_id": parent["component_id"], "granularities": [level],
                "order": order, "parts": parts, "clean_refs": coverage,
                "alias_clean_refs": [ref for part in parts for ref in part.get("alias_clean_refs", [])],
                "context": parent["context"], "context_text": context_text(parent),
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
