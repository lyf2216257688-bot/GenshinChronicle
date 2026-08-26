from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .contracts import Diagnostic, RawRef, SourcePosition
from .fingerprints import parsed_fingerprint
from .rich_text import looks_like_markup, parse_rich_text


def _pointer_token(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _path(*parts: Any) -> str:
    return "/" + "/".join(_pointer_token(part) for part in parts)


def _json_value(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class DialogueEdge:
    parent_id: str
    child_id: str
    ordering: int
    raw_ref: RawRef
    source_position: SourcePosition

    def to_dict(self) -> dict[str, Any]:
        return {
            "parent_id": self.parent_id,
            "child_id": self.child_id,
            "ordering": self.ordering,
            "raw_ref": self.raw_ref.to_dict(),
            "source_position": self.source_position.to_dict(),
        }


@dataclass(frozen=True)
class DialogueNode:
    source_id: str
    ordering: int
    option: Any = None
    dialogue: Any = None
    icon: Any = None
    speaker: str | None = None
    raw_fields: Mapping[str, Any] = field(default_factory=dict)
    raw_ref: RawRef | None = None
    source_position: SourcePosition = field(default_factory=SourcePosition)
    option_rich_text: Mapping[str, Any] | None = None
    dialogue_rich_text: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_fields", dict(self.raw_fields))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "ordering": self.ordering,
            "option": _json_value(self.option),
            "dialogue": _json_value(self.dialogue),
            "icon": _json_value(self.icon),
            "speaker": self.speaker,
            "raw_fields": _json_value(self.raw_fields),
            "raw_ref": None if self.raw_ref is None else self.raw_ref.to_dict(),
            "source_position": self.source_position.to_dict(),
            "option_rich_text": _json_value(self.option_rich_text),
            "dialogue_rich_text": _json_value(self.dialogue_rich_text),
        }


@dataclass(frozen=True)
class DialogueGroup:
    ordering: int
    source_path: str
    root_id: str | None
    nodes: tuple[DialogueNode, ...] = ()
    edges: tuple[DialogueEdge, ...] = ()
    raw_fields: Mapping[str, Any] = field(default_factory=dict)
    raw_ref: RawRef | None = None
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "edges", tuple(self.edges))
        object.__setattr__(self, "raw_fields", dict(self.raw_fields))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ordering": self.ordering,
            "source_path": self.source_path,
            "root_id": self.root_id,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "raw_fields": _json_value(self.raw_fields),
            "raw_ref": None if self.raw_ref is None else self.raw_ref.to_dict(),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True)
class DialogueGraph:
    groups: tuple[DialogueGroup, ...] = ()
    raw_fields: Mapping[str, Any] = field(default_factory=dict)
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "groups", tuple(self.groups))
        object.__setattr__(self, "raw_fields", dict(self.raw_fields))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "dialogue_graph",
            "groups": [group.to_dict() for group in self.groups],
            "raw_fields": _json_value(self.raw_fields),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }

    @property
    def parsed_fingerprint(self) -> str:
        return parsed_fingerprint(self.to_dict())


def _rich_text(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, str) and looks_like_markup(value):
        return parse_rich_text(value)
    return None


