from __future__ import annotations

from .contracts import ParsedIdentity


def detail_identity(source: str, locale: str, content_id: str) -> ParsedIdentity:
    return ParsedIdentity(
        kind="detail",
        key=f"{source}:{locale}:{content_id}",
        stability="logical",
        components=(("source", source), ("locale", locale), ("content_id", str(content_id))),
    )


def module_identity(content_id: str, module_id: str | None) -> ParsedIdentity:
    if module_id:
        return ParsedIdentity(
            kind="module",
            key=f"content:{content_id}:module:{module_id}",
            stability="candidate",
            components=(("content_id", str(content_id)), ("module_id", str(module_id))),
        )
    return ParsedIdentity(
        kind="module",
        key=f"content:{content_id}:module:missing",
        stability="snapshot_only",
        components=(("content_id", str(content_id)),),
    )


def component_identity(content_id: str, component_id: str | None, same_type_ordinal: int) -> ParsedIdentity:
    """Build a provisional component key without using the global array index.

    ``same_type_ordinal`` is the ordinal among siblings with the same source
    component type. It is still only a candidate identity until source
    stability is verified.
    """

    if same_type_ordinal < 0:
        raise ValueError("same_type_ordinal must be non-negative")
    if component_id:
        return ParsedIdentity(
            kind="component",
            key=f"content:{content_id}:component:{component_id}:ordinal:{same_type_ordinal}",
            stability="candidate",
            components=(
                ("content_id", str(content_id)),
                ("component_id", str(component_id)),
                ("same_type_ordinal", str(same_type_ordinal)),
            ),
        )
    return ParsedIdentity(
        kind="component",
        key=f"content:{content_id}:component:missing:ordinal:{same_type_ordinal}",
        stability="snapshot_only",
        components=(("content_id", str(content_id)), ("same_type_ordinal", str(same_type_ordinal))),
    )


def content_unit_identity(parent_key: str, source_id: str | None, ordinal: int) -> ParsedIdentity:
    if ordinal < 0:
        raise ValueError("ordinal must be non-negative")
    if source_id:
        return ParsedIdentity(
            kind="content_unit",
            key=f"{parent_key}:source:{source_id}",
            stability="candidate",
            components=(("parent", parent_key), ("source_id", str(source_id))),
        )
    return ParsedIdentity(
        kind="content_unit",
        key=f"{parent_key}:ordinal:{ordinal}",
        stability="snapshot_only",
        components=(("parent", parent_key), ("ordinal", str(ordinal))),
    )
