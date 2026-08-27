from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from genshin_corpus.parser.contracts import Classification, Diagnostic, ParseStatus, ParsedIdentity, RawRef
from genshin_corpus.parser.models import ParsedDetail

from .fingerprints import (
    canonical_content_fingerprint,
    canonical_dependency_fingerprint,
    canonical_record_id,
)


CANONICAL_SCHEMA_VERSION = "phase03-draft-0.1"
CANONICAL_TRANSFORM_VERSION = "contract-foundation-0.1"
STRUCTURAL_NORMALIZATION_VERSION = "none-0.1"

CanonicalRecordStatus = Literal["canonical", "canonical_with_anomalies", "blocked_integrity"]
LineageEvidenceScope = Literal["direct_raw", "inherited_parent_raw", "parsed_dependency"]

_PARSED_STATUSES = {"parsed", "parsed_with_anomalies", "preserved_unsupported", "blocked_integrity"}
_RECORD_STATUSES = {"canonical", "canonical_with_anomalies", "blocked_integrity"}
_LINEAGE_SCOPES = {"direct_raw", "inherited_parent_raw", "parsed_dependency"}


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is required")


def _require_sha256(name: str, value: str) -> None:
    _require_text(name, value)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value.lower()):
        raise ValueError(f"{name} must be a SHA-256 hex digest")


def _version_pairs(values: Mapping[str, str] | tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    items = values.items() if isinstance(values, Mapping) else values
    pairs = tuple(sorted((str(key), str(value)) for key, value in items))
    if any(not key or not value for key, value in pairs):
        raise ValueError("version keys and values must be non-empty")
    return pairs


def _metadata_lineage_pairs(
    values: Mapping[str, "LineageLink"] | tuple[tuple[str, "LineageLink"], ...],
) -> tuple[tuple[str, "LineageLink"], ...]:
    items = values.items() if isinstance(values, Mapping) else values
    pairs = tuple(sorted(((str(key), value) for key, value in items), key=lambda item: item[0]))
    if any(not key or not isinstance(value, LineageLink) for key, value in pairs):
        raise ValueError("metadata lineage requires non-empty keys and LineageLink values")
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("metadata lineage keys must be unique")
    return pairs


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


def _identity_from_mapping(value: Mapping[str, Any]) -> ParsedIdentity:
    components = value.get("components", {})
    if not isinstance(components, Mapping):
        raise ValueError("parsed identity components must be an object")
    kind = value.get("kind")
    key = value.get("key")
    stability = value.get("stability")
    if not isinstance(kind, str) or not isinstance(key, str) or not isinstance(stability, str):
        raise ValueError("parsed identity is missing kind, key, or stability")
    return ParsedIdentity(
        kind=kind,
        key=key,
        stability=stability,
        components=tuple(sorted((str(item_key), str(item_value)) for item_key, item_value in components.items())),
    )


def parsed_identity_from_input(parsed_input: ParsedDetail | Mapping[str, Any]) -> ParsedIdentity:
    """Read the preserved Parsed identity without promoting it to semantic identity."""

    if isinstance(parsed_input, ParsedDetail):
        return parsed_input.identity
    if not isinstance(parsed_input, Mapping):
        raise ValueError("Parsed input must be a ParsedDetail or serialized record object")
    identity = parsed_input.get("identity")
    if not isinstance(identity, Mapping):
        raise ValueError("serialized Parsed record is missing identity")
    return _identity_from_mapping(identity)


def _parsed_status_from_input(parsed_input: ParsedDetail | Mapping[str, Any]) -> ParseStatus:
    if isinstance(parsed_input, ParsedDetail):
        return parsed_input.metadata.parse_status
    metadata = parsed_input.get("metadata") if isinstance(parsed_input, Mapping) else None
    if not isinstance(metadata, Mapping) or metadata.get("parse_status") not in _PARSED_STATUSES:
        raise ValueError("serialized Parsed record is missing a valid metadata.parse_status")
    return metadata["parse_status"]


def source_identity_from_parsed_input(parsed_input: ParsedDetail | Mapping[str, Any]) -> ParsedIdentity | None:
    """Copy a verified normal-detail source identity; never reconstruct one."""

    if _parsed_status_from_input(parsed_input) == "blocked_integrity":
        return None

    if isinstance(parsed_input, ParsedDetail):
        identity = parsed_input.identity
        source = parsed_input.source
        locale = parsed_input.locale
        content_id = parsed_input.content_id
    else:
        required = ("source", "locale", "content_id", "modules")
        if any(field not in parsed_input for field in required):
            raise ValueError("serialized Parsed input is not a structurally valid ParsedDetail")
        source = parsed_input["source"]
        locale = parsed_input["locale"]
        content_id = parsed_input["content_id"]
        if not all(isinstance(value, str) and value for value in (source, locale, content_id)):
            raise ValueError("serialized ParsedDetail source, locale, and content_id are required strings")
        identity = parsed_identity_from_input(parsed_input)

    expected_components = {"content_id": str(content_id), "locale": locale, "source": source}
    if (
        identity.kind != "detail"
        or identity.stability != "logical"
        or len(identity.components) != len(expected_components)
        or dict(identity.components) != expected_components
        or identity.key != f"{source}:{locale}:{content_id}"
    ):
        raise ValueError("ParsedDetail does not carry the verified source-contract identity")
    return identity


@dataclass(frozen=True)
class CanonicalVersions:
    schema_version: str = CANONICAL_SCHEMA_VERSION
    transform_version: str = CANONICAL_TRANSFORM_VERSION
    structural_normalization_version: str = STRUCTURAL_NORMALIZATION_VERSION
    classification_rule_versions: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _require_text("schema_version", self.schema_version)
        _require_text("transform_version", self.transform_version)
        _require_text("structural_normalization_version", self.structural_normalization_version)
        object.__setattr__(self, "classification_rule_versions", _version_pairs(self.classification_rule_versions))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "transform_version": self.transform_version,
            "structural_normalization_version": self.structural_normalization_version,
            "classification_rule_versions": dict(self.classification_rule_versions),
        }


