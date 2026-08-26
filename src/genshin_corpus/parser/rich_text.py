from __future__ import annotations

from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Any


_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "div", "dl", "fieldset",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
    "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
    "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


@dataclass
class RichTextNode:
    tag: str | None
    attrs: tuple[tuple[str, str | None], ...] = ()
    text: str | None = None
    children: list["RichTextNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"tag": self.tag, "attrs": {key: value for key, value in self.attrs}}
        if self.text is not None:
            value["text"] = self.text
        if self.children:
            value["children"] = [child.to_dict() for child in self.children]
        return value


class _TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.root = RichTextNode(None)
        self.stack = [self.root]
        self.raw_parts: list[str] = []

    def _append(self, node: RichTextNode) -> None:
        self.stack[-1].children.append(node)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.raw_parts.append(self.get_starttag_text() or "<" + tag + ">")
        node = RichTextNode(tag.lower(), tuple(attrs))
        self._append(node)
        if tag.lower() not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.raw_parts.append(self.get_starttag_text() or "<" + tag + "/>" )
        self._append(RichTextNode(tag.lower(), tuple(attrs)))

    def handle_endtag(self, tag: str) -> None:
        self.raw_parts.append(f"</{tag}>")
        lowered = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == lowered:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.raw_parts.append(data)
        if data:
            self._append(RichTextNode(None, text=data))

    def handle_entityref(self, name: str) -> None:
        raw = f"&{name};"
        self.raw_parts.append(raw)
        self._append(RichTextNode(None, text=unescape(raw)))

    def handle_charref(self, name: str) -> None:
        raw = f"&#{name};"
        self.raw_parts.append(raw)
        self._append(RichTextNode(None, text=unescape(raw)))

    def handle_comment(self, data: str) -> None:
        self.raw_parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.raw_parts.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        self.raw_parts.append(f"<?{data}>")


def _walk(node: RichTextNode):
    yield node
    for child in node.children:
        yield from _walk(child)


def _normalized_text(node: RichTextNode, out: list[str]) -> None:
    if node.tag in {"script", "style"}:
        return
    if node.tag in {"br", "hr"}:
        out.append("\n")
        return
    if node.tag in _BLOCK_TAGS and out and not out[-1].endswith("\n"):
        out.append("\n")
    if node.text is not None:
        out.append(node.text)
    for child in node.children:
        _normalized_text(child, out)
    if node.tag in _BLOCK_TAGS:
        out.append("\n")


def _collapse_whitespace(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _text_segments(node: RichTextNode, path: tuple[int, ...] = ()) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    if node.text is not None:
        segments.append({"path": "/" + "/".join(str(index) for index in path), "text": node.text})
    for index, child in enumerate(node.children):
        segments.extend(_text_segments(child, path + (index,)))
    return segments


def parse_rich_text(markup: str) -> dict[str, Any]:
    """Parse observed OBC HTML without discarding source markup or attributes."""

    parser = _TreeParser()
    parser.feed(markup)
    parser.close()
    text_parts: list[str] = []
    _normalized_text(parser.root, text_parts)
    links: list[dict[str, str]] = []
    media: list[dict[str, str]] = []
    entries: list[dict[str, str]] = []
    for node in _walk(parser.root):
        attrs = dict(node.attrs)
        if node.tag == "a" and attrs.get("href") is not None:
            links.append({"href": attrs["href"] or ""})
        if node.tag == "img":
            url = attrs.get("src") or attrs.get("data-image-url")
            if url:
                media.append({"url": url})
        if attrs.get("data-entry-id"):
            entries.append({
                "content_id": attrs["data-entry-id"] or "",
                "name": attrs.get("data-entry-name", "") or "",
                "href": attrs.get("data-entry-link", "") or "",
            })
    return {
        "raw_markup": markup,
        "tree": parser.root.to_dict(),
        "normalized_text": _collapse_whitespace("".join(text_parts)),
        "text_segments": _text_segments(parser.root),
        "links": links,
        "media": media,
        "entry_references": entries,
    }


def looks_like_markup(value: str) -> bool:
    return "<" in value and ">" in value
