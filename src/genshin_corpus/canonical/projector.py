from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from genshin_corpus.parser.contracts import Classification, ContractMetadata, Diagnostic, ParsedIdentity, RawRef
from genshin_corpus.parser.models import ParsedComponent, ParsedContentUnit, ParsedDetail, ParsedModule, ParsedUnknown

from .contracts import (
    CanonicalObservation,
    CanonicalRecord,
    CanonicalSection,
    CanonicalUnit,
    CanonicalVersions,
    ComponentContext,
    LineageLink,
    parsed_identity_from_input,
    source_identity_from_parsed_input,
)


PROJECTOR_POLICY_VERSION = "obc-modules-as-sections-0.1"


@dataclass(frozen=True)
class _OrderedEntry:
    ordering: int
    sequence: int
    value: ParsedModule | ParsedUnknown


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


def _raw_refs_from_mapping(value: Any) -> tuple[RawRef, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("serialized Parsed metadata.raw_refs must be a list")
    if any(not isinstance(item, Mapping) for item in value):
        raise ValueError("serialized Parsed metadata.raw_refs entries must be objects")
    return tuple(RawRef(**item) for item in value)


def _diagnostics_from_mapping(value: Any) -> tuple[Diagnostic, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("serialized Parsed metadata.diagnostics must be a list")
    diagnostics: list[Diagnostic] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("serialized Parsed metadata.diagnostics entries must be objects")
        required = ("code", "message", "severity", "path")
        if any(field not in item for field in required):
            raise ValueError("serialized Parsed metadata.diagnostics entries must contain code, message, severity, and path")
        if not isinstance(item["code"], str) or not item["code"]:
            raise ValueError("serialized Parsed diagnostic code must be a non-empty string")
        if not isinstance(item["message"], str) or not item["message"]:
            raise ValueError("serialized Parsed diagnostic message must be a non-empty string")
        if item["severity"] not in {"info", "warning", "error"}:
            raise ValueError("serialized Parsed diagnostic severity is not recognized")
        if not isinstance(item["path"], str):
            raise ValueError("serialized Parsed diagnostic path must be a string")
        diagnostics.append(
            Diagnostic(
                code=item["code"],
                message=item["message"],
                severity=item["severity"],
                path=item["path"],
            )
        )
    return tuple(diagnostics)


def _lineage(
    *,
    parsed_pointer: str,
    raw_refs: tuple[RawRef, ...],
    dependency_locator: str,
    raw_evidence_scope: str = "direct_raw",
) -> LineageLink:
    if raw_refs:
        return LineageLink(parsed_pointer, raw_evidence_scope, raw_refs)
    return LineageLink(parsed_pointer, "parsed_dependency", dependency_locator=dependency_locator)


def _metadata_lineage(
    *,
    key: str,
    parsed_pointer: str,
    raw_refs: tuple[RawRef, ...],
    dependency_locator: str,
) -> tuple[str, LineageLink]:
    return key, _lineage(
        parsed_pointer=parsed_pointer,
        raw_refs=raw_refs,
        dependency_locator=dependency_locator,
        raw_evidence_scope="inherited_parent_raw",
    )


def _parsed_metadata_projection(metadata: ContractMetadata) -> dict[str, Any]:
    """Preserve Parsed contract metadata without duplicating Raw payloads."""

    return {
        "parse_status": metadata.parse_status,
        "source_position": metadata.source_position.to_dict(),
        "source_fingerprint": metadata.source_fingerprint,
        "parsed_fingerprint": metadata.parsed_fingerprint,
        "provenance": metadata.provenance.to_dict(),
        "content_role": metadata.content_role.to_dict(),
        "diagnostics": [diagnostic.to_dict() for diagnostic in metadata.diagnostics],
    }


def _unit_kind(value: Any) -> str:
    if hasattr(value, "to_dict"):
        serialized = value.to_dict()
        if isinstance(serialized, Mapping) and isinstance(serialized.get("kind"), str):
            return str(serialized["kind"])
    if isinstance(value, Mapping) and value.get("kind") == "rich_text":
        return "rich_text"
    return "structured_observation"


def _classification(value: Any) -> Classification:
    return value if isinstance(value, Classification) else Classification()


def _canonical_unit(
    unit: ParsedContentUnit,
    *,
    parent_component_key: str,
    parsed_module_index: int,
    parsed_component_index: int,
    parsed_unit_index: int,
    unit_ordinal: int,
) -> CanonicalUnit:
    metadata = unit.metadata
    pointer = f"/modules/{parsed_module_index}/components/{parsed_component_index}/units/{parsed_unit_index}"
    return CanonicalUnit(
        unit_id=f"unit:{unit_ordinal}",
        kind=_unit_kind(unit.value),
        ordinal=unit_ordinal,
        parent_component_key=parent_component_key,
        value=unit.value,
        parsed_status=metadata.parse_status,
        lineage=_lineage(
            parsed_pointer=pointer,
            raw_refs=metadata.raw_refs,
            dependency_locator=f"parsed-record:{pointer}",
            raw_evidence_scope=(
                "direct_raw" if any(reference.embedded_json_pointer for reference in metadata.raw_refs)
                else "inherited_parent_raw"
            ),
        ),
        metadata={
            "parsed_identity": unit.identity.to_dict(),
            "source_position": metadata.source_position.to_dict(),
        },
        provenance=metadata.provenance,
        content_role=metadata.content_role,
        diagnostics=metadata.diagnostics,
    )


def _unsupported_unit(
    item: ParsedUnknown,
    *,
    parent_component_key: str,
    parsed_module_index: int,
    parsed_component_index: int,
    parsed_unsupported_index: int,
    unit_ordinal: int,
) -> CanonicalUnit:
    pointer = f"/modules/{parsed_module_index}/components/{parsed_component_index}/unsupported/{parsed_unsupported_index}"
    return CanonicalUnit(
        unit_id=f"unit:{unit_ordinal}",
        kind="unsupported",
        ordinal=unit_ordinal,
        parent_component_key=parent_component_key,
        value={"reason": item.reason, "raw_value": item.raw_value, "context": item.context},
        parsed_status=item.metadata.parse_status,
        lineage=_lineage(
            parsed_pointer=pointer,
            raw_refs=item.metadata.raw_refs,
            dependency_locator=f"parsed-record:{pointer}",
        ),
        metadata={
            "parsed_identity": item.identity.to_dict(),
            "source_position": item.metadata.source_position.to_dict(),
        },
        provenance=item.metadata.provenance,
        content_role=item.metadata.content_role,
        diagnostics=item.metadata.diagnostics,
    )


def _component_context(
    component: ParsedComponent,
    *,
    parsed_module_index: int,
    component_ordinal: int,
    parsed_component_index: int,
) -> tuple[ComponentContext, tuple[CanonicalUnit, ...]]:
    metadata = component.metadata
    key = component.identity.key
    units = tuple(
        _canonical_unit(
            unit,
            parent_component_key=component.identity.key,
            parsed_module_index=parsed_module_index,
            parsed_component_index=parsed_component_index,
            parsed_unit_index=index,
            unit_ordinal=index,
        )
        for index, unit in enumerate(component.units)
    )
    unsupported = tuple(
        _unsupported_unit(
            item,
            parent_component_key=key,
            parsed_module_index=parsed_module_index,
            parsed_component_index=parsed_component_index,
            parsed_unsupported_index=index,
            unit_ordinal=len(units) + index,
        )
        for index, item in enumerate(component.unsupported)
    )
    all_units = units + unsupported
    context = ComponentContext(
        observation_key=key,
        ordinal=component_ordinal,
        source_component_id=component.source_component_id,
        source_data_encoding=component.source_data_encoding,
        source_layout=component.source_layout,
        source_style=component.source_style,
        parsed_component_fingerprint=metadata.parsed_fingerprint,
        parsed_status=metadata.parse_status,
        provenance=metadata.provenance,
        content_role=metadata.content_role,
        diagnostics=metadata.diagnostics,
        lineage=_lineage(
            parsed_pointer=f"/modules/{parsed_module_index}/components/{parsed_component_index}",
            raw_refs=metadata.raw_refs,
            dependency_locator=f"parsed-record:/modules/{parsed_module_index}/components/{parsed_component_index}",
        ),
        source_position=metadata.source_position,
        child_unit_ordinals=tuple(unit.ordinal for unit in all_units),
        unit_count=len(all_units),
    )
    return context, all_units


def _unsupported_component_context(
    item: ParsedUnknown,
    *,
    parsed_module_index: int,
    component_ordinal: int,
    parsed_unsupported_index: int,
) -> tuple[ComponentContext, tuple[CanonicalUnit, ...]]:
    metadata = item.metadata
    key = item.identity.key
    component_id = item.context.get("component_id") if isinstance(item.context, Mapping) else None
    context = ComponentContext(
        observation_key=key,
        ordinal=component_ordinal,
        source_component_id=None if component_id is None else str(component_id),
        source_data_encoding="unknown",
        source_layout=None,
        source_style=None,
        parsed_component_fingerprint=metadata.parsed_fingerprint,
        parsed_status=metadata.parse_status,
        provenance=metadata.provenance,
        content_role=metadata.content_role,
        diagnostics=metadata.diagnostics,
        lineage=_lineage(
            parsed_pointer=f"/modules/{parsed_module_index}/unsupported/{parsed_unsupported_index}",
            raw_refs=metadata.raw_refs,
            dependency_locator=f"parsed-record:/modules/{parsed_module_index}/unsupported/{parsed_unsupported_index}",
        ),
        source_position=metadata.source_position,
        child_unit_ordinals=(0,),
        unit_count=1,
    )
    unit = CanonicalUnit(
        unit_id="unit:0",
        kind="unsupported",
        ordinal=0,
        parent_component_key=key,
        value={"reason": item.reason, "raw_value": item.raw_value, "context": item.context},
        parsed_status=metadata.parse_status,
        lineage=_lineage(
            parsed_pointer=f"/modules/{parsed_module_index}/unsupported/{parsed_unsupported_index}",
            raw_refs=metadata.raw_refs,
            dependency_locator=f"parsed-record:/modules/{parsed_module_index}/unsupported/{parsed_unsupported_index}",
        ),
        metadata={
            "parsed_identity": item.identity.to_dict(),
            "source_position": metadata.source_position.to_dict(),
        },
        provenance=metadata.provenance,
        content_role=metadata.content_role,
        diagnostics=metadata.diagnostics,
    )
    return context, (unit,)


def _section_from_module(module: ParsedModule, *, ordinal: int, parsed_module_index: int) -> CanonicalSection:
    components: list[ComponentContext] = []
    units: list[CanonicalUnit] = []
    entries: list[tuple[int, int, str, int, ParsedComponent | ParsedUnknown]] = []
    entries.extend(
        (
            component.metadata.source_position.ordering if component.metadata.source_position.ordering is not None else index,
            index,
            "components",
            index,
            component,
        )
        for index, component in enumerate(module.components)
    )
    offset = len(entries)
    entries.extend(
        (
            item.metadata.source_position.ordering if item.metadata.source_position.ordering is not None else offset + index,
            offset + index,
            "unsupported",
            index,
            item,
        )
        for index, item in enumerate(module.unsupported)
    )
    entries.sort(key=lambda entry: (entry[0], entry[1]))
    for component_ordinal, (_, _, collection, parsed_index, item) in enumerate(entries):
        if isinstance(item, ParsedComponent):
            context, child_units = _component_context(
                item,
                parsed_module_index=parsed_module_index,
                component_ordinal=component_ordinal,
                parsed_component_index=parsed_index,
            )
        else:
            context, child_units = _unsupported_component_context(
                item,
                parsed_module_index=parsed_module_index,
                component_ordinal=component_ordinal,
                parsed_unsupported_index=parsed_index,
            )
        components.append(context)
        units.extend(child_units)
    metadata = {
        "source_module_id": module.source_module_id,
        "module_index": module.module_index,
        "name": module.name,
        "repeated": module.repeated,
        "is_submodule": module.is_submodule,
        "origin_module_id": module.origin_module_id,
        "layout_observations": list(module.layout_observations),
        "parsed_identity": module.identity.to_dict(),
        "parsed_metadata": _parsed_metadata_projection(module.metadata),
    }
    return CanonicalSection(
        section_id=f"section:{ordinal}",
        ordinal=ordinal,
        lineage=_lineage(
            parsed_pointer=f"/modules/{parsed_module_index}",
            raw_refs=module.metadata.raw_refs,
            dependency_locator=f"parsed-record:/modules/{parsed_module_index}",
        ),
        source_metadata=metadata,
        component_contexts=tuple(components),
        units=tuple(units),
    )


def _section_from_unsupported_module(item: ParsedUnknown, *, ordinal: int, parsed_unsupported_index: int) -> CanonicalSection:
    return CanonicalSection(
        section_id=f"section:{ordinal}",
        ordinal=ordinal,
        lineage=_lineage(
            parsed_pointer=f"/unsupported_modules/{parsed_unsupported_index}",
            raw_refs=item.metadata.raw_refs,
            dependency_locator=f"parsed-record:/unsupported_modules/{parsed_unsupported_index}",
        ),
        source_metadata={
            "unsupported": item.to_dict(),
            "source_position": item.metadata.source_position.to_dict(),
        },
    )


def _record_metadata(detail: ParsedDetail) -> tuple[dict[str, Any], tuple[tuple[str, LineageLink], ...]]:
    metadata = {
        "source": detail.source,
        "locale": detail.locale,
        "content_id": detail.content_id,
        "page_id": detail.page_id,
        "name": detail.name,
        "page_type": detail.page_type,
        "source_metadata": detail.source_metadata,
        "source_template_layout": detail.source_template_layout,
        "channel_memberships": list(detail.channel_memberships),
        "parsed_metadata": _parsed_metadata_projection(detail.metadata),
    }
    refs = detail.metadata.raw_refs
    locator = f"parsed-manifest:records/{detail.content_id}/channels"
    lineage = (
        _metadata_lineage(
            key="source_metadata",
            parsed_pointer="/source_metadata",
            raw_refs=refs,
            dependency_locator="parsed-record:/source_metadata",
        ),
        _metadata_lineage(
            key="source_template_layout",
            parsed_pointer="/source_template_layout",
            raw_refs=refs,
            dependency_locator="parsed-record:/source_template_layout",
        ),
        _metadata_lineage(
            key="parsed_metadata",
            parsed_pointer="/metadata",
            raw_refs=refs,
            dependency_locator="parsed-record:/metadata",
        ),
        ("channel_memberships", LineageLink("/channel_memberships", "parsed_dependency", dependency_locator=locator)),
    )
    return metadata, lineage


def _project_blocked(
    parsed_input: Mapping[str, Any],
    *,
    observation: CanonicalObservation,
    versions: CanonicalVersions,
) -> CanonicalRecord:
    metadata = parsed_input.get("metadata")
    if not isinstance(metadata, Mapping):
        raise ValueError("blocked Parsed input is missing metadata")
    identity = parsed_identity_from_input(parsed_input)
    raw_refs = _raw_refs_from_mapping(metadata.get("raw_refs"))
    diagnostics = _diagnostics_from_mapping(metadata.get("diagnostics"))
    error = parsed_input.get("error")
    content_id = parsed_input.get("content_id")
    channels = parsed_input.get("channels", ())
    if not isinstance(error, str) or not error:
        raise ValueError("blocked Parsed input requires error")
    if not isinstance(content_id, str) or not content_id:
        raise ValueError("blocked Parsed input requires content_id")
    if not isinstance(channels, list | tuple):
        raise ValueError("blocked Parsed input channels must be a list")
    record_metadata = {"content_id": content_id, "channel_memberships": [str(channel) for channel in channels]}
    metadata_lineage = (("channel_memberships", LineageLink("/channels", "parsed_dependency", dependency_locator=f"parsed-manifest:records/{content_id}/channels")),)
    return CanonicalRecord(
        record_id=_record_id(observation, identity),
        parsed_identity=identity,
        observation=observation,
        status="blocked_integrity",
        lineage=_lineage(
            parsed_pointer="",
            raw_refs=raw_refs,
            dependency_locator=f"parsed-record:{identity.key}",
            raw_evidence_scope="inherited_parent_raw",
        ),
        source_identity=None,
        record_metadata=record_metadata,
        metadata_lineage=metadata_lineage,
        versions=versions,
        blocked_reason=error,
        blocked_diagnostics=diagnostics,
    )


def _record_id(observation: CanonicalObservation, identity: ParsedIdentity) -> str:
    from .fingerprints import canonical_record_id

    return canonical_record_id(
        parsed_run_id=observation.parsed_run_id,
        parsed_identity_key=identity.key,
        parsed_record_sha256=observation.parsed_record_sha256,
    )


def project_parsed_input(
    parsed_input: ParsedDetail | Mapping[str, Any],
    *,
    observation: CanonicalObservation,
    versions: CanonicalVersions | None = None,
) -> CanonicalRecord:
    """Project one Parsed observation without storage or semantic normalization."""

    versions = versions or CanonicalVersions(transform_version=PROJECTOR_POLICY_VERSION)
    if isinstance(parsed_input, Mapping):
        if parsed_input.get("metadata", {}).get("parse_status") != "blocked_integrity":
            raise ValueError("serialized Parsed mappings are supported only for blocked_integrity observations")
        return _project_blocked(parsed_input, observation=observation, versions=versions)
    if not isinstance(parsed_input, ParsedDetail):
        raise TypeError("projected input must be ParsedDetail or a serialized blocked Parsed observation")
    if observation.parsed_status == "blocked_integrity" or parsed_input.metadata.parse_status == "blocked_integrity":
        raise ValueError("ParsedDetail cannot be projected as blocked_integrity")
    if observation.parsed_status != parsed_input.metadata.parse_status:
        raise ValueError("CanonicalObservation parsed_status does not match ParsedDetail metadata")
    record_metadata, metadata_lineage = _record_metadata(parsed_input)
    entries: list[_OrderedEntry] = []
    entries.extend(
        _OrderedEntry(module.module_index, index, module)
        for index, module in enumerate(parsed_input.modules)
    )
    entries.extend(
        _OrderedEntry(
            item.metadata.source_position.ordering if item.metadata.source_position.ordering is not None else len(parsed_input.modules) + index,
            len(parsed_input.modules) + index,
            item,
        )
        for index, item in enumerate(parsed_input.unsupported_modules)
    )
    entries.sort(key=lambda entry: (entry.ordering, entry.sequence))
    sections = tuple(
        _section_from_module(entry.value, ordinal=ordinal, parsed_module_index=entry.sequence)
        if isinstance(entry.value, ParsedModule)
        else _section_from_unsupported_module(
            entry.value,
            ordinal=ordinal,
            parsed_unsupported_index=entry.sequence - len(parsed_input.modules),
        )
        for ordinal, entry in enumerate(entries)
    )
    status = "canonical" if parsed_input.metadata.parse_status == "parsed" else "canonical_with_anomalies"
    return CanonicalRecord(
        record_id=_record_id(observation, parsed_input.identity),
        parsed_identity=parsed_input.identity,
        observation=observation,
        status=status,
        lineage=_lineage(
            parsed_pointer="",
            raw_refs=parsed_input.metadata.raw_refs,
            dependency_locator="parsed-record:/metadata",
            raw_evidence_scope="inherited_parent_raw",
        ),
        source_identity=source_identity_from_parsed_input(parsed_input),
        record_metadata=record_metadata,
        metadata_lineage=metadata_lineage,
        sections=sections,
        versions=versions,
    )


def project_parsed_detail(
    detail: ParsedDetail,
    *,
    observation: CanonicalObservation,
    versions: CanonicalVersions | None = None,
) -> CanonicalRecord:
    return project_parsed_input(detail, observation=observation, versions=versions)