@dataclass(frozen=True)
class CanonicalObservation:
    parsed_run_id: str
    parsed_manifest_path: str
    parsed_manifest_sha256: str
    parsed_record_path: str
    parsed_record_sha256: str
    parsed_schema_version: str
    parsed_parser_version: str
    parsed_pipeline_version: str
    parsed_status: ParseStatus
    parsed_semantic_fingerprint: str
    parsed_rule_versions: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "parsed_run_id",
            "parsed_manifest_path",
            "parsed_record_path",
            "parsed_schema_version",
            "parsed_parser_version",
            "parsed_pipeline_version",
        ):
            _require_text(name, getattr(self, name))
        for name in ("parsed_manifest_sha256", "parsed_record_sha256", "parsed_semantic_fingerprint"):
            _require_sha256(name, getattr(self, name))
        if self.parsed_status not in _PARSED_STATUSES:
            raise ValueError("parsed_status is not a recognized Parsed status")
        object.__setattr__(self, "parsed_rule_versions", _version_pairs(self.parsed_rule_versions))

    def dependency_projection(self) -> dict[str, Any]:
        return {
            "parsed_run_id": self.parsed_run_id,
            "parsed_manifest_sha256": self.parsed_manifest_sha256,
            "parsed_record_sha256": self.parsed_record_sha256,
            "parsed_schema_version": self.parsed_schema_version,
            "parsed_parser_version": self.parsed_parser_version,
            "parsed_pipeline_version": self.parsed_pipeline_version,
            "parsed_status": self.parsed_status,
            "parsed_semantic_fingerprint": self.parsed_semantic_fingerprint,
            "parsed_rule_versions": dict(self.parsed_rule_versions),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "parsed_run_id": self.parsed_run_id,
            "parsed_manifest_path": self.parsed_manifest_path,
            "parsed_manifest_sha256": self.parsed_manifest_sha256,
            "parsed_record_path": self.parsed_record_path,
            "parsed_record_sha256": self.parsed_record_sha256,
            "parsed_schema_version": self.parsed_schema_version,
            "parsed_parser_version": self.parsed_parser_version,
            "parsed_pipeline_version": self.parsed_pipeline_version,
            "parsed_status": self.parsed_status,
            "parsed_semantic_fingerprint": self.parsed_semantic_fingerprint,
            "parsed_rule_versions": dict(self.parsed_rule_versions),
        }