def _group_specs(decoded: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    listed = decoded.get("list")
    if isinstance(listed, list):
        return tuple((f"/list/{index}", item) for index, item in enumerate(listed))
    if any(key in decoded for key in ("contents", "root_id", "child_ids")):
        return (("", decoded),)
    return ()


def parse_dialogue_graph(decoded: Any, *, component_ref: RawRef) -> DialogueGraph:
    """Parse observed OBC dialogue envelopes without imposing tree semantics."""

    if not isinstance(decoded, Mapping):
        diagnostic = Diagnostic("DIALOGUE_DATA_NOT_OBJECT", "interactive_dialogue data is not an object", "error")
        return DialogueGraph(diagnostics=(diagnostic,))

    groups: list[DialogueGroup] = []
    graph_diagnostics: list[Diagnostic] = []
    for group_index, (source_path, group) in enumerate(_group_specs(decoded)):
        group_ref = component_ref if not source_path else RawRef(
            **{**component_ref.to_dict(), "embedded_json_pointer": source_path}
        )
        groups.append(_parse_group(group, group_index=group_index, source_path=source_path, group_ref=group_ref))
    if not groups:
        graph_diagnostics.append(Diagnostic("DIALOGUE_GROUPS_NOT_FOUND", "no dialogue group envelope was recognized", "error"))
    graph_diagnostics.extend(item for group in groups for item in group.diagnostics)
    known = {"list", "contents", "root_id", "child_ids"}
    return DialogueGraph(
        groups=tuple(groups),
        raw_fields={key: value for key, value in decoded.items() if key not in known},
        diagnostics=tuple(graph_diagnostics),
    )


def _parse_group(group: Any, *, group_index: int, source_path: str, group_ref: RawRef) -> DialogueGroup:
    if not isinstance(group, Mapping):
        return DialogueGroup(
            ordering=group_index,
            source_path=source_path,
            root_id=None,
            raw_fields={"raw_value": group},
            raw_ref=group_ref,
            diagnostics=(Diagnostic("DIALOGUE_GROUP_NOT_OBJECT", "dialogue group is not an object", "error", source_path),),
        )
    diagnostics: list[Diagnostic] = []
    root_value = group.get("root_id")
    root_id = None if root_value is None else str(root_value)
    if root_value is None:
        diagnostics.append(Diagnostic("DIALOGUE_ROOT_MISSING", "dialogue group has no root_id", path=source_path))
    elif not root_id:
        diagnostics.append(Diagnostic("DIALOGUE_ROOT_EMPTY", "dialogue group root_id is empty", path=source_path))

    contents = group.get("contents")
    if not isinstance(contents, Mapping):
        diagnostics.append(Diagnostic("DIALOGUE_CONTENTS_MISSING", "dialogue group contents is not an object", path=f"{source_path}/contents"))
        contents = {}
    nodes: list[DialogueNode] = []
    for ordering, (node_key, payload) in enumerate(contents.items()):
        source_id = str(node_key)
        node_path = f"{source_path}/contents/{_pointer_token(source_id)}" if source_path else _path("contents", source_id)
        node_ref = RawRef(**{**group_ref.to_dict(), "embedded_json_pointer": node_path})
        if isinstance(payload, Mapping):
            known = {"option", "dialogue", "icon"}
            nodes.append(DialogueNode(
                source_id=source_id,
                ordering=ordering,
                option=payload.get("option"),
                dialogue=payload.get("dialogue"),
                icon=payload.get("icon"),
                raw_fields={key: value for key, value in payload.items() if key not in known},
                raw_ref=node_ref,
                source_position=SourcePosition(group_ref.json_pointer, layout_path=(node_path,), ordering=ordering),
                option_rich_text=_rich_text(payload.get("option")),
                dialogue_rich_text=_rich_text(payload.get("dialogue")),
            ))
        else:
            diagnostics.append(Diagnostic("DIALOGUE_NODE_NOT_OBJECT", "dialogue node payload is not an object", path=node_path))
            nodes.append(DialogueNode(source_id=source_id, ordering=ordering, raw_fields={"raw_value": payload}, raw_ref=node_ref, source_position=SourcePosition(group_ref.json_pointer, layout_path=(node_path,), ordering=ordering)))

    child_ids = group.get("child_ids")
    if not isinstance(child_ids, Mapping):
        diagnostics.append(Diagnostic("DIALOGUE_CHILD_IDS_MISSING", "dialogue group child_ids is not an object", path=f"{source_path}/child_ids"))
        child_ids = {}
    edges: list[DialogueEdge] = []
    parents: dict[str, list[str]] = {}
    node_ids = {node.source_id for node in nodes}
    for parent_value, children in child_ids.items():
        parent_id = str(parent_value)
        if not isinstance(children, list):
            diagnostics.append(Diagnostic("DIALOGUE_CHILDREN_NOT_LIST", "child_ids entry is not a list", path=f"{source_path}/child_ids/{parent_id}"))
            continue
        for ordering, child_value in enumerate(children):
            child_id = str(child_value)
            edge_path = f"{source_path}/child_ids/{_pointer_token(parent_id)}/{ordering}" if source_path else _path("child_ids", parent_id, ordering)
            edge_ref = RawRef(**{**group_ref.to_dict(), "embedded_json_pointer": edge_path})
            edges.append(DialogueEdge(parent_id, child_id, ordering, edge_ref, SourcePosition(group_ref.json_pointer, layout_path=(edge_path,), ordering=ordering)))
            parents.setdefault(child_id, []).append(parent_id)
            if parent_id not in node_ids:
                diagnostics.append(Diagnostic("DIALOGUE_PARENT_MISSING", "edge parent is absent from contents", path=edge_path))
            if child_id not in node_ids:
                diagnostics.append(Diagnostic("DIALOGUE_DANGLING_EDGE", "edge child is absent from contents", path=edge_path))
    for child_id, parent_ids in parents.items():
        if len(parent_ids) > 1:
            diagnostics.append(Diagnostic("DIALOGUE_MULTIPLE_PARENT", f"node {child_id!r} has multiple incoming edges", path=f"{source_path}/child_ids"))

    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge.parent_id, []).append(edge.child_id)
    if root_id and root_id not in node_ids:
        diagnostics.append(Diagnostic("DIALOGUE_ROOT_NOT_FOUND", "root_id is absent from contents", path=f"{source_path}/root_id"))
    reachable: set[str] = set()
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            diagnostics.append(Diagnostic("DIALOGUE_CYCLE", "cycle detected in dialogue edges", path=f"{source_path}/child_ids/{_pointer_token(node_id)}" if source_path else _path("child_ids", node_id)))
            return
        if node_id in visited:
            return
        visiting.add(node_id)
        reachable.add(node_id)
        for child_id in adjacency.get(node_id, []):
            if child_id in node_ids:
                visit(child_id)
        visiting.remove(node_id)
        visited.add(node_id)

    if root_id and root_id in node_ids:
        visit(root_id)
    for node_id in node_ids - reachable:
        diagnostics.append(Diagnostic("DIALOGUE_ORPHAN_NODE", "node is not reachable from group root", path=f"{source_path}/contents/{_pointer_token(node_id)}" if source_path else _path("contents", node_id)))
    return DialogueGroup(
        ordering=group_index,
        source_path=source_path,
        root_id=root_id,
        nodes=tuple(nodes),
        edges=tuple(edges),
        raw_fields={key: value for key, value in group.items() if key not in {"root_id", "child_ids", "contents"}},
        raw_ref=group_ref,
        diagnostics=tuple(diagnostics),
    )
