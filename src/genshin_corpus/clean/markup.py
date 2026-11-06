"""Structure-preserving HTML projection over the existing deterministic parser."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from ..parser.rich_text import RichTextNode, _TreeParser
from .context import RecordContext

_HTML_TAGS = set("""a abbr address article aside audio b blockquote body br caption
code col colgroup dd del details div dl dt em embed figcaption figure footer form
h1 h2 h3 h4 h5 h6 head header hr html i iframe img input ins label li link main mark
meta nav ol p param pre s script section small source span strike strong style sub
summary sup table tbody td tfoot th thead time title tr track u ul video wbr""".split())
_BLOCK_TAGS = set("p h1 h2 h3 h4 h5 h6 ul ol blockquote table hr pre details dl dt dd figure figcaption caption".split())
_MARKS = {"b": "strong", "strong": "strong", "i": "emphasis", "em": "emphasis",
          "u": "underline", "s": "strikethrough", "del": "strikethrough",
          "strike": "strikethrough", "sup": "superscript", "sub": "subscript",
          "code": "code", "mark": "highlight"}


class CleanTreeParser(_TreeParser):
    """Keep comments and unrecognized literal tags addressable as source nodes."""

    def __init__(self, markup: str):
        super().__init__()
        self.original_markup = markup
        self.line_starts = [0] + [match.end() for match in re.finditer("\n", markup)]

    def handle_starttag(self, tag, attrs):
        if tag not in _HTML_TAGS:
            self._append(RichTextNode("#literal", text=self.get_starttag_text()))
        else:
            super().handle_starttag(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        if tag not in _HTML_TAGS:
            self._append(RichTextNode("#literal", text=self.get_starttag_text()))
        else:
            super().handle_startendtag(tag, attrs)

    def handle_endtag(self, tag):
        if tag not in _HTML_TAGS:
            line, column = self.getpos()
            start = self.line_starts[line - 1] + column
            end = self.original_markup.find(">", start)
            self._append(RichTextNode("#literal", text=self.original_markup[start:end + 1]))
        else:
            super().handle_endtag(tag)

    def handle_comment(self, data):
        self._append(RichTextNode("#comment", text=data))

    def handle_decl(self, decl):
        self._append(RichTextNode("#declaration", text=decl))

    def handle_pi(self, data):
        self._append(RichTextNode("#instruction", text=data))


def parse_tree(markup: str) -> RichTextNode:
    parser = CleanTreeParser(markup)
    parser.feed(markup)
    parser.close()
    return parser.root


def tree_node(root: RichTextNode, path: list[int] | tuple[int, ...]) -> RichTextNode:
    for index in path:
        root = root.children[index]
    return root


def content_target(href: str) -> str | None:
    try:
        parts = urlsplit(href)
    except ValueError:
        return None
    if parts.scheme or parts.netloc:
        return None
    match = re.fullmatch(r"/ys/obc/content/([0-9]+)/detail", parts.path)
    return match.group(1) if match else None


class MarkupProjector:
    def __init__(self, context: RecordContext, origin_id: str, markup: str):
        self.ctx = context
        self.origin_id = origin_id
        self.root = parse_tree(markup)
        self._metadata: dict[tuple[int, ...], dict[str, Any]] = {}

    def origin(self, path: tuple[int, ...], op: str = "html_node", **details) -> str:
        return self.ctx.derive(self.origin_id, {"op": op, "node_path": list(path), **details},
                               precision="text_node" if op == "html_text" else "value")

    def project(self) -> list[dict[str, Any]]:
        return self._children(self.root, ())

    def _node_metadata(self, node: RichTextNode, path: tuple[int, ...]) -> dict[str, Any]:
        if path in self._metadata:
            return self._metadata[path]
        attrs = dict(node.attrs)
        result: dict[str, Any] = {"marks": [], "media_ids": [], "relation_ids": []}
        self._metadata[path] = result
        origin = self.origin(path)
        if attrs.get("href") is not None or attrs.get("data-entry-id"):
            target_id = attrs.get("data-entry-id") or content_target(attrs.get("href") or "")
            href = attrs.get("data-entry-link", attrs.get("href"))
            relation = self.ctx.relation(
                origin, kind="entry_reference" if target_id else "hyperlink",
                target_content_id=target_id, href=href,
                source_attributes=[{"name": key, "value": val,
                                    "origin_id": self.origin(path, "html_attr", name=key)}
                                   for key, val in node.attrs
                                   if key.startswith("data-entry-") or key == "href"],
            )
            result["relation_ids"].append(relation)
            result["marks"].append({"kind": "link", "relation_id": relation})
            href_id = content_target(href or "")
            if target_id and href_id and str(target_id) != href_id:
                self.ctx.diagnostic("ENTRY_TARGET_CONFLICT", origin)
        resource_attributes = {"data-image-url": "image", "data-entry-img": "image",
                               "data-video-url": "video", "data-video-poster": "image",
                               "poster": "image"}
        if node.tag in {"img", "audio", "video", "iframe", "source", "embed"}:
            kind = {"img": "image", "iframe": "iframe", "embed": "embed"}.get(node.tag, node.tag)
            if node.tag == "source":
                parent = self.root
                for index in path[:-1]:
                    parent = parent.children[index]
                    if parent.tag in {"audio", "video"}:
                        kind = parent.tag
            resource_attributes["src"] = kind
        for name, kind in resource_attributes.items():
            if attrs.get(name):
                media = self.ctx.resource(
                    kind, attrs[name], self.origin(path, "html_attr", name=name),
                    owner_origin_id=origin, source_attribute=name,
                    labels=[self.ctx.text(attrs[key], self.origin(path, "html_attr", name=key))
                            for key in ("alt", "title") if attrs.get(key) is not None],
                )
                result["media_ids"].append(media)
        if attrs.get("style"):
            result["marks"].append({"kind": "presentation", "style": attrs["style"],
                                    "origin_id": self.origin(path, "html_attr", name="style")})
            style = attrs["style"].lower()
            for expression, kind in (
                (r"font-weight\s*:\s*(?:bold|[7-9]00)", "strong"),
                (r"font-style\s*:\s*italic", "emphasis"),
                (r"text-decoration(?:-line)?\s*:[^;]*line-through", "strikethrough"),
                (r"text-decoration(?:-line)?\s*:[^;]*underline", "underline"),
            ):
                if re.search(expression, style):
                    result["marks"].append({"kind": kind, "origin_id": self.origin(path, "html_attr", name="style")})
        handled = set(resource_attributes) | {"href", "style", "class", "alt", "title"}
        for key, value in node.attrs:
            if key.startswith("data-entry-"):
                continue
            if key == "class":
                self.ctx.counts["technical_html_attributes"] += 1
            elif key not in handled:
                self.ctx.extensions.append({"kind": "html_attribute", "name": key, "value": value,
                                            "origin_id": self.origin(path, "html_attr", name=key)})
        # alt/title also carry content on nodes which are not media.
        if not result["media_ids"]:
            for key in ("alt", "title"):
                if key in attrs:
                    self.ctx.extensions.append({"kind": "html_attribute", "name": key,
                                                "value": attrs[key],
                                                "origin_id": self.origin(path, "html_attr", name=key)})
        return result

    def _inline(self, entries: list[tuple[RichTextNode, tuple[int, ...]]], origin: str) -> dict[str, Any]:
        parts: list[str] = []
        segments: list[dict[str, Any]] = []
        marks: list[dict[str, Any]] = []
        length = 0

        def visit(node: RichTextNode, path: tuple[int, ...]) -> None:
            nonlocal length
            start = length
            if node.tag in {"#comment", "#declaration", "#instruction", "script", "style"}:
                self._preserve_nonbody(node, path)
                return
            if node.text is not None:
                parts.append(node.text)
                length += len(node.text)
                segments.append({"clean_range": [start, length],
                                 "origin_id": self.origin(path, "html_text"),
                                 "source_range": [0, len(node.text)]})
                if node.tag == "#literal":
                    self.ctx.diagnostic("UNRECOGNIZED_HTML_LITERAL", self.origin(path))
                return
            metadata = self._node_metadata(node, path)
            if node.tag == "br":
                parts.append("\n")
                length += 1
                segments.append({"clean_range": [start, length], "origin_id": None,
                                 "rule_id": "html-br-newline"})
            for index, child in enumerate(node.children):
                visit(child, path + (index,))
            for mark in metadata["marks"]:
                marks.append({"range": [start, length], **mark})
            if node.tag in _MARKS:
                marks.append({"range": [start, length], "kind": _MARKS[node.tag],
                              "origin_id": self.origin(path)})
            for media_id in metadata["media_ids"]:
                marks.append({"range": [start, length], "kind": "media", "media_id": media_id})

        for node, path in entries:
            visit(node, path)
        return {"text": "".join(parts), "origin_id": origin, "segments": segments, "marks": marks}

    def _preserve_nonbody(self, node: RichTextNode, path: tuple[int, ...]) -> None:
        self.ctx.extensions.append({"kind": "html_payload", "tag": node.tag,
                                    "origin_id": self.origin(path), "value": node.to_dict()})
        if node.tag != "style":
            self.ctx.diagnostic("PRESERVED_HTML_PAYLOAD", self.origin(path))

    def _children(self, node: RichTextNode, path: tuple[int, ...]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        pending: list[tuple[RichTextNode, tuple[int, ...]]] = []

        def flush() -> None:
            if pending:
                origin = self.origin(path)
                output.append(self.ctx.block("paragraph", origin, value=self._inline(pending, origin)))
                pending.clear()

        for index, child in enumerate(node.children):
            child_path = path + (index,)
            if child.tag in _BLOCK_TAGS or child.tag in {"div", "section", "article", "li", "summary"} or self._contains_blocks(child):
                flush()
                output.append(self._block(child, child_path))
            elif child.tag in {"html", "body", "head"}:
                flush()
                self._node_metadata(child, child_path)
                output.extend(self._children(child, child_path))
            elif child.tag in {"#comment", "#declaration", "#instruction", "script", "style"}:
                flush()
                self._preserve_nonbody(child, child_path)
            elif child.tag is None and child.text and not child.text.strip() and "\n" in child.text and not pending:
                self.ctx.counts["formatting_whitespace_nodes"] += 1
            else:
                pending.append((child, child_path))
        flush()
        return output

    def _contains_blocks(self, node: RichTextNode) -> bool:
        return any(child.tag in _BLOCK_TAGS or child.tag in {"div", "section", "article"}
                   or self._contains_blocks(child) for child in node.children)

    def _block(self, node: RichTextNode, path: tuple[int, ...]) -> dict[str, Any]:
        origin = self.origin(path)
        metadata = self._node_metadata(node, path)
        tag = node.tag or ""
        if tag == "table":
            return self._table(node, path)
        if tag == "hr":
            return self.ctx.block("separator", origin)
        if tag in {"ul", "ol"}:
            return self.ctx.block("list", origin, ordered=tag == "ol", children=self._children(node, path),
                                  source_attributes=dict(node.attrs))
        if tag in {"div", "section", "article", "details", "li", "blockquote", "dl", "figure"}:
            kind = "quote" if tag == "blockquote" else "group"
            return self.ctx.block(kind, origin, source_tag=tag, children=self._children(node, path),
                                  annotations=metadata["marks"])
        if self._contains_blocks(node):
            return self.ctx.block("group", origin, source_tag=tag, children=self._children(node, path),
                                  annotations=metadata["marks"])
        kind = "heading" if re.fullmatch(r"h[1-6]", tag) else "paragraph"
        values = {"value": self._inline([(node, path)], origin), "source_tag": tag}
        if kind == "heading":
            values["level"] = int(tag[1])
        return self.ctx.block(kind, origin, **values)

    def _table(self, node: RichTextNode, path: tuple[int, ...]) -> dict[str, Any]:
        rows: list[tuple[RichTextNode, tuple[int, ...]]] = []
        extras: list[dict[str, Any]] = []

        def collect(parent: RichTextNode, parent_path: tuple[int, ...]) -> None:
            for index, child in enumerate(parent.children):
                child_path = parent_path + (index,)
                self._node_metadata(child, child_path)
                if child.tag == "tr":
                    rows.append((child, child_path))
                elif child.tag in {"thead", "tbody", "tfoot", "colgroup"}:
                    collect(child, child_path)
                elif child.tag == "col":
                    self.ctx.extensions.append({"kind": "table_column", "origin_id": self.origin(child_path),
                                                "attributes": dict(child.attrs)})
                elif child.text is not None and not child.text.strip():
                    self.ctx.counts["formatting_whitespace_nodes"] += 1
                else:
                    extras.append(self._block(child, child_path))

        collect(node, path)
        output_rows = []
        active_spans: list[tuple[int, int, int]] = []
        for row_index, (row, row_path) in enumerate(rows):
            cells = []
            column = 0
            for index, cell in enumerate(row.children):
                cell_path = row_path + (index,)
                if cell.tag not in {"td", "th"}:
                    if cell.text is not None and not cell.text.strip():
                        continue
                    extras.append(self._block(cell, cell_path))
                    continue
                self._node_metadata(cell, cell_path)
                occupied = [left + width for end_row, left, width in active_spans
                            if row_index < end_row and left <= column < left + width]
                while occupied:
                    column = max(occupied)
                    occupied = [left + width for end_row, left, width in active_spans
                                if row_index < end_row and left <= column < left + width]
                attrs = dict(cell.attrs)
                spans = {}
                for key in ("rowspan", "colspan"):
                    raw = attrs.get(key, "1")
                    spans[key] = raw
                    if not raw or not raw.isdigit():
                        self.ctx.diagnostic("INVALID_TABLE_SPAN", self.origin(cell_path), attribute=key)
                width = max(1, int(spans["colspan"])) if spans["colspan"].isdigit() else 1
                height = int(spans["rowspan"]) if spans["rowspan"].isdigit() else 1
                end_row = len(rows) if height == 0 else row_index + height
                cells.append({"column_index": column, "is_header": cell.tag == "th", **spans,
                              "origin_id": self.origin(cell_path), "blocks": self._children(cell, cell_path),
                              "annotations": self._node_metadata(cell, cell_path)["marks"]})
                active_spans.append((end_row, column, width))
                column += width
            output_rows.append({"row_index": row_index, "origin_id": self.origin(row_path), "cells": cells,
                                "annotations": self._node_metadata(row, row_path)["marks"]})
        return self.ctx.block("table", self.origin(path), rows=output_rows, children=extras,
                              table_format="html", annotations=self._node_metadata(node, path)["marks"])