@dataclass(frozen=True)
class LineageLink:
    parsed_json_pointer: str
    evidence_scope: LineageEvidenceScope
    raw_refs: tuple[RawRef, ...] = ()
    dependency_locator: str | None = None

    def __post_init__(self) -> None:
        if self.parsed_json_pointer and not self.parsed_json_pointer.startswith("/"):
            raise ValueError("parsed_json_pointer must be an RFC 6901-style pointer or empty")
        if self.evidence_scope not in _LINEAGE_SCOPES:
            raise ValueError("evidence_scope is not recognized")
        object.__setattr__(self, "raw_refs", tuple(self.raw_refs))
        if self.evidence_scope in {"direct_raw", "inherited_parent_raw"} and not self.raw_refs:
            raise ValueError(f"{self.evidence_scope} requires at least one RawRef")
        if self.evidence_scope == "parsed_dependency":
            if self.raw_refs:
                raise ValueError("parsed_dependency must not carry RawRefs")
            _require_text("dependency_locator", self.dependency_locator or "")
        elif self.dependency_locator is not None:
            raise ValueError("dependency_locator is only valid for parsed_dependency")

    def to_dict(self) -> dict[str, Any]:
        return {
            "parsed_json_pointer": self.parsed_json_pointer,
            "evidence_scope": self.evidence_scope,
            "raw_refs": [reference.to_dict() for reference in self.raw_refs],
            "dependency_locator": self.dependency_locator,
        }


@dataclass(frozen=True)
class ComponentContext:
    observation_key: str
    ordinal: int
    source_component_id: str | None
    source_data_encoding: str
    source_layout: Any
    source_style: Any
    parsed_component_fingerprint: str
    parsed_status: ParseStatus
    provenance: Classification
    content_role: Classification
    diagnostics: tuple[Diagnostic, ...]
    lineage: LineageLink
    child_unit_ordinals: tuple[int, ...] = ()
    unit_count: int = 0

    def __post_init__(self) -> None:
        _require_text("observation_key", self.observation_key)
        _require_text("source_data_encoding", self.source_data_encoding)
        _require_sha256("parsed_component_fingerprint", self.parsed_component_fingerprint)
        if self.ordinal < 0 or self.unit_count < 0:
            raise ValueError("component ordinal and unit_count must be non-negative")
        if self.parsed_status not in _PARSED_STATUSES:
            raise ValueError("component parsed_status is not recognized")
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        ordinals = tuple(self.child_unit_ordinals)
        if any(ordinal < 0 for ordinal in ordinals) or len(set(ordinals)) != len(ordinals):
            raise ValueError("child_unit_ordinals must be unique non-negative values")
        if self.unit_count != len(ordinals):
            raise ValueError("unit_count must equal the number of child_unit_ordinals")
        object.__setattr__(self, "child_unit_ordinals", ordinals)

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_key": self.observation_key,
            "ordinal": self.ordinal,
            "source_component_id": self.source_component_id,
            "source_data_encoding": self.source_data_encoding,
            "source_layout": _json_value(self.source_layout),
            "source_style": _json_value(self.source_style),
            "parsed_component_fingerprint": self.parsed_component_fingerprint,
            "parsed_status": self.parsed_status,
            "provenance": self.provenance.to_dict(),
            "content_role": self.content_role.to_dict(),
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "lineage": self.lineage.to_dict(),
            "child_unit_ordinals": list(self.child_unit_ordinals),
            "unit_count": self.unit_count,
        }

