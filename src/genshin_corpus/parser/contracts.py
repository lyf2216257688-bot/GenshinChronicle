from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


PARSED_SCHEMA_VERSION = "phase02-draft-0.1"
PARSER_VERSION = "obc-foundation-0.2"
PROVENANCE_UNKNOWN = "unknown"
CONTENT_ROLE_UNKNOWN = "unknown"

ParseStatus = Literal[
    "parsed",
    "parsed_with_anomalies",
    "preserved_unsupported",
    "blocked_integrity",
]
IdentityStability = Literal["logical", "candidate", "snapshot_only"]


def _as_tuple(values: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    return tuple(values or ())


@dataclass(frozen=True)
class ParsedIdentity:
    """Identity is explicit about its evidence strength."""

    kind: str
    key: str
    stability: IdentityStability
    components: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.kind or not self.key:
            raise ValueError("identity kind and key are required")
        object.__setattr__(self, "components", tuple(self.components))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "key": self.key,
            "stability": self.stability,
            "components": {key: value for key, value in self.components},
        }


@dataclass(frozen=True)
class RawRef:
    """Small, resolvable pointer to immutable Raw evidence."""

    source: str
    locale: str
    run_id: str
    artifact_kind: str
    artifact_path: str
    artifact_sha256: str
    content_id: str | None = None
    json_pointer: str = ""
    embedded_json_pointer: str | None = None
    source_value_sha256: str | None = None

    def __post_init__(self) -> None:
        required = {
            "source": self.source,
            "locale": self.locale,
            "run_id": self.run_id,
            "artifact_kind": self.artifact_kind,
            "artifact_path": self.artifact_path,
            "artifact_sha256": self.artifact_sha256,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"RawRef missing required fields: {', '.join(missing)}")
        for name in ("artifact_sha256", "source_value_sha256"):
            value = getattr(self, name)
            if value is not None and (len(value) != 64 or any(char not in "0123456789abcdef" for char in value.lower())):
                raise ValueError(f"{name} must be a lowercase or uppercase SHA-256 hex digest")
        if self.json_pointer and not self.json_pointer.startswith("/"):
            raise ValueError("json_pointer must be an RFC 6901-style pointer or empty")
        if self.embedded_json_pointer and not self.embedded_json_pointer.startswith("/"):
            raise ValueError("embedded_json_pointer must be an RFC 6901-style pointer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "locale": self.locale,
            "run_id": self.run_id,
            "artifact_kind": self.artifact_kind,
            "artifact_path": self.artifact_path,
            "artifact_sha256": self.artifact_sha256,
            "content_id": self.content_id,
            "json_pointer": self.json_pointer,
            "embedded_json_pointer": self.embedded_json_pointer,
            "source_value_sha256": self.source_value_sha256,
        }


@dataclass(frozen=True)
class SourcePosition:
    json_pointer: str = ""
    array_index: int | None = None
    layout_path: tuple[str, ...] = ()
    ordering: int | None = None

    def __post_init__(self) -> None:
        if self.json_pointer and not self.json_pointer.startswith("/"):
            raise ValueError("source position json_pointer must be an RFC 6901-style pointer")
        if self.array_index is not None and self.array_index < 0:
            raise ValueError("array_index must be non-negative")
        if self.ordering is not None and self.ordering < 0:
            raise ValueError("ordering must be non-negative")
        object.__setattr__(self, "layout_path", tuple(self.layout_path))

    def to_dict(self) -> dict[str, Any]:
        return {
            "json_pointer": self.json_pointer,
            "array_index": self.array_index,
            "layout_path": list(self.layout_path),
            "ordering": self.ordering,
        }


@dataclass(frozen=True)
class Classification:
    """Versioned provenance/content-role result; unknown is first-class."""

    state: Literal["classified", "mixed", "unknown"] = "unknown"
    labels: tuple[str, ...] = ()
    basis: tuple[str, ...] = ()
    taxonomy_version: str = "draft-0.1"
    rule_id: str | None = None
    rule_version: str | None = None

    def __post_init__(self) -> None:
        if self.state == "classified" and not self.labels:
            raise ValueError("classified result requires at least one label")
        if self.state == "unknown" and self.labels:
            raise ValueError("unknown result cannot carry classified labels")
        object.__setattr__(self, "labels", _as_tuple(self.labels))
        object.__setattr__(self, "basis", _as_tuple(self.basis))

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "labels": list(self.labels),
            "basis": list(self.basis),
            "taxonomy_version": self.taxonomy_version,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
        }


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"
    path: str = ""

    def __post_init__(self) -> None:
        if not self.code or not self.message:
            raise ValueError("diagnostic code and message are required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "path": self.path,
        }


@dataclass(frozen=True)
class ContractMetadata:
    schema_version: str = PARSED_SCHEMA_VERSION
    parser_version: str = PARSER_VERSION
    parse_status: ParseStatus = "parsed"
    raw_refs: tuple[RawRef, ...] = ()
    source_position: SourcePosition = field(default_factory=SourcePosition)
    source_fingerprint: str = ""
    parsed_fingerprint: str = ""
    provenance: Classification = field(default_factory=Classification)
    content_role: Classification = field(default_factory=Classification)
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_refs", tuple(self.raw_refs))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "parser_version": self.parser_version,
            "parse_status": self.parse_status,
            "raw_refs": [ref.to_dict() for ref in self.raw_refs],
            "source_position": self.source_position.to_dict(),
            "source_fingerprint": self.source_fingerprint,
            "parsed_fingerprint": self.parsed_fingerprint,
            "provenance": self.provenance.to_dict(),
            "content_role": self.content_role.to_dict(),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }
