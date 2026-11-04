"""Conservative, source-range-bound display projection of structured RUs.

Only paired layout tags with understood attributes are simplified. The original
RU, scalar selectors, character data, entities and unknown markup stay intact.
"""

from __future__ import annotations

from hashlib import sha256
from html.parser import HTMLParser
from bisect import bisect_right
import re
from collections.abc import Mapping, Sequence
from typing import Any

from genshin_corpus.parser.rich_text import _VOID_TAGS


PROJECTION_VERSION = "phase04-structured-layout-display-1"
_LAYOUT_TAGS = {"p", "br", "span", "strong", "em", "b", "i"}
_LAYOUT_PROPERTIES = {
    "white-space", "text-align", "min-height", "line-height", "font-size",
    "font-weight", "font-family", "font-style", "margin", "margin-top",
    "margin-bottom", "padding", "text-indent", "vertical-align",
    "letter-spacing", "word-spacing",
}


def _layout_attributes(attrs: list[tuple[str, str | None]]) -> bool:
    if not attrs:
        return True
    if len(attrs) != 1 or attrs[0][0] != "style" or attrs[0][1] is None:
        return False
    for declaration in attrs[0][1].split(";"):
        if not declaration.strip():
            continue
        name, colon, value = declaration.partition(":")
        if not colon or name.strip().lower() not in _LAYOUT_PROPERTIES or not value.strip():
            return False
        if any(token in value.lower() for token in ("url(", "var(", "/*", "\\")):
            return False
    return True


class _LayoutParser(HTMLParser):
    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=False)
        self.text = text
        self.line_starts = [0] + [match.end() for match in re.finditer("\n", text)]
        self.stack: list[tuple[str, bool, tuple[int, int, str]]] = []
        self.edits: list[tuple[int, int, str]] = []
        self.uncertain = False

    def _span(self, raw: str, tag: str) -> tuple[int, int, str]:
        line, column = self.getpos()
        start = self.line_starts[line - 1] + column
        if self.text[start:start + len(raw)] != raw:
            raise ValueError("HTML token offset mismatch")
        return start, start + len(raw), "\n" if tag in {"p", "br"} else ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        span = self._span(self.get_starttag_text() or "", tag)
        editable = tag in _LAYOUT_TAGS and _layout_attributes(attrs)
        if tag in _VOID_TAGS:
            if editable:
                self.edits.append(span)
        else:
            self.stack.append((tag, editable, span))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _LAYOUT_TAGS and _layout_attributes(attrs):
            self.edits.append(self._span(self.get_starttag_text() or "", tag))

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1][0] != tag:
            self.uncertain = True
            return
        _, editable, start_span = self.stack.pop()
        if editable:
            line, column = self.getpos()
            offset = self.line_starts[line - 1] + column
            match = re.match(r"</" + re.escape(tag) + r"\s*>", self.text[offset:], re.I)
            if match is None:
                self.uncertain = True
                return
            self.edits.extend((start_span, self._span(match[0], tag)))


def _local_edits(scalar: dict, original: str, offset: int) -> list[dict]:
    edits = []
    tokens = scalar["tokens"]
    first = bisect_right(scalar["token_ends"], offset)
    for index in range(first, len(tokens)):
        start, end, replacement = tokens[index]
        if start >= offset + len(original):
            break
        local_start, local_end = max(0, start - offset), min(len(original), end - offset)
        if local_start < local_end:
            edits.append({"start": local_start, "end": local_end,
                          "replacement": replacement if start >= offset else "",
                          "source_text": original[local_start:local_end],
                          "decoded_json_pointer": scalar["pointer"],
                          "scalar_token_range": [start, end], "scalar_token": scalar["text"][start:end],
                          "scalar_context": scalar["context_index"]})
    return edits