@dataclass(frozen=True)
class CanonicalUnit:
    """One Canonical value with an observation-local addressing label."""

    unit_id: str
    kind: str
    ordinal: int
    parent_component_key: str
    value: Any
    parsed_status: ParseStatus
    lineage: LineageLink
    metadata: Mapping[str, Any] = field(default_factory=dict)
    provenance: Classification = field(default_factory=Classification)
    content_role: Classification = field(default_factory=Classification)
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        for name in ("unit_id", "kind", "parent_component_key"):
            _require_text(name, getattr(self, name))
        if self.ordinal < 0:
            raise ValueError("unit ordinal must be non-negative")
        if self.parsed_status not in _PARSED_STATUSES:
            raise ValueError("unit parsed_status is not recognized")
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "kind": self.kind,
            "ordinal": self.ordinal,
            "parent_component_key": self.parent_component_key,
            "value": _json_value(self.value),
            "parsed_status": self.parsed_status,
            "lineage": self.lineage.to_dict(),
            "metadata": _json_value(self.metadata),
            "provenance": self.provenance.to_dict(),
            "content_role": self.content_role.to_dict(),
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
        }

@dataclass(frozen=True)
class CanonicalSection:
    """A structural container; ``section_id`` is observation-local only."""

    section_id: str
    ordinal: int
    lineage: LineageLink
    source_metadata: Mapping[str, Any] = field(default_factory=dict)
    component_contexts: tuple[ComponentContext, ...] = ()
    units: tuple[CanonicalUnit, ...] = ()

    def __post_init__(self) -> None:
        _require_text("section_id", self.section_id)
        if self.ordinal < 0:
            raise ValueError("section ordinal must be non-negative")
        object.__setattr__(self, "source_metadata", dict(self.source_metadata))
        contexts = tuple(self.component_contexts)
        units = tuple(self.units)
        keys = [context.observation_key for context in contexts]
        if len(set(keys)) != len(keys):
            raise ValueError("component observation keys must be unique within a section")
        by_context: dict[str, list[int]] = {key: [] for key in keys}
        for unit in units:
            if unit.parent_component_key not in by_context:
                raise ValueError("every CanonicalUnit must reference a section ComponentContext")
            by_context[unit.parent_component_key].append(unit.ordinal)
        for context in contexts:
            child_ordinals = by_context[context.observation_key]
            if len(set(child_ordinals)) != len(child_ordinals):
                raise ValueError("unit ordinals must be unique within a ComponentContext")
            if tuple(child_ordinals) != context.child_unit_ordinals or len(child_ordinals) != context.unit_count:
                raise ValueError("ComponentContext child-unit accounting does not match section units")
        object.__setattr__(self, "component_contexts", contexts)
        object.__setattr__(self, "units", units)

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "ordinal": self.ordinal,
            "lineage": self.lineage.to_dict(),
            "source_metadata": _json_value(self.source_metadata),
            "component_contexts": [context.to_dict() for context in self.component_contexts],
            "units": [unit.to_dict() for unit in self.units],
        }

