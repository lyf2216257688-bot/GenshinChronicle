"""Provider-free Phase 05 source-bound semantic build primitives.

The semantic layer is a rebuildable navigation derivative.  It never becomes
evidence authority: active items must bind to existing Retrieval Units and
also carry an explicit fixture/curated acceptance status.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write


SEMANTIC_BUILD_SCHEMA_VERSION = "phase05-source-bound-semantic-build-0.1"
SEMANTIC_ITEM_SCHEMA_VERSION = "phase05-source-bound-semantic-item-0.1"
SEMANTIC_VIEW_SCHEMA_VERSION = "phase05-source-bound-semantic-view-0.1"
NAVIGATION_CANDIDATE_SCHEMA_VERSION = "phase05-navigation-candidate-0.1"

SOURCE_BINDING_STATUSES = frozenset({"unresolved", "verified", "rejected", "unsupported", "ambiguous"})
SEMANTIC_ACCEPTANCE_STATUSES = frozenset({
    "not_accepted",
    "accepted_for_fixture_test",
    "curated_recorded",
    "future_production_accepted",
    "rejected",
    "unsupported",
    "ambiguous",
})
ACTIVE_ACCEPTANCE_STATUSES = frozenset({"accepted_for_fixture_test", "curated_recorded"})
ITEM_KINDS = frozenset({"topic", "mention", "fact", "event", "relation"})


class SemanticCompilationError(ValueError):
    """Raised when a semantic build contract cannot be validated."""


class NavigationTraversalError(ValueError):
    """Raised when bounded navigation input or policy is invalid."""


def _text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise SemanticCompilationError(f"{label} must be a non-empty string")
    if "\n" in value or "\r" in value:
        raise SemanticCompilationError(f"{label} must be single-line text")
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SemanticCompilationError(f"{label} must be an object")
    return value


def _tuple_text(values: Sequence[str], label: str) -> tuple[str, ...]:
    result = tuple(_text(value, f"{label}[{index}]") for index, value in enumerate(values))
    if len(result) != len(set(result)):
        raise SemanticCompilationError(f"{label} must not contain duplicates")
    return result


@dataclass(frozen=True)
class SourceBinding:
    """A claimed binding; verification is performed against an RU index."""

    unit_id: str
    canonical_address: Mapping[str, Any] = None  # type: ignore[assignment]
    lineage: Mapping[str, Any] = None  # type: ignore[assignment]
    quoted_text: str | None = None

    def __post_init__(self) -> None:
        _text(self.unit_id, "source binding unit_id")
        if self.canonical_address is None or self.lineage is None:
            raise SemanticCompilationError("source binding requires canonical address and lineage placeholders")
        _mapping(self.canonical_address, "source binding canonical_address")
        _mapping(self.lineage, "source binding lineage")
        if self.quoted_text is not None:
            _text(self.quoted_text, "source binding quoted_text")

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "canonical_address": dict(self.canonical_address),
            "lineage": dict(self.lineage),
            "quoted_text": self.quoted_text,
        }


@dataclass(frozen=True)
class SemanticItem:
    item_id: str
    item_kind: str
    label: str
    source_bindings: tuple[SourceBinding, ...]
    source_binding_status: str = "unresolved"
    semantic_acceptance_status: str = "not_accepted"
    topic_path: tuple[str, ...] = ()
    subject_ref: str | None = None
    object_ref: str | None = None
    predicate: str | None = None
    event_type: str | None = None
    participants: tuple[str, ...] = ()
    qualifiers: Mapping[str, Any] = None  # type: ignore[assignment]
    compiler_stage_identity: str = "fixture"
    compiler_run_identity: str = "fixture-run"
    raw_response_identity: str | None = None
    input_content_fingerprint: str = "fixture"
    inactive_reason: str | None = None

    def __post_init__(self) -> None:
        _text(self.item_id, "semantic item_id")
        if self.item_kind not in ITEM_KINDS:
            raise SemanticCompilationError("semantic item_kind is unsupported")
        _text(self.label, "semantic item label")
        if self.source_binding_status not in SOURCE_BINDING_STATUSES:
            raise SemanticCompilationError("source_binding_status is unsupported")
        if self.semantic_acceptance_status not in SEMANTIC_ACCEPTANCE_STATUSES:
            raise SemanticCompilationError("semantic_acceptance_status is unsupported")
        if not isinstance(self.source_bindings, tuple) or not self.source_bindings:
            raise SemanticCompilationError("semantic item requires source bindings")
        if len({binding.unit_id for binding in self.source_bindings}) != len(self.source_bindings):
            raise SemanticCompilationError("semantic item source bindings must be unique")
        object.__setattr__(self, "topic_path", _tuple_text(self.topic_path, "topic_path"))
        object.__setattr__(self, "participants", _tuple_text(self.participants, "participants"))
        object.__setattr__(self, "qualifiers", dict(_mapping(self.qualifiers or {}, "qualifiers")))
        _text(self.compiler_stage_identity, "compiler_stage_identity")
        _text(self.compiler_run_identity, "compiler_run_identity")
        if self.raw_response_identity is not None:
            _text(self.raw_response_identity, "raw_response_identity")
        _text(self.input_content_fingerprint, "input_content_fingerprint")
        if self.inactive_reason is not None:
            _text(self.inactive_reason, "inactive_reason")

    @property
    def active(self) -> bool:
        return (
            self.source_binding_status == "verified"
            and self.semantic_acceptance_status in ACTIVE_ACCEPTANCE_STATUSES
        )

    def identity_projection(self) -> dict[str, Any]:
        return {
            "schema_version": SEMANTIC_ITEM_SCHEMA_VERSION,
            "item_id": self.item_id,
            "item_kind": self.item_kind,
            "label": self.label,
            "source_bindings": [binding.to_dict() for binding in self.source_bindings],
            "source_binding_status": self.source_binding_status,
            "semantic_acceptance_status": self.semantic_acceptance_status,
            "topic_path": list(self.topic_path),
            "subject_ref": self.subject_ref,
            "object_ref": self.object_ref,
            "predicate": self.predicate,
            "event_type": self.event_type,
            "participants": list(self.participants),
            "qualifiers": dict(self.qualifiers),
            "compiler_stage_identity": self.compiler_stage_identity,
            "compiler_run_identity": self.compiler_run_identity,
            "raw_response_identity": self.raw_response_identity,
            "input_content_fingerprint": self.input_content_fingerprint,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.identity_projection(), "active": self.active, "inactive_reason": self.inactive_reason}


@dataclass(frozen=True)
class SemanticStage:
    stage_id: str
    stage_identity: str
    input_identity: str
    status: str
    cost: Mapping[str, Any] = None  # type: ignore[assignment]
    output_sha256: str | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        for label, value in (("stage_id", self.stage_id), ("stage_identity", self.stage_identity), ("input_identity", self.input_identity), ("status", self.status)):
            _text(value, label)
        if self.status not in {"complete", "partial_failed", "failed"}:
            raise SemanticCompilationError("semantic stage status is unsupported")
        object.__setattr__(self, "cost", dict(_mapping(self.cost or {}, "stage cost")))
        if self.failure_reason is not None:
            _text(self.failure_reason, "stage failure_reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "stage_identity": self.stage_identity,
            "input_identity": self.input_identity,
            "status": self.status,
            "cost": dict(self.cost),
            "output_sha256": self.output_sha256,
            "failure_reason": self.failure_reason,
        }


def _verify_binding(binding: SourceBinding, ru_index: Mapping[str, Mapping[str, Any]]) -> tuple[bool, str | None]:
    row = ru_index.get(binding.unit_id)
    if row is None:
        return False, "unknown_retrieval_unit"
    source = row.get("source")
    if not isinstance(source, Mapping):
        return False, "retrieval_unit_source_missing"
    canonical_address = source.get("canonical_address")
    lineage = source.get("lineage")
    if not isinstance(canonical_address, Mapping) or not isinstance(lineage, Mapping):
        return False, "authoritative_locator_missing"
    if dict(binding.canonical_address) != dict(canonical_address) or dict(binding.lineage) != dict(lineage):
        return False, "authoritative_locator_mismatch"
    if binding.quoted_text is not None:
        authoritative_text = row.get("retrieval_visible_text")
        if not isinstance(authoritative_text, str) or binding.quoted_text != authoritative_text:
            return False, "quote_not_verified_against_retrieval_unit"
    return True, None


@dataclass(frozen=True)
class SemanticBuild:
    build_identity: str
    input_identity: str
    stages: tuple[SemanticStage, ...]
    active_items: tuple[SemanticItem, ...]
    inactive_ledger: tuple[Mapping[str, Any], ...]
    reused_item_ids: tuple[str, ...] = ()

    @classmethod
    def from_items(
        cls,
        *,
        input_identity: str,
        stages: Sequence[SemanticStage],
        items: Sequence[SemanticItem],
        ru_index: Mapping[str, Mapping[str, Any]],
        reused_item_ids: Sequence[str] = (),
    ) -> "SemanticBuild":
        _text(input_identity, "semantic input_identity")
        active: list[SemanticItem] = []
        inactive: list[Mapping[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            if item.item_id in seen:
                raise SemanticCompilationError("semantic item_id collision")
            seen.add(item.item_id)
            failures: list[str] = []
            for binding in item.source_bindings:
                verified, reason = _verify_binding(binding, ru_index)
                if not verified:
                    failures.append(f"{binding.unit_id}:{reason}")
            if failures:
                binding_status = "rejected"
            elif item.source_binding_status in {"rejected", "unsupported", "ambiguous"}:
                # A compiler-declared unresolved/unsupported/ambiguous state is
                # not cleared merely because a candidate locator is present.
                binding_status = item.source_binding_status
            else:
                binding_status = "verified"
            checked = replace(
                item,
                source_binding_status=binding_status,
                inactive_reason=None if not failures and binding_status == "verified" and item.semantic_acceptance_status in ACTIVE_ACCEPTANCE_STATUSES else (
                    ";".join(failures) if failures else (
                        "source_binding_status_not_active"
                        if binding_status != "verified"
                        else "semantic_acceptance_not_active"
                    )
                ),
            )
            if checked.active:
                active.append(checked)
            else:
                inactive.append({
                    "item": checked.to_dict(),
                    "source_binding_status": checked.source_binding_status,
                    "semantic_acceptance_status": checked.semantic_acceptance_status,
                    "compiler_stage_identity": checked.compiler_stage_identity,
                    "compiler_run_identity": checked.compiler_run_identity,
                    "raw_response_identity": checked.raw_response_identity,
                    "source_bindings": [binding.to_dict() for binding in checked.source_bindings],
                    "reason": checked.inactive_reason or "inactive",
                })
        inactive.sort(key=lambda row: row["item"]["item_id"])
        stage_projection = [stage.to_dict() for stage in stages]
        active.sort(key=lambda item: item.item_id)
        reused = tuple(sorted(set(reused_item_ids)))
        active_ids = {item.item_id for item in active}
        if any(item_id not in active_ids for item_id in reused):
            raise SemanticCompilationError("reused_item_ids must identify active semantic items")
        build_identity = sha256_json({
            "schema_version": SEMANTIC_BUILD_SCHEMA_VERSION,
            "input_identity": input_identity,
            "stages": stage_projection,
            "active_items": [item.identity_projection() for item in active],
            "inactive_ledger": list(inactive),
            "reused_item_ids": list(reused),
        })
        return cls(
            build_identity=build_identity,
            input_identity=input_identity,
            stages=tuple(stages),
            active_items=tuple(active),
            inactive_ledger=tuple(inactive),
            reused_item_ids=reused,
        )

    def manifest(self, *, hierarchy_identity: str | None = None, graph_identity: str | None = None) -> dict[str, Any]:
        return {
            "schema_version": SEMANTIC_BUILD_SCHEMA_VERSION,
            "build_identity": self.build_identity,
            "input_identity": self.input_identity,
            "stages": [stage.to_dict() for stage in self.stages],
            "counts": {
                "active_items": len(self.active_items),
                "inactive_items": len(self.inactive_ledger),
                "reused_items": len(self.reused_item_ids),
            },
            "reused_item_ids": list(self.reused_item_ids),
            "hierarchy_view_identity": hierarchy_identity,
            "graph_view_identity": graph_identity,
        }


@dataclass(frozen=True)
class HierarchyNode:
    node_id: str
    path: tuple[str, ...]
    item_ids: tuple[str, ...]
    child_ids: tuple[str, ...]
    source_unit_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "path": list(self.path),
            "item_ids": list(self.item_ids),
            "child_ids": list(self.child_ids),
            "source_unit_ids": list(self.source_unit_ids),
        }


@dataclass(frozen=True)
class HierarchyView:
    build_identity: str
    view_identity: str
    nodes: Mapping[str, HierarchyNode]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SEMANTIC_VIEW_SCHEMA_VERSION,
            "view_kind": "hierarchy",
            "build_identity": self.build_identity,
            "view_identity": self.view_identity,
            "nodes": {key: value.to_dict() for key, value in sorted(self.nodes.items())},
        }


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    node_kind: str
    label: str
    item_ids: tuple[str, ...]
    source_unit_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_kind": self.node_kind,
            "label": self.label,
            "item_ids": list(self.item_ids),
            "source_unit_ids": list(self.source_unit_ids),
        }


@dataclass(frozen=True)
class GraphEdge:
    edge_id: str
    source: str
    target: str
    predicate: str
    item_id: str
    source_unit_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "source": self.source,
            "target": self.target,
            "predicate": self.predicate,
            "item_id": self.item_id,
            "source_unit_ids": list(self.source_unit_ids),
        }


@dataclass(frozen=True)
class GraphView:
    build_identity: str
    view_identity: str
    nodes: Mapping[str, GraphNode]
    edges: tuple[GraphEdge, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SEMANTIC_VIEW_SCHEMA_VERSION,
            "view_kind": "graph",
            "build_identity": self.build_identity,
            "view_identity": self.view_identity,
            "nodes": {key: value.to_dict() for key, value in sorted(self.nodes.items())},
            "edges": [edge.to_dict() for edge in self.edges],
        }


@dataclass(frozen=True)
class SemanticViews:
    build_identity: str
    hierarchy: HierarchyView
    graph: GraphView


def build_views(build: SemanticBuild) -> SemanticViews:
    """Build distinct views from exactly one accepted intermediate lineage."""

    paths: dict[tuple[str, ...], dict[str, Any]] = {(): {"items": set(), "units": set(), "children": set()}}
    for item in build.active_items:
        path = item.topic_path or ("unclassified",)
        for index in range(1, len(path) + 1):
            current = path[:index]
            parent = path[: index - 1]
            paths.setdefault(current, {"items": set(), "units": set(), "children": set()})
            paths[parent]["children"].add(current)
        for index in range(0, len(path) + 1):
            paths[path[:index]]["items"].add(item.item_id)
        # Parent nodes may aggregate item IDs for navigation metadata, but
        # source units remain direct to their semantic location so a parent
        # cannot bypass the configured hierarchy hop bound.
        paths[path]["units"].update(binding.unit_id for binding in item.source_bindings)
    hierarchy_nodes: dict[str, HierarchyNode] = {}
    for path, value in paths.items():
        node_id = sha256_json({"build_identity": build.build_identity, "path": list(path)})
        child_ids = tuple(sorted(sha256_json({"build_identity": build.build_identity, "path": list(child)}) for child in value["children"]))
        hierarchy_nodes[node_id] = HierarchyNode(node_id, path, tuple(sorted(value["items"])), child_ids, tuple(sorted(value["units"])))
    hierarchy_identity = sha256_json({"build_identity": build.build_identity, "nodes": [node.to_dict() for node in hierarchy_nodes.values()]})
    graph_nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []

    def ensure_node(node_id: str, kind: str, label: str, item: SemanticItem) -> None:
        existing = graph_nodes.get(node_id)
        units = tuple(sorted(set((existing.source_unit_ids if existing else ()) + tuple(binding.unit_id for binding in item.source_bindings))))
        item_ids = tuple(sorted(set((existing.item_ids if existing else ()) + (item.item_id,))))
        graph_nodes[node_id] = GraphNode(node_id, kind, label, item_ids, units)

    for item in build.active_items:
        if item.item_kind in {"mention", "fact", "relation", "event"}:
            subject = item.subject_ref or (item.participants[0] if item.participants else None)
            target = item.object_ref or (item.participants[1] if len(item.participants) > 1 else None)
            if subject:
                ensure_node(subject, "entity", subject, item)
            if item.item_kind == "event":
                event_node = f"event:{item.item_id}"
                ensure_node(event_node, "event", item.event_type or item.label, item)
                for participant in item.participants:
                    ensure_node(participant, "entity", participant, item)
                    edges.append(GraphEdge(sha256_json({"item": item.item_id, "source": participant, "target": event_node}), participant, event_node, "participates_in", item.item_id, tuple(binding.unit_id for binding in item.source_bindings)))
            elif subject and target:
                ensure_node(target, "entity", target, item)
                edges.append(GraphEdge(sha256_json({"item": item.item_id, "source": subject, "target": target}), subject, target, item.predicate or "related_to", item.item_id, tuple(binding.unit_id for binding in item.source_bindings)))
    edges.sort(key=lambda edge: edge.edge_id)
    graph_identity = sha256_json({"build_identity": build.build_identity, "nodes": [node.to_dict() for node in graph_nodes.values()], "edges": [edge.to_dict() for edge in edges]})
    return SemanticViews(
        build_identity=build.build_identity,
        hierarchy=HierarchyView(build.build_identity, hierarchy_identity, hierarchy_nodes),
        graph=GraphView(build.build_identity, graph_identity, graph_nodes, tuple(edges)),
    )


@dataclass(frozen=True)
class TraversalPolicy:
    max_hops: int = 3
    max_nodes: int = 32
    max_edges: int = 64
    max_candidates: int = 32

    def __post_init__(self) -> None:
        for name in ("max_hops", "max_nodes", "max_edges", "max_candidates"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise NavigationTraversalError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class NavigationTrace:
    route_id: str
    build_identity: str
    selected_item_ids: tuple[str, ...]
    selected_unit_ids: tuple[str, ...]
    visited_nodes: tuple[str, ...]
    traversed_edges: tuple[str, ...]
    truncated: bool
    termination: str
    policy: TraversalPolicy

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "build_identity": self.build_identity,
            "selected_item_ids": list(self.selected_item_ids),
            "selected_unit_ids": list(self.selected_unit_ids),
            "visited_nodes": list(self.visited_nodes),
            "traversed_edges": list(self.traversed_edges),
            "truncated": self.truncated,
            "termination": self.termination,
            "policy": self.policy.__dict__,
        }


def traverse_graph(view: GraphView, start_nodes: Sequence[str], *, route_id: str, policy: TraversalPolicy) -> NavigationTrace:
    starts = tuple(dict.fromkeys(_text(value, "graph start node") for value in start_nodes))
    queue: deque[tuple[str, int]] = deque((node, 0) for node in starts)
    adjacency: dict[str, list[GraphEdge]] = {}
    for edge in view.edges:
        adjacency.setdefault(edge.source, []).append(edge)
    visited: set[str] = set()
    edge_ids: list[str] = []
    item_ids: set[str] = set()
    unit_ids: set[str] = set()
    truncated = False
    while queue:
        node, depth = queue.popleft()
        if node in visited:
            continue
        if len(visited) >= policy.max_nodes:
            truncated = True
            break
        visited.add(node)
        if depth >= policy.max_hops:
            continue
        for edge in sorted(adjacency.get(node, ()), key=lambda value: value.edge_id):
            if len(edge_ids) >= policy.max_edges:
                truncated = True
                break
            edge_ids.append(edge.edge_id)
            item_ids.add(edge.item_id)
            unit_ids.update(edge.source_unit_ids)
            if edge.target not in visited:
                queue.append((edge.target, depth + 1))
        if truncated:
            break
    if len(unit_ids) > policy.max_candidates:
        truncated = True
        unit_ids = set(sorted(unit_ids)[: policy.max_candidates])
    return NavigationTrace(
        route_id=_text(route_id, "route_id"),
        build_identity=view.build_identity,
        selected_item_ids=tuple(sorted(item_ids)),
        selected_unit_ids=tuple(sorted(unit_ids)),
        visited_nodes=tuple(sorted(visited)),
        traversed_edges=tuple(edge_ids),
        truncated=truncated,
        termination="budget_exhausted" if truncated else "frontier_exhausted",
        policy=policy,
    )


@dataclass(frozen=True)
class NavigationCandidateInput:
    unit_id: str
    rank: int
    route_id: str
    source_binding: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": NAVIGATION_CANDIDATE_SCHEMA_VERSION,
            "unit_id": self.unit_id,
            "rank": self.rank,
            "route_id": self.route_id,
            "source_binding": dict(self.source_binding),
            "retrieval": {"mode": "semantic_navigation", "route_id": self.route_id},
        }


def resolve_navigation_candidates(
    unit_ids: Sequence[str],
    ru_index: Mapping[str, Mapping[str, Any]],
    *,
    route_id: str,
    max_candidates: int,
) -> tuple[NavigationCandidateInput, ...]:
    _text(route_id, "route_id")
    if not isinstance(max_candidates, int) or max_candidates <= 0:
        raise NavigationTraversalError("max_candidates must be positive")
    result: list[NavigationCandidateInput] = []
    for rank, unit_id in enumerate(dict.fromkeys(unit_ids), 1):
        if len(result) >= max_candidates:
            break
        row = ru_index.get(unit_id)
        if row is None:
            raise NavigationTraversalError(f"navigation candidate RU is unknown: {unit_id}")
        source = _mapping(row.get("source"), f"RU {unit_id}.source")
        address = _mapping(source.get("canonical_address"), f"RU {unit_id}.canonical_address")
        lineage = _mapping(source.get("lineage"), f"RU {unit_id}.lineage")
        result.append(NavigationCandidateInput(unit_id, rank, route_id, {"canonical_address": dict(address), "lineage": dict(lineage)}))
    return tuple(result)


def merge_navigation_candidate_rows(
    navigation_candidates: Sequence[NavigationCandidateInput],
    baseline_rows: Sequence[Mapping[str, Any]] = (),
) -> tuple[dict[str, Any], ...]:
    """Create deterministic Assembly input without inventing ranking scores."""

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in baseline_rows:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("unit_id"), str) or not raw["unit_id"]:
            raise NavigationTraversalError("baseline candidate row lacks unit_id")
        unit_id = str(raw["unit_id"])
        if unit_id in seen:
            continue
        seen.add(unit_id)
        row = dict(raw)
        row.setdefault("retrieval", {})
        result.append(row)
    next_rank = len(result) + 1
    for candidate in navigation_candidates:
        if candidate.unit_id in seen:
            continue
        seen.add(candidate.unit_id)
        row = candidate.to_dict()
        row["rank"] = next_rank
        next_rank += 1
        result.append(row)
    return tuple(result)


def write_semantic_build(root: Path, build: SemanticBuild, views: SemanticViews) -> dict[str, Any]:
    """Publish one immutable build; existing bytes may only be reused exactly."""

    root = Path(root)
    semantic_value = {
        "build": build.manifest(),
        "active_items": [item.to_dict() for item in build.active_items],
        "inactive_ledger": list(build.inactive_ledger),
    }
    hierarchy_value = views.hierarchy.to_dict()
    graph_value = views.graph.to_dict()
    manifest = build.manifest(hierarchy_identity=views.hierarchy.view_identity, graph_identity=views.graph.view_identity)
    manifest["artifact_sha256"] = {
        "semantic_build.json": sha256_json(semantic_value),
        "hierarchy.json": sha256_json(hierarchy_value),
        "graph.json": sha256_json(graph_value),
    }
    artifacts = {
        "semantic_build.json": semantic_value,
        "hierarchy.json": hierarchy_value,
        "graph.json": graph_value,
        "manifest.json": manifest,
    }
    root.mkdir(parents=True, exist_ok=True)
    descriptors: dict[str, Any] = {}
    for name, value in artifacts.items():
        body = canonical_json_bytes(value)
        path = root / name
        if path.exists():
            if path.read_bytes() != body:
                raise SemanticCompilationError(f"refusing to overwrite semantic artifact: {path}")
        else:
            atomic_write(path, body)
        descriptors[name] = {"path": name, "sha256": sha256_json(value), "byte_count": len(body)}
    return {"manifest": manifest, "artifacts": descriptors}


def load_semantic_build(root: Path) -> Mapping[str, Any]:
    root = Path(root)
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        semantic = json.loads((root / "semantic_build.json").read_text(encoding="utf-8"))
        hierarchy = json.loads((root / "hierarchy.json").read_text(encoding="utf-8"))
        graph = json.loads((root / "graph.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise SemanticCompilationError("cannot load semantic build") from exc
    if manifest.get("schema_version") != SEMANTIC_BUILD_SCHEMA_VERSION:
        raise SemanticCompilationError("unsupported semantic build schema")
    if semantic.get("build", {}).get("build_identity") != manifest.get("build_identity"):
        raise SemanticCompilationError("semantic build identity mismatch")
    active_ids = {
        item.get("item_id")
        for item in semantic.get("active_items", ())
        if isinstance(item, Mapping)
    }
    reused_ids = manifest.get("reused_item_ids", ())
    if not isinstance(reused_ids, list) or any(not isinstance(item_id, str) for item_id in reused_ids):
        raise SemanticCompilationError("semantic reused_item_ids manifest is invalid")
    if not set(reused_ids) <= active_ids:
        raise SemanticCompilationError("semantic reused_item_ids are not active items")
    if manifest.get("counts", {}).get("reused_items") != len(reused_ids):
        raise SemanticCompilationError("semantic reused_item_ids count mismatch")
    if hierarchy.get("build_identity") != manifest.get("build_identity") or hierarchy.get("view_identity") != manifest.get("hierarchy_view_identity"):
        raise SemanticCompilationError("hierarchy view identity mismatch")
    if graph.get("build_identity") != manifest.get("build_identity") or graph.get("view_identity") != manifest.get("graph_view_identity"):
        raise SemanticCompilationError("graph view identity mismatch")
    expected_hashes = manifest.get("artifact_sha256")
    if not isinstance(expected_hashes, Mapping):
        raise SemanticCompilationError("semantic artifact hash manifest is missing")
    for name, value in (("semantic_build.json", semantic), ("hierarchy.json", hierarchy), ("graph.json", graph)):
        if expected_hashes.get(name) != sha256_json(value):
            raise SemanticCompilationError(f"semantic artifact hash mismatch: {name}")
    return {"manifest": manifest, "semantic_build": semantic, "hierarchy": hierarchy, "graph": graph}


class NavigationDecisionProvider(Protocol):
    """Provider-neutral decision seam; no seed algorithm is frozen here."""

    def decide(self, question: str, *, build_identity: str, packet_binding: Mapping[str, Any]) -> Mapping[str, Any]:
        ...


__all__ = [
    "ACTIVE_ACCEPTANCE_STATUSES",
    "GraphEdge",
    "GraphNode",
    "GraphView",
    "HierarchyNode",
    "HierarchyView",
    "NavigationCandidateInput",
    "NavigationDecisionProvider",
    "NavigationTrace",
    "NavigationTraversalError",
    "SEMANTIC_BUILD_SCHEMA_VERSION",
    "SEMANTIC_ITEM_SCHEMA_VERSION",
    "SEMANTIC_VIEW_SCHEMA_VERSION",
    "SemanticBuild",
    "SemanticCompilationError",
    "SemanticItem",
    "SemanticStage",
    "SemanticViews",
    "SourceBinding",
    "TraversalPolicy",
    "build_views",
    "load_semantic_build",
    "merge_navigation_candidate_rows",
    "resolve_navigation_candidates",
    "traverse_graph",
    "write_semantic_build",
]