def _scalar_ranges(unit: Mapping[str, Any], chain: Sequence[Mapping[str, Any]]) -> list[dict]:
    text = unit["retrieval_visible_text"]
    nested, fragment = unit["nested_selector"], unit["fragment_selector"]
    if fragment["kind"] == "packed_scalar_lines":
        pointers = nested["decoded_json_pointers"]
        starts = [0]
        for pointer in pointers[1:]:
            hits = list(re.finditer("\n" + re.escape(pointer) + "\t", text[starts[-1]:]))
            if len(hits) != 1:
                raise ValueError("ambiguous scalar line boundary")
            starts.append(starts[-1] + hits[0].start() + 1)
        ranges = []
        for pointer, start, end in zip(pointers, starts, starts[1:] + [len(text) + 1]):
            prefix = pointer + "\t"
            if not text.startswith(prefix, start):
                raise ValueError("scalar pointer mismatch")
            value_start, value_end = start + len(prefix), end - 1
            ranges.append({"text": text[value_start:value_end], "pointer": pointer,
                           "offset": -value_start, "unit_ids": [unit["unit_id"]]})
        return ranges
    if fragment["kind"] != "oversized_scalar_line_range":
        raise ValueError("unsupported structured fragment")
    ordered = sorted(chain, key=lambda row: row["fragment_selector"]["fragment_index"])
    if len(ordered) != fragment["fragment_count"]:
        raise ValueError("incomplete scalar fragment chain")
    expected_start = 0
    for index, row in enumerate(ordered):
        selector = row["fragment_selector"]
        if (row["source"]["canonical_address"] != unit["source"]["canonical_address"]
                or row["nested_selector"] != nested or selector["kind"] != fragment["kind"]
                or selector["fragment_count"] != len(ordered) or selector["fragment_index"] != index
                or selector["char_start"] != expected_start
                or selector["char_end"] - expected_start != len(row["retrieval_visible_text"])):
            raise ValueError("inconsistent scalar fragment chain")
        expected_start = selector["char_end"]
    if not any(row["unit_id"] == unit["unit_id"] and row == unit for row in ordered):
        raise ValueError("scalar chain lacks requested RU")
    joined = "".join(row["retrieval_visible_text"] for row in ordered)
    prefix = nested["decoded_json_pointer"] + "\t"
    if not joined.startswith(prefix):
        raise ValueError("scalar prefix mismatch")
    return [{"text": joined[len(prefix):], "pointer": nested["decoded_json_pointer"],
             "offset": fragment["char_start"] - len(prefix),
             "unit_ids": [row["unit_id"] for row in ordered]}]


def project_structured_unit(
    unit: Mapping[str, Any], *, fragment_chain: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Return display text and replayable RU-local edits, preserving uncertainty."""
    original = unit["retrieval_visible_text"]
    result = {"version": PROJECTION_VERSION, "text": original, "edits": [],
              "source_sha256": sha256(original.encode("utf-8")).hexdigest(), "status": "identity"}
    if unit["content_type"] != "structured":
        return result
    try:
        ranges = _scalar_ranges(unit, fragment_chain)
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        result.update(status="preserved_uncertain", reason=str(exc))
        return result
    edits, contexts = [], []
    for context_index, scalar in enumerate(ranges):
        parser = _LayoutParser(scalar["text"])
        try:
            parser.feed(scalar["text"])
            parser.close()
        except (ValueError, AssertionError):
            result.update(status="preserved_uncertain", reason="HTML parsing uncertainty")
            return result
        if parser.uncertain or parser.stack:
            result.update(status="preserved_uncertain", reason="unbalanced HTML preserved")
            return result
        scalar.update(tokens=sorted(parser.edits), context_index=context_index)
        scalar["token_ends"] = [end for _, end, _ in scalar["tokens"]]
        contexts.append({"decoded_json_pointer": scalar["pointer"], "unit_ids": scalar["unit_ids"],
                         "scalar_sha256": sha256(scalar["text"].encode("utf-8")).hexdigest()})
        if unit["fragment_selector"]["kind"] == "oversized_scalar_line_range":
            # Keep the scalar unchanged if any RU would become empty. Partial
            # fallback would restore only one piece of a cross-boundary tag.
            prefix_size = len(scalar["pointer"]) + 1
            for fragment in fragment_chain:
                raw = fragment["retrieval_visible_text"]
                offset = fragment["fragment_selector"]["char_start"] - prefix_size
                local = _local_edits(scalar, raw, offset)
                if not apply_display_edits(raw, local).strip():
                    result.update(status="preserved_uncertain", reason="empty fragment preserves scalar chain")
                    return result
        # A cross-RU tag emits a separator only in the RU containing its start.
        edits.extend(_local_edits(scalar, original, scalar["offset"]))
    edits.sort(key=lambda edit: edit["start"])
    display = apply_display_edits(original, edits)
    if not display.strip():
        result.update(status="preserved_uncertain", reason="empty display preserved")
        return result
    result.update(text=display, edits=edits, scalar_contexts=contexts, status="projected" if edits else "identity")
    return result


def apply_display_edits(original: str, edits: Sequence[Mapping[str, Any]]) -> str:
    """Validate and replay the exact local source intervals used by a projection."""
    parts, cursor = [], 0
    for edit in edits:
        start, end = edit["start"], edit["end"]
        if (not 0 <= cursor <= start < end <= len(original)
                or original[start:end] != edit["source_text"] or edit["replacement"] not in {"", "\n"}):
            raise ValueError("invalid display projection source range")
        parts.extend((original[cursor:start], edit["replacement"]))
        cursor = end
    parts.append(original[cursor:])
    return "".join(parts)