@dataclass(frozen=True)
class CanonicalRecord:
    record_id: str
    parsed_identity: ParsedIdentity
    observation: CanonicalObservation
    status: CanonicalRecordStatus
    lineage: LineageLink
    source_identity: ParsedIdentity | None = None
    record_metadata: Mapping[str, Any] = field(default_factory=dict)
    metadata_lineage: tuple[tuple[str, LineageLink], ...] = ()
    sections: tuple[CanonicalSection, ...] = ()
    versions: CanonicalVersions = field(default_factory=CanonicalVersions)
    blocked_reason: str | None = None
    blocked_diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in _RECORD_STATUSES:
            raise ValueError("Canonical record status is not recognized")
        expected_record_id = canonical_record_id(
            parsed_run_id=self.observation.parsed_run_id,
            parsed_identity_key=self.parsed_identity.key,
            parsed_record_sha256=self.observation.parsed_record_sha256,
        )
        if self.record_id != expected_record_id:
            raise ValueError("record_id does not match the approved Parsed observation derivation")
        record_metadata = dict(self.record_metadata)
        if self.status == "blocked_integrity":
            if self.observation.parsed_status != "blocked_integrity":
                raise ValueError("blocked Canonical record requires blocked Parsed status")
            if self.source_identity is not None:
                raise ValueError("blocked_integrity Canonical record must not carry source_identity")
            if self.sections:
                raise ValueError("blocked_integrity Canonical record must not contain sections")
            _require_text("blocked_reason", self.blocked_reason or "")
            _require_text("record_metadata.content_id", record_metadata.get("content_id") or "")
            if "channel_memberships" not in record_metadata:
                raise ValueError("blocked_integrity Canonical record requires record_metadata.channel_memberships")
        elif self.blocked_reason is not None or self.blocked_diagnostics:
            raise ValueError("blocked evidence fields are only valid for blocked_integrity records")
        elif self.observation.parsed_status == "blocked_integrity":
            raise ValueError("blocked Parsed status requires a blocked_integrity Canonical record")
        if self.source_identity is not None and (
            self.source_identity.kind != "detail" or self.source_identity.stability != "logical"
        ):
            raise ValueError("source_identity must remain a logical Parsed detail source-contract identity")
        object.__setattr__(self, "record_metadata", record_metadata)
        object.__setattr__(self, "blocked_diagnostics", tuple(self.blocked_diagnostics))
        metadata_lineage = _metadata_lineage_pairs(self.metadata_lineage)
        if "channel_memberships" in self.record_metadata:
            memberships_lineage = dict(metadata_lineage).get("channel_memberships")
            if memberships_lineage is None or memberships_lineage.evidence_scope != "parsed_dependency":
                raise ValueError("channel_memberships require parsed_dependency lineage, never a detail RawRef")
        object.__setattr__(self, "metadata_lineage", metadata_lineage)
        sections = tuple(self.sections)
        section_ids = [section.section_id for section in sections]
        if len(set(section_ids)) != len(section_ids):
            raise ValueError("section identifiers must be unique within a CanonicalRecord")
        object.__setattr__(self, "sections", sections)

    @property
    def dependency_fingerprint(self) -> str:
        return canonical_dependency_fingerprint(self.observation.dependency_projection(), self.versions.to_dict())

    def content_projection(self) -> dict[str, Any]:
        return {
            "parsed_identity": self.parsed_identity.to_dict(),
            "source_identity": None if self.source_identity is None else self.source_identity.to_dict(),
            "status": self.status,
            "blocked_reason": self.blocked_reason,
            "blocked_diagnostics": [diagnostic.to_dict() for diagnostic in self.blocked_diagnostics],
            "lineage": self.lineage.to_dict(),
            "record_metadata": _json_value(self.record_metadata),
            "metadata_lineage": {key: value.to_dict() for key, value in self.metadata_lineage},
            "sections": [section.to_dict() for section in self.sections],
        }

    @property
    def content_fingerprint(self) -> str:
        return canonical_content_fingerprint(self.content_projection())

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "parsed_identity": self.parsed_identity.to_dict(),
            "source_identity": None if self.source_identity is None else self.source_identity.to_dict(),
            "status": self.status,
            "observation": self.observation.to_dict(),
            "lineage": self.lineage.to_dict(),
            "record_metadata": _json_value(self.record_metadata),
            "metadata_lineage": {key: value.to_dict() for key, value in self.metadata_lineage},
            "sections": [section.to_dict() for section in self.sections],
            "versions": self.versions.to_dict(),
            "blocked_reason": self.blocked_reason,
            "blocked_diagnostics": [diagnostic.to_dict() for diagnostic in self.blocked_diagnostics],
            "dependency_fingerprint": self.dependency_fingerprint,
            "content_fingerprint": self.content_fingerprint,
        }
