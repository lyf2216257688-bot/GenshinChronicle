"""Record-local evidence pools and coverage accounting."""
from __future__ import annotations

from collections import Counter
from typing import Any

from ..evidence_io import digest
from .models import pointer_token, source_leaves


class RecordContext:
    def __init__(self) -> None:
        self.artifacts: list[dict[str, str]] = []
        self.origins: dict[str, dict[str, Any]] = {}
        self.relations: list[dict[str, Any]] = []
        self.media: list[dict[str, Any]] = []
        self.extensions: list[dict[str, Any]] = []
        self.diagnostics: list[dict[str, Any]] = []
        self.counts: Counter[str] = Counter()
        self._artifact_ids: dict[str, str] = {}
        self._origin_ids: dict[str, str] = {}
        self._block_count = 0
        self._claims: dict[str, str] | None = None

    def artifact(self, artifact: dict[str, str]) -> str:
        key = digest(artifact)
        if key not in self._artifact_ids:
            artifact_id = f"a{len(self.artifacts)}"
            self._artifact_ids[key] = artifact_id
            self.artifacts.append({"artifact_id": artifact_id, **artifact})
        return self._artifact_ids[key]

    def origin(self, artifact_id: str, steps: list[dict[str, Any]], precision: str = "value") -> str:
        value = {"artifact_id": artifact_id, "steps": steps, "precision": precision}
        key = digest(value)
        if key not in self._origin_ids:
            origin_id = f"o{len(self.origins)}"
            self._origin_ids[key] = origin_id
            self.origins[origin_id] = value
        return self._origin_ids[key]

    def derive(self, origin_id: str, *steps: dict[str, Any], precision: str = "value") -> str:
        value = {"parent_origin_id": origin_id, "steps": list(steps), "precision": precision}
        key = digest(value)
        if key not in self._origin_ids:
            derived = f"o{len(self.origins)}"
            self._origin_ids[key] = derived
            self.origins[derived] = value
        return self._origin_ids[key]

    def artifact_for_origin(self, origin_id: str) -> dict[str, str]:
        origin = self.origins[origin_id]
        while "parent_origin_id" in origin:
            origin = self.origins[origin["parent_origin_id"]]
        return next(item for item in self.artifacts if item["artifact_id"] == origin["artifact_id"])

    def child(self, origin_id: str, key: Any) -> str:
        return self.derive(origin_id, {"op": "pointer", "path": "/" + pointer_token(key)})

    def text(self, value: str, origin_id: str) -> dict[str, Any]:
        return {"text": value, "origin_id": origin_id}

    def block(self, kind: str, origin_id: str, **values: Any) -> dict[str, Any]:
        block_id = f"b{self._block_count}"
        self._block_count += 1
        return {"block_id": block_id, "kind": kind, "origin_id": origin_id, **values}

    def diagnostic(self, code: str, origin_id: str, **details: Any) -> None:
        self.diagnostics.append({"code": code, "origin_id": origin_id, **details})

    def begin_component(self) -> None:
        self._claims = {}

    def claim(self, value: Any, path: str, disposition: str = "content") -> None:
        if self._claims is None:
            return
        for leaf in source_leaves(value, path):
            if leaf in self._claims:
                raise ValueError(f"Source value consumed twice: {leaf}")
            self._claims[leaf] = disposition

    def end_component(self, value: Any) -> None:
        expected = set(source_leaves(value))
        claims = self._claims or {}
        if set(claims) != expected:
            raise ValueError(f"Component coverage mismatch: {sorted(expected - set(claims))[:8]}")
        self.counts["component_source_values"] += len(expected)
        self.counts["accounted_component_values"] += len(claims)
        for disposition in claims.values():
            self.counts["disposition_" + disposition] += 1
        self._claims = None

    def resource(self, kind: str, url: str, origin_id: str, **details: Any) -> str:
        media_id = f"m{len(self.media)}"
        self.media.append({"media_id": media_id, "kind": kind, "url": url,
                           "origin_id": origin_id, "binary_available": False, **details})
        return media_id

    def relation(self, origin_id: str, **details: Any) -> str:
        relation_id = f"r{len(self.relations)}"
        self.relations.append({"relation_id": relation_id, "origin_id": origin_id, **details})
        return relation_id
